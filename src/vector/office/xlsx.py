"""Импорт/экспорт OOXML .xlsx (фаза Ф1.1, 55-office-documents.md § 5): без потерь в обе стороны.

Известное раскладывается по модели (листы, ячейки, формулы, стили базовые, объединения, ширины колонок);
неизвестное (диаграммы, сводные, макросы, рисунки…) сохраняется в Workbook.unknown_parts и при экспорте
возвращается байт-в-байт — файл снова открывается в Excel как раньше.

Зависимостей нет: zipfile + xml.etree (стандартная библиотека). Пространства имён OOXML — константы ниже.
"""
from __future__ import annotations

import io
import re
import zipfile
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET                # генерация ответов; разбор чужого — SafeET (XXE-защита)
import defusedxml.ElementTree as SafeET

from .model import (T_BOOL, T_NUMBER, T_STRING, Cell, Sheet, Workbook, parse_addr)
from .vsheetx import SheetFormatError

# Пространства имён SpreadsheetML (ECMA-376)
M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"

# Части, которые мы пишем сами и потому НЕ считаем «неизвестными» при импорте.
_KNOWN_PARTS = re.compile(
    r"^(\[Content_Types\]\.xml|_rels/\.rels|docProps/.*|xl/workbook\.xml|xl/_rels/workbook\.xml\.rels|"
    r"xl/worksheets/sheet\d+\.xml|xl/sharedStrings\.xml|xl/styles\.xml|xl/theme/theme\d+\.xml)$")


def _local(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def read_xlsx(data: bytes) -> Workbook:
    """Байты .xlsx → книга. Битый ZIP/отсутствует workbook — SheetFormatError с понятным текстом."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SheetFormatError("Файл не читается как .xlsx (нужен ZIP-пакет OOXML).") from None
    try:
        wb_root = SafeET.fromstring(z.read("xl/workbook.xml"))
    except KeyError:
        raise SheetFormatError("В файле нет xl/workbook.xml — это не книга Excel.") from None
    except ET.ParseError:
        raise SheetFormatError("xl/workbook.xml повреждён.") from None

    # Общие строки (sharedStrings) — большинство строк ячеек ссылаются сюда по индексу.
    shared: list[str] = []
    try:
        ss_root = SafeET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in ss_root:
            # Строка может быть «богатым текстом»: несколько <r><t> — склеиваем (начертание теряем на импорте,
            # оно вернётся при экспорте через unknown-проход только если лист не редактировали — ограничение Ф1.1).
            shared.append("".join(t.text or "" for t in si.iter(f"{{{M}}}t")))
    except KeyError:
        pass

    # Листы: workbook.xml даёт имя и r:id → по .rels находим файл листа.
    rels: dict[str, str] = {}
    try:
        rels_root = SafeET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        for rel in rels_root:
            rid, target = rel.get("Id"), rel.get("Target", "")
            rels[rid] = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
    except KeyError:
        pass

    wb = Workbook()
    for sh in wb_root.iter(f"{{{M}}}sheet"):
        name = sh.get("name") or "Лист"
        path = rels.get(sh.get(f"{{{R}}}id"), "")
        if not path or not path.startswith("xl/worksheets/"):
            continue
        sheet = Sheet(name)
        try:
            sroot = SafeET.fromstring(z.read(path))
        except (KeyError, ET.ParseError):
            wb.sheets.append(sheet)
            continue
        for el in sroot.iter():
            tag = _local(el)
            if tag == "c":
                _read_cell(el, sheet, shared)
            elif tag == "mergeCell":
                ref = el.get("ref")
                if ref:
                    sheet.merged.append(ref)
            elif tag == "col":
                try:
                    mn, w = int(el.get("min", "1")) - 1, float(el.get("width", "0"))
                    if w:
                        sheet.col_widths[mn] = w
                except ValueError:
                    continue
        wb.sheets.append(sheet)
    # Стили Ф1.1 не разбираем глубоко (начертания из styles.xml — порция Ф1.4, UI): модель держит свой стиль 0.
    # Неизвестное — на хранение для round-trip.
    for n in z.namelist():
        if not _KNOWN_PARTS.match(n) and not n.endswith("/"):
            wb.unknown_parts[n] = z.read(n)
    return wb


def _read_cell(el: ET.Element, sheet: Sheet, shared: list[str]) -> None:
    """Ячейка OOXML → модель. Типы: s (shared string), inlineStr, str (формульная строка), b, n, e (ошибка —
    храним строкой вида #ДЕЛ/0!)."""
    addr = el.get("r")
    if not addr:
        return
    try:
        parse_addr(addr)
    except ValueError:
        return                                       # чужой файл — терпимо пропускаем битый адрес
    kind = el.get("t", "n")
    f_el = el.find(f"{{{M}}}f")
    formula = None
    if f_el is not None and f_el.text:
        formula = "=" + f_el.text.strip().lstrip("=")
    v_el = el.find(f"{{{M}}}v")
    is_el = el.find(f"{{{M}}}is")
    value = None
    if kind == "s" and v_el is not None and v_el.text is not None:
        idx = int(v_el.text)
        value = shared[idx] if 0 <= idx < len(shared) else ""
    elif kind == "inlineStr" and is_el is not None:
        value = "".join(t.text or "" for t in is_el.iter(f"{{{M}}}t"))
    elif kind == "b" and v_el is not None:
        value = v_el.text in ("1", "true")
    elif kind in ("n", "e") and v_el is not None and v_el.text is not None:
        if kind == "e":
            value = v_el.text                      # ошибку (#N/A и т.п.) храним строкой — не теряем
        else:
            try:
                value = Decimal(v_el.text)
            except InvalidOperation:
                value = v_el.text                  # странное число чужого файла — строкой, не теряем
    elif kind == "str" and v_el is not None:
        value = v_el.text
    if value is not None or formula is not None:
        sheet.cells[addr] = Cell(value=value, formula=formula)


# ---------------------------------------------------------------- экспорт

def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_xlsx(wb: Workbook) -> bytes:
    """Книга → байты .xlsx: минимальный валидный OOXML (Excel/LibreOffice открывают), + unknown_parts обратно.

    Строки пишем inline (без sharedStrings) — валидно по стандарту и проще; формулы — без префикса '=' (так
    хранит OOXML). Даты как числа не пишем (модель Ф1.1 хранит Decimal/строки/булевы).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types(wb))
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   f'<Relationships xmlns="{REL}">'
                   f'<Relationship Id="rId1" Type="{R.replace("/relationships", "/relationships")}/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        z.writestr("xl/workbook.xml", _workbook_xml(wb))
        z.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(wb))
        for i, sheet in enumerate(wb.sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(sheet))
        for name in sorted(wb.unknown_parts):
            z.writestr(name, wb.unknown_parts[name])
    return buf.getvalue()


def _content_types(wb: Workbook) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(len(wb.sheets)))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{overrides}</Types>")


def _workbook_xml(wb: Workbook) -> str:
    sheets = "".join(
        f'<sheet name="{_xml_escape(s.name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, s in enumerate(wb.sheets))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<workbook xmlns="{M}" xmlns:r="{R}"><sheets>{sheets}</sheets></workbook>')


def _workbook_rels(wb: Workbook) -> str:
    rels = "".join(
        f'<Relationship Id="rId{i + 1}" Type="{R}/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
        for i in range(len(wb.sheets)))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{REL}">{rels}</Relationships>')


def _sheet_xml(sheet: Sheet) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             f'<worksheet xmlns="{M}" xmlns:r="{R}">']
    if sheet.col_widths:
        parts.append("<cols>")
        for idx in sorted(sheet.col_widths):
            parts.append(f'<col min="{idx + 1}" max="{idx + 1}" width="{sheet.col_widths[idx]}" customWidth="1"/>')
        parts.append("</cols>")
    parts.append("<sheetData>")
    rows: dict[int, list[tuple[str, Cell]]] = {}
    for addr, c in sheet.cells.items():
        r, _col = parse_addr(addr)
        rows.setdefault(r, []).append((addr, c))
    for r in sorted(rows):
        parts.append(f'<row r="{r + 1}">')
        for addr, c in sorted(rows[r], key=lambda ac: parse_addr(ac[0])[1]):
            parts.append(_cell_xml(addr, c))
        parts.append("</row>")
    parts.append("</sheetData>")
    if sheet.merged:
        parts.append(f'<mergeCells count="{len(sheet.merged)}">')
        for rng in sorted(sheet.merged):
            parts.append(f'<mergeCell ref="{_xml_escape(rng)}"/>')
        parts.append("</mergeCells>")
    parts.append("</worksheet>")
    return "".join(parts)


def _cell_xml(addr: str, c: Cell) -> str:
    out = [f'<c r="{addr}"']
    if c.formula is None and c.value is None:
        return "".join(out) + "/>"
    if c.kind == T_BOOL:
        out.append(' t="b"')
    elif c.kind == T_STRING and c.formula is None:
        out.append(' t="inlineStr"')
    out.append(">")
    if c.formula is not None:
        out.append(f"<f>{_xml_escape(c.formula.lstrip('='))}</f>")
    if c.value is None:
        pass
    elif c.kind == T_BOOL:
        out.append(f"<v>{'1' if c.value else '0'}</v>")
    elif c.kind == T_NUMBER:
        out.append(f"<v>{format(c.value, 'f')}</v>")
    elif c.formula is not None:
        out.append(f"<v>{_xml_escape(str(c.value))}</v>")          # последнее посчитанное значение формулы
    else:
        out.append(f"<is><t>{_xml_escape(str(c.value))}</t></is>")  # inline-строка
    out.append("</c>")
    return "".join(out)
