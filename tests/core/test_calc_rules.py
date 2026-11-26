"""Правила расчёта комплекта (vector.core.calc): точность, «неизвестно ≠ 0», без молчаливого округления, diff."""
import uuid
from decimal import Decimal

import pytest

from vector.core import calc


def L(name="Кабель", qty="10", price="12.5", unit="м", **kw):
    """Позиция расчёта для теста (короткое имя — тесты читаются как таблица)."""
    return {"line_id": kw.pop("line_id", str(uuid.uuid4())), "name": name, "unit": unit, "qty": qty, "unit_price": price, **kw}


def test_amount_is_exact_and_total_is_decimal():
    lines, cur = calc.parse_lines([L(qty="2.5", price="1234.56"), L(qty="3", price="0.3333")], "RUB")
    assert cur == "RUB"
    assert [ln.amount for ln in lines] == [Decimal("3086.4000"), Decimal("0.9999")]
    t = calc.totals(lines)
    assert t == {"total": "3087.3999", "line_count": 2, "priced_count": 2, "unknown_price_count": 0, "complete": True}


def test_unknown_price_is_not_zero():
    lines, _ = calc.parse_lines([L(price=None), L(price="0", qty="1"), L(qty="2", price="5")], "RUB")
    t = calc.totals(lines)
    # Неизвестная цена не входит в сумму и делает итог неполным; известный ноль — входит (0).
    assert t["total"] == "10.0000" and t["unknown_price_count"] == 1 and t["priced_count"] == 2 and not t["complete"]
    assert lines[0].amount is None and lines[1].amount == Decimal("0")


def test_no_silent_rounding():
    with pytest.raises(calc.CalcError) as e:
        calc.parse_lines([L(qty="0.333", price="100.01")], "RUB")        # 33.30333 — нужно округление
    assert e.value.problems[0]["code"] == "amount_needs_rounding" and e.value.problems[0]["line"] == 1


@pytest.mark.parametrize("field,value,code", [
    ("qty", "0", "qty_not_positive"), ("qty", "-1", "qty_not_positive"), ("qty", 1.5, "float_forbidden"),
    ("qty", "1.00001", "too_many_fraction_digits"), ("qty", None, "required"), ("qty", "1e3", "invalid_format"),
    ("unit_price", "-0.01", "price_negative"), ("unit_price", 10.0, "float_forbidden"),
    ("name", "  ", "required"), ("unit", None, "required"), ("name", "x" * 301, "too_long"),
    ("line_id", "not-a-uuid", "invalid_line_id"),
])
def test_line_validation(field, value, code):
    raw = L()
    raw[field] = value
    with pytest.raises(calc.CalcError) as e:
        calc.parse_lines([raw], "RUB")
    assert code in [p["code"] for p in e.value.problems]


def test_currency_rules():
    # Без цен валюта не обязательна; с ценой — обязательна и не подставляется.
    assert calc.parse_lines([L(price=None)], None)[1] is None
    with pytest.raises(calc.CalcError) as e:
        calc.parse_lines([L()], None)
    assert e.value.code == "currency_required"
    with pytest.raises(calc.CalcError) as e:
        calc.parse_lines([L()], "rub")
    assert e.value.code == "invalid_currency"


def test_all_problems_are_reported_with_line_numbers():
    with pytest.raises(calc.CalcError) as e:
        calc.parse_lines([L(), L(qty="0"), L(name=""), {"x": 1}], "RUB")
    lines = sorted({p["line"] for p in e.value.problems})
    assert lines == [2, 3, 4]


def test_duplicate_line_ids_and_limits():
    lid = str(uuid.uuid4())
    with pytest.raises(calc.CalcError) as e:
        calc.parse_lines([L(line_id=lid), L(line_id=lid)], "RUB")
    assert e.value.code == "duplicate_line_id"
    with pytest.raises(calc.CalcError):
        calc.parse_lines([L() for _ in range(calc.MAX_LINES + 1)], "RUB")
    with pytest.raises(calc.CalcError) as e:
        calc.parse_lines([L(qty="99999999999999", price="99999999999999")], "RUB")
    assert e.value.code == "out_of_range"


def test_total_out_of_range_rejected():
    with pytest.raises(calc.CalcError) as e:
        calc.parse_lines([L(qty="1", price="99999999999999.9999"), L(qty="1", price="1")], "RUB")
    assert e.value.code == "total_out_of_range"


def test_content_hash_is_canonical():
    a = L(qty="2", price="3")
    b = dict(a, qty="2.0000", unit_price="3.00")
    h1 = calc.content_hash(calc.parse_lines([a], "RUB")[0], "RUB", None)
    h2 = calc.content_hash(calc.parse_lines([b], "RUB")[0], "RUB", None)
    assert h1 == h2 and len(h1) == 64
    # Порядок позиций, валюта и примечание — часть содержимого.
    c = L()
    assert calc.content_hash(calc.parse_lines([a, c], "RUB")[0], "RUB", None) != \
        calc.content_hash(calc.parse_lines([c, a], "RUB")[0], "RUB", None)
    assert calc.content_hash(calc.parse_lines([a], "RUB")[0], "EUR", None) != h1
    assert calc.content_hash(calc.parse_lines([a], "RUB")[0], "RUB", "прим.") != h1


def test_diff_by_line_id():
    keep, change, drop = L(name="Шкаф"), L(name="Кабель", qty="10"), L(name="Клеммы")
    base = [ln.to_json() for ln in calc.parse_lines([keep, change, drop], "RUB")[0]]
    renamed = dict(change, name="Кабель ВВГ", qty="12")
    new = [ln.to_json() for ln in calc.parse_lines([keep, renamed, L(name="Реле", price=None)], "RUB")[0]]
    d = calc.diff(base, new, "RUB", "RUB")
    assert (d["added"], d["removed"], d["changed"], d["unchanged"]) == (1, 1, 1, 1)
    ch = next(r for r in d["rows"] if r["kind"] == "changed")
    assert {c["field"] for c in ch["changes"]} == {"name", "qty", "amount"}
    # В новом наборе есть позиция без цены — разница итогов неизвестна, а не ноль.
    assert d["total_delta"] is None


def test_diff_total_delta_and_first_freeze():
    a = L(qty="1", price="100")
    base = [ln.to_json() for ln in calc.parse_lines([a], "RUB")[0]]
    new = [ln.to_json() for ln in calc.parse_lines([dict(a, unit_price="150")], "RUB")[0]]
    assert calc.diff(base, new, "RUB", "RUB")["total_delta"] == "50.0000"
    assert calc.diff(base, new, "RUB", "EUR")["total_delta"] is None
    assert calc.diff(base, new, "RUB", "EUR")["currency_changed"] is True
    first = calc.diff(None, new, None, "RUB")
    assert first["added"] == 1 and first["total_delta"] == "150.0000" and not first["currency_changed"]
