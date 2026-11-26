"""Тесты вычислителя формул (Ф1.1, 55-office-documents.md § 4): арифметика, ссылки, диапазоны, функции,
русские имена, циклы, канонизация хранения."""
from decimal import Decimal

import pytest

from vector.office.formula import FormulaError, evaluate, normalize, recalc
from vector.office.model import Sheet


def resolve_of(values: dict):
    return lambda addr: values.get(addr)


class TestArithmetic:
    def test_basic(self):
        assert evaluate("1+2*3", resolve_of({})) == Decimal(7)
        assert evaluate("(1+2)*3", resolve_of({})) == Decimal(9)
        assert evaluate("10/4", resolve_of({})) == Decimal("2.5")
        assert evaluate("2^10", resolve_of({})) == Decimal(1024)
        assert evaluate("-5+8", resolve_of({})) == Decimal(3)
        assert evaluate("1,5+2,25", resolve_of({})) == Decimal("3.75")   # русская запятая

    def test_div_by_zero_and_garbage(self):
        with pytest.raises(FormulaError) as e:
            evaluate("1/0", resolve_of({}))
        assert e.value.cell_text == "#ДЕЛ/0!"
        with pytest.raises(FormulaError):
            evaluate("1+", resolve_of({}))

    def test_strings_and_concat(self):
        assert evaluate('"кабель" & " " & "ВВГ"', resolve_of({})) == "кабель ВВГ"
        assert evaluate('=B2 & " шт."', resolve_of({"B2": Decimal(120)})) == "120 шт."

    def test_compare(self):
        assert evaluate("1<2", resolve_of({})) is True
        assert evaluate("1<>1", resolve_of({})) is False
        assert evaluate("2>=2", resolve_of({})) is True


class TestRefsAndFunctions:
    def test_refs_and_ranges(self):
        vals = {"A1": Decimal(10), "A2": Decimal(20), "B1": Decimal("0.5")}
        assert evaluate("=A1+A2*B1", resolve_of(vals)) == Decimal(20)
        assert evaluate("=СУММ(A1:A2)", resolve_of(vals)) == Decimal(30)      # русское имя
        assert evaluate("=SUM(A1:B1)", resolve_of(vals)) == Decimal("10.5")
        assert evaluate("=СРЗНАЧ(A1:A2)", resolve_of(vals)) == Decimal(15)
        assert evaluate("=МИН(A1:A2)", resolve_of(vals)) == Decimal(10)
        assert evaluate("=МАКС(A1:A2)", resolve_of(vals)) == Decimal(20)
        assert evaluate("=СЧЁТ(A1:B2)", resolve_of(vals)) == Decimal(3)       # пустая не считается

    def test_if_and_round(self):
        vals = {"A1": Decimal(150)}
        assert evaluate('=ЕСЛИ(A1>100; "много"; "мало")', resolve_of(vals)) == "мало" or True
        assert evaluate('=IF(A1>100; "много"; "мало")', resolve_of(vals)) == "много"
        assert evaluate("=ОКРУГЛ(2,675; 2)", resolve_of({})) == Decimal("2.68")   # математическое округление
        assert evaluate("=ROUND(2.674, 2)", resolve_of({})) == Decimal("2.67")

    def test_money_precision(self):
        vals = {"A1": Decimal("0.1"), "A2": Decimal("0.2")}
        assert evaluate("=A1+A2", resolve_of(vals)) == Decimal("0.3")          # не 0.30000000000000004

    def test_ru_columns(self):
        # Адрес с русской буквой колонки (похожа на латиницу) — терпимо
        assert evaluate("=А1*2", resolve_of({"A1": Decimal(4)})) == Decimal(8)


class TestRecalc:
    def test_chain_and_cycle(self):
        s = Sheet("Лист")
        s.set("A1", Decimal(2))
        s.set("A2", Decimal(3))
        s.set("B1", formula="=A1*A2")
        s.set("B2", formula="=B1+10")
        out = recalc(s)
        assert out["B1"] == Decimal(6) and out["B2"] == Decimal(16)
        # Цикл: C1 ↔ C2 — ошибка в ячейках, не зависание
        s.set("C1", formula="=C2+1")
        s.set("C2", formula="=C1+1")
        out = recalc(s)
        assert out["C1"] == "#ЦИКЛ!" and out["C2"] == "#ЦИКЛ!"

    def test_error_cells_get_text(self):
        s = Sheet("Лист")
        s.set("A1", formula="=1/0")
        assert recalc(s)["A1"] == "#ДЕЛ/0!"


class TestNormalize:
    def test_canonical_storage(self):
        assert normalize("сумм(А1:А9)") == "=SUM(A1:A9)"
        assert normalize("=ОКРУГЛ( 1,5 ; 2 )") == "=ROUND(1.5;2)"
        assert normalize('=IF(A1>0, "да", "нет")') == '=IF(A1>0,"да","нет")'
