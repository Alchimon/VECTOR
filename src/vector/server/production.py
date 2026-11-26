"""Технология и производство (этап 5, сценарий С5 шаги 1–6; decision log D-36).

Модуль — примесь (mixin) к `Service`, как calc.py и purchasing.py: транзакция, идемпотентность по operation_id,
аудит и проверка версий — в service.py; здесь только правила домена (сами правила — в vector.core.production,
общие с клиентом).

Поток работы:

    технолог: tp.create / tp.update (черновик, можно офлайн) ──tp.approve (не автор)──▶ утверждённый ТП (неизменяем)
    РП проекта (мастер, A-42): wo.release ──▶ наряд «Выпущен» + НЕИЗМЕНЯЕМЫЙ снимок (состав без цен + операции, SHA-256)
    исполнитель: wo.op_event start / pause / resume / finish / problem — ПОЛНОСТЬЮ ОФЛАЙН через очередь
                 (время устройства, operation_id очереди; конфликт — видимый, никогда не слияние)
    склад: stock.issue {…, work_order_id} — выдача материалов на наряд
    ГИП / технолог: wo.substitute — замена позиции (предпросмотр + хэш + причина); снимок не переписывается
    РП проекта: wo.close — только если все операции выполнены и материалы выданы; wo.cancel — с причиной

Главные инварианты (каждый закреплён тестом в tests/server/test_production.py):

* снимок наряда фиксируется при выпуске и не меняется никогда (триггеры БД; SHA-256 сверяется при показе);
* утверждённый ТП не меняется; утверждает не автор черновика; утверждается то содержимое, которое видел
  утверждающий (expected_version + content_hash);
* событие по выполненной операции или закрытому/отменённому наряду, событие не по порядку — Conflict с текущим
  состоянием (пользователь видит конфликт в очереди и решает сам);
* событие операции принимается только от назначенного исполнителя с действующей ролью «Производство»;
* закрыть наряд нельзя, пока не выполнены все операции и не выданы материалы действующего состава.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import delivery as dl
from vector.core import money
from vector.core import production as prod
from vector.core.errors import Conflict, Rejected

from .authz import active_delegated_project, effective_roles
from .db import (assembly_readiness_snapshots, calc_version_lines, calc_versions, kits, norm_proposals,
                 procurement_needs,
                 project_assignments, project_risk_acceptances, projects, stock_items, stock_movements,
                 stock_reservations, tech_process_approvals, tech_process_ops, tech_processes, users, wo_operations,
                 wo_substitutions, wo_timer_events, work_order_issues, work_order_snapshots, work_orders)
from .validation import canon, iso, only_keys, opt_str, req_str, req_uuid

# Статусы проекта, в которых возможны технология и производство. Сейчас реализован только «Разработка РКД»;
# остальные — из жизненного цикла blueprint (03 §4), чтобы переход проекта в «Производство» ничего не сломал.
PRODUCTION_PROJECT_STATUSES = ("rkd_in_progress", "rkd_issued", "production")
# Насколько время события может опережать часы сервера (часы устройства немного спешат — это нормально).
DEVICE_CLOCK_SKEW = timedelta(minutes=15)
# Сколько последних событий операции отдаётся клиенту (полная история — в журнале аудита и wo_timer_events).
EVENTS_IN_VIEW = 50


def _production_error(e: prod.ProductionError, current: Optional[dict] = None):
    """ProductionError → Conflict (состояние уже другое; в details.current — текущий объект) или Rejected."""
    if e.conflict:
        return Conflict(e.code, e.message_ru, {**e.details, "current": current})
    return Rejected(e.code, e.message_ru, e.details)


class ProductionMixin:
    """Обработчики tp.* / wo.*, выдача на наряд (часть stock.issue) и данные производства для pull."""

    # ------------------------------------------------------------ общие проверки
    @staticmethod
    def _project_allows_production(pr: dict) -> None:
        """Технология и наряды — только в проекте в работе (см. PRODUCTION_PROJECT_STATUSES)."""
        if pr["status"] not in PRODUCTION_PROJECT_STATUSES:
            raise Rejected("project_state", "Технология и наряды возможны только в проекте в работе.")

    def _spec_of_version(self, conn, version_id: int) -> Optional[dict]:
        """Состав изделия из зафиксированной версии расчёта — БЕЗ цен (технологу и производству деньги не нужны)."""
        v = self._get(conn, calc_versions, version_id)
        if v is None:
            return None
        lines = [{"line_id": r.line_id, "code": r.code, "name": r.name, "unit": r.unit, "qty": money.to_json(r.qty)}
                 for r in conn.execute(sa.select(calc_version_lines).where(calc_version_lines.c.version_id == version_id)
                                       .order_by(calc_version_lines.c.pos))]
        return {"calc_version_id": v["id"], "kit_id": v["kit_id"], "project_id": v["project_id"],
                "version_no": v["version_no"], "content_hash": v["content_hash"], "frozen_at": iso(v["frozen_at"]),
                "lines": lines}

    def _check_spec_version(self, conn, kit_id: str, value: Any) -> Optional[int]:
        """spec_version_id из запроса: None — «пока не выбрана»; иначе — id (целое) зафиксированной версии расчёта
        ЭТОГО комплекта.
        """
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise Rejected("invalid_type", "spec_version_id — номер (id) версии расчёта.", {"field": "spec_version_id"})
        vid = value
        v = self._get(conn, calc_versions, vid)
        if v is None or v["kit_id"] != kit_id:
            raise Rejected("spec_version_invalid", "Спецификация ТП — зафиксированная версия расчёта этого комплекта.",
                           {"field": "spec_version_id"})
        return vid

    @staticmethod
    def _parse_ops(p: dict) -> list[dict]:
        """Операции ТП из запроса (vector.core.production.parse_operations) → отказ tp_invalid со списком замечаний."""
        try:
            return prod.parse_operations(p.get("operations", []))
        except prod.ProductionError as e:
            raise _production_error(e)

    def _tp_ops(self, conn, tp_id: str) -> list[dict]:
        """Операции ТП из БД в порядке номеров (норма — десятичная строка с 4 знаками)."""
        return [{"op_no": r.op_no, "name": r.name, "work_center": r.work_center, "norm_min": money.to_json(r.norm_min),
                 "note": r.note}
                for r in conn.execute(sa.select(tech_process_ops).where(tech_process_ops.c.tp_id == tp_id)
                                      .order_by(tech_process_ops.c.op_no))]

    def _is_producer(self, conn, user_id: str, now) -> bool:
        """Действует ли у сотрудника роль «Производство» (своя или по замещению, D-34)."""
        return "production" in effective_roles(conn, user_id, now)

    def _check_producer(self, conn, value: Any, now, field: str = "assignee_id") -> Optional[str]:
        """Исполнитель операции: None — «не назначен»; иначе активный сотрудник с действующей ролью «Производство»."""
        if value is None:
            return None
        uid = req_uuid({field: value}, field)
        u = self._get(conn, users, uid)
        if u is None or not u["active"] or not self._is_producer(conn, uid, now):
            raise Rejected("assignee_not_producer", "Исполнитель операции — сотрудник с ролью «Производство».",
                           {"field": field, "user_id": uid})
        return uid

    def _project_role_users(self, conn, project_id: str, role: str, now) -> list[str]:
        """Кто сейчас в роли role в проекте: свои назначения и замещающие в этом проекте (D-35) — адресаты уведомлений."""
        pa = project_assignments
        out = [r.user_id for r in conn.execute(
            sa.select(pa.c.user_id).where(pa.c.project_id == project_id, pa.c.project_role == role,
                                          pa.c.revoked_at.is_(None), pa.c.valid_from <= now,
                                          sa.or_(pa.c.valid_until.is_(None), pa.c.valid_until > now))
            .order_by(pa.c.user_id))]
        for d in self._project_deputies(conn, [project_id], now):
            if d["project_role"] == role and any(x[2] == d["delegation_id"]
                                                 for x in active_delegated_project(conn, d["user_id"], project_id, now)):
                out.append(d["user_id"])
        return list(dict.fromkeys(out))

    # ------------------------------------------------------------ представления
    def v_tech_process(self, conn, o: dict) -> dict:
        """ТП для клиента: реквизиты, операции, состав (из выбранной версии расчёта, без цен), утверждение.

        approval_valid — содержимое совпадает с утверждённым (SHA-256): правка в обход программы будет видна.
        """
        ops = self._tp_ops(conn, o["id"])
        appr = self._get(conn, tech_process_approvals, o["id"], "tp_id")
        basis = self._basis(conn, "tech_process", o["id"]) if appr else None
        kit = self._get(conn, kits, o["kit_id"])
        pr = self._get(conn, projects, o["project_id"])
        return {"id": o["id"], "project_id": o["project_id"], "project_code": pr["code"] if pr else None,
                "kit_id": o["kit_id"], "kit_code": kit["code"] if kit else None, "kit_title": kit["title"] if kit else None,
                "revision_no": o["revision_no"], "title": o["title"], "spec_version_id": o["spec_version_id"],
                "spec": self._spec_of_version(conn, o["spec_version_id"]) if o["spec_version_id"] else None,
                "note": o["note"], "status": o["status"], "status_label": prod.TP_STATUS_LABELS_RU[o["status"]],
                "content_hash": o["content_hash"], "operations": ops,
                "norm_per_unit_min": money.to_json(sum((money.parse_decimal(x["norm_min"]) for x in ops), Decimal(0))),
                "version": o["version"], "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "updated_by": o["updated_by"], "updated_at": iso(o["updated_at"]),
                "approval": None if appr is None else {
                    "approved_by": appr["approved_by"], "approved_at": iso(appr["approved_at"]),
                    "authority": appr["authority"], "content_hash": appr["content_hash"],
                    "on_behalf_of": basis["on_behalf_of"] if basis else None},
                "approval_valid": appr is None or appr["content_hash"] == o["content_hash"]}

    def _snapshot(self, conn, wo_id: str) -> tuple[dict, str]:
        """Снимок наряда (словарь) и его сохранённый SHA-256."""
        s = self._get(conn, work_order_snapshots, wo_id, "work_order_id")
        return json.loads(s["snapshot_json"]), s["content_hash"]

    def _substitutions(self, conn, wo_id: str) -> list[dict]:
        """Записи замен наряда по порядку: строка, было/стало, причина, хэши, кто и когда, на каком основании."""
        out = []
        for r in conn.execute(sa.select(wo_substitutions).where(wo_substitutions.c.work_order_id == wo_id)
                              .order_by(wo_substitutions.c.seq)):
            b = self._basis(conn, "work_order_substitution", f"{wo_id}#{r.seq}")
            out.append({"seq": r.seq, "line_id": r.line_id, "old": json.loads(r.old_json), "new": json.loads(r.new_json),
                        "reason": r.reason, "preview_hash": r.preview_hash, "result_hash": r.result_hash,
                        "authority": r.authority, "created_by": r.created_by, "created_at": iso(r.created_at),
                        "on_behalf_of": b["on_behalf_of"] if b else None})
        return out

    def _issued_by_item(self, conn, wo_id: str) -> dict[str, Decimal]:
        """Нетто выдано со склада на наряд по позициям номенклатуры: выдачи, связанные с нарядом, минус их сторно."""
        sm = stock_movements
        linked = [r.movement_id for r in conn.execute(sa.select(work_order_issues.c.movement_id)
                                                      .where(work_order_issues.c.work_order_id == wo_id))]
        out: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        if not linked:
            return out
        for r in conn.execute(sa.select(sm.c.item_id, sm.c.qty).where(sm.c.id.in_(linked), sm.c.kind == "issue")):
            out[r.item_id] += r.qty
        for r in conn.execute(sa.select(sm.c.item_id, sm.c.qty).where(sm.c.reverses_id.in_(linked))):
            out[r.item_id] -= r.qty
        return out

    def _materials(self, conn, wo_id: str, lines: list[dict]) -> list[dict]:
        """Материалы наряда: требуется по действующему составу / выдано / состояние (vector.core.production.materials)."""
        codes = sorted({ln["code"] for ln in lines if ln.get("code")})
        items = {r.code: {"id": r.id, "unit": r.unit} for r in conn.execute(
            sa.select(stock_items.c.id, stock_items.c.code, stock_items.c.unit).where(stock_items.c.code.in_(codes)))} if codes else {}
        rows = prod.materials(lines, items, self._issued_by_item(conn, wo_id))
        for r in rows:
            r["state_label"] = prod.MATERIAL_STATE_LABELS_RU[r["state"]]
        return rows

    def v_wo_operation(self, conn, o: dict, wo: Optional[dict] = None) -> dict:
        """Операция наряда для клиента: исполнитель, состояние, таймер (по времени устройства), факт, последние события."""
        wo = wo or self._get(conn, work_orders, o["work_order_id"])
        events = [{"kind": e.kind, "kind_label": prod.EVENT_LABELS_RU[e.kind], "device_time": iso(e.device_time),
                   "server_time": iso(e.server_time), "user_id": e.user_id, "qty_done": e.qty_done, "note": e.note}
                  for e in conn.execute(sa.select(wo_timer_events).where(wo_timer_events.c.wo_operation_id == o["id"])
                                        .order_by(wo_timer_events.c.id.desc()).limit(EVENTS_IN_VIEW))]
        events.reverse()
        return {"id": o["id"], "work_order_id": o["work_order_id"], "wo_doc_no": wo["doc_no"], "wo_status": wo["status"],
                "wo_qty": wo["qty"], "op_no": o["op_no"], "name": o["name"], "work_center": o["work_center"],
                "norm_min": money.to_json(o["norm_min"]), "norm_total_min": money.to_json(o["norm_min"] * wo["qty"]),
                "assignee_id": o["assignee_id"], "state": o["state"], "state_label": prod.OP_STATE_LABELS_RU[o["state"]],
                "qty_done": o["qty_done"], "fact_seconds": o["fact_seconds"], "fact_min": prod.fact_minutes(o["fact_seconds"]),
                "started_at": iso(o["started_at"]), "finished_at": iso(o["finished_at"]),
                "running_since": iso(o["running_since"]), "last_device_time": iso(o["last_device_time"]),
                "problem_count": o["problem_count"], "events": events, "version": o["version"],
                "updated_by": o["updated_by"], "updated_at": iso(o["updated_at"])}

    def v_work_order(self, conn, o: dict) -> dict:
        """Наряд для клиента: снимок (с проверкой SHA-256), замены, действующий состав и его хэш (для предпросмотра
        замены), материалы со склада, сводка по операциям.
        """
        snap, stored_hash = self._snapshot(conn, o["id"])
        subs = self._substitutions(conn, o["id"])
        eff = prod.effective_spec(snap["spec"]["lines"], subs)
        ops = [dict(r._mapping) for r in conn.execute(sa.select(wo_operations).where(wo_operations.c.work_order_id == o["id"])
                                                      .order_by(wo_operations.c.op_no))]
        done = sum(1 for x in ops if x["state"] == "done")
        mats = self._materials(conn, o["id"], eff)
        return {"id": o["id"], "doc_no": o["doc_no"], "project_id": o["project_id"], "project_code": snap["project"]["code"],
                "kit_id": o["kit_id"], "kit_code": snap["kit"]["code"], "kit_title": snap["kit"].get("title"),
                "tp_id": o["tp_id"], "tp_revision_no": snap["tp"]["revision_no"], "qty": o["qty"], "title": o["title"],
                "note": o["note"], "status": o["status"], "status_label": prod.WO_STATUS_LABELS_RU[o["status"]],
                "snapshot": snap, "snapshot_hash": o["snapshot_hash"],
                # Целостность: снимок в БД совпадает и с хэшем при выпуске, и с хэшем в карточке наряда.
                "snapshot_ok": prod.snapshot_hash(snap) == stored_hash == o["snapshot_hash"],
                "substitutions": subs, "spec": eff, "spec_hash": prod.spec_hash(eff), "materials": mats,
                "materials_ready": not any(m["state"] in prod.BLOCKING_MATERIAL_STATES for m in mats),
                "operations_total": len(ops), "operations_done": done,
                "fact_seconds": sum(x["fact_seconds"] for x in ops),
                "assignees": sorted({x["assignee_id"] for x in ops if x["assignee_id"]}),
                "cancel_reason": o["cancel_reason"], "version": o["version"],
                "readiness": self._readiness_view(conn, o["id"]),
                "released_by": o["released_by"], "released_at": iso(o["released_at"]),
                "closed_by": o["closed_by"], "closed_at": iso(o["closed_at"]),
                "updated_by": o["updated_by"], "updated_at": iso(o["updated_at"])}

    # ------------------------------------------------------------ ТП
    def _tp_create(self, ctx, p, ev):
        """tp.create {id, kit_id, title, spec_version_id?, operations?, note?} — черновик ТП (новая ревизия).

        Ревизия = последняя по комплекту + 1; черновик у комплекта может быть только один (иначе две параллельные
        «правды» о технологии одного изделия). Можно офлайн (queued): сервер перепроверит всё при синхронизации.
        """
        only_keys(p, {"id", "kit_id", "title", "spec_version_id", "operations", "note"})
        tid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, tech_processes, tid)
        kit, pr = self._open_project_of_kit(ctx, req_uuid(p, "kit_id"))
        auth = self.policy.check(ctx.conn, ctx.user_id, "tp.create", ctx.now, project_id=pr["id"])
        self._project_allows_production(pr)
        tp = tech_processes
        if ctx.conn.execute(sa.select(tp.c.id).where(tp.c.kit_id == kit["id"], tp.c.status == "draft")).first():
            raise Rejected("tp_draft_exists", "У этого комплекта уже есть черновик ТП — продолжите его или утвердите.")
        title = req_str(p, "title", 300)
        spec = self._check_spec_version(ctx.conn, kit["id"], p.get("spec_version_id"))
        ops = self._parse_ops(p)
        note = opt_str(p, "note", 2000)
        rev = (ctx.conn.execute(sa.select(sa.func.max(tp.c.revision_no)).where(tp.c.kit_id == kit["id"])).scalar() or 0) + 1
        h = prod.tp_content_hash(title, spec, ops, note)
        ctx.conn.execute(tp.insert().values(id=tid, project_id=pr["id"], kit_id=kit["id"], revision_no=rev, title=title,
                                            spec_version_id=spec, note=note, status="draft", content_hash=h, version=1,
                                            created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id,
                                            updated_at=ctx.now))
        self._insert_tp_ops(ctx, tid, ops)
        ctx.log("tech_process", tid, auth, None, 1, {"project_id": pr["id"], "kit_code": kit["code"], "revision_no": rev,
                                                     "title": title, "operations": len(ops), "content_hash": h})
        return {"tech_process": self.v_tech_process(ctx.conn, self._get(ctx.conn, tp, tid))}

    def _insert_tp_ops(self, ctx, tid: str, ops: list[dict]) -> None:
        """Записать операции ТП (норма — Decimal из канонической строки)."""
        if ops:
            ctx.conn.execute(tech_process_ops.insert(), [
                {"tp_id": tid, "op_no": o["op_no"], "name": o["name"], "work_center": o["work_center"],
                 "norm_min": money.parse_decimal(o["norm_min"]), "note": o["note"]} for o in ops])

    def _tp_update(self, ctx, p, ev):
        """tp.update {id, title, spec_version_id?, operations, note?} — сохранить черновик ТП (операции целиком).

        Утверждённый ТП не меняется — конфликт с текущим состоянием (технолог создаёт новую ревизию). Сохранение
        без изменений не увеличивает версию (повтор из офлайн-очереди не должен останавливать синхронизацию).
        """
        only_keys(p, {"id", "title", "spec_version_id", "operations", "note"})
        o = self._require(ctx.conn, tech_processes, req_uuid(p, "id"), "ТП")
        pr = self._get(ctx.conn, projects, o["project_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "tp.update", ctx.now, project_id=pr["id"])
        self._project_allows_production(pr)
        view = lambda x: self.v_tech_process(ctx.conn, x)  # noqa: E731
        if o["status"] != "draft":
            raise Conflict("tp_approved", "ТП уже утверждён — изменить его нельзя. Создайте новую ревизию.",
                           {"current": view(o)})
        self._check_version(o, ev, view)
        title = req_str(p, "title", 300)
        spec = self._check_spec_version(ctx.conn, o["kit_id"], p.get("spec_version_id"))
        ops = self._parse_ops(p)
        note = opt_str(p, "note", 2000)
        h = prod.tp_content_hash(title, spec, ops, note)
        if h == o["content_hash"]:
            return {"tech_process": view(o), "unchanged": True}
        ctx.conn.execute(tech_process_ops.delete().where(tech_process_ops.c.tp_id == o["id"]))
        n = self._update_versioned(ctx, tech_processes, o, {"title": title, "spec_version_id": spec, "note": note,
                                                             "content_hash": h, "updated_by": ctx.user_id,
                                                             "updated_at": ctx.now})
        self._insert_tp_ops(ctx, o["id"], ops)
        ctx.log("tech_process", o["id"], auth, o["version"], n["version"],
                {"title": title, "operations": len(ops), "spec_version_id": spec, "content_hash": h})
        return {"tech_process": view(n)}

    def _tp_approve(self, ctx, p, ev):
        """tp.approve {id} — утвердить ТП. Технолог (не автор черновика) или руководитель отдела; только онлайн.

        Утверждается ровно та версия, которую видел утверждающий (expected_version), и фиксируется её SHA-256.
        Нужны хотя бы одна операция и спецификация (зафиксированная версия расчёта этого комплекта).
        """
        only_keys(p, {"id"})
        o = self._require(ctx.conn, tech_processes, req_uuid(p, "id"), "ТП")
        pr = self._get(ctx.conn, projects, o["project_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "tp.approve", ctx.now, project_id=pr["id"])
        self._project_allows_production(pr)
        view = lambda x: self.v_tech_process(ctx.conn, x)  # noqa: E731
        if o["status"] != "draft":
            raise Rejected("tp_state", "ТП уже утверждён.")
        self._check_version(o, ev, view)
        if ctx.user_id in (o["created_by"], o["updated_by"]):
            raise Rejected("self_approval", "Свой ТП утвердить нельзя: утверждает другой технолог или руководитель отдела.")
        ops = self._tp_ops(ctx.conn, o["id"])
        if not ops:
            raise Rejected("tp_no_operations", "В ТП нет операций — утверждать нечего.")
        if o["spec_version_id"] is None or self._check_spec_version(ctx.conn, o["kit_id"], o["spec_version_id"]) is None:
            raise Rejected("tp_no_spec", "Выберите спецификацию ТП — зафиксированную версию расчёта комплекта.")
        if prod.tp_content_hash(o["title"], o["spec_version_id"], ops, o["note"]) != o["content_hash"]:
            # Содержимое в БД не совпадает с хэшем — правка в обход программы; утверждать такое нельзя.
            raise Rejected("tp_integrity", "Содержимое ТП не совпадает с сохранённым хэшем. Обратитесь к администратору.")
        n = self._update_versioned(ctx, tech_processes, o, {"status": "approved", "updated_at": ctx.now,
                                                            "updated_by": o["updated_by"]})
        ctx.conn.execute(tech_process_approvals.insert().values(tp_id=o["id"], content_hash=o["content_hash"],
                                                                approved_by=ctx.user_id, authority=auth.describe(),
                                                                approved_at=ctx.now))
        self._record_basis(ctx, "tech_process", o["id"], 1, auth)
        kit = self._get(ctx.conn, kits, o["kit_id"])
        ctx.log("tech_process", o["id"], auth, o["version"], n["version"],
                {"status": "approved", "content_hash": o["content_hash"], "revision_no": o["revision_no"],
                 "kit_code": kit["code"], "event": "TPApproved"})
        self._notify(ctx, [o["created_by"], o["updated_by"]], "tp_approved",
                     f"ТП {kit['code']} ред. {o['revision_no']} утверждён",
                     f"Утвердил: {self._actor_label(ctx, auth)}.", "tech_process", o["id"])
        return {"tech_process": view(n)}

    # ------------------------------------------------------------ наряд
    # ------------------------------------------------------------ готовность сборки (D-61)
    def _readiness_gate(self, ctx, pr: dict, kit: dict) -> tuple[Optional[str], str, bool, list[dict]]:
        """Шлюз РКД при выпуске наряда (этап 5 blueprint 52): повторная проверка в той же транзакции.

        РКД согласована заказчиком (поток «РКД» = approved_for_use) — шлюз пройден. Иначе нужно ДЕЙСТВУЮЩЕЕ
        риск-разрешение «Сборка по неутверждённой РКД», действующее на этот комплект (или на весь проект);
        истёкшее не открывает шлюз (dl.risk_state). Возвращает (статус РКД, подпись, ok, разрешения, под которыми
        выпускаем) — для снимка готовности.
        """
        rkd = self._stream(ctx, pr["id"], "rkd")
        rkd_status = rkd["status"] if rkd else None
        labels = self.wf["workstream_rkd"]["labels_ru"]
        rkd_label = labels.get(rkd_status, "—") if rkd_status else "Поток не заведён"
        rkd_ok = rkd_status == "approved_for_use"
        risks = []
        for r in ctx.conn.execute(sa.select(project_risk_acceptances).where(
                project_risk_acceptances.c.project_id == pr["id"],
                project_risk_acceptances.c.kind == "production_before_rkd_approval",
                project_risk_acceptances.c.status == "active")):
            rm = dict(r._mapping)
            if dl.risk_state(rm, ctx.now.date()) != "active":
                continue
            scope = json.loads(rm["scope_json"]) if rm["scope_json"] else None
            if scope is not None and kit["id"] not in scope:
                continue
            risks.append({"id": rm["id"], "kind": rm["kind"],
                          "label_ru": self.wf["risk_acceptance"]["kinds"][rm["kind"]],
                          "reason": rm["reason"], "due_on": rm["due_on"].isoformat(), "kit_ids": scope})
        if not rkd_ok and not risks:
            raise Rejected("gate_requires_rkd_approval",
                           "Выпуск наряда возможен после согласования РКД заказчиком (поток «РКД» → «Согласована "
                           "заказчиком») либо при действующем риск-разрешении РП «Сборка по неутверждённой РКД» "
                           "(D-61). Сейчас поток «РКД» — «" + rkd_label + "».",
                           {"stream": "production", "rkd_status": rkd_status})
        return rkd_status, rkd_label, rkd_ok, risks

    def _secured_by_item(self, conn, project_id: str) -> dict:
        """Нетто «у проекта» по позиции номенклатуры: выдано проекту (минус сторно) плюс неизрасходованный остаток
        действующих резервов проекта. Свободный остаток склада сюда не входит — он никому не принадлежит."""
        out = defaultdict(Decimal)
        for r in conn.execute(sa.select(stock_movements.c.item_id, stock_movements.c.kind, stock_movements.c.qty)
                              .where(stock_movements.c.project_id == project_id)):
            out[r.item_id] += r.qty if r.kind == "issue" else -r.qty
        for r in conn.execute(sa.select(stock_reservations.c.item_id, stock_reservations.c.qty,
                                        stock_reservations.c.consumed)
                              .where(stock_reservations.c.project_id == project_id,
                                     stock_reservations.c.status == "active")):
            out[r.item_id] += r.qty - r.consumed
        return out

    def _readiness_view(self, conn, wo_id: str) -> Optional[dict]:
        """Снимок готовности наряда для клиента (D-61) с проверкой SHA-256. None — наряд выпущен до появления
        снимков (миграция со старой версии): честное «нет данных», а не зелёная галочка."""
        r = self._get(conn, assembly_readiness_snapshots, wo_id, "work_order_id")
        if r is None:
            return None
        content = json.loads(r["content_json"])
        mats = [{**m, "state_label": prod.COVERAGE_STATE_LABELS_RU[m["state"]]} for m in content["materials"]]
        return {**content, "materials": mats,
                "verdict_label": prod.READINESS_VERDICT_LABELS_RU[content["verdict"]],
                "content_hash": r["content_hash"],
                "readiness_ok": prod.readiness_hash(content) == r["content_hash"],
                "created_by": r["created_by"], "created_at": iso(r["created_at"])}

    def _wo_release(self, ctx, p, ev):
        """wo.release {id, tp_id, qty, title?, note?, assignees?} — выпустить наряд по утверждённому ТП.

        В той же транзакции: шлюз готовности РКД (D-61), наряд «Выпущен», неизменяемый снимок (состав из версии
        расчёта без цен × qty, операции ТП с нормой на наряд, SHA-256), неизменяемый снимок готовности сборки
        (основания, обеспеченность материалами, риск-разрешения, SHA-256), операции наряда «Ожидает» с
        исполнителями (assignees — {"№ операции": id}). Назначенные исполнители получают уведомление.
        """
        only_keys(p, {"id", "tp_id", "qty", "title", "note", "assignees"})
        wid = req_uuid(p, "id")
        tp = self._require(ctx.conn, tech_processes, req_uuid(p, "tp_id"), "ТП")
        pr = self._get(ctx.conn, projects, tp["project_id"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "wo.release", ctx.now, project_id=pr["id"])
        self._project_allows_production(pr)
        self._ensure_new(ctx.conn, work_orders, wid)
        if tp["status"] != "approved":
            raise Rejected("tp_not_approved", "Наряд выпускается только по утверждённому ТП.")
        appr = self._get(ctx.conn, tech_process_approvals, tp["id"], "tp_id")
        if appr is None or appr["content_hash"] != tp["content_hash"]:
            raise Rejected("tp_integrity", "Содержимое ТП не совпадает с утверждённым. Обратитесь к администратору.")
        kit = self._get(ctx.conn, kits, tp["kit_id"])
        # Шлюз готовности РКД (D-61): до любой записи; риск-разрешения возвращаются для снимка.
        rkd_status, rkd_label, rkd_ok, risks = self._readiness_gate(ctx, pr, kit)
        spec = self._spec_of_version(ctx.conn, tp["spec_version_id"])
        ops = self._tp_ops(ctx.conn, tp["id"])
        qty = p.get("qty")
        try:
            snap = prod.build_snapshot(qty=qty, project=pr, kit=kit, tp=tp, spec=spec, operations=ops)
        except prod.ProductionError as e:
            raise _production_error(e)
        h = prod.snapshot_hash(snap)
        raw_asg = p.get("assignees") or {}
        if not isinstance(raw_asg, dict):
            raise Rejected("invalid_type", "assignees — объект {№ операции: id исполнителя}.", {"field": "assignees"})
        op_nos = {o["op_no"] for o in ops}
        asg: dict[int, Optional[str]] = {}
        for k, v in raw_asg.items():
            if not str(k).isdigit() or int(k) not in op_nos:
                raise Rejected("op_not_found", f"В ТП нет операции № {k}.", {"field": "assignees"})
            asg[int(k)] = self._check_producer(ctx.conn, v, ctx.now)
        title = opt_str(p, "title", 300) or f"{kit['code']} — {tp['title']} × {qty}"
        note = opt_str(p, "note", 2000)
        no = (ctx.conn.execute(sa.select(sa.func.max(work_orders.c.doc_no))).scalar() or 0) + 1
        ctx.conn.execute(work_orders.insert().values(
            id=wid, doc_no=no, project_id=pr["id"], kit_id=kit["id"], tp_id=tp["id"], qty=qty, title=title, note=note,
            status="released", snapshot_hash=h, version=1, released_by=ctx.user_id, released_at=ctx.now,
            updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.conn.execute(work_order_snapshots.insert().values(work_order_id=wid, content_hash=h, snapshot_json=canon(snap),
                                                              created_at=ctx.now))
        for o in snap["operations"]:
            ctx.conn.execute(wo_operations.insert().values(
                id=str(uuid.uuid4()), work_order_id=wid, op_no=o["op_no"], name=o["name"], work_center=o["work_center"],
                norm_min=money.parse_decimal(o["norm_min"]), assignee_id=asg.get(o["op_no"]), state="pending",
                fact_seconds=0, problem_count=0, version=1, updated_by=ctx.user_id, updated_at=ctx.now))
        # Снимок готовности сборки (D-61): неизменяемый, с SHA-256; позднейшие согласования его не переписывают.
        snap_lines = snap["spec"]["lines"]
        codes = sorted({ln["code"] for ln in snap_lines if ln.get("code")})
        items_by_code = {r.code: {"id": r.id, "unit": r.unit} for r in ctx.conn.execute(
            sa.select(stock_items.c.id, stock_items.c.code, stock_items.c.unit)
            .where(stock_items.c.code.in_(codes)))} if codes else {}
        mats = prod.coverage_materials(snap_lines, items_by_code, self._secured_by_item(ctx.conn, pr["id"]))
        needs = [{"id": r.id, "status": r.status, "line_count": r.line_count, "matched_count": r.matched_count}
                 for r in ctx.conn.execute(sa.select(procurement_needs)
                                           .where(procurement_needs.c.calc_version_id == spec["calc_version_id"])
                                           .order_by(procurement_needs.c.created_at))]
        readiness = prod.readiness_content(work_order={"id": wid, "doc_no": no, "qty": qty}, tp=tp, spec=spec,
                                           needs=needs,
                                           rkd={"status": rkd_status, "label_ru": rkd_label, "ok": rkd_ok},
                                           risks=risks, materials=mats)
        rh = prod.readiness_hash(readiness)
        ctx.conn.execute(assembly_readiness_snapshots.insert().values(
            work_order_id=wid, project_id=pr["id"], kit_id=kit["id"], content_json=canon(readiness),
            content_hash=rh, created_by=ctx.user_id, created_at=ctx.now))
        wo = self._get(ctx.conn, work_orders, wid)
        ctx.log("work_order", wid, auth, None, 1,
                {"doc_no": no, "project_id": pr["id"], "kit_code": kit["code"], "tp_revision_no": tp["revision_no"],
                 "qty": qty, "snapshot_hash": h, "spec_version_no": spec["version_no"],
                 "readiness_verdict": readiness["verdict"], "readiness_hash": rh,
                 "readiness_risks": [r["id"] for r in risks],
                 "assignees": {str(k): v for k, v in sorted(asg.items())}})
        self._notify(ctx, [u for u in asg.values() if u], "wo_assigned", f"Наряд № {no}: вам назначены операции",
                     f"{title}. Выпустил: {self._actor_label(ctx, auth)}.", "work_order", wid)
        if risks:
            self._notify(ctx, self._project_rps(ctx, pr["id"]), "wo_released_risk",
                         f"Наряд № {no} выпущен по риск-разрешению",
                         f"{title}. РКД заказчиком не согласована («{rkd_label}»); основание — риск-разрешение "
                         f"«{risks[0]['label_ru']}» до {risks[0]['due_on']}.", "work_order", wid)
        return {"work_order": self.v_work_order(ctx.conn, wo), "wo_operations": self._wo_ops_view(ctx.conn, wo)}

    def _wo_ops_view(self, conn, wo: dict) -> list[dict]:
        """Все операции наряда для клиента."""
        return [self.v_wo_operation(conn, dict(r._mapping), wo) for r in conn.execute(
            sa.select(wo_operations).where(wo_operations.c.work_order_id == wo["id"]).order_by(wo_operations.c.op_no))]

    def _wo_of_op(self, ctx, op_id: str) -> tuple[dict, dict, dict]:
        """Операция наряда, её наряд и проект (или not_found)."""
        op = self._require(ctx.conn, wo_operations, op_id, "Операция наряда")
        wo = self._get(ctx.conn, work_orders, op["work_order_id"])
        return op, wo, self._get(ctx.conn, projects, wo["project_id"])

    def _wo_assign(self, ctx, p, ev):
        """wo.assign {id, assignee_id} — назначить (или снять) исполнителя операции наряда. Только онлайн.

        Пока операция не выполнена и наряд открыт. Если операция уже в работе, новый исполнитель продолжает её:
        таймер и факт не обнуляются (они принадлежат операции, а не человеку; события хранят, кто что сделал).
        """
        only_keys(p, {"id", "assignee_id"})
        op, wo, pr = self._wo_of_op(ctx, req_uuid(p, "id"))
        auth = self.policy.check(ctx.conn, ctx.user_id, "wo.assign", ctx.now, project_id=pr["id"])
        view = lambda x: self.v_wo_operation(ctx.conn, x, wo)  # noqa: E731
        if wo["status"] not in ("released", "in_progress"):
            raise Rejected("wo_state", f"Наряд «{prod.WO_STATUS_LABELS_RU[wo['status']]}» — назначения не меняются.")
        if op["state"] == "done":
            raise Rejected("operation_done", "Операция уже выполнена.")
        self._check_version(op, ev, view)
        uid = self._check_producer(ctx.conn, p.get("assignee_id"), ctx.now)
        if uid == op["assignee_id"]:
            return {"wo_operation": view(op), "unchanged": True}
        n = self._update_versioned(ctx, wo_operations, op, {"assignee_id": uid, "updated_by": ctx.user_id,
                                                            "updated_at": ctx.now})
        ctx.log("wo_operation", op["id"], auth, op["version"], n["version"],
                {"doc_no": wo["doc_no"], "op_no": op["op_no"], "assignee_from": op["assignee_id"], "assignee_to": uid})
        self._notify(ctx, [uid], "wo_assigned", f"Наряд № {wo['doc_no']}: вам назначена операция {op['op_no']}",
                     f"{op['name']}. Назначил: {self._actor_label(ctx, auth)}.", "work_order", wo["id"])
        return {"wo_operation": view(n)}

    def _wo_op_event(self, ctx, p, ev):
        """wo.op_event {id, kind, device_time, qty_done?, note?} — событие исполнителя (можно полностью офлайн).

        Проверки на сервере в момент приёма (при синхронизации): роль «Производство» и назначение именно на эту
        операцию; наряд открыт; переход допустим; время не раньше предыдущего события. Расхождение состояния —
        Conflict с текущей операцией (видимый конфликт в очереди клиента; сервер ничего не «додумывает»).
        Наряд переходит в «В работе» при первом старте и в «Выполнен», когда выполнены все операции.
        """
        only_keys(p, {"id", "kind", "device_time", "qty_done", "note"})
        op, wo, pr = self._wo_of_op(ctx, req_uuid(p, "id"))
        auth = self.policy.check(ctx.conn, ctx.user_id, "wo.op_event", ctx.now, project_id=pr["id"])
        view = lambda x: self.v_wo_operation(ctx.conn, x, self._get(ctx.conn, work_orders, wo["id"]))  # noqa: E731
        kind = p.get("kind")
        if kind not in prod.EVENT_KINDS:
            raise Rejected("invalid_event", "Событие — start, pause, resume, finish или problem.", {"field": "kind"})
        note = opt_str(p, "note", 2000)
        if kind == "problem" and not note:
            raise Rejected("field_required", "Опишите проблему.", {"field": "note"})
        try:
            dt = prod.parse_device_time(p.get("device_time"))
        except prod.ProductionError as e:
            raise _production_error(e)
        if dt > ctx.now + DEVICE_CLOCK_SKEW:
            raise Rejected("device_time_future", "Время события в будущем — проверьте часы компьютера и повторите.",
                           {"field": "device_time"})
        if wo["status"] not in ("released", "in_progress"):
            raise Conflict("work_order_closed",
                           f"Наряд № {wo['doc_no']} уже «{prod.WO_STATUS_LABELS_RU[wo['status']]}» — событие не применено.",
                           {"current": view(op)})
        if op["assignee_id"] != ctx.user_id:
            raise Conflict("not_assignee", "Операция назначена другому исполнителю (или снята с вас) — событие не применено.",
                           {"current": view(op)})
        try:
            ch = prod.apply_event(op, kind, dt, wo["qty"], p.get("qty_done"))
        except prod.ProductionError as e:
            raise _production_error(e, view(op))
        ctx.conn.execute(wo_timer_events.insert().values(
            wo_operation_id=op["id"], kind=kind, device_time=dt, server_time=ctx.now, user_id=ctx.user_id,
            device_id=ctx.device_id, operation_id=ctx.operation_id, qty_done=ch.get("qty_done"), note=note))
        if kind == "problem":
            ch["problem_count"] = op["problem_count"] + 1
        n = self._update_versioned(ctx, wo_operations, op, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("wo_operation", op["id"], auth, op["version"], n["version"],
                {"doc_no": wo["doc_no"], "op_no": op["op_no"], "kind": kind, "device_time": iso(dt),
                 "state": n["state"], "fact_seconds": n["fact_seconds"], "qty_done": n["qty_done"], "note": note})
        # Статус наряда следует за операциями (не отдельная команда — иначе «наряд в работе» мог бы врать).
        new_status = wo["status"]
        if kind == "start" and wo["status"] == "released":
            new_status = "in_progress"
        if kind == "finish" and not ctx.conn.execute(sa.select(wo_operations.c.id).where(
                wo_operations.c.work_order_id == wo["id"], wo_operations.c.state != "done")).first():
            new_status = "completed"
        if new_status != wo["status"]:
            wn = self._update_versioned(ctx, work_orders, wo, {"status": new_status, "updated_by": ctx.user_id,
                                                               "updated_at": ctx.now})
            ctx.log("work_order", wo["id"], auth, wo["version"], wn["version"], {"status": new_status, "by_event": kind})
            wo = wn
        rps = self._project_role_users(ctx.conn, pr["id"], "rp", ctx.now)
        if kind == "problem":
            self._notify(ctx, rps, "wo_problem", f"Наряд № {wo['doc_no']}: проблема на операции {op['op_no']}",
                         f"{op['name']}: {note}", "work_order", wo["id"])
        if new_status == "completed" and kind == "finish":
            self._notify(ctx, rps, "wo_completed", f"Наряд № {wo['doc_no']} выполнен — можно закрывать",
                         wo["title"], "work_order", wo["id"])
        return {"wo_operation": self.v_wo_operation(ctx.conn, n, wo), "work_order": self.v_work_order(ctx.conn, wo)}

    def _wo_substitute(self, ctx, p, ev):
        """wo.substitute {id, line_id, code?, name, unit, qty, reason, preview_hash} — замена позиции состава.

        Снимок НЕ переписывается: создаётся неизменяемая запись замены (было/стало, причина, хэш предпросмотра и
        результата, основание). preview_hash — хэш действующего состава из предпросмотра: если состав уже другой
        (параллельная замена), конфликт — замена «вслепую» невозможна. qty — на весь наряд, в новой единице.
        """
        only_keys(p, {"id", "line_id", "code", "name", "unit", "qty", "reason", "preview_hash"})
        wo = self._require(ctx.conn, work_orders, req_uuid(p, "id"), "Наряд")
        auth = self.policy.check(ctx.conn, ctx.user_id, "wo.substitute", ctx.now, project_id=wo["project_id"])
        view = lambda x: self.v_work_order(ctx.conn, x)  # noqa: E731
        if wo["status"] not in prod.WO_OPEN:
            raise Rejected("wo_state", f"Наряд «{prod.WO_STATUS_LABELS_RU[wo['status']]}» — замены невозможны.")
        self._check_version(wo, ev, view)
        snap, _ = self._snapshot(ctx.conn, wo["id"])
        subs = self._substitutions(ctx.conn, wo["id"])
        eff = prod.effective_spec(snap["spec"]["lines"], subs)
        cur_hash = prod.spec_hash(eff)
        if p.get("preview_hash") != cur_hash:
            raise Conflict("preview_stale", "Состав наряда изменился после предпросмотра — проверьте и повторите замену.",
                           {"current": view(wo), "spec_hash": cur_hash})
        line_id = req_str(p, "line_id", 64)
        old = next((ln for ln in eff if ln["line_id"] == line_id), None)
        if old is None:
            raise Rejected("line_not_found", "В составе наряда нет такой строки.", {"field": "line_id"})
        try:
            qty = money.to_json(prod_qty(p.get("qty")))
        except prod.ProductionError as e:
            raise _production_error(e)
        new = {"code": opt_str(p, "code", 64), "name": req_str(p, "name", 300), "unit": req_str(p, "unit", 20), "qty": qty}
        old_v = {k: old[k] for k in ("code", "name", "unit", "qty")}
        if new == old_v:
            raise Rejected("substitution_same", "Новая позиция совпадает с текущей — заменять нечего.")
        reason = req_str(p, "reason", 2000)
        seq = len(subs) + 1
        result = prod.effective_spec(snap["spec"]["lines"], subs + [{"seq": seq, "line_id": line_id, "new": new}])
        rh = prod.spec_hash(result)
        ctx.conn.execute(wo_substitutions.insert().values(
            work_order_id=wo["id"], seq=seq, line_id=line_id, old_json=canon(old_v), new_json=canon(new), reason=reason,
            preview_hash=cur_hash, result_hash=rh, authority=auth.describe(), created_by=ctx.user_id, created_at=ctx.now))
        self._record_basis(ctx, "work_order_substitution", f"{wo['id']}#{seq}", 1, auth)
        n = self._update_versioned(ctx, work_orders, wo, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("work_order", wo["id"], auth, wo["version"], n["version"],
                {"substitution_seq": seq, "line_id": line_id, "old": old_v, "new": new, "reason": reason,
                 "preview_hash": cur_hash, "result_hash": rh, "snapshot_hash": wo["snapshot_hash"]})
        return {"work_order": view(n)}

    def _wo_close(self, ctx, p, ev):
        """wo.close {id} — закрыть наряд. Проверки: все операции выполнены и материалы действующего состава выданы.

        Не выполнено — отказ со списком (какие операции, какие материалы и сколько не хватает).
        """
        only_keys(p, {"id"})
        wo = self._require(ctx.conn, work_orders, req_uuid(p, "id"), "Наряд")
        auth = self.policy.check(ctx.conn, ctx.user_id, "wo.close", ctx.now, project_id=wo["project_id"])
        view = lambda x: self.v_work_order(ctx.conn, x)  # noqa: E731
        if wo["status"] in ("closed", "cancelled"):
            raise Rejected("wo_state", f"Наряд уже «{prod.WO_STATUS_LABELS_RU[wo['status']]}».")
        self._check_version(wo, ev, view)
        pending = [{"op_no": r.op_no, "name": r.name, "state": r.state} for r in ctx.conn.execute(
            sa.select(wo_operations).where(wo_operations.c.work_order_id == wo["id"], wo_operations.c.state != "done")
            .order_by(wo_operations.c.op_no))]
        if pending:
            raise Rejected("operations_not_done",
                           f"Не выполнены операции: {', '.join(str(x['op_no']) for x in pending)}. Закрыть наряд нельзя.",
                           {"operations": pending})
        v = view(wo)
        short = [m for m in v["materials"] if m["state"] in prod.BLOCKING_MATERIAL_STATES]
        if short:
            raise Rejected("materials_not_issued",
                           "Материалы выданы со склада не полностью (или единица не совпадает со складом) — закрыть "
                           "наряд нельзя: выдайте на наряд или сделайте замену в снимке.",
                           {"materials": short})
        n = self._update_versioned(ctx, work_orders, wo, {"status": "closed", "closed_by": ctx.user_id, "closed_at": ctx.now,
                                                          "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("work_order", wo["id"], auth, wo["version"], n["version"],
                {"status": "closed", "event": "WorkOrderClosed", "fact_seconds": v["fact_seconds"],
                 "spec_hash": v["spec_hash"], "substitutions": len(v["substitutions"])})
        return {"work_order": view(n)}

    def _wo_cancel(self, ctx, p, ev):
        """wo.cancel {id, reason} — отменить наряд («Выпущен» или «В работе»), причина обязательна."""
        only_keys(p, {"id", "reason"})
        wo = self._require(ctx.conn, work_orders, req_uuid(p, "id"), "Наряд")
        auth = self.policy.check(ctx.conn, ctx.user_id, "wo.cancel", ctx.now, project_id=wo["project_id"])
        view = lambda x: self.v_work_order(ctx.conn, x)  # noqa: E731
        if wo["status"] not in ("released", "in_progress"):
            raise Rejected("wo_state", f"Наряд «{prod.WO_STATUS_LABELS_RU[wo['status']]}» отменить нельзя.")
        self._check_version(wo, ev, view)
        reason = req_str(p, "reason", 2000)
        n = self._update_versioned(ctx, work_orders, wo, {"status": "cancelled", "cancel_reason": reason,
                                                          "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("work_order", wo["id"], auth, wo["version"], n["version"], {"status": "cancelled", "reason": reason})
        return {"work_order": view(n)}

    # ------------------------------------------------------------ выдача на наряд (часть stock.issue)
    def _wo_for_issue(self, ctx, value: Any, project_id: str) -> Optional[dict]:
        """Наряд для выдачи со склада: того же проекта и открытый. None — выдача не на наряд."""
        if value is None:
            return None
        wo = self._require(ctx.conn, work_orders, req_uuid({"work_order_id": value}, "work_order_id"), "Наряд")
        if wo["project_id"] != project_id:
            raise Rejected("wo_project_mismatch", "Наряд относится к другому проекту.", {"field": "work_order_id"})
        if wo["status"] not in prod.WO_OPEN:
            raise Rejected("wo_state", f"Наряд «{prod.WO_STATUS_LABELS_RU[wo['status']]}» — выдача на него невозможна.",
                           {"field": "work_order_id"})
        return wo

    # ------------------------------------------------------------ pull
    # ------------------------------------------------------------ нормы трудоёмкости (D-72, этап 7 blueprint 52)
    @staticmethod
    def v_norm_proposal(o: dict, people: Optional[dict[str, str]] = None) -> dict:
        """Предложение нормы: операция, агрегированный диапазон (снимок на момент предложения), статус, решение."""
        people = people or {}
        return {"id": o["id"], "op_name": o["op_name"], "work_center": o["work_center"],
                "sample_size": o["sample_size"], "min_min": money.to_json(o["min_min"]),
                "median_min": money.to_json(o["median_min"]), "max_min": money.to_json(o["max_min"]),
                "proposed_min": money.to_json(o["proposed_min"]), "comment": o["comment"],
                "status": o["status"], "decision_reason": o["decision_reason"], "version": o["version"],
                "created_by": o["created_by"], "created_by_name": people.get(o["created_by"], ""),
                "created_at": iso(o["created_at"]),
                "decided_by_name": people.get(o.get("decided_by") or "", ""),
                "decided_at": iso(o["decided_at"]) if o.get("decided_at") else None}

    def _norm_stats(self, conn, uid: str, now) -> dict:
        """Запрос norm_stats: агрегированные диапазоны фактической трудоёмкости по завершённым операциям нарядов
        (все проекты; агрегат — без персональных данных). Право norm.read проверено вызывающим (_query)."""
        ops = [dict(r._mapping) for r in conn.execute(
            sa.select(wo_operations.c.name, wo_operations.c.work_center, wo_operations.c.fact_seconds,
                      wo_operations.c.finished_at).where(wo_operations.c.state == "done"))]
        stats = prod.norm_stats(ops)
        # Действующие нормы из утверждённых ТП — справочно (какие нормы сейчас стоят у таких операций).
        norms: dict[tuple, list[str]] = defaultdict(list)
        for r in conn.execute(sa.select(tech_process_ops.c.name, tech_process_ops.c.work_center,
                                        tech_process_ops.c.norm_min)
                              .select_from(tech_process_ops.join(
                                  tech_processes, tech_processes.c.id == tech_process_ops.c.tp_id))
                              .where(tech_processes.c.status == "approved")):
            norms[(r.name, r.work_center or "")].append(money.to_json(r.norm_min))
        for st in stats:
            st["current_norms"] = sorted(set(norms.get((st["op_name"], st["work_center"]), [])))
        return {"status": "accepted", "result": {"stats": stats}}

    def _norm_propose(self, ctx, p, ev):
        """norm.propose {id, op_name, work_center, proposed_min, comment?} — предложить норму по агрегированным
        данным. Диапазон сервер пересчитывает сам в момент команды (снимок); нужна выборка ≥ 2 завершённых
        операций — по одному факту норму не предлагают. Норма НЕ применяется автоматически (ручное утверждение,
        затем правка черновика ТП обычным путём)."""
        only_keys(p, {"id", "op_name", "work_center", "proposed_min", "comment"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "norm.propose", ctx.now)
        name = req_str(p, "op_name", 200)
        wc = opt_str(p, "work_center", 200) or ""
        stats = prod.norm_stats([dict(r._mapping) for r in ctx.conn.execute(
            sa.select(wo_operations.c.name, wo_operations.c.work_center, wo_operations.c.fact_seconds,
                      wo_operations.c.finished_at)
            .where(wo_operations.c.state == "done", wo_operations.c.name == name,
                   wo_operations.c.work_center == wc))])
        st = next((x for x in stats if x["op_name"] == name and x["work_center"] == wc), None)
        if st is None or st["sample_size"] < 2:
            raise Rejected("sample_too_small",
                           "Норму предлагают по агрегированным данным: нужна выборка хотя бы из двух завершённых "
                           "операций с этим наименованием и рабочим центром.", {"op_name": name, "work_center": wc})
        proposed = prod_qty(p.get("proposed_min"))
        if proposed <= 0:
            raise Rejected("bad_value", "Предлагаемая норма должна быть больше нуля (минуты).",
                           {"field": "proposed_min"})
        nid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, norm_proposals, nid)
        ctx.conn.execute(norm_proposals.insert().values(
            id=nid, op_name=name, work_center=wc, sample_size=st["sample_size"],
            min_min=money.parse_decimal(str(st["min_min"])), median_min=money.parse_decimal(str(st["median_min"])),
            max_min=money.parse_decimal(str(st["max_min"])),
            proposed_min=proposed, comment=opt_str(p, "comment", 2000), status="proposed", decision_reason=None,
            version=1, created_by=ctx.user_id, created_at=ctx.now, decided_by=None, decided_at=None))
        ctx.log("norm_proposal", nid, auth, None, 1, {"op_name": name, "work_center": wc,
                                                      "sample_size": st["sample_size"]})
        return {"proposal": self.v_norm_proposal(self._get(ctx.conn, norm_proposals, nid))}

    def _norm_decide(self, ctx, p, ev):
        """norm.decide {id, resolution, reason?} — ручное решение по предложению нормы: approved | rejected
        (отклонение — с причиной). Решает НЕ автор предложения; решение одноразовое. Утверждение не меняет нормы
        в техпроцессах — технолог правит черновик ТП обычным путём (D-72)."""
        only_keys(p, {"id", "resolution", "reason"})
        o = self._require(ctx.conn, norm_proposals, req_uuid(p, "id"), "Предложение нормы")
        auth = self.policy.check(ctx.conn, ctx.user_id, "norm.decide", ctx.now)
        if o["created_by"] == ctx.user_id:
            raise Rejected("self_decision", "Предложение нормы решает не его автор (разделение полномочий).")
        if o["status"] != "proposed":
            raise Rejected("decision_final", "Решение по предложению уже принято и неизменяемо.")
        resolution = p.get("resolution")
        if resolution not in ("approved", "rejected"):
            raise Rejected("bad_value", "Решение: approved (утвердить) или rejected (отклонить).",
                           {"field": "resolution"})
        reason = opt_str(p, "reason", 2000)
        if resolution == "rejected" and not reason:
            raise Rejected("reason_required", "Отклонение предложения нормы — с причиной.", {"field": "reason"})
        n = self._update_versioned(ctx, norm_proposals, o, {"status": resolution, "decision_reason": reason,
                                                            "decided_by": ctx.user_id, "decided_at": ctx.now})
        self._notify(ctx, [o["created_by"]], "norm_decided",
                     "Норма «" + o["op_name"] + "»: " + ("утверждена" if resolution == "approved" else "отклонена"),
                     f"Предложение нормы {o['proposed_min']} мин ({o['op_name']}, {o['work_center'] or 'без РЦ'})"
                     + (f". Причина: {reason}" if reason else ""),
                     object_type="norm_proposal", object_id=o["id"])
        ctx.log("norm_proposal", o["id"], auth, o["version"], n["version"], {"resolution": resolution})
        return {"proposal": self.v_norm_proposal(n)}

    def _pull_production(self, conn, uid: str, now) -> dict:
        """ТП и состав изделий (без цен) — по праву tp.read в проекте; наряды и их операции — по праву wo.read в
        проекте, а исполнителю с ролью «Производство» — наряды, где он назначен хотя бы на одну операцию.
        """
        out: dict[str, Any] = {"tech_processes": [], "tech_specs": [], "production_kits": [], "work_orders": [],
                               "wo_operations": [], "production_staff": [], "norm_proposals": []}
        pids = [r.id for r in conn.execute(sa.select(projects.c.id).order_by(projects.c.code))]
        tp_ids = [pid for pid in pids if self.policy.can(conn, uid, "tp.read", now, pid)]
        if tp_ids:
            for r in conn.execute(sa.select(tech_processes).where(tech_processes.c.project_id.in_(tp_ids))
                                  .order_by(tech_processes.c.kit_id, tech_processes.c.revision_no)):
                out["tech_processes"].append(self.v_tech_process(conn, dict(r._mapping)))
            kit_codes = {r.id: (r.code, r.title) for r in conn.execute(
                sa.select(kits.c.id, kits.c.code, kits.c.title).where(kits.c.project_id.in_(tp_ids)))}
            # Комплекты для «Нового ТП»: технологу не нужно право чтения проекта целиком (задачи, документы, команда) —
            # достаточно кода проекта, его статуса и списка комплектов там, где он может читать ТП.
            for r in conn.execute(sa.select(kits.c.id, kits.c.project_id, kits.c.code, kits.c.title, projects.c.code.label("pcode"),
                                            projects.c.status).join(projects, projects.c.id == kits.c.project_id)
                                  .where(kits.c.project_id.in_(tp_ids)).order_by(projects.c.code, kits.c.code)):
                out["production_kits"].append({"id": r.id, "project_id": r.project_id, "code": r.code, "title": r.title,
                                               "project_code": r.pcode, "project_status": r.status})
            for v in conn.execute(sa.select(calc_versions.c.id).where(calc_versions.c.project_id.in_(tp_ids))
                                  .order_by(calc_versions.c.kit_id, calc_versions.c.version_no)):
                s = self._spec_of_version(conn, v.id)
                code, title = kit_codes.get(s["kit_id"], ("?", ""))
                out["tech_specs"].append({"id": s["calc_version_id"], **s, "kit_code": code, "kit_title": title})
        wo_projects = [pid for pid in pids if self.policy.can(conn, uid, "wo.read", now, pid)]
        cond = []
        if wo_projects:
            cond.append(work_orders.c.project_id.in_(wo_projects))
        if self._is_producer(conn, uid, now):
            cond.append(work_orders.c.id.in_(sa.select(wo_operations.c.work_order_id).where(wo_operations.c.assignee_id == uid)))
        if cond:
            for r in conn.execute(sa.select(work_orders).where(sa.or_(*cond)).order_by(work_orders.c.doc_no)):
                wo = dict(r._mapping)
                out["work_orders"].append(self.v_work_order(conn, wo))
                out["wo_operations"].extend(self._wo_ops_view(conn, wo))
        # Справочник исполнителей — тем, кто может выпускать наряды и назначать (хотя бы в одном проекте).
        if any(self.policy.can(conn, uid, "wo.release", now, pid) for pid in pids):
            staff = []
            for u in conn.execute(sa.select(users.c.id, users.c.display_name).where(users.c.active == sa.true())
                                  .order_by(users.c.display_name)):
                if self._is_producer(conn, u.id, now):
                    staff.append({"id": u.id, "display_name": u.display_name})
            out["production_staff"] = staff
        # Предложения норм — тем, кто читает статистику норм (norm.read); ФИО автора/решения подставляем.
        if self.policy.can(conn, uid, "norm.read", now):
            people = {r.id: r.display_name for r in conn.execute(
                sa.select(users.c.id, users.c.display_name).where(users.c.active == sa.true()))}
            out["norm_proposals"] = [self.v_norm_proposal(dict(r._mapping), people) for r in conn.execute(
                sa.select(norm_proposals).order_by(norm_proposals.c.created_at))]
        return out


def prod_qty(value: Any) -> Decimal:
    """Количество в замене: строго положительная десятичная строка (≤ 4 знаков) → Decimal."""
    try:
        d = money.parse_decimal(value)
    except money.MoneyError as e:
        raise prod.ProductionError(e.code, f"«Количество»: {e.message_ru}", {"field": "qty"})
    if d <= 0:
        raise prod.ProductionError("qty_not_positive", "«Количество» должно быть больше нуля.", {"field": "qty"})
    return d
