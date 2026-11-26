"""Правила склада (vector.core.stock): количество, доступно = остаток − резерв, сторно, покрытие потребности."""
from decimal import Decimal as D

import pytest

from vector.core import stock
from vector.core.stock import Balance, StockError


@pytest.mark.parametrize("raw,code", [
    ("0", "qty_not_positive"), ("-1", "qty_not_positive"), ("0.0000", "qty_not_positive"),
    (1.5, "float_forbidden"), (2, "invalid_type"), (True, "invalid_type"), ("", "invalid_format"),
    ("1,5", "invalid_format"), ("1.00001", "too_many_fraction_digits"), ("100000000000000", "out_of_range"),
])
def test_qty_rejects(raw, code):
    with pytest.raises(StockError) as e:
        stock.parse_qty(raw)
    assert e.value.code == code


def test_qty_accepts_exact_decimal():
    assert stock.parse_qty("12.5") == D("12.5")
    assert stock.parse_qty("0.0001") == D("0.0001")


@pytest.mark.parametrize("raw", ["КБ-01", "M6x20", "a.b_c/d-1", "Я"])
def test_code_ok(raw):
    assert stock.check_code(raw) == raw


@pytest.mark.parametrize("raw,code", [(None, "field_required"), ("  ", "field_required"), ("a b", "invalid_code"),
                                      ("-x", "invalid_code"), ("x" * 65, "invalid_code"), (5, "field_required")])
def test_code_bad(raw, code):
    with pytest.raises(StockError) as e:
        stock.check_code(raw)
    assert e.value.code == code


def test_available_is_on_hand_minus_reserved():
    b = Balance(D("10"), D("3"))
    assert b.available == D("7")
    assert b.to_json() == {"on_hand": "10.0000", "reserved": "3.0000", "available": "7.0000"}


def test_free_issue_cannot_touch_other_reserves():
    b = Balance(D("10"), D("3"))
    assert stock.issue_free(b, D("7")) == Balance(D("3"), D("3"))
    with pytest.raises(StockError) as e:
        stock.issue_free(b, D("7.0001"))
    assert e.value.code == "insufficient_available"
    assert e.value.details["balance"]["available"] == "7.0000"


def test_reserved_issue_consumes_reserve_and_is_capped_by_remaining():
    b = Balance(D("10"), D("3"))
    assert stock.issue_reserved(b, D("2"), D("3")) == Balance(D("8"), D("1"))
    with pytest.raises(StockError) as e:
        stock.issue_reserved(b, D("4"), D("3"))
    assert e.value.code == "exceeds_reservation"


def test_reserve_only_from_available_and_release_returns_it():
    b = stock.reserve(Balance(D("5"), D("1")), D("4"))
    assert b == Balance(D("5"), D("5")) and b.available == 0
    with pytest.raises(StockError):
        stock.reserve(b, D("0.0001"))
    assert stock.release(b, D("4")) == Balance(D("5"), D("1"))


def test_reverse_receipt_blocked_when_goods_are_reserved():
    b = Balance(D("5"), D("2"))
    assert stock.reverse_receipt(b, D("3")) == Balance(D("2"), D("2"))
    with pytest.raises(StockError) as e:
        stock.reverse_receipt(b, D("4"))
    assert e.value.code == "insufficient_available"


def test_invariant_guard_catches_program_errors():
    with pytest.raises(StockError) as e:
        stock.release(Balance(D("5"), D("1")), D("2"))
    assert e.value.code == "stock_invariant"


def test_receive_range_guard():
    with pytest.raises(StockError) as e:
        stock.receive(Balance(D("99999999999999.9999")), D("1"))
    assert e.value.code == "out_of_range"


def test_coverage_unknown_is_not_zero():
    assert stock.coverage(D("10"), D("4"), D("6")) == {"required": "10.0000", "reserved": "4.0000", "open": "6.0000",
                                                     "available": "6.0000", "can_cover": True}
    assert stock.coverage(D("10"), D("4"), D("5"))["can_cover"] is False
    unk = stock.coverage(D("10"), D("0"), None)
    assert unk["can_cover"] is None and unk["available"] is None
    assert stock.coverage(D("2"), D("3"), D("0"))["open"] == "0.0000"   # резерв больше нужного — «открыто» не отрицательное


def test_convert_qty_is_exact_or_refuses():
    from decimal import Decimal as D
    from vector.core import stock as st
    assert st.convert_qty(D("1.85"), D("1000")) == D("1850.0000")          # 1,85 км → 1850 м
    assert st.convert_qty(D("7"), D("0.0833")) == D("0.5831")              # точный результат — принимается
    with pytest.raises(st.StockError) as e:
        st.convert_qty(D("0.5"), D("0.0001"))                               # 0,00005 — пятый знак
    assert e.value.code == "conversion_inexact" and e.value.details["exact"] == "0.00005"
    with pytest.raises(st.StockError) as e:
        st.convert_qty(D("99999999999999"), D("1000"))
    assert e.value.code == "out_of_range"
