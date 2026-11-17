"""Заказы поставщикам: правила строк, итоги, контрольная сумма и состояние поставки (сценарий С4, шаг 5, D-32).

Модуль чистый (без БД и Qt) и общий для сервера и клиента:
* сервер — окончательная проверка при сохранении черновика и при размещении заказа, контрольная сумма строк
  размещённого заказа (placed_hash);
* клиент — та же проверка ДО отправки (подсказки в форме, в том числе офлайн) и итог черновика.

Правила (менять — только с записью в журнале решений, docs/blueprint/12-decision-log.md, D-32 / A-34…A-36):
* количество — в единице позиции номенклатуры, Decimal > 0, не более 4 знаков после точки (как на складе);
* цена за единицу — необязательна: null = «цена неизвестна» (≠ 0, такая строка не входит в сумму, итог помечается
  неполным); «0» — известная нулевая цена; цена ≥ 0;
* сумма строки = количество × цена ТОЧНО, без округления (правило округления не утверждено — Q8); иначе отказ;
* одна валюта на заказ (ISO 4217); если есть хоть одна цена — валюта обязательна, значение по умолчанию не
  подставляется. НДС программа не рассчитывает: цены вводятся так, как в счёте поставщика (A-35);
* у строки устойчивый line_id (UUID от клиента) — по нему привязываются факты поставки;
* ссылка на строку потребности — необязательна, но только парой (need_id + need_line_id);
* ИНН поставщика — необязателен; если указан: 10 цифр (организация) или 12 (ИП) с верными контрольными цифрами
  (алгоритм ФНС — это проверка формата, а не сведений о поставщике).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from . import money
from .calc import exact_amount

MAX_LINES = 500
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CUR_RE = re.compile(r"^[A-Z]{3}$")
ZERO = Decimal(0)

LINE_KEYS = {"line_id", "item_id", "qty", "unit_price", "need_id", "need_line_id", "note"}
FIELD_LABELS_RU = {"line_id": "Идентификатор строки", "item_id": "Позиция", "qty": "Количество", "unit_price": "Цена",
                   "need_id": "Потребность", "need_line_id": "Строка потребности", "note": "Примечание",
                   "currency": "Валюта", "lines": "Строки", "amount": "Сумма", "inn": "ИНН"}

# Статусы заказа (как в contracts/workflows) и состояние поставки размещённого/закрытого заказа.
STATUS_LABELS_RU = {"draft": "Черновик", "placed": "Размещён", "closed": "Закрыт", "cancelled": "Отменён"}
DELIVERY_LABELS_RU = {"none": "Ждём поставку", "partial": "Поставлен частично", "full": "Поставлен полностью"}


class PurchaseError(ValueError):
    """Заказ не прошёл проверку. problems — список {line, field, code, message_ru} (line — номер с 1 или None)."""

    def __init__(self, problems: list[dict]):
        """Текст исключения — по первому замечанию («Строка 2: …») с числом остальных; полный список — в problems."""
        self.problems = problems
        first = problems[0]
        where = f"Строка {first['line']}: " if first.get("line") else ""
        more = f" (и ещё замечаний: {len(problems) - 1})" if len(problems) > 1 else ""
        super().__init__(where + first["message_ru"] + more)
        self.code = first["code"]
        self.message_ru = str(self)


@dataclass(frozen=True)
class Line:
    """Проверенная строка заказа. amount — None, если цена неизвестна."""
    line_id: str
    item_id: str
    qty: Decimal
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]
    need_id: Optional[str]
    need_line_id: Optional[str]
    note: Optional[str]

    def to_json(self) -> dict:
        """Каноническая форма для JSON: количества и деньги — строки с 4 знаками."""
        return {"line_id": self.line_id, "item_id": self.item_id, "qty": money.to_json(self.qty),
                "unit_price": money.to_json(self.unit_price), "amount": money.to_json(self.amount),
                "need_id": self.need_id, "need_line_id": self.need_line_id, "note": self.note}


def _problem(line: Optional[int], field: str, code: str, message_ru: str) -> dict:
    """Одно замечание в едином формате."""
    return {"line": line, "field": field, "code": code, "message_ru": message_ru}


def _uuid(raw: dict, key: str, n: int, problems: list, required: bool) -> Optional[str]:
    """Идентификатор (UUID в нижнем регистре) из строки заказа; ошибки копятся в problems."""
    v = raw.get(key)
    if v is None or v == "":
        if required:
            problems.append(_problem(n, key, "required", f"Заполните поле «{FIELD_LABELS_RU[key]}»."))
        return None
    if not isinstance(v, str) or not _UUID_RE.match(v):
        problems.append(_problem(n, key, "invalid_id", f"«{FIELD_LABELS_RU[key]}»: неверный идентификатор."))
        return None
    return v


def check_currency(currency: Any) -> Optional[str]:
    """Код валюты ISO 4217 (три заглавные латинские буквы) или None; иначе PurchaseError."""
    if currency is None or currency == "":
        return None
    if not isinstance(currency, str) or not _CUR_RE.match(currency):
        raise PurchaseError([_problem(None, "currency", "invalid_currency",
                                      "Валюта — трёхбуквенный код ISO 4217 латиницей, например RUB.")])
    return currency


def parse_lines(raw_lines: Any, currency: Any, require_lines: bool = False) -> tuple[list[Line], Optional[str]]:
    """Проверить строки и валюту заказа. Возвращает (строки, валюта) или бросает PurchaseError со всеми замечаниями.

    require_lines — нужна хотя бы одна строка (при размещении; черновик может быть пустым).
    """
    problems: list[dict] = []
    if not isinstance(raw_lines, list):
        raise PurchaseError([_problem(None, "lines", "invalid_type", "Строки заказа — список.")])
    if len(raw_lines) > MAX_LINES:
        raise PurchaseError([_problem(None, "lines", "too_many_lines", f"В заказе не больше {MAX_LINES} строк.")])
    if require_lines and not raw_lines:
        raise PurchaseError([_problem(None, "lines", "no_lines", "В заказе нет ни одной строки.")])
    try:
        cur = check_currency(currency)
    except PurchaseError as e:
        problems += e.problems
        cur = None
    out: list[Line] = []
    seen: set[str] = set()
    for n, raw in enumerate(raw_lines, start=1):
        if not isinstance(raw, dict):
            problems.append(_problem(n, "lines", "invalid_type", "Строка заказа — объект."))
            continue
        extra = set(raw) - LINE_KEYS
        if extra:
            problems.append(_problem(n, "lines", "unknown_field", f"Неизвестные поля: {', '.join(sorted(extra))}."))
        lid = _uuid(raw, "line_id", n, problems, True)
        if lid and lid in seen:
            problems.append(_problem(n, "line_id", "duplicate_line", "Строка с таким идентификатором уже есть в заказе."))
        seen.add(lid or "")
        item = _uuid(raw, "item_id", n, problems, True)
        need, need_line = _uuid(raw, "need_id", n, problems, False), _uuid(raw, "need_line_id", n, problems, False)
        if (raw.get("need_id") in (None, "")) != (raw.get("need_line_id") in (None, "")):
            problems.append(_problem(n, "need_line_id", "need_pair", "Ссылка на потребность — потребность и её строка вместе."))
        qty = price = amount = None
        try:
            qty = money.parse_decimal(raw.get("qty")) if raw.get("qty") not in (None, "") else None
            if qty is None:
                problems.append(_problem(n, "qty", "required", "Заполните поле «Количество»."))
            elif qty <= 0:
                problems.append(_problem(n, "qty", "qty_not_positive", "Количество должно быть больше нуля."))
                qty = None
        except money.MoneyError as e:
            problems.append(_problem(n, "qty", e.code, f"«Количество»: {e.message_ru}"))
        try:
            price = money.parse_optional(None if raw.get("unit_price") == "" else raw.get("unit_price"))  # "" = неизвестна
            if price is not None and price < 0:
                problems.append(_problem(n, "unit_price", "negative_price", "Цена не может быть отрицательной."))
                price = None
        except money.MoneyError as e:
            problems.append(_problem(n, "unit_price", e.code, f"«Цена»: {e.message_ru}"))
        if qty is not None and price is not None:
            try:
                amount = exact_amount(qty, price)
            except money.MoneyError as e:
                problems.append(_problem(n, "amount", e.code, e.message_ru))
        note = raw.get("note")
        if note is not None and (not isinstance(note, str) or len(note.strip()) > 1000):
            problems.append(_problem(n, "note", "invalid_note", "Примечание — строка не длиннее 1000 символов."))
            note = None
        if lid and item and qty is not None:
            out.append(Line(lid, item, qty, price, amount, need, need_line, (note or "").strip() or None))
    if any(ln.unit_price is not None for ln in out) and cur is None and not any(p["field"] == "currency" for p in problems):
        problems.append(_problem(None, "currency", "currency_required",
                                 "Указаны цены — укажите валюту заказа (по умолчанию она не подставляется)."))
    if problems:
        raise PurchaseError(problems)
    return out, cur


def totals(lines: list[Line]) -> dict:
    """Итог заказа: сумма строк с известной ценой и число строк без цены (итог неполный, если они есть)."""
    priced = [ln.amount for ln in lines if ln.amount is not None]
    total = sum(priced, ZERO)
    return {"total": money.to_json(total) if priced else None, "unpriced_count": sum(1 for ln in lines if ln.amount is None),
            "complete": all(ln.amount is not None for ln in lines) and bool(lines)}


def content_hash(supplier_id: str, currency: Optional[str], lines: list[Line]) -> str:
    """SHA-256 содержания размещаемого заказа (поставщик, валюта, строки в порядке) — для placed_hash."""
    body = {"supplier_id": supplier_id, "currency": currency, "lines": [ln.to_json() for ln in lines]}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def remaining(ordered: Decimal, received: Decimal) -> Decimal:
    """Сколько по строке ещё ждём: заказано − поставлено (не меньше нуля)."""
    return max(ordered - received, ZERO)


def delivery_state(lines: list[tuple[Decimal, Decimal]]) -> str:
    """Состояние поставки по парам (заказано, поставлено): none — ничего, full — всё, partial — между."""
    if not lines or all(r <= 0 for _, r in lines):
        return "none"
    return "full" if all(r >= o for o, r in lines) else "partial"


def check_inn(value: Any) -> Optional[str]:
    """ИНН: None/пусто → None; иначе 10 или 12 цифр с верными контрольными цифрами, или PurchaseError."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    v = value.strip() if isinstance(value, str) else ""
    bad = PurchaseError([_problem(None, "inn", "invalid_inn",
                                  "ИНН — 10 цифр (организация) или 12 цифр (ИП) с верными контрольными цифрами.")])
    if not v.isdigit() or len(v) not in (10, 12) or not v.isascii():
        raise bad
    d = [int(c) for c in v]

    def check(coefs: list[int], upto: int) -> int:
        """Контрольная цифра по весам ФНС для первых upto цифр."""
        return sum(c * x for c, x in zip(coefs, d[:upto])) % 11 % 10
    if len(v) == 10:
        ok = check([2, 4, 10, 3, 5, 9, 4, 6, 8], 9) == d[9]
    else:
        ok = (check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], 10) == d[10]
              and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], 11) == d[11])
    if not ok:
        raise bad
    return v
