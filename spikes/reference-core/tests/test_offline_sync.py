"""Офлайн: очередь, отказ online_required, потеря ответа, дедупликация, конфликт, отзыв прав офлайн."""
import sqlite3
import threading

from helpers import World, new_id


class OfflineSync(World):
    def test_queue_offline_and_sync_later(self):
        rp = self.client("rp1")
        rp.t.online = False
        cust, tender = new_id(), new_id()
        r1 = rp.execute("customer.create", {"id": cust, "name": "Офлайн-заказчик"})
        r2 = rp.execute("tender.create", {"id": tender, "customer_id": cust, "title": "Офлайн-тендер"})
        self.assertEqual((r1.status, r2.status), ("queued", "queued"))
        self.assertEqual(rp.get_local("customer", cust)["_sync"], "не синхронизировано")
        # На сервере ничего нет: черновик ≠ подтверждённое действие.
        with sqlite3.connect(self.server.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 0)
        # online_required офлайн — отказ, ничего не поставлено в очередь.
        r3 = rp.execute("tender.transition", {"id": tender, "to": "in_work"}, expected_version=1)
        self.assertEqual(r3.status, "refused_offline")
        self.assertEqual(rp.pending_count(), 2)
        rp.t.online = True
        rep = rp.sync()
        self.assertEqual((rep.accepted, rep.stopped_on), (2, None))
        self.assertEqual(rp.get_local("tender", tender)["_sync"], "синхронизировано")
        self.assertEqual(rp.get_local("tender", tender)["version"], 1)

    def test_lost_response_is_deduplicated(self):
        rp = self.client("rp1")
        rp.t.online = False
        cust = new_id()
        rp.execute("customer.create", {"id": cust, "name": "Дубль?"})
        rp.t.online = True
        rp.t.drop_next_response = True
        rep = rp.sync()
        self.assertTrue(rep.unknown_outcome)
        self.assertEqual(rp.pending_count(), 1)       # исход неизвестен — операция остаётся
        rep = rp.sync()                               # тот же operation_id
        self.assertEqual((rep.accepted, rep.replayed), (1, 1))
        with sqlite3.connect(self.server.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE command='customer.create'").fetchone()[0], 1)

    def test_lost_response_on_online_required(self):
        rp = self.client("rp1")
        _, tender, _ = self.won_tender(rp)
        rp.t.drop_next_response = True
        pid = new_id()
        r = rp.execute("project.start_rkd", {"id": pid, "tender_id": tender, "code": "P-U", "title": "t"})
        self.assertEqual(r.status, "unknown_outcome")
        r2 = rp.retry_online(r.operation_id)
        self.assertEqual(r2.status, "accepted")
        self.assertTrue(r2.response.get("replayed"))
        with sqlite3.connect(self.server.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1)

    def test_operation_id_reuse_with_other_payload_rejected(self):
        rp = self.client("rp1")
        op = new_id()
        base = {"type": "command", "token": rp.token, "operation_id": op, "command": "customer.create"}
        a = self.server.handle({**base, "payload": {"id": new_id(), "name": "A"}})
        b = self.server.handle({**base, "payload": {"id": new_id(), "name": "B"}})
        self.assertEqual((a["status"], b["code"]), ("accepted", "operation_id_reused"))

    def test_conflict_is_not_silently_merged(self):
        rp, head = self.client("rp1"), self.client("head1")
        cust = new_id()
        rp.execute("customer.create", {"id": cust, "name": "Исходное"})
        head.pull()
        rp.t.online = False
        rp.execute("customer.update", {"id": cust, "name": "Правка РП (офлайн)"}, expected_version=1)
        self.assertEqual(head.execute("customer.update", {"id": cust, "note": "Правка руководителя"}, expected_version=1).status, "accepted")
        rp.t.online = True
        rep = rp.sync()
        self.assertEqual(rep.stop_status, "conflict")
        probs = rp.problems()
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0]["server_current"]["version"], 2)
        self.assertEqual(probs[0]["server_current"]["note"], "Правка руководителя")
        # Сервер не изменил имя: молчаливой перезаписи нет.
        with sqlite3.connect(self.server.db_path) as conn:
            self.assertEqual(conn.execute("SELECT name, version FROM customers").fetchone(), ("Исходное", 2))
        # Пользователь осознанно повторяет поверх текущей версии — новая операция.
        r = rp.reapply_on_current(probs[0]["operation_id"])
        self.assertEqual(r.status, "accepted", r)
        self.assertNotEqual(r.operation_id, probs[0]["operation_id"])
        with sqlite3.connect(self.server.db_path) as conn:
            self.assertEqual(conn.execute("SELECT name, note, version FROM customers").fetchone(),
                             ("Правка РП (офлайн)", "Правка руководителя", 3))

    def test_conflict_discard_restores_server_state_offline(self):
        rp, head = self.client("rp1"), self.client("head1")
        cust = new_id()
        rp.execute("customer.create", {"id": cust, "name": "Исходное"})
        rp.t.online = False
        rp.execute("customer.update", {"id": cust, "name": "Моя правка"}, expected_version=1)
        head.execute("customer.update", {"id": cust, "name": "Чужая правка"}, expected_version=1)
        rp.t.online = True
        rp.sync()
        op = rp.problems()[0]["operation_id"]
        rp.t.online = False
        rp.discard(op)
        local = rp.get_local("customer", cust)
        self.assertEqual((local["name"], local["_sync"]), ("Чужая правка", "синхронизировано"))

    def test_sync_stops_preserving_order(self):
        rp, head = self.client("rp1"), self.client("head1")
        cust = new_id()
        rp.execute("customer.create", {"id": cust, "name": "A"})
        rp.t.online = False
        rp.execute("customer.update", {"id": cust, "name": "B"}, expected_version=1)
        later = new_id()
        rp.execute("tender.create", {"id": later, "customer_id": cust, "title": "после конфликта"})
        head.execute("customer.update", {"id": cust, "name": "C"}, expected_version=1)
        rp.t.online = True
        rep = rp.sync()
        self.assertEqual(rep.stop_status, "conflict")
        self.assertEqual(rp.pending_count(), 1)  # тендер ждёт решения по конфликту
        rp.discard(rp.problems()[0]["operation_id"])
        self.assertEqual(rp.sync().accepted, 1)

    def test_rights_revoked_while_offline(self):
        rp = self.client("rp1")
        rp.t.online = False
        cid = new_id()
        rp.execute("customer.create", {"id": cid, "name": "Пока был офлайн"})
        self.admin_cmd("admin.revoke_role", {"grant_id": self.grants["rp1"], "reason": "смена функции"})
        rp.t.online = True
        rep = rp.sync()
        self.assertEqual(rep.stop_status, "denied")
        self.assertEqual(rp.problems()[0]["code"], "no_authority")
        rp.discard(rp.problems()[0]["operation_id"])
        self.assertIsNone(rp.get_local("customer", cid))

    def test_pull_respects_read_rights(self):
        rp = self.client("rp1")
        rp.execute("customer.create", {"id": new_id(), "name": "Секретный"})
        eng = self.client("eng1")
        eng.pull()
        self.assertEqual(eng.list_local("customer"), [])

    def test_concurrent_updates_one_wins(self):
        rp = self.client("rp1")
        cust = new_id()
        rp.execute("customer.create", {"id": cust, "name": "Гонка"})
        tokens = [self.client("rp1").token, self.client("head1").token]
        results = []
        barrier = threading.Barrier(2)

        def worker(tok, name):
            barrier.wait()
            results.append(self.server.handle({"type": "command", "token": tok, "operation_id": new_id(), "command": "customer.update",
                                               "payload": {"id": cust, "name": name}, "expected_version": 1})["status"])

        ts = [threading.Thread(target=worker, args=(tokens[i], f"N{i}")) for i in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(results), ["accepted", "conflict"])


class SameOperationRace(World):
    def test_parallel_duplicate_operation_id_single_effect(self):
        """Два параллельных запроса с одним operation_id (повтор после обрыва): один эффект, оба получают исход."""
        rp = self.client("rp1")
        op, cid = new_id(), new_id()
        req = {"type": "command", "token": rp.token, "operation_id": op, "command": "customer.create",
               "payload": {"id": cid, "name": "Параллельный повтор"}}
        results, barrier = [], threading.Barrier(4)

        def worker():
            barrier.wait()
            results.append(self.server.handle(dict(req)))

        ts = [threading.Thread(target=worker) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(r["status"] for r in results), ["accepted"] * 4)
        self.assertEqual(sum(1 for r in results if r.get("replayed")), 3)
        with sqlite3.connect(self.server.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM customers WHERE id=?", (cid,)).fetchone()[0], 1)

    def test_parallel_duplicate_of_rejected_operation(self):
        """Повтор отклонённой операции параллельно: исход сохранён один раз, без internal_error."""
        eng = self.client("eng1")
        req = {"type": "command", "token": eng.token, "operation_id": new_id(), "command": "customer.create",
               "payload": {"id": new_id(), "name": "Нет прав"}}
        results, barrier = [], threading.Barrier(4)

        def worker():
            barrier.wait()
            results.append(self.server.handle(dict(req))["status"])

        ts = [threading.Thread(target=worker) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(results, ["denied"] * 4)
