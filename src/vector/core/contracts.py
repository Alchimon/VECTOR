"""Загрузка языконезависимых контрактов.

Источник истины — каталог /contracts репозитория. При сборке пакета контракты копируются
в vector/core/contracts_data (см. tools/sync_contracts.py); тест проверяет, что копия совпадает.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent / "contracts_data"
_REPO_DIR = Path(__file__).resolve().parents[3] / "contracts"


def contracts_dir() -> Path:
    return _REPO_DIR if _REPO_DIR.is_dir() else _PKG_DIR


@dataclass(frozen=True)
class Contracts:
    catalog: dict
    permissions: dict
    workflows: dict

    @staticmethod
    def load(directory: Path | None = None) -> "Contracts":
        d = directory or contracts_dir()

        def read(name: str) -> dict:
            with open(d / name, encoding="utf-8") as f:
                return json.load(f)

        return Contracts(read("command-catalog.slice1.json"), read("permissions.slice1.json"), read("workflows.slice1.json"))

    def offline_class(self, command: str) -> str:
        return self.catalog["commands"][command]["offline"]

    def label(self, command: str) -> str:
        return self.catalog["commands"][command]["label_ru"]
