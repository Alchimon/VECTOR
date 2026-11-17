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

# Число знаков после точки во всей системе (деньги и количества). Совпадает с масштабом NUMERIC(18,4) в БД.
SCALE = 4
# Наибольшее по модулю значение, которое помещается в NUMERIC(18,4): 14 цифр до точки и 4 после.
MAX_ABS = Decimal("99999999999999.9999")
# Допустимая запись: необязательный минус, целая часть без ведущих нулей, необязательная дробная часть через точку.
# Запятая, пробелы, «+» и экспонента («1e3») намеренно не принимаются: протокол однозначен, а удобный ввод
# («1 250 000,50») переводит в эту форму интерфейс (backend.normalizeQty / calc_view).
_RE = re.compile(r"^-?(0|[1-9][0-9]*)(\.([0-9]+))?$")


class MoneyError(ValueError):
    """Ошибка разбора денежного/количественного значения.

    code — машинный код (уходит в ответ сервера и в тесты), message_ru — готовый текст для пользователя.
    """
    def __init__(self, code: str, message_ru: str):
        """Сохраняет код и русское сообщение; текст исключения — «код: сообщение» (удобно в журналах)."""
        super().__init__(f"{code}: {message_ru}")
        self.code = code
        self.message_ru = message_ru


def parse_decimal(value: object) -> Decimal:
    """Строгий разбор десятичной строки в Decimal — единственный вход денег и количеств в систему.

    Проверки по порядку: bool и float запрещены (float даёт двоичную погрешность, bool — частая ошибка JSON);
    принимается только строка вида «-123.45» (без пробелов, запятой и экспоненты — их разбирает интерфейс, а не
    протокол); больше 4 знаков после точки — ошибка, а не округление; модуль не больше MAX_ABS (вмещается в
    NUMERIC(18,4)).

    Возвращает Decimal ровно с теми знаками, что пришли; исключение MoneyError — с понятной причиной.
    """
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
    """Decimal → целое число десятитысячных (так деньги хранятся в SQLite, где нет точного NUMERIC).

    Лишние знаки здесь уже невозможны (их отсекает parse_decimal); если всё же пришли — это ошибка программы,
    поэтому исключение, а не округление.
    """
    scaled = d.scaleb(SCALE)
    if scaled != scaled.to_integral_value():
        raise MoneyError("too_many_fraction_digits", "Внутренняя ошибка точности.")
    return int(scaled)


def from_minor(i: Optional[int]) -> Optional[Decimal]:
    """Целое число десятитысячных из SQLite → Decimal. None (неизвестно) остаётся None — не превращается в 0."""
    if i is None:
        return None
    return Decimal(i).scaleb(-SCALE)


def to_json(d: Optional[Decimal]) -> Optional[str]:
    """Decimal → десятичная строка с ровно 4 знаками («12.5000») для JSON-ответов.

    None остаётся None (неизвестно ≠ 0). «-0.0000» нормализуется в «0.0000», чтобы клиент не показывал «минус ноль».
    """
    if d is None:
        return None
    q = from_minor(to_minor(d))
    s = format(q, "f")
    if s.startswith("-") and Decimal(s) == 0:
        s = s[1:]
    return s
