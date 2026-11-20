"""Проверка входных данных команд и сериализация — общая часть для всех модулей сервиса.

Правила:
* каждая функция либо возвращает нормализованное значение, либо поднимает Rejected с кодом и русским
  сообщением, которое клиент показывает пользователю как есть (поле указано в details.field);
* None в payload означает «явно очистить поле» (для необязательных полей), пустая строка — тоже «пусто»;
  отсутствие ключа — «не менять» (это решают обработчики через `"key" in p`);
* canon() — канонический JSON (сортировка ключей, без пробелов): из него считается хэш запроса для
  идемпотентности, поэтому формат менять нельзя без переходного периода.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from vector.core import money
from vector.core.errors import Rejected

# UUID в каноническом виде (нижний регистр): один и тот же объект не должен иметь два «разных» id.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Код валюты ISO 4217 (RUB, USD, …). Список валют не проверяется — валюты по умолчанию нет (A-19).
_CUR_RE = re.compile(r"^[A-Z]{3}$")
# ИНН: 10 цифр (организация) или 12 (ИП/физлицо). Контрольная сумма пока не проверяется (см. test-pc.md).
_INN_RE = re.compile(r"^(\d{10}|\d{12})$")
# Логин: латиница, цифры, точка, дефис, подчёркивание — 3…64 символа (без пробелов и кириллицы, чтобы
# не путать похожие буквы «а»/«a» при входе).
_LOGIN_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


def canon(obj: Any) -> str:
    """Канонический JSON: ключи отсортированы, без пробелов, UTF-8. По нему считается хэш запроса для защиты от
    повторов (идемпотентность) — формат менять нельзя.
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(o):
    """Как сериализовать нестандартные типы: время — UTC с микросекундами и «Z», дата — ГГГГ-ММ-ДД, Decimal — строка с
    4 знаками (никогда не float).
    """
    if isinstance(o, datetime):
        return o.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, Decimal):
        return money.to_json(o)
    raise TypeError(type(o))


def iso(dt: Optional[datetime]) -> Optional[str]:
    """Время для ответа клиенту в едином формате (UTC, «Z»); None остаётся None."""
    return None if dt is None else _json_default(dt)


def parse_iso_utc(v: object, field: str) -> datetime:
    """Разобрать время ISO-8601 из запроса. Время без часового пояса отвергается — иначе неясно, чьё это «10:00»."""
    if not isinstance(v, str):
        raise Rejected("invalid_datetime", f"Поле «{field}»: ожидается время ISO-8601 в UTC.", {"field": field})
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise Rejected("invalid_datetime", f"Поле «{field}»: неверный формат времени.", {"field": field})
    if dt.tzinfo is None:
        raise Rejected("invalid_datetime", f"Поле «{field}»: укажите часовой пояс (UTC).", {"field": field})
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------- валидация полей
def req_str(p: dict, key: str, max_len: int = 200) -> str:
    """Обязательная строка: непустая после обрезки пробелов и не длиннее max_len. Возвращает обрезанное значение."""
    v = p.get(key)
    if not isinstance(v, str) or not v.strip():
        raise Rejected("field_required", f"Поле «{key}» обязательно.", {"field": key})
    if len(v) > max_len:
        raise Rejected("field_too_long", f"Поле «{key}» длиннее {max_len} символов.", {"field": key})
    return v.strip()


def opt_str(p: dict, key: str, max_len: int = 4000) -> Optional[str]:
    """Необязательная строка: None или пусто → None (поле пустое), иначе проверка типа и длины."""
    v = p.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise Rejected("field_type", f"Поле «{key}» должно быть строкой.", {"field": key})
    if len(v) > max_len:
        raise Rejected("field_too_long", f"Поле «{key}» длиннее {max_len} символов.", {"field": key})
    return v.strip() or None


def req_uuid(p: dict, key: str) -> str:
    """Обязательный идентификатор: UUID в нижнем регистре. Идентификаторы новых объектов создаёт клиент (это позволяет
    работать офлайн и безопасно повторять команды).
    """
    v = p.get(key)
    if not isinstance(v, str) or not _UUID_RE.match(v):
        raise Rejected("invalid_id", f"Поле «{key}» должно быть UUID в нижнем регистре.", {"field": key})
    return v


def opt_date(p: dict, key: str) -> Optional[date]:
    """Необязательная дата ГГГГ-ММ-ДД; несуществующая дата (31.02) — ошибка."""
    v = p.get(key)
    if v is None:
        return None
    try:
        if not isinstance(v, str) or len(v) != 10:
            raise ValueError
        return date.fromisoformat(v)
    except ValueError:
        raise Rejected("invalid_date", f"Поле «{key}»: ожидается существующая дата ГГГГ-ММ-ДД.", {"field": key})


def opt_int(p: dict, key: str) -> Optional[int]:
    """Необязательное целое; bool (True/False в JSON) целым не считается."""
    v = p.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise Rejected("field_type", f"Поле «{key}» должно быть целым числом.", {"field": key})
    return v


def only_keys(p: dict, allowed: set[str]) -> None:
    """Отвергнуть неизвестные поля запроса. Опечатка в имени поля не должна молча игнорироваться — иначе пользователь
    думает, что значение сохранено.
    """
    extra = set(p) - allowed
    if extra:
        raise Rejected("unknown_fields", "Неизвестные поля: " + ", ".join(sorted(extra)), {"fields": sorted(extra)})
