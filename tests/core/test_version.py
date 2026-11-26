"""Версия пакета одна и та же в pyproject.toml и vector.__version__ (релизный контур, docs/ops/release.md § 1)."""
from __future__ import annotations

import re
from pathlib import Path

import vector


def test_version_matches_pyproject():
    root = Path(__file__).resolve().parent.parent.parent
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    assert m is not None
    assert vector.__version__ == m.group(1)
