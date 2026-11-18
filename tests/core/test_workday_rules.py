"""Ядро таймера рабочего дня (D-46, ответы владельца на A-49 пп. 2–5): переходы, порядок времени, подсчёт
«сколько натикало» без округления, перерывы только по кнопке, фактическое начало дня после согласования, окно
«Начните рабочий день». Чистые функции.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from vector.core import workday as wd

T0 = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)


def e(seq, event, minutes, seconds=0):
    """Событие журнала через minutes минут (и seconds секунд) от T0."""
    return {"seq": seq, "event": event, "device_time": (T0 + timedelta(minutes=minutes, seconds=seconds)).isoformat()}


def test_transitions_and_time_order():
    """Переходы: не начат → идёт ⇄ перерыв → завершён → (вернулся) идёт; время не раньше предыдущего, день ≤ суток."""
    assert wd.status_of([]) == "not_started"
    wd.check_event([], "start", T0)
    for bad in ("pause", "resume", "finish"):
        with pytest.raises(wd.WorkdayError) as x:
            wd.check_event([], bad, T0)
        assert x.value.code == "invalid_transition"
    ev = [e(1, "start", 0)]
    with pytest.raises(wd.WorkdayError) as x:
        wd.check_event(ev, "start", T0 + timedelta(minutes=1))
    assert x.value.code == "invalid_transition"
    with pytest.raises(wd.WorkdayError) as x:
        wd.check_event(ev, "pause", T0 - timedelta(seconds=1))
    assert x.value.code == "time_order"
    with pytest.raises(wd.WorkdayError) as x:
        wd.check_event(ev, "finish", T0 + timedelta(hours=24, seconds=1))
    assert x.value.code == "day_too_long"
    with pytest.raises(wd.WorkdayError) as x:
        wd.check_event(ev, "lunch", T0)
    assert x.value.code == "invalid_event"
    ev += [e(2, "finish", 60)]
    assert wd.status_of(ev) == "finished"
    wd.check_event(ev, "resume", T0 + timedelta(minutes=90))                  # вернулся к работе после «Завершить»
    with pytest.raises(wd.WorkdayError):
        wd.check_event(ev, "pause", T0 + timedelta(minutes=90))


def test_day_state_counts_seconds_without_rounding_and_breaks_only_by_button():
    """Рабочее время — сумма отрезков по секундам; перерыв — только между «Перерыв» и «Продолжить» (и после
    «Завершить» до возвращения); идущий таймер считается до now."""
    ev = [e(1, "start", 0, 7), e(2, "pause", 120), e(3, "resume", 155, 30), e(4, "finish", 300)]
    st = wd.day_state(ev, T0 + timedelta(hours=8))
    assert st["status"] == "finished" and st["breaks"] == 1
    assert st["worked_seconds"] == 120 * 60 - 7 + (300 - 155) * 60 - 30
    assert st["break_seconds"] == 35 * 60 + 30
    running = wd.day_state(ev[:3], T0 + timedelta(minutes=200))
    assert running["status"] == "working" and running["worked_seconds"] == 120 * 60 - 7 + (200 - 155) * 60 - 30
    assert wd.clock_label(running["worked_seconds"]) == "02:44:23"
    assert wd.duration_label(running["worked_seconds"]) == "2 ч 44 мин" and wd.duration_label(59) == "0 мин"
    # Никаких «угаданных» перерывов: час без действий при идущем таймере — работа.
    assert wd.day_state([e(1, "start", 0)], T0 + timedelta(hours=3))["break_seconds"] == 0


def test_approved_start_moves_first_segment():
    """A-49 п. 4: день начался в 8, «Начать» нажали в 10 — после согласования таймер сразу показывает 2 часа;
    согласованное время не раньше нажатия ничего не меняет."""
    ev = [e(1, "start", 0)]
    st = wd.day_state(ev, T0, approved_start=(T0 - timedelta(hours=2)).isoformat())
    assert (st["worked_seconds"], st["extra_seconds"]) == (7200, 7200)
    assert st["started_at"] == T0 - timedelta(hours=2) and st["pressed_at"] == T0
    assert wd.day_state(ev, T0, approved_start=(T0 + timedelta(minutes=5)).isoformat())["extra_seconds"] == 0


def test_shift_from_day_minutes_and_night_shift():
    """Смена для отчёта — минута события (секунды отбрасываются), перерыв — целые минуты; ночная смена — конец после
    полуночи (> 1440); не завершённый день — None."""
    ev = [e(1, "start", 0, 59), e(2, "pause", 60), e(3, "resume", 90, 45), e(4, "finish", 15 * 60)]
    st = wd.day_state(ev, T0 + timedelta(days=1))
    s = wd.shift_from_day(st, date(2026, 11, 16), timezone.utc)
    assert s == {"shift_start": 600, "shift_end": 600 + 15 * 60, "break_minutes": 30}
    assert wd.shift_from_day(wd.day_state(ev[:3], T0 + timedelta(hours=3)), date(2026, 11, 16), timezone.utc) is None
    msk = timezone(timedelta(hours=3))
    assert wd.shift_from_day(st, date(2026, 11, 16), msk)["shift_end"] == 13 * 60 + 15 * 60


def test_start_request_rules_reason_and_prompt():
    """Фактическое начало: раньше нажатия, не раньше чем за 16 ч, только сегодняшний день; причина обязательна;
    окно «Начните рабочий день» — пока таймер сегодня не запускали и окно не отложено «Позже»."""
    wd.check_start_request(T0, T0 - timedelta(hours=2), T0 + timedelta(hours=1))
    for req, now, code in ((T0, T0, "not_earlier"), (T0 - timedelta(hours=16, seconds=1), T0, "too_early"),
                           (T0 - timedelta(hours=1), T0 + timedelta(hours=24, seconds=1), "too_late")):
        with pytest.raises(wd.WorkdayError) as x:
            wd.check_start_request(T0, req, now)
        assert x.value.code == code
    with pytest.raises(wd.WorkdayError) as x:
        wd.check_start_request(None, T0, T0)
    assert x.value.code == "not_started"
    assert wd.parse_reason("  Был на объекте ") == "Был на объекте"
    assert wd.parse_reason("", required=False) is None
    for bad, code in (("", "field_required"), ("x" * 501, "too_long"), (5, "invalid_text")):
        with pytest.raises(wd.WorkdayError) as x:
            wd.parse_reason(bad)
        assert x.value.code == code
    with pytest.raises(wd.WorkdayError):
        wd.parse_dt("2026-11-16T10:00:00")                                    # без пояса — чьё это «10:00»?
    assert wd.need_start_prompt([], dismissed=False) and not wd.need_start_prompt([], dismissed=True)
    assert not wd.need_start_prompt([e(1, "start", 0)], dismissed=False)
