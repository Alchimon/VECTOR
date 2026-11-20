"""Срез 1 онлайн: заказчик → тендер → ТКП → явное начало РКД → проект → ГИП/РП → комплект."""
import json
import sqlite3

from helpers import World, new_id


class Slice1Flow(World):
    def test_end_to_end_with_audit(self):
        rp = self.client("rp1")
        cust, tender, tv = self.won_tender(rp)

        # ТКП: сумма неизвестна (None) — это не ноль.
        tkp = new_id()
        r = rp.execute("tkp.create", {"id": tkp, "tender_id": tender, "label": "ТКП-15"})
        self.assertEqual(r.status, "accepted", r)
        self.assertIsNone(r.response["result"]["tkp"]["amount"])

        # Победа в тендере НЕ создаёт проект.
        self.assertEqual(rp.list_local("project"), [])

        pid = new_id()
        r = rp.execute("project.start_rkd", {"id": pid, "tender_id": tender, "code": "P-2026-001", "title": "РКД АСУ ТП"})
        self.assertEqual(r.status, "accepted", r)
        self.assertFalse(r.response["result"]["gip_assigned"])

        # Повторный старт по тому же тендеру — отказ, а не второй проект.
        r = rp.execute("project.start_rkd", {"id": new_id(), "tender_id": tender, "code": "P-2026-002", "title": "дубль"})
        self.assertEqual((r.status, r.response["code"]), ("rejected", "project_exists_for_tender"))

        # ГИП назначает только admin.
        r = rp.execute("project.assign", {"project_id": pid, "user_id": self.users["gip1"], "project_role": "gip"})
        self.assertEqual(r.status, "denied")
        self.admin_cmd("project.assign", {"project_id": pid, "user_id": self.users["gip1"], "project_role": "gip"})

        # РП назначает ГИП проекта; комплект создаёт РП проекта.
        gip = self.client("gip1")
        r = gip.execute("project.assign", {"project_id": pid, "user_id": self.users["rp1"], "project_role": "rp"})
        self.assertEqual(r.status, "accepted", r)
        kit = new_id()
        r = rp.execute("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Автоматизация"})
        self.assertEqual(r.status, "accepted", r)

        # История проекта: автор, полномочие (generation), версия тендера.
        h = rp.t.send({"type": "query", "query": "history", "token": rp.token, "object_type": "project", "object_id": pid})
        self.assertEqual(h["status"], "accepted")
        start = h["result"][0]
        self.assertEqual(start["command"], "project.start_rkd")
        self.assertEqual(start["actor_user_id"], self.users["rp1"])
        self.assertEqual(start["authority"], f"global_role:rp#grant:{self.grants['rp1']}")
        self.assertEqual(start["details"]["tender_version"], tv)

    def test_start_rkd_requires_won_tender(self):
        rp = self.client("rp1")
        cust, tender = new_id(), new_id()
        rp.execute("customer.create", {"id": cust, "name": "ООО Тест"})
        rp.execute("tender.create", {"id": tender, "customer_id": cust, "title": "T"})
        r = rp.execute("project.start_rkd", {"id": new_id(), "tender_id": tender, "code": "X", "title": "X"})
        self.assertEqual((r.status, r.response["code"]), ("rejected", "tender_status_not_allowed"))

    def test_invalid_transition_and_reason(self):
        rp = self.client("rp1")
        cust, tender = new_id(), new_id()
        rp.execute("customer.create", {"id": cust, "name": "ООО Тест"})
        rp.execute("tender.create", {"id": tender, "customer_id": cust, "title": "T"})
        r = rp.execute("tender.transition", {"id": tender, "to": "won"}, expected_version=1)
        self.assertEqual(r.response["code"], "transition_not_allowed")
        r = rp.execute("tender.transition", {"id": tender, "to": "cancelled"}, expected_version=1)
        self.assertEqual(r.response["code"], "reason_required")
        r = rp.execute("tender.transition", {"id": tender, "to": "cancelled", "reason": "Заказчик отменил"}, expected_version=1)
        self.assertEqual(r.status, "accepted")

    def test_kit_code_unique_per_project_not_global(self):
        rp = self.client("rp1")
        pids = []
        for i in range(2):
            _, tender, _ = self.won_tender(rp)
            pid = new_id()
            self.assertEqual(rp.execute("project.start_rkd", {"id": pid, "tender_id": tender, "code": f"P-{i}", "title": "t"}).status, "accepted")
            self.admin_cmd("project.assign", {"project_id": pid, "user_id": self.users["rp1"], "project_role": "rp"})
            pids.append(pid)
        for pid in pids:
            self.assertEqual(rp.execute("kit.create", {"id": new_id(), "project_id": pid, "code": "ЭМ", "title": "Э"}).status, "accepted")
        r = rp.execute("kit.create", {"id": new_id(), "project_id": pids[0], "code": "ЭМ", "title": "дубль"})
        self.assertEqual(r.response["code"], "kit_code_taken")

    def test_money_on_tkp_boundaries(self):
        rp = self.client("rp1")
        _, tender, _ = self.won_tender(rp)
        cases = [({"amount": "0", "currency": "RUB"}, "accepted", "0.0000"),
                 ({"amount": "1500000.1234", "currency": "RUB"}, "accepted", "1500000.1234"),
                 ({"amount": "1.12345", "currency": "RUB"}, "amount_too_many_fraction_digits", None),
                 ({"amount": "100000000000000", "currency": "RUB"}, "amount_out_of_range", None),
                 ({"amount": "10"}, "currency_required", None),
                 ({"amount": "", "currency": "RUB"}, "amount_invalid_format", None)]
        for extra, expect, json_amount in cases:
            with self.subTest(extra=extra):
                r = rp.execute("tkp.create", {"id": new_id(), "tender_id": tender, "label": "ТКП", **extra})
                if expect == "accepted":
                    self.assertEqual(r.status, "accepted", r)
                    self.assertEqual(r.response["result"]["tkp"]["amount"], json_amount)
                else:
                    self.assertEqual(r.response.get("code"), expect, r)
        # float на JSON-границе тоже отклоняется (обход клиента).
        resp = self.server.handle({"type": "command", "token": rp.token, "operation_id": new_id(), "command": "tkp.create",
                                   "payload": {"id": new_id(), "tender_id": tender, "label": "x", "amount": 1.5, "currency": "RUB"}})
        self.assertEqual(resp["code"], "amount_float_forbidden")
        # В БД хранится целое в десятитысячных.
        with sqlite3.connect(self.server.db_path) as conn:
            vals = sorted(r[0] for r in conn.execute("SELECT amount_minor FROM tkp"))
        self.assertEqual(vals, [0, 15000001234])

    def test_atomic_rollback_on_fault(self):
        rp = self.client("rp1")
        self.server._fault_after_effect = "customer.create"
        cid = new_id()
        r = rp.execute("customer.create", {"id": cid, "name": "Сбой"})
        self.assertEqual(r.status, "queued")  # internal error -> операция остаётся в очереди
        with sqlite3.connect(self.server.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM customers WHERE id=?", (cid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE object_id=?", (cid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM operations WHERE command='customer.create'").fetchone()[0], 0)
        self.server._fault_after_effect = None
        rep = rp.sync()
        self.assertEqual(rep.accepted, 1)
        self.assertEqual(rp.get_local("customer", cid)["_sync"], "синхронизировано")

    def test_audit_is_append_only(self):
        with sqlite3.connect(self.server.db_path) as conn:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE audit_log SET result='x'")
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM audit_log")

    def test_no_secrets_in_audit(self):
        with sqlite3.connect(self.server.db_path) as conn:
            blob = json.dumps([list(r) for r in conn.execute("SELECT * FROM audit_log")], ensure_ascii=False)
        self.assertNotIn("correct-horse-battery", blob)
