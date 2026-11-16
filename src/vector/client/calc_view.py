"""Представление расчёта комплекта и проектного итога для интерфейса (без Qt — тестируется отдельно).

Источник данных — только локальный зашифрованный кэш (SyncClient): черновик (объект calc_draft, возможно с
неотправленными правками), неизменяемые версии и ревизии (списки в meta). Поэтому всё здесь работает и офлайн:
итог черновика и сравнение с последней фиксацией считаются локально теми же правилами, что и на сервере
(vector.core.calc). Права — только подсказка интерфейса; решение принимает сервер в момент эффекта.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from vector.core import calc

# Подписи для интерфейса; внутренние коды не показываются.
KIND_LABELS = {"added": "Добавлена", "removed": "Удалена", "changed": "Изменена", "unchanged": "Без изменений"}


def group_digits(whole: str) -> str:
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return " ".join(groups) or "0"


def fmt_decimal(value: Optional[str], min_frac: int = 0) -> str:
    """«1234.5000» → «1 234,5» (min_frac=0) / «1 234,50» (min_frac=2). None → «—» (неизвестно ≠ 0)."""
    if value is None or value == "":
        return "—"
    try:
        d = Decimal(str(value))
    except Exception:
        return str(value)
    sign = "−" if d < 0 else ""
    whole, _, frac = format(abs(d), "f").partition(".")
    frac = frac.rstrip("0").ljust(min_frac, "0")
    return f"{sign}{group_digits(whole)}" + (f",{frac}" if frac else "")


def fmt_money(value: Optional[str], currency: Optional[str]) -> str:
    if value is None:
        return "—"
    return (fmt_decimal(value, 2) + (" " + currency if currency else "")).strip()


def total_label(total: Optional[str], currency: Optional[str], unknown: int, line_count: int) -> str:
    """Итог с честной пометкой неполноты: «1 245 300,00 RUB · 3 позиции без цены (итог неполный)»."""
    if line_count == 0:
        return "Позиций нет"
    s = fmt_money(total, currency)
    if unknown:
        s += f" · {unknown} {plural(unknown, 'позиция', 'позиции', 'позиций')} без цены (итог неполный)"
    return s


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def short_dt(iso_text: Optional[str]) -> str:
    return (iso_text or "")[:16].replace("T", " ")


def preview(lines: list[dict], currency: Optional[str]) -> dict:
    """Проверка рабочей копии расчёта ДО сохранения: суммы строк, замечания по строкам, итог.

    Те же правила, что на сервере (vector.core.calc); если есть замечания — строки показываются как введены,
    у проблемных строк — текст замечания, итог считается только по корректным строкам и помечается.
    """
    currency = currency or None
    problems: list[dict] = []
    try:
        parsed, _ = calc.parse_lines(lines, currency)
        by_id = {ln.line_id: ln for ln in parsed}
    except calc.CalcError as e:
        problems = e.problems
        by_id = {}
        for i, raw in enumerate(lines, start=1):
            try:          # корректные строки всё равно считаем — пользователь видит, что уже сходится
                ln = calc.parse_lines([raw], currency or "XXX")[0][0]
                if not any(p.get("line") == i for p in problems):
                    by_id[ln.line_id] = ln
            except calc.CalcError:
                pass
    line_problems: dict[int, list[str]] = {}
    for p in problems:
        if p.get("line"):
            line_problems.setdefault(p["line"], []).append(p["message_ru"])
    rows = []
    for i, raw in enumerate(lines, start=1):
        ln = by_id.get(raw.get("line_id"))
        rows.append({**raw, "pos": i,
                     "qty_label": fmt_decimal(raw.get("qty")) if raw.get("qty") not in (None, "") else "—",
                     "price_label": fmt_money(raw.get("unit_price"), None) if raw.get("unit_price") not in (None, "") else "цена неизвестна",
                     "price_unknown": raw.get("unit_price") in (None, ""),
                     "amount_label": fmt_money(calc.money.to_json(ln.amount), None) if ln and ln.amount is not None else "—",
                     "problem": " ".join(line_problems.get(i, []))})
    ok_lines = list(by_id.values())
    t = calc.totals(ok_lines)
    general = [p["message_ru"] for p in problems if not p.get("line")]
    return {"rows": rows, "valid": not problems, "problem_count": len(problems), "general_problems": general,
            "total": t["total"], "unknown_price_count": t["unknown_price_count"],
            "total_money_label": fmt_money(t["total"], currency) if lines else "Позиций нет",
            "total_label": total_label(t["total"], currency, t["unknown_price_count"], len(lines))
            + ("" if not problems else " · есть ошибки в позициях"),
            "first_problem": problems[0]["message_ru"] if problems else ""}


def decorate_version(v: dict, people: dict[str, str]) -> dict:
    return {**v, "label": f"v{v['version_no']}", "frozen_label": short_dt(v.get("frozen_at")),
            "author_name": people.get(v.get("frozen_by") or "", "—"),
            "total_label": total_label(v.get("total"), v.get("currency"), v.get("unknown_price_count", 0), v.get("line_count", 0)),
            "hash_short": (v.get("content_hash") or "")[:12]}


def kit_calc(kit: dict, draft: Optional[dict], versions: list[dict], people: dict[str, str],
             compare_no: int = 0) -> dict:
    """Всё, что показывает вкладка «Расчёт» комплекта.

    compare_no — номер версии для сравнения (0 — последняя зафиксированная).
    expected_version для сохранения вычисляет SyncClient.save_calc_draft (учитывает очередь), не интерфейс.
    """
    vs = sorted((v for v in versions if v.get("kit_id") == kit["id"]), key=lambda v: v["version_no"])
    last = vs[-1] if vs else None
    base = next((v for v in vs if v["version_no"] == compare_no), None) if compare_no else last
    lines = list((draft or {}).get("lines") or [])
    cur = (draft or {}).get("currency")
    pv = preview(lines, cur)
    d = calc.diff(base["lines"] if base else None, _normalized(lines, cur), base["currency"] if base else None, cur)
    diff_rows = []
    for r in d["rows"]:
        diff_rows.append({**r, "kind_label": KIND_LABELS[r["kind"]],
                          "changes_label": "; ".join(f"{c['label']}: {_val(c['field'], c['before'])} → {_val(c['field'], c['after'])}"
                                                     for c in r["changes"])})
    same_as_last = bool(last) and draft is not None and not draft.get("_pending") and \
        draft.get("content_hash") == last.get("content_hash")
    summary = []
    if base is None:
        summary.append("Фиксаций ещё не было — все позиции новые.")
    else:
        if d["currency_changed"]:
            summary.append(f"Валюта: {base['currency'] or '—'} → {cur or '—'}.")
        counts = [(d["added"], "добавлено"), (d["changed"], "изменено"), (d["removed"], "удалено")]
        part = ", ".join(f"{label} {n}" for n, label in counts if n)
        summary.append(f"По сравнению с v{base['version_no']}: " + (part if part else "позиции не изменились") + ".")
        if d["total_delta"] is not None:
            summary.append("Разница итога: " + ("+" if Decimal(d["total_delta"]) > 0 else "") + fmt_money(d["total_delta"], cur) + ".")
        elif d["added"] or d["changed"] or d["removed"] or d["currency_changed"]:
            summary.append("Разница итога неизвестна (есть позиции без цены или сменилась валюта).")
    return {
        "has_draft": draft is not None,
        "pending": bool((draft or {}).get("_pending")),
        "draft_version": int((draft or {}).get("version") or 0),
        "currency": cur or "", "note": (draft or {}).get("note") or "",
        "lines": lines, "preview": pv,
        "updated_label": short_dt((draft or {}).get("updated_at")),
        "updated_by_name": people.get((draft or {}).get("updated_by") or "", ""),
        "versions": [decorate_version(v, people) for v in reversed(vs)],
        "last": decorate_version(last, people) if last else None,
        "compare_no": base["version_no"] if base else 0,
        "diff_rows": diff_rows, "diff_summary": " ".join(summary),
        "has_changes_vs_base": base is None or bool(d["added"] or d["changed"] or d["removed"] or d["currency_changed"]),
        "same_as_last": same_as_last,
    }


def _normalized(lines: list[dict], cur: Optional[str]) -> list[dict]:
    """Строки черновика в канонической форме (как их вернёт сервер), чтобы diff не видел «2» ≠ «2.0000»."""
    out = []
    for raw in lines:
        try:
            out.append(calc.parse_lines([raw], cur or "XXX")[0][0].to_json())
        except calc.CalcError:
            out.append({**raw, "amount": None})
    return out


def _val(field: str, v: Any) -> str:
    if v is None:
        return "неизвестно" if field in ("unit_price", "amount") else "—"
    if field in ("qty",):
        return fmt_decimal(v)
    if field in ("unit_price", "amount"):
        return fmt_money(v, None)
    return str(v)


def project_calc(project_id: str, revisions: list[dict], people: dict[str, str]) -> dict:
    """Проектный итог: последняя ревизия (официальный итог) и история ревизий, новые сверху."""
    rs = sorted((r for r in revisions if r.get("project_id") == project_id), key=lambda r: r["revision_no"], reverse=True)
    return {"revisions": [decorate_revision(r, people) for r in rs], "latest": decorate_revision(rs[0], people) if rs else None}


def decorate_revision(r: dict, people: dict[str, str]) -> dict:
    totals = [{**t, "label": total_label(t["total"], t["currency"], t["unknown_price_count"], 1)
               if t["currency"] else ("без цен" if t["unknown_price_count"] else "0"),
               "currency_label": t["currency"] or "без валюты"} for t in r.get("totals", [])]
    return {**r, "label": f"Ревизия r{r['revision_no']}", "created_label": short_dt(r.get("created_at")),
            "author_name": people.get(r.get("created_by") or "", "—"), "totals": totals,
            "items": [{**i, "total_label": total_label(i["total"], i["currency"], i["unknown_price_count"], 1)} for i in r.get("items", [])],
            "composition": ", ".join(f"{i['kit_code']} v{i['version_no']}" for i in r.get("items", [])),
            "mixed_currencies": len([t for t in totals if t["currency"]]) > 1}


def finance_overview(revisions: list[dict], people: dict[str, str]) -> list[dict]:
    """Для страницы «Итоги проектов»: по одному элементу на проект — последняя ревизия и число ревизий."""
    by_project: dict[str, list[dict]] = {}
    for r in revisions:
        by_project.setdefault(r["project_id"], []).append(r)
    out = []
    for pid, rs in by_project.items():
        rs.sort(key=lambda r: r["revision_no"])
        latest = decorate_revision(rs[-1], people)
        out.append({"project_id": pid, "project_code": latest["project_code"], "project_title": latest["project_title"],
                    "latest": latest, "revision_count": len(rs),
                    "history": [decorate_revision(r, people) for r in reversed(rs)]})
    return sorted(out, key=lambda x: x["project_code"])
