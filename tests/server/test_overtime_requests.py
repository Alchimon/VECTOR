"""A-55/A-56: explicit overtime/off-day requests and separate manager decisions; never infer activity or calculate money."""
import sqlalchemy as sa

from conftest import new_id
from vector.server import db


def test_overtime_request_requires_reason_and_manager_decision_is_audited(world):
    """Preapproval is requested before its interval; author cannot decide their own request; event history is append-only."""
    unit_id = new_id()
    world.admin.ok("org.unit_create", {"id": unit_id, "kind": "department", "name": "Испытания",
                                       "head_user_id": world.users["head1"]})
    world.admin.ok("org.member_set", {"user_id": world.users["eng1"], "unit_id": unit_id})
    employee = world.login("eng1")
    rid = new_id()
    request = {"id": rid, "work_date": "2026-11-17", "start_at": "2026-11-17T01:00:00+03:00",
               "end_at": "2026-11-17T03:00:00+03:00", "minutes": 120, "kind": "overtime",
               "mode": "planned", "reason": "Срочный выпуск чертежей"}
    r = employee.ok("overtime.request", request)["overtime_request"]
    assert (r["status"], r["minutes"], "amount" in r, "rate" in r) == ("pending", 120, False, False)
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


def test_urgent_overtime_requires_reason_and_must_be_finished(world):
    """A retrospective urgent record cannot be submitted before end time or without an explanation."""
    employee = world.login("eng1")
    base = {"id": new_id(), "work_date": "2026-11-16", "start_at": "2026-11-16T17:00:00+03:00",
            "end_at": "2026-11-16T19:00:00+03:00", "minutes": 120, "kind": "overtime", "mode": "urgent_after"}
    assert employee.cmd("overtime.request", base)["code"] == "field_required"
    base["reason"] = "Немедленное устранение дефекта"
    assert employee.cmd("overtime.request", base)["code"] == "not_finished"
