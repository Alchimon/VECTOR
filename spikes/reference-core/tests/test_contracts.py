"""Согласованность контрактов: каталог команд ↔ матрица прав ↔ обработчики."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vector_ref.contracts import Contracts  # noqa: E402

ASSIGN = {"project.assign", "project.revoke_assignment"}


class ContractConsistency(unittest.TestCase):
    def setUp(self):
        self.c = Contracts.load()

    def test_every_command_has_rule_and_offline_class(self):
        rules = self.c.permissions["rules"]
        for cmd, meta in self.c.catalog["commands"].items():
            with self.subTest(cmd=cmd):
                self.assertIn(meta["offline"], self.c.catalog["offline_classes"])
                if cmd in ASSIGN:
                    for r in ("gip", "rp", "participant"):
                        self.assertIn(f"project.assign_{r}", rules)
                else:
                    self.assertIn(cmd, rules)

    def test_gip_assignment_admin_only(self):
        self.assertEqual(self.c.permissions["rules"]["project.assign_gip"]["any_of"], [{"global_role": ["admin"]}])

    def test_final_effects_are_online_required(self):
        for cmd in ("tender.transition", "tkp.approve", "tkp.record_sent", "project.start_rkd", "project.assign",
                    "admin.grant_role", "admin.revoke_role"):
            self.assertEqual(self.c.offline_class(cmd), "online_required", cmd)

    def test_rule_roles_exist(self):
        roles = set(self.c.permissions["roles"])
        proles = set(self.c.permissions["project_roles"])
        for name, rule in self.c.permissions["rules"].items():
            for cond in rule.get("any_of", []):
                self.assertTrue(set(cond.get("global_role", [])) <= roles, name)
                self.assertTrue(set(cond.get("project_role", [])) <= proles, name)


if __name__ == "__main__":
    unittest.main()
