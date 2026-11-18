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
from .db import (change_orders, change_requests, claim_actions, claims, document_versions, documents,
                 effectiveness_checks, instance_compositions, kits, passports, product_instances, projects,
                 quality_decisions, tech_processes)
from .validation import canon, iso, only_keys, req_uuid

# Подписи шагов журнала решений (история карточки).
DECISION_LABELS_RU = {
    "registered": "Зарегистрирована", "analysis_started": "Начат анализ причины", "planned": "Утверждён план действий",
    "actions_added": "Добавлены действия", "action_done": "Действие выполнено", "submitted_check": "Отправлена на проверку",
    "check_effective": "Проверка: действия эффективны", "check_not_effective": "Проверка: действия не эффективны",
    "submitted": "Подан", "assessed": "Оценён", "accepted": "Принят", "rejected": "Отклонён",
    "created": "Составлено", "saved": "Изменено", "approved": "Утверждено", "implemented": "Внедрено", "verified": "Проверено",
}


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
        """Сотрудник — участник проекта сейчас (своё назначение или замещение в проекте), иначе отказ по полю."""
        if not isinstance(uid, str) or not project_member(conn, uid, project_id, now):
            raise Rejected("not_project_member", "Выберите участника проекта.", {"field": field, "user_id": uid})
        return uid

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
        inst = self._get(conn, product_instances, o["instance_id"])
        kit = self._get(conn, kits, inst["kit_id"])
        acts = self._claim_actions(conn, o["id"])
        checks = [{"seq": r.seq, "effective": bool(r.effective), "evidence": r.evidence, "checked_by": r.checked_by,
                   "checked_at": iso(r.checked_at), "authority": r.authority}
                  for r in conn.execute(sa.select(effectiveness_checks).where(effectiveness_checks.c.claim_id == o["id"])
                                        .order_by(effectiveness_checks.c.seq))]
        ecrs = [{"id": r.id, "ecr_no": r.ecr_no, "status": r.status, "title": r.title}
                for r in conn.execute(sa.select(change_requests).where(change_requests.c.claim_id == o["id"])
                                      .order_by(change_requests.c.ecr_no))]
        return {"id": o["id"], "claim_no": o["claim_no"], "project_id": o["project_id"], "instance_id": o["instance_id"],
                "instance_label": f"{kit['code']} {lc.serial_label(inst['series'], inst['serial_no'])}",
                "kit_id": inst["kit_id"], "work_order_id": inst["work_order_id"],
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
        """claim.register {id, instance_id, received_date, source, description, reference?} — рекламация по экземпляру."""
        only_keys(p, {"id", "instance_id", "received_date", "source", "description", "reference"})
        cid = req_uuid(p, "id")
        inst = self._require(ctx.conn, product_instances, req_uuid(p, "instance_id"), "Экземпляр")
        auth = self.policy.check(ctx.conn, ctx.user_id, "claim.register", ctx.now, project_id=inst["project_id"])
        self._ensure_new(ctx.conn, claims, cid)
        d = _q(qa.parse_claim, p)
        self._not_future(ctx, d["received_date"], "received_date", "Дата получения")
        no = (ctx.conn.execute(sa.select(sa.func.max(claims.c.claim_no))).scalar() or 0) + 1
        ctx.conn.execute(claims.insert().values(
            id=cid, claim_no=no, project_id=inst["project_id"], instance_id=inst["id"],
            received_date=date.fromisoformat(d["received_date"]), source=d["source"], reference=d["reference"] or None,
            description=d["description"], status="registered", version=1, created_by=ctx.user_id, created_at=ctx.now,
            updated_by=ctx.user_id, updated_at=ctx.now))
        self._qdecide(ctx, "claim", cid, "registered", {"claim_no": no, "instance_id": inst["id"], "source": d["source"]}, auth)
        ctx.log("claim", cid, auth, None, 1, {"event": "ClaimRegistered", "claim_no": no, "instance_id": inst["id"]})
        self._qnotify_project(ctx, inst["project_id"], ("rp", "gip"), "claim_new", f"Рекламация № {no}: новая", cid, "claim")
        return {"claim": self.v_claim(ctx.conn, self._get(ctx.conn, claims, cid))}

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
        only_keys(p, {"id", "cause", "actions"})
        o = self._require(ctx.conn, claims, req_uuid(p, "id"), "Рекламация")
        auth = self.policy.check(ctx.conn, ctx.user_id, "claim.plan", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda x: self.v_claim(ctx.conn, x))
        self._claim_state(ctx.conn, o, "analysis", "action")
        first = o["status"] == "analysis"
        cause = _q(qa._text, p.get("cause"), "cause", "найденную причину", qa.MAX_TEXT, required=first)
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
        seq = ctx.conn.execute(sa.select(sa.func.max(claim_actions.c.seq)).where(claim_actions.c.claim_id == o["id"])).scalar() or 0
        for a in acts:
            seq += 1
            ctx.conn.execute(claim_actions.insert().values(
                id=a["id"], claim_id=o["id"], seq=seq, kind=a["kind"], description=a["description"], owner_id=a["owner_id"],
                due_date=date.fromisoformat(a["due_date"]), status="open", planned_by=ctx.user_id, planned_at=ctx.now))
        vals = {"status": "action", "updated_by": ctx.user_id, "updated_at": ctx.now}
        if cause:
            vals["cause"] = cause
        n = self._update_versioned(ctx, claims, o, vals)
        self._qdecide(ctx, "claim", o["id"], "planned" if first else "actions_added",
                      {"cause": cause or None, "actions": [{k: a[k] for k in ("id", "kind", "owner_id", "due_date")} for a in acts]}, auth)
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
                              extra=tuple(self._users_with_role(ctx.conn, "dept_head", ctx.now)), skip=tuple(busy))
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
                              f"Извещение № {o['eco_no']} внедрено — нужна проверка", o["id"], "change_order")
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
        out: dict[str, Any] = {"claims": [], "change_requests": [], "change_orders": [], "quality_refs": []}
        pids = [r.id for r in conn.execute(sa.select(projects.c.id).order_by(projects.c.code))]
        readable = [pid for pid in pids if self.policy.can(conn, uid, "quality.read", now, pid)]
        if not readable:
            return out
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
