"""Вспомогательная логика офисного редактора клиента (Ф1.4, D-95): мост между QML и ядром vector.office.

Backend держит модальное окно OfficeEditor.qml: сетка ячеек (таблица .vsheetx) или список абзацев (текст
.vdocx) поверх РАБОЧЕЙ КОПИИ модели; «Сохранить» сериализует в bytes и делает обычную новую версию документа
(SyncClient.add_document_version — онлайн, конфликты не сливаются). Здесь — только чистые преобразования
модель ↔ представление, чтобы логику покрывали тесты без QML (tests/client/test_office_editor.py):

* таблица: {rows, cols, cells:{A1:{text, formula, computed, has_formula}}} — адреса A1-нотации;
* текст: [{style, level, align, text}] — абзацы (включая заголовки), таблицы в документе первой порции
  редактора показываются строкой-пометкой (редактирование таблиц в документе — следующий шаг Ф1.4);
* правка ячейки: текст → Decimal/bool/строка (как CSV-импорт: ведущие нули сохраняются строкой), «=…» — формула;
  после правки лист пересчитывается (recalc, Kahn; циклы → #ЦИКЛ!).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from vector.office import formula as formula_core
from vector.office.model import Workbook, index_to_col
from vector.office.model_doc import Document, Paragraph, TableBlock

SHEET_MIN_ROWS, SHEET_MIN_COLS = 40, 12          # пустая сетка редактора (можно вводить за пределами данных)
DOC_TABLE_PLACEHOLDER = "Таблица {r}×{c} — редактирование таблиц в документе появится в следующем обновлении"


def _cell_text(value: Any) -> str:
    """Значение ячейки → строка для ввода/показа: Decimal без научной нотации, bool — да/нет."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _parse_input(text: str) -> tuple[Any, str | None]:
    """Ввод ячейки → (значение, формула). «=…» — формула (канон EN-нотации). Число — Decimal; «да»/«нет» —
    bool; ведущий ноль у цифровой строки — строка (коды вида «010203» не должны терять нули, как в CSV)."""
    t = text.strip()
    if t.startswith("="):
        return None, t
    if t == "":
        return None, None
    low = t.lower()
    if low in ("да", "нет"):
        return (low == "да"), None
    if t.lstrip("-").isdigit() and len(t.lstrip("-")) > 1 and t.lstrip("-").startswith("0"):
        return t, None                                  # 010203 — строка (не терять ведущие нули)
    try:
        return Decimal(t.replace(",", ".")), None       # десятичная запятая тоже принимается (ru)
    except InvalidOperation:
        return t, None


def sheet_to_view(wb: Workbook) -> dict:
    """Книга (первый лист) → представление сетки: размеры (не меньше занятой области и минимума) и ячейки."""
    sh = wb.sheets[0]
    rows, cols = sh.extent
    rows = max(rows + 5, SHEET_MIN_ROWS)
    cols = max(cols + 2, SHEET_MIN_COLS)
    cells = {}
    for addr, c in sh.cells.items():
        cells[addr] = {"text": _cell_text(c.value), "formula": c.formula or "",
                       "computed": _cell_text(c.value), "has_formula": bool(c.formula),
                       "bold": bool(c.style_id and 0 < c.style_id <= len(wb.styles) and wb.styles[c.style_id - 1].bold)}
    return {"name": sh.name, "rows": rows, "cols": cols, "cells": cells}


def col_label(idx: int) -> str:
    """Индекс колонки 0.. → буква A/B/… (для заголовков сетки)."""
    return index_to_col(idx)


def view_set_cell(wb: Workbook, addr: str, text: str) -> dict:
    """Применить ввод в ячейку РАБОЧЕЙ КОПИИ книги и пересчитать формулы. Возвращает ячейку представления
    (для точечного обновления QML без пересборки всей сетки)."""
    sh = wb.sheets[0]
    value, formula = _parse_input(text)
    sh.set(addr, value=value, formula=formula)
    if any(c.formula for c in sh.cells.values()):
        formula_core.recalc(sh)
    c = sh.get(addr)
    return {"text": _cell_text(c.value), "formula": c.formula or "", "computed": _cell_text(c.value),
            "has_formula": bool(c.formula)}


def doc_to_view(doc: Document) -> list[dict]:
    """Документ → список строк редактора: абзацы с меткой стиля; таблицы — пометкой (редактируются позже)."""
    rows = []
    for b in doc.blocks:
        if isinstance(b, Paragraph):
            rows.append({"block": "para", "style": b.style, "level": b.level, "align": b.align, "text": b.text})
        elif isinstance(b, TableBlock):
            rows.append({"block": "table-note",
                         "text": DOC_TABLE_PLACEHOLDER.format(r=len(b.rows), c=len(b.rows[0]) if b.rows else 0)})
    if not rows:
        rows.append({"block": "para", "style": "normal", "level": 1, "align": "left", "text": ""})
    return rows


def doc_from_view(title: str, rows: list[dict]) -> Document:
    """Строки редактора → документ. Пустые абзацы-хвосты не сохраняются (одна пустая строка — можно)."""
    doc = Document(title=title)
    kept = [r for r in rows if r.get("block") != "table-note"]   # таблицы не сериализуем из редактора (Ф1.4: абзацы)
    for r in kept:
        text = (r.get("text") or "").rstrip()
        doc.para(text, style=r.get("style") or "normal", level=int(r.get("level") or 1),
                 align=r.get("align") or "left")
    if not doc.blocks:
        doc.para("")
    return doc


PREVIEW_MAX_LINES = 8


def document_preview(data: bytes, kind: str) -> dict:
    """Первые строки офисного документа для мини-предпросмотра (инспектор 2.0, D-103): заголовок
    (лист/документ) и до PREVIEW_MAX_LINES непустых строк. Рабочая копия редактора не затрагивается —
    разбор из байтов версии. kind: "sheet" (.vsheetx) | "doc" (.vdocx).
    """
    if kind == "doc":
        from vector.office.vdocx import read_vdocx
        doc = read_vdocx(data)
        lines = []
        for b in doc.blocks:
            if isinstance(b, Paragraph) and (b.text or "").strip():
                lines.append(b.text.strip())
            elif isinstance(b, TableBlock):
                lines.append(DOC_TABLE_PLACEHOLDER.format(r=len(b.rows), c=len(b.rows[0]) if b.rows else 0))
        return {"title": doc.title or "", "lines": lines[:PREVIEW_MAX_LINES]}
    from vector.office.vsheetx import read_vsheetx
    wb = read_vsheetx(data)
    view = sheet_to_view(wb)
    lines = []
    for r in range(1, view["rows"] + 1):
        parts = [view["cells"].get(f"{col_label(c)}{r}", {}).get("text", "") for c in range(0, view["cols"])]
        line = "  ·  ".join(p for p in parts if p)
        if line:
            lines.append(line)
        if len(lines) >= PREVIEW_MAX_LINES:
            break
    return {"title": view["name"], "lines": lines}
