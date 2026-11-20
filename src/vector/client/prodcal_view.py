"""Представление производственного календаря (этап 7, R-20; decision log D-48 — ответ владельца на A-52 п. 3).
Чистые функции без Qt.

Что видно в окне «Производственный календарь» (раздел «Оргструктура» → «Графики работы» → «Производственный
календарь»):

* годы (прошлый, текущий, следующий) и откуда календарь на каждый: загружен (с xmlcalendar.ru или из файла), встроен
  в программу или его нет (тогда — только дни недели графика);
* особые дни выбранного года: праздники и перенесённые выходные, сокращённые (предпраздничные) дни, рабочие выходные
  по переносу; у каждого — откуда он (календарь или правка администратора);
* администратору — «Загрузить из интернета», «Загрузить из файла» и «Изменить день» (региональный праздник, решение
  компании); с октября программа администратора сама загружает календарь на следующий год (auto_note).

Флаги can_* — подсказки интерфейса; решает сервер.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from vector.core import prodcal as pc
from vector.core import schedule as sch

ORIGIN_RU = {"import": "из загруженного календаря", "builtin": "из встроенного календаря",
             "manual": "правка администратора"}
# Варианты типа дня в форме «Изменить день»; пусто — убрать правку (день — как в календаре).
KIND_OPTIONS = [{"id": "holiday", "label": "Нерабочий праздничный день"},
                {"id": "short", "label": "Сокращённый на 1 час (предпраздничный)"},
                {"id": "workday", "label": "Рабочий день (перенос выходного)"},
                {"id": "normal", "label": "Обычный день по графику (снять праздник)"},
                {"id": "", "label": "Как в календаре (убрать правку)"}]


def parse_day(text: Any) -> Optional[str]:
    """«30.12.2026» или «2026-12-30» → «2026-12-30»; неверная дата — None."""
    s = (text or "").strip() if isinstance(text, str) else ""
    try:
        if len(s) == 10 and s[2] == "." and s[5] == ".":
            return date(int(s[6:]), int(s[3:5]), int(s[:2])).isoformat()
        return date.fromisoformat(s).isoformat() if len(s) == 10 else None
    except ValueError:
        return None


def calendar_view(cal: Optional[dict], year: int, online: bool, today: date) -> dict[str, Any]:
    """Модель окна «Производственный календарь»: cal — work_calendar из pull (до первого pull — встроенный
    календарь), year — выбранный год (0 — текущий)."""
    cal = cal if isinstance(cal, dict) else {}
    years = cal.get("years") or [{"year": y, **pc.year_status(y, {})} for y in (today.year - 1, today.year,
                                                                                 today.year + 1)]
    have = [y["year"] for y in years]
    year = year if year in have else (today.year if today.year in have else have[0])
    days = cal.get("days")
    if not isinstance(days, dict):
        days = pc.effective(have, {}, [], [])
    over = {o["date"]: o for o in cal.get("overrides") or []}
    rows = []
    keys = sorted({k for k in days if k.startswith(f"{year}-")}
                  | {k for k, o in over.items() if k.startswith(f"{year}-") and o.get("kind") == "normal"})
    for k in keys:
        info = days.get(k) or {"kind": "normal", "title": (over.get(k) or {}).get("title"), "origin": "manual"}
        d = date.fromisoformat(k)
        kind = info.get("kind") or "normal"
        o = over.get(k)
        rows.append({"date": k, "date_label": f"{d:%d.%m.%Y}", "weekday": sch.WEEKDAY_SHORT_RU[d.weekday()],
                     "kind": kind, "kind_label": pc.KIND_LABELS_RU.get(kind, kind), "tone": pc.KIND_TONES.get(kind, "neutral"),
                     "title": info.get("title") or "", "origin": info.get("origin") or "",
                     "origin_label": ORIGIN_RU.get(info.get("origin") or "", ""), "manual": o is not None,
                     "version": (o or {}).get("version") or 0, "past": d < today})
    ystat = next((y for y in years if y["year"] == year), {})
    manage = bool(cal.get("manage"))
    auto = [int(y) for y in cal.get("auto_years") or []]
    c = pc.counts(r for r in rows)
    return {
        "year": year, "years": [{"year": y["year"], "label": y.get("label") or "", "state": y.get("state") or "",
                                 "selected": y["year"] == year} for y in years],
        "year_label": ystat.get("label") or "", "year_state": ystat.get("state") or "",
        "counts_label": pc.counts_label(c), "rows": rows, "empty": not rows,
        "manage": manage, "can_manage": manage and online, "online": online,
        "block": "" if online or not manage else "Загрузить календарь и изменить день можно только при связи с сервером.",
        "auto_note": ("Календарь на " + " и ".join(str(y) for y in auto) + (" годы" if len(auto) > 1 else " год")
                      + " программа загрузит сама, как только сможет скачать его с xmlcalendar.ru (проверяет раз в "
                      "день).") if manage and auto else "",
        "kind_options": KIND_OPTIONS,
        "hint": ("Праздники, сокращённые предпраздничные дни и переносы выходных. Графики, которые учитывают "
                 "календарь: в праздник окно «Начните рабочий день» не появляется, в рабочую субботу по переносу — "
                 "появляется, в сокращённый день конец по графику на час раньше. Остальные дни — по дням недели "
                 "графика."),
    }
