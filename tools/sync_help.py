#!/usr/bin/env python3
"""Синхронизация встроенной копии руководства пользователя.

`docs/manual/user-guide.md` — источник; `src/vector/client/help/user-guide.md` — упакованная в клиент копия
(раздел «Справка → Руководство», работает без связи). Запускать после каждой правки руководства;
расхождение ловит тест (`tests/client/test_help_text.py`, режим `--check`).
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "manual" / "user-guide.md"
DST = ROOT / "src" / "vector" / "client" / "help" / "user-guide.md"


def main() -> int:
    if "--check" in sys.argv:
        if not DST.exists() or SRC.read_bytes() != DST.read_bytes():
            print("help/user-guide.md устарела: запустите python tools/sync_help.py")
            return 1
        print("help: OK")
        return 0
    DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SRC, DST)
    print(f"Скопировано: {SRC.relative_to(ROOT)} → {DST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
