"""Загрузка языконезависимых контрактов из каталога contracts/ репозитория."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "contracts"


@dataclass(frozen=True)
class Contracts:
    catalog: dict
    permissions: dict
    workflows: dict

    @staticmethod
    def load(directory: Path = DEFAULT_DIR) -> "Contracts":
        def read(name: str) -> dict:
            with open(directory / name, encoding="utf-8") as f:
                return json.load(f)

        return Contracts(
            catalog=read("command-catalog.slice1.json"),
            permissions=read("permissions.slice1.json"),
            workflows=read("workflows.slice1.json"),
        )

    def offline_class(self, command: str) -> str:
        return self.catalog["commands"][command]["offline"]
