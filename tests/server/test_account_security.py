"""Собственные учётные записи: вход, блокировка, частота, смена/сброс пароля, отключение, последний admin, bootstrap."""
import pytest
import sqlalchemy as sa

from conftest import NEW_PWD, PWD, new_id
from vector.core.errors import Rejected
from vector.server import db, security


def test_generic_error_does_not_reveal_which_part_is_wrong(world):
    a = world.raw_login("nobody")
    b = world.raw_login("rp1", "wrong-password-123")
    world.admin.ok("admin.set_user_active", {"user_id": world.users["eng1"], "active": False, "reason": "t"})
    c = world.raw_login("eng1")  # верный пароль, но учётная запись отключена
    assert a == b == c
    assert a["code"] == "bad_credentials"


def test_lockout_after_threshold_and_auto_unlock(world):
    n = world.svc.sec.lockout_threshold
    for _ in range(n):
        assert world.raw_login("rp1", "wrong-password-123")["code"] == "bad_credentials"
    r = world.raw_login("rp1")  # даже верный пароль
    assert r["code"] == "account_locked" and r["locked_until"]
    world.clock.advance(minutes=world.svc.sec.lockout_minutes, seconds=1)
    assert world.raw_login("rp1")["status"] == "accepted"


def test_successful_login_resets_failed_counter(world):
    n = world.svc.sec.lockout_threshold
    for _ in range(n - 1):
        world.raw_login("rp1", "wrong-password-123")
    assert world.raw_login("rp1")["status"] == "accepted"
    for _ in range(n - 1):
        world.raw_login("rp1", "wrong-password-123")
    assert world.raw_login("rp1")["status"] == "accepted"


def test_admin_reset_unlocks(world):
    for _ in range(world.svc.sec.lockout_threshold):
        world.raw_login("rp1", "wrong-password-123")
    world.admin.ok("admin.reset_password", {"user_id": world.users["rp1"], "temporary_password": "Временный-пароль-1", "reason": "звонок"})
    r = world.raw_login("rp1", "Временный-пароль-1")
    assert r["status"] == "accepted" and r["must_change_password"] is True
    # Предел размера файла сообщается клиенту при входе (issue #9) — подсказка для интерфейса, проверяет сервер.
    assert r["max_file_bytes"] == world.svc.sec.max_file_mb * 1024 * 1024


def test_rate_limit_per_address(world):
    limit = world.svc.sec.login_rate_per_minute_per_addr
    for i in range(limit):
        world.raw_login(f"ghost{i}", "x" * 12, addr="10.9.9.9")
    assert world.raw_login("rp1", addr="10.9.9.9")["code"] == "rate_limited"
    assert world.raw_login("rp1", addr="10.9.9.10")["status"] == "accepted"  # другие адреса не страдают


def test_must_change_password_blocks_everything_else(world):
    world.admin.ok("admin.create_user", {"login": "fresh", "display_name": "Новичок", "initial_password": PWD})
    r = world.raw_login("fresh")
    assert r["status"] == "accepted" and r["must_change_password"] is True
    tok = r["token"]
    q = world.svc.handle({"type": "query", "token": tok, "query": "pull"})
    assert q["code"] == "password_change_required"
    c = world.svc.handle({"type": "command", "token": tok, "operation_id": new_id(), "command": "customer.create",
                          "payload": {"id": new_id(), "name": "X"}})
    assert c["code"] == "password_change_required"
    ok = world.svc.handle({"type": "command", "token": tok, "operation_id": new_id(), "command": "account.change_password",
                           "payload": {"current_password": PWD, "new_password": NEW_PWD}})
    assert ok["status"] == "accepted"
    assert world.svc.handle({"type": "query", "token": tok, "query": "pull"})["status"] == "accepted"


@pytest.mark.parametrize("new,code", [
    ("short", "weak_password"), ("fresh-user-password", "weak_password"), ("aaaaaaaaaaaaaa", "weak_password"),
    ("password1234", "weak_password"), (PWD, "same_password"),
])
def test_password_policy(world, new, code):
    world.make_user("fresh", None)
    s = world.login("fresh")
    assert s.cmd("account.change_password", {"current_password": PWD, "new_password": new})["code"] == code


def test_change_password_requires_current_and_revokes_other_sessions(world):
    a, b = world.login("rp1"), world.login("rp1")
    assert a.cmd("account.change_password", {"current_password": "wrong-password-123", "new_password": NEW_PWD})["code"] == "bad_current_password"
    r = a.ok("account.change_password", {"current_password": PWD, "new_password": NEW_PWD})
    assert r["other_sessions_revoked"] >= 1
    assert a.query("pull")["status"] == "accepted"
    assert b.query("pull")["code"] == "session_invalid"
    assert world.raw_login("rp1")["status"] == "unauthenticated"
    assert world.raw_login("rp1", NEW_PWD)["status"] == "accepted"


def test_reset_password_revokes_sessions(world):
    s = world.login("rp1")
    world.admin.ok("admin.reset_password", {"user_id": world.users["rp1"], "temporary_password": "Временный-пароль-1", "reason": "t"})
    assert s.query("pull")["code"] == "session_invalid"


def test_reset_password_admin_only(world):
    rp = world.login("rp1")
    r = rp.cmd("admin.reset_password", {"user_id": world.users["eng1"], "temporary_password": "Временный-пароль-1", "reason": "t"})
    assert r["status"] == "denied"


def test_disable_user_revokes_and_blocks(world):
    s = world.login("rp1")
    world.admin.ok("admin.set_user_active", {"user_id": world.users["rp1"], "active": False, "reason": "увольнение"})
    assert s.query("pull")["code"] == "session_invalid"
    assert world.raw_login("rp1")["code"] == "bad_credentials"
    world.admin.ok("admin.set_user_active", {"user_id": world.users["rp1"], "active": True, "reason": "ошибка"})
    assert world.raw_login("rp1")["status"] == "accepted"


def test_cannot_disable_self_or_last_admin(world):
    admin_id = world.admin.user_id
    r = world.admin.cmd("admin.set_user_active", {"user_id": admin_id, "active": False, "reason": "t"})
    assert r["code"] == "cannot_disable_self"
    with world.svc.engine.connect() as c:
        gid = c.execute(sa.select(db.role_grants.c.id).where(db.role_grants.c.user_id == admin_id)).scalar()
    assert world.admin.cmd("admin.revoke_role", {"grant_id": gid, "reason": "t"})["code"] == "last_admin"
    # Второй администратор снимает защиту, но только пока он активен.
    a2 = world.make_user("admin2", "admin")
    s2 = world.login("admin2")
    s2.ok("admin.revoke_role", {"grant_id": gid, "reason": "передача"})
    assert s2.cmd("admin.set_user_active", {"user_id": a2, "active": False, "reason": "t"})["code"] == "cannot_disable_self"


def test_session_idle_and_absolute_expiry(world):
    s = world.login("rp1")
    world.clock.advance(minutes=world.svc.sec.session_idle_minutes - 1)
    assert s.query("pull")["status"] == "accepted"  # активность продлевает
    world.clock.advance(minutes=world.svc.sec.session_idle_minutes + 1)
    assert s.query("pull")["code"] == "session_invalid"
    s = world.login("rp1")
    for _ in range(world.svc.sec.session_absolute_hours * 2):
        world.clock.advance(minutes=30)
        r = s.query("pull")
    assert r["code"] == "session_invalid"  # абсолютный срок не продлевается активностью


def test_logout(world):
    s = world.login("rp1")
    assert world.svc.handle({"type": "logout", "token": s.token})["revoked"] == 1
    assert s.query("pull")["code"] == "session_invalid"


def test_bootstrap_only_once(world):
    with pytest.raises(Rejected) as e:
        world.svc.bootstrap_admin("admin9", "Другой-пароль-2026")
    assert e.value.code == "already_bootstrapped"


def test_users_query_admin_only_and_no_hashes(world):
    assert world.login("rp1").query("users")["status"] == "denied"
    r = world.admin.query("users")
    assert r["status"] == "accepted"
    assert {u["login"] for u in r["result"]} >= {"admin", "rp1"}
    assert "argon2" not in str(r)


def test_login_audited_without_password(world):
    world.raw_login("rp1", "wrong-password-123")
    with world.svc.engine.connect() as c:
        rows = c.execute(sa.select(db.audit_log.c.result, db.audit_log.c.details_json).where(db.audit_log.c.command == "auth.login")).all()
    assert any(r.result == "denied" for r in rows)
    assert "wrong-password-123" not in str(rows)


def test_production_hasher_parameters():
    p = security.production_hasher_parameters()
    assert p["type"] == "ID" and p["memory_cost_kib"] >= 19 * 1024 and p["time_cost"] >= 2


def test_rate_limit_window_slides(world):
    limit = world.svc.sec.login_rate_per_minute_per_addr
    for i in range(limit):
        world.raw_login(f"ghost{i}", "x" * 12, addr="10.9.9.9")
    assert world.raw_login("rp1", addr="10.9.9.9")["code"] == "rate_limited"
    world.clock.advance(seconds=61)
    assert world.raw_login("rp1", addr="10.9.9.9")["status"] == "accepted"


def test_change_password_bruteforce_throttled(world):
    s = world.login("rp1")
    world.clock.advance(seconds=61)  # стенд уже дважды менял пароль rp1 при создании
    codes = [s.cmd("account.change_password", {"current_password": f"guess-{i}-xxxxxx", "new_password": NEW_PWD})["code"]
             for i in range(world.svc.sec.lockout_threshold + 1)]
    assert codes[-1] == "rate_limited" and set(codes[:-1]) == {"bad_current_password"}


def test_password_never_enters_request_hash_and_legacy_hashes_can_be_redacted(world):
    """Пароль в хэш запроса не входит: по operations.request_hash его не подобрать. Повтор того же operation_id с
    другим паролем — тот же запрос (сохранённый исход, без повторного эффекта). Старые записи (до исправления)
    зачищаются redact_legacy_secret_hashes: без apply — только подсчёт."""
    import hashlib
    import re
    from pathlib import Path
    from vector.core.redact import SECRET_FIELDS
    from vector.server.validation import canon
    s = world.login("eng1")
    op = new_id()
    req = {"type": "command", "token": s.token, "operation_id": op, "command": "account.change_password",
           "payload": {"current_password": PWD, "new_password": NEW_PWD}}
    assert world.svc.handle(req)["status"] == "accepted"
    with world.svc.engine.connect() as c:
        h = c.execute(sa.select(db.operations.c.request_hash).where(db.operations.c.operation_id == op)).scalar_one()
    with_pwd = hashlib.sha256(canon({"c": "account.change_password", "p": req["payload"], "v": None}).encode()).hexdigest()
    assert h != with_pwd
    assert h == hashlib.sha256(canon({"c": "account.change_password", "p": {}, "v": None}).encode()).hexdigest()
    again = world.svc.handle({**req, "payload": {"current_password": "другой", "new_password": "другой"}})
    assert again.get("replayed") is True and again["status"] == "accepted"
    # Каждое поле с «password» в командах сервера — в списке секретов (новое поле не «утечёт» в хэш незамеченным).
    src = "".join(p.read_text(encoding="utf-8") for p in Path(db.__file__).parent.glob("*.py"))
    fields = set(re.findall(r'"([a-z_]*password[a-z_]*)"', src)) - {"must_change_password", "password_changed",
                                                                    "password_change_required", "bad_current_password",
                                                                    "same_password", "weak_password", "bad_password",
                                                                    "password_changed_at", "password_reset"}
    assert fields <= SECRET_FIELDS, fields - SECRET_FIELDS
    # Старая запись с хэшем «как раньше» — находится и зачищается.
    with world.svc.engine.begin() as c:
        c.execute(db.operations.update().where(db.operations.c.operation_id == op).values(request_hash=with_pwd))
    assert world.svc.redact_legacy_secret_hashes() >= 1
    with world.svc.engine.connect() as c:
        assert c.execute(sa.select(db.operations.c.request_hash).where(db.operations.c.operation_id == op)).scalar_one() == with_pwd
    n = world.svc.redact_legacy_secret_hashes(apply=True)
    assert n >= 1 and world.svc.redact_legacy_secret_hashes() == 0
    with world.svc.engine.connect() as c:
        assert c.execute(sa.select(db.operations.c.request_hash).where(db.operations.c.operation_id == op)).scalar_one() == "redacted"
