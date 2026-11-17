"""Контракты (каталог ↔ права ↔ обработчики ↔ упакованная копия) и векторы денег."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from vector.core import money
from vector.core.contracts import Contracts, contracts_dir

ROOT = Path(__file__).resolve().parents[2]
ASSIGN = {"project.assign", "project.revoke_assignment"}
C = Contracts.load()


def test_packaged_copy_matches_repo():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "sync_contracts.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_command_has_rule_and_offline_class():
    rules = C.permissions["rules"]
    for cmd, meta in C.catalog["commands"].items():
        assert meta["offline"] in C.catalog["offline_classes"], cmd
        if cmd in ASSIGN:
            for r in ("gip", "rp", "participant"):
                assert f"project.assign_{r}" in rules
        else:
            assert cmd in rules, cmd


def test_gip_assignment_admin_only():
    assert C.permissions["rules"]["project.assign_gip"]["any_of"] == [{"global_role": ["admin"]}]


@pytest.mark.parametrize("cmd", ["tender.transition", "tkp.approve", "tkp.record_sent", "project.start_rkd", "project.assign",
                                 "admin.grant_role", "admin.revoke_role", "account.change_password", "admin.reset_password",
                                 "admin.set_user_active"])
def test_final_effects_are_online_required(cmd):
    assert C.offline_class(cmd) == "online_required"


def test_rule_roles_exist():
    roles, proles = set(C.permissions["roles"]), set(C.permissions["project_roles"])
    for name, rule in C.permissions["rules"].items():
        for cond in rule.get("any_of", []):
            assert set(cond.get("global_role", [])) <= roles, name
            assert set(cond.get("project_role", [])) <= proles, name


def _vectors():
    """Эталонные примеры разбора денег из contracts/money-vectors.json (общие для всех реализаций)."""
    return json.loads((contracts_dir() / "money-vectors.json").read_text(encoding="utf-8"))["vectors"]


@pytest.mark.parametrize("v", _vectors(), ids=lambda v: repr(v["input"])[:30])
def test_money_vectors(v):
    t = v.get("input_type", "string")
    raw = {"string": lambda x: x, "float": float, "bool": lambda x: x == "true", "null": lambda x: None}[t](v["input"])
    if v["ok"]:
        d = money.parse_optional(raw)
        if v.get("unknown"):
            assert d is None and money.to_json(d) is None
            return
        assert money.to_minor(d) == v["minor"] and money.to_json(d) == v["json"]
        assert money.to_minor(money.from_minor(v["minor"])) == v["minor"]
    else:
        with pytest.raises(money.MoneyError) as e:
            money.parse_optional(raw)
        assert e.value.code == v["error"]


def test_no_float_in_sum():
    d = money.parse_decimal("0.1")
    assert money.to_json(sum((d for _ in range(3)), money.parse_decimal("0"))) == "0.3000"
