"""CSV-импорт/экспорт для «Таблиц» (D-93; миграция из EPLAN и обмен со смежниками — D-83, 53-ecosystem § 5).

Excel в русской локали пишет CSV через «;» — автоопределение разделителя по первой строке. Кодировка —
UTF-8 (с BOM — терпимо); чужой cp1251 отдаём как есть пользователю решать (мы не угадываем кодировки молча).
Автотип: «12,5»/«12.5» → Decimal, ИСТИНА/ЛОЖЬ → bool, остальное — строка (как ввод с клавиатуры).
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation

from .model import Sheet, make_addr

_DELIMS = (";", ",", "\t")


def _autotype(text: str):
    """Строка CSV → значение ячейки: число/булево, иначе строка. Пустое — None (ячейка пустая)."""
    t = text.strip()
    if t == "":
        return None
    low = t.lower()
    if low in ("истина", "true"):
        return True
    if low in ("ложь", "false"):
        return False
    try:
        return Decimal(t.replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return t


def _detect_delim(first_line: str) -> str:
    """Разделитель — самый частый из ; , табуляции в первой строке; по умолчанию «;» (русский Excel)."""
    return max(_DELIMS, key=first_line.count) if any(d in first_line for d in _DELIMS) else ";"


def read_csv(text: str, name: str = "Лист 1", autotype: bool = True) -> Sheet:
    """Текст CSV → лист. BOM снимается; автоопределение разделителя; автотип значений (отключаем)."""
    text = text.lstrip("﻿")
    first = text.splitlines()[0] if text.splitlines() else ""
    delim = _detect_delim(first)
    sheet = Sheet(name)
    for r, row in enumerate(csv.reader(io.StringIO(text), delimiter=delim)):
        for c, val in enumerate(row):
            v = _autotype(val) if autotype else (val if val != "" else None)
            if v is not None:
                sheet.set(make_addr(r, c), v)
    return sheet


def _to_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "ИСТИНА" if v else "ЛОЖЬ"
    if isinstance(v, Decimal):
        return format(v, "f").replace(".", ",")      # русская десятичная запятая — как ждёт Excel-ru
    return str(v)


def write_csv(sheet: Sheet, delimiter: str = ";") -> str:
    """Лист → текст CSV (по умолчанию «;» — открывается русским Excel двойным щелчком). Строки — по занятой
    области; пустые ячейки — пустые поля. Формулы выгружаются ПОСЛЕДНИМ ЗНАЧЕНИЕМ (CSV формул не держит)."""
    rows, cols = sheet.extent
    out = io.StringIO()
    w = csv.writer(out, delimiter=delimiter, lineterminator="\r\n")
    for r in range(rows):
        w.writerow([_to_text(sheet.get(make_addr(r, c)).value) for c in range(cols)])
    return out.getvalue()
