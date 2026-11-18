"""Таймер рабочего дня и фактическое начало дня (этап 7, R-20; D-46 — ответы владельца на A-49 пп. 2–4).

Время устройства — по часам компьютера сотрудника (A-49 п. 5); часы сервиса в тестах — FakeClock (2026-11-16 09:00
UTC). Маршрут согласования — как у дневного отчёта (оргструктура D-44/D-45, вне неё — руководители отделов D-42).
"""
from datetime import timedelta

import sqlalchemy as sa

from conftest import new_id
from test_org import org, unit
from test_worktime import notes, pull
from vector.server import db


def review(s):
    """Запросы на фактическое начало, которые сотрудник сессии s решает (чужие из pull)."""
    st = pull(s)
    return [x for x in st["workday_start_requests"] if x["user_id"] != s.user_id]


def at(world, **kw):
    """Время устройства: часы сервиса ± сдвиг, ISO с поясом (+03:00 — как на компьютере в Волгограде)."""
    from datetime import timezone
    return (world.clock.now() + timedelta(**kw)).astimezone(timezone(timedelta(hours=3))).isoformat()


def start(s, world, wid=None, day="2026-11-16", **kw):
    """Нажать «Начать рабочий день»; вернуть сырой ответ."""
    return s.cmd("workday.mark", {"id": wid or new_id(), "work_date": day, "event": "start", "device_time": at(world, **kw)})


def mark(s, world, wid, event, **kw):
    """Нажать кнопку таймера (pause / resume / finish); вернуть сырой ответ."""
    return s.cmd("workday.mark", {"id": wid, "event": event, "device_time": at(world, **kw)})


def test_timer_cycle_exact_time_and_breaks_only_by_button(world):
    """Начать → перерыв → продолжить → завершить → продолжить (вернулся) → завершить: время по секундам устройства,
    журнал неизменяем, переходы и порядок времени проверяет сервер (конфликт, не слияние)."""
    eng = world.login("eng1")
    wid = new_id()
    r = start(eng, world, wid, seconds=-7)
    assert r["status"] == "accepted", r
    d = r["result"]["workday"]
    assert (d["status"], d["work_date"], d["version"], [e["event"] for e in d["events"]]) == ("working", "2026-11-16", 1, ["start"])
    assert d["started_at"].endswith(":59:53.000000Z")               # секунды сохранены — без округления (A-49 п. 2)
    # Второй «Начать» за ту же дату — конфликт (на другом компьютере уже начат).
    assert start(eng, world)["code"] == "already_started"
    # «Продолжить» из «Идёт» — недопустимый переход: видимый конфликт с текущим состоянием.
    r = mark(eng, world, wid, "resume")
    assert (r["status"], r["code"]) == ("conflict", "invalid_transition") and r["details"]["current"]["status"] == "working"
    world.clock.advance(hours=3)
    eng = world.login("eng1")                                    # сеанс простаивал дольше 2 часов — новый вход
    assert mark(eng, world, wid, "pause")["result"]["workday"]["status"] == "paused"
    # Время раньше предыдущего события — конфликт; из будущего — отказ.
    assert mark(eng, world, wid, "resume", hours=-4)["code"] == "time_order"
    assert mark(eng, world, wid, "resume", hours=2)["code"] == "device_time_future"
    world.clock.advance(minutes=40)
    mark(eng, world, wid, "resume")
    world.clock.advance(hours=5)
    eng = world.login("eng1")
    d = mark(eng, world, wid, "finish")["result"]["workday"]
    assert (d["status"], d["version"]) == ("finished", 4) and d["finished_at"]
    world.clock.advance(minutes=30)
    assert mark(eng, world, wid, "resume")["result"]["workday"]["status"] == "working"   # вернулся к работе
    world.clock.advance(minutes=15)
    d = mark(eng, world, wid, "finish")["result"]["workday"]
    assert [e["event"] for e in d["events"]] == ["start", "pause", "resume", "finish", "resume", "finish"]
    # Чужой день трогать нельзя.
    assert mark(world.login("lead1"), world, wid, "resume")["code"] == "not_owner"
    with world.svc.engine.begin() as c, __import__("pytest").raises(sa.exc.DBAPIError):
        c.execute(sa.update(db.workday_events).values(event="pause"))
    st = pull(eng)
    assert [x["id"] for x in st["workdays"]] == [wid] and st["workday_start_requests"] == []


def test_late_start_request_approved_by_unit_head_moves_start(world):
    """A-49 п. 4: начал в 8, нажал «Начать» в 10 → запрос «фактическое начало 08:00» → руководитель подразделения
    (head1, «Отдел АСУ ТП») согласует → день считается с 08:00; уведомления; «четыре глаза»; отклонение — с причиной."""
    o = org(world)
    eng, head = world.login("eng1"), world.login("head1")
    world.clock.advance(hours=1)                                 # 10:00 UTC
    wid = new_id()
    start(eng, world, wid)
    rid = new_id()
    p = {"id": rid, "workday_id": wid, "start_at": at(world, hours=-2), "reason": "Был на объекте, забыл нажать"}
    # Проверки времени: не раньше нажатия, не раньше чем за 16 часов, причина обязательна.
    assert eng.cmd("workday.start_request", {**p, "start_at": at(world, minutes=5)})["code"] == "not_earlier"
    assert eng.cmd("workday.start_request", {**p, "start_at": at(world, hours=-17)})["code"] == "too_early"
    assert eng.cmd("workday.start_request", {**p, "reason": " "})["code"] == "field_required"
    r = eng.ok("workday.start_request", p)["workday_start_request"]
    assert (r["status"], r["work_date"]) == ("pending", "2026-11-16")
    assert notes(world, world.users["head1"], "workday_start_request")
    assert not notes(world, world.users["dir1"], "workday_start_request")     # уходит своему руководителю
    assert eng.cmd("workday.start_request", {**p, "id": new_id()})["code"] == "already_pending"
    # Руководитель видит запрос в pull; руководитель соседнего отдела — нет и решить не может.
    assert [x["id"] for x in review(head)] == [rid]
    other = world.login("head2")
    assert review(other) == []
    assert other.cmd("workday.start_decide", {"id": rid, "decision": "approve"}, expected_version=1)["code"] == "not_your_unit"
    assert eng.cmd("workday.start_decide", {"id": rid, "decision": "approve"}, expected_version=1)["status"] == "denied"
    r = head.ok("workday.start_decide", {"id": rid, "decision": "approve"}, expected_version=1)["workday_start_request"]
    assert (r["status"], r["decided_by"]) == ("approved", world.users["head1"])
    assert head.cmd("workday.start_decide", {"id": rid, "decision": "reject", "comment": "x"},
                    expected_version=2)["code"] == "already_decided"
    d = pull(eng)["workdays"][0]
    assert d["approved_start_at"] == "2026-11-16T08:00:00.000000Z" and notes(world, world.users["eng1"], "workday_start_decided")
    # Второй запрос того же дня — можно (прежний решён); отклонить без причины нельзя, с причиной — да.
    rid2 = new_id()
    eng.ok("workday.start_request", {**p, "id": rid2, "start_at": at(world, hours=-3)})
    assert head.cmd("workday.start_decide", {"id": rid2, "decision": "reject"}, expected_version=1)["code"] == "field_required"
    r = head.ok("workday.start_decide", {"id": rid2, "decision": "reject", "comment": "В 7 вас не было"},
                expected_version=1)["workday_start_request"]
    assert (r["status"], r["decision_comment"]) == ("rejected", "В 7 вас не было")
    assert pull(eng)["workdays"][0]["approved_start_at"] == "2026-11-16T08:00:00.000000Z"   # отказ не меняет день
    # Сутки спустя «сегодняшнего» дня уже нет — согласовать нельзя.
    world.clock.advance(hours=25)
    eng = world.login("eng1")
    assert eng.cmd("workday.start_request", {**p, "id": new_id()})["code"] == "too_late"
    assert o


def test_unit_head_without_role_and_outside_structure(world):
    """Руководитель подразделения без роли (gip1 во главе группы) решает запросы своих сотрудников (D-45); сотрудник
    вне оргструктуры — его запрос получают и решают руководители отделов по роли (D-42)."""
    a, u = world.admin, world.users
    o = org(world)
    grp = unit(a, "department", "Группа ГИП", o["D1"], u["gip1"])
    a.ok("org.member_set", {"user_id": u["eng1"], "unit_id": grp})
    eng, gip = world.login("eng1"), world.login("gip1")
    wid = new_id()
    start(eng, world, wid)
    rid = new_id()
    eng.ok("workday.start_request", {"id": rid, "workday_id": wid, "start_at": at(world, hours=-1), "reason": "Забыл"})
    assert notes(world, u["gip1"], "workday_start_request")
    r = gip.ok("workday.start_decide", {"id": rid, "decision": "approve"}, expected_version=1)["workday_start_request"]
    assert r["authority"] == f"org_head#unit:{grp}"
    # Бухгалтер вне оргструктуры: запрос — руководителям отделов по роли; руководитель без роли его не видит.
    acc = world.login("acc1")
    w2, r2 = new_id(), new_id()
    start(acc, world, w2)
    acc.ok("workday.start_request", {"id": r2, "workday_id": w2, "start_at": at(world, hours=-1), "reason": "Пробки"})
    assert notes(world, u["head1"], "workday_start_request") and notes(world, u["head2"], "workday_start_request")
    assert r2 not in [x["id"] for x in review(gip)]
    assert gip.cmd("workday.start_decide", {"id": r2, "decision": "approve"}, expected_version=1)["code"] == "not_your_unit"
    assert world.login("head2").ok("workday.start_decide", {"id": r2, "decision": "approve"},
                                   expected_version=1)["workday_start_request"]["status"] == "approved"
    # Не руководитель и без роли — отказ по матрице прав.
    assert world.login("rp1").cmd("workday.start_decide", {"id": r2, "decision": "approve"},
                                  expected_version=2)["status"] == "denied"
