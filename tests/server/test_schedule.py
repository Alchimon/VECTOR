"""Графики работы и забытое «Завершить» (этап 7, R-20; D-47 — ответы владельца на A-51).

Часы сервиса — FakeClock (2026-11-16 09:00 UTC, понедельник); компьютер сотрудника — +03:00 (Волгоград), так что
09:00 UTC = 12:00 по его часам. Оргструктура — как в test_org.org (eng1 в «Отделе АСУ ТП» у head1).
"""

from conftest import new_id
from test_org import org
from test_workday import at, mark, start
from test_worktime import notes, pull


def sched(admin, name="Пятидневка", start_time="08:00", end_time="17:00", brk=60, weekdays="1111100", sid=None, **kw):
    """Создать график от имени администратора; вернуть его id."""
    sid = sid or new_id()
    admin.ok("schedule.save", {"id": sid, "name": name, "start_time": start_time, "end_time": end_time,
                               "break_minutes": brk, "weekdays": weekdays, **kw})
    return sid


def my(s):
    """Действующий график сотрудника сессии s (из pull)."""
    return pull(s)["workday_schedule"]


def test_schedules_admin_only_with_checks_and_single_default(world):
    """Создать / изменить с версией; проверки полей; «по умолчанию» — один; в архив — не «по умолчанию»; не
    администратор — отказ; все видят справочник графиков."""
    a = world.admin
    eng = world.login("eng1")
    base = {"id": new_id(), "name": "X", "start_time": "08:00", "end_time": "17:00", "break_minutes": 60,
            "weekdays": "1111100"}
    assert eng.cmd("schedule.save", base)["status"] == "denied"
    for bad, code in (({"start_time": "8"}, "invalid_time"), ({"end_time": "24:00"}, "invalid_time"),
                      ({"end_time": "08:00"}, "same_time"), ({"break_minutes": 600}, "invalid_break"),
                      ({"start_time": "08:00", "end_time": "08:30", "break_minutes": 30}, "break_too_long"),
                      ({"weekdays": "0000000"}, "no_weekdays"), ({"weekdays": "11111"}, "invalid_weekdays"),
                      ({"name": " "}, "field_required")):
        assert a.cmd("schedule.save", {**base, **bad})["code"] == code, bad
    # Пока своих графиков нет — встроенный 09:00–18:00.
    assert my(eng)["source"] == "builtin" and my(eng)["schedule"]["start_time"] == "09:00"
    s1 = sched(a, is_default=True)
    assert my(eng)["source"] == "default" and my(eng)["label"].startswith("Пятидневка: 08:00–17:00, пн–пт")
    assert a.cmd("schedule.save", {**base, "name": "пятидневка"})["code"] == "name_taken"
    s2 = sched(a, "Ночной", "22:00", "06:00", 30, "1111111", is_default=True)
    lst = {x["id"]: x for x in pull(eng)["work_schedules"]}
    assert (lst[s1]["is_default"], lst[s1]["version"], lst[s2]["is_default"]) == (False, 2, True)
    assert "ночной" in lst[s2]["label"] and "ежедневно" in lst[s2]["label"]
    # Снять «по умолчанию», не отметив другой, нельзя; в архив «по умолчанию» — нельзя; обычный — можно.
    p2 = {"id": s2, "name": "Ночной", "start_time": "22:00", "end_time": "06:00", "break_minutes": 30, "weekdays": "1111111"}
    assert a.cmd("schedule.save", {**p2, "is_default": False}, expected_version=1)["code"] == "default_required"
    assert a.cmd("schedule.save", {**p2, "is_default": True, "active": False}, expected_version=1)["code"] == "default_archived"
    p1 = {"id": s1, "name": "Пятидневка", "start_time": "08:00", "end_time": "17:00", "break_minutes": 60,
          "weekdays": "1111100"}
    assert a.cmd("schedule.save", {**p1, "active": False}, expected_version=1)["code"] == "version_conflict"
    r = a.ok("schedule.save", {**p1, "active": False}, expected_version=2)["work_schedule"]
    assert (r["active"], r["version"]) == (False, 3)


def test_assign_unit_user_inheritance_and_exempt(world):
    """График подразделения действует на вложенные подразделения; свой — важнее; «учёт не ведётся» — только
    сотруднику; в архиве — не назначается; снять — вернуться к графику выше; сотрудник получает уведомление."""
    a, u = world.admin, world.users
    o = org(world)
    eng = world.login("eng1")
    s_def = sched(a, "Обычный", "09:00", "18:00", is_default=True)
    s_ing = sched(a, "Инженерный", "08:00", "17:00")
    s_own = sched(a, "Свой", "10:00", "19:00")
    assert a.cmd("schedule.assign", {"target_kind": "user", "target_id": u["eng1"], "schedule_id": s_own},
                 )["status"] == "accepted"
    assert eng.cmd("schedule.assign", {"target_kind": "user", "target_id": u["eng1"]})["status"] == "denied"
    a.ok("schedule.assign", {"target_kind": "unit", "target_id": o["S"], "schedule_id": s_ing})
    m = my(eng)
    assert (m["source"], m["schedule"]["id"]) == ("user", s_own) and notes(world, u["eng1"], "work_schedule")
    # Снять свой — действует график «Инженерной дирекции» (вышестоящей для «Отдела АСУ ТП»).
    a.ok("schedule.assign", {"target_kind": "user", "target_id": u["eng1"]})
    m = my(eng)
    assert (m["source"], m["schedule"]["id"]) == ("unit", s_ing) and "Инженерная дирекция" in m["source_label"]
    assert my(world.login("acc1"))["schedule"]["id"] == s_def          # вне оргструктуры — «по умолчанию»
    assert a.cmd("schedule.assign", {"target_kind": "unit", "target_id": o["S"], "exempt": True})["code"] == "exempt_user_only"
    a.ok("schedule.assign", {"target_kind": "user", "target_id": u["eng1"], "exempt": True})
    m = my(eng)
    assert (m["exempt"], m["schedule"]) == (True, None) and "не ведётся" in m["label"]
    # Архивный график не назначается; назначение на график, ушедший в архив, пропускается.
    a.ok("schedule.save", {"id": s_ing, "name": "Инженерный", "start_time": "08:00", "end_time": "17:00",
                           "break_minutes": 60, "weekdays": "1111100", "active": False}, expected_version=1)
    assert a.cmd("schedule.assign", {"target_kind": "user", "target_id": u["eng1"], "schedule_id": s_ing})["code"] == "archived"
    a.ok("schedule.assign", {"target_kind": "user", "target_id": u["eng1"]})
    assert my(eng)["schedule"]["id"] == s_def
    links = pull(eng)["work_schedule_links"]
    assert [(x["target_kind"], x["schedule_id"]) for x in links] == [("unit", s_ing)]


def test_actual_start_is_bounded_by_schedule(world):
    """A-51 п. 1: фактическое начало — не раньше начала рабочего дня по графику (по часам компьютера сотрудника);
    нажал «Начать» до начала по графику — фактическое начало не нужно; нерабочий по графику день и «учёт не
    ведётся» — нельзя."""
    a, u = world.admin, world.users
    org(world)
    s = sched(a, "Пятидневка", "08:00", "17:00", is_default=True)
    eng = world.login("eng1")
    wid = new_id()
    start(eng, world, wid)                                      # 12:00 по часам компьютера (+03:00)
    p = {"id": new_id(), "workday_id": wid, "reason": "Был на объекте"}
    assert eng.cmd("workday.start_request", {**p, "start_at": at(world, hours=-4, minutes=-1)})["code"] == "before_schedule"
    r = eng.ok("workday.start_request", {**p, "start_at": at(world, hours=-4)})["workday_start_request"]
    assert (r["kind"], r["requested_start_at"]) == ("start", "2026-11-16T05:00:00.000000Z")
    # Выходной по графику (воскресенье): фактическое начало не указывается.
    a.ok("schedule.save", {"id": s, "name": "Пятидневка", "start_time": "08:00", "end_time": "17:00",
                           "break_minutes": 60, "weekdays": "1111110", "is_default": True}, expected_version=1)
    world.clock.advance(days=6)                                  # 2026-11-22, воскресенье
    eng = world.login("eng1")
    w2 = new_id()
    start(eng, world, w2, day="2026-11-22")
    assert eng.cmd("workday.start_request", {**p, "id": new_id(), "workday_id": w2,
                                             "start_at": at(world, hours=-1)})["code"] == "not_schedule_day"
    # Понедельник: «Начать» в 07:30 по часам компьютера (до начала по графику) — фактическое начало не нужно.
    world.clock.advance(days=1, hours=-7, minutes=30)            # 2026-11-23 04:30 UTC = 07:30 (+03:00)
    eng = world.login("eng1")
    w3 = new_id()
    start(eng, world, w3, day="2026-11-23")
    world.clock.advance(hours=1)
    assert eng.cmd("workday.start_request", {**p, "id": new_id(), "workday_id": w3,
                                             "start_at": at(world, hours=-2)})["code"] == "not_late"
    a = world.login("admin")                                     # сеанс администратора простаивал — новый вход
    a.ok("schedule.assign", {"target_kind": "user", "target_id": u["eng1"], "exempt": True})
    assert eng.cmd("workday.start_request", {**p, "id": new_id(), "workday_id": w3,
                                             "start_at": at(world, minutes=-30)})["code"] == "no_schedule"


def test_forgotten_finish_auto_and_actual_end_approved_by_head(world):
    """A-51 п. 5: забыли «Завершить» — программа завершила день автоматически (пометка auto); повтор с другого
    компьютера принимается без изменений; «Работал дольше» — фактическое окончание на согласование тому же
    руководителю (A-51 п. 6); согласовано — последний отрезок продлён; после ручного «Завершить» запрос не нужен."""
    o = org(world)
    eng, head = world.login("eng1"), world.login("head1")
    wid = new_id()
    start(eng, world, wid)
    assert eng.cmd("workday.mark", {"id": wid, "event": "pause", "device_time": at(world), "auto": "closed"})["code"] == "invalid_auto"
    world.clock.advance(hours=1)
    eng = world.login("eng1")
    p = {"id": new_id(), "workday_id": wid, "end_at": at(world), "reason": "Задержался на объекте"}
    assert eng.cmd("workday.end_request", p)["code"] == "not_finished"
    # Программа завершила день «в конце по графику» (сейчас минус 30 минут без ответа).
    r = eng.ok("workday.mark", {"id": wid, "event": "finish", "device_time": at(world, minutes=-30), "auto": "no_answer"})
    d = r["workday"]
    assert d["status"] == "finished" and d["events"][-1]["auto"] == "no_answer"
    # Второй компьютер тоже «завершил» — принято без изменений (конфликта нет).
    r = eng.ok("workday.mark", {"id": wid, "event": "finish", "device_time": at(world), "auto": "closed"})
    assert len(r["workday"]["events"]) == 2 and r["workday"]["version"] == d["version"]
    # Проверки фактического окончания: позже авто-завершения, не в будущем, причина обязательна.
    assert eng.cmd("workday.end_request", {**p, "end_at": at(world, minutes=-40)})["code"] == "not_later"
    assert eng.cmd("workday.end_request", {**p, "end_at": at(world, minutes=10)})["code"] == "in_future"
    assert eng.cmd("workday.end_request", {**p, "reason": ""})["code"] == "field_required"
    rq = eng.ok("workday.end_request", p)["workday_end_request"]
    assert (rq["kind"], rq["status"]) == ("end", "pending") and notes(world, world.users["head1"], "workday_end_request")
    assert eng.cmd("workday.end_request", {**p, "id": new_id()})["code"] == "already_pending"
    other = world.login("head2")
    assert [x["id"] for x in pull(head)["workday_end_requests"]] == [p["id"]] and pull(other)["workday_end_requests"] == []
    assert other.cmd("workday.end_decide", {"id": p["id"], "decision": "approve"}, expected_version=1)["code"] == "not_your_unit"
    assert head.cmd("workday.end_decide", {"id": p["id"], "decision": "reject"}, expected_version=1)["code"] == "field_required"
    r = head.ok("workday.end_decide", {"id": p["id"], "decision": "approve"}, expected_version=1)["workday_end_request"]
    assert r["status"] == "approved" and notes(world, world.users["eng1"], "workday_end_decided")
    d = pull(eng)["workdays"][0]
    assert d["approved_end_at"] == "2026-11-16T10:00:00.000000Z"
    from vector.core import workday as wd
    st = wd.day_state(d["events"], world.clock.now(), d["approved_start_at"], d["approved_end_at"])
    assert st["worked_seconds"] == 3600 and st["extra_end_seconds"] == 1800
    # Ручное «Завершить» — фактическое окончание не нужно: вернулся — «Продолжить».
    eng.ok("workday.mark", {"id": wid, "event": "resume", "device_time": at(world)})
    world.clock.advance(minutes=5)
    eng.ok("workday.mark", {"id": wid, "event": "finish", "device_time": at(world)})
    world.clock.advance(minutes=5)
    assert eng.cmd("workday.end_request", {**p, "id": new_id(), "end_at": at(world)})["code"] == "not_auto"
    # Сутки спустя — поздно.
    w2 = new_id()
    world.clock.advance(days=1)
    eng = world.login("eng1")
    start(eng, world, w2, day="2026-11-17")
    world.clock.advance(minutes=10)
    mark(eng, world, w2, "pause")
    eng.ok("workday.mark", {"id": w2, "event": "finish", "device_time": at(world), "auto": "closed"})
    world.clock.advance(hours=25)
    eng = world.login("eng1")
    assert eng.cmd("workday.end_request", {**p, "id": new_id(), "workday_id": w2,
                                           "end_at": at(world, hours=-24, minutes=-30)})["code"] == "too_late"
    assert o
