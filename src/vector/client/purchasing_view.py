"""Представления для вкладок «Заказы поставщикам» и «Поставщики» раздела «Закупка» (D-32, D-33).

Чистые функции без Qt: данные кэша → словари для QML. Всё, что связано с деньгами и количествами, считается в
Decimal правилами vector.core.purchasing (теми же, что на сервере); в QML уходят готовые подписи.

Почему здесь пересчитываются подписи, хотя сервер присылает готовый заказ: черновик, созданный или изменённый
без связи, лежит в кэше в форме ЗАПРОСА (строки без названий позиций, без итогов). Поэтому подписи строятся из
строк + справочника номенклатуры, а «поставлено» берётся из серверной копии строки (у черновика его нет).

Кнопки — ПОДСКАЗКИ по ролям и связи; окончательное решение принимает сервер (права, статус, версия).

D-33 (ответы владельца A-34, A-35): у поставщика — реквизиты и договоры; заказ перед размещением обязательно
согласует роль «Согласование закупок» (не тот, кто его создал или отправил); НДС показывается раздельно — без НДС,
НДС, с НДС. НДС строки и итоги считаются здесь теми же функциями ядра, что на сервере (до копеек в строке).
"""
from __future__ import annotations

from decimal import Decimal
from datetime import date
from typing import Optional

from vector.core import money, purchasing as pu

from .calc_view import fmt_money, plural, short_dt, total_label
from .stock_view import _date_ru, as_of_label, qty_label

# Тон плашки статуса заказа и состояния поставки (цвета задаёт тема QML).
STATUS_TONES = {"draft": "neutral", "placed": "info", "closed": "success", "cancelled": "neutral"}
DELIVERY_TONES = {"none": "warning", "partial": "info", "full": "success"}
# Тон плашки согласования (D-33): на согласовании — ждём решения; возвращён — нужно доработать.
APPROVAL_TONES = {None: "neutral", "submit": "warning", "approve": "success", "return": "danger", "withdraw": "neutral"}
# Варианты для выпадающих списков формы заказа: режим цен и ставка НДС строки ("" — не указано).
VAT_MODE_CHOICES = [{"id": "", "label": "не указано"}] + [{"id": k, "label": v} for k, v in pu.VAT_MODES.items()]
VAT_RATE_CHOICES = [{"id": "", "label": "—"}] + [{"id": k, "label": v} for k, v in pu.VAT_RATE_LABELS_RU.items()]


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


def _vat_mode_quiet(mode) -> Optional[str]:
    """Режим цен заказа или None (не указан / неизвестное значение — форма покажет «не указано»)."""
    return mode if mode in pu.VAT_MODES else None


def vat_summary(lines: list[pu.Line], mode: Optional[str], cur: Optional[str]) -> dict:
    """Итоги НДС для экрана (A-35): «без НДС», «НДС», «с НДС», НДС по ставкам и честная пометка неполноты.

    Если хоть у одной строки с ценой НДС не посчитать (нет режима цен заказа или ставки строки), итоги НДС — «—»
    с причиной, а не заниженные числа. Строки без цены в итоги не входят (как и в общий итог, D-32).
    """
    t = pu.vat_totals(lines, mode)
    priced = sum(1 for ln in lines if ln.amount is not None)
    if t["check_failed"]:
        why = "расчёт НДС не прошёл самопроверку — суммы не показываются (сообщите администратору)"
    elif not priced:
        why = "нет строк с ценой"
    elif mode is None:
        why = "не указано, как даны цены (с НДС или без НДС)"
    elif t["unknown_count"]:
        n = t["unknown_count"]
        why = f"у {n} {plural(n, 'строки', 'строк', 'строк')} не указана ставка НДС"
    else:
        why = ""
    return {"net_label": fmt_money(t["net"], cur), "vat_label": fmt_money(t["vat"], cur),
            "gross_label": fmt_money(t["gross"], cur), "known": not why, "why": why,
            "mode_label": pu.VAT_MODES.get(mode, "Режим цен не указан"),
            "by_rate_label": "; ".join(f"{b['label']}: {fmt_money(b['vat'], cur)}" for b in t["by_rate"]),
            "net": t["net"], "vat": t["vat"], "gross": t["gross"]}


def _amount_label(t: dict, vs: Optional[dict], cur: Optional[str], line_count: int) -> str:
    """Сумма заказа одной строкой: «2 348,50 RUB с НДС» или, если НДС не посчитан, сумма строк (total_label).

    t — pu.totals (total, unpriced_count), vs — vat_summary или None. Пометка «N позиций без цены (итог неполный)»
    добавляется в обоих случаях: НДС считается только по строкам с ценой, и «с НДС» тоже может быть неполным.
    """
    if not (vs and vs.get("known")):
        return total_label(t["total"], cur, t["unpriced_count"], line_count)
    return total_label(vs["gross"], cur, t["unpriced_count"], line_count).replace(
        vs["gross_label"], vs["gross_label"] + " с НДС", 1)


def _line_vat_label(ln: Optional[pu.Line], mode: Optional[str]) -> str:
    """НДС строки для подписи: «НДС 22 %: 51 429,51» / «Без НДС» / «ставка не указана» / «—» (нет цены)."""
    if ln is None or ln.amount is None:
        return "—"
    if ln.vat_rate is None:
        return "ставка НДС не указана"
    v = pu.line_vat(ln.amount, ln.vat_rate, mode)
    if v is None:
        return f"НДС {pu.VAT_RATE_LABELS_RU[ln.vat_rate]}"
    if ln.vat_rate == "none":
        return "Без НДС"
    return f"НДС {pu.VAT_RATE_LABELS_RU[ln.vat_rate]}: {fmt_money(money.to_json(v[1]), None)}"


CHECK_TONES = {"ok": "success", "warn": "warning", "error": "danger", "info": "neutral"}
CHECK_ICONS = {"ok": "circle-check", "warn": "triangle-alert", "error": "circle-x", "info": "info"}


def _invoice_quiet(v) -> Optional[Decimal]:
    """Сумма по счёту из формы/кэша или None (пусто или ошибка ввода — ошибку покажет invoice_problem)."""
    try:
        return pu.check_invoice_amount(v, "invoice_gross")
    except pu.PurchaseError:
        return None


def invoice_problem(text: str, field: str = "invoice_gross") -> str:
    """Замечание к полю «Сумма по счёту» в форме: пусто — всё в порядке (или поле не заполнено)."""
    try:
        pu.check_invoice_amount((text or "").strip().replace(" ", "").replace(",", "."), field)
        return ""
    except pu.PurchaseError as e:
        return e.problems[0]["message_ru"]


def control_view(lines: list[pu.Line], mode: Optional[str], inv_gross, inv_vat) -> dict:
    """«Контроль расчёта» (D-34, A-38) для карточки и формы: проверки с цветом и значком, сверка со счётом,
    нужна ли причина при отправке. Считается на клиенте по тем же правилам ядра, что и на сервере (решает сервер).
    """
    c = pu.calc_control(lines, mode, _invoice_quiet(inv_gross), _invoice_quiet(inv_vat))
    return {"checks": [{**x, "tone": CHECK_TONES.get(x["state"], "neutral"), "icon": CHECK_ICONS.get(x["state"], "info")}
                       for x in c["checks"]],
            "invoice_state": c["invoice"]["state"], "needs_reason": c["needs_reason"], "check_failed": c["check_failed"],
            "has_problem": c["check_failed"] or c["needs_reason"]}


def preview(raw_lines: list, currency: Optional[str], items: list, vat_mode: Optional[str] = None,
            invoice_gross: Optional[str] = None, invoice_vat: Optional[str] = None) -> dict:
    """Проверка строк в форме черновика ДО сохранения: суммы и НДС строк, замечания по строкам, итоги.

    ready_problems — чего не хватит для согласования (режим цен, ставки, валюта); сохранить черновик это не мешает.
    """
    it = {i["id"]: i for i in items}
    mode = _vat_mode_quiet(vat_mode)
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
                     "vat_label": _line_vat_label(ln, mode),
                     "problem": " ".join(per_line.get(i, []))})
    t = pu.totals(ok)
    ready = pu.ready_problems(ok, cur, mode) if not problems else []
    return {"rows": rows, "valid": not problems, "problem_count": len(problems), "vat": vat_summary(ok, mode, cur),
            # Контроль расчёта и сверка со счётом прямо в форме (D-34) — до сохранения.
            "control": control_view(ok, mode, invoice_gross, invoice_vat) if not problems else None,
            "ready_problems": [p["message_ru"] for p in ready],
            "general_problems": [p["message_ru"] for p in problems if not p.get("line")],
            "first_problem": problems[0]["message_ru"] if problems else "",
            "total_label": total_label(t["total"], cur, t["unpriced_count"], len(raw_lines or []))
            + ("" if not problems else " · есть ошибки в строках")}


def _item_label(item: dict) -> str:
    """«КВВГ-4x1.5 · Кабель …» или пусто."""
    return f"{item['code']} · {item['name']}" if item.get("code") else ""


def contract_label(c: Optional[dict], today: Optional[date] = None) -> str:
    """«№ П-14 от 20.01.2026, до 05.06.2027» (+ «истёк» / «отключён») или пусто."""
    if not c:
        return ""
    s = f"№ {c.get('number')}" + (f" от {_date_ru(c.get('signed_on'))}" if c.get("signed_on") else "")
    s += f", до {_date_ru(c['valid_until'])}" if c.get("valid_until") else ", бессрочный"
    if c.get("active") is False:
        s += " · отключён"
    elif c.get("valid_until") and c["valid_until"] < (today or date.today()).isoformat():
        s += " · истёк"
    return s


def contract_usable(c: dict, today: Optional[date] = None) -> bool:
    """Можно ли указать договор в заказе сегодня: действует и не истёк (сервер проверит то же при согласовании)."""
    return c.get("active", True) is not False and not (c.get("valid_until") and
                                                      c["valid_until"] < (today or date.today()).isoformat())


def _approval_events(events: list, people: dict) -> list[dict]:
    """История согласования для карточки: «Согласован · Зайцева Вера (замещает Иванова) · 2026-11-16 09:00».

    С D-34: решение по замещению показывает, за кого принято (основание хранится на сервере неизменяемо), а отправка
    с расхождением со счётом — причину, которую указала закупка (её видит согласующий).
    """
    out = []
    for e in events or []:
        who = people.get(e.get("actor_id") or "", "") or "пользователь"
        if e.get("on_behalf_of"):
            who += f" (замещает {people.get(e['on_behalf_of'], '') or 'коллегу'})"
        inv = ""
        if e.get("invoice_state") in ("mismatch", "unknown"):
            inv = pu.INVOICE_STATE_LABELS_RU[e["invoice_state"]] + (f": {e['invoice_reason']}" if e.get("invoice_reason") else "")
            if e.get("diff_gross") not in (None, "") and Decimal(e["diff_gross"]):
                inv += f" (разница с НДС {fmt_money(e['diff_gross'], None)})"
            elif e.get("diff_vat") not in (None, "") and Decimal(e["diff_vat"]):
                inv += f" (разница НДС {fmt_money(e['diff_vat'], None)})"
        elif e.get("invoice_state") == "match":
            inv = pu.INVOICE_STATE_LABELS_RU["match"]
        out.append({"kind": e.get("kind"), "tone": APPROVAL_TONES.get(e.get("kind"), "neutral"),
                    "label": f"{pu.APPROVAL_LABELS_RU.get(e.get('kind'), e.get('kind'))} · {who} · {short_dt(e.get('at'))}",
                    "comment": e.get("comment") or "", "invoice": inv,
                    "invoice_warn": e.get("invoice_state") in ("mismatch", "unknown"),
                    "on_behalf_of": e.get("on_behalf_of")})
    return out


def _order(o: dict, sup: dict, it: dict, needs: dict, roles: list, online: bool, prices: bool,
           contracts: Optional[dict] = None, people: Optional[dict] = None, me: str = "",
           today: Optional[date] = None) -> dict:
    """Один заказ для экрана: подписи, строки с НДС и «поставлено / ждём», согласование, подсказки кнопок.

    contracts — договоры по id (из кэша), people — имена по id, me — id текущего пользователя (для правила
    «не согласовывать свой заказ»; окончательно проверяет сервер).
    """
    contracts, people = contracts or {}, people or {}
    status = o.get("status") or "draft"
    mode = _vat_mode_quiet(o.get("vat_mode"))
    appr = o.get("approval") if isinstance(o.get("approval"), dict) else {}
    state = appr.get("state")
    # Пока заказ на согласовании или согласован, его условия заморожены (правятся только справочные поля).
    frozen = status == "draft" and state in ("submit", "approve")
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
                      # Ставку НДС видят все, кто видит заказ; НДС в деньгах — только те, кто видит цены.
                      "vat_rate": r.get("vat_rate"),
                      "vat_rate_label": pu.VAT_RATE_LABELS_RU.get(r.get("vat_rate") or "", "ставка не указана"),
                      "vat_label": _line_vat_label(ln, mode) if prices else "",
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
    # Согласует роль «Согласование закупок» (своя или по замещению — она тоже в roles) и, резервом,
    # руководитель отдела (D-34, A-37).
    is_appr = "po_approver" in roles or "dept_head" in roles
    received_any = any(p[1] > 0 for p in pairs)
    no = o.get("order_no")
    # Чего не хватает для согласования (как проверит сервер): строки, валюта, режим цен, ставки.
    ready = [p["message_ru"] for p in (problems or pu.ready_problems(ok, cur, mode))] if status == "draft" else []
    contract = contracts.get(o.get("contract_id") or "") or (
        {"number": o.get("contract_number"), "signed_on": o.get("contract_signed_on"),
         "valid_until": o.get("contract_valid_until")} if o.get("contract_number") else None)
    own = bool(me) and me in (appr.get("submitted_by"), o.get("created_by"))
    stale = state == "approve" and appr.get("hash_ok") is False
    vs = vat_summary(ok, mode, cur) if prices else None
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
        "total_label": (total_label(t["total"], cur, t["unpriced_count"], len(raw)) if prices
                        else "цены видят закупка, бухгалтерия и согласующие"),
        # Короткая сумма для списков (карточка поставщика): «с НДС», если НДС посчитан, иначе сумма строк как есть;
        # пометка «итог неполный» сохраняется всегда — строки без цены не прячутся за суммой с НДС.
        "amount_label": (_amount_label(t, vs, cur, len(raw)) if prices
                         else "цены видят закупка, бухгалтерия и согласующие"),
        # НДС (A-35): режим цен виден всем; суммы без НДС / НДС / с НДС — тем, кто видит цены.
        "vat_mode": mode, "vat_mode_label": pu.VAT_MODES.get(mode, "не указано (с НДС или без НДС)"),
        "vat": vs,
        # Сверка со счётом и контроль расчёта (D-34, A-38) — тем, кто видит цены.
        "invoice_gross": o.get("invoice_gross") if prices else None,
        "invoice_vat": o.get("invoice_vat") if prices else None,
        "invoice_gross_label": fmt_money(o.get("invoice_gross"), cur) if prices and o.get("invoice_gross") else "",
        "invoice_vat_label": fmt_money(o.get("invoice_vat"), cur) if prices and o.get("invoice_vat") else "",
        "control": control_view(ok, mode, o.get("invoice_gross"), o.get("invoice_vat")) if prices and not problems else None,
        # Договор (A-34): из справочника договоров в кэше; если его там нет — то, что прислал сервер.
        "contract_id": o.get("contract_id"), "contract_label": contract_label(contract, today) or "без договора",
        # Согласование (D-33).
        "approval_state": state, "approval_label": pu.APPROVAL_LABELS_RU.get(state, ""),
        "approval_tone": APPROVAL_TONES.get(state, "neutral"),
        "approval_events": _approval_events(appr.get("events") or [], people),
        "return_reason": next((e.get("comment") or "" for e in reversed(appr.get("events") or [])
                               if e.get("kind") == "return"), "") if state == "return" else "",
        "on_approval": state == "submit" and status == "draft", "frozen": frozen,
        "approval_stale": stale,
        "ready_problems": ready,
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
        "can_edit": is_proc and status == "draft" and not frozen,
        "can_edit_info": is_proc and (status == "placed" or frozen),
        # Согласование: отправить (закупка, черновик готов), отозвать (закупка), согласовать / вернуть
        # (роль «Согласование закупок», не свой заказ). Разместить — только согласованный и не изменённый.
        "can_submit": is_proc and online and status == "draft" and not frozen and not pending and not ready,
        "can_withdraw": is_proc and online and frozen and not pending,
        "can_approve": is_appr and online and status == "draft" and state == "submit" and not pending and not own,
        "can_return": is_appr and online and status == "draft" and state == "submit" and not pending and not own,
        "is_own_for_approver": is_appr and own and state == "submit",
        "can_place": is_proc and online and status == "draft" and state == "approve" and not stale and not pending,
        "can_cancel": is_proc and online and not pending and status in ("draft", "placed") and not received_any,
        "can_close": is_proc and online and not pending and status == "placed",
        "close_needs_reason": delivery != "full",
        "can_receive": "warehouse" in roles and online and not pending and status == "placed"
                       and any(x["can_receive"] for x in lines),
    }


def orders_view(orders: list, suppliers: list, items: list, needs: list, roles: list, online: bool,
                last_pull: str, flt: str = "active", contracts: Optional[list] = None,
                people: Optional[dict] = None, me: str = "", today: Optional[date] = None) -> dict:
    """Вкладка «Заказы поставщикам».

    flt — фильтр: active (черновики и размещённые), approval (на согласовании), all, draft, placed,
    closed (закрытые и отменённые). Порядок: новые (не отправленные) сверху, затем по убыванию номера.
    contracts — договоры поставщиков из кэша (для подписей и выбора в форме), people — имена (история
    согласования), me — id текущего пользователя.
    """
    contracts = contracts or []
    cmap = {c["id"]: c for c in contracts}
    sup = {s["id"]: s for s in suppliers}
    it = {i["id"]: i for i in items}
    nd = {n["id"]: n for n in needs}
    nd = {k: {**v, "title": f"{v.get('project_code', '')} · {v.get('kit_code', '')} v{v.get('calc_version_no', '')}"}
          for k, v in nd.items()}
    # Цены видят закупка, бухгалтерия, согласующий и руководитель отдела (D-34) — как po.read_prices на сервере.
    prices = bool({"procurement", "accountant", "po_approver", "dept_head"} & set(roles))
    views = [_order(o, sup.get(o.get("supplier_id")), it, nd, roles, online, prices, cmap, people, me, today)
             for o in orders]
    views.sort(key=lambda v: (v["order_no"] is not None, -(v["order_no"] or 0)))
    keep = {"active": ("draft", "placed"), "draft": ("draft",), "placed": ("placed",),
            "closed": ("closed", "cancelled")}.get(flt)
    if flt == "approval":
        shown = [v for v in views if v["on_approval"]]
    else:
        shown = [v for v in views if keep is None or v["status"] in keep]
    counts = {k: sum(1 for v in views if v["status"] == k) for k in ("draft", "placed", "closed", "cancelled")}
    counts["waiting"] = sum(1 for v in views if v["status"] == "placed" and v["delivery"] != "full")
    counts["on_approval"] = sum(1 for v in views if v["on_approval"])
    return {"as_of": as_of_label(last_pull), "online": online, "orders": shown, "counts": counts, "filter": flt,
            "can_create": "procurement" in roles, "sees_prices": prices, "is_approver": bool({"po_approver", "dept_head"} & set(roles)),
            "suppliers": [{"id": s["id"], "label": f"{s['code']} · {s['name']}", "vat_rate": s.get("vat_rate") or ""}
                          for s in sorted(suppliers, key=lambda s: s.get("code") or "") if s.get("active", True)],
            # Договоры для выбора в форме заказа: только действующие (выбор фильтруется по поставщику в QML).
            "contracts": [{"id": c["id"], "supplier_id": c["supplier_id"], "label": contract_label(c, today)}
                          for c in sorted(contracts, key=lambda c: (c.get("number") or ""))
                          if contract_usable(c, today)],
            "vat_modes": VAT_MODE_CHOICES, "vat_rates": VAT_RATE_CHOICES,
            "items": [{"id": i["id"], "label": _item_label(i), "unit": i.get("unit", "")} for i in
                      sorted(items, key=lambda i: i.get("code") or "") if i.get("active", True)],
            "empty_text": ("Заказов пока нет. Создайте черновик — его можно готовить и без связи." if not views and
                           "procurement" in roles else "Заказов поставщикам нет." if not views else
                           "Заказов на согласовании нет." if flt == "approval" else
                           "Под этот фильтр заказов нет.")}


def _requisites_complete(s: dict) -> bool:
    """«Реквизиты заполнены» — как на сервере: ИНН, БИК и счёт; у организации (ИНН из 10 цифр) ещё КПП.

    Считается и здесь, потому что поставщик, созданный без связи, лежит в кэше в форме запроса (без признака).
    """
    inn = s.get("inn") or ""
    return bool(inn and s.get("bik") and s.get("bank_account") and (len(inn) != 10 or s.get("kpp")))


def suppliers_view(suppliers: list, roles: list, contracts: Optional[list] = None,
                   today: Optional[date] = None) -> dict:
    """Вкладка «Поставщики»: справочник с реквизитами и договорами (ведёт закупка, A-34; правка — можно без
    связи, через очередь). Реквизиты необязательны — «не заполнены» лишь подсказка, не запрет (A-39).
    """
    by_sup: dict[str, list] = {}
    for c in sorted(contracts or [], key=lambda c: (not c.get("active", True), c.get("signed_on") or "", c.get("number") or "")):
        by_sup.setdefault(c.get("supplier_id"), []).append(
            {**c, "pending": bool(c.get("pending") or c.get("_pending")), "label": contract_label(c, today),
             "usable": contract_usable(c, today), "tone": "success" if contract_usable(c, today) else "neutral"})
    rows = []
    for s in sorted(suppliers, key=lambda s: (not s.get("active", True), s.get("code") or "")):
        complete = _requisites_complete(s)
        rows.append({**s, "pending": bool(s.get("pending") or s.get("_pending")),
                     "inn_label": f"ИНН {s['inn']}" if s.get("inn") else "ИНН не указан",
                     "state_label": "действует" if s.get("active", True) else "отключён",
                     "state_tone": "success" if s.get("active", True) else "neutral",
                     "requisites_complete": complete,
                     "requisites_label": "реквизиты заполнены" if complete else "реквизиты не заполнены",
                     "vat_rate_label": pu.VAT_RATE_LABELS_RU.get(s.get("vat_rate") or "", "не указана"),
                     "bank_label": " · ".join(x for x in (s.get("bank_name"), f"БИК {s['bik']}" if s.get("bik") else "") if x),
                     "contracts": by_sup.get(s["id"], [])})
    return {"suppliers": rows, "can_edit": "procurement" in roles, "vat_rates": VAT_RATE_CHOICES,
            "empty_text": "Поставщиков пока нет." + (" Добавьте первого — код, название и, если известен, ИНН."
                                                     if "procurement" in roles else "")}


def requisites_problems(fields: dict, inn: str) -> dict:
    """Проверка реквизитов в форме поставщика теми же правилами, что на сервере: {поле: текст замечания}.

    Пустые поля допустимы; счета проверяются вместе с БИК (ключ счёта), КПП/ОГРН — вместе с ИНН.
    """
    try:
        i = pu.check_inn((inn or "").strip() or None)
    except pu.PurchaseError:
        i = None            # ИНН проверяет своё поле; реквизиты — без согласования с ним
    try:
        pu.check_requisites({k: ((fields or {}).get(k) or "").strip() or None for k in pu.REQUISITE_KEYS}, i)
        return {}
    except pu.PurchaseError as e:
        out: dict[str, str] = {}
        for p in e.problems:
            out.setdefault(p.get("field") or "", p["message_ru"])
        return out


def inn_problem(text: str) -> str:
    """Проверка ИНН в форме теми же правилами, что на сервере (пусто — допустимо)."""
    try:
        pu.check_inn(text.strip() or None)
        return ""
    except pu.PurchaseError as e:
        return e.message_ru
