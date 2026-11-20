"""Срез 1 (сервер): заказчик → тендер → ТКП → явное начало РКД → проект → ГИП/РП → комплект.
Порт spikes/reference-core/tests/test_slice1_flow.py на продуктовый сервис; SQLite + PostgreSQL."""
import json

import pytest
import sqlalchemy as sa

from conftest import PWD, new_id
from vector.server import db


def test_end_to_end_with_audit(world):
    rp = world.login("rp1")
    cust, tender, tv = world.won_tender(rp)

    tkp = new_id()
    r = rp.ok("tkp.create", {"id": tkp, "tender_id": tender, "label": "ТКП-15"})
    assert r["tkp"]["amount"] is None  # неизвестно — не ноль

    assert rp.query("pull")["result"]["projects"] == []  # победа не создаёт проект

    pid = new_id()
    r = rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "P-2026-001", "title": "РКД АСУ ТП"})
    assert r["gip_assigned"] is False

    r = rp.cmd("project.start_rkd", {"id": new_id(), "tender_id": tender, "code": "P-2026-002", "title": "дубль"})
    assert (r["status"], r["code"]) == ("rejected", "project_exists_for_tender")

    r = rp.cmd("project.assign", {"project_id": pid, "user_id": world.users["gip1"], "project_role": "gip"})
    assert r["status"] == "denied"
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["gip1"], "project_role": "gip"})

    gip = world.login("gip1")
    gip.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp"})
    rp.ok("kit.create", {"id": new_id(), "project_id": pid, "code": "АТХ", "title": "Автоматизация"})

    h = rp.query("history", object_type="project", object_id=pid)
    assert h["status"] == "accepted"
    start = h["result"][0]
    assert start["command"] == "project.start_rkd"
    assert start["actor_user_id"] == world.users["rp1"]
    assert start["authority"] == f"global_role:rp#grant:{world.grants['rp1']}"
    assert start["details"]["tender_version"] == tv


def test_start_rkd_requires_won_tender(world):
    rp = world.login("rp1")
    cust, tender = new_id(), new_id()
    rp.ok("customer.create", {"id": cust, "name": "ООО Тест"})
    rp.ok("tender.create", {"id": tender, "customer_id": cust, "title": "T"})
    r = rp.cmd("project.start_rkd", {"id": new_id(), "tender_id": tender, "code": "X", "title": "X"})
    assert (r["status"], r["code"]) == ("rejected", "tender_status_not_allowed")


def test_invalid_transition_and_reason(world):
    rp = world.login("rp1")
    cust, tender = new_id(), new_id()
    rp.ok("customer.create", {"id": cust, "name": "ООО Тест"})
    rp.ok("tender.create", {"id": tender, "customer_id": cust, "title": "T"})
    assert rp.cmd("tender.transition", {"id": tender, "to": "won"}, 1)["code"] == "transition_not_allowed"
    assert rp.cmd("tender.transition", {"id": tender, "to": "cancelled"}, 1)["code"] == "reason_required"
    assert rp.cmd("tender.transition", {"id": tender, "to": "cancelled", "reason": "Заказчик отменил"}, 1)["status"] == "accepted"


def test_kit_code_unique_per_project_not_global(world):
    rp = world.login("rp1")
    pids = []
    for i in range(2):
        _, tender, _ = world.won_tender(rp)
        pid = new_id()
        rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": f"P-{i}", "title": "t"})
        world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp"})
        pids.append(pid)
    for pid in pids:
        rp.ok("kit.create", {"id": new_id(), "project_id": pid, "code": "ЭМ", "title": "Э"})
    assert rp.cmd("kit.create", {"id": new_id(), "project_id": pids[0], "code": "ЭМ", "title": "дубль"})["code"] == "kit_code_taken"


def test_project_code_unique(world):
    rp = world.login("rp1")
    _, t1, _ = world.won_tender(rp)
    _, t2, _ = world.won_tender(rp)
    rp.ok("project.start_rkd", {"id": new_id(), "tender_id": t1, "code": "P-1", "title": "t"})
    r = rp.cmd("project.start_rkd", {"id": new_id(), "tender_id": t2, "code": "P-1", "title": "t"})
    assert (r["status"], r["code"]) == ("rejected", "project_code_taken")


@pytest.mark.parametrize("extra,expect,json_amount", [
    ({"amount": "0", "currency": "RUB"}, "accepted", "0.0000"),
    ({"amount": "1500000.1234", "currency": "RUB"}, "accepted", "1500000.1234"),
    ({"amount": "-0.0001", "currency": "RUB"}, "accepted", "-0.0001"),
    ({"amount": "99999999999999.9999", "currency": "RUB"}, "accepted", "99999999999999.9999"),
    ({"amount": "1.12345", "currency": "RUB"}, "amount_too_many_fraction_digits", None),
    ({"amount": "100000000000000", "currency": "RUB"}, "amount_out_of_range", None),
    ({"amount": "10"}, "currency_required", None),
    ({"amount": "", "currency": "RUB"}, "amount_invalid_format", None),
    ({"amount": "1e3", "currency": "RUB"}, "amount_invalid_format", None),
    ({"amount": 1.5, "currency": "RUB"}, "amount_float_forbidden", None),
])
def test_money_on_tkp_boundaries(world, extra, expect, json_amount):
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    tid = new_id()
    r = rp.cmd("tkp.create", {"id": tid, "tender_id": tender, "label": "ТКП", **extra})
    if expect == "accepted":
        assert r["status"] == "accepted", r
        assert r["result"]["tkp"]["amount"] == json_amount
        # Значение в БД точно совпадает (NUMERIC(18,4) на PG, BIGINT×10⁴ на SQLite) — без float.
        with world.svc.engine.connect() as c:
            v = c.execute(sa.select(db.tkp.c.amount).where(db.tkp.c.id == tid)).scalar()
        from decimal import Decimal
        assert isinstance(v, Decimal) and v == Decimal(json_amount)
    else:
        assert r.get("code") == expect, r


def test_atomic_rollback_on_fault(world):
    rp = world.login("rp1")
    world.svc._fault_after_effect = "customer.create"
    cid, op = new_id(), new_id()
    r = rp.cmd("customer.create", {"id": cid, "name": "Сбой"}, op_id=op)
    assert (r["status"], r["code"]) == ("error", "internal_error")
    with world.svc.engine.connect() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(db.customers).where(db.customers.c.id == cid)).scalar() == 0
        assert c.execute(sa.select(sa.func.count()).select_from(db.audit_log).where(db.audit_log.c.object_id == cid)).scalar() == 0
        assert c.execute(sa.select(sa.func.count()).select_from(db.operations).where(db.operations.c.operation_id == op)).scalar() == 0
    world.svc._fault_after_effect = None
    r = rp.cmd("customer.create", {"id": cid, "name": "Сбой"}, op_id=op)  # повтор той же операции безопасен
    assert r["status"] == "accepted" and not r.get("replayed")


def test_audit_is_append_only(world):
    eng = world.svc.engine
    for stmt in ("UPDATE audit_log SET result='x'", "DELETE FROM audit_log"):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.exec_driver_sql(stmt)
    if eng.dialect.name == "postgresql":
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.exec_driver_sql("TRUNCATE audit_log")


def test_no_secrets_in_db_except_hashes(world):
    with world.svc.engine.connect() as c:
        blobs = []
        for t in db.metadata.sorted_tables:
            blobs.append(json.dumps([[str(x) for x in r] for r in c.execute(sa.select(t))], ensure_ascii=False))
    blob = "\n".join(blobs)
    assert PWD not in blob
    assert "$argon2id$" in blob  # пароли — только хэши Argon2id
    # Токены сеансов в БД не хранятся в открытом виде.
    s = world.login("eng1")
    assert s.token not in blob


def test_unknown_fields_and_bad_input_rejected(world):
    rp = world.login("rp1")
    r = rp.cmd("customer.create", {"id": new_id(), "name": "X", "role": "admin"})
    assert (r["status"], r["code"]) == ("rejected", "unknown_fields")
    r = rp.cmd("customer.create", {"id": "not-a-uuid", "name": "X"})
    assert r["status"] == "rejected"
    r = rp.cmd("customer.create", {"id": new_id(), "name": "   "})
    assert r["status"] == "rejected"
    r = rp.cmd("customer.create", {"id": new_id(), "name": "X", "inn": "12345"})
    assert r["code"] == "invalid_inn"
    r = world.svc.handle({"type": "command", "token": rp.token, "operation_id": "x", "command": "customer.create", "payload": {}})
    assert r["code"] == "bad_operation_id"
    r = world.svc.handle({"type": "command", "token": rp.token, "operation_id": new_id(), "command": "drop.everything", "payload": {}})
    assert r["code"] == "unknown_command"
    r = rp.cmd("customer.update", {"id": new_id(), "name": "X"})
    assert r["code"] == "expected_version_required"
