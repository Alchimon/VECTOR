"""Консолидация закупок и аналоги (этап 4 blueprint 52): закупочные пакеты, распределения, предложения аналогов.

Пакет собирает строки потребности разных комплектов/проектов в одну закупку по ТОЧНОЙ позиции номенклатуры и
единице. Покрытие строки потребности считается в транзакции команды (SERIALIZABLE): потребность минус занятое
другими пакетами (open/consolidated) и строками черновых/размещённых заказов. Отмена пакета освобождает покрытие.
Аналог — только предложение с отдельным решением ГИП/ведущего инженера; молчаливой замены нет.

Чистые правила — vector.core.consolidation; переходы пакета — контракт workflows (procurement_batch).
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import consolidation as cs
from vector.core.errors import Rejected

from vector.core import money

from .db import (item_analogs, procurement_allocations, procurement_batch_lines, procurement_batches,
                 procurement_groups, procurement_need_lines, procurement_needs, purchase_order_batch_lines,
                 purchase_order_lines, purchase_orders, stock_items)
from .validation import iso, only_keys, opt_date, opt_str, req_str, req_uuid


class ConsolidationMixin:
    """Обработчики команд и pull-срез консолидации закупок. Подключается в Service (базы + self.handlers)."""

    # ------------------------------------------------------------ представления для клиента
    @staticmethod
    def v_group(o: dict) -> dict:
        return {"id": o["id"], "code": o["code"], "name": o["name"], "active": o["active"],
                "version": o["version"], "updated_at": iso(o["updated_at"])}

    def v_batch(self, o: dict) -> dict:
        wf = cs.batch_workflow(self.wf)
        return {"id": o["id"], "batch_no": o["batch_no"], "title": o["title"], "group_id": o["group_id"],
                "planned_on": iso(o["planned_on"]), "status": o["status"],
                "status_label": wf["labels_ru"][o["status"]], "close_reason": o["close_reason"],
                "version": o["version"], "created_by": o["created_by"], "created_at": iso(o["created_at"]),
                "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_batch_line(o: dict, allocated: Optional[Decimal] = None, ordered: Optional[Decimal] = None,
                     delivered: Optional[Decimal] = None) -> dict:
        """Строка пакета: позиция и единица + обеспеченность по происхождению (D-59): распределено по
        потребностям, заказано (размещённые заказы) и поставлено (приходы без сторно)."""
        return {"id": o["id"], "batch_id": o["batch_id"], "item_id": o["item_id"], "unit": o["unit"],
                "allocated": money.to_json(allocated), "ordered": money.to_json(ordered),
                "delivered": money.to_json(delivered)}

    @staticmethod
    def v_allocation(o: dict, delivered: Optional[Decimal] = None) -> dict:
        """Распределение строки пакета на строку потребности + поставлено по нему (приходы без сторно)."""
        return {"id": o["id"], "batch_line_id": o["batch_line_id"], "need_id": o["need_id"],
                "need_line_id": o["need_line_id"], "qty": str(o["qty"]), "delivered": money.to_json(delivered)}

    @staticmethod
    def v_analog(o: dict) -> dict:
        labels = {"proposed": "Предложен", "approved": "Утверждён", "rejected": "Отклонён"}
        return {"id": o["id"], "item_id": o["item_id"], "analog_item_id": o["analog_item_id"],
                "note": o["note"], "status": o["status"], "status_label": labels[o["status"]],
                "proposed_by": o["proposed_by"], "created_at": iso(o["created_at"]),
                "decided_by": o["decided_by"], "decided_at": iso(o["decided_at"]),
                "decision_reason": o["decision_reason"], "version": o["version"]}

    # ------------------------------------------------------------ покрытие строки потребности
    def _need_line(self, ctx, need_id: str, line_id: str) -> dict:
        """Строка потребности (неизменяемый снимок расчёта) с позицией номенклатуры, или отказ."""
        r = ctx.conn.execute(sa.select(procurement_need_lines).where(
            procurement_need_lines.c.need_id == need_id,
            procurement_need_lines.c.line_id == line_id)).first()
        if r is None:
            raise Rejected("need_line_not_found", "Строка потребности не найдена.", {"need_id": need_id})
        row = dict(r._mapping)
        if row["item_id"] is None:
            raise Rejected("line_not_matched",
                           f"Строка «{row['name']}» не сопоставлена с номенклатурой — сначала сопоставьте её.",
                           {"need_id": need_id, "line_id": line_id})
        return row

    def _coverage(self, ctx, need_id: str, line_id: str, exclude_batch: Optional[str] = None) -> dict:
        """Занятое покрытие строки потребности: распределениями действующих пакетов и строками заказов.

        Вызывается в транзакции команды (SERIALIZABLE): конкурирующее распределение либо ждёт, либо повторяется
        и видит уже занятое — двойного покрытия нет.
        """
        q = sa.select(sa.func.coalesce(sa.func.sum(procurement_allocations.c.qty), 0)).select_from(
            procurement_allocations.join(procurement_batch_lines,
                                         procurement_batch_lines.c.id == procurement_allocations.c.batch_line_id)
            .join(procurement_batches, procurement_batches.c.id == procurement_batch_lines.c.batch_id)).where(
            procurement_allocations.c.need_id == need_id, procurement_allocations.c.need_line_id == line_id,
            procurement_batches.c.status.in_(cs.COVERING_BATCH_STATUSES))
        if exclude_batch:
            q = q.where(procurement_batches.c.id != exclude_batch)
        allocated = Decimal(str(ctx.conn.execute(q).scalar()))
        ordered = Decimal(str(ctx.conn.execute(
            sa.select(sa.func.coalesce(sa.func.sum(purchase_order_lines.c.qty), 0)).select_from(
                purchase_order_lines.join(purchase_orders, purchase_orders.c.id == purchase_order_lines.c.order_id))
            .where(purchase_order_lines.c.need_id == need_id, purchase_order_lines.c.need_line_id == line_id,
                   purchase_orders.c.status.in_(cs.COVERING_ORDER_STATUSES))).scalar()))
        return {"allocated": allocated, "ordered": ordered}

    def _apply_allocations(self, ctx, batch_id: str, items: list, exclude_batch: Optional[str] = None) -> None:
        """Проверить и записать распределения пакета: строки пакета по (позиция, единица) + распределения."""
        lines: dict[tuple[str, str], str] = {}
        for need_id, line_id, qty in items:
            nl = self._need_line(ctx, need_id, line_id)
            cov = self._coverage(ctx, need_id, line_id, exclude_batch=exclude_batch)
            if qty > cs.remaining(Decimal(str(nl["qty"])), cov["allocated"], cov["ordered"]):
                raise Rejected("coverage_exceeded",
                               f"Строка «{nl['name']}»: распределить можно не больше "
                               f"{cs.remaining(Decimal(str(nl['qty'])), cov['allocated'], cov['ordered'])} "
                               f"(потребность {nl['qty']}, занято пакетами {cov['allocated']}, заказами {cov['ordered']}).",
                               {"need_id": need_id, "line_id": line_id})
            key = (nl["item_id"], nl["unit"])
            if key not in lines:
                lid = str(uuid.uuid4())
                ctx.conn.execute(procurement_batch_lines.insert().values(
                    id=lid, batch_id=batch_id, item_id=key[0], unit=key[1]))
                lines[key] = lid
            ctx.conn.execute(procurement_allocations.insert().values(
                id=str(uuid.uuid4()), batch_line_id=lines[key], need_id=need_id, need_line_id=line_id, qty=qty))

    # ------------------------------------------------------------ группы закупки
    def _pgroup_create(self, ctx, p, ev):
        """pgroup.create {id, code, name} — группа закупки (категория пакета)."""
        only_keys(p, {"id", "code", "name"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "pgroup.create", ctx.now)
        gid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, procurement_groups, gid)
        code = req_str(p, "code", 40)
        if ctx.conn.execute(sa.select(procurement_groups.c.id).where(procurement_groups.c.code == code)).first():
            raise Rejected("group_code_taken", "Группа с таким кодом уже есть.", {"field": "code"})
        ctx.conn.execute(procurement_groups.insert().values(
            id=gid, code=code, name=req_str(p, "name", 200), active=True, version=1,
            created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("procurement_group", gid, auth, None, 1, {"code": code})
        return {"group": self.v_group(self._get(ctx.conn, procurement_groups, gid))}

    def _pgroup_update(self, ctx, p, ev):
        """pgroup.update {id, name?, active?} — правка группы; отключённые группы не принимают новые пакеты."""
        only_keys(p, {"id", "name", "active"})
        g = self._require(ctx.conn, procurement_groups, req_uuid(p, "id"), "Группа закупки")
        auth = self.policy.check(ctx.conn, ctx.user_id, "pgroup.update", ctx.now)
        self._check_version(g, ev, self.v_group)
        ch: dict[str, Any] = {}
        if "name" in p:
            ch["name"] = req_str(p, "name", 200)
        if "active" in p:
            if not isinstance(p["active"], bool):
                raise Rejected("bad_value", "active — булево.", {"field": "active"})
            ch["active"] = p["active"]
        ch = {k: v for k, v in ch.items() if v != g[k]}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        n = self._update_versioned(ctx, procurement_groups, g, {**ch, "updated_by": ctx.user_id,
                                                                "updated_at": ctx.now})
        ctx.log("procurement_group", g["id"], auth, g["version"], n["version"],
                {"before": {k: g[k] for k in ch}, "after": ch})
        return {"group": self.v_group(n)}

    # ------------------------------------------------------------ закупочный пакет
    def _batch_create(self, ctx, p, ev):
        """batch.create {id, title, group_id?, planned_on?, allocations: [{need_id, need_line_id, qty}]} —
        консолидированный пакет: строки по точной позиции/единице, распределения с проверкой покрытия.
        """
        only_keys(p, {"id", "title", "group_id", "planned_on", "allocations"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "batch.create", ctx.now)
        bid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, procurement_batches, bid)
        gid = None
        if p.get("group_id") is not None:
            gid = req_uuid(p, "group_id")
            g = self._require(ctx.conn, procurement_groups, gid, "Группа закупки")
            if not g["active"]:
                raise Rejected("group_inactive", "Группа закупки отключена.", {"field": "group_id"})
        items = cs.parse_allocations(p.get("allocations"))
        if items is None:
            raise Rejected("bad_allocations",
                           "Распределения: непустой список {need_id, need_line_id, qty>0} без повторов строки.",
                           {"field": "allocations"})
        no = ctx.conn.execute(sa.select(sa.func.max(procurement_batches.c.batch_no))).scalar() or 0
        ctx.conn.execute(procurement_batches.insert().values(
            id=bid, batch_no=no + 1, title=req_str(p, "title", 300), group_id=gid, planned_on=opt_date(p, "planned_on"),
            status=cs.batch_workflow(self.wf)["initial"], version=1,
            created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        self._apply_allocations(ctx, bid, items)
        ctx.log("procurement_batch", bid, auth, None, 1, {"batch_no": no + 1, "lines": len(items)})
        return {"batch": self.v_batch(self._get(ctx.conn, procurement_batches, bid))}

    def _batch_update(self, ctx, p, ev):
        """batch.update {id, title?, group_id?, planned_on?, allocations?} — правка пакета «Формируется»;
        allocations заменяют состав целиком (строки и распределения пересобираются в той же транзакции)."""
        only_keys(p, {"id", "title", "group_id", "planned_on", "allocations"})
        b = self._require(ctx.conn, procurement_batches, req_uuid(p, "id"), "Закупочный пакет")
        auth = self.policy.check(ctx.conn, ctx.user_id, "batch.update", ctx.now)
        self._check_version(b, ev, self.v_batch)
        if b["status"] != "open":
            raise Rejected("batch_not_open", "Править можно только пакет «Формируется»; возврат — с причиной "
                                             "(переход «Сформирован» → «Формируется»).")
        ch: dict[str, Any] = {}
        if "title" in p:
            ch["title"] = req_str(p, "title", 300)
        if "group_id" in p:
            gid = None if p["group_id"] is None else req_uuid(p, "group_id")
            if gid is not None:
                g = self._require(ctx.conn, procurement_groups, gid, "Группа закупки")
                if not g["active"]:
                    raise Rejected("group_inactive", "Группа закупки отключена.", {"field": "group_id"})
            ch["group_id"] = gid
        if "planned_on" in p:
            ch["planned_on"] = opt_date(p, "planned_on")
        new_items = None
        if "allocations" in p:
            new_items = cs.parse_allocations(p["allocations"])
            if new_items is None:
                raise Rejected("bad_allocations",
                               "Распределения: непустой список {need_id, need_line_id, qty>0} без повторов строки.",
                               {"field": "allocations"})
        if not ch and new_items is None:
            raise Rejected("no_changes", "Нет изменений.")
        if new_items is not None:
            # Пересобираем состав: старые строки и распределения удаляем (пакет «Формируется» — ещё не обещание
            # поставщику), покрытие считаем без учёта этого пакета.
            line_ids = [r.id for r in ctx.conn.execute(sa.select(procurement_batch_lines.c.id).where(
                procurement_batch_lines.c.batch_id == b["id"]))]
            if line_ids:
                ctx.conn.execute(procurement_allocations.delete().where(
                    procurement_allocations.c.batch_line_id.in_(line_ids)))
                ctx.conn.execute(procurement_batch_lines.delete().where(
                    procurement_batch_lines.c.batch_id == b["id"]))
            self._apply_allocations(ctx, b["id"], new_items, exclude_batch=b["id"])
        n = self._update_versioned(ctx, procurement_batches, b, {**ch, "updated_by": ctx.user_id,
                                                                 "updated_at": ctx.now})
        ctx.log("procurement_batch", b["id"], auth, b["version"], n["version"],
                {"before": {k: b[k] for k in ch}, "after": ch, "allocations_replaced": new_items is not None})
        return {"batch": self.v_batch(n)}

    def _batch_transition(self, ctx, p, ev):
        """batch.transition {id, to, reason?} — по контракту procurement_batch; при формировании покрытие
        перепроверяется (мир мог измениться, пока пакет собирали)."""
        only_keys(p, {"id", "to", "reason"})
        b = self._require(ctx.conn, procurement_batches, req_uuid(p, "id"), "Закупочный пакет")
        auth = self.policy.check(ctx.conn, ctx.user_id, "batch.transition", ctx.now)
        self._check_version(b, ev, self.v_batch)
        wf = cs.batch_workflow(self.wf)
        to = req_str(p, "to", 16)
        if not cs.can_transition(self.wf, b["status"], to):
            raise Rejected("transition_not_allowed",
                           f"Переход «{wf['labels_ru'][b['status']]}» → «{wf['labels_ru'].get(to, to)}» "
                           "не предусмотрен контрактом.", {"from": b["status"], "to": to})
        reason = opt_str(p, "reason", 2000)
        if cs.needs_reason(self.wf, b["status"], to) and not reason:
            raise Rejected("reason_required", "Укажите причину.", {"field": "reason"})
        if to in ("open", "cancelled") and b["status"] == "consolidated":
            # По пакету уже размещают заказы: вернуть в работу или отменить его молча нельзя — сначала отмените
            # или закройте связанные заказы (поставки по ним уже могли пойти в резервы проектов).
            linked = ctx.conn.execute(sa.select(sa.func.count()).select_from(
                purchase_order_batch_lines.join(purchase_orders,
                                                purchase_orders.c.id == purchase_order_batch_lines.c.order_id))
                .where(purchase_order_batch_lines.c.batch_id == b["id"],
                       purchase_orders.c.status.in_(("draft", "placed")))).scalar()
            if linked:
                raise Rejected("batch_has_orders",
                               f"По пакету № {b['batch_no']} есть черновики или размещённые заказы ({linked} строк) — "
                               "сначала отмените или закройте их, затем меняйте пакет.",
                               {"linked_order_lines": linked})
        if to == "closed":
            # Закрытие — только когда каждое распределение поставлено полностью: «закрыт» значит «закуплено»,
            # а не «перестали ждать» (перестали ждать — отмена остатка с причиной через возврат в работу).
            allocs = [dict(r._mapping) for r in ctx.conn.execute(
                sa.select(procurement_allocations).join(
                    procurement_batch_lines,
                    procurement_batch_lines.c.id == procurement_allocations.c.batch_line_id)
                .where(procurement_batch_lines.c.batch_id == b["id"]))]
            delivered = self._delivered_by_allocation(ctx.conn, [a["id"] for a in allocs])
            open_left = [(a, Decimal(str(a["qty"])) - delivered.get(a["id"], Decimal(0))) for a in allocs]
            open_left = [(a, left) for a, left in open_left if left > 0]
            if open_left:
                a, left = open_left[0]
                raise Rejected("allocations_open",
                               f"По пакету № {b['batch_no']} поставлено не всё: осталось {money.to_json(left)} "
                               f"(всего незакрытых распределений: {len(open_left)}). Дождитесь поставки или "
                               "верните пакет в работу и отмените остаток с причиной.",
                               {"open_allocations": len(open_left)})
        if to == "consolidated":
            allocs = [dict(r._mapping) for r in ctx.conn.execute(
                sa.select(procurement_allocations).join(
                    procurement_batch_lines,
                    procurement_batch_lines.c.id == procurement_allocations.c.batch_line_id)
                .where(procurement_batch_lines.c.batch_id == b["id"]))]
            if not allocs:
                raise Rejected("batch_empty", "Пакет без распределений формировать нельзя.")
            for a in allocs:
                nl = self._need_line(ctx, a["need_id"], a["need_line_id"])
                cov = self._coverage(ctx, a["need_id"], a["need_line_id"], exclude_batch=b["id"])
                if Decimal(str(a["qty"])) > cs.remaining(Decimal(str(nl["qty"])), cov["allocated"], cov["ordered"]):
                    raise Rejected("coverage_exceeded",
                                   f"Строка «{nl['name']}»: покрытие изменилось, пока пакет собирался — "
                                   f"доступно {cs.remaining(Decimal(str(nl['qty'])), cov['allocated'], cov['ordered'])}, "
                                   f"в пакете {a['qty']}. Верните пакет в работу и поправьте распределения.",
                                   {"need_id": a["need_id"], "line_id": a["need_line_id"]})
        n = self._update_versioned(ctx, procurement_batches, b,
                                   {"status": to, "close_reason": reason if to in ("cancelled", "closed") else None,
                                    "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("procurement_batch", b["id"], auth, b["version"], n["version"],
                {"from": b["status"], "to": to, "reason": reason})
        if to == "consolidated":
            # Руководство затронутых проектов узнаёт, что их потребность вошла в сформированный пакет.
            proj_ids = [r.project_id for r in ctx.conn.execute(
                sa.select(procurement_needs.c.project_id).distinct().select_from(
                    procurement_allocations.join(
                        procurement_batch_lines,
                        procurement_batch_lines.c.id == procurement_allocations.c.batch_line_id)
                    .join(procurement_needs, procurement_needs.c.id == procurement_allocations.c.need_id))
                .where(procurement_batch_lines.c.batch_id == b["id"]))]
            users: list[str] = []
            for pid in proj_ids:
                users += self._project_rps(ctx, pid)
            self._notify(ctx, list(dict.fromkeys(users)), "batch_consolidated",
                         f"Закупочный пакет № {b['batch_no']} сформирован", b["title"][:200],
                         object_type="procurement_batch", object_id=b["id"])
        return {"batch": self.v_batch(n)}

    # ------------------------------------------------------------ аналоги
    def _analog_propose(self, ctx, p, ev):
        """analog.propose {id, item_id, analog_item_id, note?} — предложение аналога позиции.

        Предложение ничего не заменяет: утверждённый аналог — только основание для новой ревизии потребности
        из новой редакции расчёта (молчаливой замены нет).
        """
        only_keys(p, {"id", "item_id", "analog_item_id", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "analog.propose", ctx.now)
        aid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, item_analogs, aid)
        item_id, analog_id = req_uuid(p, "item_id"), req_uuid(p, "analog_item_id")
        if item_id == analog_id:
            raise Rejected("bad_value", "Аналог не может совпадать с позицией.", {"field": "analog_item_id"})
        for key, iid in (("item_id", item_id), ("analog_item_id", analog_id)):
            it = self._require(ctx.conn, stock_items, iid, "Позиция номенклатуры")
            if not it["active"]:
                raise Rejected("item_inactive", "Позиция номенклатуры отключена.", {"field": key})
        if ctx.conn.execute(sa.select(item_analogs.c.id).where(
                item_analogs.c.item_id == item_id, item_analogs.c.analog_item_id == analog_id)).first():
            raise Rejected("analog_exists", "Такое предложение уже есть (решение по нему — в журнале).",
                           {"field": "analog_item_id"})
        ctx.conn.execute(item_analogs.insert().values(
            id=aid, item_id=item_id, analog_item_id=analog_id, note=opt_str(p, "note", 1000),
            status="proposed", proposed_by=ctx.user_id, created_at=ctx.now, version=1))
        ctx.log("item_analog", aid, auth, None, 1, {"item_id": item_id, "analog_item_id": analog_id})
        return {"analog": self.v_analog(self._get(ctx.conn, item_analogs, aid))}

    def _analog_decide(self, ctx, p, ev):
        """analog.decide {id, decision: approve|reject, reason?} — решение ГИП/ведущего инженера; одноразовое."""
        only_keys(p, {"id", "decision", "reason"})
        a = self._require(ctx.conn, item_analogs, req_uuid(p, "id"), "Предложение аналога")
        auth = self.policy.check(ctx.conn, ctx.user_id, "analog.decide", ctx.now)
        self._check_version(a, ev, self.v_analog)
        decision = req_str(p, "decision", 8)
        if decision not in ("approve", "reject"):
            raise Rejected("bad_value", "Решение — approve или reject.", {"field": "decision"})
        if a["status"] != "proposed":
            raise Rejected("already_decided", "Решение по аналогу уже принято.")
        reason = opt_str(p, "reason", 2000)
        if decision == "reject" and not reason:
            raise Rejected("reason_required", "Укажите причину отклонения.", {"field": "reason"})
        n = self._update_versioned(ctx, item_analogs, a,
                                   {"status": "approved" if decision == "approve" else "rejected",
                                    "decided_by": ctx.user_id, "decided_at": ctx.now, "decision_reason": reason})
        ctx.log("item_analog", a["id"], auth, a["version"], n["version"], {"decision": decision, "reason": reason})
        self._notify(ctx, [a["proposed_by"]], "analog_decided",
                     f"Аналог позиции: {self.v_analog(n)['status_label']}",
                     reason, object_type="item_analog", object_id=a["id"])
        return {"analog": self.v_analog(n)}

    # ------------------------------------------------------------ pull
    def _pull_consolidation(self, conn, uid: str, now, readable: list[str]) -> dict:
        """Группы, пакеты со строками и распределениями, аналоги.

        Пакеты целиком видят закупка/склад/руководство; ГИП/РП проекта — пакеты, где есть распределения
        потребности их проектов. Аналоги — по analog.read. Без цен: коммерческие поля здесь не живут.
        """
        out: dict[str, Any] = {"procurement_groups": [], "procurement_batches": [], "procurement_batch_lines": [],
                               "procurement_allocations": [], "item_analogs": []}
        if self.policy.can(conn, uid, "pgroup.read", now):
            out["procurement_groups"] = [self.v_group(dict(r._mapping)) for r in conn.execute(
                sa.select(procurement_groups).order_by(procurement_groups.c.code))]
        if self.policy.can(conn, uid, "analog.read", now):
            out["item_analogs"] = [self.v_analog(dict(r._mapping)) for r in conn.execute(
                sa.select(item_analogs).order_by(item_analogs.c.created_at))]
        if not (self.policy.can(conn, uid, "batch.read", now)
                or any(self.policy.can(conn, uid, "batch.read", now, project_id=pid) for pid in readable)):
            return out
        batches = [dict(r._mapping) for r in conn.execute(
            sa.select(procurement_batches).order_by(procurement_batches.c.batch_no.desc()))]
        if not self._is_global(conn, uid, "batch.read", now):
            # Проектное чтение (ГИП/РП): только пакеты, где есть распределения потребности их проектов.
            if not readable:
                return out
            mine = {r.batch_id for r in conn.execute(
                sa.select(procurement_batch_lines.c.batch_id).distinct().select_from(
                    procurement_allocations.join(
                        procurement_batch_lines,
                        procurement_batch_lines.c.id == procurement_allocations.c.batch_line_id)
                    .join(procurement_needs, procurement_needs.c.id == procurement_allocations.c.need_id))
                .where(procurement_needs.c.project_id.in_(readable)))}
            batches = [b for b in batches if b["id"] in mine]
        b_ids = [b["id"] for b in batches]
        lines = [dict(r._mapping) for r in conn.execute(
            sa.select(procurement_batch_lines).where(procurement_batch_lines.c.batch_id.in_(b_ids)))] if b_ids else []
        line_ids = [ln["id"] for ln in lines]
        allocs = [dict(r._mapping) for r in conn.execute(
            sa.select(procurement_allocations)
            .where(procurement_allocations.c.batch_line_id.in_(line_ids)))] if line_ids else []
        # Обеспеченность по происхождению (D-59): распределено / заказано (размещённые) / поставлено (без сторно).
        allocated: dict[str, Decimal] = defaultdict(Decimal)
        for a in allocs:
            allocated[a["batch_line_id"]] += Decimal(str(a["qty"]))
        ordered: dict[str, Decimal] = defaultdict(Decimal)
        if line_ids:
            q = (sa.select(purchase_order_batch_lines.c.batch_line_id, purchase_order_lines.c.qty)
                 .select_from(purchase_order_batch_lines
                              .join(purchase_order_lines,
                                    sa.and_(purchase_order_lines.c.order_id == purchase_order_batch_lines.c.order_id,
                                            purchase_order_lines.c.line_id == purchase_order_batch_lines.c.line_id))
                              .join(purchase_orders, purchase_orders.c.id == purchase_order_batch_lines.c.order_id))
                 .where(purchase_order_batch_lines.c.batch_line_id.in_(line_ids),
                        purchase_orders.c.status.in_(("placed", "closed"))))
            for r in conn.execute(q):
                ordered[r.batch_line_id] += r.qty
        delivered_alloc = self._delivered_by_allocation(conn, [a["id"] for a in allocs])
        delivered_line: dict[str, Decimal] = defaultdict(Decimal)
        for a in allocs:
            delivered_line[a["batch_line_id"]] += delivered_alloc.get(a["id"], Decimal(0))
        out["procurement_batches"] = [self.v_batch(b) for b in batches]
        out["procurement_batch_lines"] = [self.v_batch_line(ln, allocated.get(ln["id"], Decimal(0)),
                                                            ordered.get(ln["id"], Decimal(0)),
                                                            delivered_line.get(ln["id"], Decimal(0)))
                                          for ln in lines]
        out["procurement_allocations"] = [self.v_allocation(a, delivered_alloc.get(a["id"], Decimal(0)))
                                          for a in allocs]
        return out

    def _is_global(self, conn, uid: str, action: str, now) -> bool:
        """Действие доступно глобальной ролью (не проектной): пакет виден целиком, а не по проектам."""
        try:
            auth = self.policy.check(conn, uid, action, now)
            return auth.describe().startswith(("global_role:", "delegated_role:"))
        except Exception:
            return False
