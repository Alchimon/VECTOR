"""Типизированные исходы команд. Каждому соответствует понятное сообщение на русском."""
from __future__ import annotations

from typing import Any, Optional


class CommandOutcome(Exception):
    """Базовое исключение для «ожидаемых» исходов команды: отказ, запрет, конфликт, нет входа.

    Сервер ловит его в одном месте (Service.handle) и превращает в ответ {status, code, message_ru, details}; status
    берётся из атрибута класса. Неожиданные исключения (ошибки программы) этим путём не идут — они откатывают
    транзакцию и пишутся в журнал сервера без подробностей для клиента.
    """
    status = "rejected"

    def __init__(self, code: str, message_ru: str, details: Optional[dict[str, Any]] = None):
        """code — машинный код (стабилен, на него опираются клиент и тесты); message_ru — текст для пользователя;
        details — дополнительные данные (например, поле с ошибкой или текущая версия объекта).
        """
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
    """Нет действующего сеанса (токен неизвестен, истёк, отозван) — клиенту нужно войти заново."""
    status = "unauthenticated"
