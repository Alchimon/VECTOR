"""Правила модулей и лицензий платформы (D-80, A-64; docs/blueprint/53-ecosystem.md)."""
from __future__ import annotations

import json
import unittest
from datetime import date

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vector.core import modules as M

TODAY = date(2026, 11, 25)


def make_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def pub_pem(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)


def sign(key: Ed25519PrivateKey, payload: dict) -> str:
    import base64
    return base64.b64encode(key.sign(M.canonical_payload(payload))).decode("ascii")


def lic_text(key: Ed25519PrivateKey, **over) -> str:
    payload = {"license_id": "L-2026-001", "customer": "ООО «Завод»", "issued_at": "2026-11-01",
               "kind": "subscription", "valid_until": "2026-12-31", "support_until": "2026-12-31",
               "modules": ["pdm", "supply"], "seats": 10, "grace_days": 14, "key_id": "dev"}
    payload.update(over)
    return json.dumps({"payload": payload, "signature": sign(key, payload)}, ensure_ascii=False)


class TestRegistry(unittest.TestCase):
    def test_modules_unique_and_known(self):
        self.assertEqual(len(M.MODULES), len(M.MODULE_BY_ID))
        self.assertIn("pdm", M.CURRENT_MODULE_IDS)
        self.assertIn("schema", M.PLANNED_MODULE_IDS)
        self.assertNotIn("core", M.MODULE_BY_ID)                       # ядро не продаётся

    def test_state_labels_cover_all_states(self):
        self.assertEqual(sorted(M.MODULE_STATE_LABELS_RU), sorted(M.MODULE_STATES))


class TestLicenseFile(unittest.TestCase):
    def setUp(self):
        self.key = make_key()
        self.pub = serialization.load_pem_public_key(pub_pem(self.key))

    def test_roundtrip(self):
        lic = M.load_license(lic_text(self.key), self.pub)
        self.assertEqual((lic.license_id, lic.kind, lic.seats), ("L-2026-001", "subscription", 10))
        self.assertEqual(lic.modules, frozenset({"pdm", "supply"}))

    def test_forged_signature_rejected(self):
        other = make_key()
        with self.assertRaises(M.LicenseError) as cm:
            M.load_license(lic_text(other), self.pub)
        self.assertEqual(cm.exception.code, "bad_signature")

    def test_tampered_payload_rejected(self):
        doc = json.loads(lic_text(self.key))
        doc["payload"]["seats"] = 999                                  # подпись уже не сойдётся
        with self.assertRaises(M.LicenseError) as cm:
            M.load_license(json.dumps(doc), self.pub)
        self.assertEqual(cm.exception.code, "bad_signature")

    def test_bad_json_and_schema(self):
        with self.assertRaises(M.LicenseError) as cm:
            M.load_license("не json", self.pub)
        self.assertEqual(cm.exception.code, "bad_json")
        bad = {"license_id": "x", "customer": "y", "issued_at": "2026-01-01", "kind": "forever"}
        with self.assertRaises(M.LicenseError):
            M.load_license(json.dumps({"payload": bad, "signature": sign(self.key, bad)}), self.pub)
        bad2 = {"license_id": "x", "customer": "y", "issued_at": "2026-01-01", "kind": "subscription",
                "modules": [], "seats": None}                          # подписка без valid_until
        with self.assertRaises(M.LicenseError) as cm:
            M.load_license(json.dumps({"payload": bad2, "signature": sign(self.key, bad2)}), self.pub)
        self.assertEqual(cm.exception.details.get("field"), "valid_until")

    def test_seats_and_grace_validation(self):
        with self.assertRaises(M.LicenseError):
            M.load_license(lic_text(self.key, seats=0), self.pub)
        with self.assertRaises(M.LicenseError):
            M.load_license(lic_text(self.key, grace_days=500), self.pub)

    def test_unknown_module_ignored_not_fatal(self):
        lic = M.load_license(lic_text(self.key, modules=["pdm", "hyperdrive"]), self.pub)
        self.assertEqual(lic.modules, frozenset({"pdm"}))


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self.key = make_key()
        self.pub = serialization.load_pem_public_key(pub_pem(self.key))

    def st(self, lic, module="pdm"):
        return M.evaluate(lic, TODAY).modules[module].state

    def test_no_license_is_demo_all_current_active(self):
        s = M.evaluate(None, TODAY)
        self.assertEqual(s.mode, "demo")
        self.assertTrue(all(s.modules[m].state == "active" for m in M.CURRENT_MODULE_IDS))
        self.assertEqual(s.modules["schema"].state, "planned")

    def test_subscription_active_grace_readonly(self):
        lic = M.load_license(lic_text(self.key), self.pub)
        self.assertEqual(self.st(lic), "active")                       # до 2026-12-31
        grace = M.evaluate(lic, date(2027, 1, 10)).modules["pdm"]      # +10 дней после истечения
        self.assertEqual(grace.state, "grace")
        self.assertIn("Данные сохраняются", grace.detail_ru)
        ro = M.evaluate(lic, date(2027, 1, 20)).modules["pdm"]         # после льготного периода
        self.assertEqual(ro.state, "readonly")
        self.assertIn("выгружать можно всегда", ro.detail_ru)          # данные не отнимаем

    def test_perpetual_never_expires_but_support_does(self):
        lic = M.load_license(lic_text(self.key, kind="perpetual", valid_until=None), self.pub)
        self.assertEqual(M.evaluate(lic, date(2035, 1, 1)).modules["pdm"].state, "active")
        s = M.evaluate(lic, date(2027, 6, 1))
        self.assertIn("истекла", s.support_label_ru)

    def test_not_licensed_and_planned(self):
        lic = M.load_license(lic_text(self.key, modules=["pdm"]), self.pub)
        self.assertEqual(self.st(lic, "supply"), "not_licensed")
        self.assertEqual(self.st(lic, "cad"), "planned")

    def test_support_labels(self):
        lic = M.load_license(lic_text(self.key, support_until=None), self.pub)
        self.assertEqual(M.evaluate(lic, TODAY).support_label_ru, "Поддержка не включена")



class TestCommandModuleMap(unittest.TestCase):
    def test_command_module_covers_catalog(self):
        import json as _json
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent
        cat = _json.loads((root / "contracts" / "command-catalog.slice1.json").read_text(encoding="utf-8"))
        for cmd in cat["commands"]:
            mod = M.command_module(cmd)
            self.assertTrue(mod is None or mod in M.CURRENT_MODULE_IDS,
                            f"{cmd} → неизвестный модуль {mod}")
        # и обратно: у каждого выпущенного модуля есть команды или это сознательно пусто (analytics/integrations
        # — чтение и конфигурация, без команд записи)
        used = {M.command_module(c) for c in cat["commands"]} - {None}
        self.assertEqual(used, M.CURRENT_MODULE_IDS - {"analytics", "integrations"})

    def test_platform_commands_stay_free(self):
        for cmd in ("account.change_password", "admin.create_user", "config.publish",
                    "delegation.create", "notification.read", "org.unit_create"):
            self.assertIsNone(M.command_module(cmd))

if __name__ == "__main__":
    unittest.main()
