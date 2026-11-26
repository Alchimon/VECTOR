"""Идемпотентность, версии, конкуренция (серверная сторона протокола синхронизации)."""
import threading

import sqlalchemy as sa

from conftest import new_id
from vector.server import db


def test_replay_returns_stored_outcome(world):
    rp = world.login("rp1")
    op, cid = new_id(), new_id()
    a = rp.cmd("customer.create", {"id": cid, "name": "A"}, op_id=op)
    b = rp.cmd("customer.create", {"id": cid, "name": "A"}, op_id=op)
    assert a["status"] == b["status"] == "accepted"
    assert b["replayed"] is True and not a.get("replayed")
    assert a["result"] == b["result"]


def test_operation_id_reuse_with_other_payload_rejected(world):
    rp = world.login("rp1")
    op = new_id()
    rp.cmd("customer.create", {"id": new_id(), "name": "A"}, op_id=op)
    r = rp.cmd("customer.create", {"id": new_id(), "name": "B"}, op_id=op)
    assert (r["status"], r["code"]) == ("rejected", "operation_id_reused")


def test_operation_id_of_other_user_rejected(world):
    rp, rp2 = world.login("rp1"), world.login("rp2")
    op, payload = new_id(), {"id": new_id(), "name": "A"}
    rp.cmd("customer.create", payload, op_id=op)
    r = rp2.cmd("customer.create", payload, op_id=op)
    assert r["code"] == "operation_id_reused"  # чужой исход не раскрывается


def test_conflict_carries_current_server_state(world):
    rp, rp2 = world.login("rp1"), world.login("rp2")
    cid = new_id()
    rp.ok("customer.create", {"id": cid, "name": "Исходное"})
    rp2.ok("customer.update", {"id": cid, "name": "Победитель"}, 1)
    r = rp.cmd("customer.update", {"id": cid, "name": "Проигравший"}, 1)
    assert (r["status"], r["code"]) == ("conflict", "version_conflict")
    assert r["details"]["current"]["name"] == "Победитель" and r["details"]["current"]["version"] == 2


def test_concurrent_updates_one_wins(world):
    tokens = [world.login("rp1").token, world.login("rp2").token]
    cid = new_id()
    world.login("rp1").ok("customer.create", {"id": cid, "name": "X"})
    results, barrier = [], threading.Barrier(2)

    def worker(tok, name):
        barrier.wait()
        results.append(world.svc.handle({"type": "command", "token": tok, "operation_id": new_id(), "command": "customer.update",
                                         "payload": {"id": cid, "name": name}, "expected_version": 1}))
    ts = [threading.Thread(target=worker, args=(tokens[i], f"N{i}")) for i in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(r["status"] for r in results) == ["accepted", "conflict"], results


def test_parallel_duplicate_operation_id_single_effect(world):
    tok = world.login("rp1").token
    req = {"type": "command", "token": tok, "operation_id": new_id(), "command": "customer.create",
           "payload": {"id": new_id(), "name": "Гонка"}}
    results, barrier = [], threading.Barrier(4)

    def worker():
        barrier.wait()
        results.append(world.svc.handle(dict(req)))
    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(r["status"] == "accepted" for r in results), results
    assert sum(1 for r in results if not r.get("replayed")) == 1
    with world.svc.engine.connect() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(db.customers).where(db.customers.c.name == "Гонка")).scalar() == 1
        assert c.execute(sa.select(sa.func.count()).select_from(db.audit_log).where(
            db.audit_log.c.operation_id == req["operation_id"])).scalar() == 1


def test_denied_outcome_is_final_for_operation(world):
    """P-6: отказ — сохранённый исход операции; после выдачи роли нужна НОВАЯ операция."""
    uid = world.make_user("late", None)
    s = world.login("late")
    op, payload = new_id(), {"id": new_id(), "name": "A"}
    assert s.cmd("customer.create", payload, op_id=op)["status"] == "denied"
    world.admin.ok("admin.grant_role", {"user_id": uid, "role": "rp", "reason": "t"})
    r = s.cmd("customer.create", payload, op_id=op)
    assert r["status"] == "denied" and r["replayed"] is True
    assert s.cmd("customer.create", payload)["status"] == "accepted"
