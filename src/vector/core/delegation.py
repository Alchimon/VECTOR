"""Замещение (передача роли на время) — правила, общие для сервера и клиента (D-34, ответ владельца на A-37).

Владелец: «все и вся должны быть заменяемыми, не должно возникать ситуации, что если нет человека, невозможно было
решить какой-то вопрос или кому-то делегировать». Отсюда правила (временные предположения — A-40, Decision Log):

* передаётся **глобальная роль доступа** (кроме «Администратор»: её выдаёт только администратор — A-09);
* на **срок** — с даты по дату включительно, не дольше MAX_DAYS дней (постоянная замена — обычная выдача роли
  администратором); начало может быть в будущем («с понедельника, на время отпуска»), но не в прошлом;
* **причина обязательна** («отпуск», «командировка», «больничный») — она видна в истории и в карточке замещения;
* передаёт **сам носитель роли**; если его нет на месте — **руководитель отдела или администратор** от его имени
  (иначе отсутствие человека как раз и блокировало бы работу);
* **без цепочек**: замещающий не может передать полученную роль дальше — только свою собственную;
* носитель роль **не теряет**: на время замещения решать могут оба; замещающий действует под своим именем, в
  журнале и истории — «Иванов (замещает Зайцеву)»;
* замещение действует, только пока у самого носителя есть эта роль (собственная выдача, не полученная
  замещением): отозвали или истекла его роль — замещение перестаёт действовать сразу (состояние
  «приостановлено»); снова выдали роль носителю в пределах срока — замещение снова действует;
* отменить может носитель, замещающий (отказаться), тот, кто оформил, руководитель отдела и администратор.

Модуль чистый (без БД и Qt). Даты — календарные (date), «сегодня» — дата сервера (как у договоров поставщиков).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from .errors import Rejected

MAX_DAYS = 366
# Роли, которые нельзя передать замещением (A-09: роль администратора выдаёт только администратор).
NON_DELEGABLE = frozenset({"admin"})

STATE_LABELS_RU = {"planned": "Запланировано", "active": "Действует", "ended": "Завершено", "revoked": "Отменено",
                   "suspended": "Приостановлено: у носителя нет этой роли"}
STATE_TONES = {"planned": "info", "active": "success", "ended": "neutral", "revoked": "neutral", "suspended": "warning"}


def parse_day(value: Any, field: str) -> Optional[date]:
    """Дата «ГГГГ-ММ-ДД» из протокола → date; None/пусто → None; иначе Rejected (поле в details)."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise Rejected("invalid_date", "Дата — строка ГГГГ-ММ-ДД.", {"field": field})
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Rejected("invalid_date", "Дата — в формате ГГГГ-ММ-ДД (например, 2026-12-01).", {"field": field})


def check_period(valid_from: Optional[date], valid_until: Optional[date], today: date) -> tuple[date, date]:
    """Проверить срок замещения: с (по умолчанию сегодня, не в прошлом) по (обязательно, ≥ с, не дольше MAX_DAYS)."""
    start = valid_from or today
    if start < today:
        raise Rejected("invalid_period", "Начало замещения не может быть в прошлом.", {"field": "valid_from"})
    if valid_until is None:
        raise Rejected("field_required", "Укажите, по какое число действует замещение (включительно).",
                       {"field": "valid_until"})
    if valid_until < start:
        raise Rejected("invalid_period", "Дата окончания раньше даты начала.", {"field": "valid_until"})
    if (valid_until - start).days + 1 > MAX_DAYS:
        raise Rejected("invalid_period", f"Замещение — временное: не дольше {MAX_DAYS} дней. Постоянную замену "
                       "оформляет администратор выдачей роли.", {"field": "valid_until"})
    return start, valid_until


def check_role(role: Any, known_roles: list[str]) -> str:
    """Роль для передачи: известная глобальная роль и не «Администратор»."""
    if role not in known_roles:
        raise Rejected("bad_role", "Неизвестная роль.", {"field": "role"})
    if role in NON_DELEGABLE:
        raise Rejected("role_not_delegable", "Роль «Администратор» замещением не передаётся — её выдаёт только "
                       "администратор.", {"field": "role"})
    return role


def state(valid_from: date, valid_until: date, revoked: bool, holder_has_role: bool, today: date) -> str:
    """Состояние замещения на дату today: planned / active / ended / revoked / suspended (нет роли у носителя)."""
    if revoked:
        return "revoked"
    if valid_until < today:
        return "ended"
    if valid_from > today:
        return "planned"
    return "active" if holder_has_role else "suspended"


def is_active(valid_from: date, valid_until: date, revoked: bool, today: date) -> bool:
    """Действует ли замещение по сроку и отмене (роль носителя проверяется отдельно, в той же транзакции)."""
    return not revoked and valid_from <= today <= valid_until


def period_label(valid_from: date, valid_until: date) -> str:
    """«с 16.11.2026 по 30.11.2026» (включительно)."""
    return f"с {valid_from:%d.%m.%Y} по {valid_until:%d.%m.%Y}"


def default_until(today: date, days: int = 14) -> date:
    """Подсказка для формы: срок по умолчанию — две недели (типичный отпуск); пользователь меняет."""
    return today + timedelta(days=days - 1)
