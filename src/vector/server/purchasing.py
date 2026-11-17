"""Заказы поставщикам и факт поставки (сценарий С4, шаг 5; решение D-32).

Модуль — примесь (mixin) к `Service`, как stock.py: транзакция SERIALIZABLE, идемпотентность по operation_id,
аудит и проверка версий — в service.py; здесь — только правила закупки.

    поставщики (справочник; правка офлайн через очередь — supplier.create / supplier.update)

    заказ:  draft ──po.place──▶ placed ──po.close──▶ closed
              │                   │
              └──po.cancel──▶ cancelled ◀──po.cancel (пока по заказу ничего не принято)

    факт поставки: po.receive — приход на склад по строке размещённого заказа (обычное движение журнала +
                   неизменяемая связь purchase_receipts). «Поставлено» = сумма таких приходов без сторнированных;
                   «ждём» = заказано − поставлено. Состояние «частично / полностью» — расчёт, а не статус.

Главные правила (каждое закреплено тестом в tests/server/test_purchasing.py):

* черновик правит закупка, можно офлайн (очередь, версии); размещение, отмена, закрытие и приход — только онлайн;
* размещённый заказ не меняется: строки заморожены, их SHA-256 — в placed_hash (проверяется при показе); у
  размещённого можно уточнить только справочные поля — номер счёта поставщика, ожидаемую дату, примечание;
* принять больше, чем ещё ждём по строке, нельзя (излишек — обычный приход с основанием, A-36); принимает склад;
* строка, привязанная к строке потребности, — только той же позиции номенклатуры, что выбрана для строки
  потребности (иначе «заказано» в покрытии потребности было бы ложью);
* цены видят закупка и бухгалтерия (po.read_prices); складу — количества без цен (A-35). НДС и курсы не считаются;
* ничего не придумывается: валюта не подставляется, сумма не округляется (Q8), дата поставки — только для справки.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import money, purchasing as pu, stock
from vector.core.errors import Rejected

from .db import (procurement_need_lines, procurement_needs, purchase_order_lines, purchase_orders, purchase_receipts, stock_items,
                 stock_movements, suppliers, warehouses)
from .stock import _code, _qty, _stock_rejected
from .validation import iso, only_keys, opt_date, opt_str, req_str, req_uuid

# Сколько последних закрытых/отменённых заказов отдаётся в pull (все черновики и размещённые — всегда).
PULL_CLOSED_ORDERS_LIMIT = 300


def _purchase_rejected(e: pu.PurchaseError) -> Rejected:
    """Замечания к заказу из vector.core.purchasing → отказ с первым кодом и полным списком problems."""
    return Rejected(e.code, e.message_ru, {"problems": e.problems, "field": e.problems[0].get("field")})


class PurchasingMixin:
    """Обработчики supplier.* / po.* и данные закупки для pull."""

    # ------------------------------------------------------------ представления
    @staticmethod
    def v_supplier(o: dict) -> dict:
        """Поставщик для клиента."""
        return {k: o[k] for k in ("id", "code", "name", "inn", "contact", "note", "version")} | {"active": bool(o["active"])}

    def _received_by_line(self, conn, where=None) -> dict[tuple[str, str], Decimal]:
        """Поставлено по строкам заказов: (order_id, line_id) → сумма приходов, которые НЕ сторнированы.

        Сторно прихода — отдельное движение с reverses_id; псевдоним rev ищет такое движение для каждого прихода.
        """
        rev = stock_movements.alias("rev")
        q = (sa.select(purchase_receipts.c.order_id, purchase_receipts.c.line_id, purchase_receipts.c.qty)
             .select_from(purchase_receipts.outerjoin(rev, rev.c.reverses_id == purchase_receipts.c.movement_id))
             .where(rev.c.id.is_(None)))
        if where is not None:
            q = q.where(where)
        out: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for r in conn.execute(q):
            out[(r.order_id, r.line_id)] += r.qty
        return out

    def _order_lines(self, conn, oid: str) -> list[dict]:
        """Строки заказа по порядку (как в БД)."""
        return [dict(r._mapping) for r in conn.execute(
            sa.select(purchase_order_lines).where(purchase_order_lines.c.order_id == oid).order_by(purchase_order_lines.c.pos))]

    def _raw_lines(self, conn, oid: str) -> list[dict]:
        """Строки заказа из БД в форме запроса (без суммы) — чтобы перепроверить их теми же правилами ядра."""
        return [{k: v for k, v in ln.to_json().items() if k != "amount"} for ln in self._core_lines(self._order_lines(conn, oid))]

    @staticmethod
    def _core_lines(rows: list[dict]) -> list[pu.Line]:
        """Строки из БД → проверенные строки ядра (для итога и контрольной суммы)."""
        out = []
        for r in rows:
            amount = pu.exact_amount(r["qty"], r["unit_price"]) if r["unit_price"] is not None else None
            out.append(pu.Line(r["line_id"], r["item_id"], r["qty"], r["unit_price"], amount, r["need_id"],
                               r["need_line_id"], r["note"]))
        return out

    def v_order(self, conn, o: dict, with_prices: bool, received: Optional[dict] = None,
                items: Optional[dict] = None, sup: Optional[dict] = None) -> dict:
        """Заказ для клиента: реквизиты, поставщик, строки с «поставлено / ждём», итог и состояние поставки.

        with_prices=False — цены и суммы не отдаются (склад видит количества, A-35). received / items / sup —
        заранее загруженные данные (pull), иначе читаются здесь. hash_ok — совпадают ли строки размещённого заказа с
        замороженными при размещении (False — данные изменены в обход программы).
        """
        rows = self._order_lines(conn, o["id"])
        if received is None:
            received = self._received_by_line(conn, purchase_receipts.c.order_id == o["id"])
        if items is None:
            ids = {r["item_id"] for r in rows}
            items = {r.id: dict(r._mapping) for r in conn.execute(sa.select(stock_items).where(stock_items.c.id.in_(ids)))} if ids else {}
        sup = sup or self._get(conn, suppliers, o["supplier_id"]) or {}
        core = self._core_lines(rows)
        lines, pairs = [], []
        for r, c in zip(rows, core):
            got = received.get((o["id"], r["line_id"]), Decimal(0))
            pairs.append((r["qty"], got))
            it = items.get(r["item_id"], {})
            lines.append({"line_id": r["line_id"], "pos": r["pos"], "item_id": r["item_id"], "item_code": it.get("code"),
                          "item_name": it.get("name"), "unit": it.get("unit"), "qty": money.to_json(r["qty"]),
                          "unit_price": money.to_json(r["unit_price"]) if with_prices else None,
                          "amount": money.to_json(c.amount) if with_prices else None,
                          "need_id": r["need_id"], "need_line_id": r["need_line_id"], "note": r["note"],
                          "received": money.to_json(got), "remaining": money.to_json(pu.remaining(r["qty"], got))})
        delivery = pu.delivery_state(pairs) if o["status"] in ("placed", "closed") else None
        t = pu.totals(core)
        return {"id": o["id"], "order_no": o["order_no"], "supplier_id": o["supplier_id"],
                "supplier_code": sup.get("code"), "supplier_name": sup.get("name"), "status": o["status"],
                "status_label": pu.STATUS_LABELS_RU[o["status"]], "delivery": delivery,
                "delivery_label": pu.DELIVERY_LABELS_RU[delivery] if delivery else None,
                "currency": o["currency"], "supplier_ref": o["supplier_ref"],
                "expected_on": o["expected_on"].isoformat() if o["expected_on"] else None, "note": o["note"],
                "prices_visible": with_prices,
                "total": t["total"] if with_prices else None, "unpriced_count": t["unpriced_count"] if with_prices else None,
                "hash_ok": (o["placed_hash"] == pu.content_hash(o["supplier_id"], o["currency"], core))
                if o["placed_hash"] else None,
                "placed_by": o["placed_by"], "placed_at": iso(o["placed_at"]) if o["placed_at"] else None,
                "closed_by": o["closed_by"], "closed_at": iso(o["closed_at"]) if o["closed_at"] else None,
                "close_reason": o["close_reason"], "version": o["version"], "created_by": o["created_by"],
                "created_at": iso(o["created_at"]), "lines": lines}

    def _order_view(self, ctx, oid: str) -> dict:
        """Заказ oid глазами текущего пользователя (с ценами — если у него есть право их видеть)."""
        prices = self.policy.can(ctx.conn, ctx.user_id, "po.read_prices", ctx.now)
        return self.v_order(ctx.conn, self._get(ctx.conn, purchase_orders, oid), prices)

    # ------------------------------------------------------------ поставщики
    def _supplier_fields(self, p: dict, partial: bool) -> dict:
        """Поля поставщика из запроса (name, inn, contact, note); partial — только присланные."""
        out: dict[str, Any] = {}
        if not partial or "name" in p:
            out["name"] = req_str(p, "name", 300)
        if not partial or "inn" in p:
            try:
                out["inn"] = pu.check_inn(p.get("inn"))
            except pu.PurchaseError as e:
                raise _purchase_rejected(e)
        for k, n in (("contact", 500), ("note", 1000)):
            if not partial or k in p:
                out[k] = opt_str(p, k, n)
        return out

    def _supplier_create(self, ctx, p, ev):
        """supplier.create {id, code, name, inn?, contact?, note?} — новый поставщик (закупка, A-34). Можно офлайн."""
        only_keys(p, {"id", "code", "name", "inn", "contact", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "supplier.create", ctx.now)
        sid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, suppliers, sid)
        code = _code(p)
        if self._get(ctx.conn, suppliers, code, "code"):
            raise Rejected("code_exists", f"Поставщик с кодом «{code}» уже есть.", {"field": "code"})
        row = {"id": sid, "code": code, **self._supplier_fields(p, partial=False), "active": True, "version": 1,
               "created_by": ctx.user_id, "created_at": ctx.now, "updated_by": ctx.user_id, "updated_at": ctx.now}
        ctx.conn.execute(suppliers.insert().values(**row))
        ctx.log("supplier", sid, auth, None, 1, {"code": code, "name": row["name"], "inn": row["inn"]})
        return {"supplier": self.v_supplier(self._get(ctx.conn, suppliers, sid))}

    def _supplier_update(self, ctx, p, ev):
        """supplier.update {id, name?, inn?, contact?, note?, active?} + версия. Код не меняется; отключённому
        поставщику нельзя размещать новые заказы (уже размещённые остаются в силе).
        """
        only_keys(p, {"id", "name", "inn", "contact", "note", "active"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "supplier.update", ctx.now)
        o = self._require(ctx.conn, suppliers, req_uuid(p, "id"), "Поставщик")
        self._check_version(o, ev, self.v_supplier)
        ch = self._supplier_fields(p, partial=True)
        if "active" in p:
            if not isinstance(p["active"], bool):
                raise Rejected("field_type", "Поле «active» должно быть true или false.", {"field": "active"})
            ch["active"] = p["active"]
        ch = {k: v for k, v in ch.items() if v != (bool(o[k]) if k == "active" else o[k])}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: (bool(o[k]) if k == "active" else o[k]) for k in ch}
        n = self._update_versioned(ctx, suppliers, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("supplier", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"supplier": self.v_supplier(n)}

    # ------------------------------------------------------------ черновик заказа
    def _check_order_lines(self, ctx, lines: list[pu.Line], for_place: bool) -> None:
        """Проверить ссылки строк по БД: позиции существуют (при размещении — действующие); строка потребности
        существует и для неё выбрана та же позиция номенклатуры.
        """
        ids = {ln.item_id for ln in lines}
        items = {r.id: r for r in ctx.conn.execute(sa.select(stock_items).where(stock_items.c.id.in_(ids)))} if ids else {}
        for n, ln in enumerate(lines, start=1):
            it = items.get(ln.item_id)
            if it is None:
                raise Rejected("not_found", f"Строка {n}: позиция номенклатуры не найдена.", {"line": n, "field": "item_id"})
            if for_place and not it.active:
                raise Rejected("item_inactive", f"Строка {n}: позиция «{it.code}» отключена — заказывать её нельзя.",
                               {"line": n, "field": "item_id"})
            if ln.need_id:
                if self._get(ctx.conn, procurement_needs, ln.need_id) is None:
                    raise Rejected("not_found", f"Строка {n}: потребность не найдена.", {"line": n, "field": "need_id"})
                nl = ctx.conn.execute(sa.select(procurement_need_lines).where(
                    procurement_need_lines.c.need_id == ln.need_id, procurement_need_lines.c.line_id == ln.need_line_id)).first()
                if nl is None:
                    raise Rejected("not_found", f"Строка {n}: строка потребности не найдена.", {"line": n, "field": "need_line_id"})
                # Действующая позиция строки потребности: ручное решение закупки (A-25) важнее автомата из снимка.
                chosen = self._line_items(ctx.conn, ln.need_id).get(ln.need_line_id, nl.item_id)
                if chosen != ln.item_id:
                    raise Rejected("need_item_mismatch", f"Строка {n}: для строки потребности выбрана другая позиция "
                                   "номенклатуры. Сначала сопоставьте строку потребности (закупка), затем заказывайте.",
                                   {"line": n, "field": "item_id"})

    def _write_lines(self, ctx, oid: str, lines: list[pu.Line]) -> None:
        """Заменить строки черновика целиком (порядок = порядок в запросе)."""
        ctx.conn.execute(purchase_order_lines.delete().where(purchase_order_lines.c.order_id == oid))
        for pos, ln in enumerate(lines, start=1):
            ctx.conn.execute(purchase_order_lines.insert().values(
                order_id=oid, pos=pos, line_id=ln.line_id, item_id=ln.item_id, qty=ln.qty, unit_price=ln.unit_price,
                need_id=ln.need_id, need_line_id=ln.need_line_id, note=ln.note))

    def _supplier_ref(self, ctx, sid: str, must_be_active: bool) -> dict:
        """Поставщик по id (для размещения — действующий) или отказ."""
        s = self._require(ctx.conn, suppliers, sid, "Поставщик")
        if must_be_active and not s["active"]:
            raise Rejected("supplier_inactive", f"Поставщик «{s['code']}» отключён — новые заказы ему не размещают.",
                           {"field": "supplier_id"})
        return s

    def _po_create(self, ctx, p, ev):
        """po.create {id, supplier_id, currency?, supplier_ref?, expected_on?, note?, lines[]} — черновик заказа.
        Можно офлайн (очередь). Номер заказа — сквозной, назначает сервер при создании.
        """
        only_keys(p, {"id", "supplier_id", "currency", "supplier_ref", "expected_on", "note", "lines"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "po.create", ctx.now)
        oid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, purchase_orders, oid)
        sup = self._supplier_ref(ctx, req_uuid(p, "supplier_id"), must_be_active=False)
        try:
            lines, cur = pu.parse_lines(p.get("lines", []), p.get("currency"))
        except pu.PurchaseError as e:
            raise _purchase_rejected(e)
        self._check_order_lines(ctx, lines, for_place=False)
        no = (ctx.conn.execute(sa.select(sa.func.max(purchase_orders.c.order_no))).scalar() or 0) + 1
        ctx.conn.execute(purchase_orders.insert().values(
            id=oid, order_no=no, supplier_id=sup["id"], status="draft", currency=cur,
            supplier_ref=opt_str(p, "supplier_ref", 200), expected_on=opt_date(p, "expected_on"),
            note=opt_str(p, "note", 2000), version=1, created_by=ctx.user_id, created_at=ctx.now,
            updated_by=ctx.user_id, updated_at=ctx.now))
        self._write_lines(ctx, oid, lines)
        ctx.log("purchase_order", oid, auth, None, 1, {"order_no": no, "supplier_code": sup["code"], "status": "draft",
                                                       "lines": len(lines), "currency": cur, **pu.totals(lines)})
        return {"purchase_order": self._order_view(ctx, oid)}

    def _po_update(self, ctx, p, ev):
        """po.update {id, …} + версия. Черновик — любые поля и строки целиком. Размещённый — только справочные поля
        (supplier_ref, expected_on, note): условия заказа после размещения не меняются (A-36). Закрытый/отменённый —
        ничего.
        """
        only_keys(p, {"id", "supplier_id", "currency", "supplier_ref", "expected_on", "note", "lines"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "po.update", ctx.now)
        o = self._require(ctx.conn, purchase_orders, req_uuid(p, "id"), "Заказ поставщику")
        self._check_version(o, ev, lambda x: self._order_view(ctx, x["id"]))
        if o["status"] in ("closed", "cancelled"):
            raise Rejected("order_state", f"Заказ № {o['order_no']} {pu.STATUS_LABELS_RU[o['status']].lower()} — менять его нельзя.")
        frozen = {"supplier_id", "currency", "lines"} & set(p)
        if o["status"] == "placed" and frozen:
            raise Rejected("order_placed", f"Заказ № {o['order_no']} уже размещён: поставщик, валюта и строки не меняются. "
                           "Нужны другие условия — закройте или отмените заказ и разместите новый.",
                           {"field": sorted(frozen)[0]})
        ch: dict[str, Any] = {}
        if "supplier_id" in p:
            ch["supplier_id"] = self._supplier_ref(ctx, req_uuid(p, "supplier_id"), must_be_active=False)["id"]
        if "supplier_ref" in p:
            ch["supplier_ref"] = opt_str(p, "supplier_ref", 200)
        if "expected_on" in p:
            ch["expected_on"] = opt_date(p, "expected_on")
        if "note" in p:
            ch["note"] = opt_str(p, "note", 2000)
        lines_changed = False
        if "lines" in p or "currency" in p:
            cur_in = p["currency"] if "currency" in p else o["currency"]
            # Меняется только валюта — текущие строки перепроверяются с новой валютой (цены без валюты недопустимы).
            raw = p["lines"] if "lines" in p else self._raw_lines(ctx.conn, o["id"])
            try:
                lines, cur = pu.parse_lines(raw, cur_in)
            except pu.PurchaseError as e:
                raise _purchase_rejected(e)
            self._check_order_lines(ctx, lines, for_place=False)
            if cur != o["currency"]:
                ch["currency"] = cur
            if "lines" in p:
                if self._raw_lines(ctx.conn, o["id"]) != [{k: v for k, v in ln.to_json().items() if k != "amount"} for ln in lines]:
                    self._write_lines(ctx, o["id"], lines)
                    lines_changed = True
        ch = {k: v for k, v in ch.items() if v != o[k]}
        if not ch and not lines_changed:
            raise Rejected("no_changes", "Нет изменений.")
        n = self._update_versioned(ctx, purchase_orders, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("purchase_order", o["id"], auth, o["version"], n["version"],
                {"order_no": o["order_no"], "changed": sorted(ch) + (["lines"] if lines_changed else []),
                 "before": {k: (o[k].isoformat() if hasattr(o[k], "isoformat") else o[k]) for k in ch},
                 "after": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in ch.items()}})
        return {"purchase_order": self._order_view(ctx, o["id"])}

    # ------------------------------------------------------------ размещение, отмена, закрытие
    def _po_place(self, ctx, p, ev):
        """po.place {id} + версия — разместить заказ у поставщика: черновик → «Размещён». Только онлайн.

        Проверяется всё заново: поставщик действующий, хотя бы одна строка, позиции действующие, ссылки на
        потребность, валюта при ценах. Строки замораживаются (SHA-256 — placed_hash).
        """
        only_keys(p, {"id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "po.place", ctx.now)
        o = self._require(ctx.conn, purchase_orders, req_uuid(p, "id"), "Заказ поставщику")
        self._check_version(o, ev, lambda x: self._order_view(ctx, x["id"]))
        if o["status"] != "draft":
            raise Rejected("order_state", f"Заказ № {o['order_no']} уже {pu.STATUS_LABELS_RU[o['status']].lower()} — "
                           "разместить можно только черновик.")
        sup = self._supplier_ref(ctx, o["supplier_id"], must_be_active=True)
        try:
            lines, cur = pu.parse_lines(self._raw_lines(ctx.conn, o["id"]), o["currency"], require_lines=True)
        except pu.PurchaseError as e:
            raise _purchase_rejected(e)
        self._check_order_lines(ctx, lines, for_place=True)
        h = pu.content_hash(o["supplier_id"], cur, lines)
        n = self._update_versioned(ctx, purchase_orders, o, {"status": "placed", "placed_hash": h, "placed_by": ctx.user_id,
                                                             "placed_at": ctx.now, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("purchase_order", o["id"], auth, o["version"], n["version"],
                {"order_no": o["order_no"], "status": "placed", "supplier_code": sup["code"], "lines": len(lines),
                 "currency": cur, "placed_hash": h, **pu.totals(lines)})
        return {"purchase_order": self._order_view(ctx, o["id"])}

    def _po_cancel(self, ctx, p, ev):
        """po.cancel {id, reason} + версия — отменить черновик или размещённый заказ, по которому ещё ничего не
        поставлено (с учётом сторно). Причина обязательна. Только онлайн.
        """
        only_keys(p, {"id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "po.cancel", ctx.now)
        o = self._require(ctx.conn, purchase_orders, req_uuid(p, "id"), "Заказ поставщику")
        self._check_version(o, ev, lambda x: self._order_view(ctx, x["id"]))
        reason = req_str(p, "reason", 2000)
        if o["status"] not in ("draft", "placed"):
            raise Rejected("order_state", f"Заказ № {o['order_no']} уже {pu.STATUS_LABELS_RU[o['status']].lower()}.")
        got = sum(self._received_by_line(ctx.conn, purchase_receipts.c.order_id == o["id"]).values(), Decimal(0))
        if got > 0:
            raise Rejected("order_has_receipts", f"По заказу № {o['order_no']} уже есть поставки — отменить его нельзя. "
                           "Закройте заказ (больше не ждём) или сторнируйте ошибочные приходы.")
        n = self._update_versioned(ctx, purchase_orders, o, {"status": "cancelled", "close_reason": reason,
                                                             "closed_by": ctx.user_id, "closed_at": ctx.now,
                                                             "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("purchase_order", o["id"], auth, o["version"], n["version"],
                {"order_no": o["order_no"], "status": "cancelled", "from": o["status"], "reason": reason})
        return {"purchase_order": self._order_view(ctx, o["id"])}

    def _po_close(self, ctx, p, ev):
        """po.close {id, reason?} + версия — закрыть размещённый заказ: больше по нему не ждём. Если поставлено не
        всё — причина обязательна (недопоставка). Поставки после закрытия не принимаются. Только онлайн.
        """
        only_keys(p, {"id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "po.close", ctx.now)
        o = self._require(ctx.conn, purchase_orders, req_uuid(p, "id"), "Заказ поставщику")
        self._check_version(o, ev, lambda x: self._order_view(ctx, x["id"]))
        if o["status"] != "placed":
            raise Rejected("order_state", f"Закрыть можно только размещённый заказ (№ {o['order_no']} — "
                           f"{pu.STATUS_LABELS_RU[o['status']].lower()}).")
        got = self._received_by_line(ctx.conn, purchase_receipts.c.order_id == o["id"])
        state = pu.delivery_state([(r["qty"], got.get((o["id"], r["line_id"]), Decimal(0))) for r in self._order_lines(ctx.conn, o["id"])])
        reason = opt_str(p, "reason", 2000)
        if state != "full" and not reason:
            raise Rejected("field_required", "Поставлено не всё: укажите причину закрытия (недопоставка, отказ поставщика …).",
                           {"field": "reason"})
        n = self._update_versioned(ctx, purchase_orders, o, {"status": "closed", "close_reason": reason,
                                                             "closed_by": ctx.user_id, "closed_at": ctx.now,
                                                             "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("purchase_order", o["id"], auth, o["version"], n["version"],
                {"order_no": o["order_no"], "status": "closed", "delivery": state, "reason": reason})
        return {"purchase_order": self._order_view(ctx, o["id"])}

    # ------------------------------------------------------------ факт поставки
    def _po_receive(self, ctx, p, ev):
        """po.receive {id, order_id, line_id, warehouse_id, qty, source_doc, reason?} — приход на склад по строке
        размещённого заказа (факт поставки). Только склад, только онлайн.

        Не больше, чем ещё ждём по строке (заказано − поставлено); основание (накладная) обязательно. Движение —
        обычный приход журнала (его можно сторнировать штатно — тогда «поставлено» уменьшится).
        """
        only_keys(p, {"id", "order_id", "line_id", "warehouse_id", "qty", "source_doc", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "po.receive", ctx.now)
        mid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, stock_movements, mid)
        o = self._require(ctx.conn, purchase_orders, req_uuid(p, "order_id"), "Заказ поставщику")
        if o["status"] != "placed":
            raise Rejected("order_state", f"Заказ № {o['order_no']} — {pu.STATUS_LABELS_RU[o['status']].lower()}: поставку по "
                           "нему принять нельзя. Если товар всё же пришёл — обычный приход с основанием.")
        lid = req_uuid(p, "line_id")
        line = next((r for r in self._order_lines(ctx.conn, o["id"]) if r["line_id"] == lid), None)
        if line is None:
            raise Rejected("not_found", "Строка заказа не найдена.", {"field": "line_id"})
        w = self._require(ctx.conn, warehouses, req_uuid(p, "warehouse_id"), "Склад")
        it = self._get(ctx.conn, stock_items, line["item_id"])
        if not w["active"]:
            raise Rejected("warehouse_inactive", f"Склад «{w['code']}» отключён: приход на него невозможен.", {"field": "warehouse_id"})
        if not it["active"]:
            raise Rejected("item_inactive", f"Позиция «{it['code']}» отключена: приход по ней невозможен.")
        qty = _qty(p)
        src = req_str(p, "source_doc", 200)
        got = self._received_by_line(ctx.conn, sa.and_(purchase_receipts.c.order_id == o["id"],
                                                       purchase_receipts.c.line_id == lid)).get((o["id"], lid), Decimal(0))
        left = pu.remaining(line["qty"], got)
        if qty > left:
            raise Rejected("exceeds_ordered", f"По строке заказа ждём ещё {money.to_json(left)} {it['unit']} — принять "
                           f"{money.to_json(qty)} нельзя. Излишек оформите обычным приходом с основанием.",
                           {"field": "qty", "remaining": money.to_json(left)})
        row, b = self._load_balance(ctx.conn, w["id"], it["id"])
        try:
            nb = stock.receive(b, qty)
        except stock.StockError as e:
            raise _stock_rejected(e)
        self._save_balance(ctx, w["id"], it["id"], row, nb)
        mv = self._post(ctx, auth, mid=mid, kind="receipt", w=w, it=it, qty=qty, source_doc=src,
                        reason=opt_str(p, "reason", 2000),
                        details={"purchase_order_no": o["order_no"], "order_id": o["id"], "line_id": lid,
                                 "balance_after": nb.to_json()})
        ctx.conn.execute(purchase_receipts.insert().values(movement_id=mid, order_id=o["id"], line_id=lid, qty=qty))
        n = self._update_versioned(ctx, purchase_orders, o, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("purchase_order", o["id"], auth, o["version"], n["version"],
                {"order_no": o["order_no"], "received": money.to_json(qty), "line_id": lid, "movement_id": mid,
                 "warehouse_code": w["code"], "item_code": it["code"], "unit": it["unit"], "source_doc": src})
        return {"movement": mv, "balance": {"warehouse_id": w["id"], "item_id": it["id"], **nb.to_json()},
                "purchase_order": self._order_view(ctx, o["id"])}

    # ------------------------------------------------------------ для потребности и pull
    def _ordered_open_by_need_line(self, conn) -> dict[tuple[str, str], Decimal]:
        """«Заказано, ещё не поставлено» по строкам потребности: (need_id, line_id) → сумма остатков строк
        РАЗМЕЩЁННЫХ заказов, привязанных к строке потребности (черновики, закрытые и отменённые не считаются).
        """
        received = self._received_by_line(conn)
        out: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        q = (sa.select(purchase_order_lines).join(purchase_orders, purchase_orders.c.id == purchase_order_lines.c.order_id)
             .where(purchase_orders.c.status == "placed", purchase_order_lines.c.need_id.isnot(None)))
        for r in conn.execute(q):
            out[(r.need_id, r.need_line_id)] += pu.remaining(r.qty, received.get((r.order_id, r.line_id), Decimal(0)))
        return out

    def _pull_purchasing(self, conn, uid: str, now) -> dict:
        """Часть pull: поставщики (supplier.read) и заказы (po.read; цены — только с po.read_prices).

        Все черновики и размещённые заказы + последние PULL_CLOSED_ORDERS_LIMIT закрытых/отменённых.
        """
        out: dict[str, Any] = {"suppliers": [], "purchase_orders": []}
        if self.policy.can(conn, uid, "supplier.read", now):
            out["suppliers"] = [self.v_supplier(dict(r._mapping)) for r in conn.execute(sa.select(suppliers).order_by(suppliers.c.code))]
        if not self.policy.can(conn, uid, "po.read", now):
            return out
        prices = self.policy.can(conn, uid, "po.read_prices", now)
        rows = [dict(r._mapping) for r in conn.execute(
            sa.select(purchase_orders).where(purchase_orders.c.status.in_(("draft", "placed")))
            .order_by(purchase_orders.c.order_no.desc()))]
        rows += [dict(r._mapping) for r in conn.execute(
            sa.select(purchase_orders).where(purchase_orders.c.status.in_(("closed", "cancelled")))
            .order_by(purchase_orders.c.order_no.desc()).limit(PULL_CLOSED_ORDERS_LIMIT))]
        received = self._received_by_line(conn)
        items = {r.id: dict(r._mapping) for r in conn.execute(sa.select(stock_items))}
        sups = {r.id: dict(r._mapping) for r in conn.execute(sa.select(suppliers))}
        out["purchase_orders"] = [self.v_order(conn, o, prices, received, items, sups.get(o["supplier_id"])) for o in rows]
        return out
