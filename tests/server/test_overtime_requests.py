"""A-55/A-56: explicit overtime/off-day requests and separate manager decisions; never infer activity or calculate money."""
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa

from conftest import new_id
from vector.server import db


def test_overtime_request_requires_reason_and_manager_decision_is_audited(world):
    """Preapproval is requested before its interval; author cannot decide their own request; event history is append-only."""
    unit_id = new_id()
    world.admin.ok("org.unit_create", {"id": unit_id, "kind": "department", "name": "Испытания",
                                       "head_user_id": world.users["head1"]})
    world.admin.ok("org.member_set", {"user_id": world.users["eng1"], "unit_id": unit_id})
    world.admin.ok("overtime.period_set", {"mode": "month"})
    employee = world.login("eng1")
    rid = new_id()
    request = {"id": rid, "work_date": "2026-11-17", "start_at": "2026-11-17T01:00:00+03:00",
               "end_at": "2026-11-17T03:00:00+03:00", "minutes": 120, "kind": "overtime",
               "mode": "planned", "reason": "Срочный выпуск чертежей"}
    r = employee.ok("overtime.request", request)["overtime_request"]
    assert (r["status"], r["minutes"], "amount" in r, "rate" in r) == ("pending", 120, False, False)
    assert r["period"] == {"mode": "month", "start": "2026-11-01", "end": "2026-11-30", "config_version": 1}
    # Changing the company default must not rewrite this request's period snapshot.
    world.admin.ok("overtime.period_set", {"mode": "quarter"}, expected_version=1)
    assert employee.cmd("overtime.decide", {"id": rid, "decision": "approve"}, expected_version=1)["status"] == "denied"
    manager = world.login("head1")
    approved = manager.ok("overtime.decide", {"id": rid, "decision": "approve"}, expected_version=1)["overtime_request"]
    assert (approved["status"], approved["version"]) == ("approved", 2)
    assert approved["authority"].startswith("org_head")
    assert employee.cmd("overtime.decide", {"id": rid, "decision": "reject", "comment": "x"},
                        expected_version=2)["status"] == "denied"
    pulled = employee.query("pull")["result"]["overtime_requests"]
    assert [x["id"] for x in pulled] == [rid]
    assert [x["id"] for x in manager.query("pull")["result"]["overtime_requests"]] == [rid]
    assert world.login("rp1").query("pull")["result"]["overtime_requests"] == []
    with world.svc.engine.connect() as conn:
        events = conn.execute(sa.select(db.overtime_events).where(db.overtime_events.c.request_id == rid)
                              .order_by(db.overtime_events.c.seq)).all()
        assert [e.event for e in events] == ["created", "approved"]
        assert all(e.authority for e in events)
    closed = world.admin.ok("overtime.period_close", {"id": new_id(), "user_id": world.users["eng1"],
        "anchor_date": "2026-11-17", "reason": "Закрытие квартала"})["overtime_period_closure"]
    assert (closed["mode"], closed["revision"], closed["period_start"], closed["period_end"],
            closed["minutes_by_kind"]["overtime"], closed["total_minutes"]) == (
                "quarter", 1, "2026-10-01", "2026-12-31", 120, 120)
    assert len(closed["content_sha256"]) == 64
    corrected = world.admin.ok("overtime.period_close", {"id": new_id(), "user_id": world.users["eng1"],
        "anchor_date": "2026-11-17", "reason": "Исправлена первичная проверка", "supersedes_id": closed["id"]})[
            "overtime_period_closure"]
    assert (corrected["revision"], corrected["supersedes_id"], corrected["snapshot"]["total_minutes"]) == (
        2, closed["id"], 120)
    assert world.login("rp1").query("pull")["result"]["overtime_closures"] == []


def test_company_must_select_accounting_period_before_requests(world):
    """Missing company configuration fails closed instead of silently assuming a period."""
    employee = world.login("eng1")
    payload = {"id": new_id(), "work_date": "2026-11-17", "start_at": "2026-11-17T18:00:00+03:00",
               "end_at": "2026-11-17T19:00:00+03:00", "minutes": 60, "kind": "overtime",
               "mode": "planned", "reason": "Подготовка испытаний"}
    assert employee.cmd("overtime.request", payload)["code"] == "period_not_configured"


def test_daily_overtime_cap_rejects_excess_at_creation(world):
    """The worker cannot reserve more than four overtime hours in one calendar day."""
    world.admin.ok("overtime.period_set", {"mode": "month"})
    employee = world.login("eng1")
    base = {"work_date": "2026-11-17", "start_at": "2026-11-17T18:00:00+03:00",
            "end_at": "2026-11-17T23:00:00+03:00", "minutes": 241, "kind": "overtime",
            "mode": "planned", "reason": "Испытание дневного ограничения"}
    result = employee.cmd("overtime.request", {"id": new_id(), **base})
    assert result["code"] == "daily_overtime_limit"


def test_annual_overtime_cap_rejects_reservation(world):
    """Without validated agreement/HR scope, annual overtime is conservatively capped at 120 hours."""
    world.admin.ok("overtime.period_set", {"mode": "month"})
    user_id = world.users["eng1"]
    first = date(2026, 11, 17)
    rows = []
    for index in range(30):
        day = first + timedelta(days=index)
        start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        rows.append({"id": new_id(), "user_id": user_id, "work_date": day, "start_at": start,
                     "end_at": start + timedelta(hours=4), "minutes": 240, "kind": "overtime", "mode": "planned",
                     "reason": "Existing approved overtime", "status": "approved", "created_at": world.clock.now(),
                     "decided_by": world.users["head1"], "decided_at": world.clock.now(),
                     "decision_comment": None, "authority": "test", "version": 2})
    with world.svc.engine.begin() as conn:
        conn.execute(db.overtime_requests.insert(), rows)
    employee = world.login("eng1")
    result = employee.cmd("overtime.request", {"id": new_id(), "work_date": "2026-12-17",
        "start_at": "2026-12-17T18:00:00+03:00", "end_at": "2026-12-17T19:00:00+03:00", "minutes": 60,
        "kind": "overtime", "mode": "planned", "reason": "Испытание годового ограничения"})
    assert result["code"] == "annual_overtime_limit"


def test_period_close_is_blocked_by_pending_decision(world):
    """A period cannot be closed while any of this employee's overtime requests still awaits a manager decision."""
    world.admin.ok("overtime.period_set", {"mode": "month"})
    employee = world.login("eng1")
    employee.ok("overtime.request", {"id": new_id(), "work_date": "2026-11-17",
        "start_at": "2026-11-17T18:00:00+03:00", "end_at": "2026-11-17T19:00:00+03:00", "minutes": 60,
        "kind": "overtime", "mode": "planned", "reason": "Сверка перед выпуском"})
    result = world.admin.cmd("overtime.period_close", {"id": new_id(), "user_id": world.users["eng1"],
        "anchor_date": "2026-11-17", "reason": "Не закрывать до решения"})
    assert result["code"] == "pending_requests"


def test_urgent_overtime_requires_reason_and_must_be_finished(world):
    """A retrospective urgent record cannot be submitted before end time or without an explanation."""
    world.admin.ok("overtime.period_set", {"mode": "month"})
    employee = world.login("eng1")
    base = {"id": new_id(), "work_date": "2026-11-16", "start_at": "2026-11-16T17:00:00+03:00",
            "end_at": "2026-11-16T19:00:00+03:00", "minutes": 120, "kind": "overtime", "mode": "urgent_after"}
    assert employee.cmd("overtime.request", base)["code"] == "field_required"
    base["reason"] = "Немедленное устранение дефекта"
    assert employee.cmd("overtime.request", base)["code"] == "not_finished"
