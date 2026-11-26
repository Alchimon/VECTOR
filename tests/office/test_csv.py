"""CSV-обмен «Таблиц» (D-93): русский Excel («;», запятая десятичная), автотип, round-trip."""
from decimal import Decimal

from vector.office.csvmx import read_csv, write_csv
from vector.office.model import Sheet


def test_read_ru_excel_csv():
    text = "Позиция;Количество;Срочно\nКабель ВВГнг 3×2,5;120,5;ИСТИНА\nАвтомат ВА47-29;24;ЛОЖЬ\n"
    s = read_csv(text)
    assert s.get("A1").value == "Позиция"
    assert s.get("B2").value == Decimal("120.5")          # «120,5» → Decimal
    assert s.get("C2").value is True and s.get("C3").value is False
    assert s.get("A3").value == "Автомат ВА47-29"
    assert s.extent == (3, 3)


def test_detect_comma_and_tab():
    assert read_csv("a,b,c\n1,2,3", autotype=False).get("B1").value == "b"
    assert read_csv("a\tb\n1\t2", autotype=False).get("B1").value == "b"
    assert read_csv("﻿x;y", autotype=False).get("A1").value == "x"      # BOM снят


def test_roundtrip_ru():
    s = Sheet("Лист")
    s.set("A1", "Наименование")
    s.set("B1", "Кол-во")
    s.set("A2", "Кабель")
    s.set("B2", Decimal("120.5"))
    s.set("C2", True)
    s.set("B3", Decimal(241), formula="=SUM(B2:B2)")      # формула → последнее значение
    text = write_csv(s)
    assert "120,5" in text and "ИСТИНА" in text
    back = read_csv(text)
    assert back.get("B2").value == Decimal("120.5")
    assert back.get("B3").value == Decimal(241)           # значение формулы выгружено
    assert back.get("C2").value is True


def test_autotype_off():
    s = read_csv("код\n00125", autotype=False)            # коды с ведущими нулями не превращаем в числа
    assert s.get("A2").value == "00125"


def test_empty_cells():
    s = read_csv("a;;c\n;;\n", autotype=False)
    assert s.get("A1").value == "a" and s.get("C1").value == "c"
    assert s.extent == (1, 3)                              # пустая вторая строка не раздувает область
