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

from vector.core import stock

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
             people: dict, last_pull: str, online: bool, roles: list) -> dict:
    """Экран «Склад»: позиции с остатками по складам, резервы, журнал, подсказки действий."""
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
        mv_rows.append({**m, "item_code": i.get("code", "?"), "item_name": i.get("name", ""),
                        "warehouse_code": wh.get(m["warehouse_id"], {}).get("code", "?"),
                        "project_code": proj_label(m.get("project_id")),
                        "qty_label": ("+" if sign > 0 else "−") + qty_label(m["qty"], i.get("unit", "")),
                        "tone": "success" if sign > 0 else "warning",
                        "is_storno": bool(m.get("reverses_id")), "reversed": bool(m.get("reversed_by")),
                        # Номер отменённого движения (если оно есть в снимке) — «Сторно прихода № 3».
                        "reverses_doc_no": doc_of.get(m.get("reverses_id")) if m.get("reverses_id") else None,
                        "author": people.get(m.get("posted_by") or "", ""),
                        "posted_label": (m.get("posted_at") or "").replace("T", " ")[:16]})

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
                     "movements": [m for m in mv_rows if m["item_id"] == i["id"]][:30]})
    is_wh = "warehouse" in roles
    return {"as_of": as_of_label(last_pull), "online": online, "items": rows,
            "warehouses": [{**w, "pending": bool(w.get("_pending")),
                            "item_count": sum(1 for b in balances if b["warehouse_id"] == w["id"] and _d(b["on_hand"]) > 0)}
                           for w in sorted(warehouses, key=lambda x: x["code"].lower())],
            "reservations": sorted(res_rows, key=lambda r: (r["status"] != "active", r.get("created_at") or "")),
            "movements": mv_rows,
            "projects": [{"id": p["id"], "label": f"{p['code']} · {p['title']}"} for p in projects],
            "summary": {"items": len(rows), "with_stock": sum(1 for r in rows if r["state"] != "none"),
                        "active_reservations": sum(1 for r in res_rows if r["status"] == "active"),
                        "movements": len(mv_rows)},
            # Подсказки: проводки и резервы — склад и только при связи (online_required); справочники — офлайн тоже.
            "can_post": is_wh and online, "can_edit_refs": is_wh or "procurement" in roles,
            "can_edit_warehouses": is_wh, "is_warehouse": is_wh}


def _match_label(ln: dict) -> str:
    """Подпись «как сопоставлена строка» для карточки потребности: автоматически, вручную (с причиной и номером
    решения) или пусто, если строка не сопоставлена (тогда показывается причина автомата).
    """
    src = ln.get("match_source")
    if src == "auto":
        return "Сопоставлена автоматически (код и единица совпали)"
    if src == "manual":
        n = ln.get("match_count") or 1
        head = "Сопоставлена вручную" + (f" (решение №{n})" if n > 1 else "")
        return head + (": " + ln["match_reason"] if ln.get("match_reason") else "")
    return ""


def needs_view(needs: list, candidates: list, items: list, last_pull: str, online: bool, roles: list) -> dict:
    """Экран «Закупка»: потребности со строками и покрытием, кандидаты для формирования."""
    it = {i["id"]: i for i in items}
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
            lines.append({**ln, "qty_label": qty_label(ln["qty"], unit), "reserved_label": qty_label(cov["reserved"], unit),
                          "open_label": qty_label(cov["open"], unit),
                          "available_label": qty_label(cov["available"], unit) if cov["available"] is not None else "неизвестно",
                          "state_label": st, "state_tone": tone,
                          "item_label": (it[ln["item_id"]]["code"] + " · " + it[ln["item_id"]]["name"]) if ln.get("item_id") in it
                          else ("сопоставлена" if ln.get("item_id") else ""),
                          # Ручное сопоставление (A-25): чьё решение действует и можно ли его поменять. Поменять —
                          # только пока под строку ничего не зарезервировано (сервер проверит то же самое).
                          "match_source": ln.get("match_source"),
                          "match_label": _match_label(ln),
                          "can_rematch": _d(cov["reserved"]) == 0})
        open_count = sum(1 for x in lines if x["item_id"] and _d(x["coverage"]["open"]) > 0)
        out.append({**n, "lines": lines, "open_count": open_count,
                    "unmatched_count": sum(1 for x in lines if not x["item_id"]),
                    "covered_count": sum(1 for x in lines if x["item_id"] and _d(x["coverage"]["open"]) == 0),
                    "title": f"{n.get('project_code', '')} · {n.get('kit_code', '')} v{n['calc_version_no']}"})
    return {"as_of": as_of_label(last_pull), "online": online, "needs": out, "candidates": candidates,
            "can_generate": "procurement" in roles and online, "can_reserve": "warehouse" in roles and online,
            "can_match": "procurement" in roles and online,
            "sees_stock": "warehouse" in roles or "procurement" in roles}
