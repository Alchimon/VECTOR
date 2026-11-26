"""Векторы денег из contracts/money-vectors.json (общие для всех реализаций)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vector_ref import money  # noqa: E402
from vector_ref.contracts import DEFAULT_DIR  # noqa: E402


class MoneyVectors(unittest.TestCase):
    def test_vectors(self):
        with open(DEFAULT_DIR / "money-vectors.json", encoding="utf-8") as f:
            data = json.load(f)
        self.assertGreaterEqual(len(data["vectors"]), 20)
        for v in data["vectors"]:
            with self.subTest(v=v):
                t = v.get("input_type", "string")
                raw = {"string": lambda x: x, "float": float, "bool": lambda x: x == "true", "null": lambda x: None}[t](v["input"])
                if v["ok"]:
                    d = money.parse_optional(raw)
                    if v.get("unknown"):
                        self.assertIsNone(d)
                        self.assertIsNone(money.to_json(d))
                        continue
                    self.assertEqual(money.to_minor(d), v["minor"])
                    self.assertEqual(money.to_json(d), v["json"])
                    self.assertEqual(money.to_minor(money.from_minor(v["minor"])), v["minor"])
                else:
                    with self.assertRaises(money.MoneyError) as cm:
                        money.parse_optional(raw)
                    self.assertEqual(cm.exception.code, v["error"])

    def test_no_float_anywhere_in_round_trip(self):
        d = money.parse_decimal("0.1")
        total = sum((d for _ in range(3)), money.parse_decimal("0"))
        self.assertEqual(money.to_json(total), "0.3000")  # float дал бы 0.30000000000000004


if __name__ == "__main__":
    unittest.main()
