"""Права в момент эффекта: роль клиента игнорируется, срок/отзыв, generation согласования."""
from helpers import World, new_id


class Authorization(World):
    def test_client_supplied_role_is_ignored(self):
        eng = self.client("eng1")
        resp = self.server.handle({"type": "command", "token": eng.token, "operation_id": new_id(), "command": "customer.create",
                                   "payload": {"id": new_id(), "name": "X"}, "role": "admin", "roles": ["gip"]})
        self.assertEqual(resp["status"], "denied")

    def test_admin_has_no_implicit_business_rights(self):
        r = self.admin.execute("customer.create", {"id": new_id(), "name": "X"})
        self.assertEqual(r.status, "denied")

    def test_no_token_and_revoked_session(self):
        resp = self.server.handle({"type": "command", "operation_id": new_id(), "command": "customer.create", "payload": {}})
        self.assertEqual(resp["status"], "unauthenticated")
        rp = self.client("rp1")
        self.admin_cmd("admin.revoke_sessions", {"user_id": self.users["rp1"], "reason": "увольнение"})
        r = rp.execute("customer.create", {"id": new_id(), "name": "X"})
        self.assertEqual(r.status, "queued")  # остаётся в очереди, не теряется
        self.assertEqual(rp.sync().stop_status, "unauthenticated")

    def test_expired_grant(self):
        uid = self.make_user("temp", None)
        until = "2026-11-16T10:00:00.000000Z"
        self.admin_cmd("admin.grant_role", {"user_id": uid, "role": "rp", "valid_until": until, "reason": "замещение"})
        c = self.client("temp")
        self.assertEqual(c.execute("customer.create", {"id": new_id(), "name": "A"}).status, "accepted")
        self.clock.advance(hours=2)
        r = c.execute("customer.create", {"id": new_id(), "name": "B"})
        self.assertEqual(r.response["status"], "denied")

    def test_project_role_scope(self):
        rp = self.client("rp1")
        _, tender, _ = self.won_tender(rp)
        pid = new_id()
        rp.execute("project.start_rkd", {"id": pid, "tender_id": tender, "code": "P1", "title": "t"})
        # Глобальная роль rp без назначения в проекте не даёт права создавать комплект.
        r = rp.execute("kit.create", {"id": new_id(), "project_id": pid, "code": "K", "title": "K"})
        self.assertEqual(r.response["status"], "denied")
        aid = self.admin_cmd("project.assign", {"project_id": pid, "user_id": self.users["rp1"], "project_role": "rp"})["assignment_id"]
        self.assertEqual(rp.execute("kit.create", {"id": new_id(), "project_id": pid, "code": "K", "title": "K"}).status, "accepted")
        self.admin_cmd("project.revoke_assignment", {"assignment_id": aid, "reason": "перевод"})
        r = rp.execute("kit.create", {"id": new_id(), "project_id": pid, "code": "K2", "title": "K2"})
        self.assertEqual(r.response["status"], "denied")

    def test_second_gip_requires_explicit_revoke(self):
        rp = self.client("rp1")
        _, tender, _ = self.won_tender(rp)
        pid = new_id()
        rp.execute("project.start_rkd", {"id": pid, "tender_id": tender, "code": "P1", "title": "t"})
        self.admin_cmd("project.assign", {"project_id": pid, "user_id": self.users["gip1"], "project_role": "gip"})
        r = self.admin.execute("project.assign", {"project_id": pid, "user_id": self.users["head1"], "project_role": "gip"})
        self.assertEqual(r.response["code"], "gip_already_assigned")

    def _approved_tkp(self):
        rp = self.client("rp1")
        _, tender, _ = self.won_tender(rp)
        tkp = new_id()
        rp.execute("tkp.create", {"id": tkp, "tender_id": tender, "label": "ТКП-1", "amount": "100.00", "currency": "RUB"})
        head = self.client("head1")
        r = head.execute("tkp.approve", {"id": tkp}, expected_version=1)
        self.assertEqual(r.status, "accepted", r)
        return rp, head, tkp

    def test_self_approval_forbidden(self):
        head = self.client("head1")
        rp = self.client("rp1")
        _, tender, _ = self.won_tender(rp)
        tkp = new_id()
        head.execute("tkp.create", {"id": tkp, "tender_id": tender, "label": "ТКП-2"})
        r = head.execute("tkp.approve", {"id": tkp}, expected_version=1)
        self.assertEqual((r.response["status"], r.response["code"]), ("denied", "self_approval"))

    def test_revoke_then_regrant_does_not_revive_approval(self):
        rp, head, tkp = self._approved_tkp()
        self.admin_cmd("admin.revoke_role", {"grant_id": self.grants["head1"], "reason": "проверка"})
        self.admin_cmd("admin.grant_role", {"user_id": self.users["head1"], "role": "dept_head", "reason": "восстановление"})
        r = rp.execute("tkp.record_sent", {"id": tkp, "sent_on": "2026-11-16"}, expected_version=2)
        self.assertEqual((r.status, r.response["code"]), ("rejected", "approval_invalid"))
        # Новое согласование возможно только через явный возврат в черновик и повторное согласование.
        self.assertEqual(rp.execute("tkp.reopen", {"id": tkp, "reason": "переутверждение"}, expected_version=2).status, "accepted")
        self.assertEqual(head.execute("tkp.approve", {"id": tkp}, expected_version=3).status, "accepted")
        r = rp.execute("tkp.record_sent", {"id": tkp, "sent_on": "2026-11-16", "channel": "e-mail"}, expected_version=4)
        self.assertEqual(r.status, "accepted", r)
        self.assertEqual(r.response["result"]["tkp"]["status"], "sent_recorded")

    def test_approved_tkp_is_frozen(self):
        rp, head, tkp = self._approved_tkp()
        r = rp.execute("tkp.update", {"id": tkp, "amount": "200.00"}, expected_version=2)
        self.assertEqual(r.response["code"], "tkp_not_draft")

    def test_unknown_action_fails_closed(self):
        from vector_ref.errors import Denied
        with self.assertRaises(Denied):
            self.server.policy.rule("document.delete_everything")
