"""Встроенное руководство пользователя (issue #2): парсинг разделов и синхронизация упакованной копии."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from vector.client import help_text

ROOT = Path(__file__).resolve().parents[2]


def test_guide_sections_parsed_and_clean():
    """Разделы извлекаются по «## », снимки заменены подписями, разделители убраны."""
    sections = help_text.guide_sections(help_text.load_guide())
    assert len(sections) >= 20, "в руководстве 21+ раздел — проверьте парсер"
    titles = [s["title"] for s in sections]
    assert "1. Главное за минуту" in titles[0] and any("Сокращения" in t for t in titles)
    for s in sections:
        assert s["body"], f"пустой раздел: {s['title']}"
        assert "![" not in s["body"], f"разметка снимка осталась в разделе {s['title']}"
        assert "\n---" not in s["body"]
    first = sections[0]["body"]
    assert "Сервер — главный" in first
    # Подписи снимков заменяют разметку изображений там, где они были.
    assert any("снимок экрана" in s["body"].lower() for s in sections)


def test_bundled_guide_matches_source():
    """Упакованная копия (src/vector/client/help/user-guide.md) синхронизирована с docs/manual/user-guide.md."""
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "sync_help.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
