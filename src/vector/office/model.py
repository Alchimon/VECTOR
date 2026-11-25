"""Модель электронной таблицы ВЕКТОР (фаза Ф1.1, D-83): книга → листы → ячейки.

Чистое ядро без Qt и БД: используется форматами (.vsheetx, .xlsx) и в будущем — сервером и клиентом.
Числа — Decimal (деньги точно, 55-office-documents.md § 2); адреса ячеек — A1-нотация; формулы хранятся
строкой в канонической EN-нотации (SUM, не СУММ) — перевод на русский для UI делает клиент, экспорт в .xlsx
не требует обратного перевода.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

# A1-адрес: буквы колонки + номер строки (A1, BC12). Строки с 1, колонки с A — как в Excel.
_CELL_RE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")

# Виды значений ячейки (атрибут t= в XML): число, строка, булево; пустая ячейка — value=None.
T_NUMBER, T_STRING, T_BOOL = "n", "s", "b"


def col_to_index(col: str) -> int:
    """Буквы колонки → индекс с 0 (A→0, Z→25, AA→26)."""
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def index_to_col(idx: int) -> str:
    """Индекс колонки с 0 → буквы (0→A, 26→AA)."""
    s = ""
    idx += 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def parse_addr(addr: str) -> tuple[int, int]:
    """'BC12' → (индекс строки с 0, индекс колонки с 0). Неверный адрес — ValueError (ошибка программы)."""
    m = _CELL_RE.match(addr)
    if not m:
        raise ValueError(f"Недопустимый адрес ячейки: {addr!r}")
    return int(m.group(2)) - 1, col_to_index(m.group(1))


def make_addr(row: int, col: int) -> str:
    """(строка с 0, колонка с 0) → 'BC12'."""
    return f"{index_to_col(col)}{row + 1}"


@dataclass
class Cell:
    """Ячейка: значение ИЛИ формула (или и то: формула + последнее посчитанное значение — как в OOXML)."""
    value: Optional[object] = None                 # Decimal | str | bool | None
    formula: Optional[str] = None                  # "=SUM(A1:A9)" — каноническая EN-нотация
    style_id: int = 0                              # индекс в Workbook.styles (0 — стиль по умолчанию)

    @property
    def kind(self) -> Optional[str]:
        """Код типа значения для XML (n/s/b) или None для пустой."""
        if self.value is None:
            return None
        if isinstance(self.value, bool):
            return T_BOOL
        if isinstance(self.value, Decimal):
            return T_NUMBER
        return T_STRING


@dataclass
class Style:
    """Минимальный стиль ячейки первой порции (расширяется — format_version миграциями)."""
    bold: bool = False
    italic: bool = False
    font_size: float = 11.0
    number_format: str = ""                        # "0.00", "DD.MM.YYYY" — строка формата, как в Excel
    fill: str = ""                                 # цвет заливки RRGGBB или "" (без заливки)


@dataclass
class Sheet:
    """Лист: имя, ячейки по адресу (только непустые), объединённые диапазоны, ширины колонок."""
    name: str
    cells: dict[str, Cell] = field(default_factory=dict)
    merged: list[str] = field(default_factory=list)          # "A1:C1"
    col_widths: dict[int, float] = field(default_factory=dict)  # индекс колонки → ширина в символах

    def set(self, addr: str, value=None, formula: Optional[str] = None, style_id: int = 0) -> Cell:
        """Поставить значение/формулу по адресу A1. None + без формулы — ячейка удаляется (не храним пустые)."""
        parse_addr(addr)                                       # проверка адреса
        if value is None and formula is None and style_id == 0:
            self.cells.pop(addr, None)
            return Cell()
        c = Cell(value=value, formula=formula, style_id=style_id)
        self.cells[addr] = c
        return c

    def get(self, addr: str) -> Cell:
        """Ячейка по адресу; пустая (не хранящаяся) → Cell() по умолчанию."""
        return self.cells.get(addr) or Cell()

    @property
    def extent(self) -> tuple[int, int]:
        """(число строк, число колонок) занятой области — для сетки клиента и тестов."""
        if not self.cells:
            return (0, 0)
        rows, cols = zip(*(parse_addr(a) for a in self.cells), strict=False)
        return (max(rows) + 1, max(cols) + 1)


@dataclass
class Workbook:
    """Книга: листы по порядку, таблица стилей (styles[0] — по умолчанию), неизвестные части OOXML для
    round-trip (путь в пакете → байты): диаграммы, сводные, макросы из импортированного .xlsx возвращаются
    при экспорте байт-в-байт (55-office-documents.md § 2 «без потерь»)."""
    sheets: list[Sheet] = field(default_factory=list)
    styles: list[Style] = field(default_factory=lambda: [Style()])
    unknown_parts: dict[str, bytes] = field(default_factory=dict)
    format_version: int = 1

    def sheet(self, name: str) -> Sheet:
        """Лист по имени или создание нового (как в Excel при обращении к новому листу)."""
        for s in self.sheets:
            if s.name == name:
                return s
        s = Sheet(name)
        self.sheets.append(s)
        return s

    def style_add(self, st: Style) -> int:
        """Добавить стиль, вернуть его id (для Cell.style_id)."""
        self.styles.append(st)
        return len(self.styles) - 1
