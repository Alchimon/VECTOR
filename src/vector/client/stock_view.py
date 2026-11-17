"""Представление склада и закупочной потребности для интерфейса (без Qt — тестируется отдельно).

Источник — только локальный зашифрованный кэш (SyncClient):

* склады и номенклатура — объекты кэша (правятся офлайн через очередь, могут быть «не отправлены»);
* остатки, резервы, журнал движений и потребности — СНИМОК на момент последнего обновления (meta). Их меняют
  только онлайн-команды, поэтому офлайн экран показывает их с пометкой «на момент …» и не предлагает
  проводок. «Доступно» на клиенте — подсказка; окончательная проверка — на сервере в транзакции.

Права — тоже подсказка (роли из кэша); решение принимает сервер в момент эффекта.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from vector.core import money, stock

from .calc_view import fmt_decimal

ZERO = Decimal("0")


def qty_label(value: Optional[str], unit: str = "") -> str:
    """«12.5000», «м» → «12,5 м». None → «—» (неизвестно ≠ 0)."""
    s = fmt_decimal(value)
    return f"{s} {unit}".strip() if s != "—" else "—"


def as_of_label(last_pull: str) -> str:
    """Время снимка для пометки «на момент …» (UTC — как на сервере; часовой пояс показывается явно)."""
    if not last_pull:
        return "данные склада ещё не загружались"
    try:
        dt = datetime.fromisoformat(last_pull.replace("Z", "+00:00"))
    except ValueError:
        return f"на момент {last_pull}"
    return "на момент " + dt.strftime("%d.%m.%Y %H:%M") + " UTC"


def _date_ru(v: Optional[str]) -> str:
    """«2026-11-20» → «20.11.2026»; пусто → «»."""
    return f"{v[8:10]}.{v[5:7]}.{v[0:4]}" if v and len(v) >= 10 else ""


def shortage(shipped: str, accepted_text: str, unit: str = "") -> dict:
    """Недостача при приёмке перемещения (D-30) для формы «Принять»: сколько не пришло, Decimal без float.

    Возвращает {problem, shortage, shortage_label, has_shortage}: problem — текст ошибки ввода (пусто — нет ошибки);
    пустое поле = «пришло всё». Правила те же, что на сервере: > 0, ≤ 4 знаков, не больше отгруженного.
    """
    t = (accepted_text or "").strip().replace(",", ".").replace(" ", "").replace("\u00a0", "")
    if not t:
        return {"problem": "", "shortage": "0", "shortage_label": "", "has_shortage": False}
    try:
        acc = stock.parse_qty(t)
    except stock.StockError as e:
        return {"problem": e.message_ru, "shortage": "0", "shortage_label": "", "has_shortage": False}
    diff = Decimal(shipped) - acc
    if diff < 0:
        return {"problem": "Больше отгруженного — излишек перемещением не оформляется, проведите его приходом.",
                "shortage": "0", "shortage_label": "", "has_shortage": False}
    return {"problem": "", "shortage": str(diff), "shortage_label": qty_label(str(diff), unit), "has_shortage": diff > 0}


def _d(v) -> Decimal:
    """Количество из кэша (строка) → Decimal; пусто или None → 0 (используется только для остатков, где отсутствие
    строки и есть ноль).
    """
    return Decimal(str(v)) if v not in (None, "") else ZERO


def _state(on_hand: Decimal, reserved: Decimal) -> tuple[str, str, str]:
    """(код, подпись, тон чипа) для строки остатка."""
    if on_hand <= 0:
        return "none", "Нет на складе", "neutral"
    if on_hand - reserved <= 0:
        return "all_reserved", "Всё в резерве", "warning"
    if reserved > 0:
        return "partly_reserved", "Есть резерв", "info"
    return "free", "Свободно", "success"


def overview(warehouses: list, items: list, balances: list, reservations: list, movements: list, projects: list,
             people: dict, last_pull: str, online: bool, roles: list, transfers: Optional[list] = None,
             config: Optional[list] = None) -> dict:
    """Экран «Склад»: позиции с остатками по складам, резервы, перемещения «в пути», журнал, подсказки действий.

    transfers — перемещения из pull (D-29): все «в пути» и недавние закрытые. Количество «в пути» не входит ни в
    остаток, ни в доступное — оно показывается отдельно, чтобы отгруженное не выглядело «потерянным».
    У перемещений бывает ожидаемая дата прибытия «для справки» и акт недостачи (D-30): срок считается прошедшим по
    дате снимка last_pull (тем же «на момент …», что и остатки), а не по часам компьютера.
    config — настройки компании из pull: по stock.act_signature форма приёмки/утраты знает, как подписывать акт
    (D-31): confirm — флажок, password — флажок и пароль, qes — КЭП не подключена, подписать нельзя (сервер всё равно
    проверит действующее значение сам). Перемещение с актом об утрате — статус «lost», без действий.
    """
    wh = {w["id"]: w for w in warehouses}
    it = {i["id"]: i for i in items}
    pr = {p["id"]: p for p in projects}
    proj_label = lambda pid: (pr[pid]["code"] if pid in pr else "проект скрыт") if pid else ""  # noqa: E731

    res_rows = []
    for r in reservations:
        i = it.get(r["item_id"], {})
        unit = i.get("unit", "")
        res_rows.append({**r, "item_code": i.get("code", "?"), "item_name": i.get("name", ""),
                         "warehouse_code": wh.get(r["warehouse_id"], {}).get("code", "?"),
                         "project_code": proj_label(r["project_id"]),
                         "qty_label": qty_label(r["qty"], unit), "consumed_label": qty_label(r["consumed"], unit),
                         "remaining_label": qty_label(r["remaining"], unit),
                         "status_tone": {"active": "info", "consumed": "success", "released": "neutral"}.get(r["status"], "neutral"),
                         "author": people.get(r.get("created_by") or "", "")})
    mv_rows = []
    doc_of = {m["id"]: m.get("doc_no") for m in movements}
    for m in sorted(movements, key=lambda m: -int(m.get("doc_no") or 0)):
        i = it.get(m["item_id"], {})
        sign = stock.MOVEMENT_SIGN.get(m["kind"], 0)
        own = wh.get(m["warehouse_id"], {}).get("code", "?")
        # Половина перемещения (A-28): подпись маршрута «СК-1 → СК-2» и запрет сторно (исправляют обратным перемещением).
        tr_dir = m.get("transfer_direction")          # out — отгрузка, in — приёмка, return — возврат отправителю
        peer = wh.get(m.get("transfer_peer_warehouse_id") or "", {}).get("code", "?") if tr_dir else ""
        mv_rows.append({**m, "item_code": i.get("code", "?"), "item_name": i.get("name", ""),
                        "warehouse_code": own,
                        "project_code": proj_label(m.get("project_id")),
                        "qty_label": ("+" if sign > 0 else "−") + qty_label(m["qty"], i.get("unit", "")),
                        "tone": "info" if tr_dir else ("success" if sign > 0 else "warning"),
                        "is_transfer": bool(tr_dir),
                        # Маршрут всегда «откуда → куда» исходного перемещения: у приёмки «откуда» — второй склад,
                        # у отгрузки и возврата — свой (возврат проведён на складе-отправителе).
                        "transfer_label": (f"{peer} → {own}" if tr_dir == "in" else f"{own} → {peer}") if tr_dir else "",
                        "is_storno": bool(m.get("reverses_id")), "reversed": bool(m.get("reversed_by")),
                        # Номер отменённого движения (если оно есть в снимке) — «Сторно прихода № 3».
                        "reverses_doc_no": doc_of.get(m.get("reverses_id")) if m.get("reverses_id") else None,
                        "author": people.get(m.get("posted_by") or "", ""),
                        "posted_label": (m.get("posted_at") or "").replace("T", " ")[:16]})

    # Перемещения (D-29): маршрут, статус, действия. Действовать можно только по своим «в пути» — склад, при связи;
    # одношаговые перемещения прежней версии (legacy) — только история.
    is_wh = "warehouse" in roles
    tr_rows = []
    as_of_day = (last_pull or "")[:10]          # ГГГГ-ММ-ДД снимка; пусто — срок не оцениваем
    for t in transfers or []:
        i = it.get(t["item_id"], {})
        unit = i.get("unit", "")
        a_code = wh.get(t["from_warehouse_id"], {}).get("code", "?")
        b_code = wh.get(t["to_warehouse_id"], {}).get("code", "?")
        tr_rows.append({**t, "item_code": i.get("code", "?"), "item_name": i.get("name", ""),
                        "from_code": a_code, "to_code": b_code, "route_label": f"{a_code} → {b_code}", "unit": unit,
                        "qty_label": qty_label(t["qty"], unit),
                        # Принято с недостачей (есть акт) — «danger»: это расхождение, на которое стоит обратить внимание.
                        "status_tone": "danger" if t.get("act") or t["status"] == "lost" else
                                       {"in_transit": "warning", "received": "success", "returned": "neutral"}.get(t["status"], "neutral"),
                        "expected_label": _date_ru(t.get("expected_on")),
                        "overdue": bool(t["status"] == "in_transit" and t.get("expected_on") and as_of_day
                                        and t["expected_on"] < as_of_day),
                        "accepted_label": qty_label(t.get("accepted_qty"), unit) if t.get("accepted_qty") else "",
                        "act_label": (f"Акт недостачи № {t['act']['act_no']}: отгружено {qty_label(t['act']['shipped_qty'], unit)}, "
                                      f"принято {qty_label(t['act']['accepted_qty'], unit)}, недостача "
                                      f"{qty_label(t['act']['shortage_qty'], unit)}") if t.get("act") else "",
                        "act_signed_label": (f"{t['act']['reason']}  ·  подписал приёмщик {t['act']['signer_name']}, "
                                             f"{t['act']['signed_at'].replace('T', ' ')[:16]} UTC"
                                             f"{_sig_suffix(t['act'].get('signature'))}") if t.get("act") else "",
                        # Акт об утрате (D-31): не пришло ничего — количество списано актом.
                        "loss_label": (f"Акт об утрате № {t['loss']['act_no']}: утрачено {qty_label(t['loss']['qty'], unit)}"
                                       if t.get("loss") else ""),
                        "loss_signed_label": (f"{t['loss']['reason']}  ·  подписал приёмщик {t['loss']['signer_name']}, "
                                              f"{t['loss']['signed_at'].replace('T', ' ')[:16]} UTC"
                                              f"{_sig_suffix(t['loss'].get('signature'))}") if t.get("loss") else "",
                        "author": people.get(t.get("created_by") or "", ""),
                        "closer": people.get(t.get("closed_by") or "", ""),
                        "created_label": (t.get("created_at") or "").replace("T", " ")[:16],
                        "closed_label": (t.get("closed_at") or "").replace("T", " ")[:16],
                        "can_act": is_wh and online and t["status"] == "in_transit" and not t.get("legacy")})
    transit_by_item: dict[str, Decimal] = {}
    for t in tr_rows:
        if t["status"] == "in_transit":
            transit_by_item[t["item_id"]] = transit_by_item.get(t["item_id"], ZERO) + _d(t["qty"])

    by_item: dict[str, list] = {}
    for b in balances:
        by_item.setdefault(b["item_id"], []).append(b)
    rows = []
    for i in sorted(items, key=lambda x: x["code"].lower()):
        bs = by_item.get(i["id"], [])
        on_hand = sum((_d(b["on_hand"]) for b in bs), ZERO)
        reserved = sum((_d(b["reserved"]) for b in bs), ZERO)
        code, label, tone = _state(on_hand, reserved)
        unit = i.get("unit", "")
        rows.append({**i, "pending": bool(i.get("_pending")),
                     "on_hand_label": qty_label(str(on_hand), unit), "reserved_label": qty_label(str(reserved), unit),
                     "available_label": qty_label(str(on_hand - reserved), unit), "available": str(on_hand - reserved),
                     "state": code, "state_label": label if i.get("active", True) else "Отключена",
                     "state_tone": tone if i.get("active", True) else "neutral",
                     "by_warehouse": [{"warehouse_id": b["warehouse_id"], "warehouse_code": wh.get(b["warehouse_id"], {}).get("code", "?"),
                                       "warehouse_name": wh.get(b["warehouse_id"], {}).get("name", ""),
                                       "on_hand_label": qty_label(b["on_hand"], unit), "reserved_label": qty_label(b["reserved"], unit),
                                       "available_label": qty_label(b["available"], unit), "available": b["available"]}
                                      for b in sorted(bs, key=lambda b: wh.get(b["warehouse_id"], {}).get("code", ""))],
                     "reservations": [r for r in res_rows if r["item_id"] == i["id"] and r["status"] == "active"],
                     # «В пути» — отдельно от остатка: отгружено, но ещё не принято получателем (D-29).
                     "in_transit": str(transit_by_item.get(i["id"], ZERO)),
                     "in_transit_label": qty_label(str(transit_by_item.get(i["id"], ZERO)), unit),
                     "transfers": [t for t in tr_rows if t["item_id"] == i["id"] and t["status"] == "in_transit"],
                     "movements": [m for m in mv_rows if m["item_id"] == i["id"]][:30]})
    active_wh = sum(1 for w in warehouses if w.get("active", True) and not w.get("_pending"))
    return {"as_of": as_of_label(last_pull), "online": online, "items": rows,
            "warehouses": [{**w, "pending": bool(w.get("_pending")),
                            "item_count": sum(1 for b in balances if b["warehouse_id"] == w["id"] and _d(b["on_hand"]) > 0)}
                           for w in sorted(warehouses, key=lambda x: x["code"].lower())],
            "reservations": sorted(res_rows, key=lambda r: (r["status"] != "active", r.get("created_at") or "")),
            "movements": mv_rows,
            "transfers": tr_rows,
            "projects": [{"id": p["id"], "label": f"{p['code']} · {p['title']}"} for p in projects],
            "summary": {"items": len(rows), "with_stock": sum(1 for r in rows if r["state"] != "none"),
                        "active_reservations": sum(1 for r in res_rows if r["status"] == "active"),
                        "in_transit": sum(1 for t in tr_rows if t["status"] == "in_transit"),
                        "movements": len(mv_rows)},
            # Подсказки: проводки и резервы — склад и только при связи (online_required); справочники — офлайн тоже.
            "can_post": is_wh and online, "can_edit_refs": is_wh or "procurement" in roles,
            # Перемещать есть куда, только если действующих (уже отправленных на сервер) складов не меньше двух.
            "can_transfer": is_wh and online and active_wh >= 2,
            "can_edit_warehouses": is_wh, "is_warehouse": is_wh,
            "act_signature": signature_mode(config or [])}


def _sig_suffix(sig: Optional[dict]) -> str:
    """Хвост подписи акта: способ и — если содержание вдруг не совпало с подписанным — предупреждение."""
    if not sig:
        return ""
    warn = "" if sig.get("content_ok", True) else "  ·  ВНИМАНИЕ: содержание не совпадает с подписанным"
    return f"  ·  {sig.get('method_label', '')}{warn}"


def signature_mode(config: list) -> dict:
    """Как подписывать акты склада (настройка stock.act_signature, D-31) — для формы:
    method; needs_password — показать поле пароля; blocked — подписать нельзя (КЭП не подключена) и почему; hint.
    Если настройки нет в снимке (старый кэш) — считаем самым строгим доступным (password): лишний вопрос пароля
    безопаснее, чем пропущенный; сервер всё равно решает по своему действующему значению.
    """
    m = setting_value(config, "stock.act_signature", "password")
    if m == "qes":
        return {"method": m, "needs_password": False, "blocked": True,
                "hint": "В настройках выбрана подпись КЭП, но она ещё не подключена — акт подписать нельзя. "
                        "Обратитесь к администратору."}
    if m == "confirm":
        return {"method": m, "needs_password": False, "blocked": False,
                "hint": "Подпись — подтверждение в программе от вашего имени."}
    return {"method": "password", "needs_password": True, "blocked": False,
            "hint": "Для подписи акта введите свой пароль. Пароль проверит сервер; он нигде не сохраняется."}


def _match_label(ln: dict) -> str:
    """Подпись «как сопоставлена строка» для карточки потребности: автоматически, вручную (с причиной и номером
    решения) или пусто, если строка не сопоставлена (тогда показывается причина автомата).
    """
    src = ln.get("match_source")
    if src == "auto":
        return "Сопоставлена автоматически (код и единица совпали)"
    if src == "alias":
        # Примечание автомата уже содержит «по другому обозначению X → позиция Y» (D-25).
        return "Сопоставлена автоматически. " + (ln.get("auto_match_note") or "По другому обозначению.")
    if src == "manual":
        n = ln.get("match_count") or 1
        head = "Сопоставлена вручную" + (f" (решение №{n})" if n > 1 else "")
        if ln.get("factor"):
            head += f"; пересчёт 1 {ln['unit']} = {fmt_decimal(ln['factor'])} {ln.get('item_unit') or ''}".rstrip()
        return head + (": " + ln["match_reason"] if ln.get("match_reason") else "")
    return ""


def conversion_for(item: dict, line_unit: str, conversions: list) -> Optional[dict]:
    """Действующий пересчёт «line_unit → единица позиции»: сначала для самой позиции, затем общий.

    Те же правила, что у сервера (_find_conversion). Это подсказка для формы; сервер всё равно сверит
    коэффициент со справочником в момент решения и откажет, если его успели изменить (conversion_changed).
    """
    for want in (item["id"], None):
        for c in conversions:
            if (c.get("active") and c.get("item_id") == want and c.get("from_unit") == line_unit
                    and c.get("to_unit") == item.get("unit")):
                return c
    return None


def match_candidates(line: dict, items: list, conversions: list) -> list:
    """Позиции, с которыми можно сопоставить строку: с той же единицей или с пересчётом из справочника (A-27).

    Для позиции с пересчётом сразу считаем количество в её единицах — точно, как сервер (convert_qty). Неточный
    пересчёт (больше 4 знаков) не скрываем, а помечаем problem: человек увидит, почему выбрать нельзя.
    """
    out = []
    for i in items:
        if i.get("active") is False or i["id"] == line.get("item_id"):
            continue
        base = {"id": i["id"], "label": f"{i['code']} · {i['name']}", "unit": i["unit"],
                "conversion_id": "", "factor": "", "item_qty_label": "", "problem": ""}
        if i["unit"] == line["unit"]:
            out.append(base)
            continue
        c = conversion_for(i, line["unit"], conversions)
        if c is None:
            continue
        rule = f"1 {line['unit']} = {fmt_decimal(c['factor'])} {i['unit']}"
        try:
            q = stock.convert_qty(_d(line["qty"]), _d(c["factor"]))
            base.update(item_qty_label=qty_label(money.to_json(q), i["unit"]), problem="")
        except stock.StockError as e:
            base["problem"] = e.message_ru
        base.update(label=f"{base['label']}  (пересчёт: {rule})", conversion_id=c["id"], factor=c["factor"], rule=rule)
        out.append(base)
    return out


def needs_view(needs: list, candidates: list, items: list, last_pull: str, online: bool, roles: list,
               conversions: Optional[list] = None, config: Optional[list] = None) -> dict:
    """Экран «Закупка»: потребности со строками и покрытием, кандидаты для формирования.

    conversions — справочник пересчёта (кандидаты для ручного решения); config — настройки компании: от
    procurement.rematch_reserved_line зависит, предлагать ли «Изменить» у строки с резервом (D-26).
    """
    it = {i["id"]: i for i in items}
    conversions = conversions or []
    rematch = setting_value(config or [], "procurement.rematch_reserved_line", "forbid")
    out = []
    for n in needs:
        lines = []
        for ln in n["lines"]:
            cov = ln["coverage"]
            if ln.get("item_id") is None:
                st, tone = "Не сопоставлена", "neutral"
            elif _d(cov["open"]) == 0:
                st, tone = "Покрыта резервом", "success"
            elif cov["can_cover"] is True:
                st, tone = "Есть на складе", "info"
            elif cov["can_cover"] is False:
                st, tone = "Нужна закупка", "warning"
            else:
                st, tone = "Остаток неизвестен", "neutral"
            unit = ln["unit"]
            # Покрытие считает сервер в единицах ПОЗИЦИИ (при пересчёте они отличаются от единиц строки, A-27).
            cu = ln.get("item_unit") or unit
            reserved = _d(cov["reserved"])
            lines.append({**ln, "qty_label": qty_label(ln["qty"], unit), "reserved_label": qty_label(cov["reserved"], cu),
                          "item_qty_label": qty_label(ln.get("item_qty"), cu) if ln.get("factor") else "",
                          "open_label": qty_label(cov["open"], cu),
                          # «Заказано, ждём» — по размещённым заказам поставщикам (D-32); нет данных — пусто.
                          "on_order_label": qty_label(ln.get("on_order"), cu) if _d(ln.get("on_order")) > 0 else "",
                          "available_label": qty_label(cov["available"], cu) if cov["available"] is not None else "неизвестно",
                          "state_label": st, "state_tone": tone,
                          "item_label": (it[ln["item_id"]]["code"] + " · " + it[ln["item_id"]]["name"]) if ln.get("item_id") in it
                          else ("сопоставлена" if ln.get("item_id") else ""),
                          # Ручное сопоставление (A-25): чьё решение действует и можно ли его поменять. При резервах —
                          # по настройке компании (D-26): «запрещено» → кнопки нет; «снимать» → есть, с предупреждением.
                          # Выданное со склада и право снимать резервы проверит сервер.
                          "match_source": ln.get("match_source"),
                          "match_label": _match_label(ln),
                          "can_rematch": reserved == 0 or rematch == "release_active",
                          "rematch_warning": ("Под строку зарезервировано " + qty_label(cov["reserved"], cu) + ". По настройке "
                                              "компании действующие резервы будут сняты (нужно право склада снимать "
                                              "резервы; выданное со склада не отменить).") if reserved > 0 else "",
                          "match_candidates": match_candidates(ln, items, conversions)})
        open_count = sum(1 for x in lines if x["item_id"] and _d(x["coverage"]["open"]) > 0)
        out.append({**n, "lines": lines, "open_count": open_count,
                    "unmatched_count": sum(1 for x in lines if not x["item_id"]),
                    "covered_count": sum(1 for x in lines if x["item_id"] and _d(x["coverage"]["open"]) == 0),
                    "title": f"{n.get('project_code', '')} · {n.get('kit_code', '')} v{n['calc_version_no']}"})
    return {"as_of": as_of_label(last_pull), "online": online, "needs": out, "candidates": candidates,
            "can_generate": "procurement" in roles and online, "can_reserve": "warehouse" in roles and online,
            "can_match": "procurement" in roles and online,
            "sees_stock": "warehouse" in roles or "procurement" in roles}


def setting_value(config: list, key: str, default: str) -> str:
    """Действующее значение настройки компании из снимка pull (сервер уже подставил strictest, если не задана)."""
    for c in config:
        if c.get("key") == key:
            return c.get("value") or default
    return default


def directory_view(aliases: list, conversions: list, items: list, roles: list, online: bool) -> dict:
    """Вкладка «Справочник» раздела «Склад»: другие обозначения позиций (D-25) и пересчёт единиц (A-27).

    Вести справочник могут склад и закупка (A-27), только при связи; остальные (если видят склад) — читают.
    """
    it = {i["id"]: i for i in items}

    def item_label(iid):
        """«Код · наименование» позиции или «—», если позиции нет в снимке (например, удалённая из кэша)."""
        i = it.get(iid)
        return f"{i['code']} · {i['name']}" if i else "—"
    al = [{**a, "item_label": item_label(a["item_id"]), "state_label": "Действует" if a["active"] else "Отключено",
           "state_tone": "success" if a["active"] else "neutral"} for a in aliases]
    cv = [{**c, "rule_label": f"1 {c['from_unit']} = {fmt_decimal(c['factor'])} {c['to_unit']}",
           "scope_label": item_label(c["item_id"]) if c.get("item_id") else "Общий (любая позиция в «" + c["to_unit"] + "»)",
           "state_label": "Действует" if c["active"] else "Отключён", "state_tone": "success" if c["active"] else "neutral"}
          for c in conversions]
    return {"aliases": al, "conversions": cv, "online": online,
            "can_edit": online and ("warehouse" in roles or "procurement" in roles),
            "items": [{"id": i["id"], "label": f"{i['code']} · {i['name']}", "unit": i["unit"]}
                      for i in items if i.get("active") is not False]}


def settings_view(config: list, roles: list, online: bool) -> dict:
    """Раздел настроек в «Администрировании»: настройки компании, действующее значение, кто/когда/почему задал.

    «Не задано» показывается явно: тогда действует самое строгое значение (07-configuration). Публикует только
    администратор при связи (A-26); остальные видят, какие правила сейчас действуют.
    """
    out = []
    for c in config:
        labels = {v["value"]: v["label_ru"] for v in c.get("values", [])}
        out.append({**c, "value_label": labels.get(c["value"], c["value"]),
                    "state_label": "Задано" if c.get("is_set") else "Не задано — действует самое строгое",
                    "state_tone": "info" if c.get("is_set") else "neutral",
                    # Недоступные пока варианты (например, КЭП, D-31) в список публикации не попадают — их видно
                    # в описании (unavailable), а сервер всё равно отклонит такую публикацию (value_unavailable).
                    "options": [{"id": v["value"], "label": v["label_ru"]} for v in c.get("values", [])
                                if v.get("available") is not False],
                    "unavailable": [f"{v['label_ru']} — {v.get('unavailable_ru') or 'пока недоступно'}"
                                    for v in c.get("values", []) if v.get("available") is False]})
    return {"items": out, "can_publish": online and "admin" in roles}
