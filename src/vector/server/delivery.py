"""Сквозные процессы проекта: лиды, потоки, риск-разрешения, события, архив (D-56, blueprint 52).

Модуль — примесь (mixin) к `Service`: обработчики команд opportunity.*, project.transition, workstream.* и
risk.* пользуются общей инфраструктурой сервиса (транзакция, идемпотентность по operation_id, журнал аудита,
проверка версий). Чистые правила домена — vector.core.delivery (общие с клиентом).

Главные инварианты (каждый закреплён тестом в tests/server/test_delivery.py):

* Лид — отдельная сущность (решение владельца, п. 5): тендерный отдел и менеджеры ведут возможности продаж,
  конвертация в тендер сохраняет связь (converted_tender_id) и не копирует записи бесконтрольно.
* Потоки проекта идут параллельно; projects.status — грубая рамка («в работе»/«завершается»/«архив»/«отменён»),
  детальные этапы — в project_workstreams. Переходы — только по контракту workflows.
* Шлюзы (ответ владельца, пп. 2–3): «заказано» в закупке и «запущена» в производстве требуют РКД, согласованной
  ЗАКАЗЧИКОМ (approved_for_use), либо действующего риск-разрешения РП; истёкшее разрешение шлюз не открывает.
* Риск-разрешение принимает РП проекта (ответ владельца, п. 1), обязательны причина и срок; снятие — с причиной.
  Разрешение не меняет статус документа и не является его утверждением.
* Каждая веха пишется в неизменяемый журнал событий проекта (project_events) — его читает карточка проекта.
* Архив — только когда все потоки в конечных статусах и нет действующих риск-разрешений; при закрытии создаётся
  неизменяемый снимок (project_closeouts) с контрольной суммой.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import delivery as dl
from vector.core.errors import Rejected

from .authz import project_member
from .db import (checklist_templates, customers, documents, fat_findings, fat_sessions, kits, pnr_requests,
                 project_closeouts, project_events, project_risk_acceptances, project_workstreams, projects,
                 sales_opportunities, tasks, tenders, users)
from .validation import iso, only_keys, opt_date, opt_str, req_str, req_uuid


class DeliveryMixin:
    """Обработчики лидов, потоков проекта, риск-разрешений и архива; представления для pull."""

    # ------------------------------------------------------------ представления (то, что видит клиент)
    @staticmethod
    def v_opportunity(o: dict) -> dict:
        """Лид для клиента: источник, заказчик (из справочника или текстом), ответственный, статус, связь с тендером."""
        return {"id": o["id"], "title": o["title"], "source": o["source"], "source_detail": o["source_detail"],
                "source_label": dl.OPPORTUNITY_SOURCES[o["source"]], "customer_id": o["customer_id"],
                "customer_name": o["customer_name"], "owner_user_id": o["owner_user_id"],
                "expected_on": o["expected_on"].isoformat() if o["expected_on"] else None, "note": o["note"],
                "status": o["status"], "status_reason": o["status_reason"],
                "converted_tender_id": o["converted_tender_id"], "version": o["version"],
                "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_checklist(o: dict) -> dict:
        """Шаблон чек-листа для клиента: разделы и градации замечаний распакованы из JSON."""
        return {"id": o["id"], "kind": o["kind"], "kind_label": dl.CHECKLIST_KINDS_RU[o["kind"]],
                "name": o["name"], "sections": dl.parse_sections(o["sections_json"]),
                "severities": dl.parse_severities(o["severities_json"]), "active": bool(o["active"]),
                "version": o["version"], "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_pnr(o: dict) -> dict:
        """Запрос ПНР для клиента: вид, питание, плановая дата, статус."""
        kinds = dl.PNR_KINDS_RU
        return {"id": o["id"], "project_id": o["project_id"], "kit_id": o["kit_id"], "kind": o["kind"],
                "kind_label": kinds[o["kind"]], "title": o["title"], "need_power": bool(o["need_power"]),
                "planned_on": o["planned_on"].isoformat() if o["planned_on"] else None, "note": o["note"],
                "status": o["status"], "status_reason": o["status_reason"], "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_fat(o: dict) -> dict:
        """FAT-сессия для клиента: вид, итог, шаблон (снимок разделов и градаций), кто начал/завершил."""
        return {"id": o["id"], "project_id": o["project_id"], "seq": o["seq"], "kit_id": o["kit_id"],
                "kind": o["kind"],
                "template_id": o["template_id"], "template_name": o["template_name"],
                "sections": dl.parse_sections(o["sections_json"]),
                "severities": dl.parse_severities(o["severities_json"]),
                "status": o["status"], "note": o["note"], "version": o["version"],
                "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "finished_by": o["finished_by"], "finished_at": iso(o["finished_at"]) if o["finished_at"] else None}

    @staticmethod
    def v_finding(o: dict) -> dict:
        """Замечание FAT: раздел, градация (снимок), блокирует ли отгрузку, статус, закрытие. К проекту, не к
        человеку (ответ владельца п. 5) — автора хранит журнал аудита, в представлении его нет."""
        return {"id": o["id"], "fat_id": o["fat_id"], "seq": o["seq"], "section_code": o["section_code"],
                "severity": o["severity"], "severity_label": o["severity_label"], "blocking": bool(o["blocking"]),
                "text": o["text"], "status": o["status"], "close_reason": o["close_reason"],
                "version": o["version"], "created_at": iso(o["created_at"]),
                "closed_at": iso(o["closed_at"]) if o["closed_at"] else None}

    @staticmethod
    def v_workstream(o: dict) -> dict:
        """Поток проекта для клиента: вид, статус, ответственный, плановые даты, версия."""
        return {"id": o["id"], "project_id": o["project_id"], "kind": o["kind"],
                "kind_label": dl.STREAM_LABELS_RU[o["kind"]], "status": o["status"],
                "responsible_user_id": o["responsible_user_id"],
                "planned_start": o["planned_start"].isoformat() if o["planned_start"] else None,
                "planned_due": o["planned_due"].isoformat() if o["planned_due"] else None,
                "note": o["note"], "version": o["version"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_risk(o: dict, today) -> dict:
        """Риск-разрешение для клиента: вид, причина, срок, вычисленное состояние (active/expired/…), снятие."""
        return {"id": o["id"], "project_id": o["project_id"], "kind": o["kind"],
                "scope": o["scope_json"], "reason": o["reason"], "mitigation": o["mitigation"],
                "due_on": o["due_on"].isoformat(), "status": o["status"], "state": dl.risk_state(o, today),
                "accepted_by": o["accepted_by"], "created_at": iso(o["created_at"]),
                "resolved_by": o["resolved_by"], "resolved_at": iso(o["resolved_at"]),
                "resolution_reason": o["resolution_reason"], "version": o["version"]}

    @staticmethod
    def v_project_event(o: dict) -> dict:
        """Событие проекта для ленты в карточке: вид, краткий текст, кто и когда."""
        return {"id": o["id"], "project_id": o["project_id"], "seq": o["seq"], "kind": o["kind"],
                "object_type": o["object_type"], "object_id": o["object_id"], "summary": o["summary"],
                "actor_id": o["actor_id"], "at": iso(o["at"])}

    # ------------------------------------------------------------ общие проверки и журнал проекта
    def _project_editable(self, pr: dict) -> None:
        """Лиды/потоки/риски меняются только в проекте в работе или завершаемом; архив и отмена — только чтение."""
        if pr["status"] in ("archived", "cancelled"):
            raise Rejected("project_state", "Проект в архиве или отменён — изменения запрещены (только чтение).",
                           {"project_status": pr["status"]})

    def _project_event(self, ctx, project_id: str, kind: str, summary: str, authority,
                       object_type: Optional[str] = None, object_id: Optional[str] = None,
                       details: Optional[dict] = None) -> None:
        """Записать веху в неизменяемый журнал событий проекта (следующий seq в проекте, в транзакции команды)."""
        seq = ctx.conn.execute(sa.select(sa.func.max(project_events.c.seq)).where(
            project_events.c.project_id == project_id)).scalar() or 0
        ctx.conn.execute(project_events.insert().values(
            project_id=project_id, seq=seq + 1, kind=kind, object_type=object_type,
            object_id=str(object_id) if object_id is not None else None, summary=summary[:500],
            details_json=dl.canon(details or {}), actor_id=ctx.user_id,
            authority=authority.describe() if authority else None, at=ctx.now))

    def _streams_init(self, ctx, project_id: str) -> None:
        """Создать шесть потоков проекта в начальных статусах контракта (вызывается из project.start_rkd).

        Схема старше v29 не знает таблиц потоков (миграция ещё не выполнена — сервер работает на старой БД
        только в тестах «старая версия → миграция»): тогда потоки пропускаются, их создаст миграция пустыми,
        а первые события появятся при первых действиях с проектом.
        """
        from . import db as _db
        if not sa.inspect(ctx.conn).has_table(_db.project_workstreams.name):
            return
        for kind in dl.STREAM_KINDS:
            ctx.conn.execute(project_workstreams.insert().values(
                id=str(uuid.uuid4()), project_id=project_id, kind=kind, status=dl.stream_initial(self.wf, kind),
                responsible_user_id=None, planned_start=None, planned_due=None, note=None, version=1,
                created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        self._project_event(ctx, project_id, "project_created", "Проект создан: открыты потоки «Договор», «РКД», "
                            "«Закупка», «Производство», «Отгрузка», «ПНР и ШМР на площадке»", None,
                            object_type="project", object_id=project_id)

    def _stream(self, ctx, project_id: str, kind: str) -> Optional[dict]:
        """Поток проекта по виду (None, если проект старый и потока нет)."""
        r = ctx.conn.execute(sa.select(project_workstreams).where(
            project_workstreams.c.project_id == project_id, project_workstreams.c.kind == kind)).first()
        return dict(r._mapping) if r else None

    def _project_rps(self, ctx, project_id: str) -> list[str]:
        """РП и ГИП проекта (и их замещающие) — адресаты уведомлений о рисках и статусах."""
        out: list[str] = []
        for role in ("rp", "gip"):
            out += self._project_role_users(ctx.conn, project_id, role, ctx.now)
        return list(dict.fromkeys(out))

    # ------------------------------------------------------------ лиды (возможности продаж)
    def _opportunity_customer(self, ctx, customer_id: Any, customer_name: Any) -> tuple[Optional[str], Optional[str]]:
        """Заказчик лида: из справочника (customer_id) или свободным текстом (customer_name); одно из двух обязательно."""
        cid = req_uuid({"customer_id": customer_id}, "customer_id") if customer_id is not None else None
        if cid is not None:
            self._require(ctx.conn, customers, cid, "Заказчик")
        name = opt_str({"customer_name": customer_name}, "customer_name", 300) if customer_name is not None else None
        if cid is None and not name:
            raise Rejected("customer_required", "Укажите заказчика: из справочника (customer_id) или текстом "
                           "(customer_name), если его ещё нет в справочнике.", {"field": "customer_id"})
        return cid, name

    def _opportunity_owner(self, ctx, value: Any) -> str:
        """Ответственный менеджер лида: существующий действующий пользователь; по умолчанию — автор записи."""
        if value is None:
            return ctx.user_id
        uid = req_uuid({"owner_user_id": value}, "owner_user_id")
        u = self._get(ctx.conn, users, uid)
        if not u or not u["active"]:
            raise Rejected("user_not_found", "Ответственный не найден или учётная запись отключена.",
                           {"field": "owner_user_id"})
        return uid

    def _opportunity_create(self, ctx, p, ev):
        """opportunity.create {id, title, source, source_detail?, customer_id?, customer_name?, owner_user_id?,
        expected_on?, note?} — зарегистрировать лид (заказ от тендерного отдела или менеджера). Можно офлайн.
        """
        only_keys(p, {"id", "title", "source", "source_detail", "customer_id", "customer_name", "owner_user_id",
                      "expected_on", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "opportunity.create", ctx.now)
        oid = req_uuid(p, "id")
        source = req_str(p, "source", 16)
        if source not in dl.OPPORTUNITY_SOURCES:
            raise Rejected("bad_source", "Источник лида: platform (площадка), manager (личный канал менеджера), "
                           "inbound (входящее обращение) или other.", {"field": "source"})
        cid, cname = self._opportunity_customer(ctx, p.get("customer_id"), p.get("customer_name"))
        owner = self._opportunity_owner(ctx, p.get("owner_user_id"))
        self._ensure_new(ctx.conn, sales_opportunities, oid)
        ctx.conn.execute(sales_opportunities.insert().values(
            id=oid, title=req_str(p, "title", 300), source=source,
            source_detail=opt_str(p, "source_detail", 300), customer_id=cid, customer_name=cname,
            owner_user_id=owner, expected_on=opt_date(p, "expected_on"), note=opt_str(p, "note", 2000),
            status=self.wf["sales_opportunity"]["initial"], status_reason=None, converted_tender_id=None,
            version=1, created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("sales_opportunity", oid, auth, None, 1, {"title": p["title"], "source": source, "owner": owner})
        return {"opportunity": self.v_opportunity(self._get(ctx.conn, sales_opportunities, oid))}

    def _opportunity_update(self, ctx, p, ev):
        """Правка лида (не в конечном статусе). Отсутствующий ключ — «не менять», null — «очистить» (необязательные)."""
        only_keys(p, {"id", "title", "source_detail", "customer_id", "customer_name", "owner_user_id", "expected_on",
                      "note"})
        o = self._require(ctx.conn, sales_opportunities, req_uuid(p, "id"), "Лид")
        auth = self.policy.check(ctx.conn, ctx.user_id, "opportunity.update", ctx.now)
        self._check_version(o, ev, self.v_opportunity)
        if o["status"] in self.wf["sales_opportunity"]["terminal"]:
            raise Rejected("opportunity_closed", "Лид в конечном статусе — изменение запрещено.")
        ch: dict[str, Any] = {}
        if "title" in p:
            ch["title"] = req_str(p, "title", 300)
        if "source_detail" in p:
            ch["source_detail"] = opt_str(p, "source_detail", 300)
        if "customer_id" in p or "customer_name" in p:
            ch["customer_id"], ch["customer_name"] = self._opportunity_customer(
                ctx, p.get("customer_id", o["customer_id"]), p.get("customer_name", o["customer_name"]))
        if "owner_user_id" in p:
            ch["owner_user_id"] = self._opportunity_owner(ctx, p["owner_user_id"])
        if "expected_on" in p:
            ch["expected_on"] = opt_date(p, "expected_on")
        if "note" in p:
            ch["note"] = opt_str(p, "note", 2000)
        ch = {k: v for k, v in ch.items() if v != o[k]}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: (v.isoformat() if k == "expected_on" and v else v) for k, v in ((k, o[k]) for k in ch)}
        n = self._update_versioned(ctx, sales_opportunities, o, {**ch, "updated_by": ctx.user_id,
                                                                 "updated_at": ctx.now})
        ctx.log("sales_opportunity", o["id"], auth, o["version"], n["version"], {"before": before, "after":
                {k: (v.isoformat() if k == "expected_on" and v else v) for k, v in ch.items()}})
        return {"opportunity": self.v_opportunity(n)}

    def _opportunity_transition(self, ctx, p, ev):
        """opportunity.transition {id, to, reason?} — сменить статус лида по контракту; «отклонён» — с причиной.
        Перевод в тендер — только командой opportunity.convert_to_tender (она создаёт тендер и связь).
        """
        only_keys(p, {"id", "to", "reason"})
        o = self._require(ctx.conn, sales_opportunities, req_uuid(p, "id"), "Лид")
        auth = self.policy.check(ctx.conn, ctx.user_id, "opportunity.transition", ctx.now)
        self._check_version(o, ev, self.v_opportunity)
        wf = self.wf["sales_opportunity"]
        to = p.get("to")
        if to == "tendered":
            raise Rejected("use_convert_command", "Перевод в тендер выполняет команда opportunity.convert_to_tender — "
                           "она создаёт тендер и сохраняет связь с лидом.", {"use": "opportunity.convert_to_tender"})
        allowed = wf["transitions"].get(o["status"], [])
        if to not in allowed:
            raise Rejected("transition_not_allowed",
                           f"Переход «{o['status']}» → «{to}» не разрешён. Допустимо: {', '.join(allowed) or 'нет'}.",
                           {"from": o["status"], "to": to, "allowed": allowed})
        reason = opt_str(p, "reason", 2000)
        if to in wf["reason_required_for"] and not reason:
            raise Rejected("reason_required", "Для отклонения лида укажите причину.", {"field": "reason"})
        n = self._update_versioned(ctx, sales_opportunities, o, {"status": to, "status_reason": reason,
                                                                 "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("sales_opportunity", o["id"], auth, o["version"], n["version"],
                {"from": o["status"], "to": to, "reason": reason})
        return {"opportunity": self.v_opportunity(n)}

    def _opportunity_convert_to_tender(self, ctx, p, ev):
        """opportunity.convert_to_tender {id, tender_id, title?, submission_deadline?} — создать тендер из
        квалифицированного лида и связать их. Ответственный по тендеру — ответственный лида. Только онлайн.
        """
        only_keys(p, {"id", "tender_id", "title", "submission_deadline"})
        o = self._require(ctx.conn, sales_opportunities, req_uuid(p, "id"), "Лид")
        auth = self.policy.check(ctx.conn, ctx.user_id, "opportunity.convert_to_tender", ctx.now)
        self._check_version(o, ev, self.v_opportunity)
        if o["status"] != "qualified":
            raise Rejected("opportunity_not_qualified", "В тендер переводится только квалифицированный лид "
                           "(сначала — статус «Квалифицирован»).", {"status": o["status"]})
        if o["customer_id"] is None:
            raise Rejected("customer_required", "Перед переводом в тендер укажите заказчика из справочника "
                           "(карточка лида, поле «Заказчик»).", {"field": "customer_id"})
        tid = req_uuid(p, "tender_id")
        self._ensure_new(ctx.conn, tenders, tid)
        title = opt_str(p, "title", 200) or o["title"]
        ctx.conn.execute(tenders.insert().values(
            id=tid, customer_id=o["customer_id"], title=title, submission_deadline=opt_date(p, "submission_deadline"),
            owner_user_id=o["owner_user_id"], status=self.wf["tender"]["initial"], status_reason=None, version=1,
            created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        n = self._update_versioned(ctx, sales_opportunities, o,
                                   {"status": "tendered", "status_reason": None, "converted_tender_id": tid,
                                    "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("sales_opportunity", o["id"], auth, o["version"], n["version"],
                {"to": "tendered", "tender_id": tid, "customer_id": o["customer_id"]})
        self._notify(ctx, [o["owner_user_id"]], "opportunity_converted", "Лид переведён в тендер",
                     f"«{o['title']}» — создан тендер, связь с лидом сохранена.",
                     object_type="sales_opportunity", object_id=o["id"])
        return {"opportunity": self.v_opportunity(n), "tender": self.v_tender(self._get(ctx.conn, tenders, tid))}

    # ------------------------------------------------------------ потоки проекта
    def _workstream_update(self, ctx, p, ev):
        """workstream.update {id, responsible_user_id?, planned_start?, planned_due?, note?} — план и ответственный
        потока. Ответственный — только участник этого проекта (или его замещающий).
        """
        only_keys(p, {"id", "responsible_user_id", "planned_start", "planned_due", "note"})
        o = self._require(ctx.conn, project_workstreams, req_uuid(p, "id"), "Поток проекта")
        pr = self._get(ctx.conn, projects, o["project_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "workstream.update", ctx.now, project_id=o["project_id"])
        self._project_editable(pr)
        self._check_version(o, ev, self.v_workstream)
        ch: dict[str, Any] = {}
        if "responsible_user_id" in p:
            uid = p["responsible_user_id"]
            if uid is not None:
                uid = req_uuid({"responsible_user_id": uid}, "responsible_user_id")
                if not project_member(ctx.conn, uid, o["project_id"], ctx.now):
                    raise Rejected("not_project_member", "Ответственным за поток может быть только участник команды "
                                   "этого проекта (или его замещающий).", {"field": "responsible_user_id"})
            ch["responsible_user_id"] = uid
        if "planned_start" in p:
            ch["planned_start"] = opt_date(p, "planned_start")
        if "planned_due" in p:
            ch["planned_due"] = opt_date(p, "planned_due")
        start, due = ch.get("planned_start", o["planned_start"]), ch.get("planned_due", o["planned_due"])
        if start and due and due < start:
            raise Rejected("bad_dates", "Плановая дата окончания раньше даты начала.", {"field": "planned_due"})
        if "note" in p:
            ch["note"] = opt_str(p, "note", 2000)
        ch = {k: v for k, v in ch.items() if v != o[k]}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        n = self._update_versioned(ctx, project_workstreams, o, {**ch, "updated_by": ctx.user_id,
                                                                 "updated_at": ctx.now})
        ctx.log("project_workstream", o["id"], auth, o["version"], n["version"],
                {"project_id": o["project_id"], "kind": o["kind"], "changed": sorted(ch)})
        return {"workstream": self.v_workstream(n)}

    def _gate_check(self, ctx, pr: dict, kind: str, to: str) -> None:
        """Проверить шлюз перехода: заказ в закупке и запуск производства требуют РКД, согласованной заказчиком,
        либо действующего риск-разрешения РП (ответы владельца, пп. 2–3).
        """
        gate = dl.stream_gate(self.wf, kind, to)
        if not gate:
            return
        # Шлюз отгрузки (D-68, ответ владельца п. 3): «Отгружена» — только после успешного финального FAT без
        # открытых блокирующих замечаний; предварительный FAT на шлюз не влияет (п. 4).
        if "requires_fat" in gate:
            fats = [dict(r._mapping) for r in ctx.conn.execute(
                sa.select(fat_sessions).where(fat_sessions.c.project_id == pr["id"],
                                              fat_sessions.c.kind == "final")
                .order_by(fat_sessions.c.seq.desc()))]   # «последний финальный» — по монотонному seq в проекте
            findings = []
            if fats:
                findings = [dict(r._mapping) for r in ctx.conn.execute(
                    sa.select(fat_findings).where(fat_findings.c.fat_id.in_([f["id"] for f in fats])))]
            why = dl.fat_shipping_gate(fats, findings)
            if why:
                raise Rejected("gate_requires_fat", why, {"stream": kind, "to": to})
            return
        rkd = self._stream(ctx, pr["id"], "rkd")
        if rkd and rkd["status"] == gate["requires_rkd"]:
            return
        risk_kind = gate["risk_kind"]
        today = ctx.now.date()
        for r in ctx.conn.execute(sa.select(project_risk_acceptances).where(
                project_risk_acceptances.c.project_id == pr["id"],
                project_risk_acceptances.c.kind == risk_kind,
                project_risk_acceptances.c.status == "active")):
            if dl.risk_state(dict(r._mapping), today) == "active":
                return
        label = dl.STREAM_LABELS_RU[kind]
        raise Rejected("gate_requires_rkd_approval",
                       f"Переход потока «{label}» в этот статус возможен после согласования РКД заказчиком "
                       f"(поток «РКД» → «Согласована заказчиком») либо при действующем риск-разрешении РП. "
                       "Если сроки поджимают — РП проекта оформляет риск-разрешение с причиной и сроком.",
                       {"stream": kind, "to": to, "risk_kind": risk_kind})

    def _workstream_set_status(self, ctx, p, ev):
        """workstream.set_status {id, to, reason?} — перевод потока по контракту workflows; шлюзы и обязательные
        причины проверяются в момент эффекта. Каждый переход — событие проекта и уведомление команде.
        """
        only_keys(p, {"id", "to", "reason"})
        o = self._require(ctx.conn, project_workstreams, req_uuid(p, "id"), "Поток проекта")
        pr = self._get(ctx.conn, projects, o["project_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "workstream.set_status", ctx.now, project_id=o["project_id"])
        self._project_editable(pr)
        self._check_version(o, ev, self.v_workstream)
        wf = dl.stream_workflow(self.wf, o["kind"])
        to = p.get("to")
        allowed = wf["transitions"].get(o["status"], [])
        if to not in allowed:
            raise Rejected("transition_not_allowed",
                           f"Поток «{dl.STREAM_LABELS_RU[o['kind']]}»: переход «{o['status']}» → «{to}» не разрешён. "
                           f"Допустимо: {', '.join(allowed) or 'нет'}.",
                           {"from": o["status"], "to": to, "allowed": allowed})
        reason = opt_str(p, "reason", 2000)
        if to in wf.get("reason_required_for", []) and not reason:
            raise Rejected("reason_required", "Для этого перехода укажите причину (для согласования РКД — номер "
                           "письма или протокола заказчика).", {"field": "reason"})
        self._gate_check(ctx, pr, o["kind"], to)
        n = self._update_versioned(ctx, project_workstreams, o, {"status": to, "updated_by": ctx.user_id,
                                                                 "updated_at": ctx.now})
        label = dl.STREAM_LABELS_RU[o["kind"]]
        from_label, to_label = wf["labels_ru"][o["status"]], wf["labels_ru"][to]
        self._project_event(ctx, o["project_id"], "workstream_status",
                            f"{label}: «{from_label}» → «{to_label}»", auth,
                            object_type="project_workstream", object_id=o["id"],
                            details={"kind": o["kind"], "from": o["status"], "to": to, "reason": reason})
        ctx.log("project_workstream", o["id"], auth, o["version"], n["version"],
                {"project_id": o["project_id"], "kind": o["kind"], "from": o["status"], "to": to, "reason": reason})
        to_notify = ([o["responsible_user_id"]] if o["responsible_user_id"] else []) + self._project_rps(ctx, o["project_id"])
        self._notify(ctx, to_notify, "workstream_status", f"Поток «{label}»: {to_label}",
                     f"Проект {pr['code']}: «{wf['labels_ru'][o['status']]}» → «{to_label}»" +
                     (f". Причина: {reason}" if reason else ""),
                     object_type="project_workstream", object_id=o["id"])
        return {"workstream": self.v_workstream(n)}

    # ------------------------------------------------------------ риск-разрешения
    def _risk_accept(self, ctx, p, ev):
        """risk.accept {id, project_id, kind, reason, due_on, mitigation?, kit_ids?} — РП проекта осознанно
        разрешает шаг до выполнения стандартного условия (ответ владельца, п. 1).

        Обязательны причина и срок действия (не в прошлом). Действующее разрешение того же вида в проекте
        одно: повторное — только после снятия прежнего. Разрешение не меняет статусы документов.
        """
        only_keys(p, {"id", "project_id", "kind", "reason", "due_on", "mitigation", "kit_ids"})
        pr = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "risk.accept", ctx.now, project_id=pr["id"])
        self._project_editable(pr)
        kind = p.get("kind")
        if kind not in dl.RISK_KINDS:
            raise Rejected("bad_risk_kind", "Вид риск-разрешения: procurement_before_rkd_approval (закупка до "
                           "согласования РКД) или production_before_rkd_approval (сборка по неутверждённой РКД).",
                           {"field": "kind"})
        reason = req_str(p, "reason", 2000)
        due = opt_date(p, "due_on")
        if due is None:
            raise Rejected("field_required", "Укажите срок действия риск-разрешения (due_on).", {"field": "due_on"})
        if due < ctx.now.date():
            raise Rejected("date_in_past", "Срок действия риск-разрешения не может быть в прошлом.",
                           {"field": "due_on"})
        kit_ids = p.get("kit_ids")
        scope: Optional[str] = None
        if kit_ids is not None:
            if not isinstance(kit_ids, list) or not kit_ids:
                raise Rejected("bad_scope", "kit_ids — непустой список комплектов проекта или его отсутствие "
                               "(весь проект).", {"field": "kit_ids"})
            for kid in kit_ids:
                k = self._get(ctx.conn, kits, req_uuid({"kit_id": kid}, "kit_id")) if isinstance(kid, str) else None
                if not k or k["project_id"] != pr["id"]:
                    raise Rejected("kit_not_in_project", "Комплект не найден в этом проекте.",
                                   {"field": "kit_ids", "kit_id": kid})
            scope = dl.canon(sorted(kit_ids))
        dup = ctx.conn.execute(sa.select(project_risk_acceptances.c.id).where(
            project_risk_acceptances.c.project_id == pr["id"], project_risk_acceptances.c.kind == kind,
            project_risk_acceptances.c.status == "active")).first()
        if dup:
            raise Rejected("risk_already_active", "Действующее разрешение этого вида уже есть. Сначала снимите его "
                           "(risk.resolve) — история сохранится.", {"active_id": dup.id})
        rid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, project_risk_acceptances, rid)
        ctx.conn.execute(project_risk_acceptances.insert().values(
            id=rid, project_id=pr["id"], kind=kind, scope_json=scope, reason=reason,
            mitigation=opt_str(p, "mitigation", 2000), due_on=due, status="active",
            accepted_by=ctx.user_id, authority=auth.describe(), created_at=ctx.now,
            resolved_by=None, resolved_at=None, resolution_reason=None, version=1))
        kind_label = self.wf["risk_acceptance"]["kinds"][kind]
        self._project_event(ctx, pr["id"], "risk_accepted",
                            f"Риск-разрешение «{kind_label}»: {reason[:120]}", auth,
                            object_type="project_risk_acceptance", object_id=rid,
                            details={"kind": kind, "due_on": due.isoformat(), "scope": scope})
        ctx.log("project_risk_acceptance", rid, auth, None, 1,
                {"project_id": pr["id"], "kind": kind, "due_on": due.isoformat(), "scope": scope})
        self._notify(ctx, self._project_rps(ctx, pr["id"]), "risk_accepted",
                     f"Риск-разрешение: {kind_label}",
                     f"Проект {pr['code']}: принято риск-разрешение до {due.isoformat()}. Причина: {reason[:300]}",
                     object_type="project_risk_acceptance", object_id=rid)
        return {"risk": self.v_risk(self._get(ctx.conn, project_risk_acceptances, rid), ctx.now.date())}

    def _risk_resolve(self, ctx, p, ev):
        """risk.resolve {id, resolution, reason} — снять риск-разрешение: resolved (условие выполнено) или
        withdrawn (отозвано). Причина обязательна; строка остаётся в истории.
        """
        only_keys(p, {"id", "resolution", "reason"})
        o = self._require(ctx.conn, project_risk_acceptances, req_uuid(p, "id"), "Риск-разрешение")
        pr = self._get(ctx.conn, projects, o["project_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "risk.resolve", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, lambda r: self.v_risk(r, ctx.now.date()))
        if o["status"] != "active":
            raise Rejected("risk_not_active", "Разрешение уже снято — повторное снятие не требуется.",
                           {"status": o["status"]})
        resolution = p.get("resolution")
        if resolution not in ("resolved", "withdrawn"):
            raise Rejected("bad_resolution", "Решение: resolved (условие выполнено) или withdrawn (отозвано).",
                           {"field": "resolution"})
        reason = req_str(p, "reason", 2000)
        n = self._update_versioned(ctx, project_risk_acceptances, o,
                                   {"status": resolution, "resolved_by": ctx.user_id, "resolved_at": ctx.now,
                                    "resolution_reason": reason})
        kind_label = self.wf["risk_acceptance"]["kinds"][o["kind"]]
        res_label = self.wf["risk_acceptance"]["labels_ru"][resolution]
        self._project_event(ctx, o["project_id"], "risk_resolved",
                            f"Риск-разрешение «{kind_label}»: {res_label.lower()}", auth,
                            object_type="project_risk_acceptance", object_id=o["id"],
                            details={"kind": o["kind"], "resolution": resolution, "reason": reason})
        ctx.log("project_risk_acceptance", o["id"], auth, o["version"], n["version"],
                {"resolution": resolution, "reason": reason})
        self._notify(ctx, [o["accepted_by"]] + self._project_rps(ctx, o["project_id"]), "risk_resolved",
                     f"Снято риск-разрешение: {kind_label}",
                     f"Проект {pr['code']}: {res_label.lower()}. Причина: {reason[:300]}",
                     object_type="project_risk_acceptance", object_id=o["id"])
        return {"risk": self.v_risk(n, ctx.now.date())}

    # ------------------------------------------------------------ статус проекта и архив
    def _archive_gates(self, ctx, pr: dict) -> None:
        """Условия архива: все потоки в конечных статусах и нет действующих риск-разрешений (даже истёкших — их
        сначала явно снимают, чтобы история была честной).
        """
        open_streams: list[str] = []
        for s in ctx.conn.execute(sa.select(project_workstreams).where(
                project_workstreams.c.project_id == pr["id"])):
            wf = dl.stream_workflow(self.wf, s.kind)
            if s.status not in wf["terminal"]:
                open_streams.append(dl.STREAM_LABELS_RU[s.kind])
        if open_streams:
            raise Rejected("streams_open", "В архив — только когда все потоки завершены. Не завершены: " +
                           ", ".join(open_streams) + ".", {"open_streams": open_streams})
        open_risks = [r.id for r in ctx.conn.execute(sa.select(project_risk_acceptances.c.id).where(
            project_risk_acceptances.c.project_id == pr["id"], project_risk_acceptances.c.status == "active"))]
        if open_risks:
            raise Rejected("risks_open", "Есть действующие риск-разрешения — сначала снимите их (risk.resolve "
                           "с причиной), чтобы архив фиксировал честный итог.", {"open_risks": open_risks})

    def _closeout(self, ctx, pr: dict, auth) -> dict:
        """Создать неизменяемый снимок закрытия проекта (состояние потоков, рисков и счётчики объектов)."""
        streams = [dict(r._mapping) for r in ctx.conn.execute(
            sa.select(project_workstreams).where(project_workstreams.c.project_id == pr["id"]))]
        risks = [dict(r._mapping) for r in ctx.conn.execute(
            sa.select(project_risk_acceptances).where(project_risk_acceptances.c.project_id == pr["id"]))]
        counts = {
            "kits": ctx.conn.execute(sa.select(sa.func.count()).select_from(kits).where(kits.c.project_id == pr["id"])).scalar_one(),
            "documents": ctx.conn.execute(sa.select(sa.func.count()).select_from(documents).where(documents.c.project_id == pr["id"])).scalar_one(),
            "tasks": ctx.conn.execute(sa.select(sa.func.count()).select_from(tasks).where(tasks.c.project_id == pr["id"])).scalar_one(),
        }
        content = dl.closeout_content(pr, streams, risks, counts, dl.STREAM_LABELS_RU)
        cid = str(uuid.uuid4())
        ctx.conn.execute(project_closeouts.insert().values(
            id=cid, project_id=pr["id"], snapshot_json=dl.canon(content),
            content_sha256=dl.closeout_sha256(content), closed_by=ctx.user_id, authority=auth.describe(),
            created_at=ctx.now))
        return {"id": cid, "content": content}

    def _project_transition(self, ctx, p, ev):
        """project.transition {id, to, reason?} — грубая рамка проекта: «Завершается», «Архив», «Отменён»
        (решение РП проекта, ответ владельца п. 1). Детальные этапы — в потоках (workstream.set_status).
        """
        only_keys(p, {"id", "to", "reason"})
        o = self._require(ctx.conn, projects, req_uuid(p, "id"), "Проект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "project.transition", ctx.now, project_id=o["id"])
        self._check_version(o, ev, self.v_project)
        wf = self.wf["project"]
        to = p.get("to")
        allowed = wf.get("transitions", {}).get(o["status"], [])
        if to not in allowed:
            raise Rejected("transition_not_allowed",
                           f"Переход проекта «{o['status']}» → «{to}» не разрешён. Допустимо: "
                           f"{', '.join(allowed) or 'нет'}.", {"from": o["status"], "to": to, "allowed": allowed})
        reason = opt_str(p, "reason", 2000)
        if to in wf.get("reason_required_for", []) and not reason:
            raise Rejected("reason_required", "Для этого перехода укажите причину.", {"field": "reason"})
        if o["status"] == "closing" and to == "rkd_in_progress" and not reason:
            raise Rejected("reason_required", "Возврат проекта в работу — только с причиной.", {"field": "reason"})
        closeout = None
        if to == "archived":
            self._archive_gates(ctx, o)
            closeout = self._closeout(ctx, o, auth)
        n = self._update_versioned(ctx, projects, o, {"status": to, "updated_by": ctx.user_id,
                                                      "updated_at": ctx.now})
        to_label = wf["labels_ru"][to]
        self._project_event(ctx, o["id"], "project_status", f"Проект: «{wf['labels_ru'][o['status']]}» → "
                            f"«{to_label}»" + (f". Причина: {reason}" if reason else ""), auth,
                            object_type="project", object_id=o["id"],
                            details={"from": o["status"], "to": to, "reason": reason,
                                     "closeout_id": closeout["id"] if closeout else None})
        ctx.log("project", o["id"], auth, o["version"], n["version"], {"from": o["status"], "to": to, "reason": reason})
        self._notify(ctx, self._project_rps(ctx, o["id"]), "project_stage", f"Проект {o['code']}: {to_label}",
                     (f"Причина: {reason[:300]}" if reason else None),
                     object_type="project", object_id=o["id"])
        out = {"project": self.v_project(n)}
        if closeout:
            out["closeout"] = {"id": closeout["id"], "content_sha256":
                               self._get(ctx.conn, project_closeouts, closeout["id"])["content_sha256"]}
        return out

    # ------------------------------------------------------------ шаблоны чек-листов ПНР/FAT (D-66/D-67)
    def _checklist_fields(self, p: dict, existing: Optional[dict]) -> dict:
        """Проверенные поля шаблона чек-листа (создание — все обязательные; правка — только переданные)."""
        out: dict[str, Any] = {}
        if existing is None or "kind" in p:
            kind = req_str(p, "kind", 8)
            if kind not in dl.CHECKLIST_KINDS:
                raise Rejected("bad_value", "Вид чек-листа — pnr (пусконаладка) или fat (приёмочные испытания).",
                               {"field": "kind"})
            out["kind"] = kind
        if existing is None or "name" in p:
            out["name"] = req_str(p, "name", 200)
        if existing is None or "sections" in p:
            try:
                sections = dl.validate_sections(p.get("sections"))
            except ValueError as e:
                raise Rejected("bad_checklist", str(e), {"field": "sections"})
            out["sections_json"] = dl.canon(sections)
        if existing is None or "severities" in p:
            try:
                sev = dl.validate_severities(p.get("severities"))
            except ValueError as e:
                raise Rejected("bad_checklist", str(e), {"field": "severities"})
            # None (не заданы) → градации по умолчанию снимком в шаблоне: дальше везде работаем с явным списком.
            out["severities_json"] = dl.canon(sev if sev is not None else dl.DEFAULT_SEVERITIES)
        return out

    def _checklist_create(self, ctx, p, ev):
        """checklist.create {id, kind, name, sections, severities?} — новый шаблон чек-листа ПНР/FAT. Настраивает
        начальник сервисной службы (ответ владельца D-66 п. 2; право checklist.manage — руководитель отдела, A-58)."""
        only_keys(p, {"id", "kind", "name", "sections", "severities"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "checklist.manage", ctx.now)
        fields = self._checklist_fields(p, existing=None)
        tid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, checklist_templates, tid)
        ctx.conn.execute(checklist_templates.insert().values(
            id=tid, **fields, active=True, version=1, created_by=ctx.user_id, created_at=ctx.now,
            updated_by=ctx.user_id, updated_at=ctx.now))
        o = self._get(ctx.conn, checklist_templates, tid)
        ctx.log("checklist_template", tid, auth, None, 1, {"kind": fields["kind"], "name": fields["name"]})
        return {"checklist": self.v_checklist(o)}

    def _checklist_update(self, ctx, p, ev):
        """checklist.update {id, name?, sections?, severities?} — правка шаблона. Чек-листы, уже созданные по шаблону,
        хранят снимок разделов/градаций — правка шаблона историю испытаний не переписывает."""
        only_keys(p, {"id", "name", "sections", "severities"})
        o = self._require(ctx.conn, checklist_templates, req_uuid(p, "id"), "Шаблон чек-листа")
        auth = self.policy.check(ctx.conn, ctx.user_id, "checklist.manage", ctx.now)
        fields = self._checklist_fields(p, existing=o)
        fields = {k: v for k, v in fields.items() if v != o[k]}
        if not fields:
            raise Rejected("no_changes", "Нет изменений.")
        self._check_version(o, ev, self.v_checklist)
        n = self._update_versioned(ctx, checklist_templates, o, {**fields, "updated_by": ctx.user_id,
                                                                 "updated_at": ctx.now})
        ctx.log("checklist_template", o["id"], auth, o["version"], n["version"], {"changed": sorted(fields)})
        return {"checklist": self.v_checklist(n)}

    def _checklist_set_active(self, ctx, p, ev, active: bool):
        """checklist.archive/unarchive {id} — отключить/вернуть шаблон (новые чек-листы создаются по активным)."""
        only_keys(p, {"id"})
        o = self._require(ctx.conn, checklist_templates, req_uuid(p, "id"), "Шаблон чек-листа")
        auth = self.policy.check(ctx.conn, ctx.user_id, "checklist.manage", ctx.now)
        if bool(o["active"]) == active:
            raise Rejected("no_changes", "Шаблон уже в этом состоянии.")
        self._check_version(o, ev, self.v_checklist)
        n = self._update_versioned(ctx, checklist_templates, o, {"active": active, "updated_by": ctx.user_id,
                                                                 "updated_at": ctx.now})
        ctx.log("checklist_template", o["id"], auth, o["version"], n["version"], {"active": active})
        return {"checklist": self.v_checklist(n)}

    def _checklist_archive(self, ctx, p, ev):
        """checklist.archive — отключить шаблон (действующие чек-листы по нему не меняются)."""
        return self._checklist_set_active(ctx, p, ev, False)

    def _checklist_unarchive(self, ctx, p, ev):
        """checklist.unarchive — вернуть шаблон в работу."""
        return self._checklist_set_active(ctx, p, ev, True)

    # ------------------------------------------------------------ ПНР и FAT (D-68, этап 6 blueprint 52)
    def _pnr_create(self, ctx, p, ev):
        """pnr.create {id, project_id, kit_id?, kind, title, need_power?, planned_on?, note?} — запрос пусконаладки
        (внутренней или на площадке). Создаёт участник проекта; статус ведёт РП (pnr.set_status)."""
        only_keys(p, {"id", "project_id", "kit_id", "kind", "title", "need_power", "planned_on", "note"})
        pr = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "pnr.create", ctx.now, project_id=pr["id"])
        self._project_editable(pr)
        kind = req_str(p, "kind", 8)
        if kind not in dl.PNR_KINDS:
            raise Rejected("bad_value", "Вид запроса: internal (внутренняя наладка) или site (ПНР на площадке).",
                           {"field": "kind"})
        kid = p.get("kit_id")
        if kid is not None:
            kid = req_uuid({"kit_id": kid}, "kit_id")
            k = self._get(ctx.conn, kits, kid)
            if not k or k["project_id"] != pr["id"]:
                raise Rejected("kit_not_in_project", "Комплект не найден в этом проекте.", {"field": "kit_id"})
        rid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, pnr_requests, rid)
        ctx.conn.execute(pnr_requests.insert().values(
            id=rid, project_id=pr["id"], kit_id=kid, kind=kind, title=req_str(p, "title", 300),
            need_power=bool(p.get("need_power")), planned_on=opt_date(p, "planned_on"),
            note=opt_str(p, "note", 2000), status=self.wf["pnr_request"]["initial"], status_reason=None,
            version=1, created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("pnr_request", rid, auth, None, 1, {"project_id": pr["id"], "kind": kind})
        self._project_event(ctx, pr["id"], "pnr_requested", f"Запрос ПНР ({dl.PNR_KINDS_RU[kind].lower()}): "
                            f"{p['title'][:120]}", auth, object_type="pnr_request", object_id=rid)
        return {"pnr": self.v_pnr(self._get(ctx.conn, pnr_requests, rid))}

    def _pnr_set_status(self, ctx, p, ev):
        """pnr.set_status {id, to, reason?} — перевод запроса по контракту workflows; отмена — с причиной."""
        only_keys(p, {"id", "to", "reason"})
        o = self._require(ctx.conn, pnr_requests, req_uuid(p, "id"), "Запрос ПНР")
        pr = self._get(ctx.conn, projects, o["project_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "pnr.set_status", ctx.now, project_id=o["project_id"])
        self._project_editable(pr)
        self._check_version(o, ev, self.v_pnr)
        wf = self.wf["pnr_request"]
        to = p.get("to")
        allowed = wf["transitions"].get(o["status"], [])
        if to not in allowed:
            raise Rejected("transition_not_allowed",
                           f"Переход запроса ПНР «{wf['labels_ru'][o['status']]}» → «{to}» не разрешён. Допустимо: "
                           f"{', '.join(allowed) or 'нет'}.", {"from": o["status"], "to": to})
        reason = opt_str(p, "reason", 2000)
        if to in wf.get("reason_required_for", []) and not reason:
            raise Rejected("reason_required", "Для отмены запроса укажите причину.", {"field": "reason"})
        n = self._update_versioned(ctx, pnr_requests, o, {"status": to, "status_reason": reason,
                                                          "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._project_event(ctx, o["project_id"], "pnr_status",
                            f"ПНР «{o['title'][:80]}»: «{wf['labels_ru'][o['status']]}» → «{wf['labels_ru'][to]}»"
                            + (f". Причина: {reason}" if reason else ""), auth,
                            object_type="pnr_request", object_id=o["id"])
        ctx.log("pnr_request", o["id"], auth, o["version"], n["version"], {"to": to})
        return {"pnr": self.v_pnr(n)}

    def _fat_create(self, ctx, p, ev):
        """fat.create {id, project_id, kit_id?, kind, template_id, note?} — начать испытание по шаблону чек-листа.

        Разделы и градации фиксируются СНИМКОМ шаблона на момент создания: правка шаблона историю испытаний не
        переписывает. Шаблон обязан быть активным и вида «fat». Предварительный FAT не влияет на показатели и
        отгрузку (ответ владельца п. 4); финальный решает шлюз отгрузки (п. 3).
        """
        only_keys(p, {"id", "project_id", "kit_id", "kind", "template_id", "note"})
        pr = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "fat.create", ctx.now, project_id=pr["id"])
        self._project_editable(pr)
        kind = req_str(p, "kind", 12)
        if kind not in dl.FAT_KINDS:
            raise Rejected("bad_value", "Вид испытания: preliminary (предварительный) или final (приёмочный).",
                           {"field": "kind"})
        tpl = self._get(ctx.conn, checklist_templates, req_uuid(p, "template_id"))
        if not tpl or not tpl["active"] or tpl["kind"] != "fat":
            raise Rejected("template_unusable", "Шаблон не найден, отключён или не вида «FAT».",
                           {"field": "template_id"})
        kid = p.get("kit_id")
        if kid is not None:
            kid = req_uuid({"kit_id": kid}, "kit_id")
            k = self._get(ctx.conn, kits, kid)
            if not k or k["project_id"] != pr["id"]:
                raise Rejected("kit_not_in_project", "Комплект не найден в этом проекте.", {"field": "kit_id"})
        fid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, fat_sessions, fid)
        seq = ctx.conn.execute(sa.select(sa.func.max(fat_sessions.c.seq)).where(
            fat_sessions.c.project_id == pr["id"])).scalar() or 0
        ctx.conn.execute(fat_sessions.insert().values(
            id=fid, project_id=pr["id"], seq=seq + 1, kit_id=kid, kind=kind, template_id=tpl["id"],
            template_name=tpl["name"], sections_json=tpl["sections_json"],
            severities_json=tpl["severities_json"], status=self.wf["fat_session"]["initial"],
            note=opt_str(p, "note", 2000), version=1, created_by=ctx.user_id, created_at=ctx.now,
            finished_by=None, finished_at=None))
        kind_label = self.wf["fat_session"]["kinds"][kind]
        self._project_event(ctx, pr["id"], "fat_started", f"{kind_label} начат (шаблон «{tpl['name']}»)", auth,
                            object_type="fat_session", object_id=fid)
        ctx.log("fat_session", fid, auth, None, 1, {"project_id": pr["id"], "kind": kind,
                                                    "template_id": tpl["id"]})
        return {"fat": self.v_fat(self._get(ctx.conn, fat_sessions, fid))}

    def _fat_finish(self, ctx, p, ev):
        """fat.finish {id, result, reason?} — завершить испытание: passed | failed; «неуспешен» — с причиной.
        Итог неизменяем: повторное испытание — новая сессия (история сохраняется)."""
        only_keys(p, {"id", "result", "reason"})
        o = self._require(ctx.conn, fat_sessions, req_uuid(p, "id"), "FAT-сессия")
        pr = self._get(ctx.conn, projects, o["project_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "fat.finish", ctx.now, project_id=o["project_id"])
        self._project_editable(pr)
        self._check_version(o, ev, self.v_fat)
        wf = self.wf["fat_session"]
        result = p.get("result")
        allowed = wf["transitions"].get(o["status"], [])
        if result not in allowed:
            raise Rejected("transition_not_allowed",
                           "Испытание уже завершено (итог неизменяем) — повторное испытание создаётся новой сессией."
                           if not allowed else f"Итог: {', '.join(allowed)}.", {"status": o["status"]})
        reason = opt_str(p, "reason", 2000)
        if result in wf.get("reason_required_for", []) and not reason:
            raise Rejected("reason_required", "Для итога «неуспешен» укажите причину.", {"field": "reason"})
        n = self._update_versioned(ctx, fat_sessions, o, {"status": result, "finished_by": ctx.user_id,
                                                          "finished_at": ctx.now,
                                                          "note": reason or o["note"]})
        kind_label = wf["kinds"][o["kind"]]
        res_label = wf["labels_ru"][result]
        self._project_event(ctx, o["project_id"], "fat_finished", f"{kind_label}: {res_label.lower()}"
                            + (f". Причина: {reason}" if reason else ""), auth,
                            object_type="fat_session", object_id=o["id"],
                            details={"kind": o["kind"], "result": result})
        ctx.log("fat_session", o["id"], auth, o["version"], n["version"], {"result": result})
        self._notify(ctx, self._project_rps(ctx, o["project_id"]), "fat_finished",
                     f"{kind_label}: {res_label.lower()}",
                     f"Проект {pr['code']}: испытание завершено" + (f". Причина: {reason[:300]}" if reason else ""),
                     object_type="fat_session", object_id=o["id"])
        return {"fat": self.v_fat(n)}

    def _fat_add_finding(self, ctx, p, ev):
        """fat.add_finding {id, fat_id, section_code, severity, text} — замечание к испытанию.

        Раздел и градация — из СНИМКА шаблона сессии (а не из текущего шаблона); снимок блокирующего признака и
        подписи градации хранится в замечании. Замечание атрибутируется к проекту, не к человеку (ответ п. 5).
        Добавлять можно в идущее испытание; к завершённому — нельзя (замечания тогда закрывают, а новое испытание
        соберёт свои).
        """
        only_keys(p, {"id", "fat_id", "section_code", "severity", "text"})
        fat = self._require(ctx.conn, fat_sessions, req_uuid(p, "fat_id"), "FAT-сессия")
        pr = self._get(ctx.conn, projects, fat["project_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "fat.add_finding", ctx.now, project_id=fat["project_id"])
        self._project_editable(pr)
        if fat["status"] != "in_progress":
            raise Rejected("fat_finished", "Испытание завершено — замечания добавляются в идущее испытание; "
                                           "по итогам закрывайте имеющиеся или создайте новое испытание.")
        sections = {s_["code"] for s_ in dl.parse_sections(fat["sections_json"])}
        sev_by_code = {s_["code"]: s_ for s_ in dl.parse_severities(fat["severities_json"])}
        section = req_str(p, "section_code", 40)
        if section not in sections:
            raise Rejected("bad_value", "Раздел не из чек-листа этого испытания.", {"field": "section_code"})
        sev = req_str(p, "severity", 40)
        if sev not in sev_by_code:
            raise Rejected("bad_value", "Градация не из словаря этого испытания.", {"field": "severity"})
        fid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, fat_findings, fid)
        seq = ctx.conn.execute(sa.select(sa.func.max(fat_findings.c.seq)).where(
            fat_findings.c.fat_id == fat["id"])).scalar() or 0
        ctx.conn.execute(fat_findings.insert().values(
            id=fid, fat_id=fat["id"], seq=seq + 1, section_code=section, severity=sev,
            severity_label=sev_by_code[sev]["label"], blocking=sev_by_code[sev]["blocking"],
            text=req_str(p, "text", 2000), status="open", close_reason=None, version=1,
            created_by=ctx.user_id, created_at=ctx.now, closed_by=None, closed_at=None))
        ctx.log("fat_finding", fid, auth, None, 1, {"fat_id": fat["id"], "severity": sev,
                                                    "blocking": sev_by_code[sev]["blocking"]})
        return {"finding": self.v_finding(self._get(ctx.conn, fat_findings, fid))}

    def _fat_close_finding(self, ctx, p, ev):
        """fat.close_finding {id, reason} — закрыть замечание с причиной (что сделано). Закрытие не меняет итог
        сессии; для разблокировки отгрузки после failed нужна новая финальная сессия."""
        only_keys(p, {"id", "reason"})
        o = self._require(ctx.conn, fat_findings, req_uuid(p, "id"), "Замечание FAT")
        fat = self._get(ctx.conn, fat_sessions, o["fat_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "fat.close_finding", ctx.now, project_id=fat["project_id"])
        self._check_version(o, ev, self.v_finding)
        if o["status"] != "open":
            raise Rejected("finding_closed", "Замечание уже закрыто.")
        reason = req_str(p, "reason", 2000)
        n = self._update_versioned(ctx, fat_findings, o, {"status": "closed", "close_reason": reason,
                                                          "closed_by": ctx.user_id, "closed_at": ctx.now})
        ctx.log("fat_finding", o["id"], auth, o["version"], n["version"], {"closed": True})
        return {"finding": self.v_finding(n)}

    # ------------------------------------------------------------ данные для pull
    def _pull_delivery(self, conn, uid: str, now, readable: list[str]) -> dict:
        """Лиды (по праву opportunity.read) и, по читаемым проектам, — потоки, риск-разрешения, события и снимок
        архива. Состояние риска (действует/истёк) вычисляется на дату сервера.
        """
        today = now.date()
        out: dict[str, Any] = {"opportunities": [], "workstreams": [], "project_risks": [], "project_events": [],
                               "project_closeouts": [], "checklist_templates": []}
        if self.policy.can(conn, uid, "checklist.read", now):
            out["checklist_templates"] = [self.v_checklist(dict(r._mapping)) for r in conn.execute(
                sa.select(checklist_templates).order_by(checklist_templates.c.kind, checklist_templates.c.name))]
        out["pnr_requests"] = []
        out["fat_sessions"] = []
        out["fat_findings"] = []
        if readable:
            out["pnr_requests"] = [self.v_pnr(dict(r._mapping)) for r in conn.execute(
                sa.select(pnr_requests).where(pnr_requests.c.project_id.in_(readable))
                .order_by(pnr_requests.c.created_at, pnr_requests.c.id))]
            fats = [dict(r._mapping) for r in conn.execute(
                sa.select(fat_sessions).where(fat_sessions.c.project_id.in_(readable))
                .order_by(fat_sessions.c.project_id, fat_sessions.c.seq))]
            out["fat_sessions"] = [self.v_fat(o) for o in fats]
            if fats:
                out["fat_findings"] = [self.v_finding(dict(r._mapping)) for r in conn.execute(
                    sa.select(fat_findings).where(fat_findings.c.fat_id.in_([f["id"] for f in fats]))
                    .order_by(fat_findings.c.fat_id, fat_findings.c.seq))]
        if self.policy.can(conn, uid, "opportunity.read", now):
            out["opportunities"] = [self.v_opportunity(dict(r._mapping)) for r in conn.execute(
                sa.select(sales_opportunities).order_by(sales_opportunities.c.created_at, sales_opportunities.c.id))]
        if readable:
            out["workstreams"] = [self.v_workstream(dict(r._mapping)) for r in conn.execute(
                sa.select(project_workstreams).where(project_workstreams.c.project_id.in_(readable))
                .order_by(project_workstreams.c.project_id, project_workstreams.c.kind))]
            out["project_risks"] = [self.v_risk(dict(r._mapping), today) for r in conn.execute(
                sa.select(project_risk_acceptances).where(project_risk_acceptances.c.project_id.in_(readable))
                .order_by(project_risk_acceptances.c.created_at))]
            out["project_events"] = [self.v_project_event(dict(r._mapping)) for r in conn.execute(
                sa.select(project_events).where(project_events.c.project_id.in_(readable))
                .order_by(project_events.c.project_id, project_events.c.seq))]
            out["project_closeouts"] = [
                {"id": r.id, "project_id": r.project_id, "content_sha256": r.content_sha256,
                 "snapshot": r.snapshot_json, "closed_by": r.closed_by, "created_at": iso(r.created_at)}
                for r in conn.execute(sa.select(project_closeouts).where(
                    project_closeouts.c.project_id.in_(readable)))]
        return out
