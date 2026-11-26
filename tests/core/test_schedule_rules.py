"""Ядро графиков работы (D-47, ответ владельца на A-51 п. 1): проверка полей, подписи, окно рабочего дня по часам
компьютера (в том числе ночной график через полночь), какой график действует для сотрудника. Чистые функции."""
from datetime import date, datetime, timedelta, timezone

import pytest

from vector.core import schedule as sch

MSK = timezone(timedelta(hours=3))
S = {"id": "s1", "name": "Пятидневка", "start_time": "08:00", "end_time": "17:00", "break_minutes": 60,
     "weekdays": "1111100", "is_default": False, "active": True}


def test_check_and_labels():
    """Проверка полей формы и подписи правила."""
    assert sch.check_schedule({**S, "name": "  Пятидневка "})["name"] == "Пятидневка"
    for bad, code in (({"start_time": "8:0"}, "invalid_time"), ({"start_time": "25:00"}, "invalid_time"),
                      ({"start_time": 800}, "invalid_time"), ({"end_time": "08:00"}, "same_time"),
                      ({"break_minutes": True}, "invalid_break"), ({"break_minutes": 241}, "invalid_break"),
                      ({"end_time": "08:30", "break_minutes": 30}, "break_too_long"),
                      ({"weekdays": "111110x"}, "invalid_weekdays"), ({"weekdays": "0000000"}, "no_weekdays"),
                      ({"name": ""}, "field_required"), ({"name": "x" * 121}, "too_long")):
        with pytest.raises(sch.ScheduleError) as x:
            sch.check_schedule({**S, **bad})
        assert x.value.code == code, bad
    assert sch.label(S) == "08:00–17:00, пн–пт, перерыв 60 мин"
    assert sch.weekdays_label("1010100") == "пн, ср, пт" and sch.weekdays_label("1111111") == "ежедневно"
    assert sch.weekdays_label("1101111") == "пн, вт, чт–вс" and sch.weekdays_label("0000011") == "сб, вс"
    night = {**S, "start_time": "22:00", "end_time": "06:00", "break_minutes": 0}
    assert sch.is_night(night) and sch.length_minutes(night) == 480
    assert sch.label(night) == "22:00–06:00 (ночной — до утра), пн–пт, без перерыва"
    assert sch.label(None) == "учёт рабочего времени не ведётся"
    assert sch.full_label(S).startswith("Пятидневка: 08:00")


def test_window_by_device_clock_and_night():
    """Окно рабочего дня — по часам компьютера сотрудника; выходной — None; ночной заканчивается утром."""
    mon, sun = date(2026, 11, 16), date(2026, 11, 22)
    a, b = sch.window(S, mon, MSK)
    assert (a, b) == (datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc), datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc))
    assert sch.window(S, sun, MSK) is None and sch.window(None, mon, MSK) is None
    a, b = sch.window({**S, "start_time": "22:00", "end_time": "06:00"}, mon, MSK)
    assert b - a == timedelta(hours=8) and b.astimezone(MSK).date() == date(2026, 11, 17)
    assert sch.tz_of("2026-11-16T10:00:00+03:00") == MSK and sch.tz_of("x") is None and sch.tz_of(None) is None


def test_resolve_order():
    """Свой → подразделения (вверх по цепочке) → «по умолчанию» → встроенный; архивный пропускается; exempt."""
    parent = {"d1": "s", "s": "f", "f": None}
    dflt = {**S, "id": "def", "name": "Обычный", "is_default": True}
    unit_s = {**S, "id": "us"}
    own = {**S, "id": "own"}
    arch = {**S, "id": "arch", "active": False}
    all_s = [dflt, unit_s, own, arch]
    links = [{"target_kind": "unit", "target_id": "s", "schedule_id": "us"}]
    assert sch.resolve("u1", "d1", parent, links, all_s, {"s": "Дирекция"})["source_label"] == "подразделения «Дирекция»"
    r = sch.resolve("u1", "d1", parent, links + [{"target_kind": "user", "target_id": "u1", "schedule_id": "own"}], all_s)
    assert (r["source"], r["schedule"]["id"]) == ("user", "own")
    r = sch.resolve("u1", "d1", parent, [{"target_kind": "unit", "target_id": "d1", "schedule_id": "arch"}], all_s)
    assert r["source"] == "default"
    r = sch.resolve("u1", None, parent, [{"target_kind": "user", "target_id": "u1", "exempt": True}], all_s)
    assert (r["exempt"], r["schedule"]) == (True, None)
    r = sch.resolve("u1", None, {}, [], [])
    assert r["source"] == "builtin" and r["schedule"]["start_time"] == "09:00"
    # Круг в данных (не должен возникать) не зацикливает.
    assert sch.resolve("u1", "a", {"a": "b", "b": "a"}, [], [dflt])["source"] == "default"
