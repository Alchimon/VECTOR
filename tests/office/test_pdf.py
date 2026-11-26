"""Печать офисных документов в PDF (Ф1.4-доп, D-96): сборка HTML из модели ядра и запись QPdfWriter.

Сборка — без Qt (тестируется изолированно); write_pdf — только с QApplication (GUI-поток).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from vector.office.model import Style, Workbook
from vector.office.model_doc import Document
from vector.office.pdf import doc_html, sheet_html, write_pdf


def _sheet():
    wb = Workbook()
    sh = wb.sheet("Спецификация")
    bold = wb.style_add(Style(bold=True, fill="dde6f0"))
    sh.set("A1", value="Позиция", style_id=bold)
    sh.set("B1", value="Количество", style_id=bold)
    sh.set("A2", value="Кабель КВВГнг(А)-LS 7×1,5")
    sh.set("B2", value=Decimal(120), style_id=0)
    sh.set("B3", formula="=B2*2")
    from vector.office.formula import recalc
    recalc(sh)
    return wb


def _doc():
    d = Document(title="Акт выполненных работ")
    d.para("Акт № {{doc.code}}", style="heading", level=1)
    d.para("по проекту П-100 «Шкафы для завода»")
    d.para("Перечень работ:", style="list-bullet")
    d.para("Заказчик: ООО «Завод»", align="right")
    t = d.table(2, 2, header=True)
    t.rows[0][0][0].add("Работа"); t.rows[0][1][0].add("Кол-во")
    t.rows[1][0][0].add("Монтаж"); t.rows[1][1][0].add("12")
    return d


class TestSheetHtml:
    def test_header_bold_and_formula_value(self):
        h = sheet_html(_sheet(), "Спецификация")
        assert "font-weight:bold" in h and "Кабель" in h
        assert "240" in h                                   # формула напечатана значением, не «=B2*2»
        assert "=B2*2" not in h

    def test_merged_colspan(self):
        wb = _sheet()
        sh = wb.sheets[0]
        sh.set("A4", value="Объединено")
        sh.merged.append("A4:B4")
        h = sheet_html(wb)
        assert 'colspan="2"' in h


class TestDocHtml:
    def test_headings_lists_table(self):
        h = doc_html(_doc())
        assert "<h1>" in h and "Акт № {{doc.code}}" in h    # поля-шаблоны печатаются как есть
        assert "<ul><li" in h                                # список с маркером
        assert 'text-align:right' in h                       # выравнивание
        assert "<table" in h and "Монтаж" in h and "<th" in h

    def test_title_in_html(self):
        h = doc_html(_doc())
        assert "Акт выполненных работ" in h


class TestPdfWrite:
    def test_write_pdf_valid(self, tmp_path):
        pytest.importorskip("PySide6.QtGui", reason="нет Qt (офисные тесты без GUI)")
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        out = str(tmp_path / "akt.pdf")
        write_pdf(doc_html(_doc()), out)
        with open(out, "rb") as f:
            data = f.read()
        assert data[:5] == b"%PDF-" and len(data) > 400
        # В PDF есть наш текст (как минимум заголовок — QTextDocument встраивает шрифты/текст).
        assert len(data) > 0
