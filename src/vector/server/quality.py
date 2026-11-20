"""Качество: рекламации → CAPA → проверка эффективности; запрос на изменение (ECR) → извещение (ECO)
(этап 6, сценарий С5 шаг 10 и сценарий 10 «Сервис/качество»; decision log D-39).

Модуль — примесь (mixin) к `Service`, как lifecycle.py: транзакция, идемпотентность по operation_id, аудит и
проверка версий — в service.py; здесь только правила домена (сами правила — в vector.core.quality, общие с
клиентом).

Поток работы:

    РП или ГИП проекта (резерв — руководитель отдела):
        claim.register (можно без связи) — рекламация по экземпляру изделия («Зарегистрирована»)
        claim.start_analysis — ответственный за анализ причины («Анализ причины»)
        claim.plan — причина и план CAPA: действия с исполнителем и сроком («Выполнение действий»)
    исполнитель действия (или тот, кто ведёт рекламацию): claim.action_done — действие выполнено, с результатом
    РП / ГИП: claim.submit_check — все действия выполнены → «Проверка эффективности»
    ДРУГОЙ человек (ГИП, РП, руководитель отдела — не составлял план, не выполнял действия, не отправлял):
        claim.check — запись проверки (неизменяемо): эффективно → «Закрыта», нет → снова «Выполнение действий»

    любой участник проекта: ecr.submit (можно без связи) — запрос на изменение («Подан»)
    РП или ГИП: ecr.assess — оценка влияния («Оценён»)
    ГИП проекта, не автор: ecr.decide — «Принят» / «Отклонён»
    РП или ГИП: eco.create / eco.save (можно без связи) — извещение по принятому запросу: ТОЧНЫЕ затронутые версии
    ГИП проекта, не составитель: eco.approve — «Утверждено» (событие ECOReleased; строки больше не меняются)
    РП или ГИП: eco.implement — на какие версии заменены («Внедрено»)
    не тот, кто внедрял: eco.verify — «Проверено»

Главные инварианты (каждый закреплён тестом в tests/server/test_quality.py):

* рекламация закрывается ТОЛЬКО записью проверки эффективности независимого проверяющего; закрытие не доказывает
  устранение причины (это сказано в карточке — vector.core.quality.CLOSURE_NOTE);
* решение по ECR и утверждение ECO — не автор («четыре глаза»); проверку внедрения делает не тот, кто внедрял;
* строки ECO — точные версии документов и редакции техпроцессов этого проекта; новая версия при внедрении
  существует и новее прежней;
* каждый шаг — в неизменяемом журнале решений (quality_decisions) с основанием («замещает …», D-34/D-35).

Расширение D-40 (ответ владельца на A-46):

* роль ОТК (otk — отдел технического контроля) ведёт рекламации наравне с РП и ГИП, проверяет эффективность и
  внедрение (правило «другой человек» действует и для неё). D-41 (ответ владельца на A-47, «по проектам»): роль
  действует только в проектах, куда сотрудник ОТК назначен (участник проекта, условие in_project), — там он и
  ответственный, и исполнитель, и получатель уведомлений;
* охват рекламации (claim_terms.scope): экземпляр (заводской номер), партия — наряд (экземпляры наряда; номера могут
  быть неизвестны) или проект без номера изделия; claims.instance_id тогда NULL;
* классификация дефекта: тяжесть — при регистрации (обязательна), категория — при первом плане CAPA (по найденной
  причине) или раньше; claim.classify уточняет их с причиной;
* срок ответа заказчику (обязателен для рекламации от заказчика): claim.set_due переносит его с причиной,
  claim.respond записывает ответ (неизменяемо). Срок «закрыт», когда после его установки записан ответ;
* напоминания о сроке: при обновлении (pull) сервер создаёт пользователю уведомление «срок подходит» / «срок прошёл»
  (один раз на срок и вид), если он ведёт рекламацию (РП, ГИП проекта, их замещающие в проекте, ответственный за
  анализ, ОТК) — отдельного планировщика нет, уведомление появляется при ближайшем обновлении.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import lifecycle as lc
from vector.core import quality as qa
from vector.core.errors import Conflict, Denied, Rejected

from .authz import project_member
from .db import (change_orders, change_requests, claim_actions, claim_responses, claim_terms, claims, document_versions,
                 documents, effectiveness_checks, instance_compositions, kits, notifications, passports, product_instances,
                 projects, quality_decisions, tech_processes, work_orders)
from .validation import canon, iso, only_keys, req_uuid

# Подписи шагов журнала решений (история карточки).
DECISION_LABELS_RU = {
    "registered": "Зарегистрирована", "analysis_started": "Начат анализ причины", "planned": "Утверждён план действий",
    "actions_added": "Добавлены действия", "action_done": "Действие выполнено", "submitted_check": "Отправлена на проверку",
    "check_effective": "Проверка: действия эффективны", "check_not_effective": "Проверка: действия не эффективны",
    "submitted": "Подан", "assessed": "Оценён", "accepted": "Принят", "rejected": "Отклонён",
    "created": "Составлено", "saved": "Изменено", "approved": "Утверждено", "implemented": "Внедрено", "verified": "Проверено",
    # D-40
    "classified": "Классификация дефекта", "due_set": "Изменён срок ответа заказчику", "responded": "Ответ заказчику",
}
# Наряды, по которым можно заявить рекламацию на партию: изготовление завершено.
BATCH_WO_STATES = ("completed", "closed")


def _q(fn, *a, **kw):
    """Вызвать правило vector.core.quality; QualityError → Rejected (ошибка ввода, с полем и замечаниями)."""
    try:
        return fn(*a, **kw)
    except lc.LifecycleError as e:
        raise Rejected(e.code, e.message_ru, e.details)


class QualityMixin:
    """Обработчики claim.* / ecr.* / eco.* и данные качества для pull."""

    # ------------------------------------------------------------ журнал решений
    def _qdecide(self, ctx, otype: str, oid: str, decision: str, details: dict, auth) -> int:
        """Неизменяемая запись шага (следующий номер по объекту) и основание решения (замещение — D-34/D-35)."""
        qd = quality_decisions
        seq = (ctx.conn.execute(sa.select(sa.func.max(qd.c.seq)).where(qd.c.object_type == otype, qd.c.object_id == oid))
               .scalar() or 0) + 1
        ctx.conn.execute(qd.insert().values(object_type=otype, object_id=oid, seq=seq, decision=decision,
                                            details_json=canon(details), authority=auth.describe(),
                                            decided_by=ctx.user_id, decided_at=ctx.now))
        self._record_basis(ctx, "quality_decision", f"{otype}:{oid}#{seq}", 1, auth)
        return seq

    def _qhistory(self, conn, otype: str, oid: str) -> list[dict]:
        """История объекта по журналу решений: шаг, подпись, подробности, кто, когда, за кого (замещение)."""
        qd = quality_decisions
        out = []
        for r in conn.execute(sa.select(qd).where(qd.c.object_type == otype, qd.c.object_id == oid).order_by(qd.c.seq)):
            b = self._basis(conn, "quality_decision", f"{otype}:{oid}#{r.seq}")
            out.append({"seq": r.seq, "decision": r.decision, "label": DECISION_LABELS_RU.get(r.decision, r.decision),
                        "details": json.loads(r.details_json), "authority": r.authority, "by": r.decided_by,
                        "at": iso(r.decided_at), "on_behalf_of": b["on_behalf_of"] if b else None})
        return out

    def _qnotify_project(self, ctx, project_id: str, roles: tuple, kind: str, title: str, oid: str, otype: str,
                         extra: tuple = (), skip: tuple = ()) -> None:
        """Уведомить людей проекта в ролях roles (и их замещающих в проекте), а также extra; кроме skip и автора."""
        to = [u for r in roles for u in self._project_role_users(ctx.conn, project_id, r, ctx.now)] + list(extra)
        self._notify(ctx, [u for u in to if u not in skip], kind, title, object_type=otype, object_id=oid)

    def _member_or_reject(self, conn, uid: Any, project_id: str, field: str, now) -> str:
        """Сотрудник — участник проекта сейчас (своё назначение или замещение в проекте), иначе отказ по полю.
        Сотрудник ОТК тоже должен быть назначен в проект (D-41: ОТК — по проектам)."""
        if not isinstance(uid, str) or not project_member(conn, uid, project_id, now):
            raise Rejected("not_project_member", "Выберите участника проекта (сотрудник ОТК — отдела технического "
                                                 "контроля — должен быть назначен в проект).",
                           {"field": field, "user_id": uid})
        return uid

    def _project_otk(self, conn, project_id: str, now) -> list[str]:
        """Сотрудники ОТК этого проекта (D-41): роль «Технический контроль» (своя или по замещению) и назначение в
        проект (своё или по замещению в проекте). ОТК других проектов сюда не попадают."""
        return [u for u in self._users_with_role(conn, "otk", now) if project_member(conn, u, project_id, now)]

    def _claim_leads(self, conn, o: dict, now) -> list[str]:
        """Кто ведёт рекламацию (адресаты уведомлений о сроке и новых рекламациях): РП и ГИП проекта (и их замещающие
        в проекте), ответственный за анализ, сотрудники ОТК этого проекта (D-41)."""
        out = [u for r in ("rp", "gip") for u in self._project_role_users(conn, o["project_id"], r, now)]
        out += [o["responsible_id"]] if o.get("responsible_id") else []
        out += self._project_otk(conn, o["project_id"], now)
        return list(dict.fromkeys(out))

    def _not_future(self, ctx, iso_d: str, field: str, what: str) -> None:
        """Дата не в будущем (по часам сервера; запас — сутки на часовые пояса)."""
        if date.fromisoformat(iso_d) > (ctx.now + timedelta(days=1)).date():
            raise Rejected("date_in_future", f"{what} ещё не наступила.", {"field": field})

    # ------------------------------------------------------------ представления
    def _claim_actions(self, conn, claim_id: str) -> list[dict]:
        """Действия CAPA рекламации по порядку."""
        return [{"id": r.id, "seq": r.seq, "kind": r.kind, "kind_label": qa.ACTION_KINDS_RU[r.kind],
                 "description": r.description, "owner_id": r.owner_id, "due_date": r.due_date.isoformat(),
                 "status": r.status, "status_label": qa.ACTION_STATUS_RU[r.status], "result": r.result,
                 "done_by": r.done_by, "done_at": iso(r.done_at), "planned_by": r.planned_by, "planned_at": iso(r.planned_at)}
                for r in conn.execute(sa.select(claim_actions).where(claim_actions.c.claim_id == claim_id)
                                      .order_by(claim_actions.c.seq))]

    def v_claim(self, conn, o: dict) -> dict:
        """Рекламация для клиента: экземпляр (точная связь), действия CAPA, проверки эффективности, история; кто
        участвовал (составлял план, выполнял, отправлял) — для подсказки «кто может проверять».
        """
        inst = self._get(conn, product_instances, o["instance_id"]) if o["instance_id"] else None
        kit = self._get(conn, kits, inst["kit_id"]) if inst else None
        terms = self._claim_terms(conn, o)
        acts = self._claim_actions(conn, o["id"])
        checks = [{"seq": r.seq, "effective": bool(r.effective), "evidence": r.evidence, "checked_by": r.checked_by,
                   "checked_at": iso(r.checked_at), "authority": r.authority}
                  for r in conn.execute(sa.select(effectiveness_checks).where(effectiveness_checks.c.claim_id == o["id"])
                                        .order_by(effectiveness_checks.c.seq))]
        ecrs = [{"id": r.id, "ecr_no": r.ecr_no, "status": r.status, "title": r.title}
                for r in conn.execute(sa.select(change_requests).where(change_requests.c.claim_id == o["id"])
                                      .order_by(change_requests.c.ecr_no))]
        resp = self._claim_responses(conn, o["id"])
        answered = bool(terms["due_set_at"]) and any(r["responded_at"] >= terms["due_set_at"] for r in resp)
        subject = self._claim_subject(conn, o, terms, inst, kit)
        return {"id": o["id"], "claim_no": o["claim_no"], "project_id": o["project_id"], "instance_id": o["instance_id"],
                "instance_label": subject["label"], "scope": terms["scope"], "scope_label": qa.CLAIM_SCOPES_RU[terms["scope"]],
                "batch_instances": subject["instances"],
                "kit_id": inst["kit_id"] if inst else subject["kit_id"],
                "work_order_id": inst["work_order_id"] if inst else terms["work_order_id"],
                "severity": terms["severity"], "severity_label": qa.SEVERITY_RU.get(terms["severity"] or "", ""),
                "category": terms["category"], "category_label": qa.CATEGORY_RU.get(terms["category"] or "", ""),
                "response_due": terms["response_due"], "due_set_at": iso(terms["due_set_at"]), "answered": answered,
                "deadline": qa.deadline_state(terms["response_due"], answered, self.now().date().isoformat())
                if o["source"] == "customer" else "none",
                "responses": [{**r, "responded_at": iso(r["responded_at"])} for r in resp],
                "received_date": o["received_date"].isoformat(), "source": o["source"],
                "source_label": qa.CLAIM_SOURCES_RU[o["source"]], "reference": o["reference"], "description": o["description"],
                "status": o["status"], "status_label": qa.CLAIM_STATUS_LABELS_RU[o["status"]],
                "responsible_id": o["responsible_id"], "cause": o["cause"], "submitted_by": o["submitted_by"],
                "actions": acts, "checks": checks, "change_requests": ecrs,
                "planners": sorted({a["planned_by"] for a in acts}), "doers": sorted({a["done_by"] for a in acts if a["done_by"]}),
                "closure_note": qa.CLOSURE_NOTE if o["status"] == "closed" else None,
                "history": self._qhistory(conn, "claim", o["id"]), "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "updated_by": o["updated_by"], "updated_at": iso(o["updated_at"])}

    def _claim_terms(self, conn, o: dict) -> dict:
        """Условия рекламации (claim_terms). У рекламаций до v19 строки нет — «экземпляр, не классифицирована, без
        срока ответа»."""
        t = conn.execute(sa.select(claim_terms).where(claim_terms.c.claim_id == o["id"])).mappings().first()
        if t is None:
            return {"scope": "instance", "work_order_id": None, "severity": None, "category": None, "response_due": None,
                    "due_set_at": None, "exists": False}
        return {"scope": t["scope"], "work_order_id": t["work_order_id"], "severity": t["severity"], "category": t["category"],
                "response_due": t["response_due"].isoformat() if t["response_due"] else None, "due_set_at": t["due_set_at"],
                "exists": True}

    def _claim_responses(self, conn, claim_id: str) -> list[dict]:
        """Ответы заказчику по порядку (неизменяемые записи)."""
        cr = claim_responses
        return [{"seq": r.seq, "kind": r.kind, "kind_label": qa.RESPONSE_KINDS_RU[r.kind],
                 "response_date": r.response_date.isoformat(), "summary": r.summary, "reference": r.reference,
                 "authority": r.authority, "responded_by": r.responded_by, "responded_at": r.responded_at}
                for r in conn.execute(sa.select(cr).where(cr.c.claim_id == claim_id).order_by(cr.c.seq))]

    def _claim_subject(self, conn, o: dict, terms: dict, inst: Optional[dict], kit: Optional[dict]) -> dict:
        """К чему относится рекламация: подпись («АТХ № 0001 (серия 2026)», «Наряд № 2 — АТХ, 3 шт.», «Проект
        П-2026-014 без номера изделия»), изделие и экземпляры партии (для связи с извещениями)."""
        if terms["scope"] == "instance" and inst:
            return {"label": f"{kit['code']} {lc.serial_label(inst['series'], inst['serial_no'])}", "instances": [], "kit_id": inst["kit_id"]}
        if terms["scope"] == "batch" and terms["work_order_id"]:
            wo = self._get(conn, work_orders, terms["work_order_id"])
            k = self._get(conn, kits, wo["kit_id"])
            pis = [{"id": r.id, "label": f"{k['code']} {lc.serial_label(r.series, r.serial_no)}"}
                   for r in conn.execute(sa.select(product_instances).where(product_instances.c.work_order_id == wo["id"])
                                         .order_by(product_instances.c.series, product_instances.c.serial_no))]
            return {"label": f"Партия: наряд № {wo['doc_no']} — {k['code']}, {wo['qty']} шт.", "instances": pis, "kit_id": wo["kit_id"]}
        pr = self._get(conn, projects, o["project_id"])
        return {"label": f"Проект {pr['code']} — без номера изделия", "instances": [], "kit_id": None}

    def v_change_request(self, conn, o: dict) -> dict:
        """Запрос на изменение для клиента: связи (изделие, рекламация), оценка, решение, извещение, история."""
        kit = self._get(conn, kits, o["kit_id"]) if o["kit_id"] else None
        cl = self._get(conn, claims, o["claim_id"]) if o["claim_id"] else None
        eco = conn.execute(sa.select(change_orders.c.id, change_orders.c.eco_no, change_orders.c.status)
                           .where(change_orders.c.ecr_id == o["id"])).first()
        return {"id": o["id"], "ecr_no": o["ecr_no"], "project_id": o["project_id"], "kit_id": o["kit_id"],
                "kit_code": kit["code"] if kit else None, "claim_id": o["claim_id"], "claim_no": cl["claim_no"] if cl else None,
                "title": o["title"], "reason": o["reason"], "proposal": o["proposal"], "status": o["status"],
                "status_label": qa.ECR_STATUS_LABELS_RU[o["status"]], "impact": o["impact"], "assessed_by": o["assessed_by"],
                "assessed_at": iso(o["assessed_at"]), "decision_reason": o["decision_reason"], "decided_by": o["decided_by"],
                "decided_at": iso(o["decided_at"]),
                "change_order": {"id": eco.id, "eco_no": eco.eco_no, "status": eco.status} if eco else None,
                "history": self._qhistory(conn, "ecr", o["id"]), "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "updated_by": o["updated_by"], "updated_at": iso(o["updated_at"])}

    def _eco_instances(self, conn, project_id: str, items: list[dict]) -> list[dict]:
        """Экземпляры, к которым относится извещение (связь с экземплярами и версиями, 02 сценарий 10): собраны по
        затронутой редакции техпроцесса (состав экземпляра) или их паспорт ссылается на затронутую версию документа.
        """
        tp_ids = {it["tp_id"] for it in items if it["kind"] == "tech_process"}
        doc_vers = {(it["document_id"], it["from_version"]) for it in items if it["kind"] == "document"}
        out = []
        rows = conn.execute(sa.select(product_instances, instance_compositions.c.composition_json, kits.c.code.label("kit_code"),
                                      passports.c.documents_json)
                            .join(instance_compositions, instance_compositions.c.instance_id == product_instances.c.id)
                            .join(kits, kits.c.id == product_instances.c.kit_id)
                            .outerjoin(passports, passports.c.instance_id == product_instances.c.id)
                            .where(product_instances.c.project_id == project_id)
                            .order_by(product_instances.c.series, product_instances.c.serial_no))
        for r in rows:
            comp = json.loads(r.composition_json)
            why = []
            if (comp.get("tp") or {}).get("id") in tp_ids:
                why.append(f"собран по техпроцессу редакции {comp['tp'].get('revision_no')}")
            for d in json.loads(r.documents_json or "[]"):
                if (d.get("document_id"), d.get("version_no")) in doc_vers:
                    why.append(f"в паспорте — {d.get('code')} версии {d.get('version_no')}")
            if why:
                out.append({"id": r.id, "label": f"{r.kit_code} {lc.serial_label(r.series, r.serial_no)}",
                            "status": r.status, "why": why})
        return out

    def v_change_order(self, conn, o: dict) -> dict:
        """Извещение для клиента: запрос, затронутые версии (и на какие заменены), экземпляры, история."""
        ecr = self._get(conn, change_requests, o["ecr_id"])
        items = json.loads(o["items_json"])
        return {"id": o["id"], "eco_no": o["eco_no"], "ecr_id": o["ecr_id"], "ecr_no": ecr["ecr_no"], "project_id": o["project_id"],
                "title": o["title"], "description": o["description"], "items": items,
                "results": json.loads(o["results_json"]) if o["results_json"] else None,
                "status": o["status"], "status_label": qa.ECO_STATUS_LABELS_RU[o["status"]],
                "approved_by": o["approved_by"], "approved_at": iso(o["approved_at"]),
                "implemented_by": o["implemented_by"], "implemented_at": iso(o["implemented_at"]),
                "verified_by": o["verified_by"], "verified_at": iso(o["verified_at"]), "verify_note": o["verify_note"],
                "instances": self._eco_instances(conn, o["project_id"], items),
                "editors": self._eco_editors(conn, o), "history": self._qhistory(conn, "eco", o["id"]), "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "updated_by": o["updated_by"], "updated_at": iso(o["updated_at"])}

    def _eco_editors(self, conn, o: dict) -> list[str]:
        """Кто составлял или правил извещение (они не утверждают — «четыре глаза»)."""
        qd = quality_decisions
        ids = {o["created_by"]} | {r.decided_by for r in conn.execute(
            sa.select(qd.c.decided_by).where(qd.c.object_type == "eco", qd.c.object_id == o["id"],
                                             qd.c.decision.in_(["created", "saved"])))}
        return sorted(ids)

    # ------------------------------------------------------------ рекламация
    def _claim_state(self, conn, o: dict, *allowed: str) -> None:
        """Рекламация в одном из состояний allowed, иначе видимый конфликт с текущим состоянием."""
        if o["status"] not in allowed:
            raise Conflict("claim_state", f"Рекламация № {o['claim_no']} уже «{qa.CLAIM_STATUS_LABELS_RU[o['status']]}» — "
                                          "действие не применено.", {"current": self.v_claim(conn, o)})

    def _claim_register(self, ctx, p, ev):
        """claim.register {id, scope?, instance_id | work_order_id | project_id, received_date, source, description,
        reference?, severity, category?, response_due?} — рекламация на экземпляр, партию (наряд) или проект (D-40).

        Охват по умолчанию — экземпляр (как до D-40). Партия — наряд с завершённым изготовлением (номера экземпляров
        могут быть неизвестны). Для рекламации от заказчика срок ответа обязателен.
        """
        only_keys(p, {"id", "scope", "instance_id", "work_order_id", "project_id", "received_date", "source", "description",
                      "reference", "severity", "category", "response_due"})
        cid = req_uuid(p, "id")
        # Сначала — объект охвата и права в его проекте (без права — отказ до разбора остальных полей).
        scope = p.get("scope") or "instance"
        inst = wo = None
        if scope == "instance":
            inst = self._require(ctx.conn, product_instances, req_uuid(p, "instance_id"), "Экземпляр")
            project_id = inst["project_id"]
        elif scope == "batch":
            wo = self._require(ctx.conn, work_orders, req_uuid(p, "work_order_id"), "Наряд")
            project_id = wo["project_id"]
        elif scope == "project":
            project_id = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")["id"]
        else:
            raise Rejected("invalid_scope", "Охват рекламации — экземпляр, партия (наряд) или проект.", {"field": "scope"})
        if p.get("project_id") and p.get("project_id") != project_id:
            raise Rejected("project_mismatch", "Экземпляр или наряд — из другого проекта.", {"field": "project_id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "claim.register", ctx.now, project_id=project_id)
        self._ensure_new(ctx.conn, claims, cid)
        d = _q(qa.parse_claim, p)
        if wo is not None and wo["status"] not in BATCH_WO_STATES:
            raise Rejected("batch_not_made", f"Наряд № {wo['doc_no']} ещё не выполнен — рекламация на партию возможна по "
                                             "выполненному или закрытому наряду.", {"field": "work_order_id"})
        self._not_future(ctx, d["received_date"], "received_date", "Дата получения")
        no = (ctx.conn.execute(sa.select(sa.func.max(claims.c.claim_no))).scalar() or 0) + 1
        ctx.conn.execute(claims.insert().values(
            id=cid, claim_no=no, project_id=project_id, instance_id=inst["id"] if inst else None,
            received_date=date.fromisoformat(d["received_date"]), source=d["source"], reference=d["reference"] or None,
            description=d["description"], status="registered", version=1, created_by=ctx.user_id, created_at=ctx.now,
            updated_by=ctx.user_id, updated_at=ctx.now))
        due = date.fromisoformat(d["response_due"]) if d["response_due"] else None
        ctx.conn.execute(claim_terms.insert().values(
            claim_id=cid, scope=d["scope"], work_order_id=wo["id"] if wo else None, severity=d["severity"],
            category=d["category"], response_due=due, due_set_at=ctx.now if due else None,
            updated_by=ctx.user_id, updated_at=ctx.now))
        self._qdecide(ctx, "claim", cid, "registered",
                      {"claim_no": no, "scope": d["scope"], "instance_id": inst["id"] if inst else None,
                       "work_order_id": wo["id"] if wo else None, "source": d["source"], "severity": d["severity"],
                       "category": d["category"], "response_due": d["response_due"]}, auth)
        ctx.log("claim", cid, auth, None, 1, {"event": "ClaimRegistered", "claim_no": no, "scope": d["scope"],
                                             "instance_id": inst["id"] if inst else None,
                                             "work_order_id": wo["id"] if wo else None, "severity": d["severity"]})
        o = self._get(ctx.conn, claims, cid)
        body = f"Тяжесть: {qa.SEVERITY_RU[d['severity']].lower()}" + (
            f"; ответ заказчику — до {date.fromisoformat(d['response_due']):%d.%m.%Y}" if d["response_due"] else "")
        # Критический дефект — отдельный вид уведомления (заметнее в списке), остальные — «новая рекламация».
        crit = d["severity"] == "critical"
        self._notify(ctx, self._claim_leads(ctx.conn, o, ctx.now), "claim_critical" if crit else "claim_new",
                     f"Рекламация № {no}: новая" + (", критический дефект" if crit else ""), body,
                     object_type="claim", object_id=cid)
        return {"claim": self.v_claim(ctx.conn, o)}

    def _claim_start_analysis(self, ctx, p, ev):
        """claim.start_analysis {id, responsible_id} — ответственный за анализ причины (участник проекта)."""
        only_keys(p, {"id", "responsible_id"})
        o = self._require(ctx.conn, claims, req_uuid(p, "id"), "Рекламация")
        auth = self.policy.check(ctx.conn, ctx.user_id, "claim.start_analysis", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_claim(ctx.conn, x))
        self._claim_state(ctx.conn, o, "registered")
        uid = self._member_or_reject(ctx.conn, p.get("responsible_id"), o["project_id"], "responsible_id", ctx.now)
        n = self._update_versioned(ctx, claims, o, {"status": "analysis", "responsible_id": uid,
                                                    "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "claim", o["id"], "analysis_started", {"responsible_id": uid}, auth)
        ctx.log("claim", o["id"], auth, o["version"], n["version"], {"event": "ClaimAnalysisStarted", "responsible_id": uid})
        self._notify(ctx, [uid], "claim_assigned", f"Рекламация № {o['claim_no']}: вы ведёте анализ причины",
                     object_type="claim", object_id=o["id"])
        return {"claim": self.v_claim(ctx.conn, n)}

    def _claim_plan(self, ctx, p, ev):
        """claim.plan {id, cause?, actions} — причина и план CAPA. Из «Анализ причины» (причина обязательна, нужно
        корректирующее действие) → «Выполнение действий»; в «Выполнение действий» — добавить действия.
        """
        only_keys(p, {"id", "cause", "category", "actions"})
        o = self._require(ctx.conn, claims, req_uuid(p, "id"), "Рекламация")
        auth = self.policy.check(ctx.conn, ctx.user_id, "claim.plan", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_claim(ctx.conn, x))
        self._claim_state(ctx.conn, o, "analysis", "action")
        first = o["status"] == "analysis"
        cause = _q(qa._text, p.get("cause"), "cause", "найденную причину", qa.MAX_TEXT, required=first)
        # Категория дефекта (D-40) — по найденной причине: при первом плане обязательна, если её ещё нет.
        terms = self._claim_terms(ctx.conn, o)
        category = p.get("category") or None
        if category is not None and category not in qa.CATEGORY_RU:
            raise Rejected("invalid_category", "Категория дефекта — из списка.", {"field": "category"})
        acts = _q(qa.parse_actions, p.get("actions"))
        if first and not qa.has_corrective(acts):
            raise Rejected("no_corrective", "В плане должно быть хотя бы одно корректирующее действие — оно устраняет причину.",
                           {"field": "actions"})
        floor = (ctx.now - timedelta(days=1)).date()
        for a in acts:
            req_uuid(a, "id")
            self._ensure_new(ctx.conn, claim_actions, a["id"])
            self._member_or_reject(ctx.conn, a["owner_id"], o["project_id"], "actions", ctx.now)
            if date.fromisoformat(a["due_date"]) < floor:
                raise Rejected("due_in_past", "Срок действия уже прошёл — укажите будущую дату.", {"field": "actions"})
        if first and category is None and terms["category"] is None:
            raise Rejected("category_required", "Укажите категорию дефекта — откуда он (по найденной причине).",
                           {"field": "category"})
        seq = ctx.conn.execute(sa.select(sa.func.max(claim_actions.c.seq)).where(claim_actions.c.claim_id == o["id"])).scalar() or 0
        for a in acts:
            seq += 1
            ctx.conn.execute(claim_actions.insert().values(
                id=a["id"], claim_id=o["id"], seq=seq, kind=a["kind"], description=a["description"], owner_id=a["owner_id"],
                due_date=date.fromisoformat(a["due_date"]), status="open", planned_by=ctx.user_id, planned_at=ctx.now))
        vals = {"status": "action", "updated_by": ctx.user_id, "updated_at": ctx.now}
        if cause:
            vals["cause"] = cause
        if category is not None and category != terms["category"]:
            self._put_terms(ctx, o, terms, {"category": category})
        n = self._update_versioned(ctx, claims, o, vals)
        self._qdecide(ctx, "claim", o["id"], "planned" if first else "actions_added",
                      {"cause": cause or None, "category": category,
                       "actions": [{k: a[k] for k in ("id", "kind", "owner_id", "due_date")} for a in acts]}, auth)
        ctx.log("claim", o["id"], auth, o["version"], n["version"],
                {"event": "CapaPlanned" if first else "CapaActionsAdded", "actions": len(acts)})
        self._notify(ctx, [a["owner_id"] for a in acts], "capa_assigned",
                     f"Рекламация № {o['claim_no']}: вам назначено действие CAPA", object_type="claim", object_id=o["id"])
        return {"claim": self.v_claim(ctx.conn, n)}

    def _claim_action_done(self, ctx, p, ev):
        """claim.action_done {id, action_id, result} — действие выполнено (исполнитель или тот, кто ведёт рекламацию)."""
        only_keys(p, {"id", "action_id", "result"})
        o = self._require(ctx.conn, claims, req_uuid(p, "id"), "Рекламация")
        auth = self.policy.check(ctx.conn, ctx.user_id, "claim.action_done", ctx.now, project_id=o["project_id"])
        a = self._get(ctx.conn, claim_actions, req_uuid(p, "action_id"))
        if a is None or a["claim_id"] != o["id"]:
            raise Rejected("not_found", "Действие не найдено в этой рекламации.", {"field": "action_id"})
        if a["owner_id"] != ctx.user_id:
            if not self.policy.can(ctx.conn, ctx.user_id, "claim.edit", ctx.now, o["project_id"]):
                raise Denied("not_action_owner", "Отметить действие может его исполнитель или тот, кто ведёт рекламацию.")
            auth = self.policy.check(ctx.conn, ctx.user_id, "claim.edit", ctx.now, project_id=o["project_id"])
        self._claim_state(ctx.conn, o, "action")
        if a["status"] == "done":
            raise Conflict("action_done", "Действие уже отмечено выполненным.", {"current": self.v_claim(ctx.conn, o)})
        result = _q(qa._text, p.get("result"), "result", "что сделано (результат)", 2000)
        ctx.conn.execute(claim_actions.update().where(claim_actions.c.id == a["id"], claim_actions.c.status == "open")
                         .values(status="done", result=result, done_by=ctx.user_id, done_at=ctx.now))
        n = self._update_versioned(ctx, claims, o, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "claim", o["id"], "action_done", {"action_id": a["id"], "seq": a["seq"], "result": result}, auth)
        ctx.log("claim", o["id"], auth, o["version"], n["version"], {"event": "CapaActionDone", "action_id": a["id"]})
        return {"claim": self.v_claim(ctx.conn, n)}

    def _claim_submit_check(self, ctx, p, ev):
        """claim.submit_check {id} — все действия выполнены → «Проверка эффективности»; уведомить проверяющих."""
        only_keys(p, {"id"})
        o = self._require(ctx.conn, claims, req_uuid(p, "id"), "Рекламация")
        auth = self.policy.check(ctx.conn, ctx.user_id, "claim.submit_check", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_claim(ctx.conn, x))
        self._claim_state(ctx.conn, o, "action")
        acts = self._claim_actions(ctx.conn, o["id"])
        left = [a["seq"] for a in acts if a["status"] != "done"]
        if not acts or left:
            raise Rejected("actions_open", f"Не выполнены действия: {', '.join(map(str, left))}." if left
                           else "В плане нет действий.", {"open": left})
        n = self._update_versioned(ctx, claims, o, {"status": "effectiveness_check", "submitted_by": ctx.user_id,
                                                    "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "claim", o["id"], "submitted_check", {}, auth)
        ctx.log("claim", o["id"], auth, o["version"], n["version"], {"event": "ClaimSubmittedForCheck"})
        # Проверяющие — ГИП, РП проекта и руководители отдела, кроме участвовавших (они проверять не могут).
        busy = {a["planned_by"] for a in acts} | {a["done_by"] for a in acts}
        self._qnotify_project(ctx, o["project_id"], ("gip", "rp"), "claim_check",
                              f"Рекламация № {o['claim_no']}: нужна проверка эффективности", o["id"], "claim",
                              extra=tuple(self._users_with_role(ctx.conn, "dept_head", ctx.now)
                                          + self._project_otk(ctx.conn, o["project_id"], ctx.now)), skip=tuple(busy))
        return {"claim": self.v_claim(ctx.conn, n)}

    def _claim_check(self, ctx, p, ev):
        """claim.check {id, effective, evidence} — проверка эффективности независимым проверяющим (неизменяемая
        запись). Эффективно → «Закрыта»; нет → «Выполнение действий» (нужны новые действия).
        """
        only_keys(p, {"id", "effective", "evidence"})
        o = self._require(ctx.conn, claims, req_uuid(p, "id"), "Рекламация")
        auth = self.policy.check(ctx.conn, ctx.user_id, "claim.check", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_claim(ctx.conn, x))
        self._claim_state(ctx.conn, o, "effectiveness_check")
        acts = self._claim_actions(ctx.conn, o["id"])
        why = qa.checker_conflicts(ctx.user_id, planners=[a["planned_by"] for a in acts],
                                   doers=[a["done_by"] for a in acts if a["done_by"]], submitter=o["submitted_by"])
        if why:
            raise Denied("checker_not_independent", "Проверять эффективность должен другой человек: " + "; ".join(why) + ".",
                         {"reasons": why})
        d = _q(qa.parse_check, p)
        ec = effectiveness_checks
        seq = (ctx.conn.execute(sa.select(sa.func.max(ec.c.seq)).where(ec.c.claim_id == o["id"])).scalar() or 0) + 1
        ctx.conn.execute(ec.insert().values(claim_id=o["id"], seq=seq, effective=d["effective"], evidence=d["evidence"],
                                            authority=auth.describe(), checked_by=ctx.user_id, checked_at=ctx.now))
        vals = {"status": "closed"} if d["effective"] else {"status": "action", "submitted_by": None}
        n = self._update_versioned(ctx, claims, o, {**vals, "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "claim", o["id"], "check_effective" if d["effective"] else "check_not_effective",
                      {"check_seq": seq, "evidence": d["evidence"]}, auth)
        ctx.log("claim", o["id"], auth, o["version"], n["version"],
                {"event": "ClaimClosed" if d["effective"] else "ClaimCheckFailed", "check_seq": seq})
        title = (f"Рекламация № {o['claim_no']} закрыта: действия эффективны" if d["effective"]
                 else f"Рекламация № {o['claim_no']}: действия не эффективны — нужны новые")
        self._qnotify_project(ctx, o["project_id"], ("rp",), "claim_checked", title, o["id"], "claim",
                              extra=(o["responsible_id"], o["submitted_by"]))
        return {"claim": self.v_claim(ctx.conn, n)}

    # ------------------------------------------------------------ D-40: классификация, срок и ответы заказчику
    def _put_terms(self, ctx, o: dict, terms: dict, vals: dict) -> None:
        """Записать условия рекламации: обновить строку claim_terms или создать её (рекламации до v19 — «экземпляр»)."""
        vals = {**vals, "updated_by": ctx.user_id, "updated_at": ctx.now}
        if terms["exists"]:
            ctx.conn.execute(claim_terms.update().where(claim_terms.c.claim_id == o["id"]).values(**vals))
        else:
            ctx.conn.execute(claim_terms.insert().values(claim_id=o["id"], scope="instance", **vals))

    def _claim_classify(self, ctx, p, ev):
        """claim.classify {id, severity, category?, reason} — уточнить тяжесть и категорию дефекта (с причиной).

        Закрытую рекламацию не переклассифицируют (итог уже проверен). Без изменений — отказ «уже так».
        """
        only_keys(p, {"id", "severity", "category", "reason"})
        o = self._require(ctx.conn, claims, req_uuid(p, "id"), "Рекламация")
        auth = self.policy.check(ctx.conn, ctx.user_id, "claim.classify", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_claim(ctx.conn, x))
        self._claim_state(ctx.conn, o, "registered", "analysis", "action", "effectiveness_check")
        d = _q(qa.parse_classification, p, require_category=False)
        reason = _q(qa._text, p.get("reason"), "reason", "причину изменения классификации", 2000)
        terms = self._claim_terms(ctx.conn, o)
        if (d["severity"], d["category"]) == (terms["severity"], terms["category"]):
            raise Rejected("no_changes", "Классификация уже такая.")
        if terms["category"] and d["category"] is None:
            raise Rejected("category_required", "Категорию дефекта можно изменить, но не убрать.", {"field": "category"})
        self._put_terms(ctx, o, terms, d)
        n = self._update_versioned(ctx, claims, o, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "claim", o["id"], "classified",
                      {"severity": d["severity"], "category": d["category"], "previous": {"severity": terms["severity"],
                                                                                          "category": terms["category"]},
                       "reason": reason}, auth)
        ctx.log("claim", o["id"], auth, o["version"], n["version"], {"event": "ClaimClassified", **d})
        if d["severity"] == "critical" and terms["severity"] != "critical":
            # Дефект стал критическим — это должны знать все, кто ведёт рекламацию.
            self._notify(ctx, self._claim_leads(ctx.conn, o, ctx.now), "claim_critical",
                         f"Рекламация № {o['claim_no']}: дефект критический", reason, object_type="claim", object_id=o["id"])
        return {"claim": self.v_claim(ctx.conn, n)}

    def _customer_claim(self, conn, o: dict) -> None:
        """Срок и ответы — только у рекламации от заказчика (внутренней отвечать некому)."""
        if o["source"] != "customer":
            raise Rejected("not_customer_claim", "Срок и ответ заказчику — только у рекламации от заказчика.")

    def _claim_set_due(self, ctx, p, ev):
        """claim.set_due {id, response_due, reason} — новый срок ответа заказчику (по согласованию с ним или для
        следующего ответа после промежуточного). Не раньше сегодняшнего дня; отсчёт «ответ дан» — с этого момента."""
        only_keys(p, {"id", "response_due", "reason"})
        o = self._require(ctx.conn, claims, req_uuid(p, "id"), "Рекламация")
        auth = self.policy.check(ctx.conn, ctx.user_id, "claim.set_due", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_claim(ctx.conn, x))
        self._customer_claim(ctx.conn, o)
        due = _q(qa._date, p.get("response_due"), "response_due", "новый срок ответа")
        _q(qa.check_due, o["received_date"].isoformat(), due)
        if date.fromisoformat(due) < (ctx.now - timedelta(days=1)).date():
            raise Rejected("due_in_past", "Новый срок уже прошёл — укажите сегодняшнюю или будущую дату.", {"field": "response_due"})
        reason = _q(qa._text, p.get("reason"), "reason", "причину (например, «согласовано с заказчиком письмом № …»)", 2000)
        terms = self._claim_terms(ctx.conn, o)
        self._put_terms(ctx, o, terms, {"response_due": date.fromisoformat(due), "due_set_at": ctx.now})
        n = self._update_versioned(ctx, claims, o, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "claim", o["id"], "due_set", {"response_due": due, "previous": terms["response_due"],
                                                          "reason": reason}, auth)
        ctx.log("claim", o["id"], auth, o["version"], n["version"], {"event": "ClaimResponseDueSet", "response_due": due})
        return {"claim": self.v_claim(ctx.conn, n)}

    def _claim_respond(self, ctx, p, ev):
        """claim.respond {id, kind, response_date, summary, reference?} — ответ заказчику отправлен (неизменяемая
        запись). Ответ после установки срока «закрывает» этот срок; новый срок (например, для окончательного ответа
        после промежуточного) задаёт claim.set_due."""
        only_keys(p, {"id", "kind", "response_date", "summary", "reference"})
        o = self._require(ctx.conn, claims, req_uuid(p, "id"), "Рекламация")
        auth = self.policy.check(ctx.conn, ctx.user_id, "claim.respond", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_claim(ctx.conn, x))
        self._customer_claim(ctx.conn, o)
        d = _q(qa.parse_response, p)
        self._not_future(ctx, d["response_date"], "response_date", "Дата ответа")
        if date.fromisoformat(d["response_date"]) < o["received_date"]:
            raise Rejected("response_before_received", "Ответ не может быть раньше получения рекламации.",
                           {"field": "response_date"})
        cr = claim_responses
        seq = (ctx.conn.execute(sa.select(sa.func.max(cr.c.seq)).where(cr.c.claim_id == o["id"])).scalar() or 0) + 1
        ctx.conn.execute(cr.insert().values(claim_id=o["id"], seq=seq, kind=d["kind"],
                                            response_date=date.fromisoformat(d["response_date"]), summary=d["summary"],
                                            reference=d["reference"] or None, authority=auth.describe(),
                                            responded_by=ctx.user_id, responded_at=ctx.now))
        n = self._update_versioned(ctx, claims, o, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "claim", o["id"], "responded", {"seq": seq, "kind": d["kind"], "response_date": d["response_date"],
                                                            "reference": d["reference"] or None, "note": d["summary"]}, auth)
        ctx.log("claim", o["id"], auth, o["version"], n["version"], {"event": "ClaimResponded", "seq": seq, "kind": d["kind"]})
        return {"claim": self.v_claim(ctx.conn, n)}

    def _quality_reminders(self, conn, uid: str, now) -> int:
        """Напоминания о сроке ответа заказчику — при обновлении (pull) этого пользователя (отдельного планировщика
        нет). Уведомление «срок подходит» (за DUE_SOON_DAYS дней и в день срока) или «срок прошёл» создаётся один раз
        на установленный срок и вид — только тем, кто ведёт рекламацию (_claim_leads) и видит её (quality.read).
        Возвращает число созданных уведомлений.
        """
        today = now.date()
        ct, cr, nt = claim_terms, claim_responses, notifications
        rows = conn.execute(sa.select(claims, ct.c.response_due, ct.c.due_set_at).join(ct, ct.c.claim_id == claims.c.id)
                            .where(claims.c.source == "customer", ct.c.response_due.isnot(None),
                                   ct.c.response_due <= today + timedelta(days=qa.DUE_SOON_DAYS))
                            .order_by(claims.c.claim_no)).mappings().all()
        made = 0
        for r in rows:
            o = dict(r)
            answered = conn.execute(sa.select(sa.func.count()).select_from(cr).where(
                cr.c.claim_id == o["id"], cr.c.responded_at >= o["due_set_at"])).scalar_one()
            if answered or uid not in self._claim_leads(conn, o, now):
                continue
            if not self.policy.can(conn, uid, "quality.read", now, o["project_id"]):
                continue
            kind = "claim_overdue" if o["response_due"] < today else "claim_due_soon"
            seen = conn.execute(sa.select(sa.func.count()).select_from(nt).where(
                nt.c.user_id == uid, nt.c.kind == kind, nt.c.object_type == "claim", nt.c.object_id == o["id"],
                nt.c.created_at >= o["due_set_at"])).scalar_one()
            if seen:
                continue
            title = (f"Рекламация № {o['claim_no']}: срок ответа заказчику прошёл ({o['response_due']:%d.%m.%Y})"
                     if kind == "claim_overdue" else
                     f"Рекламация № {o['claim_no']}: ответ заказчику — до {o['response_due']:%d.%m.%Y}")
            conn.execute(nt.insert().values(user_id=uid, kind=kind, title=title[:200], body=None, object_type="claim",
                                            object_id=o["id"], actor_id=None, created_at=now, read_at=None))
            made += 1
        return made

    # ------------------------------------------------------------ ECR
    def _ecr_state(self, conn, o: dict, *allowed: str) -> None:
        """Запрос в одном из состояний allowed, иначе видимый конфликт."""
        if o["status"] not in allowed:
            raise Conflict("ecr_state", f"Запрос № {o['ecr_no']} уже «{qa.ECR_STATUS_LABELS_RU[o['status']]}» — действие не "
                                        "применено.", {"current": self.v_change_request(conn, o)})

    def _ecr_submit(self, ctx, p, ev):
        """ecr.submit {id, project_id, title, reason, proposal, kit_id?, claim_id?} — запрос на изменение."""
        only_keys(p, {"id", "project_id", "title", "reason", "proposal", "kit_id", "claim_id"})
        rid = req_uuid(p, "id")
        pr = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "ecr.submit", ctx.now, project_id=pr["id"])
        self._ensure_new(ctx.conn, change_requests, rid)
        d = _q(qa.parse_ecr, p)
        kit_id = p.get("kit_id") or None
        if kit_id is not None:
            k = self._get(ctx.conn, kits, req_uuid(p, "kit_id"))
            if k is None or k["project_id"] != pr["id"]:
                raise Rejected("kit_invalid", "Изделие (комплект) — из этого проекта.", {"field": "kit_id"})
        claim_id = p.get("claim_id") or None
        if claim_id is not None:
            c = self._get(ctx.conn, claims, req_uuid(p, "claim_id"))
            if c is None or c["project_id"] != pr["id"]:
                raise Rejected("claim_invalid", "Рекламация — из этого проекта.", {"field": "claim_id"})
        no = (ctx.conn.execute(sa.select(sa.func.max(change_requests.c.ecr_no))).scalar() or 0) + 1
        ctx.conn.execute(change_requests.insert().values(
            id=rid, ecr_no=no, project_id=pr["id"], kit_id=kit_id, claim_id=claim_id, **d, status="submitted",
            version=1, created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        self._qdecide(ctx, "ecr", rid, "submitted", {"ecr_no": no, "kit_id": kit_id, "claim_id": claim_id}, auth)
        ctx.log("change_request", rid, auth, None, 1, {"event": "EcrSubmitted", "ecr_no": no, "claim_id": claim_id})
        self._qnotify_project(ctx, pr["id"], ("rp", "gip"), "ecr_new", f"Запрос на изменение № {no}: {d['title']}", rid,
                              "change_request")
        return {"change_request": self.v_change_request(ctx.conn, self._get(ctx.conn, change_requests, rid))}

    def _ecr_assess(self, ctx, p, ev):
        """ecr.assess {id, impact} — оценка влияния («Подан» → «Оценён»)."""
        only_keys(p, {"id", "impact"})
        o = self._require(ctx.conn, change_requests, req_uuid(p, "id"), "Запрос на изменение")
        auth = self.policy.check(ctx.conn, ctx.user_id, "ecr.assess", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_change_request(ctx.conn, x))
        self._ecr_state(ctx.conn, o, "submitted")
        impact = _q(qa._text, p.get("impact"), "impact", "оценку влияния (что затронет, сроки, затраты)", qa.MAX_TEXT)
        n = self._update_versioned(ctx, change_requests, o, {"status": "assessed", "impact": impact, "assessed_by": ctx.user_id,
                                                             "assessed_at": ctx.now, "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "ecr", o["id"], "assessed", {"impact": impact}, auth)
        ctx.log("change_request", o["id"], auth, o["version"], n["version"], {"event": "EcrAssessed"})
        self._qnotify_project(ctx, o["project_id"], ("gip",), "ecr_assessed",
                              f"Запрос на изменение № {o['ecr_no']} оценён — нужно решение", o["id"], "change_request")
        return {"change_request": self.v_change_request(ctx.conn, n)}

    def _ecr_decide(self, ctx, p, ev):
        """ecr.decide {id, decision, reason} — решение ГИП проекта, не автора запроса («Принят» / «Отклонён»)."""
        only_keys(p, {"id", "decision", "reason"})
        o = self._require(ctx.conn, change_requests, req_uuid(p, "id"), "Запрос на изменение")
        auth = self.policy.check(ctx.conn, ctx.user_id, "ecr.decide", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_change_request(ctx.conn, x))
        self._ecr_state(ctx.conn, o, "assessed")
        if o["created_by"] == ctx.user_id:
            raise Denied("four_eyes", "Решение по своему запросу принимает другой человек («четыре глаза»).")
        dec = p.get("decision")
        if dec not in ("accept", "reject"):
            raise Rejected("invalid_decision", "Решение — «Принять» или «Отклонить».", {"field": "decision"})
        reason = _q(qa._text, p.get("reason"), "reason", "обоснование решения", qa.MAX_TEXT)
        st = "accepted" if dec == "accept" else "rejected"
        n = self._update_versioned(ctx, change_requests, o, {"status": st, "decision_reason": reason, "decided_by": ctx.user_id,
                                                             "decided_at": ctx.now, "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "ecr", o["id"], st, {"reason": reason}, auth)
        ctx.log("change_request", o["id"], auth, o["version"], n["version"], {"event": "EcrAccepted" if st == "accepted" else "EcrRejected"})
        self._notify(ctx, [o["created_by"], o["assessed_by"]], "ecr_decided",
                     f"Запрос на изменение № {o['ecr_no']}: {qa.ECR_STATUS_LABELS_RU[st].lower()}",
                     object_type="change_request", object_id=o["id"])
        return {"change_request": self.v_change_request(ctx.conn, n)}

    # ------------------------------------------------------------ ECO
    def _eco_state(self, conn, o: dict, *allowed: str) -> None:
        """Извещение в одном из состояний allowed, иначе видимый конфликт."""
        if o["status"] not in allowed:
            raise Conflict("eco_state", f"Извещение № {o['eco_no']} уже «{qa.ECO_STATUS_LABELS_RU[o['status']]}» — действие не "
                                        "применено.", {"current": self.v_change_order(conn, o)})

    def _eco_items(self, conn, project_id: str, raw: Any) -> list[dict]:
        """Строки извещения с точными версиями: документ этого проекта и существующая версия (+ обозначение, имя файла,
        SHA-256); техпроцесс этого проекта (+ изделие, редакция, статус, SHA-256 содержимого).
        """
        out = []
        for it in _q(qa.parse_eco_items, raw):
            if it["kind"] == "document":
                d = self._get(conn, documents, it["document_id"])
                if d is None or d["project_id"] != project_id:
                    raise Rejected("document_invalid", "Документ извещения — документ этого проекта.", {"field": "items"})
                v = conn.execute(sa.select(document_versions).where(document_versions.c.document_id == d["id"],
                                                                    document_versions.c.version_no == it["from_version"])).first()
                if v is None:
                    raise Rejected("version_not_found", f"У документа {d['code']} нет версии {it['from_version']}.", {"field": "items"})
                out.append({**it, "code": d["code"], "title": d["title"], "file_name": v.file_name, "sha256": v.sha256})
            else:
                t = self._get(conn, tech_processes, it["tp_id"])
                if t is None or t["project_id"] != project_id:
                    raise Rejected("tp_invalid", "Техпроцесс извещения — из этого проекта.", {"field": "items"})
                k = self._get(conn, kits, t["kit_id"])
                out.append({**it, "kit_id": t["kit_id"], "kit_code": k["code"], "revision_no": t["revision_no"],
                            "title": t["title"], "tp_status": t["status"], "content_hash": t["content_hash"]})
        return out

    def _eco_create(self, ctx, p, ev):
        """eco.create {id, ecr_id, title?, description, items} — извещение по принятому запросу (одно на запрос)."""
        only_keys(p, {"id", "ecr_id", "title", "description", "items"})
        eid = req_uuid(p, "id")
        ecr = self._require(ctx.conn, change_requests, req_uuid(p, "ecr_id"), "Запрос на изменение")
        auth = self.policy.check(ctx.conn, ctx.user_id, "eco.create", ctx.now, project_id=ecr["project_id"])
        self._ensure_new(ctx.conn, change_orders, eid)
        if ecr["status"] != "accepted":
            raise Rejected("ecr_not_accepted", f"Извещение составляется по принятому запросу (запрос № {ecr['ecr_no']} — "
                                               f"«{qa.ECR_STATUS_LABELS_RU[ecr['status']]}»).")
        other = self._get(ctx.conn, change_orders, ecr["id"], "ecr_id")
        if other is not None:
            raise Conflict("eco_exists", f"По запросу № {ecr['ecr_no']} уже есть извещение № {other['eco_no']} — откройте его.",
                           {"current": self.v_change_order(ctx.conn, other)})
        title = _q(qa._text, p.get("title"), "title", "", qa.MAX_TITLE, required=False) or ecr["title"]
        desc = _q(qa._text, p.get("description"), "description", "что и как изменяется", qa.MAX_TEXT)
        items = self._eco_items(ctx.conn, ecr["project_id"], p.get("items"))
        no = (ctx.conn.execute(sa.select(sa.func.max(change_orders.c.eco_no))).scalar() or 0) + 1
        ctx.conn.execute(change_orders.insert().values(
            id=eid, eco_no=no, ecr_id=ecr["id"], project_id=ecr["project_id"], title=title, description=desc,
            items_json=canon(items), status="draft", version=1, created_by=ctx.user_id, created_at=ctx.now,
            updated_by=ctx.user_id, updated_at=ctx.now))
        self._qdecide(ctx, "eco", eid, "created", {"eco_no": no, "ecr_id": ecr["id"], "items": len(items)}, auth)
        ctx.log("change_order", eid, auth, None, 1, {"event": "EcoCreated", "eco_no": no, "ecr_id": ecr["id"]})
        self._qnotify_project(ctx, ecr["project_id"], ("gip",), "eco_approval", f"Извещение № {no} ждёт утверждения", eid,
                              "change_order")
        return {"change_order": self.v_change_order(ctx.conn, self._get(ctx.conn, change_orders, eid)),
                "change_request": self.v_change_request(ctx.conn, ecr)}

    def _eco_save(self, ctx, p, ev):
        """eco.save {id, title?, description?, items?} — правка черновика (переданное заменяется целиком)."""
        only_keys(p, {"id", "title", "description", "items"})
        o = self._require(ctx.conn, change_orders, req_uuid(p, "id"), "Извещение")
        auth = self.policy.check(ctx.conn, ctx.user_id, "eco.save", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_change_order(ctx.conn, x))
        self._eco_state(ctx.conn, o, "draft")
        vals: dict[str, Any] = {}
        if "title" in p:
            vals["title"] = _q(qa._text, p.get("title"), "title", "заголовок", qa.MAX_TITLE)
        if "description" in p:
            vals["description"] = _q(qa._text, p.get("description"), "description", "что и как изменяется", qa.MAX_TEXT)
        if "items" in p:
            vals["items_json"] = canon(self._eco_items(ctx.conn, o["project_id"], p.get("items")))
        changed = sorted(k for k, v in vals.items() if v != o[k])
        if not changed:
            return {"change_order": self.v_change_order(ctx.conn, o), "unchanged": True}
        n = self._update_versioned(ctx, change_orders, o, {**vals, "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "eco", o["id"], "saved", {"changed": changed}, auth)
        ctx.log("change_order", o["id"], auth, o["version"], n["version"], {"event": "EcoSaved", "changed": changed})
        return {"change_order": self.v_change_order(ctx.conn, n)}

    def _eco_approve(self, ctx, p, ev):
        """eco.approve {id} — утверждение ГИП проекта, не составителем и не правившим («четыре глаза»). Версии строк
        перепроверяются: всё, на что ссылается извещение, существует (событие ECOReleased).
        """
        only_keys(p, {"id"})
        o = self._require(ctx.conn, change_orders, req_uuid(p, "id"), "Извещение")
        auth = self.policy.check(ctx.conn, ctx.user_id, "eco.approve", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_change_order(ctx.conn, x))
        self._eco_state(ctx.conn, o, "draft")
        if ctx.user_id in self._eco_editors(ctx.conn, o):
            raise Denied("four_eyes", "Извещение утверждает не тот, кто его составлял или правил («четыре глаза»).")
        items = json.loads(o["items_json"])
        fresh = self._eco_items(ctx.conn, o["project_id"], [{k: it[k] for k in ("kind", "document_id", "from_version", "tp_id") if k in it}
                                                            for it in items])
        if [qa.item_key(x) + (x.get("sha256") or x.get("content_hash"),) for x in fresh] != \
           [qa.item_key(x) + (x.get("sha256") or x.get("content_hash"),) for x in items]:
            raise Conflict("eco_items_changed", "Затронутые версии изменились после составления — обновите извещение.",
                           {"current": self.v_change_order(ctx.conn, o)})
        n = self._update_versioned(ctx, change_orders, o, {"status": "approved", "approved_by": ctx.user_id, "approved_at": ctx.now,
                                                           "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "eco", o["id"], "approved", {"items_hash": lc.canon_hash(items)}, auth)
        ctx.log("change_order", o["id"], auth, o["version"], n["version"],
                {"event": "ECOReleased", "eco_no": o["eco_no"], "items_hash": lc.canon_hash(items)})
        self._qnotify_project(ctx, o["project_id"], ("rp",), "eco_approved", f"Извещение № {o['eco_no']} утверждено — "
                              "можно внедрять", o["id"], "change_order", extra=(o["created_by"],))
        return {"change_order": self.v_change_order(ctx.conn, n)}

    def _eco_implement(self, ctx, p, ev):
        """eco.implement {id, results} — на какие версии заменены затронутые: документ — существующая версия новее
        прежней; техпроцесс — УТВЕРЖДЁННАЯ редакция того же изделия, новее прежней.
        """
        only_keys(p, {"id", "results"})
        o = self._require(ctx.conn, change_orders, req_uuid(p, "id"), "Извещение")
        auth = self.policy.check(ctx.conn, ctx.user_id, "eco.implement", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_change_order(ctx.conn, x))
        self._eco_state(ctx.conn, o, "approved")
        items = json.loads(o["items_json"])
        res = _q(qa.parse_eco_results, items, p.get("results"))
        out = []
        for it, r in zip(items, res):
            if it["kind"] == "document":
                v = ctx.conn.execute(sa.select(document_versions).where(document_versions.c.document_id == it["document_id"],
                                                                        document_versions.c.version_no == r["to_version"])).first()
                if v is None:
                    raise Rejected("version_not_found", f"У документа {it['code']} нет версии {r['to_version']} — сначала "
                                                        "загрузите новую версию.", {"field": "results", "index": r["index"]})
                out.append({**r, "file_name": v.file_name, "sha256": v.sha256})
            else:
                t = self._get(ctx.conn, tech_processes, req_uuid(r, "to_tp_id"))
                if t is None or t["kit_id"] != it["kit_id"] or t["revision_no"] <= it["revision_no"]:
                    raise Rejected("tp_not_newer", f"Новая редакция техпроцесса — того же изделия ({it['kit_code']}) и новее "
                                                   f"редакции {it['revision_no']}.", {"field": "results", "index": r["index"]})
                if t["status"] != "approved":
                    raise Rejected("tp_not_approved", f"Редакция {t['revision_no']} ещё не утверждена технологом.",
                                   {"field": "results", "index": r["index"]})
                out.append({**r, "revision_no": t["revision_no"], "content_hash": t["content_hash"]})
        n = self._update_versioned(ctx, change_orders, o, {"status": "implemented", "results_json": canon(out),
                                                           "implemented_by": ctx.user_id, "implemented_at": ctx.now,
                                                           "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "eco", o["id"], "implemented", {"results": out}, auth)
        ctx.log("change_order", o["id"], auth, o["version"], n["version"], {"event": "EcoImplemented", "results": len(out)})
        self._qnotify_project(ctx, o["project_id"], ("gip", "rp"), "eco_implemented",
                              f"Извещение № {o['eco_no']} внедрено — нужна проверка", o["id"], "change_order",
                              extra=tuple(self._project_otk(ctx.conn, o["project_id"], ctx.now)))
        return {"change_order": self.v_change_order(ctx.conn, n)}

    def _eco_verify(self, ctx, p, ev):
        """eco.verify {id, note} — проверка внедрения не тем, кто внедрял («Внедрено» → «Проверено»)."""
        only_keys(p, {"id", "note"})
        o = self._require(ctx.conn, change_orders, req_uuid(p, "id"), "Извещение")
        auth = self.policy.check(ctx.conn, ctx.user_id, "eco.verify", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_change_order(ctx.conn, x))
        self._eco_state(ctx.conn, o, "implemented")
        if o["implemented_by"] == ctx.user_id:
            raise Denied("verifier_not_independent", "Внедрение проверяет не тот, кто его отмечал.")
        note = _q(qa._text, p.get("note"), "note", "что проверено (документы выданы, техпроцесс применяется)", qa.MAX_TEXT)
        n = self._update_versioned(ctx, change_orders, o, {"status": "verified", "verified_by": ctx.user_id, "verified_at": ctx.now,
                                                           "verify_note": note, "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._qdecide(ctx, "eco", o["id"], "verified", {"note": note}, auth)
        ctx.log("change_order", o["id"], auth, o["version"], n["version"], {"event": "EcoVerified"})
        self._notify(ctx, [o["implemented_by"], o["created_by"]], "eco_verified", f"Извещение № {o['eco_no']} проверено",
                     object_type="change_order", object_id=o["id"])
        return {"change_order": self.v_change_order(ctx.conn, n)}

    # ------------------------------------------------------------ pull
    def _pull_quality(self, conn, uid: str, now) -> dict:
        """Рекламации, запросы и извещения — по праву quality.read в проекте; справочник для строк извещения
        (документы с номерами версий, техпроцессы с редакциями) — тем, кто может составлять извещения (eco.edit).
        """
        out: dict[str, Any] = {"claims": [], "change_requests": [], "change_orders": [], "quality_refs": [],
                               "quality_projects": [], "quality_otk": {}, "claim_batches": []}
        prs = conn.execute(sa.select(projects.c.id, projects.c.code, projects.c.title).order_by(projects.c.code)).all()
        readable = [r.id for r in prs if self.policy.can(conn, uid, "quality.read", now, r.id)]
        if not readable:
            return out
        # D-40: проекты (код и название), где можно регистрировать рекламации и подавать запросы; сотрудники ОТК по
        # проектам (D-41: {проект: [id]} — пометка «(ОТК)» в списке ответственных и исполнителей); выполненные наряды —
        # для рекламации на партию.
        reg = set()
        for r in prs:
            if r.id in readable:
                can_reg = self.policy.can(conn, uid, "claim.register", now, r.id)
                reg |= {r.id} if can_reg else set()
                out["quality_projects"].append({"id": r.id, "code": r.code, "title": r.title, "can_claim": can_reg,
                                                "can_ecr": self.policy.can(conn, uid, "ecr.submit", now, r.id)})
        out["quality_otk"] = {pid: u for pid in readable if (u := self._project_otk(conn, pid, now))}
        if reg:
            for r in conn.execute(sa.select(work_orders, kits.c.code.label("kit_code")).join(kits, kits.c.id == work_orders.c.kit_id)
                                  .where(work_orders.c.project_id.in_(reg), work_orders.c.status.in_(BATCH_WO_STATES))
                                  .order_by(work_orders.c.doc_no)):
                n = conn.execute(sa.select(sa.func.count()).select_from(product_instances)
                                 .where(product_instances.c.work_order_id == r.id)).scalar_one()
                out["claim_batches"].append({"id": r.id, "project_id": r.project_id, "doc_no": r.doc_no, "kit_code": r.kit_code,
                                             "qty": r.qty, "title": r.title, "instances": n})
        for t, key, view, order in ((claims, "claims", self.v_claim, claims.c.claim_no),
                                    (change_requests, "change_requests", self.v_change_request, change_requests.c.ecr_no),
                                    (change_orders, "change_orders", self.v_change_order, change_orders.c.eco_no)):
            for r in conn.execute(sa.select(t).where(t.c.project_id.in_(readable)).order_by(order)):
                out[key].append(view(conn, dict(r._mapping)))
        edit = [pid for pid in readable if self.policy.can(conn, uid, "eco.edit", now, pid)]
        if edit:
            for r in conn.execute(sa.select(documents).where(documents.c.project_id.in_(edit), documents.c.current_version_no > 0)
                                  .order_by(documents.c.code)):
                out["quality_refs"].append({"kind": "document", "id": r.id, "project_id": r.project_id, "code": r.code,
                                            "title": r.title, "current_version_no": r.current_version_no})
            for r in conn.execute(sa.select(tech_processes, kits.c.code.label("kit_code")).join(kits, kits.c.id == tech_processes.c.kit_id)
                                  .where(tech_processes.c.project_id.in_(edit))
                                  .order_by(kits.c.code, tech_processes.c.revision_no)):
                out["quality_refs"].append({"kind": "tech_process", "id": r.id, "project_id": r.project_id, "kit_id": r.kit_id,
                                            "kit_code": r.kit_code, "revision_no": r.revision_no, "title": r.title,
                                            "status": r.status})
        return out

    def _quality_project(self, conn, otype: str, oid: str) -> Optional[str]:
        """Проект объекта качества (для истории и проверки прав чтения)."""
        t = {"claim": claims, "change_request": change_requests, "change_order": change_orders}[otype]
        o = self._get(conn, t, oid)
        return o["project_id"] if o else None
