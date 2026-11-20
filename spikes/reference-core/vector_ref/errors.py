"""Типизированные исходы команд. Каждому соответствует понятное сообщение на русском."""
from __future__ import annotations

from typing import Any, Optional


class CommandOutcome(Exception):
    status = "rejected"

    def __init__(self, code: str, message_ru: str, details: Optional[dict[str, Any]] = None):
        super().__init__(f"{code}: {message_ru}")
        self.code = code
        self.message_ru = message_ru
        self.details = details or {}


class Rejected(CommandOutcome):
    """Нарушение валидации или состояния. Повтор с теми же данными даст тот же результат."""
    status = "rejected"


class Denied(CommandOutcome):
    """Нет действующего полномочия в момент эффекта."""
    status = "denied"


class Conflict(CommandOutcome):
    """Объект изменился с версии, на которой основано изменение клиента."""
    status = "conflict"


class Unauthenticated(CommandOutcome):
    status = "unauthenticated"
