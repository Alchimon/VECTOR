"""Печать офисных документов в PDF (Ф1.4-доп, D-96): .vsheetx/.vdocx → PDF через QPdfWriter.

Чистая сборка HTML из модели ядра (без Qt) — в `sheet_html`/`doc_html`; запись в PDF — `write_pdf`
(QPdfWriter + QTextDocument: стили, заливки, колонтитулы). Локально, без связи; работает и офлайн.

Инварианты (тесты tests/office/test_pdf.py):
* таблица: шапка жирная (первый лист, первая строка), формулы — последним вычисленным значением (как CSV);
  объединённые ячейки — colspan/rowspan; ширины колонок из модели;
* текст: заголовки крупнее и жирнее, списки с маркерами/номерами, таблицы — с рамками; выравнивание;
* поля-шаблоны {{…}} печатаются как есть (это документ, а не render — подстановка делает templates.render);
* PDF — валидный (начинается с %PDF), страницы разбиваются по высоте автоматически (QTextDocument).
"""
from __future__ import annotations

import html
from decimal import Decimal
from typing import Any

from vector.office.model import Workbook, index_to_col, parse_addr
from vector.office.model_doc import Document, Paragraph, TableBlock

# ---------- сборка HTML (без Qt — тестируется изолированно) ----------

def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def sheet_html(wb: Workbook, title: str = "") -> str:
    """Первый лист книги → HTML-таблица: шапка (первая строка) жирная, формулы — значением, объединения и
    ширины колонок сохраняются. Возвращает полный HTML-документ."""
    sh = wb.sheets[0]
    rows, cols = sh.extent
    if rows == 0:
        rows, cols = 1, 1
    # Объединённые диапазоны: {старт-ячейка: (colspan, rowspan)} и множество покрытых (не рисуем их).
    spans: dict[str, tuple[int, int]] = {}
    covered: set[str] = set()
    for rng in sh.merged:
        try:
            (r1, c1), (r2, c2) = (parse_addr(a) for a in rng.split(":"))
        except (ValueError, AttributeError):
            continue
        spans[f"{index_to_col(c1)}{r1 + 1}"] = (c2 - c1 + 1, r2 - r1 + 1)
        for rr in range(r1, r2 + 1):
            for cc in range(c1, c2 + 1):
                if (rr, cc) != (r1, c1):
                    covered.add(f"{index_to_col(cc)}{rr + 1}")
    parts = ['<table border="0" cellspacing="0" cellpadding="4" style="border-collapse:collapse;">']
    # ширины колонок — отдельной служебной строкой colgroup (не смещает данные)
    if sh.col_widths:
        parts.append("<colgroup>")
        for c in range(cols):
            w = sh.col_widths.get(c)
            parts.append(f'<col style="width:{w}em;">' if w else "<col>")
        parts.append("</colgroup>")
    for r in range(rows):
        parts.append("<tr>")
        for c in range(cols):
            addr = f"{index_to_col(c)}{r + 1}"
            if addr in covered:
                continue
            cell = sh.get(addr)
            sp = spans.get(addr, (1, 1))
            attr = f' colspan="{sp[0]}"' if sp[0] > 1 else ""
            attr += f' rowspan="{sp[1]}"' if sp[1] > 1 else ""
            # стиль ячейки
            st = wb.styles[cell.style_id] if 0 <= cell.style_id < len(wb.styles) else None
            css = ' style="border:1px solid #999;'
            if st:
                if st.bold:
                    css += "font-weight:bold;"
                if st.italic:
                    css += "font-style:italic;"
                if st.fill:
                    css += f"background-color:#{st.fill};"
                if st.number_format:
                    pass
            css += '"'
            tag = "th" if r == 0 else "td"
            parts.append(f"<{tag}{attr}{css}>{html.escape(_cell_text(cell.value))}</{tag}>")
        parts.append("</tr>")
    parts.append("</table>")
    return _wrap_html(title or sh.name, "".join(parts))


def doc_html(doc: Document) -> str:
    """Текстовый документ → HTML: заголовки крупнее/жирнее, списки с маркерами/номерами, таблицы с рамками."""
    parts = []
    for b in doc.blocks:
        if isinstance(b, Paragraph):
            parts.append(_para_html(b))
        elif isinstance(b, TableBlock):
            parts.append(_table_html(b))
    return _wrap_html(doc.title, "".join(parts))


def _para_html(p: Paragraph) -> str:
    """Абзац → HTML по стилю: heading → h1..h4, list-bullet/number → li (грубо), иначе p."""
    text = html.escape(p.text)
    if not text:
        return "<p>&nbsp;</p>"
    align = f' style="text-align:{p.align};"' if p.align != "left" else ""
    if p.style == "heading":
        lvl = min(max(p.level, 1), 4)
        return f"<h{lvl}{align}>{text}</h{lvl}>"
    if p.style == "list-bullet":
        return f'<ul><li{align}>{text}</li></ul>'
    if p.style == "list-number":
        return f'<ol><li{align}>{text}</li></ol>'
    return f"<p{align}>{text}</p>"


def _table_html(t: TableBlock) -> str:
    rows = []
    for ri, row in enumerate(t.rows):
        tag = "th" if (t.header and ri == 0) else "td"
        cells = "".join(f'<{tag} style="border:1px solid #999;padding:4px;">'
                        f"{html.escape(t.cell_text(ri, ci))}</{tag}>" for ci in range(len(row)))
        rows.append(f"<tr>{cells}</tr>")
    return '<table border="0" cellspacing="0" style="border-collapse:collapse;">' + "".join(rows) + "</table>"


def _wrap_html(title: str, body: str) -> str:
    return ('<html><head><meta charset="utf-8"><style>'
            "body{font-family:sans-serif;font-size:11pt;} table{margin:6pt 0;}"
            "h1{font-size:16pt;} h2{font-size:14pt;} h3,h4{font-size:12pt;}"
            "</style></head><body>"
            + (f"<h1>{html.escape(title)}</h1>" if title else "")
            + body + "</body></html>")


# ---------- запись в PDF (Qt) ----------

def write_pdf(html_text: str, dest: str) -> None:
    """HTML → PDF в файл dest (QPdfWriter + QTextDocument). Вызывать в GUI-потоке с QApplication."""
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument
    w = QPdfWriter(dest)
    layout = QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Portrait,
                         QMarginsF(15, 15, 15, 15), QPageLayout.Millimeter)
    w.setPageLayout(layout)
    doc = QTextDocument()
    doc.setHtml(html_text)
    doc.setPageSize(QSizeF(layout.paintRectPixels(w.resolution()).size()))
    doc.print_(w)
