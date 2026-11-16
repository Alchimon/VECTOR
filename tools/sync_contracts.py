"""Копирует /contracts в пакет (src/vector/core/contracts_data) для установки без исходников.

  python tools/sync_contracts.py          # скопировать
  python tools/sync_contracts.py --check  # только проверить совпадение (код 1 при расхождении)
"""
from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "contracts"
DST = ROOT / "src" / "vector" / "core" / "contracts_data"


def files():
    return sorted(p.name for p in SRC.glob("*.json"))


def check() -> list[str]:
    bad = []
    for n in files():
        if not (DST / n).exists() or not filecmp.cmp(SRC / n, DST / n, shallow=False):
            bad.append(n)
    extra = sorted({p.name for p in DST.glob("*.json")} - set(files())) if DST.exists() else []
    return bad + [f"лишний: {e}" for e in extra]


def main():
    if "--check" in sys.argv:
        bad = check()
        print("contracts_data: OK" if not bad else "contracts_data расходится: " + ", ".join(bad))
        raise SystemExit(1 if bad else 0)
    DST.mkdir(parents=True, exist_ok=True)
    for p in DST.glob("*.json"):
        p.unlink()
    for n in files():
        shutil.copy2(SRC / n, DST / n)
    print(f"Скопировано {len(files())} файлов в {DST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
