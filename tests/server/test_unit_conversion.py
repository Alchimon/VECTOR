"""Справочник аналогов и пересчёта единиц (D-25, A-27) и его использование при сопоставлении потребности.

Стенд — как в test_need_matching: wh1 — склад, pr1 — закупка, позиция st.item «КВВГ-4x1.5» в метрах. Потребность:
кабель в метрах (авто), тот же кабель в километрах (единица не совпала), шкаф без номенклатуры, строка без кода.

Что проверяется:
* обозначения: уникальны, не совпадают с кодом позиции, по ним need.generate сопоставляет автоматически —
  но только если совпадает единица;
* пересчёт: «1 from = factor to», factor > 0 и ≤ 4 знаков, общий или для позиции, пара уникальна;
* ручное решение с пересчётом: клиент подтверждает показанный коэффициент; изменённый справочник → отказ;
  неточный результат → отказ без округления; коэффициент фиксируется в решении и не меняется задним числом;
* права: склад и закупка ведут справочник (A-27), остальные — нет.
"""
import json

import pytest
import sqlalchemy as sa

from conftest import new_id
from test_need_matching import match, nd  # noqa: F401  (фикстура nd)
from test_stock import _freeze, calc_line, receive, st  # noqa: F401  (фикстура st)
from test_tasks_documents import audit, proj  # noqa: F401  (фикстура proj)
from vector.server import db


def conv(s, fu, tu, factor, item=None, **kw):
    """unit_conversion.create «1 fu = factor tu» (общий или для позиции item)."""
    p = {"id": new_id(), "from_unit": fu, "to_unit": tu, "factor": factor, **kw}
    if item:
        p["item_id"] = item
    return s.cmd("unit_conversion.create", p)


# ------------------------------------------------------------------ обозначения (аналоги)
def test_alias_rules_and_rights(world, st):
    a = st.wh.ok("item_alias.create", {"id": new_id(), "item_id": st.item, "alias": "КВВГ 4х1,5", "note": "так пишут в ТКП"})
    assert a["item_alias"]["alias"] == "КВВГ 4х1,5" and a["item_alias"]["version"] == 1
    # Закупка тоже ведёт справочник (A-27); проектные роли и администратор — нет.
    assert st.pr.cmd("item_alias.create", {"id": new_id(), "item_id": st.item, "alias": "KVVG-4x1.5"})["status"] == "accepted"
    for s in (st.proj.gip, st.proj.rp, world.admin):
        assert s.cmd("item_alias.create", {"id": new_id(), "item_id": st.item, "alias": "Z1"})["status"] == "denied"
    # Обозначение уникально и не может быть чужим кодом.
    r = st.wh.cmd("item_alias.create", {"id": new_id(), "item_id": st.item, "alias": "КВВГ 4х1,5"})
    assert r["code"] == "alias_exists" and "КВВГ-4x1.5" in r["message_ru"]
    assert st.wh.cmd("item_alias.create", {"id": new_id(), "item_id": st.item, "alias": "КВВГ-4x1.5"})["code"] == "alias_is_code"
    assert st.wh.cmd("item_alias.create", {"id": new_id(), "item_id": st.item, "alias": "   "})["code"] == "field_required"
    assert st.wh.cmd("item_alias.create", {"id": new_id(), "item_id": new_id(), "alias": "Q"})["code"] == "not_found"
    # Правка: версия обязательна, позицию сменить нельзя (поле не принимается), «без изменений» — отказ.
    aid = a["item_alias"]["id"]
    assert st.wh.cmd("item_alias.update", {"id": aid, "active": False})["code"] == "expected_version_required"
    assert st.wh.cmd("item_alias.update", {"id": aid, "item_id": st.item}, expected_version=1)["code"] == "unknown_fields"
    assert st.wh.cmd("item_alias.update", {"id": aid, "active": True}, expected_version=1)["code"] == "no_changes"
    off = st.wh.ok("item_alias.update", {"id": aid, "active": False}, expected_version=1)["item_alias"]
    assert (off["active"], off["version"]) == (False, 2)
    assert st.wh.cmd("item_alias.update", {"id": aid, "note": "x"}, expected_version=1)["status"] == "conflict"
    log = [json.loads(x["details_json"]) for x in audit(world, otype="item_alias", oid=aid)]
    assert log[0]["alias"] == "КВВГ 4х1,5" and log[1] == {"before": {"active": True}, "after": {"active": False}}
    # pull: справочник виден тем, кто видит склад; ГИП — нет (пустой список, а не ошибка).
    assert {x["alias"] for x in st.wh.query("pull")["result"]["item_aliases"]} == {"КВВГ 4х1,5", "KVVG-4x1.5"}
    assert st.proj.gip.query("pull")["result"]["item_aliases"] == []


def test_need_generate_matches_by_alias_only_when_unit_matches(st):
    cab = new_id()
    st.wh.ok("item.create", {"id": cab, "code": "ШК-1-И", "name": "Шкаф ШК-1 (исп. 2)", "unit": "шт"})
    st.wh.ok("item_alias.create", {"id": new_id(), "item_id": cab, "alias": "ШК-1"})
    st.wh.ok("item_alias.create", {"id": new_id(), "item_id": cab, "alias": "ШК-2"})
    st.wh.ok("item_alias.create", {"id": new_id(), "item_id": st.item, "alias": "Кабель-К"})
    off = st.wh.ok("item_alias.create", {"id": new_id(), "item_id": cab, "alias": "ШК-3"})["item_alias"]
    st.wh.ok("item_alias.update", {"id": off["id"], "active": False}, expected_version=1)
    ver = _freeze(st.proj, [calc_line("Шкаф", "ШК-1", "шт", "1"), calc_line("Шкаф м", "ШК-2", "м", "1"),
                            calc_line("Кабель", "Кабель-К", "м", "5"), calc_line("Шкаф старый", "ШК-3", "шт", "1")])
    n = st.pr.ok("need.generate", {"id": new_id(), "calc_version_id": ver["id"]})["need"]
    l0, l1, l2, l3 = n["lines"]
    assert (l0["item_id"], l0["match_source"]) == (cab, "alias")
    assert "ШК-1" in l0["auto_match_note"] and "ШК-1-И" in l0["auto_match_note"]
    assert (l1["item_id"], l1["match_source"]) == (None, None)            # единица не совпала — решает человек
    assert (l2["item_id"], l2["match_source"]) == (st.item, "alias")
    assert l3["item_id"] is None                                          # отключённое обозначение не работает
    assert n["matched_count"] == 2


# ------------------------------------------------------------------ пересчёт единиц
def test_conversion_rules_and_rights(world, st):
    g = conv(st.wh, "км", "м", "1000", note="метрическая")
    assert g["status"] == "accepted", g
    c = g["result"]["unit_conversion"]
    assert (c["item_id"], c["factor"], c["version"]) == (None, "1000.0000", 1)
    assert conv(st.pr, "бухта", "м", "100", item=st.item)["status"] == "accepted"        # закупка тоже может
    for s in (st.proj.gip, world.admin):
        assert conv(s, "т", "кг", "1000")["status"] == "denied"
    assert conv(st.wh, "км", "м", "999")["code"] == "conversion_exists"                  # пара в общей области
    assert conv(st.wh, "км", "м", "1000", item=st.item)["status"] == "accepted"          # для позиции — отдельно
    assert conv(st.wh, "м", "м", "1")["code"] == "units_same"
    assert conv(st.wh, "т", "кг", "0")["code"] == "qty_not_positive"
    assert conv(st.wh, "т", "кг", "-1")["code"] == "qty_not_positive"
    assert conv(st.wh, "т", "кг", "0.33333")["code"] == "too_many_fraction_digits"
    assert conv(st.wh, "т", "кг", 1000)["status"] == "rejected"                          # число, не строка — нет
    assert conv(st.wh, "катушка", "шт", "5", item=st.item)["code"] == "to_unit_mismatch"  # позиция в метрах
    assert conv(st.wh, "", "кг", "1")["code"] == "field_required"
    # Правка: только коэффициент/примечание/активность, с версией и аудитом «было/стало».
    assert st.wh.cmd("unit_conversion.update", {"id": c["id"], "to_unit": "см"}, expected_version=1)["code"] == "unknown_fields"
    n = st.wh.ok("unit_conversion.update", {"id": c["id"], "factor": "1000.5"}, expected_version=1)["unit_conversion"]
    assert (n["factor"], n["version"]) == ("1000.5000", 2)
    assert st.wh.cmd("unit_conversion.update", {"id": c["id"], "factor": "1000.5"}, expected_version=2)["code"] == "no_changes"
    log = [json.loads(x["details_json"]) for x in audit(world, otype="unit_conversion", oid=c["id"])]
    assert log[-1] == {"before": {"factor": "1000.0000"}, "after": {"factor": "1000.5000"}}
    assert len(st.wh.query("pull")["result"]["unit_conversions"]) == 3


def test_match_with_conversion_fixes_factor_and_drives_coverage(world, nd):
    st, need, _, _ = nd
    ln = need["lines"][1]                                     # «Кабель, км», 1 км; позиция st.item — в метрах
    assert (ln["unit"], ln["qty"], ln["item_id"]) == ("км", "1.0000", None)
    # Пересчёта нет — прежний отказ с подсказкой, где его завести.
    r = match(st.pr, need, 1, st.item)
    assert r["code"] == "unit_mismatch" and "Справочник" in r["message_ru"]
    c = conv(st.wh, "км", "м", "1000")["result"]["unit_conversion"]
    # Пересчёт есть, но пользователь его не подтвердил — сервер возвращает, что нужно подтвердить.
    r = match(st.pr, need, 1, st.item)
    assert r["code"] == "conversion_required" and r["details"]["conversion"]["factor"] == "1000.0000"
    # Показанный коэффициент устарел (справочник поменяли) — отказ, человек подтверждал другое.
    assert match(st.pr, need, 1, st.item, conversion_id=c["id"], factor="999")["code"] == "conversion_changed"
    assert match(st.pr, need, 1, st.item, conversion_id=new_id(), factor="1000")["code"] == "conversion_changed"
    ok = match(st.pr, need, 1, st.item, reason="Кабель учитываем в метрах", conversion_id=c["id"], factor="1000.0000")
    assert ok["status"] == "accepted", ok
    n = ok["result"]["need"]
    l1 = n["lines"][1]
    assert (l1["item_id"], l1["factor"], l1["item_unit"], l1["item_qty"]) == (st.item, "1000.0000", "м", "1000.0000")
    assert l1["coverage"]["required"] == "1000.0000"
    assert n["lines"][0]["factor"] is None and n["lines"][0]["item_qty"] == "120.0000"
    # Резерв под строку — в метрах, не больше 1000.
    receive(st.wh, st, "1500")
    big = st.wh.cmd("stock.reserve", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "project_id": st.proj.pid,
                                      "qty": "1001", "reason": "x", "need_id": n["id"], "need_line_id": l1["line_id"]})
    assert big["status"] == "rejected", big
    st.wh.ok("stock.reserve", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "project_id": st.proj.pid,
                               "qty": "1000", "reason": "x", "need_id": n["id"], "need_line_id": l1["line_id"]})
    # Справочник поменяли позже — решение не меняется задним числом (коэффициент зафиксирован).
    st.wh.ok("unit_conversion.update", {"id": c["id"], "factor": "1"}, expected_version=1)
    again = [x for x in st.pr.query("pull")["result"]["needs"] if x["id"] == n["id"]][0]["lines"][1]
    assert (again["factor"], again["coverage"]["required"]) == ("1000.0000", "1000.0000")
    # Аудит решения: коэффициент, единицы, пересчитанное количество.
    a = [json.loads(x["details_json"]) for x in audit(world, otype="procurement_need", oid=need["id"])
         if x["command"] == "need.match_line"][-1]
    assert (a["factor"], a["line_unit"], a["item_unit"], a["item_qty"]) == ("1000.0000", "км", "м", "1000.0000")
    # Запись о пересчёте неизменяема.
    t = db.procurement_need_line_match_units
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as cn:
            cn.execute(t.update().values(line_unit="подмена"))


def test_item_specific_conversion_wins_and_same_unit_refuses_factor(nd):
    st, need, cab, _ = nd
    conv(st.wh, "км", "м", "1000")
    spec = conv(st.wh, "км", "м", "1000.5", item=st.item)["result"]["unit_conversion"]
    r = match(st.pr, need, 1, st.item)
    assert r["details"]["conversion"]["id"] == spec["id"]                   # для позиции — важнее общего
    assert match(st.pr, need, 2, cab, conversion_id=spec["id"], factor="1")["code"] == "conversion_not_needed"
    # Отключённый пересчёт не используется: остаётся общий.
    st.wh.ok("unit_conversion.update", {"id": spec["id"], "active": False}, expected_version=1)
    assert match(st.pr, need, 1, st.item)["details"]["conversion"]["item_id"] is None


def test_inexact_conversion_is_refused_not_rounded(st):
    pack = new_id()
    st.wh.ok("item.create", {"id": pack, "code": "УП-1", "name": "Упаковка", "unit": "упак"})
    c = conv(st.wh, "шт", "упак", "0.0001")["result"]["unit_conversion"]
    ver = _freeze(st.proj, [calc_line("Метизы", "М-1", "шт", "0.5")])
    need = st.pr.ok("need.generate", {"id": new_id(), "calc_version_id": ver["id"]})["need"]
    r = match(st.pr, need, 0, pack, conversion_id=c["id"], factor="0.0001")
    assert r["code"] == "conversion_inexact" and r["details"]["exact"] == "0.00005"
