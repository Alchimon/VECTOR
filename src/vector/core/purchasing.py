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

Дополнения D-33 (ответы владельца на A-34…A-36):
* **НДС** (A-35 → D-33, правило округления — A-38): у заказа режим цен — «с НДС» (included) или «без НДС»
  (excluded); у строки — ставка из VAT_RATES (22 / 10 / 7 / 5 / 0 % или «без НДС»). Для строки с известной ценой:
    - цены с НДС:  НДС = сумма × ставка / (100 + ставка); без НДС = сумма − НДС; с НДС = сумма;
    - цены без НДС: НДС = сумма × ставка / 100;          без НДС = сумма;       с НДС = сумма + НДС.
  НДС строки округляется до копеек по правилу математического округления (0,005 → 0,01) — так, как в
  счёте-фактуре; итоги — суммы строк (не пересчёт от итога). Сумма строки по-прежнему точная (без округления).
  Пока режим или ставка не указаны, НДС «не указан» (а не 0): строка не входит в итог НДС;
* **готовность к согласованию и размещению** (ready_problems): хотя бы одна строка, режим цен и ставки у строк с
  ценой, валюта при ценах. Черновик может быть неполным — проверка выполняется при po.submit и po.place;
* **реквизиты поставщика** (check_requisites, A-39): КПП, ОГРН/ОГРНИП, БИК, расчётный и корреспондентский счета —
  необязательны; если указаны — проверяются формат и контрольные цифры (алгоритмы ФНС и Банка России) и
  согласованность (КПП и ОГРН — у организации, ОГРНИП — у ИП; счёт — только вместе с БИК).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from . import money
from .calc import exact_amount

MAX_LINES = 500
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CUR_RE = re.compile(r"^[A-Z]{3}$")
ZERO = Decimal(0)

LINE_KEYS = {"line_id", "item_id", "qty", "unit_price", "need_id", "need_line_id", "note", "vat_rate"}
FIELD_LABELS_RU = {"line_id": "Идентификатор строки", "item_id": "Позиция", "qty": "Количество", "unit_price": "Цена",
                   "need_id": "Потребность", "need_line_id": "Строка потребности", "note": "Примечание",
                   "currency": "Валюта", "lines": "Строки", "amount": "Сумма", "inn": "ИНН", "vat_rate": "Ставка НДС",
                   "vat_mode": "Цены с НДС / без НДС", "kpp": "КПП", "ogrn": "ОГРН", "bik": "БИК",
                   "bank_account": "Расчётный счёт", "corr_account": "Корр. счёт"}

# Ставки НДС (ст. 164 НК РФ в редакции 425-ФЗ: основная 22 % с 01.01.2026, льготные 10 % и 0 %, специальные 5 % и
# 7 % для УСН) и «без НДС» (поставщик не плательщик / операция освобождена). Код ставки — строка, как в протоколе.
# Изменится закон — меняется этот список (и запись в журнале решений); старые заказы хранят свой код ставки.
VAT_RATES: dict[str, Optional[Decimal]] = {"22": Decimal(22), "10": Decimal(10), "7": Decimal(7), "5": Decimal(5),
                                           "0": Decimal(0), "none": None}
VAT_RATE_LABELS_RU = {"22": "22 %", "10": "10 %", "7": "7 %", "5": "5 %", "0": "0 %", "none": "Без НДС"}
# Режим цен заказа: как указаны цены в счёте поставщика.
VAT_MODES = {"included": "Цены с НДС", "excluded": "Цены без НДС"}
_KOPECK = Decimal("0.01")

# Статусы заказа (как в contracts/workflows) и состояние поставки размещённого/закрытого заказа.
STATUS_LABELS_RU = {"draft": "Черновик", "placed": "Размещён", "closed": "Закрыт", "cancelled": "Отменён"}
DELIVERY_LABELS_RU = {"none": "Ждём поставку", "partial": "Поставлен частично", "full": "Поставлен полностью"}
# Согласование черновика (D-33): состояние — по последнему событию в purchase_order_approvals; None — не отправляли.
APPROVAL_LABELS_RU = {None: "Не отправлен на согласование", "submit": "На согласовании", "approve": "Согласован",
                      "return": "Возвращён на доработку", "withdraw": "Отозван с согласования"}


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
    vat_rate: Optional[str] = None       # код из VAT_RATES; None — ставка не указана (D-33)

    def to_json(self) -> dict:
        """Каноническая форма для JSON: количества и деньги — строки с 4 знаками."""
        return {"line_id": self.line_id, "item_id": self.item_id, "qty": money.to_json(self.qty),
                "unit_price": money.to_json(self.unit_price), "amount": money.to_json(self.amount),
                "need_id": self.need_id, "need_line_id": self.need_line_id, "note": self.note, "vat_rate": self.vat_rate}


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
        vat = raw.get("vat_rate")
        if vat in ("", None):
            vat = None
        elif vat not in VAT_RATES:
            problems.append(_problem(n, "vat_rate", "invalid_vat_rate",
                                     "Ставка НДС — одна из: " + ", ".join(VAT_RATE_LABELS_RU.values()) + "."))
            vat = None
        if lid and item and qty is not None:
            out.append(Line(lid, item, qty, price, amount, need, need_line, (note or "").strip() or None, vat))
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


def check_vat_mode(value: Any) -> Optional[str]:
    """Режим цен заказа: None/пусто → None (не указан); included / excluded; иначе PurchaseError."""
    if value in (None, ""):
        return None
    if value not in VAT_MODES:
        raise PurchaseError([_problem(None, "vat_mode", "invalid_vat_mode", "Режим цен — «с НДС» или «без НДС».")])
    return value


def line_vat(amount: Optional[Decimal], rate: Optional[str], mode: Optional[str]) -> Optional[tuple[Decimal, Decimal, Decimal]]:
    """НДС строки: (без НДС, НДС, с НДС) или None, если цена неизвестна или не указаны ставка / режим цен.

    НДС округляется до копеек математически (A-38); «без НДС» / «с НДС» получаются вычитанием / сложением, поэтому
    без НДС + НДС = с НДС ровно. Ставка «без НДС» и 0 % дают НДС 0.
    """
    if amount is None or rate is None or mode is None:
        return None
    r = VAT_RATES[rate]
    if not r:
        return amount, ZERO, amount
    if mode == "included":
        vat = (amount * r / (100 + r)).quantize(_KOPECK, rounding=ROUND_HALF_UP)
        return amount - vat, vat, amount
    vat = (amount * r / 100).quantize(_KOPECK, rounding=ROUND_HALF_UP)
    return amount, vat, amount + vat


def vat_totals(lines: list[Line], mode: Optional[str]) -> dict:
    """Итоги НДС по строкам с известной ценой: без НДС, НДС, с НДС и НДС по ставкам.

    unknown_count — строки с ценой, для которых НДС не посчитать (не указан режим цен заказа или ставка строки);
    если такие есть, net/vat/gross — None (итог НДС неизвестен, а не меньше). Без цен — тоже None.
    """
    priced = [ln for ln in lines if ln.amount is not None]
    parts = [(ln, line_vat(ln.amount, ln.vat_rate, mode)) for ln in priced]
    unknown = sum(1 for _, v in parts if v is None)
    by_rate: dict[str, Decimal] = {}
    for ln, v in parts:
        if v is not None:
            by_rate[ln.vat_rate] = by_rate.get(ln.vat_rate, ZERO) + v[1]
    known = bool(priced) and unknown == 0
    return {"net": money.to_json(sum((v[0] for _, v in parts), ZERO)) if known else None,
            "vat": money.to_json(sum((v[1] for _, v in parts), ZERO)) if known else None,
            "gross": money.to_json(sum((v[2] for _, v in parts), ZERO)) if known else None,
            "by_rate": [{"rate": r, "label": VAT_RATE_LABELS_RU[r], "vat": money.to_json(by_rate[r])}
                        for r in VAT_RATES if r in by_rate],
            "unknown_count": unknown, "mode": mode}


def ready_problems(lines: list[Line], currency: Optional[str], vat_mode: Optional[str]) -> list[dict]:
    """Чего не хватает черновику для согласования и размещения (D-33): строки, режим цен, ставки, валюта.

    Пустой список — готов. Ссылки (поставщик, позиции, договор, потребность) проверяет сервер по БД.
    """
    out: list[dict] = []
    if not lines:
        out.append(_problem(None, "lines", "no_lines", "В заказе нет ни одной строки."))
    priced = [n for n, ln in enumerate(lines, start=1) if ln.unit_price is not None]
    if priced and currency is None:
        out.append(_problem(None, "currency", "currency_required",
                            "Указаны цены — укажите валюту заказа (по умолчанию она не подставляется)."))
    if priced and vat_mode is None:
        out.append(_problem(None, "vat_mode", "vat_mode_required",
                            "Укажите, как даны цены: с НДС или без НДС (так, как в счёте поставщика)."))
    for n in priced:
        if lines[n - 1].vat_rate is None:
            out.append(_problem(n, "vat_rate", "vat_rate_required", "Укажите ставку НДС (или «Без НДС»)."))
    return out


def content_hash(supplier_id: str, currency: Optional[str], lines: list[Line], contract_id: Optional[str] = None,
                 vat_mode: Optional[str] = None) -> str:
    """SHA-256 содержания заказа (поставщик, валюта, строки в порядке; с D-33 — договор, режим цен и ставки НДС).

    Используется для placed_hash (замороженные условия размещённого заказа) и для согласования (согласовано
    именно это содержание). Поля D-33 входят в тело, только если заданы: у заказов, размещённых до D-33, сумма не
    меняется и hash_ok остаётся верным.
    """
    rows = []
    for ln in lines:
        j = ln.to_json()
        if j.get("vat_rate") is None:
            j.pop("vat_rate", None)
        rows.append(j)
    body: dict[str, Any] = {"supplier_id": supplier_id, "currency": currency, "lines": rows}
    if contract_id is not None:
        body["contract_id"] = contract_id
    if vat_mode is not None:
        body["vat_mode"] = vat_mode
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


# ---------------------------------------------------------------- реквизиты поставщика (D-33, A-39)
REQUISITE_KEYS = ("kpp", "ogrn", "address", "bank_name", "bik", "bank_account", "corr_account")


def _digits(v: Any) -> Optional[str]:
    """Строка из цифр без пробелов (пробелы при вводе допускаются) или None для пустого; не строка — ''."""
    if v is None:
        return None
    if not isinstance(v, str):
        return ""
    v = v.replace(" ", "").strip()
    return v or None


def _account_key_ok(prefix: str, account: str) -> bool:
    """Контрольный ключ счёта (Банк России): 23 цифры (prefix + счёт) × веса 7,1,3,…; сумма делится на 10."""
    s = prefix + account
    return sum(int(c) * (7, 1, 3)[i % 3] for i, c in enumerate(s)) % 10 == 0


def check_requisites(p: dict, inn: Optional[str]) -> dict:
    """Проверить реквизиты поставщика и вернуть нормализованные (цифры без пробелов, пустое → None).

    Все поля необязательны; если поле указано — проверяется формат и контрольные цифры. Согласованность: КПП и
    ОГРН (13 цифр) — у организации (ИНН из 10 цифр), ОГРНИП (15 цифр) — у ИП (ИНН из 12 цифр); счета — только
    вместе с БИК (ключ счёта считается от БИК). Ошибки копятся: PurchaseError со всеми problems.
    """
    problems: list[dict] = []
    out: dict[str, Optional[str]] = {}

    def bad(field: str, code: str, msg: str) -> None:
        """Записать замечание и обнулить поле."""
        problems.append(_problem(None, field, code, msg))
        out[field] = None

    kpp = _digits(p.get("kpp"))
    out["kpp"] = kpp.upper() if kpp else None
    if kpp is not None and not re.fullmatch(r"\d{4}[0-9A-Z]{2}\d{3}", kpp.upper()):
        bad("kpp", "invalid_kpp", "КПП — 9 знаков: 4 цифры, 2 цифры или заглавные латинские буквы, 3 цифры.")
    ogrn = _digits(p.get("ogrn"))
    out["ogrn"] = ogrn
    if ogrn is not None:
        if not ogrn.isdigit() or not ogrn.isascii() or len(ogrn) not in (13, 15):
            bad("ogrn", "invalid_ogrn", "ОГРН — 13 цифр (организация), ОГРНИП — 15 цифр (ИП).")
        elif (int(ogrn[:12]) % 11 % 10 if len(ogrn) == 13 else int(ogrn[:14]) % 13 % 10) != int(ogrn[-1]):
            bad("ogrn", "invalid_ogrn", "ОГРН / ОГРНИП: неверная контрольная цифра.")
    bik = _digits(p.get("bik"))
    out["bik"] = bik
    if bik is not None and (not bik.isdigit() or not bik.isascii() or len(bik) != 9):
        bad("bik", "invalid_bik", "БИК — 9 цифр.")
    for key, label in (("bank_account", "Расчётный счёт"), ("corr_account", "Корр. счёт")):
        acc = _digits(p.get(key))
        out[key] = acc
        if acc is None:
            continue
        if not acc.isdigit() or not acc.isascii() or len(acc) != 20:
            bad(key, "invalid_account", f"{label} — 20 цифр.")
        elif not out.get("bik"):
            bad(key, "bik_required", f"{label} проверяется по БИК банка — укажите БИК.")
        else:
            prefix = out["bik"][-3:] if key == "bank_account" else "0" + out["bik"][4:6]
            if not _account_key_ok(prefix, acc):
                bad(key, "invalid_account", f"{label} не сходится с БИК (неверный контрольный ключ). Проверьте обе цифры.")
    for key, n in (("address", 500), ("bank_name", 300)):
        v = p.get(key)
        if v is not None and not isinstance(v, str):
            bad(key, "invalid_type", f"«{key}» — строка.")
            continue
        v = (v or "").strip() or None
        if v is not None and len(v) > n:
            bad(key, "too_long", f"Не длиннее {n} символов.")
            continue
        out[key] = v
    if inn and len(inn) == 12:
        if out.get("kpp"):
            bad("kpp", "kpp_for_ip", "У ИП (ИНН из 12 цифр) нет КПП — оставьте поле пустым.")
        if out.get("ogrn") and len(out["ogrn"]) != 15:
            bad("ogrn", "ogrn_mismatch", "У ИП (ИНН из 12 цифр) — ОГРНИП из 15 цифр.")
    if inn and len(inn) == 10 and out.get("ogrn") and len(out["ogrn"]) != 13:
        bad("ogrn", "ogrn_mismatch", "У организации (ИНН из 10 цифр) — ОГРН из 13 цифр.")
    if problems:
        raise PurchaseError(problems)
    return out
