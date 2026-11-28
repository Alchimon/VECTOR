"""Ядро рабочего времени, D-43 (ответы владельца на A-48): ночная смена, пересечение со сменой соседнего дня,
самопроверка и сборка дневного отчёта программой по действиям сотрудника (assemble_report). Чистые функции.
"""
from datetime import date, datetime, timezone

import pytest

from vector.core import worktime as wt

DAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
P1, P2, T1 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", \
    "33333333-3333-4333-8333-333333333333"


def at(hm: str, d: date = DAY) -> str:
    """ISO-время «ЧЧ:ММ» дня d (UTC)."""
    h, m = (int(x) for x in hm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=timezone.utc).isoformat()


def act(hm, command="task.update", label="Изменить задачу", obj="«Схема»", pid=P1, code="П-1", tid=T1, op=None):
    """Одно действие из pull «worktime_activity»."""
    return {"at": at(hm), "operation_id": op, "command": command, "label": label, "object": obj, "project_id": pid,
            "project_code": code, "task_id": tid}


def test_night_shift_is_stored_past_midnight_and_limited_to_a_day():
    """Конец раньше начала — ночная смена до утра следующего дня; равные границы и «24:00» в начале — ошибка."""
    r = wt.parse_shift({"shift_start": "22:00", "shift_end": "06:00", "break_minutes": 30})
    assert (r["shift_start"], r["shift_end"]) == (1320, 1800) and wt.overnight(1320, 1800)
    assert wt.shift_net(1320, 1800, 30) == 450 and wt.time_label(1800) == "06:00"
    assert not wt.overnight(540, 1080)
    with pytest.raises(wt.WorktimeError) as e:
        wt.parse_shift({"shift_start": "09:00", "shift_end": "09:00"})
    assert e.value.code == "shift_order"
    with pytest.raises(wt.WorktimeError):
        wt.parse_shift({"shift_start": "24:00", "shift_end": "06:00"})


def test_shift_overlap_with_neighbour_days():
    """Ночная смена накануне до 06:00 и смена с 05:00 — пересечение; с 06:00 — нет; то же со стороны ночной смены."""
    night = {"work_date": "2026-11-15", "shift_start": 1320, "shift_end": 1800}
    assert "пересекаются" in wt.shift_overlap(night, {"work_date": "2026-11-16", "shift_start": 300, "shift_end": 780})
    assert wt.shift_overlap(night, {"work_date": "2026-11-16", "shift_start": 360, "shift_end": 840}) is None
    nxt = {"work_date": "2026-11-16", "shift_start": 300, "shift_end": 780}
    assert "Исправьте конец смены" in wt.shift_overlap(None, night, nxt)
    assert wt.shift_overlap(None, {"shift_start": None, "shift_end": None}, nxt) is None


def test_self_check_requires_every_item():
    """Самопроверка: все пункты отмечены (True), лишних нет; иначе — какие пункты не отмечены."""
    assert wt.parse_self_check({"shift": True, "lines": True, "hours": True}) == {"shift": True, "lines": True, "hours": True}
    for bad in (None, [], {"shift": True, "lines": True}, {"shift": True, "lines": True, "hours": "да"}):
        with pytest.raises(wt.WorktimeError) as e:
            wt.parse_self_check(bad)
        assert e.value.code == "self_check_required"
    with pytest.raises(wt.WorktimeError) as e:
        wt.parse_self_check({"shift": True, "lines": True, "hours": True, "x": True})
    assert e.value.code == "unknown_field"


def test_assemble_day_shift_break_measured_and_estimated_lines():
    """Без таймера рабочего дня: смена — от первого до последнего действия минута в минуту (без округления, D-46 —
    A-49 п. 2), перерыв не угадывается (только по кнопке, A-49 п. 3); таймер операции — строка «по таймеру»,
    остальное время — «оценка программы» по числу действий; сумма = смена без перерыва; результат проходит правила
    сервера."""
    activity = {
        "actions": [{"at": at("08:52"), "command": "auth.login", "label": "Вход"},
                    act("09:05"), act("09:40"), act("11:10", command="document.add_version", label="Добавить версию",
                                                    obj="Схема.pdf", tid=None),
                    act("14:20", pid=P2, code="П-2", tid=None, label="Изменить проект", obj="П-2"),
                    act("17:31", pid=None, code=None, tid=None, command="customer.update", label="Изменить заказчика",
                        obj="ООО «Ромашка»")],
        "timers": [{"start": at("15:00"), "end": at("16:30"), "project_id": P1, "project_code": "П-1",
                    "label": "Наряд № 3 · Сборка"}]}
    r = wt.assemble_report(DAY, activity, [P1], NOW, tz=timezone.utc)
    assert (r["shift_start"], r["shift_end"], r["break_minutes"]) == (8 * 60 + 52, 17 * 60 + 31, 0)
    net = r["shift_end"] - r["shift_start"] - r["break_minutes"]
    assert sum(ln["minutes"] for ln in r["lines"]) == net == 519
    timer = next(ln for ln in r["lines"] if ln["source"] == "measured")
    assert (timer["minutes"], timer["project_id"], timer["note"]) == (90, P1, "Наряд № 3 · Сборка")
    est = [ln for ln in r["lines"] if ln["source"] == "estimated"]
    assert {(ln["project_id"], ln["task_id"]) for ln in est} == {(P1, T1), (P1, None), (None, None)}
    # Чужой проект (не участник) — строка «не по проекту» с кодом в тексте.
    assert any(ln["project_id"] is None and "П-2: Изменить проект" in ln["note"] for ln in est)
    two = next(ln for ln in est if ln["task_id"] == T1)
    assert "Изменить задачу: «Схема» (×2)" in two["note"]
    assert r["summary"] == {"actions": 5, "measured_minutes": 90, "lines": len(r["lines"]), "workday_minutes": 0}
    assert [ln["line_no"] for ln in r["lines"]] == list(range(1, len(r["lines"]) + 1))
    parsed = wt.parse_report({"shift_start": wt.time_label(r["shift_start"]), "shift_end": wt.time_label(r["shift_end"]),
                              "break_minutes": r["break_minutes"], "lines": [{k: ln[k] for k in (
                                  "project_id", "task_id", "minutes", "note", "source")} for ln in r["lines"]]})
    assert wt.submit_problems(parsed) == []


def test_assemble_uses_local_time_of_offline_actions_and_skips_empty_days():
    """Сделанное без связи попадает в день, когда сделано (время очереди компьютера), а не в день синхронизации;
    день без действий — None; только вход — смена без строк."""
    synced_next_day = {"actions": [{**act("10:00"), "at": at("08:00", date(2026, 11, 17)), "operation_id": "op-1"}]}
    assert wt.assemble_report(DAY, synced_next_day, [P1], NOW, tz=timezone.utc) is None
    r = wt.assemble_report(DAY, synced_next_day, [P1], NOW, tz=timezone.utc, local_times={"op-1": at("10:00")})
    assert (r["shift_start"], r["shift_end"]) == (600, 601) and r["lines"][0]["minutes"] == 1
    assert wt.assemble_report(DAY, {"actions": [], "timers": []}, [P1], NOW, tz=timezone.utc) is None
    only_login = wt.assemble_report(DAY, {"actions": [{"at": at("09:00"), "command": "auth.login"}]}, [P1], NOW, tz=timezone.utc)
    assert only_login["lines"] == [] and only_login["summary"]["actions"] == 0


def test_assemble_merges_too_many_groups_into_other_line():
    """Больше 40 разных работ — последние сливаются в одну строку, всего строк не больше 40."""
    acts = [act(f"{9 + i // 60:02d}:{i % 60:02d}", pid=None, tid=None, code=None, label=f"Действие {i}", obj=None)
            for i in range(0, 1)]
    acts += [{**act(f"{9 + i // 12:02d}:{(i % 12) * 5:02d}"), "task_id": f"{i:08d}-0000-4000-8000-000000000000"}
             for i in range(45)]
    r = wt.assemble_report(DAY, {"actions": acts}, [P1], NOW)
    assert len(r["lines"]) <= wt.MAX_LINES
    assert sum(ln["minutes"] for ln in r["lines"]) <= r["shift_end"] - r["shift_start"] - r["break_minutes"]


def test_assemble_takes_shift_and_breaks_from_workday_timer():
    """D-46: день отмечен таймером рабочего дня — смена и перерыв из него точно («сколько натикало»), строки делятся
    по минуте (без кратности 15), сумма строк = рабочее время по таймеру; сводка помнит минуты таймера."""
    from vector.core import workday as wdy
    ev = [{"seq": 1, "event": "start", "device_time": "2026-11-16T08:03:20+00:00"},
          {"seq": 2, "event": "pause", "device_time": "2026-11-16T12:10:00+00:00"},
          {"seq": 3, "event": "resume", "device_time": "2026-11-16T12:47:30+00:00"},
          {"seq": 4, "event": "finish", "device_time": "2026-11-16T17:01:59+00:00"}]
    shift = wdy.shift_from_day(wdy.day_state(ev, NOW), DAY, timezone.utc)
    assert shift == {"shift_start": 8 * 60 + 3, "shift_end": 17 * 60 + 1, "break_minutes": 37}
    acts = {"actions": [act("09:00"), act("10:00", tid=None, label="Добавить версию"), act("16:00", pid=None, tid=None)]}
    r = wt.assemble_report(DAY, acts, [P1], NOW, tz=timezone.utc, shift=shift)
    assert (r["shift_start"], r["shift_end"], r["break_minutes"]) == (483, 1021, 37)
    assert sum(ln["minutes"] for ln in r["lines"]) == 1021 - 483 - 37 == r["summary"]["workday_minutes"] == 501
    assert any(ln["minutes"] % 15 for ln in r["lines"])                   # без кратности 15 минутам
