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


# ---------------------------------------------------------------- D-33: НДС, готовность к согласованию, реквизиты
def test_vat_totals_by_rate_and_unknown():
    """Итоги НДС — суммы строк (A-38); без режима или ставки — неизвестно (None), а не меньше."""
    D = Decimal
    ln = lambda amt, rate: pu.Line(str(amt), "i", D(1), amt, amt, None, None, None, rate)  # noqa: E731
    lines = [ln(D("100"), "22"), ln(D("110"), "10"), ln(D("50"), "none"), ln(None, None)]
    t = pu.vat_totals(lines, "included")
    assert (t["net"], t["vat"], t["gross"], t["unknown_count"]) == ("231.9700", "28.0300", "260.0000", 0)
    assert [(b["rate"], b["vat"]) for b in t["by_rate"]] == [("22", "18.0300"), ("10", "10.0000"), ("none", "0.0000")]
    assert pu.vat_totals(lines, None)["net"] is None
    t = pu.vat_totals(lines + [ln(D("1"), None)], "excluded")
    assert (t["gross"], t["unknown_count"]) == (None, 1)
    assert pu.vat_totals([ln(None, "22")], "included")["gross"] is None          # нет цен — нет итога
    with pytest.raises(pu.PurchaseError):
        pu.check_vat_mode("gross")
    assert pu.check_vat_mode("") is None


def test_ready_problems_for_approval():
    """Чего не хватает для согласования: строки, валюта, режим цен, ставки строк с ценой (все замечания сразу)."""
    D = Decimal
    assert [p["code"] for p in pu.ready_problems([], None, None)] == ["no_lines"]
    priced = pu.Line("a", "i", D(1), D(5), D(5), None, None, None, None)
    unpriced = pu.Line("b", "i", D(1), None, None, None, None, None, None)
    assert [(p["code"], p["line"]) for p in pu.ready_problems([unpriced, priced], None, None)] == \
        [("currency_required", None), ("vat_mode_required", None), ("vat_rate_required", 2)]
    assert pu.ready_problems([unpriced], None, None) == []                      # без цен НДС не нужен
    lines, _ = pu.parse_lines([{"line_id": str(uuid.uuid4()), "item_id": str(uuid.uuid4()), "qty": "1",
                                "unit_price": "5", "vat_rate": "10"}], "RUB")
    assert pu.ready_problems(lines, "RUB", "excluded") == []


def test_requisites_checks():
    """Реквизиты (A-39): необязательны; формат и контрольные цифры; согласованность с типом ИНН."""
    ok = {"kpp": "000001001", "ogrn": "1020000001010", "bik": "040000001", "bank_account": "4070 2810 7000 0000 0101",
          "corr_account": "30101810100000000001", "address": " г. Демоград ", "bank_name": "АО «Демо-Банк»"}
    out = pu.check_requisites(ok, "0000001011")
    assert (out["bank_account"], out["address"]) == ("40702810700000000101", "г. Демоград")   # пробелы убраны
    assert pu.check_requisites({}, None) == {"kpp": None, "ogrn": None, "bik": None, "bank_account": None,
                                             "corr_account": None, "address": None, "bank_name": None}

    def codes(p, inn=None):
        """Коды замечаний (поле, код)."""
        with pytest.raises(pu.PurchaseError) as e:
            pu.check_requisites(p, inn)
        return [(x["field"], x["code"]) for x in e.value.problems]
    assert codes({"kpp": "12AB"}) == [("kpp", "invalid_kpp")]
    assert codes({"ogrn": "1020000001011"}) == [("ogrn", "invalid_ogrn")]           # контрольная цифра
    assert codes({"bik": "04000000"}) == [("bik", "invalid_bik")]
    assert codes({"bank_account": ok["bank_account"]}) == [("bank_account", "bik_required")]
    assert codes({**ok, "corr_account": "30101810100000000002"}, "0000001011") == [("corr_account", "invalid_account")]
    assert codes({"kpp": "000001001"}, "500100732259") == [("kpp", "kpp_for_ip")]
    assert codes({"ogrn": "1020000001010"}, "500100732259") == [("ogrn", "ogrn_mismatch")]
    assert codes({"address": "x" * 501}) == [("address", "too_long")]
    # Ошибки копятся: все замечания сразу.
    assert len(codes({"kpp": "1", "ogrn": "2", "bik": "3"})) == 3
