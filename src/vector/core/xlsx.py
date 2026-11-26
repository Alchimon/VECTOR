"""Файлы Excel (.xlsx, OOXML Spreadsheet) без внешних зависимостей и без float (требование R-31).

Почему не openpyxl и не float:

* число в .xlsx хранится в XML как десятичный текст («12.5000»); читаем его как есть — в Decimal,
  без прохода через двоичную плавающую точку (0.1 не превращается в 0.1000000000000000055);
* Excel сам хранит числа с точностью не более 15 значащих цифр (float64). Поэтому при ЗАПИСИ
  десятичное значение длиннее 15 значащих цифр пишется ТЕКСТОМ — точность не теряется,
  а при ЧТЕНИИ числовая ячейка с более чем 15 значащими цифрами — повод для замечания
  (Excel такое значение уже исказил, см. calc.sheet_to_lines, код excel_precision);
* запись файла атомарна (atomic_write): временный файл + fsync + os.replace — обрыв записи
  не оставляет обрезанного .xlsx («без частичного файла», R-31).

Модуль чистый (без БД и Qt), общий для клиента и тестов. Поддерживается первый лист книги —
для обмена расчётом этого достаточно; чужие книги с несколькими листами читаются по первому листу.
"""
from __future__ import annotations

import io
import os
import re
import uuid
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple, Optional
from xml.etree import ElementTree as ET

# Пределы — защита от битых и враждебных файлов (zip-бомбы, гигантские листы).
MAX_FILE = 32 * 1024 * 1024            # сжатый .xlsx
MAX_UNPACKED = 256 * 1024 * 1024       # суммарный распакованный объём
MAX_ROWS = 5000
MAX_COLS = 64
# Точность Excel (float64): числа длиннее пишем/принимаем только текстом.
EXCEL_SIG_DIGITS = 15

_PLAIN_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


class XlsxError(ValueError):
    """Файл не удалось прочитать как книгу .xlsx. Текст — по-русски, для показа пользователю."""


class Cell(NamedTuple):
    """Ячейка листа: текстовое значение (числа — исходным текстом из XML) и признак «было числом»."""

    text: str
    numeric: bool = False


def _ln(tag: str) -> str:
    """Локальное имя XML-тега без пространства имён («{…}sheetData» → «sheetData»)."""
    return tag.rsplit("}", 1)[-1]


def _parse_xml(raw: bytes, what: str) -> ET.Element:
    """Разобрать XML с защитой от внешних сущностей (как в core.prodcal): DOCTYPE/ENTITY запрещены."""
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise XlsxError(f"{what}: файл содержит определения сущностей — не принимается из соображений безопасности.")  # noqa: S314
    try:
        return ET.fromstring(raw)  # noqa: S314 — внешние сущности запрещены проверкой выше
    except ET.ParseError:
        raise XlsxError(f"{what}: повреждённый XML внутри .xlsx — файл не открывается.") from None


def _unzip(data: bytes) -> zipfile.ZipFile:
    """Открыть .xlsx как zip с пределами по размеру (защита от zip-бомбы)."""
    if len(data) > MAX_FILE:
        raise XlsxError(f"Файл больше {MAX_FILE // (1024 * 1024)} МБ — это не книга расчёта.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise XlsxError("Файл не является книгой Excel (.xlsx) или повреждён.") from None
    if sum(i.file_size for i in zf.infolist()) > MAX_UNPACKED:
        raise XlsxError("Распакованный размер книги неприемлемо велик — файл не принят.")
    return zf


def _col_index(ref: str) -> int:
    """«B7» → 1 (номер колонки с 0). Колонки дальше MAX_COLS игнорируются вызывающим кодом."""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - ord("A") + 1)
    return n - 1


def _sheet_path(zf: zipfile.ZipFile) -> str:
    """Путь XML первого листа: workbook.xml → первый <sheet r:id> → workbook.xml.rels → Target."""
    try:
        wb = _parse_xml(zf.read("xl/workbook.xml"), "Книга")
    except KeyError:
        raise XlsxError("В файле нет xl/workbook.xml — это не книга .xlsx.") from None
    rid = None
    for el in wb.iter():
        if _ln(el.tag) == "sheet":
            rid = next((v for k, v in el.attrib.items() if k.rsplit("}", 1)[-1] == "id"), None)
            break
    if not rid:
        raise XlsxError("В книге нет ни одного листа.")
    rels = _parse_xml(zf.read("xl/_rels/workbook.xml.rels"), "Связи книги")
    for el in rels.iter():
        if _ln(el.tag) == "Relationship" and el.attrib.get("Id") == rid:
            target = el.attrib.get("Target", "")
            if target.startswith("/"):
                return target.lstrip("/")
            return "xl/" + target.lstrip("/")
    raise XlsxError("Не найден первый лист книги (связи workbook.xml).")


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    """Общие строки книги (Excel пишет текст через sharedStrings.xml; наш писатель — inline)."""
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = _parse_xml(raw, "Общие строки")
    out = []
    for si in root:
        if _ln(si.tag) != "si":
            continue
        out.append("".join(t.text or "" for t in si.iter() if _ln(t.tag) == "t"))
    return out


def read_cells(data: bytes) -> list[list[Cell]]:
    """Первый лист книги → строки ячеек. Числа — исходным текстом из XML (без float).

    Пустые хвостовые строки отбрасываются; строки выравниваются по ширине самой широкой.
    """
    zf = _unzip(data)
    shared = _shared_strings(zf)
    try:
        raw = zf.read(_sheet_path(zf))
    except KeyError:
        raise XlsxError("Не найден XML первого листа — файл повреждён.") from None
    root = _parse_xml(raw, "Лист")
    rows: list[list[Cell]] = []
    for el in root.iter():
        if _ln(el.tag) != "row":
            continue
        if len(rows) >= MAX_ROWS:
            raise XlsxError(f"На листе больше {MAX_ROWS} строк — такой расчёт не принимается.")
        row: list[Cell] = []
        for c in el:
            if _ln(c.tag) != "c":
                continue
            idx = _col_index(c.attrib.get("r", ""))
            if idx < 0 or idx >= MAX_COLS:
                continue
            while len(row) <= idx:
                row.append(Cell(""))
            t = c.attrib.get("t", "")
            v = next((x for x in c if _ln(x.tag) == "v"), None)
            if t == "s":
                num = v.text if v is not None and v.text else "0"
                row[idx] = Cell(shared[int(num)] if num.isdigit() and int(num) < len(shared) else "")
            elif t == "inlineStr":
                row[idx] = Cell("".join(x.text or "" for x in c.iter() if _ln(x.tag) == "t"))
            elif v is not None and v.text is not None:
                # Число (без t), строка формулы (t="str"), логическое (t="b") — текстом как записано.
                row[idx] = Cell(v.text, numeric=(t == ""))
            else:
                row[idx] = Cell("")
        rows.append(row)
    while rows and all(not c.text.strip() for c in rows[-1]):
        rows.pop()
    width = max((len(r) for r in rows), default=0)
    return [r + [Cell("")] * (width - len(r)) for r in rows]


def read_rows(data: bytes) -> list[list[str]]:
    """То же, что read_cells, но только тексты ячеек (для простых применений)."""
    return [[c.text for c in r] for r in read_cells(data)]


def significant_digits(text: str) -> Optional[int]:
    """Число значащих цифр десятичной строки; None — если это не число. Пробелы-разделители недопустимы."""
    try:
        d = Decimal(text.strip())
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return len(d.normalize().as_tuple().digits)


def _cell_xml(ref: str, value: str) -> str:
    """XML ячейки: десятичное значение в пределах точности Excel — числом, остальное — текстом."""
    text = value.strip()
    if text and _PLAIN_NUMBER.match(text):
        sig = significant_digits(text)
        if sig is not None and sig <= EXCEL_SIG_DIGITS:
            return f'<c r="{ref}"><v>{text}</v></c>'
    esc = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{esc}</t></is></c>'


def _col_name(idx: int) -> str:
    """0 → «A», 25 → «Z», 26 → «AA»."""
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(ord("A") + rem) + s
    return s


def write_rows(rows: list[list[str]], sheet_name: str = "Лист1") -> bytes:
    """Собрать минимальную корректную книгу .xlsx с одним листом из строк текстовых значений."""
    if len(rows) > MAX_ROWS:
        raise XlsxError(f"Не более {MAX_ROWS} строк в файле расчёта.")
    sheet = ['<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
    for i, row in enumerate(rows, start=1):
        if len(row) > MAX_COLS:
            raise XlsxError(f"Не более {MAX_COLS} колонок в файле расчёта.")
        cells = "".join(_cell_xml(f"{_col_name(j)}{i}", v) for j, v in enumerate(row) if str(v).strip())
        sheet.append(f'<row r="{i}">{cells}</row>')
    sheet.append("</sheetData></worksheet>")
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    parts = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>",
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{main}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>",
        "xl/styles.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<styleSheet xmlns="{main}"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="1"><fill><patternFill patternType="none"/></patternFill></fills>'
            '<borders count="1"><border/></borders>'
            '<cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="1"><xf/></cellXfs></styleSheet>',
        "xl/worksheets/sheet1.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + "".join(sheet),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in parts.items():
            zf.writestr(name, content)
    return buf.getvalue()


def atomic_write(path: Path, data: bytes) -> None:
    """Записать файл атомарно: временный файл в том же каталоге → fsync → os.replace.

    Обрыв на любом шаге оставляет либо прежний файл, либо ничего — но не обрезанный .xlsx (R-31).
    """
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise XlsxError(f"Не удалось записать файл «{path.name}»: {e.strerror or e}.") from e
