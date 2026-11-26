"""Свой формат .vsheetx (фаза Ф1.1, 55-office-documents.md § 2): ZIP с XML-частями, надмножество SpreadsheetML.

Детерминированная запись: части в фиксированном порядке, ячейки по адресу, XML с отступами — файлы сравнимы.
Чтение терпимое: неизвестные элементы пропускаются (вперёд-совместимость минорных версий формата).
"""
from __future__ import annotations

import io
import zipfile
from decimal import Decimal
from xml.etree import ElementTree as ET                # запись XML (генерация — стандартная библиотека)
import defusedxml.ElementTree as SafeET                 # разбор чужих файлов: защита от XXE/billion laughs

from .model import (T_BOOL, T_NUMBER, Cell, Sheet, Style, Workbook, parse_addr)

NS = "urn:vector:sheet:1"                          # наше пространство имён (надмножество — свои части свои)
CONTENT_TYPE = "application/vnd.vector.sheet"      # для установки ассоциации файла в ОС


class SheetFormatError(Exception):
    """Файл .vsheetx не читается — сообщение пользователю (без трассировки)."""


def _fmt_decimal(v: Decimal) -> str:
    """Decimal → строка без экспоненты (деньги точно: 0.10 не станет 0.1E-1)."""
    return format(v, "f")


def _esc_text(v: str) -> str:
    return v                                       # ET сам экранирует; функция — точка расширения (BOM и пр.)


def _sheet_xml(sheet: Sheet) -> bytes:
    root = ET.Element(f"{{{NS}}}sheet", attrib={"name": sheet.name})
    if sheet.col_widths:
        cols = ET.SubElement(root, f"{{{NS}}}cols")
        for idx in sorted(sheet.col_widths):
            ET.SubElement(cols, f"{{{NS}}}col", attrib={"i": str(idx), "w": str(sheet.col_widths[idx])})
    for addr in sorted(sheet.cells, key=lambda a: parse_addr(a)):
        c = sheet.cells[addr]
        el = ET.SubElement(root, f"{{{NS}}}c", attrib={"r": addr})
        if c.style_id:
            el.set("s", str(c.style_id))
        if c.formula is not None:
            ET.SubElement(el, f"{{{NS}}}f").text = c.formula
        if c.value is not None:
            k = c.kind
            el.set("t", k)
            v = ET.SubElement(el, f"{{{NS}}}v")
            v.text = ("1" if c.value else "0") if k == T_BOOL else (
                _fmt_decimal(c.value) if k == T_NUMBER else _esc_text(str(c.value)))
    if sheet.merged:
        m = ET.SubElement(root, f"{{{NS}}}merged")
        for rng in sorted(sheet.merged):
            ET.SubElement(m, f"{{{NS}}}range").text = rng
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _workbook_xml(wb: Workbook) -> bytes:
    root = ET.Element(f"{{{NS}}}workbook", attrib={"format_version": str(wb.format_version)})
    for s in wb.sheets:
        ET.SubElement(root, f"{{{NS}}}sheet", attrib={"name": s.name})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _styles_xml(wb: Workbook) -> bytes:
    root = ET.Element(f"{{{NS}}}styles")
    for st in wb.styles:
        el = ET.SubElement(root, f"{{{NS}}}style")
        if st.bold:
            el.set("bold", "1")
        if st.italic:
            el.set("italic", "1")
        if st.font_size != 11.0:
            el.set("font_size", str(st.font_size))
        if st.number_format:
            el.set("number_format", st.number_format)
        if st.fill:
            el.set("fill", st.fill)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_vsheetx(wb: Workbook) -> bytes:
    """Книга → байты .vsheetx (ZIP). Порядок частей фиксирован; unknown_parts — после известных, по имени."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("workbook.xml", _workbook_xml(wb))
        z.writestr("styles.xml", _styles_xml(wb))
        for i, s in enumerate(wb.sheets):
            z.writestr(f"sheets/sheet-{i + 1}.xml", _sheet_xml(s))
        for name in sorted(wb.unknown_parts):
            z.writestr(f"unknown/{name}", wb.unknown_parts[name])
    return buf.getvalue()


def _read_xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return SafeET.fromstring(z.read(name))
    except KeyError:
        raise SheetFormatError(f"В файле нет обязательной части {name} — это не .vsheetx?") from None
    except ET.ParseError:
        raise SheetFormatError(f"Часть {name} не читается (повреждённый XML).") from None


def _tag(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]               # локальное имя без пространства


def read_vsheetx(data: bytes) -> Workbook:
    """Байты .vsheetx → книга. Не ZIP/нет workbook.xml — SheetFormatError с понятным текстом."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SheetFormatError("Файл не читается как .vsheetx (нужен ZIP-пакет).") from None
    root = _read_xml(z, "workbook.xml")
    if _tag(root) != "workbook":
        raise SheetFormatError("Это не книга .vsheetx (нет элемента workbook).")
    wb = Workbook(format_version=int(root.get("format_version", "1")))
    names = [el.get("name") for el in root if _tag(el) == "sheet"]
    # Стили
    styles_root = _read_xml(z, "styles.xml")
    wb.styles = []
    for el in styles_root:
        if _tag(el) != "style":
            continue
        wb.styles.append(Style(bold=el.get("bold") == "1", italic=el.get("italic") == "1",
                               font_size=float(el.get("font_size", "11")),
                               number_format=el.get("number_format", ""), fill=el.get("fill", "")))
    if not wb.styles:
        wb.styles = [Style()]
    # Листы — по порядку имён в workbook.xml, файлы sheets/sheet-N.xml по индексу
    for i, name in enumerate(names):
        sroot = _read_xml(z, f"sheets/sheet-{i + 1}.xml")
        sheet = Sheet(name or f"Лист {i + 1}")
        for el in sroot.iter():
            t = _tag(el)
            if t == "col":
                sheet.col_widths[int(el.get("i"))] = float(el.get("w"))
            elif t == "c":
                addr = el.get("r")
                parse_addr(addr)                   # битый адрес — ошибка формата (файл не наш)
                kind = el.get("t")
                f_el = el.find(f"{{{NS}}}f")
                v_el = el.find(f"{{{NS}}}v")
                value = None
                if v_el is not None and v_el.text is not None:
                    raw = v_el.text
                    value = (Decimal(raw) if kind == T_NUMBER else
                             (raw == "1") if kind == T_BOOL else raw)
                sheet.cells[addr] = Cell(value=value,
                                         formula=f_el.text if f_el is not None else None,
                                         style_id=int(el.get("s", "0")))
            elif t == "range" and el.text:
                sheet.merged.append(el.text.strip())
        wb.sheets.append(sheet)
    # Неизвестное — возвращаем для round-trip
    for n in z.namelist():
        if n.startswith("unknown/"):
            wb.unknown_parts[n[len("unknown/"):]] = z.read(n)
    return wb
