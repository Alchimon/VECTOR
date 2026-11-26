"""Общий стенд Class A: временная SQLite, управляемые часы, пользователи с ролями."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vector_ref.client import Client  # noqa: E402
from vector_ref.server import Server  # noqa: E402
from vector_ref.transport import LoopbackTransport  # noqa: E402

PWD = "correct-horse-battery"


class FakeClock:
    def __init__(self):
        self.t = datetime(2026, 11, 16, 9, 0, 0, tzinfo=timezone.utc)

    def now(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def new_id() -> str:
    return str(uuid.uuid4())


class World(unittest.TestCase):
    """Сервер + админ + набор пользователей. Каждый тест — чистая временная БД."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.server = Server(os.path.join(self.tmp.name, "server.db"), clock=self.clock)
        self.server.bootstrap_admin("admin", PWD)
        self.admin = self.client("admin")
        self.users: dict[str, str] = {}
        self.grants: dict[str, int] = {}
        for login, role in [("rp1", "rp"), ("gip1", "gip"), ("head1", "dept_head"), ("eng1", "engineer"),
                            ("lead1", "lead_engineer"), ("acc1", "accountant"), ("rp2", "rp")]:
            self.users[login] = self.make_user(login, role)

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, login: str, password: str = PWD, device: str | None = None) -> Client:
        c = Client(os.path.join(self.tmp.name, f"local-{login}-{uuid.uuid4().hex[:6]}.db"),
                   LoopbackTransport(self.server), device or f"dev-{login}")
        r = c.login(login, password)
        assert r.status == "accepted", r
        return c

    def admin_cmd(self, command: str, payload: dict):
        r = self.admin.execute(command, payload)
        assert r.status == "accepted", (command, r)
        return r.response["result"]

    def make_user(self, login: str, role: str | None) -> str:
        uid = self.admin_cmd("admin.create_user", {"login": login, "display_name": login.upper(), "initial_password": PWD})["user_id"]
        if role:
            self.grants[login] = self.admin_cmd("admin.grant_role", {"user_id": uid, "role": role, "reason": "test"})["grant_id"]
        return uid

    # ---------------------------------------------------------- бизнес-шорткаты
    def won_tender(self, c: Client):
        cust, tender = new_id(), new_id()
        assert c.execute("customer.create", {"id": cust, "name": "АО Заказчик"}).status == "accepted"
        assert c.execute("tender.create", {"id": tender, "customer_id": cust, "title": "Тендер на АСУ ТП"}).status == "accepted"
        v = 1
        for to in ("in_work", "bid_submitted", "won"):
            r = c.execute("tender.transition", {"id": tender, "to": to}, expected_version=v)
            assert r.status == "accepted", r
            v += 1
        return cust, tender, v
