"""Правила производственного календаря (D-48, ответ на A-52 п. 3): разбор XML xmlcalendar.ru, встроенные годы,
действующий календарь (загруженный → встроенный → ручные правки) и его влияние на окно рабочего дня по графику."""

from datetime import date, timedelta, timezone

import pytest

from vector.core import prodcal as pc
from vector.core import schedule as sch

MSK = timezone(timedelta(hours=3))
FIVE = {"id": "s", "name": "Пятидневка", "start_time": "08:00", "end_time": "17:00", "break_minutes": 60,
        "weekdays": "1111100"}


def test_builtin_2026_matches_official_calendar():
    """2026 (постановление о переносах): новогодние 1–8 января, 9 января — перенос с 3-го, 31 декабря — перенос с 4
    января; сокращённые — 30 апреля, 8 мая, 11 июня, 3 ноября."""
    c = pc.builtin(2026)
    days = {d["date"]: d for d in c["days"]}
    assert c["source_date"] == "2025-09-30"
    assert all(days[f"2026-01-0{i}"]["kind"] == "holiday" for i in range(1, 9))
    assert days["2026-01-09"]["moved_from"] == "2026-01-03" and days["2026-12-31"]["moved_from"] == "2026-01-04"
    assert days["2026-01-07"]["title"] == "Рождество Христово"
    assert [k for k, d in days.items() if d["kind"] == "short"] == ["2026-04-30", "2026-05-08", "2026-06-11",
                                                                   "2026-11-03"]
    assert c["counts"] == {"holiday": 18, "short": 4, "workday": 0, "normal": 0}


def test_parse_rejects_bad_files():
    """Неверный год, страна, дата, тип, повтор дня — понятные ошибки; DOCTYPE запрещён."""
    ok = "<calendar year='2026' country='ru'><days><day d='01.01' t='1'/></days></calendar>"
    assert pc.parse_xml(ok)["days"][0] == {"date": "2026-01-01", "kind": "holiday", "title": None, "moved_from": None}
    for text, code in ((ok.replace("2026", "2025", 1), "wrong_year"), (ok.replace("'ru'", "'by'"), "wrong_country"),
                       (ok.replace("01.01", "13.01"), "invalid_day"), (ok.replace("t='1'", "t='7'"), "invalid_day"),
                       (ok.replace("</days>", "<day d='01.01' t='2'/></days>"), "duplicate_day"),
                       ("<!DOCTYPE calendar>" + ok, "unsafe_xml"), ("<calendar", "invalid_xml"), ("", "empty")):
        with pytest.raises(pc.CalendarError) as e:
            pc.parse_xml(text, 2026)
        assert e.value.code == code, text


def test_effective_precedence_and_auto_years():
    """Загруженный год важнее встроенного; ручная правка — важнее всего, normal снимает особый день; встроенного года
    нет — только дни недели. Программа администратора скачивает текущий год, если он не загружен, и с октября —
    следующий."""
    loaded = {2026: {"source": "файл"}}
    imported = [{"date": "2026-03-09", "kind": "holiday", "title": None}]
    manual = [{"date": "2026-03-10", "kind": "short", "title": "Решение"}, {"date": "2026-03-09", "kind": "normal"},
              {"date": "2027-01-04", "kind": "workday", "title": None}]
    e = pc.effective([2026, 2027], loaded, imported, manual)
    assert "2026-01-01" not in e                                 # 2026 загружен — встроенный не подмешивается
    assert "2026-03-09" not in e and e["2026-03-10"]["origin"] == "manual"
    assert e["2027-01-01"]["origin"] == "builtin" and e["2027-01-04"]["kind"] == "workday"
    assert pc.effective([2099], {}, [], []) == {}
    assert pc.year_status(2099, {})["state"] == "missing" and pc.year_status(2026, loaded)["state"] == "loaded"
    assert pc.needs_auto_years(date(2026, 9, 1), loaded) == []
    assert pc.needs_auto_years(date(2026, 10, 1), loaded) == [2027]
    assert pc.needs_auto_years(date(2027, 2, 1), loaded) == [2027]


def test_calendar_changes_schedule_window():
    """Праздник — окна нет; сокращённый — конец на час раньше; рабочая суббота — окно по времени графика; график без
    календаря и ночной — по дням недели; «короткий» график короче часа + перерыв не сокращается."""
    cal = pc.effective([2024, 2026], {}, [], [])
    assert sch.window(FIVE, date(2026, 1, 5), MSK, cal) is None
    assert sch.window(FIVE, date(2026, 1, 5), MSK) is not None    # без календаря — понедельник рабочий
    a, b = sch.window(FIVE, date(2026, 11, 3), MSK, cal)          # 3 ноября — предпраздничный
    assert (b - a) == timedelta(hours=8)
    sat = sch.window(FIVE, date(2024, 12, 28), MSK, cal)          # суббота 28.12.2024 — рабочая по переносу
    assert sat is not None and sat[0].astimezone(MSK).hour == 8
    shift = {**FIVE, "use_calendar": False}
    assert sch.window(shift, date(2026, 1, 5), MSK, cal) is not None and "без производственного" in sch.label(shift)
    tiny = {**FIVE, "start_time": "09:00", "end_time": "10:30", "break_minutes": 30}
    a, b = sch.window(tiny, date(2026, 11, 3), MSK, cal)
    assert (b - a) == timedelta(minutes=90)
    assert pc.day_note(cal["2026-01-07"]).startswith("праздник: Рождество")
    assert "на 1 час" in pc.day_note(cal["2026-11-03"])
