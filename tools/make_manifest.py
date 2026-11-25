#!/usr/bin/env python3
"""Манифест целостности поставки (D-85, карта соответствия — 54-compliance-fstek.md § 3.2).

Релиз сопровождается файлом MANIFEST.sha256: sha256 каждого файла поставки (формат sha256sum: «хэш␣␣путь»,
строки отсортированы). Администратор проверяет установку: `vector-server check` (package.manifest в server.toml)
сравнивает файлы с манифестом — изменённый или отсутствующий файл = поставка повреждена (обновление по
инструкции, вирус, правка в обход). Файлы, которых нет в манифесте (журналы, копии, загрузки), — не ошибка.

Использование:
    python tools/make_manifest.py <корень поставки> [--out MANIFEST.sha256]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# Служебное и изменяемое в работе — в манифест не входит (как в .gitignore проекта).
EXCLUDE_DIRS = {".git", "__pycache__", ".venv", "node_modules", "backups", "logs", ".pytest_cache"}
EXCLUDE_FILES = {"MANIFEST.sha256"}


def manifest_lines(root: Path) -> list[str]:
    """Строки манифеста для дерева root: «<sha256>  <относительный путь>», отсортированные по пути."""
    lines = []
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts) or f.name in EXCLUDE_FILES:
            continue
        h = hashlib.sha256()
        with f.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {rel.as_posix()}")
    lines.sort(key=lambda line: line.split("  ", 1)[1])
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="куда записать (по умолчанию — в stdout)")
    args = ap.parse_args()
    if not args.root.is_dir():
        print(f"Нет такого каталога: {args.root}", file=sys.stderr)
        return 2
    lines = manifest_lines(args.root)
    text = "\n".join(lines) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"Манифест: {args.out} — файлов: {len(lines)}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
