"""Гарантии расчёта НДС и правила замещения — чистые функции (D-34: ответы владельца на A-37 и A-38).

A-38: НДС по строкам с округлением до копеек оставлен, но «не должно быть возможности, чтобы ошибки расчёта дали
неверный результат». Поэтому:
* основной расчёт (Decimal, quantize) сверяется с независимым (точные дроби, целочисленное округление) — fuzz ниже;
* если расчёт сломан (подменяем line_vat), суммы не показываются и заказ не отправить (fail-closed);
* итог можно сверить со счётом поставщика; расхождение — только с причиной.
"""
import random
import uuid
from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from vector.core import delegation as dl
from vector.core import purchasing as pu
from vector.core.errors import Rejected


def lines(*specs, mode_rate="22"):
    """Строки заказа из пар (qty, price[, rate]) через parse_lines (как в сервере)."""
    raw = [{"line_id": str(uuid.uuid4()), "item_id": str(uuid.uuid4()), "qty": q, "unit_price": p,
            "vat_rate": r[0] if r else mode_rate} for q, p, *r in specs]
    return pu.parse_lines(raw, "RUB")[0]


# ====================================================================== две независимые реализации
def test_vat_two_implementations_agree_fuzz():
    rnd = random.Random(20261116)
    for _ in range(20000):
        amount = Decimal(rnd.randint(0, 10 ** 9)) / Decimal(10 ** rnd.choice((0, 2, 4)))
        rate = rnd.choice(list(pu.VAT_RATES))
        mode = rnd.choice(("included", "excluded"))
        a = pu.line_vat(amount, rate, mode)
        b = pu._vat_exact(amount, rate, mode)
        assert tuple(Fraction(x) for x in a) == b, (amount, rate, mode)
        assert a[0] + a[1] == a[2] and a[1] == a[1].quantize(Decimal("0.01"))


def test_half_kopeck_rounds_up_in_both():
    # 0,61 с НДС 22 %: 0,61 × 22/122 = 0,11 (0,1100…); 2,75 без НДС × 10 % = 0,275 → 0,28 (половина — вверх).
    assert pu.line_vat(Decimal("2.75"), "10", "excluded")[1] == Decimal("0.28")
    assert pu._vat_exact(Decimal("2.75"), "10", "excluded")[1] == Fraction(28, 100)


# ====================================================================== fail-closed
def test_broken_line_vat_hides_totals_and_blocks_submit(monkeypatch):
    ls = lines(("10", "122"), ("1", "33.33"))
    assert pu.vat_totals(ls, "included")["check_failed"] is False

    def broken(amount, rate, mode):
        """Ошибка в формуле: НДС на копейку больше (и тождество при этом соблюдено — его одного мало)."""
        net, vat, gross = real(amount, rate, mode)
        return net - Decimal("0.01"), vat + Decimal("0.01"), gross
    real = pu.line_vat
    monkeypatch.setattr(pu, "line_vat", broken)
    t = pu.vat_totals(ls, "included")
    assert (t["check_failed"], t["net"], t["vat"], t["gross"], t["by_rate"]) == (True, None, None, None, [])
    assert "calc_check_failed" in [p["code"] for p in pu.ready_problems(ls, "RUB", "included")]
    cc = pu.calc_control(ls, "included", Decimal("1253.33"), Decimal("226.01"))
    assert cc["check_failed"] is True and cc["checks"][0]["state"] == "error"
    assert cc["invoice"]["state"] == "unknown" and cc["needs_reason"] is True


def test_identity_violation_is_caught(monkeypatch):
    real = pu.line_vat
    monkeypatch.setattr(pu, "line_vat", lambda a, r, m: (lambda v: (v[0], v[1], v[2] + Decimal("0.01")))(real(a, r, m)))
    assert pu.vat_totals(lines(("1", "100")), "excluded")["check_failed"] is True


# ====================================================================== сверка со счётом и копейки
def test_invoice_check_states():
    ls = lines(("10", "122"))                                   # 1220,00 / НДС 220,00
    assert pu.invoice_check(ls, "included", None, None)["state"] == "none"
    m = pu.invoice_check(ls, "included", Decimal("1220.00"), None)
    assert (m["state"], m["diff_gross"], m["diff_vat"]) == ("match", "0.0000", None)
    mm = pu.invoice_check(ls, "included", Decimal("1220.00"), Decimal("220.01"))
    assert (mm["state"], mm["diff_vat"]) == ("mismatch", "0.0100")
    assert pu.invoice_check(ls, None, Decimal("1220"), None)["state"] == "unknown"     # режим цен не указан
    assert pu.invoice_check(lines(("1", "10"), ("2", None)), "included", Decimal("10"), None)["state"] == "unknown"
    cc = pu.calc_control(ls, "included", Decimal("1220.00"), Decimal("219.99"))
    txt = next(c["text"] for c in cc["checks"] if c["code"] == "invoice")
    assert "по счёту 219,99, по расчёту 220,00" in txt and cc["needs_reason"] is True
    assert pu.calc_control(ls, "included")["needs_reason"] is False


def test_invoice_amount_input():
    assert pu.check_invoice_amount("", "invoice_gross") is None
    assert pu.check_invoice_amount("1220.50", "invoice_gross") == Decimal("1220.50")
    for bad, code in (("-1", "negative_amount"), ("1.005", "not_kopecks")):
        with pytest.raises(pu.PurchaseError) as e:
            pu.check_invoice_amount(bad, "invoice_vat")
        assert e.value.problems[0]["code"] == code and e.value.problems[0]["field"] == "invoice_vat"


def test_kopeck_lines_warning():
    ls = lines(("333", "3.8555"), ("1", "10"), ("3", "0.3333"))
    assert pu.kopeck_lines(ls) == [1, 3]
    t = next(c for c in pu.calc_control(ls, "excluded")["checks"] if c["code"] == "kopecks")
    assert t["state"] == "warn" and t["text"].startswith("Суммы строк 1, 3")
    assert pu._ru("1283.8815") == "1 283,8815" and pu._ru("1234.5000") == "1 234,50"


def test_invoice_amounts_change_content_hash():
    ls = lines(("1", "10"))
    sid = str(uuid.uuid4())
    h0 = pu.content_hash(sid, "RUB", ls)
    assert pu.content_hash(sid, "RUB", ls, invoice_gross=Decimal("10.00")) != h0


# ====================================================================== замещение
def test_delegation_period_and_role_rules():
    today = date(2026, 11, 16)
    assert dl.check_period(None, date(2026, 11, 30), today) == (today, date(2026, 11, 30))
    for vf, vu in ((date(2026, 11, 15), date(2026, 11, 30)), (today, date(2026, 11, 10)), (today, date(2027, 11, 18))):
        with pytest.raises(Rejected):
            dl.check_period(vf, vu, today)
    with pytest.raises(Rejected) as e:
        dl.check_period(today, None, today)
    assert e.value.code == "field_required"
    with pytest.raises(Rejected) as e:
        dl.check_role("admin", ["admin", "rp"])
    assert e.value.code == "role_not_delegable"
    with pytest.raises(Rejected):
        dl.parse_day("16.11.2026", "valid_until")
    assert dl.parse_day("2026-11-30", "valid_until") == date(2026, 11, 30)
    assert dl.default_until(today) == date(2026, 11, 29)   # 14 дней включительно


@pytest.mark.parametrize("vf,vu,revoked,holder,expected", [
    ("2026-11-20", "2026-11-30", False, True, "planned"),
    ("2026-11-16", "2026-11-16", False, True, "active"),
    ("2026-11-01", "2026-11-15", False, True, "ended"),
    ("2026-11-01", "2026-11-30", True, True, "revoked"),
    ("2026-11-01", "2026-11-30", False, False, "suspended"),
])
def test_delegation_states(vf, vu, revoked, holder, expected):
    today = date(2026, 11, 16)
    st = dl.state(date.fromisoformat(vf), date.fromisoformat(vu), revoked, holder, today)
    assert st == expected and st in dl.STATE_LABELS_RU
    assert dl.is_active(date.fromisoformat(vf), date.fromisoformat(vu), revoked, today) == \
        (expected in ("active", "suspended"))
