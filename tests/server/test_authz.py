"""Права в момент эффекта: роль клиента игнорируется, срок/отзыв, generation согласования."""
import pytest

from conftest import new_id
from vector.core.errors import Denied


def test_client_supplied_role_is_ignored(world):
    eng = world.login("eng1")
    r = world.svc.handle({"type": "command", "token": eng.token, "operation_id": new_id(), "command": "customer.create",
                          "payload": {"id": new_id(), "name": "X"}, "role": "admin", "roles": ["gip"]})
    assert r["status"] == "denied"


def test_admin_has_no_implicit_business_rights(world):
    assert world.admin.cmd("customer.create", {"id": new_id(), "name": "X"})["status"] == "denied"


def test_denial_is_audited(world):
    eng = world.login("eng1")
    cid = new_id()
    eng.cmd("customer.create", {"id": cid, "name": "X"})
    import sqlalchemy as sa
    from vector.server import db
    with world.svc.engine.connect() as c:
        rows = c.execute(sa.select(db.audit_log.c.result, db.audit_log.c.command).where(
            db.audit_log.c.actor_user_id == world.users["eng1"], db.audit_log.c.command == "customer.create")).all()
    assert [tuple(r) for r in rows] == [("denied", "customer.create")]


def test_no_token_and_revoked_session(world):
    r = world.svc.handle({"type": "command", "operation_id": new_id(), "command": "customer.create", "payload": {}})
    assert r["status"] == "unauthenticated"
    rp = world.login("rp1")
    world.admin.ok("admin.revoke_sessions", {"user_id": world.users["rp1"], "reason": "увольнение"})
    r = rp.cmd("customer.create", {"id": new_id(), "name": "X"})
    assert (r["status"], r["code"]) == ("unauthenticated", "session_invalid")


def test_revoke_sessions_unknown_user(world):
    r = world.admin.cmd("admin.revoke_sessions", {"user_id": new_id(), "reason": "x"})
    assert r["code"] == "user_not_found"


def test_expired_grant(world):
    uid = world.make_user("temp", None)
    world.admin.ok("admin.grant_role", {"user_id": uid, "role": "rp", "valid_until": "2026-11-16T10:00:00.000000Z", "reason": "замещение"})
    c = world.login("temp")
    assert c.cmd("customer.create", {"id": new_id(), "name": "A"})["status"] == "accepted"
    world.clock.advance(hours=1, minutes=1)
    assert c.cmd("customer.create", {"id": new_id(), "name": "B"})["status"] == "denied"


@pytest.mark.parametrize("bad", ["завтра", "2026-13-01T00:00:00Z", "2026-11-16T08:00:00Z", 12345])
def test_grant_valid_until_validated(world, bad):
    r = world.admin.cmd("admin.grant_role", {"user_id": world.users["eng1"], "role": "rp", "valid_until": bad, "reason": "x"})
    assert r["status"] == "rejected", r


def test_project_role_scope(world):
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    pid = new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "P1", "title": "t"})
    assert rp.cmd("kit.create", {"id": new_id(), "project_id": pid, "code": "K", "title": "K"})["status"] == "denied"
    aid = world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp"})["assignment_id"]
    rp.ok("kit.create", {"id": new_id(), "project_id": pid, "code": "K", "title": "K"})
    world.admin.ok("project.revoke_assignment", {"assignment_id": aid, "reason": "перевод"})
    assert rp.cmd("kit.create", {"id": new_id(), "project_id": pid, "code": "K2", "title": "K2"})["status"] == "denied"


def test_project_read_requires_assignment_for_engineer(world):
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    pid = new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "P1", "title": "t"})
    eng = world.login("eng1")
    assert eng.query("pull")["result"]["projects"] == []
    assert eng.query("history", object_type="project", object_id=pid)["status"] == "denied"
    # Участника назначает ГИП/РП проекта (A-07), не admin.
    r = world.admin.cmd("project.assign", {"project_id": pid, "user_id": world.users["eng1"], "project_role": "participant"})
    assert r["status"] == "denied"
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp"})
    rp.ok("project.assign", {"project_id": pid, "user_id": world.users["eng1"], "project_role": "participant"})
    assert [p["id"] for p in eng.query("pull")["result"]["projects"]] == [pid]


def test_second_gip_requires_explicit_revoke(world):
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    pid = new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "P1", "title": "t"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["gip1"], "project_role": "gip"})
    r = world.admin.cmd("project.assign", {"project_id": pid, "user_id": world.users["head1"], "project_role": "gip"})
    assert r["code"] == "gip_already_assigned"


def _approved_tkp(world):
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    tkp = new_id()
    rp.ok("tkp.create", {"id": tkp, "tender_id": tender, "label": "ТКП-1", "amount": "100.00", "currency": "RUB"})
    head = world.login("head1")
    head.ok("tkp.approve", {"id": tkp}, 1)
    return rp, head, tkp


def test_self_approval_forbidden(world):
    head = world.login("head1")
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    tkp = new_id()
    head.ok("tkp.create", {"id": tkp, "tender_id": tender, "label": "ТКП-2"})
    r = head.cmd("tkp.approve", {"id": tkp}, 1)
    assert (r["status"], r["code"]) == ("denied", "self_approval")


def test_revoke_then_regrant_does_not_revive_approval(world):
    rp, head, tkp = _approved_tkp(world)
    world.admin.ok("admin.revoke_role", {"grant_id": world.grants["head1"], "reason": "проверка"})
    world.admin.ok("admin.grant_role", {"user_id": world.users["head1"], "role": "dept_head", "reason": "восстановление"})
    r = rp.cmd("tkp.record_sent", {"id": tkp, "sent_on": "2026-11-16"}, 2)
    assert (r["status"], r["code"]) == ("rejected", "approval_invalid")
    rp.ok("tkp.reopen", {"id": tkp, "reason": "переутверждение"}, 2)
    head.ok("tkp.approve", {"id": tkp}, 3)
    r = rp.ok("tkp.record_sent", {"id": tkp, "sent_on": "2026-11-16", "channel": "e-mail"}, 4)
    assert r["tkp"]["status"] == "sent_recorded"


def test_approved_tkp_is_frozen(world):
    rp, head, tkp = _approved_tkp(world)
    assert rp.cmd("tkp.update", {"id": tkp, "amount": "200.00"}, 2)["code"] == "tkp_not_draft"


def test_unknown_action_fails_closed(world):
    with pytest.raises(Denied):
        world.svc.policy.rule("document.delete_everything")
