"""Расчёт комплекта: правила позиций, итоги, контрольная сумма содержимого и сравнение версий (сценарий С3).

Модуль чистый (без БД и Qt) и общий для сервера и клиента:

* сервер — окончательная проверка при сохранении черновика и при фиксации, hash неизменяемой версии;
* клиент — та же проверка ДО отправки (подсказки у полей, в том числе офлайн), локальный итог черновика
  и сравнение «черновик ↔ последняя зафиксированная версия» по кэшу без связи с сервером.

Правила (не менять без записи в Decision Log, см. docs/blueprint/12-decision-log.md, A-19):

* количество и цена — Decimal, не более 4 знаков после точки, диапазон NUMERIC(18,4), на входе — только
  десятичная строка (float запрещён, см. vector.core.money);
* количество > 0 (позиция с нулевым количеством бессмысленна — её удаляют);
* цена ≥ 0 или null. null = «цена неизвестна» — это НЕ ноль: такая позиция не входит в сумму, а итог
  помечается неполным («N позиций без цены»). "0" = известная нулевая цена (например, давальческий материал);
* сумма позиции = количество × цена ТОЧНО. Если в произведении больше 4 знаков после точки, позиция
  отклоняется: правило округления не утверждено (вопрос Q8), и молча округлять деньги нельзя;
* одна валюта на расчёт комплекта (ISO 4217); если есть хоть одна цена, валюта обязательна — значение
  по умолчанию не подставляется; итоги разных валют НЕ складываются (курсы и правило — OQ-11);
* у каждой позиции устойчивый line_id (UUID, создаётся клиентом): по нему сравниваются версии, поэтому
  переименование позиции — это «изменение», а не «удалена одна + добавлена другая».
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any, Optional

from . import money

MAX_LINES = 2000
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CUR_RE = re.compile(r"^[A-Z]{3}$")
_QUANT = Decimal("0.0001")
# Поля позиции и их предельная длина (строки); qty/unit_price — десятичные строки.
_TEXT_FIELDS = {"code": 100, "name": 300, "unit": 20, "note": 1000}
LINE_KEYS = {"line_id", "code", "name", "unit", "qty", "unit_price", "note"}
FIELD_LABELS_RU = {"code": "Обозначение", "name": "Наименование", "unit": "Ед. изм.", "qty": "Количество",
                   "unit_price": "Цена", "note": "Примечание", "amount": "Сумма", "currency": "Валюта",
                   "line_id": "Идентификатор позиции", "lines": "Позиции"}


@dataclass(frozen=True)
class Line:
    """Проверенная позиция расчёта. amount — None, если цена неизвестна."""
    line_id: str
    code: Optional[str]
    name: str
    unit: str
    qty: Decimal
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]
    note: Optional[str]

    def to_json(self) -> dict:
        """Представление для JSON: деньги и количества — десятичные строки с 4 знаками (каноническая форма)."""
        return {"line_id": self.line_id, "code": self.code, "name": self.name, "unit": self.unit,
                "qty": money.to_json(self.qty), "unit_price": money.to_json(self.unit_price),
                "amount": money.to_json(self.amount), "note": self.note}


class CalcError(ValueError):
    """Расчёт не прошёл проверку. problems — список {line, field, code, message_ru} (line — номер с 1 или None)."""

    def __init__(self, problems: list[dict]):
        """Текст исключения строится по первому замечанию («Позиция 3: …») с числом остальных — для уведомления; полный
        список остаётся в problems и показывается в редакторе по строкам.
        """
        self.problems = problems
        first = problems[0]
        where = f"Позиция {first['line']}: " if first.get("line") else ""
        more = f" (и ещё замечаний: {len(problems) - 1})" if len(problems) > 1 else ""
        super().__init__(where + first["message_ru"] + more)
        self.code = first["code"]
        self.message_ru = str(self)


def _problem(line: Optional[int], field: str, code: str, message_ru: str) -> dict:
    """Одно замечание к расчёту в едином формате {line, field, code, message_ru}."""
    return {"line": line, "field": field, "code": code, "message_ru": message_ru}


def _text(raw: dict, key: str, n: int, required: bool, problems: list) -> Optional[str]:
    """Текстовое поле позиции: обрезает пробелы, проверяет обязательность, тип и длину.

    Ошибки не бросаются сразу, а копятся в problems — пользователь видит все замечания разом, а не по одному.
    Возвращает строку или None (пусто/ошибка).
    """
    v = raw.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            problems.append(_problem(n, key, "required", f"Заполните поле «{FIELD_LABELS_RU[key]}»."))
        return None
    if not isinstance(v, str):
        problems.append(_problem(n, key, "invalid_type", f"Поле «{FIELD_LABELS_RU[key]}» должно быть строкой."))
        return None
    v = v.strip()
    if len(v) > _TEXT_FIELDS[key]:
        problems.append(_problem(n, key, "too_long", f"Поле «{FIELD_LABELS_RU[key]}» длиннее {_TEXT_FIELDS[key]} символов."))
        return None
    return v


def _decimal(raw: dict, key: str, n: int, problems: list, optional: bool) -> tuple[Optional[Decimal], bool]:
    """(значение, ok). Для optional None — «неизвестно» и это ok."""
    v = raw.get(key)
    if v is None:
        if optional:
            return None, True
        problems.append(_problem(n, key, "required", f"Заполните поле «{FIELD_LABELS_RU[key]}»."))
        return None, False
    try:
        return money.parse_decimal(v), True
    except money.MoneyError as e:
        problems.append(_problem(n, key, e.code, f"«{FIELD_LABELS_RU[key]}»: {e.message_ru}"))
        return None, False


def exact_amount(qty: Decimal, price: Decimal) -> Decimal:
    """Точное произведение без округления; MoneyError, если нужно округление или выход за диапазон."""
    with localcontext() as ctx:
        ctx.prec = 60          # 18+18 значащих цифр помещаются целиком — произведение и quantize точные
        a = qty * price
        if abs(a) > money.MAX_ABS:
            raise money.MoneyError("out_of_range", "Сумма позиции вне допустимого диапазона.")
        q = a.quantize(_QUANT)
    if a != q:
        raise money.MoneyError("amount_needs_rounding",
                               "Сумма позиции (количество × цена) получается больше чем с 4 знаками после запятой, "
                               "а правило округления не утверждено (Q8). Уточните количество или цену.")
    return q


def parse_lines(raw_lines: Any, currency: Any) -> tuple[list[Line], Optional[str]]:
    """Проверить позиции и валюту. Возвращает (позиции, валюта) или бросает CalcError со всеми замечаниями."""
    problems: list[dict] = []
    if not isinstance(raw_lines, list):
        raise CalcError([_problem(None, "lines", "invalid_type", "Позиции расчёта передаются списком.")])
    if len(raw_lines) > MAX_LINES:
        raise CalcError([_problem(None, "lines", "too_many_lines", f"Не более {MAX_LINES} позиций в расчёте комплекта.")])
    cur: Optional[str] = None
    if currency is not None:
        if not isinstance(currency, str) or not _CUR_RE.match(currency):
            problems.append(_problem(None, "currency", "invalid_currency",
                                     "Валюта — трёхбуквенный код ISO 4217 латиницей (например, RUB)."))
        else:
            cur = currency
    out: list[Line] = []
    seen: set[str] = set()
    any_price = False
    for i, raw in enumerate(raw_lines, start=1):
        if not isinstance(raw, dict):
            problems.append(_problem(i, "lines", "invalid_type", "Позиция должна быть объектом."))
            continue
        extra = set(raw) - LINE_KEYS - {"amount"}      # amount клиент может прислать — сервер его пересчитывает
        if extra:
            problems.append(_problem(i, "lines", "unknown_fields", "Неизвестные поля позиции: " + ", ".join(sorted(extra))))
        lid = raw.get("line_id")
        if not isinstance(lid, str) or not _UUID_RE.match(lid):
            problems.append(_problem(i, "line_id", "invalid_line_id", "У позиции нет корректного идентификатора (UUID)."))
            lid = None
        elif lid in seen:
            problems.append(_problem(i, "line_id", "duplicate_line_id", "Позиция с таким идентификатором повторяется."))
        else:
            seen.add(lid)
        name = _text(raw, "name", i, True, problems)
        unit = _text(raw, "unit", i, True, problems)
        code = _text(raw, "code", i, False, problems)
        note = _text(raw, "note", i, False, problems)
        qty, q_ok = _decimal(raw, "qty", i, problems, optional=False)
        price, p_ok = _decimal(raw, "unit_price", i, problems, optional=True)
        if q_ok and qty is not None and qty <= 0:
            problems.append(_problem(i, "qty", "qty_not_positive", "Количество должно быть больше нуля (позицию с нулём удалите)."))
            q_ok = False
        if p_ok and price is not None and price < 0:
            problems.append(_problem(i, "unit_price", "price_negative", "Цена не может быть отрицательной."))
            p_ok = False
        amount = None
        if q_ok and p_ok and price is not None:
            any_price = True
            try:
                amount = exact_amount(qty, price)
            except money.MoneyError as e:
                problems.append(_problem(i, "amount", e.code, e.message_ru))
        if lid and name and unit and q_ok and p_ok and (price is None or amount is not None):
            out.append(Line(lid, code, name, unit, qty, price, amount, note))
    if any_price and cur is None and not any(p["field"] == "currency" for p in problems):
        problems.append(_problem(None, "currency", "currency_required",
                                 "Для расчёта с ценами укажите валюту — значение по умолчанию не подставляется."))
    if not problems:
        total = sum((ln.amount for ln in out if ln.amount is not None), Decimal(0))
        if abs(total) > money.MAX_ABS:
            problems.append(_problem(None, "amount", "total_out_of_range", "Итог расчёта вне допустимого диапазона."))
    if problems:
        raise CalcError(problems)
    return out, cur


def totals(lines: list[Line]) -> dict:
    """Итог расчёта. Неизвестная цена не превращается в ноль: итог помечается неполным."""
    known = [ln.amount for ln in lines if ln.amount is not None]
    unknown = sum(1 for ln in lines if ln.amount is None)
    return {"total": money.to_json(sum(known, Decimal(0))), "line_count": len(lines), "priced_count": len(known),
            "unknown_price_count": unknown, "complete": unknown == 0}


def content_hash(lines: list[Line], currency: Optional[str], note: Optional[str]) -> str:
    """SHA-256 канонического содержимого расчёта (порядок позиций значим, суммы — в канонической форме).

    Нужен, чтобы (1) неизменяемая версия K.n однозначно идентифицировалась содержимым, (2) «зафиксировать то же
    самое ещё раз» распознавалось без сравнения построчно, (3) клиент мог проверить, что видит ровно ту версию.
    """
    body = {"currency": currency, "note": note, "lines": [ln.to_json() for ln in lines]}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def lines_from_json(items: list[dict], currency: Optional[str]) -> list[Line]:
    """Позиции из JSON (кэш клиента, ответ сервера). Проверка та же, что и при вводе."""
    return parse_lines(items, currency)[0]


_DIFF_FIELDS = ("code", "name", "unit", "qty", "unit_price", "amount", "note")


def diff(base: Optional[list[dict]], new: list[dict], base_currency: Optional[str] = None,
         new_currency: Optional[str] = None) -> dict:
    """Сравнение двух наборов позиций (JSON-представления) по line_id.

    base=None — сравнивать не с чем (фиксаций ещё не было): все позиции «добавлены».
    Возвращает {"rows": [...], "added", "removed", "changed", "unchanged", "currency_changed", "total_delta"}.
    total_delta считается только если валюта одна и в ОБОИХ наборах нет позиций без цены — иначе None
    (разница «неизвестна», а не ноль).
    """
    base = base or []
    b = {x["line_id"]: x for x in base}
    rows: list[dict] = []
    counts = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
    for x in new:
        old = b.get(x["line_id"])
        if old is None:
            rows.append({"kind": "added", "line_id": x["line_id"], "name": x["name"], "changes": []})
            counts["added"] += 1
            continue
        ch = [{"field": f, "label": FIELD_LABELS_RU[f], "before": old.get(f), "after": x.get(f)}
              for f in _DIFF_FIELDS if _norm(f, old.get(f)) != _norm(f, x.get(f))]
        kind = "changed" if ch else "unchanged"
        counts[kind] += 1
        rows.append({"kind": kind, "line_id": x["line_id"], "name": x["name"], "changes": ch})
    new_ids = {x["line_id"] for x in new}
    for x in base:
        if x["line_id"] not in new_ids:
            rows.append({"kind": "removed", "line_id": x["line_id"], "name": x["name"], "changes": []})
            counts["removed"] += 1

    def known_total(items):
        """Сумма позиций, если у всех известна цена; иначе None — разницу итогов при неизвестных ценах не показываем
        (неизвестно ≠ 0).
        """
        if any(i.get("amount") is None for i in items):
            return None
        return sum((Decimal(i["amount"]) for i in items), Decimal(0))
    tb, tn = known_total(base), known_total(new)
    same_cur = (base_currency == new_currency) or not base or not new
    delta = money.to_json(tn - tb) if (tb is not None and tn is not None and same_cur) else None
    return {"rows": rows, **counts, "currency_changed": bool(base) and base_currency != new_currency,
            "total_delta": delta}


def _norm(field: str, v: Any) -> Any:
    """Сравнение без ложных различий «2» ↔ «2.0000» — только для числовых полей; текст сравнивается как есть."""
    if field in ("qty", "unit_price", "amount") and isinstance(v, str):
        return Decimal(v)
    return v


# ------------------------------------------------------------ обмен с Excel (.xlsx), R-31
from . import xlsx  # noqa: E402  (внизу файла: чистый модуль, циклических импортов нет)

# Колонки листа расчёта — в этом же порядке идёт вставка из буфера обмена (CalcEditor.pasteRows).
SHEET_HEADER = ["Обозначение", "Наименование", "Ед. изм.", "Количество", "Цена", "Примечание"]


def lines_to_sheet(lines: list[dict]) -> list[list[str]]:
    """Позиции редактора → строки листа Excel: заголовок + данные, числа каноническими строками.

    line_id в файл не пишется: при импорте позиции получают новые идентификаторы (импорт ДОБАВЛЯЕТ
    позиции в черновик, а не заменяет его — как вставка из буфера). Итоги в файл не пишем:
    лист кругово импортируется, а суммы в Excel пусть считает сам Excel.
    """
    rows = [list(SHEET_HEADER)]
    for ln in lines:
        rows.append([str(ln.get("code") or ""), str(ln.get("name") or ""), str(ln.get("unit") or ""),
                     str(ln.get("qty") or "").strip(), str(ln.get("unit_price") or "").strip(),
                     str(ln.get("note") or "")])
    return rows


def _is_header(row: list[xlsx.Cell]) -> bool:
    """Первая строка — наш заголовок? Сравнение без учёта регистра по первым двум колонкам."""
    return (len(row) >= 2 and row[0].text.strip().casefold() == SHEET_HEADER[0].casefold()
            and row[1].text.strip().casefold() == SHEET_HEADER[1].casefold())


def sheet_to_lines(cells: list[list[xlsx.Cell]], new_id=None) -> tuple[list[dict], list[dict]]:
    """Строки листа → (позиции для редактора, замечания). Импорт «всё или ничего»: любое замечание
    отменяет импорт целиком — частично загруженного расчёта не бывает (R-31).

    Пропускаются: строка-заголовок (см. _is_header) и полностью пустые строки. Замечания адресуются
    номером строки ЛИСТА (как в Excel, с 1), чтобы пользователь нашёл место в своём файле.
    Числовая ячейка с более чем 15 значащими цифрами — замечание excel_precision: Excel хранит
    числа с точностью float64, такое значение в файле уже искажено; текстовые ячейки проходят точно.
    """
    import uuid as _uuid
    new_id = new_id or (lambda: str(_uuid.uuid4()))
    raw: list[dict] = []
    sheet_row: list[int] = []          # raw-индекс → номер строки листа
    problems: list[dict] = []
    for n, row in enumerate(cells, start=1):
        if all(not c.text.strip() for c in row):
            continue
        if not raw and not sheet_row and _is_header(row):
            continue
        vals = [(row[i].text.strip() if i < len(row) else "") for i in range(6)]
        for i, field in ((3, "qty"), (4, "unit_price")):
            if i < len(row) and row[i].numeric and vals[i]:
                sig = xlsx.significant_digits(vals[i])
                if sig is not None and sig > xlsx.EXCEL_SIG_DIGITS:
                    problems.append(_problem(n, field, "excel_precision",
                                             f"Больше {xlsx.EXCEL_SIG_DIGITS} значащих цифр — Excel хранит числа неточно; "
                                             "проверьте значение или перенесите его текстом."))
        raw.append({"line_id": new_id(), "code": vals[0] or None, "name": vals[1], "unit": vals[2],
                    "qty": vals[3] or None, "unit_price": vals[4] or None, "note": vals[5] or None})
        sheet_row.append(n)
    if not raw and not problems:
        problems.append(_problem(None, "lines", "empty_sheet", "На листе нет ни одной позиции."))
        return [], problems
    if len(raw) > MAX_LINES:
        problems.append(_problem(None, "lines", "too_many_lines",
                                 f"Не более {MAX_LINES} позиций в расчёте комплекта (в файле {len(raw)})."))
        return [], problems
    try:
        # Валюта здесь осознанно не проверяется (заглушка): она относится ко всему расчёту, остаётся в поле
        # редактора и будет проверена при сохранении; импорт отвечает только за позиции.
        parse_lines(raw, "XXX")
    except CalcError as e:
        for p in e.problems:
            if p.get("line"):
                p["line"] = sheet_row[p["line"] - 1] if p["line"] - 1 < len(sheet_row) else p["line"]
            problems.append(p)
    return ([], problems) if problems else (raw, [])
