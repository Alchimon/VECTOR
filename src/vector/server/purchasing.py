"""Заказы поставщикам и факт поставки (сценарий С4, шаг 5; решения D-32, D-33).

Модуль — примесь (mixin) к `Service`, как stock.py: транзакция SERIALIZABLE, идемпотентность по operation_id,
аудит и проверка версий — в service.py; здесь — только правила закупки.

    поставщики (справочник с реквизитами; правка офлайн через очередь — supplier.create / supplier.update)
    договоры поставщика (contract.create / contract.update; офлайн через очередь)

    заказ:  draft ──po.place──▶ placed ──po.close──▶ closed
              │                   │
              └──po.cancel──▶ cancelled ◀──po.cancel (пока по заказу ничего не принято)

    согласование черновика (D-33, обязательно перед размещением):
              ┌───────── po.withdraw (закупка) ─────────┐
              ▼                                         │
         (правится) ──po.submit──▶ на согласовании ──po.approve──▶ согласован ──po.place──▶ placed
              ▲                         │
              └──────── po.return ──────┘ (согласующий, с причиной)

    факт поставки: po.receive — приход на склад по строке размещённого заказа (обычное движение журнала +
                   неизменяемая связь purchase_receipts). «Поставлено» = сумма таких приходов без сторнированных;
                   «ждём» = заказано − поставлено. Состояние «частично / полностью» — расчёт, а не статус.

Главные правила (каждое закреплено тестом в tests/server/test_purchasing.py и test_purchasing_approval.py):

* черновик правит закупка, можно офлайн (очередь, версии); согласование, размещение, отмена, закрытие и приход —
  только онлайн;
* **согласование обязательно** (владелец, D-33): разместить можно только черновик, согласованный в ТЕКУЩЕМ виде —
  SHA-256 содержания (поставщик, валюта, договор, режим цен, строки со ставками НДС) совпадает с согласованным.
  Пока заказ на согласовании или согласован, его условия не меняются (только счёт, дата, примечание); чтобы
  изменить — отозвать (po.withdraw) и отправить заново. Согласует роль «Согласование закупок» (po_approver) и
  не тот, кто заказ создал или отправил на согласование (принцип «четырёх глаз», A-37);
* размещённый заказ не меняется: условия заморожены, их SHA-256 — в placed_hash (проверяется при показе);
* **НДС** (D-33, A-38): режим цен заказа (с НДС / без НДС) и ставка у каждой строки с ценой обязательны для
  согласования; НДС строки — с округлением до копеек, итоги — суммы строк (vector.core.purchasing.line_vat);
* **реквизиты и договоры** (D-33, A-39): реквизиты необязательны, но проверяются (контрольные цифры); договор в
  заказе необязателен, а если указан — того же поставщика и действует на дату согласования и размещения;
* принять больше, чем ещё ждём по строке, нельзя (излишек — обычный приход с основанием, A-36 подтверждено
  владельцем); принимает склад;
* строка, привязанная к строке потребности, — только той же позиции номенклатуры, что выбрана для строки
  потребности (иначе «заказано» в покрытии потребности было бы ложью);
* цены видят закупка, бухгалтерия, согласующий и руководитель отдела (po.read_prices, D-34); складу — количества без цен;
* ничего не придумывается: валюта и режим цен не подставляются, сумма строки не округляется (Q8), дата поставки —
  только для справки.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import consolidation as cs
from vector.core import money, purchasing as pu, stock
from vector.core.errors import Rejected

from .db import (approval_bases, procurement_allocation_receipts, procurement_allocations, procurement_batch_lines,
                 procurement_batches, procurement_need_lines, procurement_needs, purchase_order_approvals,
                 purchase_order_batch_lines, purchase_order_invoice_checks, purchase_order_invoices,
                 purchase_order_line_taxes, purchase_order_lines, purchase_order_terms, purchase_orders,
                 purchase_receipts, stock_item_packs, stock_items, stock_movements, stock_reservations,
                 supplier_contracts, supplier_requisites, suppliers, warehouses)
from .stock import _code, _qty, _stock_rejected
from .validation import iso, only_keys, opt_date, opt_str, req_str, req_uuid

# Сколько последних закрытых/отменённых заказов отдаётся в pull (все черновики и размещённые — всегда).
PULL_CLOSED_ORDERS_LIMIT = 300

# Поля, которые после отправки на согласование (и после размещения) не меняются: это «условия» заказа, их хэш
# согласуют и замораживают. Справочные поля (счёт поставщика, ожидаемая дата, примечание) меняются всегда.
# С D-34 к условиям относятся и суммы по счёту поставщика: сверка с ними — часть того, что согласуют.
FROZEN_FIELDS = {"supplier_id", "currency", "lines", "contract_id", "vat_mode", "invoice_gross", "invoice_vat"}
ORDER_KEYS = {"id", "supplier_id", "currency", "supplier_ref", "expected_on", "note", "lines", "contract_id", "vat_mode",
              "invoice_gross", "invoice_vat"}
# Пустые условия заказа (нет строк-расширений v13/v14).
NO_TERMS = {"contract_id": None, "vat_mode": None, "invoice_gross": None, "invoice_vat": None}
SUPPLIER_KEYS = {"name", "inn", "contact", "note", "vat_rate", *pu.REQUISITE_KEYS}


def _purchase_rejected(e: pu.PurchaseError) -> Rejected:
    """Замечания к заказу из vector.core.purchasing → отказ с первым кодом и полным списком problems."""
    return Rejected(e.code, e.message_ru, {"problems": e.problems, "field": e.problems[0].get("field")})


def _vat_rate(p: dict, key: str = "vat_rate") -> Optional[str]:
    """Код ставки НДС из запроса (VAT_RATES) или None; иначе отказ."""
    v = p.get(key)
    if v in (None, ""):
        return None
    if v not in pu.VAT_RATES:
        raise Rejected("invalid_vat_rate", "Ставка НДС — одна из: " + ", ".join(pu.VAT_RATE_LABELS_RU.values()) + ".",
                       {"field": key})
    return v


def _d(v) -> Optional[str]:
    """Дата → «ГГГГ-ММ-ДД» для JSON (None остаётся None)."""
    return v.isoformat() if v else None


class PurchasingMixin:
    """Обработчики supplier.* / contract.* / po.* и данные закупки для pull."""

    # ------------------------------------------------------------ чтение расширений (v13)
    def _requisites(self, conn, sid: str) -> dict:
        """Реквизиты поставщика (пустой словарь — не заполнены)."""
        r = self._get(conn, supplier_requisites, sid, "supplier_id")
        return {k: v for k, v in r.items() if k != "supplier_id"} if r else {}

    def _terms(self, conn, oid: str) -> dict:
        """Условия заказа сверх v12: договор и режим цен (v13), суммы по счёту поставщика (v14); None — не указаны."""
        r = self._get(conn, purchase_order_terms, oid, "order_id")
        inv = self._get(conn, purchase_order_invoices, oid, "order_id")
        return {"contract_id": r["contract_id"] if r else None, "vat_mode": r["vat_mode"] if r else None,
                "invoice_gross": inv["invoice_gross"] if inv else None, "invoice_vat": inv["invoice_vat"] if inv else None}

    @staticmethod
    def _hash(supplier_id: str, currency: Optional[str], lines: list[pu.Line], terms: dict) -> str:
        """SHA-256 содержания заказа: поставщик, валюта, строки и условия (договор, режим цен, суммы по счёту)."""
        return pu.content_hash(supplier_id, currency, lines, terms["contract_id"], terms["vat_mode"],
                               terms.get("invoice_gross"), terms.get("invoice_vat"))

    def _line_taxes(self, conn, oid: str) -> dict[str, str]:
        """Ставки НДС строк заказа: line_id → код ставки."""
        return {r.line_id: r.vat_rate for r in conn.execute(
            sa.select(purchase_order_line_taxes).where(purchase_order_line_taxes.c.order_id == oid))}

    def _approvals(self, conn, oid: str) -> list[dict]:
        """События согласования заказа по порядку — с основанием решения и результатом сверки со счётом (D-34)."""
        events = {oid: [dict(r._mapping) for r in conn.execute(
            sa.select(purchase_order_approvals).where(purchase_order_approvals.c.order_id == oid)
            .order_by(purchase_order_approvals.c.seq))]}
        self._attach_bases(conn, events, oid)
        return events[oid]

    @staticmethod
    def _attach_bases(conn, events: dict[str, list], oid: Optional[str] = None) -> None:
        """Дополнить события согласования (order_id → [события]) основанием решения (approval_bases: своя роль,
        роль руководителя отдела или замещение) и результатом сверки со счётом при отправке (invoice_check).
        oid — только для одного заказа (иначе — все). У событий до D-34 этих сведений нет (None).
        """
        qb = sa.select(approval_bases).where(approval_bases.c.object_type == "purchase_order")
        qc = sa.select(purchase_order_invoice_checks)
        if oid is not None:
            qb = qb.where(approval_bases.c.object_id == oid)
            qc = qc.where(purchase_order_invoice_checks.c.order_id == oid)
        bases = {(r.object_id, r.seq): dict(r._mapping) for r in conn.execute(qb)}
        checks = {(r.order_id, r.seq): dict(r._mapping) for r in conn.execute(qc)}
        for order_id, evs in events.items():
            for e in evs:
                e["basis"] = bases.get((order_id, e["seq"]))
                e["invoice_check"] = checks.get((order_id, e["seq"]))

    @staticmethod
    def _approval_state(events: list[dict]) -> Optional[str]:
        """Состояние согласования — вид последнего события (submit / approve / return / withdraw) или None."""
        return events[-1]["kind"] if events else None

    # ------------------------------------------------------------ представления
    @staticmethod
    def v_supplier(o: dict, req: Optional[dict] = None) -> dict:
        """Поставщик для клиента: карточка + реквизиты (пустые — None) + признак «реквизиты заполнены»."""
        req = req or {}
        out = {k: o[k] for k in ("id", "code", "name", "inn", "contact", "note", "version")} | {"active": bool(o["active"])}
        out.update({k: req.get(k) for k in (*pu.REQUISITE_KEYS, "vat_rate")})
        # «Заполнены» — есть ИНН, банк и счёт; у организации ещё КПП. Это подсказка в интерфейсе, не запрет (A-39).
        out["requisites_complete"] = bool(o["inn"] and req.get("bik") and req.get("bank_account")
                                          and (len(o["inn"]) == 12 or req.get("kpp")))
        return out

    def _supplier_view(self, conn, o: dict) -> dict:
        """Поставщик с реквизитами из БД."""
        return self.v_supplier(o, self._requisites(conn, o["id"]))

    @staticmethod
    def v_contract(o: dict) -> dict:
        """Договор поставщика для клиента."""
        return {"id": o["id"], "supplier_id": o["supplier_id"], "number": o["number"], "signed_on": _d(o["signed_on"]),
                "valid_until": _d(o["valid_until"]), "subject": o["subject"], "note": o["note"],
                "active": bool(o["active"]), "version": o["version"]}

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
        return [{k: v for k, v in ln.to_json().items() if k != "amount"}
                for ln in self._core_lines(self._order_lines(conn, oid), self._line_taxes(conn, oid),
                                           self._batch_links(conn, oid))]

    @staticmethod
    def _core_lines(rows: list[dict], taxes: Optional[dict] = None, links: Optional[dict] = None) -> list[pu.Line]:
        """Строки из БД (+ ставки НДС и связи со строками пакетов) → проверенные строки ядра (для итога, НДС
        и контрольной суммы). Связь с пакетом входит в согласуемое и замораживаемое содержание (D-59)."""
        taxes = taxes or {}
        links = links or {}
        out = []
        for r in rows:
            amount = pu.exact_amount(r["qty"], r["unit_price"]) if r["unit_price"] is not None else None
            link = links.get(r["line_id"])
            out.append(pu.Line(r["line_id"], r["item_id"], r["qty"], r["unit_price"], amount, r["need_id"],
                               r["need_line_id"], r["note"], taxes.get(r["line_id"]),
                               link["batch_line_id"] if link else None))
        return out

    def _batch_links(self, conn, oid: str) -> dict:
        """Связи строк заказа со строками пакетов: line_id → строка purchase_order_batch_lines (D-59)."""
        return {r.line_id: dict(r._mapping) for r in conn.execute(
            sa.select(purchase_order_batch_lines).where(purchase_order_batch_lines.c.order_id == oid))}

    def _order_hash(self, conn, o: dict) -> str:
        """SHA-256 содержания заказа сейчас — для сверки с согласованным и замороженным (placed_hash)."""
        return self._hash(o["supplier_id"], o["currency"],
                          self._core_lines(self._order_lines(conn, o["id"]), self._line_taxes(conn, o["id"]),
                                           self._batch_links(conn, o["id"])),
                          self._terms(conn, o["id"]))

    def v_order(self, conn, o: dict, with_prices: bool, pre: Optional[dict] = None) -> dict:
        """Заказ для клиента: реквизиты, поставщик, договор, строки с НДС и «поставлено / ждём», итоги, согласование.

        with_prices=False — цены, суммы и НДС в деньгах не отдаются (склад видит количества). pre — заранее
        загруженные данные для pull (received, items, sups, terms, taxes, approvals, contracts), иначе читаются
        здесь. hash_ok — совпадают ли условия размещённого заказа с замороженными (False — изменены в обход
        программы); approval.hash_ok — совпадает ли содержание с согласованным.
        """
        pre = pre or {}
        rows = self._order_lines(conn, o["id"])
        received = pre.get("received")
        if received is None:
            received = self._received_by_line(conn, purchase_receipts.c.order_id == o["id"])
        items = pre.get("items")
        if items is None:
            ids = {r["item_id"] for r in rows}
            items = {r.id: dict(r._mapping) for r in conn.execute(sa.select(stock_items).where(stock_items.c.id.in_(ids)))} if ids else {}
        sup = (pre["sups"].get(o["supplier_id"]) if "sups" in pre else self._get(conn, suppliers, o["supplier_id"])) or {}
        terms = pre["terms"].get(o["id"], NO_TERMS) if "terms" in pre else self._terms(conn, o["id"])
        taxes = pre["taxes"].get(o["id"], {}) if "taxes" in pre else self._line_taxes(conn, o["id"])
        events = pre["approvals"].get(o["id"], []) if "approvals" in pre else self._approvals(conn, o["id"])
        if "links" in pre:
            links = pre["links"].get(o["id"], {})
        else:
            links = self._batch_links(conn, o["id"])
        bnos = pre.get("batch_nos", {})
        missing = {l["batch_id"] for l in links.values()} - set(bnos)
        if missing:
            bnos = {**bnos, **{r.id: r.batch_no for r in conn.execute(
                sa.select(procurement_batches).where(procurement_batches.c.id.in_(missing)))}}
        cid = terms["contract_id"]
        contract = ((pre["contracts"].get(cid) if "contracts" in pre else self._get(conn, supplier_contracts, cid))
                    if cid else None)
        mode = terms["vat_mode"]
        core = self._core_lines(rows, taxes, links)
        lines, pairs = [], []
        for r, c in zip(rows, core):
            got = received.get((o["id"], r["line_id"]), Decimal(0))
            pairs.append((r["qty"], got))
            it = items.get(r["item_id"], {})
            link = links.get(r["line_id"])
            v = pu.line_vat(c.amount, c.vat_rate, mode) if with_prices else None
            lines.append({"line_id": r["line_id"], "pos": r["pos"], "item_id": r["item_id"], "item_code": it.get("code"),
                          "item_name": it.get("name"), "unit": it.get("unit"), "qty": money.to_json(r["qty"]),
                          "unit_price": money.to_json(r["unit_price"]) if with_prices else None,
                          "amount": money.to_json(c.amount) if with_prices else None,
                          "vat_rate": c.vat_rate,
                          "net": money.to_json(v[0]) if v else None, "vat": money.to_json(v[1]) if v else None,
                          "gross": money.to_json(v[2]) if v else None,
                          "need_id": r["need_id"], "need_line_id": r["need_line_id"],
                          "batch_id": link["batch_id"] if link else None,
                          "batch_no": bnos.get(link["batch_id"]) if link else None,
                          "batch_line_id": link["batch_line_id"] if link else None, "note": r["note"],
                          "received": money.to_json(got), "remaining": money.to_json(pu.remaining(r["qty"], got))})
        delivery = pu.delivery_state(pairs) if o["status"] in ("placed", "closed") else None
        t = pu.totals(core)
        h_now = self._hash(o["supplier_id"], o["currency"], core, terms)
        state = self._approval_state(events)
        last_approve = next((e for e in reversed(events) if e["kind"] == "approve"), None)
        last_submit = next((e for e in reversed(events) if e["kind"] == "submit"), None)
        return {"id": o["id"], "order_no": o["order_no"], "supplier_id": o["supplier_id"],
                "supplier_code": sup.get("code"), "supplier_name": sup.get("name"), "status": o["status"],
                "status_label": pu.STATUS_LABELS_RU[o["status"]], "delivery": delivery,
                "delivery_label": pu.DELIVERY_LABELS_RU[delivery] if delivery else None,
                "currency": o["currency"], "supplier_ref": o["supplier_ref"],
                "expected_on": _d(o["expected_on"]), "note": o["note"],
                "contract_id": cid, "contract_number": contract["number"] if contract else None,
                "contract_signed_on": _d(contract["signed_on"]) if contract else None,
                "contract_valid_until": _d(contract["valid_until"]) if contract else None,
                "vat_mode": mode, "vat_mode_label": pu.VAT_MODES.get(mode) if mode else None,
                "prices_visible": with_prices,
                "total": t["total"] if with_prices else None, "unpriced_count": t["unpriced_count"] if with_prices else None,
                "vat_totals": pu.vat_totals(core, mode) if with_prices else None,
                # Сверка со счётом и «Контроль расчёта» (D-34) — только тем, кто видит цены.
                "invoice_gross": money.to_json(terms["invoice_gross"]) if with_prices else None,
                "invoice_vat": money.to_json(terms["invoice_vat"]) if with_prices else None,
                "calc_control": (pu.calc_control(core, mode, terms["invoice_gross"], terms["invoice_vat"])
                                 if with_prices else None),
                "hash_ok": (o["placed_hash"] == h_now) if o["placed_hash"] else None,
                "approval": {"state": state, "label": pu.APPROVAL_LABELS_RU[state],
                             # Совпадает ли содержание с последним согласованным (для «Разместить»).
                             "hash_ok": (last_approve["content_hash"] == h_now) if last_approve and state == "approve" else None,
                             "submitted_by": last_submit["actor_id"] if last_submit else None,
                             "events": [self._v_event(e, with_prices) for e in events]},
                "placed_by": o["placed_by"], "placed_at": iso(o["placed_at"]) if o["placed_at"] else None,
                "closed_by": o["closed_by"], "closed_at": iso(o["closed_at"]) if o["closed_at"] else None,
                "close_reason": o["close_reason"], "version": o["version"], "created_by": o["created_by"],
                "created_at": iso(o["created_at"]), "lines": lines}

    @staticmethod
    def _v_event(e: dict, with_prices: bool) -> dict:
        """Событие согласования для клиента: кто, когда, комментарий; с D-34 — чьим правом (роль, замещение — за
        кого) и сверка со счётом при отправке (состояние и причина; разницы в деньгах — только тем, кто видит цены).
        """
        b, c = e.get("basis"), e.get("invoice_check")
        return {"seq": e["seq"], "kind": e["kind"], "label": pu.APPROVAL_LABELS_RU[e["kind"]], "actor_id": e["actor_id"],
                "at": iso(e["at"]), "comment": e["comment"],
                "role": b["role"] if b else None, "on_behalf_of": b["on_behalf_of"] if b else None,
                "delegation_id": b["delegation_id"] if b else None,
                "invoice_state": c["state"] if c else None, "invoice_reason": c["reason"] if c else None,
                "diff_gross": money.to_json(c["diff_gross"]) if c and with_prices else None,
                "diff_vat": money.to_json(c["diff_vat"]) if c and with_prices else None}

    def _order_view(self, ctx, oid: str) -> dict:
        """Заказ oid глазами текущего пользователя (с ценами — если у него есть право их видеть)."""
        prices = self.policy.can(ctx.conn, ctx.user_id, "po.read_prices", ctx.now)
        return self.v_order(ctx.conn, self._get(ctx.conn, purchase_orders, oid), prices)

    # ------------------------------------------------------------ поставщики
    def _supplier_fields(self, p: dict, partial: bool, current_inn: Optional[str] = None,
                         current_req: Optional[dict] = None) -> tuple[dict, dict]:
        """Поля поставщика из запроса → (поля карточки, реквизиты). partial — только присланные (для update).

        Реквизиты проверяются вместе (ключ счёта — от БИК, КПП/ОГРН — от вида ИНН), поэтому при частичной правке
        присланные значения накладываются на текущие и проверяется итог.
        """
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
        merged = {**(current_req or {}), **{k: p.get(k) for k in pu.REQUISITE_KEYS if not partial or k in p}}
        try:
            req = pu.check_requisites(merged, out["inn"] if "inn" in out else current_inn)
        except pu.PurchaseError as e:
            raise _purchase_rejected(e)
        req["vat_rate"] = _vat_rate(p) if (not partial or "vat_rate" in p) else (current_req or {}).get("vat_rate")
        return out, req

    def _save_requisites(self, ctx, sid: str, req: dict) -> None:
        """Записать реквизиты поставщика (вставка или замена строки-расширения)."""
        ctx.conn.execute(supplier_requisites.delete().where(supplier_requisites.c.supplier_id == sid))
        if any(v is not None for v in req.values()):
            ctx.conn.execute(supplier_requisites.insert().values(supplier_id=sid, **req))

    def _supplier_create(self, ctx, p, ev):
        """supplier.create {id, code, name, inn?, contact?, note?, kpp?, ogrn?, address?, bank_name?, bik?,
        bank_account?, corr_account?, vat_rate?} — новый поставщик (закупка, A-34). Можно офлайн.
        """
        only_keys(p, {"id", "code", *SUPPLIER_KEYS})
        auth = self.policy.check(ctx.conn, ctx.user_id, "supplier.create", ctx.now)
        sid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, suppliers, sid)
        code = _code(p)
        if self._get(ctx.conn, suppliers, code, "code"):
            raise Rejected("code_exists", f"Поставщик с кодом «{code}» уже есть.", {"field": "code"})
        fields, req = self._supplier_fields(p, partial=False)
        row = {"id": sid, "code": code, **fields, "active": True, "version": 1,
               "created_by": ctx.user_id, "created_at": ctx.now, "updated_by": ctx.user_id, "updated_at": ctx.now}
        ctx.conn.execute(suppliers.insert().values(**row))
        self._save_requisites(ctx, sid, req)
        ctx.log("supplier", sid, auth, None, 1, {"code": code, "name": row["name"], "inn": row["inn"],
                                                 **{k: v for k, v in req.items() if v is not None}})
        return {"supplier": self._supplier_view(ctx.conn, self._get(ctx.conn, suppliers, sid))}

    def _supplier_update(self, ctx, p, ev):
        """supplier.update {id, name?, inn?, contact?, note?, active?, реквизиты?} + версия. Код не меняется;
        отключённому поставщику нельзя размещать и отправлять на согласование новые заказы (размещённые остаются).
        """
        only_keys(p, {"id", "active", *SUPPLIER_KEYS})
        auth = self.policy.check(ctx.conn, ctx.user_id, "supplier.update", ctx.now)
        o = self._require(ctx.conn, suppliers, req_uuid(p, "id"), "Поставщик")
        self._check_version(o, ev, lambda x: self._supplier_view(ctx.conn, x))
        cur_req = self._requisites(ctx.conn, o["id"])
        ch, req = self._supplier_fields(p, partial=True, current_inn=o["inn"],
                                        current_req={k: cur_req.get(k) for k in pu.REQUISITE_KEYS + ("vat_rate",)})
        if "active" in p:
            if not isinstance(p["active"], bool):
                raise Rejected("field_type", "Поле «active» должно быть true или false.", {"field": "active"})
            ch["active"] = p["active"]
        ch = {k: v for k, v in ch.items() if v != (bool(o[k]) if k == "active" else o[k])}
        req_ch = {k: v for k, v in req.items() if v != cur_req.get(k)}
        if not ch and not req_ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {**{k: (bool(o[k]) if k == "active" else o[k]) for k in ch}, **{k: cur_req.get(k) for k in req_ch}}
        n = self._update_versioned(ctx, suppliers, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        if req_ch:
            self._save_requisites(ctx, o["id"], req)
        ctx.log("supplier", o["id"], auth, o["version"], n["version"], {"before": before, "after": {**ch, **req_ch}})
        return {"supplier": self._supplier_view(ctx.conn, n)}

    # ------------------------------------------------------------ договоры поставщика
    def _contract_dates(self, p: dict, cur: Optional[dict] = None) -> tuple:
        """Дата заключения и срок действия (присланные поверх текущих); срок не раньше заключения."""
        signed = opt_date(p, "signed_on") if (cur is None or "signed_on" in p) else cur["signed_on"]
        if signed is None:
            raise Rejected("field_required", "Укажите дату заключения договора.", {"field": "signed_on"})
        until = opt_date(p, "valid_until") if (cur is None or "valid_until" in p) else cur["valid_until"]
        if until is not None and until < signed:
            raise Rejected("invalid_period", "Срок действия договора не может закончиться раньше даты заключения.",
                           {"field": "valid_until"})
        return signed, until

    def _contract_create(self, ctx, p, ev):
        """contract.create {id, supplier_id, number, signed_on, valid_until?, subject?, note?} — договор с
        поставщиком (закупка, A-39). Номер уникален у поставщика. Можно офлайн.
        """
        only_keys(p, {"id", "supplier_id", "number", "signed_on", "valid_until", "subject", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "contract.create", ctx.now)
        cid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, supplier_contracts, cid)
        sup = self._require(ctx.conn, suppliers, req_uuid(p, "supplier_id"), "Поставщик")
        number = req_str(p, "number", 100)
        if ctx.conn.execute(sa.select(supplier_contracts.c.id).where(
                supplier_contracts.c.supplier_id == sup["id"], supplier_contracts.c.number == number)).first():
            raise Rejected("contract_exists", f"У поставщика «{sup['code']}» уже есть договор № {number}.", {"field": "number"})
        signed, until = self._contract_dates(p)
        row = {"id": cid, "supplier_id": sup["id"], "number": number, "signed_on": signed, "valid_until": until,
               "subject": opt_str(p, "subject", 500), "note": opt_str(p, "note", 1000), "active": True, "version": 1,
               "created_by": ctx.user_id, "created_at": ctx.now, "updated_by": ctx.user_id, "updated_at": ctx.now}
        ctx.conn.execute(supplier_contracts.insert().values(**row))
        ctx.log("supplier_contract", cid, auth, None, 1, {"supplier_code": sup["code"], "number": number,
                                                          "signed_on": _d(signed), "valid_until": _d(until)})
        return {"supplier_contract": self.v_contract(self._get(ctx.conn, supplier_contracts, cid))}

    def _contract_update(self, ctx, p, ev):
        """contract.update {id, number?, signed_on?, valid_until?, subject?, note?, active?} + версия. Поставщик
        договора не меняется. Отключённый договор нельзя указать в новом заказе; заказы, уже размещённые по нему,
        остаются в силе.
        """
        only_keys(p, {"id", "number", "signed_on", "valid_until", "subject", "note", "active"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "contract.update", ctx.now)
        o = self._require(ctx.conn, supplier_contracts, req_uuid(p, "id"), "Договор")
        self._check_version(o, ev, self.v_contract)
        ch: dict[str, Any] = {}
        if "number" in p:
            ch["number"] = req_str(p, "number", 100)
            dup = ctx.conn.execute(sa.select(supplier_contracts.c.id).where(
                supplier_contracts.c.supplier_id == o["supplier_id"], supplier_contracts.c.number == ch["number"],
                supplier_contracts.c.id != o["id"])).first()
            if dup:
                raise Rejected("contract_exists", f"У поставщика уже есть договор № {ch['number']}.", {"field": "number"})
        if "signed_on" in p or "valid_until" in p:
            ch["signed_on"], ch["valid_until"] = self._contract_dates(p, o)
        for k, n in (("subject", 500), ("note", 1000)):
            if k in p:
                ch[k] = opt_str(p, k, n)
        if "active" in p:
            if not isinstance(p["active"], bool):
                raise Rejected("field_type", "Поле «active» должно быть true или false.", {"field": "active"})
            ch["active"] = p["active"]
        ch = {k: v for k, v in ch.items() if v != (bool(o[k]) if k == "active" else o[k])}
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        enc = lambda v: v.isoformat() if hasattr(v, "isoformat") else v  # noqa: E731 — даты в журнал аудита строкой
        n = self._update_versioned(ctx, supplier_contracts, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("supplier_contract", o["id"], auth, o["version"], n["version"],
                {"before": {k: enc(o[k]) for k in ch}, "after": {k: enc(v) for k, v in ch.items()}})
        return {"supplier_contract": self.v_contract(n)}

    def _contract_ref(self, ctx, cid: Optional[str], supplier_id: str, for_use: bool) -> Optional[dict]:
        """Договор заказа: того же поставщика; for_use (согласование, размещение) — действующий на сегодня (UTC)."""
        if cid is None:
            return None
        c = self._require(ctx.conn, supplier_contracts, cid, "Договор")
        if c["supplier_id"] != supplier_id:
            raise Rejected("contract_supplier_mismatch", f"Договор № {c['number']} заключён с другим поставщиком.",
                           {"field": "contract_id"})
        if for_use:
            today = ctx.now.date()
            if not c["active"]:
                raise Rejected("contract_inactive", f"Договор № {c['number']} отключён — заказ по нему не оформить.",
                               {"field": "contract_id"})
            if c["signed_on"] > today or (c["valid_until"] is not None and c["valid_until"] < today):
                period = f"с {c['signed_on']:%d.%m.%Y}" + (f" по {c['valid_until']:%d.%m.%Y}" if c["valid_until"] else "")
                raise Rejected("contract_not_valid", f"Договор № {c['number']} действует {period} — сегодня он не "
                               "действует. Выберите другой договор или продлите этот.", {"field": "contract_id"})
        return c

    # ------------------------------------------------------------ черновик заказа
    def _check_order_lines(self, ctx, lines: list[pu.Line], for_place: bool, order_id: Optional[str] = None) -> None:
        """Проверить ссылки строк по БД: позиции существуют (при согласовании/размещении — действующие); строка
        потребности существует и для неё выбрана та же позиция номенклатуры.
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
        self._check_batch_links(ctx, lines, for_place, order_id)

    def _check_batch_links(self, ctx, lines: list[pu.Line], for_place: bool,
                           order_id: Optional[str] = None) -> None:
        """Проверить связи строк со строками закупочных пакетов (D-59).

        Черновик и согласование (for_place=False): строка пакета существует и её позиция совпадает с позицией
        строки заказа. Размещение (онлайн): пакет сформирован; количество кратно упаковке позиции (если задана);
        а по каждой строке пакета «заказано всего» (размещённые заказы, КРОМЕ этого заказа, + поставленное +
        этот черновик) не выше распределённого количества, округлённого вверх до кратности, — излишек возможен
        только из кратности. Свой заказ исключён: повторная отправка на согласование после возврата не считается
        новым заказом.
        """
        linked = [(n, ln) for n, ln in enumerate(lines, start=1) if ln.batch_line_id]
        if not linked:
            return
        rows = {r.id: dict(r._mapping) for r in ctx.conn.execute(
            sa.select(procurement_batch_lines)
            .where(procurement_batch_lines.c.id.in_({ln.batch_line_id for _, ln in linked})))}
        batches = {r.id: dict(r._mapping) for r in ctx.conn.execute(
            sa.select(procurement_batches)
            .where(procurement_batches.c.id.in_({bl["batch_id"] for bl in rows.values()})))} if rows else {}
        packs = {r.item_id: r.pack_qty for r in ctx.conn.execute(sa.select(stock_item_packs))}
        for n, ln in linked:
            bl = rows.get(ln.batch_line_id)
            if bl is None:
                raise Rejected("not_found", f"Строка {n}: строка закупочного пакета не найдена.",
                               {"line": n, "field": "batch_line_id"})
            if bl["item_id"] != ln.item_id:
                raise Rejected("batch_item_mismatch", f"Строка {n}: позиция не совпадает с позицией строки пакета. "
                               "Строку пакета заказывают той позицией, что распределена в неё.",
                               {"line": n, "field": "item_id"})
            b = batches[bl["batch_id"]]
            if for_place and b["status"] != "consolidated":
                raise Rejected("batch_not_consolidated", f"Строка {n}: пакет № {b['batch_no']} — "
                               f"«{cs.batch_workflow(self.wf)['labels_ru'][b['status']]}». Заказывать из пакета можно, "
                               "когда он сформирован.", {"line": n, "field": "batch_line_id"})
            if for_place:
                pack = packs.get(ln.item_id)
                if not cs.is_pack_multiple(ln.qty, pack):
                    raise Rejected("pack_multiple", f"Строка {n}: позиция заказывается кратно "
                                   f"{money.to_json(pack)} — количество {money.to_json(ln.qty)} не подходит. "
                                   "Излишек от округления распределится по приходе: потребности — в резервы, "
                                   "остаток — в свободный складской остаток.",
                                   {"line": n, "field": "qty", "pack_qty": money.to_json(pack)})
        if not for_place:
            return
        mine: dict[str, Decimal] = defaultdict(Decimal)
        for _, ln in linked:
            mine[ln.batch_line_id] += ln.qty
        # Уже занято по строке пакета: строки РАЗМЕЩЁННЫХ заказов (кроме этого заказа — он пересогласовывается)
        # + поставленное.
        others: dict[str, Decimal] = defaultdict(Decimal)
        q = (sa.select(purchase_order_batch_lines.c.batch_line_id, purchase_order_lines.c.qty)
             .select_from(purchase_order_batch_lines
                          .join(purchase_order_lines,
                                sa.and_(purchase_order_lines.c.order_id == purchase_order_batch_lines.c.order_id,
                                        purchase_order_lines.c.line_id == purchase_order_batch_lines.c.line_id))
                          .join(purchase_orders, purchase_orders.c.id == purchase_order_batch_lines.c.order_id))
             .where(purchase_order_batch_lines.c.batch_line_id.in_(mine), purchase_orders.c.status == "placed",
                    purchase_orders.c.id != order_id))
        for r in ctx.conn.execute(q):
            others[r.batch_line_id] += r.qty
        for bl_id, want in mine.items():
            bl = rows[bl_id]
            pack = packs.get(bl["item_id"])
            allocated = ctx.conn.execute(sa.select(sa.func.coalesce(sa.func.sum(procurement_allocations.c.qty), 0))
                                         .where(procurement_allocations.c.batch_line_id == bl_id)).scalar()
            limit = cs.order_limit(Decimal(str(allocated)), pack)
            b = batches[bl["batch_id"]]
            delivered = self._batch_line_delivered(ctx.conn, bl_id)
            taken = others[bl_id] + delivered
            if want > limit - taken:
                raise Rejected("batch_order_exceeded",
                               f"Строка пакета № {b['batch_no']}: распределено {money.to_json(Decimal(str(allocated)))}, "
                               f"с учётом кратности заказать можно {money.to_json(limit)}; уже заказано и поставлено "
                               f"{money.to_json(taken)}, запрошено {money.to_json(want)}. Осталось заказать не больше "
                               f"{money.to_json(max(limit - taken, Decimal(0)))}.",
                               {"batch_line_id": bl_id, "limit": money.to_json(limit),
                                "taken": money.to_json(taken), "requested": money.to_json(want)})

    def _batch_line_delivered(self, conn, batch_line_id: str) -> Decimal:
        """Поставлено по строке пакета: сумма распределений приходов по её распределениям (без сторнированных)."""
        rev = stock_movements.alias("rev")
        return Decimal(str(conn.execute(
            sa.select(sa.func.coalesce(sa.func.sum(procurement_allocation_receipts.c.qty), 0))
            .select_from(procurement_allocation_receipts
                         .join(procurement_allocations,
                               procurement_allocations.c.id == procurement_allocation_receipts.c.allocation_id)
                         .outerjoin(rev, rev.c.reverses_id == procurement_allocation_receipts.c.movement_id))
            .where(procurement_allocations.c.batch_line_id == batch_line_id, rev.c.id.is_(None))).scalar()))

    def _write_lines(self, ctx, oid: str, lines: list[pu.Line]) -> None:
        """Заменить строки черновика целиком (порядок = порядок в запросе) вместе со ставками НДС и связями
        со строками закупочных пакетов (D-59)."""
        ctx.conn.execute(purchase_order_line_taxes.delete().where(purchase_order_line_taxes.c.order_id == oid))
        ctx.conn.execute(purchase_order_batch_lines.delete().where(purchase_order_batch_lines.c.order_id == oid))
        ctx.conn.execute(purchase_order_lines.delete().where(purchase_order_lines.c.order_id == oid))
        bl_ids = {ln.batch_line_id for ln in lines if ln.batch_line_id}
        bl_batch = {r.id: r.batch_id for r in ctx.conn.execute(
            sa.select(procurement_batch_lines).where(procurement_batch_lines.c.id.in_(bl_ids)))} if bl_ids else {}
        for pos, ln in enumerate(lines, start=1):
            ctx.conn.execute(purchase_order_lines.insert().values(
                order_id=oid, pos=pos, line_id=ln.line_id, item_id=ln.item_id, qty=ln.qty, unit_price=ln.unit_price,
                need_id=ln.need_id, need_line_id=ln.need_line_id, note=ln.note))
            if ln.batch_line_id:
                ctx.conn.execute(purchase_order_batch_lines.insert().values(
                    order_id=oid, line_id=ln.line_id, batch_id=bl_batch[ln.batch_line_id],
                    batch_line_id=ln.batch_line_id))
            if ln.vat_rate is not None:
                ctx.conn.execute(purchase_order_line_taxes.insert().values(order_id=oid, line_id=ln.line_id, vat_rate=ln.vat_rate))

    def _write_terms(self, ctx, oid: str, terms: dict) -> None:
        """Записать условия заказа: договор и режим цен (v13), суммы по счёту (v14); пустые — строк-расширений нет."""
        ctx.conn.execute(purchase_order_terms.delete().where(purchase_order_terms.c.order_id == oid))
        if terms["contract_id"] is not None or terms["vat_mode"] is not None:
            ctx.conn.execute(purchase_order_terms.insert().values(order_id=oid, contract_id=terms["contract_id"],
                                                                  vat_mode=terms["vat_mode"]))
        ctx.conn.execute(purchase_order_invoices.delete().where(purchase_order_invoices.c.order_id == oid))
        if terms.get("invoice_gross") is not None or terms.get("invoice_vat") is not None:
            ctx.conn.execute(purchase_order_invoices.insert().values(order_id=oid, invoice_gross=terms.get("invoice_gross"),
                                                                     invoice_vat=terms.get("invoice_vat")))

    @staticmethod
    def _invoice_amount(p: dict, key: str) -> Optional[Decimal]:
        """Сумма по счёту поставщика из запроса (invoice_gross / invoice_vat): None — не введена; иначе проверка."""
        try:
            return pu.check_invoice_amount(p.get(key), key)
        except pu.PurchaseError as e:
            raise _purchase_rejected(e)

    def _supplier_ref(self, ctx, sid: str, must_be_active: bool) -> dict:
        """Поставщик по id (для согласования и размещения — действующий) или отказ."""
        s = self._require(ctx.conn, suppliers, sid, "Поставщик")
        if must_be_active and not s["active"]:
            raise Rejected("supplier_inactive", f"Поставщик «{s['code']}» отключён — новые заказы ему не размещают.",
                           {"field": "supplier_id"})
        return s

    @staticmethod
    def _vat_mode(p: dict) -> Optional[str]:
        """Режим цен из запроса (included / excluded / None) или отказ."""
        try:
            return pu.check_vat_mode(p.get("vat_mode"))
        except pu.PurchaseError as e:
            raise _purchase_rejected(e)

    def _po_create(self, ctx, p, ev):
        """po.create {id, supplier_id, currency?, contract_id?, vat_mode?, invoice_gross?, invoice_vat?, supplier_ref?,
        expected_on?, note?, lines[]} — черновик заказа. Можно офлайн (очередь). Номер заказа — сквозной, назначает
        сервер при создании. invoice_* — суммы по счёту поставщика для сверки (D-34), в целых копейках.
        """
        only_keys(p, ORDER_KEYS)
        auth = self.policy.check(ctx.conn, ctx.user_id, "po.create", ctx.now)
        oid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, purchase_orders, oid)
        sup = self._supplier_ref(ctx, req_uuid(p, "supplier_id"), must_be_active=False)
        try:
            lines, cur = pu.parse_lines(p.get("lines", []), p.get("currency"))
        except pu.PurchaseError as e:
            raise _purchase_rejected(e)
        mode = self._vat_mode(p)
        cid = req_uuid(p, "contract_id") if p.get("contract_id") is not None else None
        self._contract_ref(ctx, cid, sup["id"], for_use=False)
        inv_g, inv_v = self._invoice_amount(p, "invoice_gross"), self._invoice_amount(p, "invoice_vat")
        self._check_order_lines(ctx, lines, for_place=False)
        no = (ctx.conn.execute(sa.select(sa.func.max(purchase_orders.c.order_no))).scalar() or 0) + 1
        ctx.conn.execute(purchase_orders.insert().values(
            id=oid, order_no=no, supplier_id=sup["id"], status="draft", currency=cur,
            supplier_ref=opt_str(p, "supplier_ref", 200), expected_on=opt_date(p, "expected_on"),
            note=opt_str(p, "note", 2000), version=1, created_by=ctx.user_id, created_at=ctx.now,
            updated_by=ctx.user_id, updated_at=ctx.now))
        self._write_lines(ctx, oid, lines)
        self._write_terms(ctx, oid, {"contract_id": cid, "vat_mode": mode, "invoice_gross": inv_g, "invoice_vat": inv_v})
        ctx.log("purchase_order", oid, auth, None, 1, {"order_no": no, "supplier_code": sup["code"], "status": "draft",
                                                       "lines": len(lines), "currency": cur, "vat_mode": mode,
                                                       "contract_id": cid, "invoice_gross": money.to_json(inv_g),
                                                       "invoice_vat": money.to_json(inv_v), **pu.totals(lines)})
        return {"purchase_order": self._order_view(ctx, oid)}

    def _po_update(self, ctx, p, ev):
        """po.update {id, …} + версия. Черновик — любые поля и строки целиком. Черновик на согласовании или
        согласованный, а также размещённый — только справочные поля (supplier_ref, expected_on, note): условия,
        которые согласуют и замораживают, не меняются (D-33, A-36). Закрытый/отменённый — ничего.
        """
        only_keys(p, ORDER_KEYS)
        auth = self.policy.check(ctx.conn, ctx.user_id, "po.update", ctx.now)
        o = self._require(ctx.conn, purchase_orders, req_uuid(p, "id"), "Заказ поставщику")
        self._check_version(o, ev, lambda x: self._order_view(ctx, x["id"]))
        if o["status"] in ("closed", "cancelled"):
            raise Rejected("order_state", f"Заказ № {o['order_no']} {pu.STATUS_LABELS_RU[o['status']].lower()} — менять его нельзя.")
        frozen = FROZEN_FIELDS & set(p)
        if o["status"] == "placed" and frozen:
            raise Rejected("order_placed", f"Заказ № {o['order_no']} уже размещён: поставщик, валюта, договор, НДС и "
                           "строки не меняются. Нужны другие условия — закройте или отмените заказ и разместите новый.",
                           {"field": sorted(frozen)[0]})
        state = self._approval_state(self._approvals(ctx.conn, o["id"]))
        if frozen and state in ("submit", "approve"):
            raise Rejected("order_on_approval", f"Заказ № {o['order_no']} {pu.APPROVAL_LABELS_RU[state].lower()}: его "
                           "условия не меняются. Чтобы изменить — отзовите его с согласования и отправьте заново.",
                           {"field": sorted(frozen)[0]})
        terms = self._terms(ctx.conn, o["id"])
        ch: dict[str, Any] = {}
        if "supplier_id" in p:
            ch["supplier_id"] = self._supplier_ref(ctx, req_uuid(p, "supplier_id"), must_be_active=False)["id"]
        if "supplier_ref" in p:
            ch["supplier_ref"] = opt_str(p, "supplier_ref", 200)
        if "expected_on" in p:
            ch["expected_on"] = opt_date(p, "expected_on")
        if "note" in p:
            ch["note"] = opt_str(p, "note", 2000)
        new_terms = dict(terms)
        if "vat_mode" in p:
            new_terms["vat_mode"] = self._vat_mode(p)
        if "contract_id" in p:
            new_terms["contract_id"] = req_uuid(p, "contract_id") if p["contract_id"] is not None else None
        for key in ("invoice_gross", "invoice_vat"):
            if key in p:
                new_terms[key] = self._invoice_amount(p, key)
        # Договор — того же поставщика (в том числе если сменили поставщика, а договор оставили прежним).
        self._contract_ref(ctx, new_terms["contract_id"], ch.get("supplier_id", o["supplier_id"]), for_use=False)
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
        terms_ch = {k: v for k, v in new_terms.items() if v != terms[k]}
        if not ch and not lines_changed and not terms_ch:
            raise Rejected("no_changes", "Нет изменений.")
        if terms_ch:
            self._write_terms(ctx, o["id"], new_terms)
        n = self._update_versioned(ctx, purchase_orders, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        # Даты и суммы — в журнал аудита строкой (Decimal → «12.5000»).
        enc = lambda v: v.isoformat() if hasattr(v, "isoformat") else (money.to_json(v) if isinstance(v, Decimal) else v)  # noqa: E731
        ctx.log("purchase_order", o["id"], auth, o["version"], n["version"],
                {"order_no": o["order_no"], "changed": sorted(ch) + sorted(terms_ch) + (["lines"] if lines_changed else []),
                 "before": {**{k: enc(o[k]) for k in ch}, **{k: enc(terms[k]) for k in terms_ch}},
                 "after": {**{k: enc(v) for k, v in ch.items()}, **{k: enc(v) for k, v in terms_ch.items()}}})
        return {"purchase_order": self._order_view(ctx, o["id"])}

    # ------------------------------------------------------------ согласование (D-33)
    def _check_ready(self, ctx, o: dict, for_place: bool = False) -> tuple[list[pu.Line], dict, dict, str]:
        """Полная проверка черновика перед согласованием и размещением: поставщик действующий, строки (хотя бы одна,
        позиции действующие, ссылки на потребность), валюта, режим цен и ставки НДС, договор действует сегодня.

        for_place=True (размещение): дополнительно проверяются связи с закупочными пакетами (пакет сформирован,
        кратность, лимит «заказано всего» по строке пакета, D-59). Возвращает (строки, поставщик, условия,
        SHA-256 содержания). Все замечания ядра — одним отказом (problems).
        """
        sup = self._supplier_ref(ctx, o["supplier_id"], must_be_active=True)
        try:
            lines, cur = pu.parse_lines(self._raw_lines(ctx.conn, o["id"]), o["currency"], require_lines=True)
        except pu.PurchaseError as e:
            raise _purchase_rejected(e)
        terms = self._terms(ctx.conn, o["id"])
        probs = pu.ready_problems(lines, cur, terms["vat_mode"])
        if probs:
            raise _purchase_rejected(pu.PurchaseError(probs))
        self._check_order_lines(ctx, lines, for_place=for_place, order_id=o["id"])
        self._contract_ref(ctx, terms["contract_id"], o["supplier_id"], for_use=True)
        return lines, sup, terms, self._hash(o["supplier_id"], cur, lines, terms)

    def _add_approval(self, ctx, o: dict, kind: str, content_hash: Optional[str], comment: Optional[str],
                      auth) -> int:
        """Добавить событие в неизменяемый журнал согласования заказа (seq — следующий номер) вместе с основанием
        решения (D-34: своя роль, роль руководителя отдела или замещение — за кого). Возвращает seq.
        """
        seq = (ctx.conn.execute(sa.select(sa.func.max(purchase_order_approvals.c.seq))
                                .where(purchase_order_approvals.c.order_id == o["id"])).scalar() or 0) + 1
        ctx.conn.execute(purchase_order_approvals.insert().values(order_id=o["id"], seq=seq, kind=kind,
                                                                  content_hash=content_hash, comment=comment,
                                                                  actor_id=ctx.user_id, at=ctx.now))
        self._record_basis(ctx, "purchase_order", o["id"], seq, auth)
        return seq

    def _draft_for_approval(self, ctx, p, ev, action: str) -> tuple[dict, Any, list[dict]]:
        """Общее начало команд согласования: право, заказ, версия, статус «черновик». → (заказ, основание, события)."""
        auth = self.policy.check(ctx.conn, ctx.user_id, action, ctx.now)
        o = self._require(ctx.conn, purchase_orders, req_uuid(p, "id"), "Заказ поставщику")
        self._check_version(o, ev, lambda x: self._order_view(ctx, x["id"]))
        if o["status"] != "draft":
            raise Rejected("order_state", f"Заказ № {o['order_no']} уже {pu.STATUS_LABELS_RU[o['status']].lower()} — "
                           "согласование касается только черновика.")
        return o, auth, self._approvals(ctx.conn, o["id"])

    def _po_submit(self, ctx, p, ev):
        """po.submit {id, comment?, invoice_reason?} + версия — отправить черновик на согласование (закупка, онлайн).

        Черновик проверяется так же полно, как при размещении; согласующему уходит именно это содержание (SHA-256).
        Сверка со счётом (D-34, A-38): если суммы по счёту не сходятся с расчётом или сверить нельзя (расчёт
        неполный) — нужна причина invoice_reason, иначе отказ invoice_reason_required (в details — сверка).
        Результат сверки и причина записываются неизменяемо и показываются согласующему.
        """
        only_keys(p, {"id", "comment", "invoice_reason"})
        o, auth, events = self._draft_for_approval(ctx, p, ev, "po.submit")
        state = self._approval_state(events)
        if state in ("submit", "approve"):
            raise Rejected("order_state", f"Заказ № {o['order_no']} уже {pu.APPROVAL_LABELS_RU[state].lower()}.")
        lines, sup, terms, h = self._check_ready(ctx, o)
        control = pu.calc_control(lines, terms["vat_mode"], terms["invoice_gross"], terms["invoice_vat"])
        inv = control["invoice"]
        reason = opt_str(p, "invoice_reason", 2000)
        if control["needs_reason"] and not reason:
            raise Rejected("invoice_reason_required",
                           pu.INVOICE_STATE_LABELS_RU[inv["state"]] + ". Проверьте цены и суммы по счёту; если всё "
                           "верно (например, поставщик считает НДС от итога) — укажите причину расхождения, её увидит "
                           "согласующий.", {"field": "invoice_reason", "invoice": inv})
        comment = opt_str(p, "comment", 2000)
        seq = self._add_approval(ctx, o, "submit", h, comment, auth)
        ctx.conn.execute(purchase_order_invoice_checks.insert().values(
            order_id=o["id"], seq=seq, state=inv["state"],
            diff_gross=Decimal(inv["diff_gross"]) if inv["diff_gross"] is not None else None,
            diff_vat=Decimal(inv["diff_vat"]) if inv["diff_vat"] is not None else None,
            reason=reason if control["needs_reason"] else None))
        n = self._update_versioned(ctx, purchase_orders, o, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("purchase_order", o["id"], auth, o["version"], n["version"],
                {"order_no": o["order_no"], "approval": "submit", "content_hash": h, "comment": comment,
                 "supplier_code": sup["code"], "lines": len(lines), "vat_mode": terms["vat_mode"],
                 "invoice_state": inv["state"], "invoice_reason": reason if control["needs_reason"] else None,
                 **{k: v for k, v in pu.vat_totals(lines, terms["vat_mode"]).items() if k in ("net", "vat", "gross")}})
        # Уведомить согласующих и руководителей отдела (A-37/A-40 → D-35). Денег в тексте нет.
        body = f"Поставщик: {sup['name']}. Позиций: {len(lines)}. Отправил(а): {self._actor_label(ctx, auth)}."
        if control["needs_reason"]:
            body += f" Расхождение со счётом поставщика, причина: {reason}."
        if comment:
            body += f" Комментарий: {comment}"
        self._notify(ctx, self._approvers_and_heads(ctx), "po_submitted",
                     f"Заказ № {o['order_no']} ждёт согласования", body, "purchase_order", o["id"])
        return {"purchase_order": self._order_view(ctx, o["id"])}

    def _po_withdraw(self, ctx, p, ev):
        """po.withdraw {id, reason?} + версия — отозвать заказ с согласования (или отказаться от согласования), чтобы
        изменить условия (закупка, онлайн). После правки — отправить на согласование заново.
        """
        only_keys(p, {"id", "reason"})
        o, auth, events = self._draft_for_approval(ctx, p, ev, "po.withdraw")
        state = self._approval_state(events)
        if state not in ("submit", "approve"):
            raise Rejected("order_state", f"Заказ № {o['order_no']} не на согласовании — отзывать нечего.")
        reason = opt_str(p, "reason", 2000)
        self._add_approval(ctx, o, "withdraw", None, reason, auth)
        n = self._update_versioned(ctx, purchase_orders, o, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("purchase_order", o["id"], auth, o["version"], n["version"],
                {"order_no": o["order_no"], "approval": "withdraw", "from": state, "reason": reason})
        self._notify(ctx, self._approvers_and_heads(ctx), "po_withdrawn",
                     f"Заказ № {o['order_no']} отозван с согласования",
                     f"Отозвал(а): {self._actor_label(ctx, auth)}." + (f" Причина: {reason}" if reason else ""),
                     "purchase_order", o["id"])
        return {"purchase_order": self._order_view(ctx, o["id"])}

    def _decision_checks(self, ctx, o: dict, events: list[dict]) -> dict:
        """Общие проверки решения согласующего: заказ на согласовании, решает не автор и не отправивший (A-37)."""
        state = self._approval_state(events)
        if state != "submit":
            raise Rejected("order_state", f"Заказ № {o['order_no']} не на согласовании "
                           f"({pu.APPROVAL_LABELS_RU[state].lower()}).")
        sub = events[-1]
        if ctx.user_id in (sub["actor_id"], o["created_by"]):
            raise Rejected("self_approval", "Нельзя согласовать заказ, который вы сами создали или отправили на "
                           "согласование: решение принимает другой сотрудник — согласующий закупки, его замещающий "
                           "или руководитель отдела.")
        return sub

    def _approvers_and_heads(self, ctx) -> list[str]:
        """Кому сообщать о заказе на согласовании (D-35): все, у кого сейчас есть роль «Согласование закупок» или
        «Руководитель отдела» (своя или по замещению). Автор действия отсеивается в _notify.
        """
        return (self._users_with_role(ctx.conn, "po_approver", ctx.now)
                + self._users_with_role(ctx.conn, "dept_head", ctx.now))

    def _notify_decision(self, ctx, o: dict, sub: dict, kind: str, title: str, body: str) -> None:
        """Уведомить о решении по заказу (согласован / возвращён): создателя, отправившего и руководителей отдела."""
        self._notify(ctx, [o["created_by"], sub["actor_id"]] + self._users_with_role(ctx.conn, "dept_head", ctx.now),
                     kind, title, body, "purchase_order", o["id"])

    def _po_approve(self, ctx, p, ev):
        """po.approve {id, comment?} + версия — согласовать заказ (роль «Согласование закупок», онлайн).

        Согласуется ровно отправленное содержание: если хэш изменился (правка в обход программы) — отказ.
        Перед решением проверяется и готовность (поставщик, договор и позиции могли отключить за это время).
        """
        only_keys(p, {"id", "comment"})
        o, auth, events = self._draft_for_approval(ctx, p, ev, "po.approve")
        sub = self._decision_checks(ctx, o, events)
        lines, sup, terms, h = self._check_ready(ctx, o)
        if h != sub["content_hash"]:
            raise Rejected("approval_stale", f"Содержание заказа № {o['order_no']} изменилось после отправки на "
                           "согласование — согласовать нельзя. Закупка должна отправить его заново.")
        comment = opt_str(p, "comment", 2000)
        self._add_approval(ctx, o, "approve", h, comment, auth)
        n = self._update_versioned(ctx, purchase_orders, o, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("purchase_order", o["id"], auth, o["version"], n["version"],
                {"order_no": o["order_no"], "approval": "approve", "content_hash": h, "comment": comment,
                 "submitted_by": sub["actor_id"], "on_behalf_of": auth.on_behalf_of})
        self._notify_decision(ctx, o, sub, "po_approved", f"Заказ № {o['order_no']} согласован",
                              f"Согласовал(а): {self._actor_label(ctx, auth)}."
                              + (f" Комментарий: {comment.rstrip('.')}." if comment else "")
                              + " Можно размещать у поставщика.")
        return {"purchase_order": self._order_view(ctx, o["id"])}

    def _po_return(self, ctx, p, ev):
        """po.return {id, reason} + версия — вернуть заказ на доработку (роль «Согласование закупок», онлайн).
        Причина обязательна: закупка увидит её в карточке заказа.
        """
        only_keys(p, {"id", "reason"})
        o, auth, events = self._draft_for_approval(ctx, p, ev, "po.return")
        self._decision_checks(ctx, o, events)
        reason = req_str(p, "reason", 2000)
        sub = events[-1]      # событие «отправлено»: его автор получит уведомление о возврате
        self._add_approval(ctx, o, "return", None, reason, auth)
        n = self._update_versioned(ctx, purchase_orders, o, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("purchase_order", o["id"], auth, o["version"], n["version"],
                {"order_no": o["order_no"], "approval": "return", "reason": reason})
        self._notify_decision(ctx, o, sub, "po_returned", f"Заказ № {o['order_no']} возвращён на доработку",
                              f"Вернул(а): {self._actor_label(ctx, auth)}. Причина: {reason}")
        return {"purchase_order": self._order_view(ctx, o["id"])}

    # ------------------------------------------------------------ размещение, отмена, закрытие
    def _po_place(self, ctx, p, ev):
        """po.place {id} + версия — разместить заказ у поставщика: черновик → «Размещён». Только онлайн.

        Нужен согласованный черновик (D-33), и содержание должно совпадать с согласованным. Всё проверяется заново:
        поставщик, договор, позиции, ссылки на потребность, валюта и НДС. Условия замораживаются (placed_hash).
        """
        only_keys(p, {"id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "po.place", ctx.now)
        o = self._require(ctx.conn, purchase_orders, req_uuid(p, "id"), "Заказ поставщику")
        self._check_version(o, ev, lambda x: self._order_view(ctx, x["id"]))
        if o["status"] != "draft":
            raise Rejected("order_state", f"Заказ № {o['order_no']} уже {pu.STATUS_LABELS_RU[o['status']].lower()} — "
                           "разместить можно только черновик.")
        events = self._approvals(ctx.conn, o["id"])
        if self._approval_state(events) != "approve":
            raise Rejected("approval_required", f"Заказ № {o['order_no']} не согласован "
                           f"({pu.APPROVAL_LABELS_RU[self._approval_state(events)].lower()}). Разместить можно только "
                           "согласованный заказ — отправьте его на согласование.")
        lines, sup, terms, h = self._check_ready(ctx, o, for_place=True)
        if h != events[-1]["content_hash"]:
            raise Rejected("approval_stale", f"Содержание заказа № {o['order_no']} отличается от согласованного — "
                           "разместить нельзя. Отправьте заказ на согласование заново.")
        n = self._update_versioned(ctx, purchase_orders, o, {"status": "placed", "placed_hash": h, "placed_by": ctx.user_id,
                                                             "placed_at": ctx.now, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("purchase_order", o["id"], auth, o["version"], n["version"],
                {"order_no": o["order_no"], "status": "placed", "supplier_code": sup["code"], "lines": len(lines),
                 "currency": o["currency"], "placed_hash": h, "approved_by": events[-1]["actor_id"], **pu.totals(lines)})
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
    def _plan_receipt_distribution(self, ctx, link: dict, it: dict, qty: Decimal,
                                   nb: stock.Balance) -> tuple[stock.Balance, list[dict], Decimal]:
        """Спланировать распределение прихода по строке заказа из пакета (D-59), ничего не записывая:
        потребная часть — в резервы проектов по распределениям строки пакета (в порядке строк потребностей),
        излишек (от кратности) остаётся свободным складским остатком. Возвращает (остаток после резервов,
        строки распределения, излишек).

        Если строка потребности к моменту прихода уже покрыта со склада (ручной резерв) или переведена на
        другую позицию, её часть прихода в резерв не идёт — остаётся свободной (излишек без дублей); поставлено
        по распределению учитывается в любом случае.
        """
        allocs = [dict(r._mapping) for r in ctx.conn.execute(
            sa.select(procurement_allocations)
            .where(procurement_allocations.c.batch_line_id == link["batch_line_id"]))]
        # Порядок распределения детерминирован: по позиции строки в потребности, затем по id распределения.
        pos = {(r.need_id, r.line_id): r.pos for r in ctx.conn.execute(
            sa.select(procurement_need_lines)
            .where(procurement_need_lines.c.need_id.in_({a["need_id"] for a in allocs})))} if allocs else {}
        delivered = self._delivered_by_allocation(ctx.conn, [a["id"] for a in allocs])
        allocs.sort(key=lambda a: (pos.get((a["need_id"], a["need_line_id"]), 0), a["id"]))
        plan, surplus = cs.distribute(qty, [(a["id"], a["qty"] - delivered.get(a["id"], Decimal(0))) for a in allocs])
        by_id = {a["id"]: a for a in allocs}
        dist: list[dict] = []
        for aid, attributed in plan:
            a = by_id[aid]
            need = self._get(ctx.conn, procurement_needs, a["need_id"])
            nl = self._get(ctx.conn, procurement_need_lines, a["need_line_id"], "line_id")
            # Действующая позиция строки (ручное решение) и её коэффициент — как в stock.reserve (A-25/A-27).
            m = self._line_matches(ctx.conn, a["need_id"]).get(a["need_line_id"])
            chosen = (m["item_id"] if m else nl["item_id"])
            factor = m["factor"] if m else Decimal(1)
            reserve_qty = Decimal(0)
            if chosen == it["id"]:
                open_qty = stock.convert_qty(nl["qty"], factor) - self._need_line_covered(
                    ctx.conn, a["need_id"], a["need_line_id"])
                reserve_qty = min(attributed, max(open_qty, Decimal(0)))
            if reserve_qty > 0:
                try:
                    nb = stock.reserve(nb, reserve_qty)
                except stock.StockError as e:
                    raise _stock_rejected(e)
            dist.append({"allocation_id": aid, "need_id": a["need_id"], "need_line_id": a["need_line_id"],
                         "project_id": need["project_id"], "qty": money.to_json(attributed),
                         "reserved": money.to_json(reserve_qty),
                         "reservation_id": str(uuid.uuid4()) if reserve_qty > 0 else None})
        return nb, dist, surplus

    def _apply_receipt_distribution(self, ctx, dist: list[dict], batch_id: str, mid: str, o: dict, w: dict,
                                    it: dict, auth) -> None:
        """Записать распланированное распределение прихода: резервы проектов и неизменяемые связи
        распределение↔приход; уведомить руководство затронутых проектов."""
        if not dist:
            return
        b = self._get(ctx.conn, procurement_batches, batch_id)
        projects: set[str] = set()
        for d in dist:
            reserved = Decimal(d["reserved"])
            if reserved > 0:
                ctx.conn.execute(stock_reservations.insert().values(
                    id=d["reservation_id"], warehouse_id=w["id"], item_id=it["id"], project_id=d["project_id"],
                    need_id=d["need_id"], need_line_id=d["need_line_id"], qty=reserved, consumed=Decimal(0),
                    status="active", reason=f"Приход по заказу № {o['order_no']} (пакет № {b['batch_no']})",
                    close_reason=None, version=1, created_by=ctx.user_id, created_at=ctx.now,
                    updated_by=ctx.user_id, updated_at=ctx.now))
                ctx.log("stock_reservation", d["reservation_id"], auth, None, 1,
                        {"warehouse_code": w["code"], "item_code": it["code"], "qty": d["reserved"],
                         "need_id": d["need_id"], "need_line_id": d["need_line_id"], "movement_id": mid,
                         "reason": f"приход по заказу № {o['order_no']} (пакет № {b['batch_no']})"})
                projects.add(d["project_id"])
            ctx.conn.execute(procurement_allocation_receipts.insert().values(
                id=str(uuid.uuid4()), allocation_id=d["allocation_id"], movement_id=mid,
                reservation_id=d["reservation_id"], qty=Decimal(d["qty"])))
        users: list[str] = []
        for pid in projects:
            users += self._project_rps(ctx, pid)
        reserved_total = sum((Decimal(d["reserved"]) for d in dist), Decimal(0))
        self._notify(ctx, list(dict.fromkeys(users)), "batch_received",
                     f"Поставка по закупочному пакету № {b['batch_no']}",
                     f"{it['name']}: {money.to_json(reserved_total)} {it['unit']} зарезервировано за проектом "
                     f"(заказ № {o['order_no']}).",
                     object_type="procurement_batch", object_id=b["id"])

    def _delivered_by_allocation(self, conn, alloc_ids) -> dict[str, Decimal]:
        """Поставлено по распределениям: allocation_id → сумма приходов, которые НЕ сторнированы (как
        «поставлено» заказа, D-32)."""
        if not alloc_ids:
            return {}
        rev = stock_movements.alias("rev")
        out: dict[str, Decimal] = defaultdict(Decimal)
        q = (sa.select(procurement_allocation_receipts.c.allocation_id, procurement_allocation_receipts.c.qty)
             .select_from(procurement_allocation_receipts
                          .outerjoin(rev, rev.c.reverses_id == procurement_allocation_receipts.c.movement_id))
             .where(procurement_allocation_receipts.c.allocation_id.in_(alloc_ids), rev.c.id.is_(None)))
        for r in conn.execute(q):
            out[r.allocation_id] += r.qty
        return out

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
        # Строка из закупочного пакета (D-59): приход распределяется по распределениям пакета — потребная часть
        # в резервы проектов, излишек (от кратности) остаётся свободным складским остатком. Всё — в этой
        # транзакции: либо распределилось всё, либо ничего.
        link = ctx.conn.execute(sa.select(purchase_order_batch_lines).where(
            purchase_order_batch_lines.c.order_id == o["id"],
            purchase_order_batch_lines.c.line_id == lid)).first()
        dist: list[dict] = []
        surplus = Decimal(0)
        if link is not None:
            nb, dist, surplus = self._plan_receipt_distribution(ctx, dict(link._mapping), it, qty, nb)
        mv = self._post(ctx, auth, mid=mid, kind="receipt", w=w, it=it, qty=qty, source_doc=src,
                        reason=opt_str(p, "reason", 2000),
                        details={"purchase_order_no": o["order_no"], "order_id": o["id"], "line_id": lid,
                                 "balance_after": nb.to_json()})
        ctx.conn.execute(purchase_receipts.insert().values(movement_id=mid, order_id=o["id"], line_id=lid, qty=qty))
        if link is not None:
            self._apply_receipt_distribution(ctx, dist, link.batch_id, mid, o, w, it, auth)
        self._save_balance(ctx, w["id"], it["id"], row, nb)
        n = self._update_versioned(ctx, purchase_orders, o, {"updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("purchase_order", o["id"], auth, o["version"], n["version"],
                {"order_no": o["order_no"], "received": money.to_json(qty), "line_id": lid, "movement_id": mid,
                 "warehouse_code": w["code"], "item_code": it["code"], "unit": it["unit"], "source_doc": src,
                 "distributed": money.to_json(qty - surplus) if link is not None else None,
                 "surplus": money.to_json(surplus) if link is not None else None})
        return {"movement": mv, "balance": {"warehouse_id": w["id"], "item_id": it["id"], **nb.to_json()},
                "distribution": dist or None, "surplus": money.to_json(surplus) if link is not None else None,
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
        """Часть pull: поставщики с реквизитами и договоры (supplier.read), заказы (po.read; цены — только с
        po.read_prices).

        Все черновики и размещённые заказы + последние PULL_CLOSED_ORDERS_LIMIT закрытых/отменённых. Расширения v13
        (условия, ставки НДС, события согласования, договоры) загружаются одним запросом на таблицу.
        """
        out: dict[str, Any] = {"suppliers": [], "supplier_contracts": [], "purchase_orders": []}
        can_sup = self.policy.can(conn, uid, "supplier.read", now)
        can_po = self.policy.can(conn, uid, "po.read", now)
        if not can_sup and not can_po:
            return out
        sups = {r.id: dict(r._mapping) for r in conn.execute(sa.select(suppliers).order_by(suppliers.c.code))}
        reqs = {r.supplier_id: {k: v for k, v in r._mapping.items() if k != "supplier_id"}
                for r in conn.execute(sa.select(supplier_requisites))}
        contracts = {r.id: dict(r._mapping) for r in conn.execute(sa.select(supplier_contracts))}
        if can_sup:
            out["suppliers"] = [self.v_supplier(s, reqs.get(s["id"])) for s in sups.values()]
            out["supplier_contracts"] = [self.v_contract(c) for c in sorted(
                contracts.values(), key=lambda c: (c["supplier_id"], c["signed_on"], c["number"]))]
        if not can_po:
            return out
        prices = self.policy.can(conn, uid, "po.read_prices", now)
        rows = [dict(r._mapping) for r in conn.execute(
            sa.select(purchase_orders).where(purchase_orders.c.status.in_(("draft", "placed")))
            .order_by(purchase_orders.c.order_no.desc()))]
        rows += [dict(r._mapping) for r in conn.execute(
            sa.select(purchase_orders).where(purchase_orders.c.status.in_(("closed", "cancelled")))
            .order_by(purchase_orders.c.order_no.desc()).limit(PULL_CLOSED_ORDERS_LIMIT))]
        taxes: dict[str, dict] = defaultdict(dict)
        for r in conn.execute(sa.select(purchase_order_line_taxes)):
            taxes[r.order_id][r.line_id] = r.vat_rate
        approvals: dict[str, list] = defaultdict(list)
        for r in conn.execute(sa.select(purchase_order_approvals).order_by(purchase_order_approvals.c.order_id,
                                                                           purchase_order_approvals.c.seq)):
            approvals[r.order_id].append(dict(r._mapping))
        self._attach_bases(conn, approvals)
        terms = {r.order_id: {**NO_TERMS, "contract_id": r.contract_id, "vat_mode": r.vat_mode}
                 for r in conn.execute(sa.select(purchase_order_terms))}
        for r in conn.execute(sa.select(purchase_order_invoices)):
            terms.setdefault(r.order_id, dict(NO_TERMS)).update(invoice_gross=r.invoice_gross, invoice_vat=r.invoice_vat)
        links: dict[str, dict] = defaultdict(dict)
        batch_ids: set[str] = set()
        for r in conn.execute(sa.select(purchase_order_batch_lines)):
            links[r.order_id][r.line_id] = dict(r._mapping)
            batch_ids.add(r.batch_id)
        pre = {"received": self._received_by_line(conn),
               "items": {r.id: dict(r._mapping) for r in conn.execute(sa.select(stock_items))},
               "sups": sups, "contracts": contracts, "taxes": taxes, "approvals": approvals, "terms": terms,
               "links": links,
               "batch_nos": {r.id: r.batch_no for r in conn.execute(
                   sa.select(procurement_batches).where(procurement_batches.c.id.in_(batch_ids)))} if batch_ids else {}}
        out["purchase_orders"] = [self.v_order(conn, o, prices, pre) for o in rows]
        return out
