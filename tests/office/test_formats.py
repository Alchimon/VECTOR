"""Тесты формата .vsheetx и OOXML .xlsx (Ф1.1, 55-office-documents.md § 5): round-trip без потерь в обе стороны.
"""
import io
import zipfile
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from vector.office.model import Sheet, Style, Workbook, col_to_index, index_to_col, make_addr, parse_addr
from vector.office.vsheetx import SheetFormatError, read_vsheetx, write_vsheetx
from vector.office.xlsx import read_xlsx, write_xlsx


def sample_workbook() -> Workbook:
    """Книга с типичным набором: числа (Decimal, в т. ч. денежные), строки, булево, формула, стиль,
    объединение, ширина колонки, второй лист."""
    wb = Workbook()
    s1 = wb.sheet("Спецификация")
    bold = wb.style_add(Style(bold=True, number_format="0.00", fill="FBEEDD"))
    s1.set("A1", "Позиция", style_id=bold)
    s1.set("B1", "Количество", style_id=bold)
    s1.set("A2", "Кабель ВВГнг 3×2,5")
    s1.set("B2", Decimal("120.5"))
    s1.set("B3", Decimal("120.5000"))                    # точность: не превращается в float-шум
    s1.set("C2", True)
    s1.set("B4", Decimal("241"), formula="=SUM(B2:B3)")
    s1.set("D1", "Комментарий с <тегами> & амперсандом") # экранирование
    s1.merged.append("A1:D1")
    s1.col_widths[0] = 28.5
    wb.sheet("Лист 2").set("A1", Decimal("42"))
    return wb


class TestAddr:
    def test_col_roundtrip(self):
        for col, idx in [("A", 0), ("Z", 25), ("AA", 26), ("BC", 54), ("XFD", 16383)]:
            assert col_to_index(col) == idx and index_to_col(idx) == col

    def test_addr_parse_and_make(self):
        assert parse_addr("BC12") == (11, 54)
        assert make_addr(11, 54) == "BC12"
        with pytest.raises(ValueError):
            parse_addr("1A")                              # номер строки обязателен
        with pytest.raises(ValueError):
            parse_addr("A0")                              # строки с 1


class TestVsheetx:
    def test_roundtrip_values(self):
        wb = sample_workbook()
        back = read_vsheetx(write_vsheetx(wb))
        assert [s.name for s in back.sheets] == ["Спецификация", "Лист 2"]
        s1 = back.sheets[0]
        assert s1.get("B2").value == Decimal("120.5")
        assert s1.get("B3").value == Decimal("120.5000")  # масштаб сохранён
        assert s1.get("C2").value is True
        assert s1.get("A2").value == "Кабель ВВГнг 3×2,5"
        assert s1.get("D1").value == "Комментарий с <тегами> & амперсандом"
        f = s1.get("B4")
        assert f.formula == "=SUM(B2:B3)" and f.value == Decimal("241")
        assert s1.get("A1").style_id == 1
        assert back.styles[1].bold and back.styles[1].number_format == "0.00" and back.styles[1].fill == "FBEEDD"
        assert s1.merged == ["A1:D1"] and s1.col_widths[0] == 28.5
        assert back.sheets[1].get("A1").value == Decimal("42")

    def test_deterministic_bytes(self):
        wb = sample_workbook()
        assert write_vsheetx(wb) == write_vsheetx(wb)      # каноническая запись

    def test_empty_cell_removed(self):
        s = Sheet("Лист")
        s.set("A1", "x")
        s.set("A1", None)
        assert "A1" not in s.cells and s.extent == (0, 0)

    def test_bad_file_errors(self):
        with pytest.raises(SheetFormatError):
            read_vsheetx(b"not a zip at all")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.txt", "hi")
        with pytest.raises(SheetFormatError):
            read_vsheetx(buf.getvalue())

    def test_unknown_parts_roundtrip(self):
        wb = sample_workbook()
        wb.unknown_parts["xl/charts/chart1.xml"] = b"<chart/>"
        back = read_vsheetx(write_vsheetx(wb))
        assert back.unknown_parts["xl/charts/chart1.xml"] == b"<chart/>"


class TestXlsx:
    def test_export_import_roundtrip(self):
        """Наш .xlsx → наш же импорт: значения, формулы, объединения, ширины — без потерь."""
        wb = sample_workbook()
        back = read_xlsx(write_xlsx(wb))
        assert [s.name for s in back.sheets] == ["Спецификация", "Лист 2"]
        s1 = back.sheets[0]
        assert s1.get("B2").value == Decimal("120.5")
        assert s1.get("C2").value is True
        assert s1.get("A2").value == "Кабель ВВГнг 3×2,5"
        assert s1.get("D1").value == "Комментарий с <тегами> & амперсандом"
        f = s1.get("B4")
        assert f.formula == "=SUM(B2:B3)" and f.value == Decimal("241")
        assert s1.merged == ["A1:D1"] and abs(s1.col_widths[0] - 28.5) < 1e-9

    def test_vsheetx_xlsx_vsheetx(self):
        """vsheetx → xlsx → vsheetx: модель идентична (двусторонняя миграция без потерь)."""
        wb = sample_workbook()
        via_xlsx = read_xlsx(write_xlsx(wb))
        back = read_vsheetx(write_vsheetx(via_xlsx))
        s1 = back.sheets[0]
        assert s1.get("B3").value == Decimal("120.5000")
        assert s1.get("B4").formula == "=SUM(B2:B3)"
        assert s1.merged == ["A1:D1"]

    def test_import_handmade_xlsx(self):
        """Чужой .xlsx ручной сборки (sharedStrings, число, булево, ошибка-значение) читается."""
        M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        REL = "http://schemas.openxmlformats.org/package/2006/relationships"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("[Content_Types].xml",
                       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                       '<Default Extension="xml" ContentType="application/xml"/>'
                       '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                       '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            z.writestr("_rels/.rels",
                       f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{R}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr("xl/workbook.xml",
                       f'<workbook xmlns="{M}" xmlns:r="{R}"><sheets><sheet name="Данные" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels",
                       f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr("xl/sharedStrings.xml",
                       f'<sst xmlns="{M}"><si><t>Кабель</t></si><si><r><t>Мног</t></r><r><t>ость</t></r></si></sst>')
            z.writestr("xl/worksheets/sheet1.xml",
                       f'<worksheet xmlns="{M}"><sheetData>'
                       f'<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                       f'<row r="2"><c r="A2"><v>7.25</v></c><c r="B2" t="b"><v>1</v></c>'
                       f'<c r="C2" t="e"><v>#ДЕЛ/0!</v></c><c r="D2"><f>SUM(A2:A2)</f><v>7.25</v></c></row>'
                       f"</sheetData></worksheet>")
            z.writestr("xl/charts/chart1.xml", "<chartSpace/>")   # неизвестное — должно пережить round-trip
        wb = read_xlsx(buf.getvalue())
        s = wb.sheets[0]
        assert s.name == "Данные"
        assert s.get("A1").value == "Кабель"
        assert s.get("B1").value == "Многость"                    # rich text склеен
        assert s.get("A2").value == Decimal("7.25")
        assert s.get("B2").value is True
        assert s.get("C2").value == "#ДЕЛ/0!"                     # ошибка сохранена строкой
        d2 = s.get("D2")
        assert d2.formula == "=SUM(A2:A2)" and d2.value == Decimal("7.25")
        assert wb.unknown_parts["xl/charts/chart1.xml"] == b"<chartSpace/>"
        # Round-trip: экспорт возвращает диаграмму, значения на месте.
        back = read_xlsx(write_xlsx(wb))
        assert back.unknown_parts["xl/charts/chart1.xml"] == b"<chartSpace/>"
        assert back.sheets[0].get("A2").value == Decimal("7.25")

    def test_bad_xlsx_errors(self):
        with pytest.raises(SheetFormatError):
            read_xlsx(b"junk")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/styles.xml", "<x/>")
        with pytest.raises(SheetFormatError):
            read_xlsx(buf.getvalue())                             # нет workbook.xml

    def test_export_is_valid_zip_with_ooxml_parts(self):
        data = write_xlsx(sample_workbook())
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                    "xl/_rels/workbook.xml.rels", "xl/worksheets/sheet1.xml"} <= names
            # workbook.xml парсится и ссылается на листы
            root = ET.fromstring(z.read("xl/workbook.xml"))
            assert root.tag.endswith("workbook")
