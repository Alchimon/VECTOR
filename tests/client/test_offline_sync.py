"""Офлайн-ядро клиента против продуктового сервиса (Class A, SQLite): очередь, отказ online_required, потеря ответа,
дедупликация, конфликт, отзыв прав офлайн, шифрование локального кэша, офлайн-разблокировка."""
import sqlite3
import uuid

import pytest
import sqlalchemy as sa

from conftest import NEW_PWD, PWD, new_id
from vector.client.sync import SyncClient
from vector.client.transport import LoopbackTransport
from vector.client.vault import MemoryStore
from vector.server import db

FAST_KDF = {"n": 2 ** 10, "r": 8, "p": 1}
SERVER = "https://vector.test"

BACKENDS = ["sqlite"]  # клиентское ядро не зависит от СУБД сервера; серверные инварианты — в tests/server на SQLite+PG


@pytest.fixture
def mk(world, tmp_path):
    store = MemoryStore()

    def make(login, base=None, password=PWD):
        c = SyncClient(LoopbackTransport(world.svc), SERVER, base or (tmp_path / f"profiles-{uuid.uuid4().hex[:6]}"), store,
                       device_id=f"dev-{login}", clock=world.clock, kdf=FAST_KDF)
        r = c.login(login, password)
        assert r.status == "accepted", r
        return c
    make.store = store
    return make


def count(world, table, where=None):
    t = db.metadata.tables[table]
    q = sa.select(sa.func.count()).select_from(t)
    if where is not None:
        q = q.where(where)
    with world.svc.engine.connect() as c:
        return c.execute(q).scalar()


def test_queue_offline_and_sync_later(world, mk):
    rp = mk("rp1")
    rp.t.online = False
    cust, tender = new_id(), new_id()
    assert rp.execute("customer.create", {"id": cust, "name": "Офлайн-заказчик"}).status == "queued"
    assert rp.execute("tender.create", {"id": tender, "customer_id": cust, "title": "Офлайн-тендер"}).status == "queued"
    assert rp.get_local("customer", cust)["_pending"] is True
    assert count(world, "customers") == 0
    r = rp.execute("tender.transition", {"id": tender, "to": "in_work"}, expected_version=1)
    assert r.status in ("refused_offline", "blocked")
    assert rp.pending_count() == 2
    rp.t.online = True
    rep = rp.sync()
    assert (rep.accepted, rep.stopped_on) == (2, None)
    t = rp.get_local("tender", tender)
    assert t["_pending"] is False and t["version"] == 1


def test_online_required_offline_not_queued(world, mk):
    rp = mk("rp1")
    _, tender, _ = world.won_tender(world.login("rp1"))
    rp.pull()
    rp.t.online = False
    r = rp.execute("project.start_rkd", {"id": new_id(), "tender_id": tender, "code": "P", "title": "t"})
    assert r.status == "refused_offline" and rp.pending_count() == 0


def test_lost_response_is_deduplicated(world, mk):
    rp = mk("rp1")
    rp.t.online = False
    rp.execute("customer.create", {"id": new_id(), "name": "Дубль?"})
    rp.t.online = True
    rp.t.drop_next_response = True
    assert rp.sync().unknown_outcome and rp.pending_count() == 1
    rep = rp.sync()
    assert (rep.accepted, rep.replayed) == (1, 1)
    assert count(world, "customers") == 1
    assert count(world, "audit_log", db.audit_log.c.command == "customer.create") == 1


def test_lost_response_on_online_required(world, mk):
    rp = mk("rp1")
    _, tender, _ = world.won_tender(world.login("rp1"))
    rp.t.drop_next_response = True
    r = rp.execute("project.start_rkd", {"id": new_id(), "tender_id": tender, "code": "P-U", "title": "t"})
    assert r.status == "unknown_outcome"
    r2 = rp.retry_online(r.operation_id)
    assert r2.status == "accepted" and r2.response.get("replayed")
    assert count(world, "projects") == 1


def test_conflict_is_not_silently_merged(world, mk):
    rp, head = mk("rp1"), mk("rp2")
    cust = new_id()
    rp.execute("customer.create", {"id": cust, "name": "Исходное"})
    head.pull()
    rp.t.online = False
    rp.execute("customer.update", {"id": cust, "name": "Правка РП (офлайн)"}, expected_version=1)
    assert head.execute("customer.update", {"id": cust, "note": "Правка коллеги"}, expected_version=1).status == "accepted"
    rp.t.online = True
    assert rp.sync().stop_status == "conflict"
    probs = rp.problems()
    assert len(probs) == 1 and probs[0]["server_current"]["version"] == 2 and probs[0]["server_current"]["note"] == "Правка коллеги"
    with world.svc.engine.connect() as c:
        assert tuple(c.execute(sa.select(db.customers.c.name, db.customers.c.version)).one()) == ("Исходное", 2)
    r = rp.reapply_on_current(probs[0]["operation_id"])
    assert r.status == "accepted" and r.operation_id != probs[0]["operation_id"]
    with world.svc.engine.connect() as c:
        assert tuple(c.execute(sa.select(db.customers.c.name, db.customers.c.note, db.customers.c.version)).one()) == \
            ("Правка РП (офлайн)", "Правка коллеги", 3)


def test_conflict_discard_restores_server_state_offline(world, mk):
    rp, other = mk("rp1"), mk("rp2")
    cust = new_id()
    rp.execute("customer.create", {"id": cust, "name": "Исходное"})
    rp.t.online = False
    rp.execute("customer.update", {"id": cust, "name": "Моя правка"}, expected_version=1)
    other.execute("customer.update", {"id": cust, "name": "Чужая правка"}, expected_version=1)
    rp.t.online = True
    rp.sync()
    op = rp.problems()[0]["operation_id"]
    rp.t.online = False
    rp.discard(op)
    local = rp.get_local("customer", cust)
    assert (local["name"], local["_pending"]) == ("Чужая правка", False)


def test_sync_stops_preserving_order(world, mk):
    rp, other = mk("rp1"), mk("rp2")
    cust = new_id()
    rp.execute("customer.create", {"id": cust, "name": "A"})
    rp.t.online = False
    rp.execute("customer.update", {"id": cust, "name": "B"}, expected_version=1)
    rp.execute("tender.create", {"id": new_id(), "customer_id": cust, "title": "после конфликта"})
    other.execute("customer.update", {"id": cust, "name": "C"}, expected_version=1)
    rp.t.online = True
    assert rp.sync().stop_status == "conflict"
    assert rp.pending_count() == 1
    rp.discard(rp.problems()[0]["operation_id"])
    assert rp.sync().accepted == 1


def test_rights_revoked_while_offline(world, mk):
    rp = mk("rp1")
    rp.t.online = False
    cid = new_id()
    rp.execute("customer.create", {"id": cid, "name": "Пока был офлайн"})
    world.admin.ok("admin.revoke_role", {"grant_id": world.grants["rp1"], "reason": "смена функции"})
    rp.t.online = True
    assert rp.sync().stop_status == "denied"
    assert rp.problems()[0]["code"] == "no_authority"
    rp.discard(rp.problems()[0]["operation_id"])
    assert rp.get_local("customer", cid) is None


def test_pull_respects_read_rights(world, mk):
    mk("rp1").execute("customer.create", {"id": new_id(), "name": "Секретный"})
    eng = mk("eng1")
    eng.pull()
    assert eng.list_local("customer") == []


def test_session_revoked_keeps_queue(world, mk):
    rp = mk("rp1")
    world.admin.ok("admin.revoke_sessions", {"user_id": world.users["rp1"], "reason": "t"})
    r = rp.execute("customer.create", {"id": new_id(), "name": "X"})
    assert r.status == "queued" and rp.pending_count() == 1 and rp.token is None
    assert rp.relogin_online(PWD).status == "accepted"
    assert rp.sync().accepted == 1


# ---------------------------------------------------------------- защита локальных данных
def test_local_db_is_encrypted(world, mk, tmp_path):
    base = tmp_path / "p"
    rp = mk("rp1", base=base)
    rp.t.online = False
    rp.execute("customer.create", {"id": new_id(), "name": "Секретное ООО Ромашка", "inn": "7701234567"})
    rp.db.close()
    raw = b"".join(p.read_bytes() for p in base.rglob("*") if p.is_file())
    for needle in ("Ромашка".encode(), b"7701234567", PWD.encode()):
        assert needle not in raw


def test_tampered_local_row_detected(world, mk, tmp_path):
    from vector.client.vault import VaultError
    rp = mk("rp1", base=tmp_path / "p")
    rp.t.online = False
    a, b = new_id(), new_id()
    rp.execute("customer.create", {"id": a, "name": "A"})
    rp.execute("customer.create", {"id": b, "name": "B"})
    # Перенос шифртекста между строками обнаруживается (AAD = ключ строки).
    enc_b = rp.db.execute("SELECT data_enc FROM cache WHERE id=?", (b,)).fetchone()[0]
    rp.db.execute("UPDATE cache SET data_enc=? WHERE id=?", (enc_b, a))
    with pytest.raises(VaultError):
        rp.list_local("customer")


def test_offline_unlock_and_later_sync(world, mk, tmp_path):
    base = tmp_path / "p"
    rp = mk("rp1", base=base)
    rp.logout()
    c = SyncClient(LoopbackTransport(world.svc), SERVER, base, mk.store, "dev-rp1", clock=world.clock, kdf=FAST_KDF)
    c.t.online = False
    assert c.login("rp1", "wrong-password-123").status == "refused_offline"
    r = c.login("rp1", PWD)
    assert r.status == "offline_unlocked" and c.token is None
    cid = new_id()
    assert c.execute("customer.create", {"id": cid, "name": "Сделано офлайн"}).status == "queued"
    assert c.execute("admin.create_user", {"login": "x"}).status == "refused_offline"
    c.t.online = True
    assert c.sync().stop_status == "unauthenticated"
    assert c.relogin_online(PWD).status == "accepted"
    assert c.sync().accepted == 1 and count(world, "customers") == 1


def test_offline_unlock_limits(world, mk, tmp_path):
    base = tmp_path / "p"
    mk("rp1", base=base).logout()
    other_device = MemoryStore()  # другая учётная запись Windows / другой компьютер
    c = SyncClient(LoopbackTransport(world.svc), SERVER, base, other_device, "dev", clock=world.clock, kdf=FAST_KDF)
    c.t.online = False
    assert c.login("rp1", PWD).response["code"] == "device_secret_missing"
    c = SyncClient(LoopbackTransport(world.svc), SERVER, base, mk.store, "dev", clock=world.clock, kdf=FAST_KDF)
    c.t.online = False
    assert c.login("unknown", PWD).status == "refused_offline"
    world.clock.advance(days=8)
    assert c.login("rp1", PWD).response["code"] == "offline_expired"


def test_profiles_are_isolated_per_user(world, mk, tmp_path):
    base = tmp_path / "p"
    rp = mk("rp1", base=base)
    rp.execute("customer.create", {"id": new_id(), "name": "Только для rp1"})
    rp.logout()
    eng = mk("eng1", base=base)
    assert eng.list_local("customer") == []


def test_password_change_rewraps_and_keeps_queue(world, mk, tmp_path):
    base = tmp_path / "p"
    rp = mk("rp1", base=base)
    assert rp.change_password(PWD, NEW_PWD).status == "accepted"
    rp.t.online = False
    rp.execute("customer.create", {"id": new_id(), "name": "В очереди"})
    rp.logout()
    c = SyncClient(LoopbackTransport(world.svc), SERVER, base, mk.store, "dev", clock=world.clock, kdf=FAST_KDF)
    c.t.online = False
    assert c.login("rp1", PWD).status == "refused_offline"           # старый пароль больше не открывает профиль
    assert c.login("rp1", NEW_PWD).status == "offline_unlocked"
    assert c.pending_count() == 1  # ключ данных тот же — очередь читается под новым паролем


def test_admin_reset_orphans_old_cache_without_deleting(world, mk, tmp_path):
    base = tmp_path / "p"
    rp = mk("rp1", base=base)
    rp.t.online = False
    rp.execute("customer.create", {"id": new_id(), "name": "Не отправлено"})
    rp.logout()
    world.admin.ok("admin.reset_password", {"user_id": world.users["rp1"], "temporary_password": "Временный-пароль-1", "reason": "забыл"})
    c = SyncClient(LoopbackTransport(world.svc), SERVER, base, mk.store, "dev", clock=world.clock, kdf=FAST_KDF)
    r = c.login("rp1", "Временный-пароль-1")
    assert r.status == "accepted" and c.must_change_password
    assert c.orphaned_cache is not None and c.orphaned_cache.exists()
    assert c.pending_count() == 0


def test_pull_delivers_only_visible_active_assignments(world, mk):
    s = world.login("rp1")
    _, tender, _ = world.won_tender(s)
    pid = new_id()
    s.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-1", "title": "Проект"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["gip1"], "project_role": "gip", "reason": "Приказ"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp", "reason": "Приказ"})
    a_eng = s.ok("project.assign", {"project_id": pid, "user_id": world.users["eng1"], "project_role": "participant", "reason": "Работы"})
    rp = mk("rp1")
    assert rp.pull()
    mine = {(a["user_id"], a["project_role"]) for a in rp.get_meta("assignments") if a["project_id"] == pid}
    assert (world.users["gip1"], "gip") in mine and (world.users["eng1"], "participant") in mine
    # Пользователь без доступа к проекту (инженер не в команде; A-06: глобальные gip/rp/dept_head читают все) назначений не получает.
    other = mk("lead1")
    assert other.pull()
    assert [a for a in other.get_meta("assignments") if a["project_id"] == pid] == []
    # Отозванное назначение исчезает из кэша при следующем pull.
    aid = a_eng["assignment_id"]
    s.ok("project.revoke_assignment", {"assignment_id": aid, "reason": "Перевод"})
    assert rp.pull()
    assert (world.users["eng1"], "participant") not in {(a["user_id"], a["project_role"]) for a in rp.get_meta("assignments")}
    # В открытом виде в локальной БД назначения не хранятся (meta шифруется).
    raw = sqlite3.connect(str(rp.ov.path / "local.db")).execute("SELECT v_enc FROM meta WHERE k='assignments'").fetchone()[0]
    assert world.users["gip1"].encode() not in (raw if isinstance(raw, bytes) else raw.encode())
