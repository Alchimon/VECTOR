"""Документация: все относительные ссылки и якоря ведут в существующие файлы и разделы.

Руководства (docs/manual) ссылаются на разделы docs/ops и друг на друга; переименование заголовка молча ломает
ссылку. Проверка — tools/check_doc_links.py (те же правила якорей, что у GitHub).
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("check_doc_links", ROOT / "tools" / "check_doc_links.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_broken_doc_links():
    problems = _load().check()
    assert problems == [], "\n".join(problems)


def test_slug_rules_match_github():
    m = _load()
    assert m.slugify("7. Пользователи, роли и доступ") == "7-пользователи-роли-и-доступ"
    assert m.slugify("9. Обновление ✅ (SQLite, PostgreSQL)") == "9-обновление--sqlite-postgresql"
    assert m.slugify("Служба `systemd`") == "служба-systemd"


def test_checker_detects_broken_anchor(tmp_path, monkeypatch):
    m = _load()
    (tmp_path / "a.md").write_text("# Раздел\n[ок](#раздел) [нет](#другой) [файл](b.md)\n", encoding="utf-8")
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.setattr(m, "SCAN", ["a.md"])
    problems = m.check()
    assert len(problems) == 2
    assert any("#другой" in p for p in problems) and any("b.md" in p for p in problems)
