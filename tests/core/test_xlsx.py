"""Файлы .xlsx для расчёта (R-31): точность без float, предел Excel в 15 значащих цифр,
атомарная запись (без частичного файла), импорт «всё или ничего» с номерами строк листа."""
import os
import zipfile
from decimal import Decimal

import pytest

from vector.core import calc, xlsx


def _book(rows):
    """Собрать книгу и прочитать её обратно ячейками."""
    return xlsx.read_cells(xlsx.write_rows(rows))


class TestRoundTrip:
    def test_decimal_strings_survive_exactly(self):
        """Канонические строки («0.1», «12.5000») не превращаются в float-мусор."""
        rows = _book([["a", "0.1", "12.5000", "999999999999999"]])
        assert [c.text for c in rows[0][1:]] == ["0.1", "12.5000", "999999999999999"]
        assert all(c.numeric for c in rows[0][1:])

    def test_longer_than_15_digits_written_as_text(self):
        """Число длиннее 15 значащих цифр пишется текстом — Excel не исказит; читаем точно."""
        rows = _book([["99999999999999.9999"]])     # 18 значащих — предел NUMERIC(18,4)
        assert rows[0][0].text == "99999999999999.9999" and not rows[0][0].numeric

    def test_unicode_and_escaping(self):
        rows = _book([['Труба & "дёшево" <красная>', "комментарий"]])
        assert rows[0][0].text == 'Труба & "дёшево" <красная>'

    def test_empty_cells_and_rows(self):
        rows = xlsx.read_rows(xlsx.write_rows([["a", "", "c"], ["", "", ""], ["x"]]))
        assert rows == [["a", "", "c"], ["", "", ""], ["x", "", ""]]  # середина сохраняется,
        # хвостовая пустая строка отброшена, строки выровнены по ширине

    def test_empty_sheet(self):
        assert xlsx.read_rows(xlsx.write_rows([])) == []


class TestReadForeign:
    """Книги, собранные не нами: sharedStrings, разреженные ячейки, экспоненциальная запись."""

    def _foreign(self, sheet_xml, shared=None):
        parts = {
            "[Content_Types].xml": "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>",
            "xl/workbook.xml": (
                "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main' "
                "xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>"
                "<sheets><sheet name='S' sheetId='1' r:id='rId1'/></sheets></workbook>"),
            "xl/_rels/workbook.xml.rels": (
                "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
                "<Relationship Id='rId1' Target='worksheets/sheet1.xml'/></Relationships>"),
            "xl/worksheets/sheet1.xml": sheet_xml,
        }
        if shared:
            parts["xl/sharedStrings.xml"] = shared
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for k, v in parts.items():
                zf.writestr(k, v)
        return buf.getvalue()

    def test_shared_strings_and_sparse_cells(self):
        data = self._foreign(
            "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData>"
            "<row r='1'><c r='A1' t='s'><v>0</v></c><c r='D1'><v>2.5</v></c></row></sheetData></worksheet>",
            shared="<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
                   "<si><t>Кабель</t></si></sst>")
        row = xlsx.read_cells(data)[0]
        assert row[0].text == "Кабель" and row[3].text == "2.5" and row[3].numeric
        assert [c.text for c in row[1:3]] == ["", ""]

    def test_scientific_notation_is_decimal_text(self):
        data = self._foreign(
            "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData>"
            "<row r='1'><c r='A1'><v>1.2345678901234567E+20</v></c></row></sheetData></worksheet>")
        cell = xlsx.read_cells(data)[0][0]
        assert cell.numeric and xlsx.significant_digits(cell.text) == 17

    def test_bad_zip(self):
        with pytest.raises(xlsx.XlsxError, match="не является книгой"):
            xlsx.read_rows(b"not a zip at all")

    def test_doctype_rejected(self):
        data = self._foreign(
            "<!DOCTYPE x [<!ENTITY e 'boom'>]><worksheet "
            "xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData/></worksheet>")
        with pytest.raises(xlsx.XlsxError, match="определения сущностей"):
            xlsx.read_rows(data)


class TestAtomicWrite:
    def test_write_and_replace(self, tmp_path):
        p = tmp_path / "calc.xlsx"
        xlsx.atomic_write(p, b"one")
        xlsx.atomic_write(p, b"two")
        assert p.read_bytes() == b"two" and not list(tmp_path.glob("*.tmp"))

    def test_failure_leaves_no_partial_file(self, tmp_path, monkeypatch):
        p = tmp_path / "calc.xlsx"
        p.write_bytes(b"previous")
        monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(xlsx.XlsxError, match="Не удалось записать"):
            xlsx.atomic_write(p, b"new")
        assert p.read_bytes() == b"previous"          # прежний файл не тронут
        assert not list(tmp_path.glob("*.tmp"))       # временный убран


class TestCalcMapping:
    def _cells(self, rows, numeric_cols=()):
        return [[xlsx.Cell(str(v), numeric=(j in numeric_cols and v != "")) for j, v in enumerate(r)]
                for r in rows]

    def test_export_header_and_rows(self):
        rows = calc.lines_to_sheet([{"code": "КБЛ", "name": "Кабель", "unit": "м",
                                     "qty": "120.5000", "unit_price": "99.9900", "note": None}])
        assert rows[0] == calc.SHEET_HEADER
        assert rows[1] == ["КБЛ", "Кабель", "м", "120.5000", "99.9900", ""]

    def test_import_skips_header_and_blank_rows(self):
        lines, problems = calc.sheet_to_lines(self._cells([
            calc.SHEET_HEADER, ["", "", "", "", "", ""],
            ["КБЛ", "Кабель", "м", "120.5", "99.99", ""]]))
        assert not problems and len(lines) == 1 and lines[0]["name"] == "Кабель"

    def test_import_reports_sheet_row_numbers(self):
        lines, problems = calc.sheet_to_lines(self._cells([
            calc.SHEET_HEADER,
            ["КБЛ", "Кабель", "м", "120.5", "99.99", ""],
            ["", "Без количества", "шт", "", "10", ""],          # строка листа 3
            ["", "", "", "", "", ""],
            ["", "Нулевое количество", "шт", "0", "", ""]]))     # строка листа 5
        assert lines == []                                       # всё или ничего
        rows = {p["line"] for p in problems}
        assert rows == {3, 5}

    def test_import_rejects_numeric_beyond_excel_precision(self):
        """Числовая ячейка > 15 значащих цифр — замечание excel_precision (Excel уже исказил)."""
        lines, problems = calc.sheet_to_lines(self._cells(
            [["", "Кабель", "м", "999999999999.9999", "", ""]], numeric_cols={3}))
        assert lines == [] and [p["code"] for p in problems] == ["excel_precision"]

    def test_same_value_as_text_passes_precision_guard(self):
        """Текстовая ячейка не искажена Excel — идёт обычной проверкой (16 значащих в пределах NUMERIC(18,4))."""
        lines, problems = calc.sheet_to_lines(self._cells(
            [["", "Кабель", "м", "123456789012.3456", "", ""]]))
        assert not problems
        assert Decimal(lines[0]["qty"]) == Decimal("123456789012.3456")

    def test_empty_sheet_reported(self):
        lines, problems = calc.sheet_to_lines(self._cells([["", "", "", "", "", ""]]))
        assert lines == [] and problems[0]["code"] == "empty_sheet"

    def test_exported_book_reimports_clean(self):
        """Круговой обход: позиции → файл → позиции без замечаний."""
        src = [{"line_id": "ignored", "code": "АТХ-01", "name": "Шкаф автоматики", "unit": "шт",
                "qty": "2.0000", "unit_price": "1500000.0000", "note": "осн."}]
        data = xlsx.write_rows(calc.lines_to_sheet(src))
        lines, problems = calc.sheet_to_lines(xlsx.read_cells(data))
        assert not problems and len(lines) == 1
        assert lines[0]["qty"] == "2.0000" and lines[0]["unit_price"] == "1500000.0000"
