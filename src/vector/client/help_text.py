"""Встроенное руководство пользователя (issue #2, «справочный центр»; этап 7).

Текст `docs/manual/user-guide.md` встроен в клиент упакованной копией `help/user-guide.md`
(синхронизация — `tools/sync_help.py`, расхождение ловит тест) и доступен в разделе «Справка»
(F1 → вкладка «Руководство») полностью без связи. Снимки экранов в клиент не упаковываются —
в тексте они заменены курсивными подписями.
"""
from __future__ import annotations

import re
from importlib import resources

_IMG_LINE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]*)\)\s*$")
_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.M)


def load_guide() -> str:
    """Текст встроенного руководства (упакованная копия docs/manual/user-guide.md)."""
    return resources.files("vector.client").joinpath("help/user-guide.md").read_text(encoding="utf-8")


def _img_name(target: str) -> str:
    """Имя файла снимка из ссылки руководства («../screenshots/light-01-login.png» → «light-01-login.png»);
    None, если снимка нет в упакованной папке справки."""
    name = target.rsplit("/", 1)[-1].strip()
    return name if name and SCREENSHOTS.joinpath(name).exists() else ""


SCREENSHOTS = resources.files("vector.client").joinpath("help/screenshots")


def _body(md: str) -> tuple[str, list[dict]]:
    """Тело раздела для экрана: строки-снимки собираются в список images [{src, caption}] (папка
    help/screenshots — упакованная копия курируемых снимков справки), разделители «---» убраны.
    Снимок, которого нет в упакованной папке, остаётся курсивной подписью."""
    out, images = [], []
    for ln in md.strip().splitlines():
        m = _IMG_LINE.match(ln)
        if m:
            cap, name = m.group(1).strip(), _img_name(m.group(2))
            if name:
                images.append({"src": SCREENSHOTS.joinpath(name).as_posix(), "caption": cap})
            elif cap:
                out.append(f"*{cap} — снимок экрана, есть в файле руководства.*")
            continue
        if ln.strip() == "---":
            continue
        out.append(ln)
    return "\n".join(out).strip(), images


def guide_sections(text: str) -> list[dict]:
    """Разделы руководства по заголовкам «## …»: [{id, title, body}]. Шапка файла (до первого «##») не выводится."""
    matches = list(_SECTION.finditer(text))
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body, images = _body(text[m.end():end])
        out.append({"id": f"s{i}", "title": m.group(1), "body": body, "images": images})
    return out
