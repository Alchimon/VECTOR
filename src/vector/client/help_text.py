"""Встроенное руководство пользователя (issue #2, «справочный центр»; этап 7).

Текст `docs/manual/user-guide.md` встроен в клиент упакованной копией `help/user-guide.md`
(синхронизация — `tools/sync_help.py`, расхождение ловит тест) и доступен в разделе «Справка»
(F1 → вкладка «Руководство») полностью без связи. Снимки экранов в клиент не упаковываются —
в тексте они заменены курсивными подписями.
"""
from __future__ import annotations

import re
from importlib import resources

_IMG_LINE = re.compile(r"^\s*!\[([^\]]*)\]\([^)]*\)\s*$")
_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.M)


def load_guide() -> str:
    """Текст встроенного руководства (упакованная копия docs/manual/user-guide.md)."""
    return resources.files("vector.client").joinpath("help/user-guide.md").read_text(encoding="utf-8")


def _body(md: str) -> str:
    """Тело раздела для экрана: строки-снимки → курсивные подписи; разделители «---» убраны."""
    out = []
    for ln in md.strip().splitlines():
        m = _IMG_LINE.match(ln)
        if m:
            cap = m.group(1).strip()
            if cap:
                out.append(f"*{cap} — снимок экрана, есть в файле руководства.*")
            continue
        if ln.strip() == "---":
            continue
        out.append(ln)
    return "\n".join(out).strip()


def guide_sections(text: str) -> list[dict]:
    """Разделы руководства по заголовкам «## …»: [{id, title, body}]. Шапка файла (до первого «##») не выводится."""
    matches = list(_SECTION.finditer(text))
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append({"id": f"s{i}", "title": m.group(1), "body": _body(text[m.end():end])})
    return out
