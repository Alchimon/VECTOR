"""Представления для вкладок «Заказы поставщикам» и «Поставщики» раздела «Закупка» (D-32).

Чистые функции без Qt: данные кэша → словари для QML. Всё, что связано с деньгами и количествами, считается в
Decimal правилами vector.core.purchasing (теми же, что на сервере); в QML уходят готовые подписи.

Почему здесь пересчитываются подписи, хотя сервер присылает готовый заказ: черновик, созданный или изменённый
без связи, лежит в кэше в форме ЗАПРОСА (строки без названий позиций, без итогов). Поэтому подписи строятся из
строк + справочника номенклатуры, а «поставлено» берётся из серверной копии строки (у черновика его нет).

Кнопки — ПОДСКАЗКИ по ролям и связи; окончательное решение принимает сервер (права, статус, версия).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from vector.core import money, purchasing as pu

from .calc_view import fmt_money, plural, short_dt, total_label
from .stock_view import _date_ru, as_of_label, qty_label

# Тон плашки статуса заказа и состояния поставки (цвета задаёт тема QML).
STATUS_TONES = {"draft": "neutral", "placed": "info", "closed": "success", "cancelled": "neutral"}
DELIVERY_TONES = {"none": "warning", "partial": "info", "full": "success"}


def _d(v) -> Decimal:
    """Десятичная строка → Decimal; пусто/None → 0 (только для «поставлено», где отсутствие = ничего не пришло)."""
    return Decimal(str(v)) if v not in (None, "") else Decimal(0)


def _parse_quiet(raw_lines: list, currency) -> tuple[list[pu.Line], Optional[str], list[dict]]:
    """Разобрать строки правилами ядра, не падая: (корректные строки, валюта, замечания).

    Корректные строки считаются и при замечаниях в других строках — пользователь видит, что уже сходится.
    """
    try:
        lines, cur = pu.parse_lines(raw_lines or [], currency)
        return lines, cur, []
    except pu.PurchaseError as e:
        problems = e.problems
    ok = []
    bad = {p.get("line") for p in problems}
    for i, raw in enumerate(raw_lines or [], start=1):
        if i in bad:
            continue
        try:
            ok += pu.parse_lines([raw], currency or "XXX")[0]
        except pu.PurchaseError:
            pass
    try:
        cur = pu.check_currency(currency)
    except pu.PurchaseError:
        cur = None
    return ok, cur, problems


def preview(raw_lines: list, currency: Optional[str], items: list) -> dict:
    """Проверка строк в форме черновика ДО сохранения: суммы строк, замечания по строкам, итог."""
    it = {i["id"]: i for i in items}
    ok, cur, problems = _parse_quiet(raw_lines, currency or None)
    by_id = {ln.line_id: ln for ln in ok}
    per_line: dict[int, list[str]] = {}
    for p in problems:
        if p.get("line"):
            per_line.setdefault(p["line"], []).append(p["message_ru"])
    rows = []
    for i, raw in enumerate(raw_lines or [], start=1):
        ln = by_id.get(raw.get("line_id"))
        item = it.get(raw.get("item_id")) or {}
        rows.append({**raw, "pos": i, "item_label": _item_label(item), "unit": item.get("unit", ""),
                     "amount_label": fmt_money(money.to_json(ln.amount), None) if ln and ln.amount is not None else "—",
                     "problem": " ".join(per_line.get(i, []))})
    t = pu.totals(ok)
    return {"rows": rows, "valid": not problems, "problem_count": len(problems),
            "general_problems": [p["message_ru"] for p in problems if not p.get("line")],
            "first_problem": problems[0]["message_ru"] if problems else "",
            "total_label": total_label(t["total"], cur, t["unpriced_count"], len(raw_lines or []))
            + ("" if not problems else " · есть ошибки в строках")}


def _item_label(item: dict) -> str:
    """«КВВГ-4x1.5 · Кабель …» или пусто."""
    return f"{item['code']} · {item['name']}" if item.get("code") else ""


def _order(o: dict, sup: dict, it: dict, needs: dict, roles: list, online: bool, prices: bool) -> dict:
    """Один заказ для экрана: подписи, строки с «поставлено / ждём», подсказки кнопок."""
    status = o.get("status") or "draft"
    pending = bool(o.get("pending") or o.get("_pending"))
    server_lines = {ln.get("line_id"): ln for ln in (o.get("lines") or []) if isinstance(ln, dict)}
    raw = [{k: ln.get(k) for k in pu.LINE_KEYS if k in ln} for ln in (o.get("lines") or []) if isinstance(ln, dict)]
    ok, cur, problems = _parse_quiet(raw, o.get("currency"))
    by_id = {ln.line_id: ln for ln in ok}
    lines, pairs = [], []
    for pos, r in enumerate(raw, start=1):
        item = it.get(r.get("item_id")) or {}
        unit = item.get("unit") or server_lines.get(r.get("line_id"), {}).get("unit") or ""
        s = server_lines.get(r.get("line_id"), {})
        got = _d(s.get("received"))
        qty = _d(r.get("qty")) if r.get("qty") not in (None, "") else None
        left = pu.remaining(qty, got) if qty is not None else None
        if status in ("placed", "closed") and qty is not None:
            pairs.append((qty, got))
        ln = by_id.get(r.get("line_id"))
        need = needs.get(r.get("need_id")) if r.get("need_id") else None
        lines.append({"line_id": r.get("line_id"), "pos": pos, "item_id": r.get("item_id"),
                      "item_label": _item_label(item) or (s.get("item_code") or "позиция не найдена в справочнике"),
                      "unit": unit, "qty": money.to_json(qty) if qty is not None else None,
                      "qty_label": qty_label(money.to_json(qty) if qty is not None else None, unit),
                      "unit_price": r.get("unit_price") if prices else None,
                      "price_label": (fmt_money(r.get("unit_price"), None) if r.get("unit_price") not in (None, "")
                                      else "неизвестна") if prices else "",   # в QML: «Цена неизвестна»
                      "amount_label": (fmt_money(money.to_json(ln.amount), None) if ln and ln.amount is not None else "—")
                      if prices else "",
                      "received_label": qty_label(money.to_json(got), unit),
                      "remaining": money.to_json(left) if left is not None else None,
                      "remaining_label": qty_label(money.to_json(left), unit) if left is not None else "—",
                      "need_id": r.get("need_id"), "need_line_id": r.get("need_line_id"), "note": r.get("note"),
                      "need_label": (f"Потребность {need.get('title') or ''}".strip() if need else
                                     ("под строку потребности" if r.get("need_id") else "")),
                      "can_receive": status == "placed" and left is not None and left > 0})
    delivery = pu.delivery_state(pairs) if status in ("placed", "closed") else None
    t = pu.totals(ok)
    is_proc = "procurement" in roles
    received_any = any(p[1] > 0 for p in pairs)
    no = o.get("order_no")
    return {
        "id": o["id"], "version": o.get("version") or 0, "order_no": no,
        "title": (f"Заказ № {no}" if no else "Новый заказ (не отправлен)"),
        "supplier_id": o.get("supplier_id"),
        "supplier_label": (f"{sup.get('code')} · {sup.get('name')}" if sup else (o.get("supplier_name") or "поставщик не найден")),
        "status": status, "status_label": pu.STATUS_LABELS_RU.get(status, status), "status_tone": STATUS_TONES.get(status, "neutral"),
        "delivery": delivery, "delivery_label": pu.DELIVERY_LABELS_RU[delivery] if delivery else "",
        "delivery_tone": DELIVERY_TONES.get(delivery, "neutral"),
        "currency": cur or o.get("currency"), "currency_label": cur or o.get("currency") or "валюта не указана",
        "prices_visible": prices,
        "total_label": (total_label(t["total"], cur, t["unpriced_count"], len(raw)) if prices else "цены видят закупка и бухгалтерия"),
        "supplier_ref": o.get("supplier_ref") or "", "expected_on": o.get("expected_on") or "",
        "expected_label": ("ожидается " + _date_ru(o.get("expected_on")) + " (для справки)") if o.get("expected_on") else "",
        "note": o.get("note") or "", "close_reason": o.get("close_reason") or "",
        "placed_label": ("размещён " + short_dt(o.get("placed_at"))) if o.get("placed_at") else "",
        "closed_label": ((("закрыт " if status == "closed" else "отменён ") + short_dt(o.get("closed_at")))
                         if o.get("closed_at") else ""),
        # Строки размещённого заказа не совпали с замороженными при размещении (правка в обход программы).
        "hash_warning": "Строки заказа не совпадают с размещёнными — сообщите администратору." if o.get("hash_ok") is False else "",
        "pending": pending, "problem": problems[0]["message_ru"] if problems else "",
        "line_count_label": f"{len(raw)} {plural(len(raw), 'строка', 'строки', 'строк')}",
        "lines": lines,
        # Подсказки кнопок. Черновик правит закупка (можно без связи); остальное — при связи.
        "can_edit": is_proc and status == "draft",
        "can_edit_info": is_proc and status == "placed",
        "can_place": is_proc and online and status == "draft" and not pending and bool(raw),
        "can_cancel": is_proc and online and not pending and status in ("draft", "placed") and not received_any,
        "can_close": is_proc and online and not pending and status == "placed",
        "close_needs_reason": delivery != "full",
        "can_receive": "warehouse" in roles and online and not pending and status == "placed"
                       and any(x["can_receive"] for x in lines),
    }


def orders_view(orders: list, suppliers: list, items: list, needs: list, roles: list, online: bool,
                last_pull: str, flt: str = "active") -> dict:
    """Вкладка «Заказы поставщикам».

    flt — фильтр: active (черновики и размещённые), all, draft, placed, closed (закрытые и отменённые).
    Порядок: новые (не отправленные) сверху, затем по убыванию номера.
    """
    sup = {s["id"]: s for s in suppliers}
    it = {i["id"]: i for i in items}
    nd = {n["id"]: n for n in needs}
    nd = {k: {**v, "title": f"{v.get('project_code', '')} · {v.get('kit_code', '')} v{v.get('calc_version_no', '')}"}
          for k, v in nd.items()}
    prices = "procurement" in roles or "accountant" in roles
    views = [_order(o, sup.get(o.get("supplier_id")), it, nd, roles, online, prices) for o in orders]
    views.sort(key=lambda v: (v["order_no"] is not None, -(v["order_no"] or 0)))
    keep = {"active": ("draft", "placed"), "draft": ("draft",), "placed": ("placed",),
            "closed": ("closed", "cancelled")}.get(flt)
    shown = [v for v in views if keep is None or v["status"] in keep]
    counts = {k: sum(1 for v in views if v["status"] == k) for k in ("draft", "placed", "closed", "cancelled")}
    counts["waiting"] = sum(1 for v in views if v["status"] == "placed" and v["delivery"] != "full")
    return {"as_of": as_of_label(last_pull), "online": online, "orders": shown, "counts": counts, "filter": flt,
            "can_create": "procurement" in roles, "sees_prices": prices,
            "suppliers": [{"id": s["id"], "label": f"{s['code']} · {s['name']}"} for s in
                          sorted(suppliers, key=lambda s: s.get("code") or "") if s.get("active", True)],
            "items": [{"id": i["id"], "label": _item_label(i), "unit": i.get("unit", "")} for i in
                      sorted(items, key=lambda i: i.get("code") or "") if i.get("active", True)],
            "empty_text": ("Заказов пока нет. Создайте черновик — его можно готовить и без связи." if not views and
                           "procurement" in roles else "Заказов поставщикам нет." if not views else
                           "Под этот фильтр заказов нет.")}


def suppliers_view(suppliers: list, roles: list) -> dict:
    """Вкладка «Поставщики»: справочник (ведёт закупка, A-34; правка — можно без связи, через очередь)."""
    rows = []
    for s in sorted(suppliers, key=lambda s: (not s.get("active", True), s.get("code") or "")):
        rows.append({**s, "pending": bool(s.get("pending") or s.get("_pending")),
                     "inn_label": f"ИНН {s['inn']}" if s.get("inn") else "ИНН не указан",
                     "state_label": "действует" if s.get("active", True) else "отключён",
                     "state_tone": "success" if s.get("active", True) else "neutral"})
    return {"suppliers": rows, "can_edit": "procurement" in roles,
            "empty_text": "Поставщиков пока нет." + (" Добавьте первого — код, название и, если известен, ИНН."
                                                     if "procurement" in roles else "")}


def inn_problem(text: str) -> str:
    """Проверка ИНН в форме теми же правилами, что на сервере (пусто — допустимо)."""
    try:
        pu.check_inn(text.strip() or None)
        return ""
    except pu.PurchaseError as e:
        return e.message_ru
