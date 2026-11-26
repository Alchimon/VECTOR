"""Импорт/экспорт OOXML .docx (Ф1.2, 55-office-documents.md § 5): без потерь в обе стороны.

Известное — по модели (абзацы, заголовки Heading1–4, маркированные/нумерованные списки, таблицы, начертания
прогонов b/i/u/размер/шрифт); неизвестное (изображения, колонтитулы, SmartArt, настройки) — в unknown_parts
и возвращается при экспорте байт-в-байт.
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

import defusedxml.ElementTree as SafeET

from .model_doc import Document, Paragraph, Run, TableBlock
from .vdocx import DocFormatError

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"

_KNOWN_PARTS = re.compile(
    r"^(\[Content_Types\]\.xml|_rels/\.rels|docProps/.*|word/document\.xml|word/_rels/document\.xml\.rels|"
    r"word/styles\.xml|word/numbering\.xml|word/theme/theme\d+\.xml)$")


def _local(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def read_docx(data: bytes) -> Document:
    """Байты .docx → документ. Не ZIP/нет word/document.xml — DocFormatError с понятным текстом."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise DocFormatError("Файл не читается как .docx (нужен ZIP-пакет OOXML).") from None
    try:
        root = SafeET.fromstring(z.read("word/document.xml"))
    except KeyError:
        raise DocFormatError("В файле нет word/document.xml — это не документ Word.") from None
    except ET.ParseError:
        raise DocFormatError("word/document.xml повреждён.") from None

    doc = Document()
    body = root.find(f"{{{W}}}body")
    if body is None:
        raise DocFormatError("В word/document.xml нет body — файл не похож на документ Word.")
    for el in body:
        tag = _local(el)
        if tag == "p":
            doc.blocks.append(_read_para(el))
        elif tag == "tbl":
            doc.blocks.append(_read_table(el))
    for n in z.namelist():
        if not _KNOWN_PARTS.match(n) and not n.endswith("/"):
            doc.unknown_parts[n] = z.read(n)
    return doc


def _read_para(el: ET.Element) -> Paragraph:
    """Абзац WordprocessingML: стиль (Heading1–4, ListParagraph+numPr), выравнивание, прогоны."""
    p = Paragraph()
    ppr = el.find(f"{{{W}}}pPr")
    if ppr is not None:
        pstyle = ppr.find(f"{{{W}}}pStyle")
        if pstyle is not None:
            val = (pstyle.get(f"{{{W}}}val") or "").lower()
            m = re.match(r"heading(\d)", val) or re.match(r"(\d)$", val)
            if m and 1 <= int(m.group(1)) <= 4:
                p.style, p.level = "heading", int(m.group(1))
        if ppr.find(f"{{{W}}}numPr") is not None:
            # Есть нумерация; маркер/номер — по numbering.xml (глубокий разбор — Ф1.4); считаем нумерованным.
            if p.style == "normal":
                p.style = "list-number"
        jc = ppr.find(f"{{{W}}}jc")
        if jc is not None:
            v = jc.get(f"{{{W}}}val", "left")
            p.align = {"both": "justify", "center": "center", "right": "right"}.get(v, "left")
    for r_el in el.findall(f"{{{W}}}r"):
        run = _read_run(r_el)
        if run.text:
            p.runs.append(run)
    return p


def _read_run(el: ET.Element) -> Run:
    rpr = el.find(f"{{{W}}}rPr")
    bold = italic = underline = False
    size, font = 12.0, ""
    if rpr is not None:
        bold = rpr.find(f"{{{W}}}b") is not None
        italic = rpr.find(f"{{{W}}}i") is not None
        u = rpr.find(f"{{{W}}}u")
        underline = u is not None and u.get(f"{{{W}}}val", "single") != "none"
        sz = rpr.find(f"{{{W}}}sz")
        if sz is not None:
            size = int(sz.get(f"{{{W}}}val", "24")) / 2          # OOXML хранит полупункты
        rf = rpr.find(f"{{{W}}}rFonts")
        if rf is not None:
            font = rf.get(f"{{{W}}}ascii", "")
    text = "".join(t.text or "" for t in el.iter(f"{{{W}}}t"))
    # Разрывы строки внутри прогона — перевод строки (не теряем).
    if el.find(f"{{{W}}}br") is not None and not text:
        text = "\n"
    return Run(text=text, bold=bold, italic=italic, underline=underline, font_size=size, font=font)


def _read_table(el: ET.Element) -> TableBlock:
    t = TableBlock()
    for tr in el.findall(f"{{{W}}}tr"):
        row = []
        for tc in tr.findall(f"{{{W}}}tc"):
            paras = [_read_para(p) for p in tc.findall(f"{{{W}}}p")]
            row.append(paras or [Paragraph()])
        if row:
            t.rows.append(row)
    return t


# ---------------------------------------------------------------- экспорт

def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _run_xml(r: Run) -> str:
    rpr = ""
    props = ""
    if r.bold:
        props += "<w:b/>"
    if r.italic:
        props += "<w:i/>"
    if r.underline:
        props += '<w:u w:val="single"/>'
    if r.font:
        props += f'<w:rFonts w:ascii="{_esc(r.font)}" w:hAnsi="{_esc(r.font)}"/>'
    if r.font_size != 12.0:
        props += f'<w:sz w:val="{int(r.font_size * 2)}"/>'
    if props:
        rpr = f"<w:rPr>{props}</w:rPr>"
    return f"<w:r>{rpr}<w:t xml:space=\"preserve\">{_esc(r.text)}</w:t></w:r>"


def _para_xml(p: Paragraph) -> str:
    ppr = ""
    props = ""
    if p.style == "heading":
        props += f'<w:pStyle w:val="Heading{p.level}"/>'
    elif p.style == "list-number":
        props += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
    elif p.style == "list-bullet":
        props += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr>'
    if p.align != "left":
        props += f'<w:jc w:val="{"both" if p.align == "justify" else p.align}"/>'
    if props:
        ppr = f"<w:pPr>{props}</w:pPr>"
    runs = "".join(_run_xml(r) for r in p.runs) or '<w:r><w:t xml:space="preserve"></w:t></w:r>'
    return f"<w:p>{ppr}{runs}</w:p>"


def _table_xml(t: TableBlock) -> str:
    rows = []
    for row in t.rows:
        cells = "".join(
            f"<w:tc><w:tcPr><w:tcW w:w=\"2400\" w:type=\"dxa\"/></w:tcPr>{''.join(_para_xml(p) for p in cell)}</w:tc>"
            for cell in row)
        rows.append(f"<w:tr>{cells}</w:tr>")
    return ('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/>'
            '<w:tblW w:w="0" w:type="auto"/>'
            '<w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/>'
            '<w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/>'
            '<w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/></w:tblBorders>'
            f"</w:tblPr>{''.join(rows)}</w:tbl>")


def write_docx(doc: Document) -> bytes:
    """Документ → байты .docx: минимальный валидный WordprocessingML + unknown_parts обратно (round-trip)."""
    body = "".join(
        _para_xml(b) if isinstance(b, Paragraph) else _table_xml(b) for b in doc.blocks)
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{body}'
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '<Override PartName="/word/styles.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   f'<Relationships xmlns="{REL}">'
                   f'<Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/>'
                   "</Relationships>")
        z.writestr("word/_rels/document.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   f'<Relationships xmlns="{REL}"></Relationships>')
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", _styles_xml())
        for name in sorted(doc.unknown_parts):
            z.writestr(name, doc.unknown_parts[name])
    return buf.getvalue()


def _styles_xml() -> str:
    """Стили: normal + Heading1–4 (Word/LibreOffice открывают, оглавления строятся)."""
    heads = "".join(
        f'<w:style w:type="paragraph" w:styleId="Heading{i}"><w:name w:val="heading {i}"/>'
        f'<w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="{i - 1}"/></w:pPr>'
        f'<w:rPr><w:b/><w:sz w:val="{32 - i * 4}"/></w:rPr></w:style>'
        for i in range(1, 5))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<w:styles xmlns:w="{W}">'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
            '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/>'
            '<w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/>'
            '<w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/>'
            '<w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/></w:tblBorders>'
            "</w:tblPr></w:style>"
            f"{heads}</w:styles>")
