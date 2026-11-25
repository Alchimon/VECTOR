"""Свой формат .vdocx (Ф1.2, 55-office-documents.md § 3): ZIP с XML-частями, тот же подход, что у .vsheetx.

document.xml — блоки (абзацы/заголовки/списки/таблицы) с прогонами и начертанием; unknown/ — неизвестное
из импортированного .docx (изображения, колонтитулы, SmartArt) — возвращается при экспорте байт-в-байт.
Детерминированная запись; чтение терпимое (неизвестные атрибуты пропускаются).
"""
from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET                # запись XML (генерация — стандартная библиотека)

import defusedxml.ElementTree as SafeET                 # разбор чужих файлов: защита от XXE/billion laughs

from .model_doc import Document, Paragraph, Run, TableBlock

NS = "urn:vector:doc:1"
CONTENT_TYPE = "application/vnd.vector.doc"

_STYLES = {"normal", "heading", "list-bullet", "list-number"}
_ALIGNS = {"left", "center", "right", "justify"}


class DocFormatError(Exception):
    """Файл .vdocx не читается — сообщение пользователю (без трассировки)."""


def _run_xml(parent: ET.Element, r: Run) -> None:
    el = ET.SubElement(parent, f"{{{NS}}}r")
    if r.bold:
        el.set("b", "1")
    if r.italic:
        el.set("i", "1")
    if r.underline:
        el.set("u", "1")
    if r.font_size != 12.0:
        el.set("sz", str(r.font_size))
    if r.font:
        el.set("font", r.font)
    el.text = r.text


def _para_xml(parent: ET.Element, p: Paragraph) -> None:
    attrib = {}
    if p.style != "normal":
        attrib["style"] = p.style
    if p.level != 1:
        attrib["level"] = str(p.level)
    if p.align != "left":
        attrib["align"] = p.align
    el = ET.SubElement(parent, f"{{{NS}}}p", attrib=attrib)
    for r in p.runs:
        _run_xml(el, r)


def _table_xml(parent: ET.Element, t: TableBlock) -> None:
    el = ET.SubElement(parent, f"{{{NS}}}tbl", attrib={"header": "1" if t.header else "0"})
    for row in t.rows:
        r_el = ET.SubElement(el, f"{{{NS}}}tr")
        for cell in row:
            c_el = ET.SubElement(r_el, f"{{{NS}}}tc")
            for p in cell:
                _para_xml(c_el, p)


def _document_xml(doc: Document) -> bytes:
    root = ET.Element(f"{{{NS}}}document",
                      attrib={"format_version": str(doc.format_version), "title": doc.title})
    for b in doc.blocks:
        if isinstance(b, Paragraph):
            _para_xml(root, b)
        elif isinstance(b, TableBlock):
            _table_xml(root, b)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_vdocx(doc: Document) -> bytes:
    """Документ → байты .vdocx (ZIP). Порядок частей фиксирован; unknown_parts — после известных, по имени."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("document.xml", _document_xml(doc))
        for name in sorted(doc.unknown_parts):
            z.writestr(f"unknown/{name}", doc.unknown_parts[name])
    return buf.getvalue()


def _local(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _read_run(el: ET.Element) -> Run:
    return Run(text=el.text or "", bold=el.get("b") == "1", italic=el.get("i") == "1",
               underline=el.get("u") == "1", font_size=float(el.get("sz", "12")), font=el.get("font", ""))


def _read_para(el: ET.Element) -> Paragraph:
    p = Paragraph(style=el.get("style", "normal"), level=int(el.get("level", "1")),
                  align=el.get("align", "left"))
    if p.style not in _STYLES:
        p.style = "normal"                             # неизвестный стиль чужой версии — терпимо в абзац
    if p.align not in _ALIGNS:
        p.align = "left"
    if el.text and el.text.strip():
        p.runs.append(Run(el.text))                    # текст вне прогона (терпимость к чужим файлам)
    for ch in el:
        if _local(ch) == "r":
            p.runs.append(_read_run(ch))
    return p


def read_vdocx(data: bytes) -> Document:
    """Байты .vdocx → документ. Не ZIP/нет document.xml — DocFormatError с понятным текстом."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise DocFormatError("Файл не читается как .vdocx (нужен ZIP-пакет).") from None
    try:
        root = SafeET.fromstring(z.read("document.xml"))
    except KeyError:
        raise DocFormatError("В файле нет document.xml — это не документ .vdocx.") from None
    except ET.ParseError:
        raise DocFormatError("document.xml повреждён.") from None
    if _local(root) != "document":
        raise DocFormatError("Это не документ .vdocx (нет элемента document).")
    doc = Document(title=root.get("title", ""), format_version=int(root.get("format_version", "1")))
    for el in root:
        tag = _local(el)
        if tag == "p":
            doc.blocks.append(_read_para(el))
        elif tag == "tbl":
            t = TableBlock(header=el.get("header") == "1")
            for r_el in el:
                if _local(r_el) != "tr":
                    continue
                row = []
                for c_el in r_el:
                    if _local(c_el) != "tc":
                        continue
                    row.append([_read_para(p) for p in c_el if _local(p) == "p"] or [Paragraph()])
                t.rows.append(row)
            doc.blocks.append(t)
    for n in z.namelist():
        if n.startswith("unknown/"):
            doc.unknown_parts[n[len("unknown/"):]] = z.read(n)
    return doc
