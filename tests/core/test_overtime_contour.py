"""D-74: ТК-контур сверхурочных — чистые правила vector.core.overtime.

* основание — только из справочника ст. 99/113 ТК РФ и только для своего вида работы;
* запрещающие категории (несовершеннолетний, беременная) — отказ всегда, при любом основании;
* основания части 2 и согласительные категории (инвалид, ребёнок до 3 лет) требуют отметок о согласии
  и о разъяснении права отказа; чрезвычайные основания части 3 согласия не требуют;
* лимиты ст. 99 ч. 6: 4 часа в день, 4 часа за два дня подряд, 120 часов в год (константы).
"""
import pytest

from vector.core import overtime as ot
from vector.core.overtime import OvertimeError


def raises(fn, code):
    with pytest.raises(OvertimeError) as e:
        fn()
    return e.value.code == code


def test_ground_must_come_from_the_statutory_catalog():
    assert raises(lambda: ot.validate_request("overtime", None, [], False, False), "ground_required")
    assert raises(lambda: ot.validate_request("overtime", "because", [], False, False), "ground_required")
    assert raises(lambda: ot.validate_request("overtime", "", [], False, False), "ground_required")


def test_ground_must_match_the_work_kind():
    # «Закончить начатую работу» — только сверхурочно; «по согласию» — только выходной/праздник.
    assert raises(lambda: ot.validate_request("weekend", "finish_urgent", [], True, False), "ground_kind_mismatch")
    assert raises(lambda: ot.validate_request("holiday", "repair_mechanisms", [], True, False), "ground_kind_mismatch")
    assert raises(lambda: ot.validate_request("overtime", "consent_employee", [], True, False), "ground_kind_mismatch")
    # Чрезвычайные основания подходят всем видам.
    for kind in ("overtime", "weekend", "holiday"):
        ot.validate_request(kind, "disaster_prevention", [], False, False)


def test_consent_grounds_require_the_consent_flag():
    assert raises(lambda: ot.validate_request("overtime", "finish_urgent", [], False, False), "consent_required")
    ot.validate_request("overtime", "finish_urgent", [], True, False)
    assert raises(lambda: ot.validate_request("weekend", "consent_employee", [], False, False), "consent_required")
    ot.validate_request("weekend", "consent_employee", [], True, False)
    # Часть 3 — без согласия.
    ot.validate_request("overtime", "emergency_state", [], False, False)


def test_forbidden_categories_cannot_be_engaged_at_all():
    for cat in ("minor", "pregnant"):
        for kind in ("overtime", "weekend", "holiday"):
            for ground in ("finish_urgent", "consent_employee", "disaster_prevention", "emergency_state"):
                if kind not in ot.GROUNDS_BY_CODE[ground]["kinds"]:
                    continue
                assert raises(lambda: ot.validate_request(kind, ground, [cat], True, True), "category_forbidden")


def test_consent_categories_need_consent_and_refusal_notice():
    for cat in ("disabled", "parent_child_under3"):
        # даже на чрезвычайном основании — письменное согласие и разъяснение права отказа
        assert raises(lambda: ot.validate_request("overtime", "disaster_prevention", [cat], False, False),
                      "consent_required")
        assert raises(lambda: ot.validate_request("overtime", "disaster_prevention", [cat], True, False),
                      "refusal_notice_required")
        ot.validate_request("overtime", "disaster_prevention", [cat], True, True)
        ot.validate_request("holiday", "consent_employee", [cat], True, True)


def test_categories_parse_unique_known_in_catalog_order():
    assert ot.parse_categories(None) == [] and ot.parse_categories([]) == []
    assert ot.parse_categories(["disabled", "minor", "disabled"]) == ["minor", "disabled"]
    assert raises(lambda: ot.parse_categories("minor"), "invalid_categories")
    assert raises(lambda: ot.parse_categories([1]), "invalid_categories")
    assert raises(lambda: ot.parse_categories(["vip"]), "unknown_category")


def test_requirements_drive_the_form():
    r = ot.requirements("overtime", "finish_urgent", [])
    assert (r["ground_known"], r["ground_allowed"], r["forbidden"], r["consent_required"],
            r["refusal_notice_required"]) == (True, True, [], True, False)
    r = ot.requirements("overtime", "disaster_prevention", ["disabled"])
    assert (r["consent_required"], r["refusal_notice_required"]) == (True, True)
    r = ot.requirements("overtime", "consent_employee", [])
    assert r["ground_allowed"] is False
    assert ot.requirements("overtime", "emergency_state", ["minor"])["forbidden"] == ["minor"]


def test_catalogs_and_limits():
    assert [g["code"] for g in ot.grounds_for("overtime")] == [
        "finish_urgent", "repair_mechanisms", "continuous_no_show",
        "disaster_prevention", "public_infra", "emergency_state"]
    assert [g["code"] for g in ot.grounds_for("weekend")] == [
        "consent_employee", "disaster_prevention", "public_infra", "emergency_state"]
    assert ot.category_labels(["parent_child_under3"]) == ["Работник с ребёнком до трёх лет"]
    assert ot.category_labels(["old_code"]) == []
    assert (ot.DAY_LIMIT_MIN, ot.TWO_DAY_LIMIT_MIN, ot.YEAR_LIMIT_MIN) == (240, 240, 7200)
