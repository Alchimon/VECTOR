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

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CUR_RE = re.compile(r"^[A-Z]{3}$")
_INN_RE = re.compile(r"^(\d{10}|\d{12})$")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
def canon(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(o):
    if isinstance(o, datetime):
        return o.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, Decimal):
        return money.to_json(o)
    raise TypeError(type(o))


def iso(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else _json_default(dt)


def parse_iso_utc(v: object, field: str) -> datetime:
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
    v = p.get(key)
    if not isinstance(v, str) or not v.strip():
        raise Rejected("field_required", f"Поле «{key}» обязательно.", {"field": key})
    if len(v) > max_len:
        raise Rejected("field_too_long", f"Поле «{key}» длиннее {max_len} символов.", {"field": key})
    return v.strip()


def opt_str(p: dict, key: str, max_len: int = 4000) -> Optional[str]:
    v = p.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise Rejected("field_type", f"Поле «{key}» должно быть строкой.", {"field": key})
    if len(v) > max_len:
        raise Rejected("field_too_long", f"Поле «{key}» длиннее {max_len} символов.", {"field": key})
    return v.strip() or None


def req_uuid(p: dict, key: str) -> str:
    v = p.get(key)
    if not isinstance(v, str) or not _UUID_RE.match(v):
        raise Rejected("invalid_id", f"Поле «{key}» должно быть UUID в нижнем регистре.", {"field": key})
    return v


def opt_date(p: dict, key: str) -> Optional[date]:
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
    v = p.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise Rejected("field_type", f"Поле «{key}» должно быть целым числом.", {"field": key})
    return v


def only_keys(p: dict, allowed: set[str]) -> None:
    extra = set(p) - allowed
    if extra:
        raise Rejected("unknown_fields", "Неизвестные поля: " + ", ".join(sorted(extra)), {"fields": sorted(extra)})
