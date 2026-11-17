"""Загрузка языконезависимых контрактов.

Источник истины — каталог /contracts репозитория. При сборке пакета контракты копируются
в vector/core/contracts_data (см. tools/sync_contracts.py); тест проверяет, что копия совпадает.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent / "contracts_data"
_REPO_DIR = Path(__file__).resolve().parents[3] / "contracts"


def contracts_dir() -> Path:
    """Каталог контрактов: в репозитории — /contracts (источник истины), в установленном пакете — копия contracts_data.
    """
    return _REPO_DIR if _REPO_DIR.is_dir() else _PKG_DIR


@dataclass(frozen=True)
class Contracts:
    """Контракты системы, загруженные из JSON.

    * catalog — каталог команд: подпись, офлайн-класс (offline_full / queued / online_required);
    * permissions — правила прав «действие × роль × контекст проекта» (их исполняет server.authz.Policy);
    * workflows — допустимые переходы статусов (тендер, ТКП, проект, задача, резерв);
    * settings — каталог настроек (ключ, допустимые значения, самое строгое значение «если не задано», кто
      публикует). Настройка вне каталога не существует: произвольных ключей нет (07 §4).

    Одни и те же файлы читают сервер и клиент, поэтому подсказки интерфейса и решения сервера не расходятся.
    """
    catalog: dict
    permissions: dict
    workflows: dict
    settings: dict = field(default_factory=lambda: {"items": {}})

    @staticmethod
    def load(directory: Path | None = None) -> "Contracts":
        """Прочитать все файлы контрактов из каталога (по умолчанию — contracts_dir())."""
        d = directory or contracts_dir()

        def read(name: str) -> dict:
            """Прочитать один JSON-файл контракта в UTF-8."""
            with open(d / name, encoding="utf-8") as f:
                return json.load(f)

        return Contracts(read("command-catalog.slice1.json"), read("permissions.slice1.json"), read("workflows.slice1.json"),
                         read("settings.slice1.json"))

    def offline_class(self, command: str) -> str:
        """Офлайн-класс команды: можно ли её выполнить без связи (queued — в очередь) или нужна связь
        (online_required).
        """
        return self.catalog["commands"][command]["offline"]

    def label(self, command: str) -> str:
        """Русская подпись команды для уведомлений и истории («Создать заказчика»)."""
        return self.catalog["commands"][command]["label_ru"]
