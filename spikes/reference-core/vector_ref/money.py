"""Деньги: Decimal, не более 4 дробных знаков, диапазон NUMERIC(18,4).

* float запрещён на входе (никакого Decimal->float и обратно);
* None = «неизвестно», пустая строка = ошибка формата, "0" = известный ноль;
* хранение в SQLite — INTEGER в десятитысячных; в JSON — десятичная строка;
* молчаливого округления нет: лишние знаки -> ошибка.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

SCALE = 4
MAX_ABS = Decimal("99999999999999.9999")
_RE = re.compile(r"^-?(0|[1-9][0-9]*)(\.([0-9]+))?$")


class MoneyError(ValueError):
    def __init__(self, code: str, message_ru: str):
        super().__init__(f"{code}: {message_ru}")
        self.code = code
        self.message_ru = message_ru


def parse_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise MoneyError("invalid_type", "Ожидалась десятичная строка, получено логическое значение.")
    if isinstance(value, float):
        raise MoneyError("float_forbidden", "Двоичные числа с плавающей точкой запрещены — передайте десятичную строку.")
    if not isinstance(value, str):
        raise MoneyError("invalid_type", "Ожидалась десятичная строка.")
    m = _RE.match(value)
    if not m:
        raise MoneyError("invalid_format", "Неверный формат числа: допускаются цифры, знак «-» и точка; без пробелов, экспоненты и запятой.")
    frac = m.group(3) or ""
    if len(frac) > SCALE:
        raise MoneyError("too_many_fraction_digits", f"Больше {SCALE} знаков после точки: значение не округляется автоматически.")
    d = Decimal(value)
    if abs(d) > MAX_ABS:
        raise MoneyError("out_of_range", "Значение вне допустимого диапазона NUMERIC(18,4).")
    return d


def parse_optional(value: object) -> Optional[Decimal]:
    """None -> неизвестно (None). Любое другое значение разбирается строго."""
    if value is None:
        return None
    return parse_decimal(value)


def to_minor(d: Decimal) -> int:
    scaled = d.scaleb(SCALE)
    if scaled != scaled.to_integral_value():
        raise MoneyError("too_many_fraction_digits", "Внутренняя ошибка точности.")
    return int(scaled)


def from_minor(i: Optional[int]) -> Optional[Decimal]:
    if i is None:
        return None
    return Decimal(i).scaleb(-SCALE)


def to_json(d: Optional[Decimal]) -> Optional[str]:
    if d is None:
        return None
    q = from_minor(to_minor(d))
    s = format(q, "f")
    if s.startswith("-") and Decimal(s) == 0:
        s = s[1:]
    return s
