"""Общие стенды.

* Class A: временная SQLite (каждый тест — новая БД), управляемые часы.
* Class B (маркер postgres): встроенный временный PostgreSQL (pgserver), новая БД на тест.
  Это не «реальная БД компании» — процесс и данные живут только во время прогона.
* MS SQL: в этой среде нет сервера/ODBC — не проверяется (см. отчёт); диалект поддерживается кодом.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from vector.server import db, security
from vector.server.security import SecurityPolicy
from vector.server.service import Service

security.configure_hasher_for_tests()

PWD = "correct-horse-battery"
NEW_PWD = "Новый-пароль-2026!"


class FakeClock:
    def __init__(self):
        self.t = datetime(2026, 11, 16, 9, 0, 0, tzinfo=timezone.utc)

    def now(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------- движки БД
_PG = {}


def _pg_server():
    if "srv" not in _PG:
        pgserver = pytest.importorskip("pgserver")
        d = tempfile.mkdtemp(prefix="vector-pg-")
        _PG["dir"] = d
        _PG["srv"] = pgserver.get_server(d, cleanup_mode="stop")
    return _PG["srv"]


def pytest_sessionfinish(session, exitstatus):
    srv = _PG.pop("srv", None)
    if srv is not None:
        try:
            srv.cleanup()
        except Exception:
            pass
        shutil.rmtree(_PG.pop("dir", ""), ignore_errors=True)


def make_url(backend: str, tmp_path) -> str:
    if backend == "sqlite":
        return f"sqlite:///{tmp_path / ('db-' + uuid.uuid4().hex[:8] + '.sqlite')}"
    srv = _pg_server()
    name = "t_" + uuid.uuid4().hex[:12]
    srv.psql(f"CREATE DATABASE {name};")
    base = srv.get_uri()  # postgresql://postgres:@/postgres?host=/tmp/...
    url = base.replace("/postgres?", f"/{name}?", 1)
    return url.replace("postgresql://", "postgresql+psycopg2://", 1)


def make_world_engine(backend: str, tmp_path, clock):
    eng = db.make_engine(make_url(backend, tmp_path))
    db.create_schema(eng, str(uuid.uuid4()), clock.now())
    return eng


def pytest_generate_tests(metafunc):
    if "backend" in metafunc.fixturenames:
        only = getattr(metafunc.module, "BACKENDS", None)  # модуль может ограничить набор СУБД
        if only:
            metafunc.parametrize("backend", only)
            return
        metafunc.parametrize("backend", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])


# ---------------------------------------------------------------- клиент-обёртка над сервисом
class Session:
    def __init__(self, svc: Service, token: str, user_id: str, addr: str = "10.0.0.5"):
        self.svc, self.token, self.user_id, self.addr = svc, token, user_id, addr

    def cmd(self, command: str, payload: dict, expected_version: int | None = None, op_id: str | None = None) -> dict:
        return self.svc.handle({"type": "command", "token": self.token, "operation_id": op_id or new_id(), "command": command,
                                "payload": payload, "expected_version": expected_version}, self.addr)

    def ok(self, command: str, payload: dict, expected_version: int | None = None) -> dict:
        r = self.cmd(command, payload, expected_version)
        assert r["status"] == "accepted", (command, r)
        return r["result"]

    def query(self, query: str, **kw) -> dict:
        return self.svc.handle({"type": "query", "token": self.token, "query": query, **kw}, self.addr)


class World:
    def __init__(self, svc: Service, clock: FakeClock):
        self.svc, self.clock = svc, clock
        svc.bootstrap_admin("admin", PWD)
        self.admin = self.login("admin")
        self.users: dict[str, str] = {}
        self.grants: dict[str, int] = {}
        for login, role in [("rp1", "rp"), ("gip1", "gip"), ("head1", "dept_head"), ("eng1", "engineer"),
                            ("lead1", "lead_engineer"), ("acc1", "accountant"), ("rp2", "rp")]:
            self.users[login] = self.make_user(login, role)

    def raw_login(self, login: str, password: str = PWD, addr: str = "10.0.0.5", device: str = "dev-1") -> dict:
        return self.svc.handle({"type": "login", "login": login, "password": password, "device_id": device}, addr)

    def login(self, login: str, password: str = PWD, addr: str = "10.0.0.5") -> Session:
        r = self.raw_login(login, password, addr)
        assert r["status"] == "accepted", r
        return Session(self.svc, r["token"], r["user_id"], addr)

    def make_user(self, login: str, role: str | None, change_password: bool = True) -> str:
        uid = self.admin.ok("admin.create_user", {"login": login, "display_name": login.upper(), "initial_password": PWD})["user_id"]
        if role:
            self.grants[login] = self.admin.ok("admin.grant_role", {"user_id": uid, "role": role, "reason": "test"})["grant_id"]
        if change_password:
            # Созданный пользователь обязан сменить временный пароль; для стенда меняем на тот же PWD через промежуточный.
            s = self.login(login)
            s.ok("account.change_password", {"current_password": PWD, "new_password": PWD + "-x"})
            s.ok("account.change_password", {"current_password": PWD + "-x", "new_password": PWD})
        return uid

    def won_tender(self, s: Session):
        cust, tender = new_id(), new_id()
        s.ok("customer.create", {"id": cust, "name": "АО Заказчик"})
        s.ok("tender.create", {"id": tender, "customer_id": cust, "title": "Тендер на АСУ ТП"})
        v = 1
        for to in ("in_work", "bid_submitted", "won"):
            s.ok("tender.transition", {"id": tender, "to": to}, expected_version=v)
            v += 1
        return cust, tender, v


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def engine(backend, tmp_path, clock):
    eng = make_world_engine(backend, tmp_path, clock)
    yield eng
    eng.dispose()


@pytest.fixture
def fast_policy():
    # Argon2id с параметрами по умолчанию медленный по замыслу; политика сессий/блокировок — по умолчанию.
    return SecurityPolicy()


@pytest.fixture
def svc(engine, clock, fast_policy):
    return Service(engine, clock=clock, sec=fast_policy)


@pytest.fixture
def world(svc, clock):
    return World(svc, clock)
