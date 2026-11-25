"""Тесты формата .vdocx и OOXML .docx (Ф1.2, 55-office-documents.md § 3/§ 5): round-trip без потерь."""
import io
import zipfile
from xml.etree import ElementTree as ET

import pytest

from vector.office.docx import read_docx, write_docx
from vector.office.model_doc import Document, Paragraph, TableBlock
from vector.office.vdocx import DocFormatError, read_vdocx, write_vdocx


def sample_doc() -> Document:
    """Акт выполненных работ: заголовок, абзацы с начертанием, список, таблица — типичный документ пилота."""
    doc = Document(title="Акт выполненных работ")
    doc.para("Акт № 17 от 25.11.2026", style="heading", level=1, align="center")
    p = doc.para()
    p.add("Исполнитель: ")
    p.add("ООО «ВЕКТОР»", bold=True)
    p.add(", заказчик — ")
    p.add("ООО «Заказчик»", italic=True, underline=True)
    doc.para("Перечень работ:", style="normal")
    doc.para("Монтаж шкафа ЩР-1", style="list-number")
    doc.para("Пусконаладка ПНР", style="list-number")
    t = doc.table(2, 2, header=True)
    t.rows[0][0][0].add("Работа", bold=True)
    t.rows[0][1][0].add("Сумма", bold=True)
    t.rows[1][0][0].add("Монтаж")
    t.rows[1][1][0].add("120 000,00 ₽")
    doc.para("Подпись: ____________", align="right")
    return doc


class TestVdocx:
    def test_roundtrip(self):
        doc = sample_doc()
        back = read_vdocx(write_vdocx(doc))
        assert back.title == "Акт выполненных работ"
        h = back.blocks[0]
        assert isinstance(h, Paragraph) and h.style == "heading" and h.level == 1 and h.align == "center"
        p1 = back.blocks[1]
        assert p1.text == "Исполнитель: ООО «ВЕКТОР», заказчик — ООО «Заказчик»"
        assert p1.runs[1].bold and p1.runs[3].italic and p1.runs[3].underline
        assert back.blocks[3].style == "list-number"
        t = next(b for b in back.blocks if isinstance(b, TableBlock))
        assert t.header and t.cell_text(0, 0) == "Работа" and t.cell_text(1, 1) == "120 000,00 ₽"
        assert back.blocks[-1].align == "right"

    def test_deterministic(self):
        doc = sample_doc()
        assert write_vdocx(doc) == write_vdocx(doc)

    def test_bad_file(self):
        with pytest.raises(DocFormatError):
            read_vdocx(b"junk")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.txt", "x")
        with pytest.raises(DocFormatError):
            read_vdocx(buf.getvalue())

    def test_unknown_parts_roundtrip(self):
        doc = sample_doc()
        doc.unknown_parts["word/media/image1.png"] = b"\x89PNG fake"
        back = read_vdocx(write_vdocx(doc))
        assert back.unknown_parts["word/media/image1.png"] == b"\x89PNG fake"


class TestDocx:
    def test_export_import_roundtrip(self):
        doc = sample_doc()
        back = read_docx(write_docx(doc))
        h = back.blocks[0]
        assert h.style == "heading" and h.level == 1 and h.align == "center"
        assert "ВЕКТОР" in back.blocks[1].text
        assert back.blocks[1].runs[1].bold
        assert back.blocks[3].style == "list-number"
        t = next(b for b in back.blocks if isinstance(b, TableBlock))
        assert t.cell_text(1, 1) == "120 000,00 ₽"
        assert back.blocks[-1].align == "right"

    def test_vdocx_docx_vdocx(self):
        doc = sample_doc()
        via = read_docx(write_docx(doc))
        back = read_vdocx(write_vdocx(via))
        assert "Пусконаладка" in back.text and "120 000,00 ₽" in back.text

    def test_import_handmade_docx(self):
        """Чужой .docx ручной сборки: заголовок, полужирный прогон, таблица; рисунок переживает round-trip."""
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        REL = "http://schemas.openxmlformats.org/package/2006/relationships"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("[Content_Types].xml",
                       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                       '<Default Extension="xml" ContentType="application/xml"/>'
                       '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            z.writestr("_rels/.rels",
                       f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>')
            z.writestr("word/document.xml",
                       f'<w:document xmlns:w="{W}"><w:body>'
                       f'<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Шапка проекта</w:t></w:r></w:p>'
                       f'<w:p><w:r><w:rPr><w:b/><w:sz w:val="28"/></w:rPr><w:t>Жирный 14pt</w:t></w:r>'
                       f'<w:r><w:t> и обычный</w:t></w:r></w:p>'
                       f'<w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>'
                       f'<w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                       f"</w:body></w:document>")
            z.writestr("word/media/logo.png", b"PNG-DATA")
        doc = read_docx(buf.getvalue())
        assert doc.blocks[0].style == "heading" and doc.blocks[0].level == 2
        p = doc.blocks[1]
        assert p.runs[0].bold and p.runs[0].font_size == 14.0 and p.runs[1].text == " и обычный"
        t = doc.blocks[2]
        assert isinstance(t, TableBlock) and t.cell_text(0, 1) == "B"
        assert doc.unknown_parts["word/media/logo.png"] == b"PNG-DATA"
        back = read_docx(write_docx(doc))
        assert back.unknown_parts["word/media/logo.png"] == b"PNG-DATA"
        assert "Шапка проекта" in back.text

    def test_bad_docx(self):
        with pytest.raises(DocFormatError):
            read_docx(b"junk")

    def test_export_valid_parts(self):
        data = write_docx(sample_doc())
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml"} <= set(z.namelist())
            ET.fromstring(z.read("word/document.xml"))          # валидный XML
            ET.fromstring(z.read("word/styles.xml"))
