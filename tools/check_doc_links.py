"""Проверка относительных ссылок в документации Markdown: файл существует, якорь (#раздел) существует.

Зачем: руководства ссылаются друг на друга и на разделы `docs/ops/*` («см. install.md, § 5»). При переименовании
заголовка ссылка молча ломается — пользователь попадает в начало документа. Скрипт ловит это до коммита.

Якоря вычисляются так же, как на GitHub: заголовок → нижний регистр → удаляются символы, кроме букв, цифр,
пробелов, «-» и «_» → пробелы заменяются на «-»; повторяющиеся заголовки получают суффикс «-1», «-2».
Внешние ссылки (http/https/mailto) не проверяются — у песочницы нет доступа в интернет, и это не цель скрипта.

Запуск: `python tools/check_doc_links.py` (код возврата 1 и список проблем, если есть битые ссылки).
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Где искать документы. Каталоги сборки/окружений не трогаем.
SCAN = ["README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "docs"]
SKIP_PARTS = {".venv", "node_modules", "build", "dist", ".git"}

LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")   # [текст](цель) — но не картинки ![..](..)
IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")          # картинки проверяем только на существование файла
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def slugify(title: str) -> str:
    """Якорь заголовка по правилам GitHub (без суффикса для дублей)."""
    title = re.sub(r"`([^`]*)`", r"\1", title)                       # `код` → код
    title = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)            # [текст](url) → текст
    out = []
    for ch in title.strip().lower():
        cat = unicodedata.category(ch)
        if ch in " -_" or cat[0] in ("L", "N") or cat == "Mn":
            out.append("-" if ch == " " else ch)
    return "".join(out)


def anchors_of(path: Path) -> set[str]:
    """Все якоря документа (заголовки вне блоков кода; дубли — с суффиксами -1, -2…)."""
    seen: dict[str, int] = {}
    result: set[str] = set()
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if not m:
            continue
        base = slugify(m.group(2))
        n = seen.get(base, 0)
        seen[base] = n + 1
        result.add(base if n == 0 else f"{base}-{n}")
    return result


def iter_docs() -> list[Path]:
    """Список проверяемых файлов .md."""
    files: list[Path] = []
    for entry in SCAN:
        p = ROOT / entry
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(f for f in sorted(p.rglob("*.md")) if not SKIP_PARTS.intersection(f.parts))
    return files


def check() -> list[str]:
    """Вернуть список проблем «файл:строка: описание» (пустой — всё в порядке)."""
    problems: list[str] = []
    cache: dict[Path, set[str]] = {}
    for doc in iter_docs():
        in_fence = False
        for no, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            if FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            line_wo_code = re.sub(r"`[^`]*`", "", line)   # ссылки внутри `кода` — это примеры, не ссылки
            targets = [(t, False) for t in LINK_RE.findall(line_wo_code)]
            targets += [(t, True) for t in IMG_RE.findall(line_wo_code)]
            for target, is_image in targets:
                if re.match(r"^[a-z]+:", target):        # http:, https:, mailto: — внешние
                    continue
                file_part, _, anchor = target.partition("#")
                dest = doc if not file_part else (doc.parent / file_part).resolve()
                rel = doc.relative_to(ROOT)
                if not dest.exists():
                    problems.append(f"{rel}:{no}: нет файла «{file_part}»")
                    continue
                if anchor and not is_image and dest.suffix == ".md":
                    if dest not in cache:
                        cache[dest] = anchors_of(dest)
                    if anchor not in cache[dest]:
                        problems.append(f"{rel}:{no}: в «{dest.relative_to(ROOT)}» нет раздела «#{anchor}»")
    return problems


def main() -> int:
    """Точка входа CLI: печатает проблемы и возвращает код 0/1."""
    problems = check()
    for p in problems:
        print(p)
    print("Ссылки в документации: OK" if not problems else f"Битых ссылок: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
