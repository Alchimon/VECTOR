"""Правила заказов поставщикам (vector.core.purchasing, D-32): строки, валюта, точные суммы, ИНН, состояние поставки."""
import uuid
from decimal import Decimal

import pytest

from vector.core import purchasing as pu


def ln(**kw):
    """Строка заказа с новыми идентификаторами и количеством 1."""
    return {"line_id": str(uuid.uuid4()), "item_id": str(uuid.uuid4()), "qty": "1", **kw}


def codes(e):
    """Коды всех замечаний ошибки."""
    return [p["code"] for p in e.value.problems]


def test_lines_prices_and_currency():
    lines, cur = pu.parse_lines([ln(qty="2.5", unit_price="100.10"), ln(unit_price=None), ln(unit_price="")], "RUB")
    assert cur == "RUB" and lines[0].amount == Decimal("250.25") and lines[1].amount is None and lines[2].unit_price is None
    t = pu.totals(lines)
    assert (t["total"], t["unpriced_count"], t["complete"]) == ("250.2500", 2, False)
    assert pu.totals([])["total"] is None                                    # нет цен — сумма неизвестна, не 0
    assert pu.parse_lines([ln(unit_price="0")], "USD")[0][0].amount == Decimal("0")
    with pytest.raises(pu.PurchaseError) as e:                               # цены без валюты
        pu.parse_lines([ln(unit_price="1")], None)
    assert codes(e) == ["currency_required"]
    with pytest.raises(pu.PurchaseError) as e:
        pu.parse_lines([], "rub")
    assert codes(e) == ["invalid_currency"]
    assert pu.parse_lines([ln()], None)[1] is None                           # без цен валюта не нужна


def test_line_errors_are_collected():
    lid = str(uuid.uuid4())
    with pytest.raises(pu.PurchaseError) as e:
        pu.parse_lines([ln(line_id=lid, qty="0"), ln(line_id=lid, unit_price="-1"), ln(qty="1.00001"),
                        ln(qty="0.0001", unit_price="0.0001"), ln(need_id=str(uuid.uuid4())), ln(item_id="x"),
                        ln(extra=1), {"line_id": str(uuid.uuid4()), "item_id": str(uuid.uuid4())}], "RUB")
    assert codes(e) == ["qty_not_positive", "duplicate_line", "negative_price", "too_many_fraction_digits",
                        "amount_needs_rounding", "need_pair", "invalid_id", "unknown_field", "required"]
    assert str(e.value).startswith("Строка 1: Количество должно быть больше нуля")
    with pytest.raises(pu.PurchaseError) as e:
        pu.parse_lines([], None, require_lines=True)
    assert codes(e) == ["no_lines"]
    with pytest.raises(pu.PurchaseError):
        pu.parse_lines([ln(qty=1.5)], None)                                  # float запрещён


def test_hash_and_delivery_state():
    a, _ = pu.parse_lines([ln(qty="2")], None)
    sid = str(uuid.uuid4())
    assert pu.content_hash(sid, None, a) == pu.content_hash(sid, None, a)
    assert pu.content_hash(sid, "RUB", a) != pu.content_hash(sid, None, a)
    assert pu.remaining(Decimal("5"), Decimal("7")) == 0 and pu.remaining(Decimal("5"), Decimal("2")) == 3
    assert pu.delivery_state([(Decimal(5), Decimal(0))]) == "none"
    assert pu.delivery_state([(Decimal(5), Decimal(5)), (Decimal(1), Decimal(0))]) == "partial"
    assert pu.delivery_state([(Decimal(5), Decimal(5)), (Decimal(1), Decimal(1))]) == "full"


def test_inn_checksum():
    assert pu.check_inn("7707083893") == "7707083893"            # 10 цифр, контрольная верна
    assert pu.check_inn(" 500100732259 ") == "500100732259"      # 12 цифр
    assert pu.check_inn("") is None and pu.check_inn(None) is None
    for bad in ("7707083894", "50010073225", "500100732258", "77070838٩3", "abcdefghij", 7707083893):
        with pytest.raises(pu.PurchaseError):
            pu.check_inn(bad)
