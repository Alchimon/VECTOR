"""Согласование заказов, НДС, реквизиты и договоры поставщиков (D-33: ответы владельца на A-34…A-36; A-37…A-39).

Правила (server/purchasing.py, core/purchasing.py):
* разместить можно только согласованный заказ, и только в том виде, в каком его согласовали (SHA-256);
* согласует роль «Согласование закупок» и не тот, кто заказ создал или отправил (четыре глаза);
* пока заказ на согласовании или согласован, его условия не меняются (только справочные поля);
* НДС: режим цен заказа и ставка строки обязательны для согласования; НДС строки — до копеек, итоги — суммы строк;
* реквизиты поставщика необязательны, но проверяются; договор заказа — того же поставщика и действующий.
"""
from decimal import Decimal

import pytest

from conftest import new_id
from test_purchasing import _W, _remember_world, approved, approver, draft, line, placed, pulled_order, supplier  # noqa: F401
from test_stock import st  # noqa: F401
from test_tasks_documents import proj  # noqa: F401
from vector.core import purchasing as pu
from vector.server import db

# Вымышленные реквизиты: код региона «00» не выдаётся настоящим организациям и банкам (контрольные цифры верные).
REQ = {"inn": "0000001011", "kpp": "000001001", "ogrn": "1020000001010", "bik": "040000001",
       "bank_account": "40702810700000000101", "corr_account": "30101810100000000001",
       "bank_name": "АО «Демо-Банк»", "address": "г. Демоград"}


# ====================================================================== согласование
def test_approval_is_required_four_eyes_and_frozen(world, st):  # noqa: F811
    sup = supplier(st.pr)
    o = draft(st, sup, [line(st.item, "10", "122")])
    ap = approver()
    # Согласующий не создаёт и не размещает; закупка не согласует.
    assert ap.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "lines": []})["status"] == "denied"
    assert st.pr.cmd("po.approve", {"id": o["id"]}, expected_version=1)["status"] == "denied"
    # Согласовать можно только отправленный.
    assert ap.cmd("po.approve", {"id": o["id"]}, expected_version=1)["code"] == "order_state"
    s = st.pr.ok("po.submit", {"id": o["id"], "comment": "Срочно"}, expected_version=1)["purchase_order"]
    assert (s["approval"]["state"], s["approval"]["label"], s["version"]) == ("submit", "На согласовании", 2)
    assert st.pr.cmd("po.submit", {"id": o["id"]}, expected_version=2)["code"] == "order_state"
    # Условия на согласовании не меняются; справочные поля — можно (версия растёт).
    r = st.pr.cmd("po.update", {"id": o["id"], "lines": [line(st.item, "11", "122")]}, expected_version=2)
    assert (r["code"], r["details"]["field"]) == ("order_on_approval", "lines")
    s = st.pr.ok("po.update", {"id": o["id"], "supplier_ref": "Счёт 7"}, expected_version=2)["purchase_order"]
    # Согласующий решает по версии, которую видел.
    assert ap.cmd("po.approve", {"id": o["id"]}, expected_version=2)["status"] == "conflict"
    # Четыре глаза: тот, кто создал или отправил, не согласует, даже имея роль согласующего.
    world.admin.ok("admin.grant_role", {"user_id": st.pr.user_id, "role": "po_approver", "reason": "test"})
    assert st.pr.cmd("po.approve", {"id": o["id"]}, expected_version=3)["code"] == "self_approval"
    # Возврат — с причиной; после возврата черновик снова правится.
    assert ap.cmd("po.return", {"id": o["id"]}, expected_version=3)["code"] == "field_required"
    s = ap.ok("po.return", {"id": o["id"], "reason": "Нет договора"}, expected_version=3)["purchase_order"]
    assert (s["approval"]["state"], s["approval"]["events"][-1]["comment"]) == ("return", "Нет договора")
    assert st.pr.cmd("po.place", {"id": o["id"]}, expected_version=4)["code"] == "approval_required"
    s = st.pr.ok("po.update", {"id": o["id"], "lines": [line(st.item, "5", "122")]}, expected_version=4)["purchase_order"]
    s = st.pr.ok("po.submit", {"id": o["id"]}, expected_version=5)["purchase_order"]
    s = ap.ok("po.approve", {"id": o["id"], "comment": "Ок"}, expected_version=6)["purchase_order"]
    assert (s["approval"]["state"], s["approval"]["hash_ok"]) == ("approve", True)
    # Согласованный тоже заморожен; отозвать — и можно править, но тогда нужно новое согласование.
    assert st.pr.cmd("po.update", {"id": o["id"], "vat_mode": "excluded"}, expected_version=7)["code"] == "order_on_approval"
    s = st.pr.ok("po.withdraw", {"id": o["id"], "reason": "Поменялась цена"}, expected_version=7)["purchase_order"]
    assert s["approval"]["state"] == "withdraw"
    assert st.pr.cmd("po.withdraw", {"id": o["id"]}, expected_version=8)["code"] == "order_state"
    assert st.pr.cmd("po.place", {"id": o["id"]}, expected_version=8)["code"] == "approval_required"
    s = approved(st, s)
    p = st.pr.ok("po.place", {"id": o["id"]}, expected_version=s["version"])["purchase_order"]
    assert p["status"] == "placed" and p["hash_ok"] is True
    assert [e["kind"] for e in p["approval"]["events"]] == ["submit", "return", "submit", "approve", "withdraw",
                                                          "submit", "approve"]
    # После размещения согласование не трогают.
    assert st.pr.cmd("po.withdraw", {"id": o["id"]}, expected_version=p["version"])["code"] == "order_state"
    h = st.pr.query("history", object_type="purchase_order", object_id=o["id"])["result"]
    assert h[-1]["details"]["approved_by"] == ap.user_id


def test_tampered_content_cannot_be_approved_or_placed(world, st):  # noqa: F811
    sup = supplier(st.pr)
    o = draft(st, sup, [line(st.item, "10", "2")])
    s = st.pr.ok("po.submit", {"id": o["id"]}, expected_version=1)["purchase_order"]
    # Правка в обход программы после отправки — согласовать нельзя.
    with world.svc.engine.begin() as c:
        c.execute(db.purchase_order_lines.update().where(db.purchase_order_lines.c.order_id == o["id"]).values(qty=Decimal(99)))
    assert approver().cmd("po.approve", {"id": o["id"]}, expected_version=s["version"])["code"] == "approval_stale"
    # И после согласования — разместить нельзя.
    o2 = approved(st, draft(st, sup, [line(st.item, "1", "2")]))
    with world.svc.engine.begin() as c:
        c.execute(db.purchase_order_line_taxes.update().where(db.purchase_order_line_taxes.c.order_id == o2["id"])
                  .values(vat_rate="10"))
    assert pulled_order(st.pr, o2["id"])["approval"]["hash_ok"] is False
    assert st.pr.cmd("po.place", {"id": o2["id"]}, expected_version=o2["version"])["code"] == "approval_stale"
    # Журнал согласования неизменяем.
    with pytest.raises(Exception):
        with world.svc.engine.begin() as c:
            c.execute(db.purchase_order_approvals.delete())


def test_approver_sees_prices_and_suppliers_but_not_needs(world, st):  # noqa: F811
    sup = supplier(st.pr)
    o = placed(st, sup, [line(st.item, "10", "3")])
    pulled = approver().query("pull")["result"]
    got = next(x for x in pulled["purchase_orders"] if x["id"] == o["id"])
    assert got["total"] == "30.0000" and [x["code"] for x in pulled["suppliers"]] == ["ЭТМ"]
    assert pulled["needs"] == [] and pulled["warehouses"] == []


# ====================================================================== НДС
def test_vat_required_for_approval_and_computed_per_line(world, st):  # noqa: F811
    sup = supplier(st.pr)
    # Режим и ставка — не обязательны для черновика, но обязательны для согласования (все замечания сразу).
    o = draft(st, sup, [line(st.item, "1", "100", vat_rate=None), line(st.item, "2", None)], vat_mode=None)
    assert o["vat_totals"]["net"] is None and o["vat_totals"]["unknown_count"] == 1
    r = st.pr.cmd("po.submit", {"id": o["id"]}, expected_version=1)
    assert [p["code"] for p in r["details"]["problems"]] == ["vat_mode_required", "vat_rate_required"]
    assert st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "currency": "RUB", "vat_mode": "gross",
                                   "lines": []})["code"] == "invalid_vat_mode"
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "currency": "RUB",
                                "lines": [line(st.item, "1", "1", vat_rate="20")]})
    assert r["code"] == "invalid_vat_rate"
    # Цены с НДС: НДС выделяется (22/122) с округлением до копеек в каждой строке; итоги — суммы строк.
    o = draft(st, sup, [line(st.item, "2000", "142.6"), line(st.item, "1", "0.01"), line(st.item, "3", "10", vat_rate="none"),
                        line(st.item, "1", "110", vat_rate="10")])
    t = o["vat_totals"]
    assert (t["gross"], t["vat"], t["net"]) == ("285340.0100", "51439.5100", "233900.5000")
    assert [(b["label"], b["vat"]) for b in t["by_rate"]] == [("22 %", "51429.5100"), ("10 %", "10.0000"), ("Без НДС", "0.0000")]
    assert [(x["vat"], x["net"]) for x in o["lines"]][:2] == [("51429.5100", "233770.4900"), ("0.0000", "0.0100")]
    # Цены без НДС: НДС начисляется сверху.
    o = st.pr.ok("po.update", {"id": o["id"], "vat_mode": "excluded"}, expected_version=1)["purchase_order"]
    assert (o["vat_totals"]["net"], o["vat_totals"]["vat"], o["vat_totals"]["gross"]) == \
        ("285340.0100", "62755.0000", "348095.0100")
    # Склад НДС в деньгах не видит (ставку — видит).
    w = pulled_order(st.wh, o["id"])
    assert (w["vat_totals"], w["lines"][0]["vat"], w["lines"][0]["vat_rate"]) == (None, None, "22")


def test_vat_core_rules():
    D = Decimal
    assert pu.line_vat(D("100"), "22", "included") == (D("81.97"), D("18.03"), D("100"))
    assert pu.line_vat(D("100"), "22", "excluded") == (D("100"), D("22.00"), D("122.00"))
    assert pu.line_vat(D("0.05"), "10", "excluded")[1] == D("0.01")          # 0,005 → 0,01 (математически)
    assert pu.line_vat(D("100"), "0", "excluded") == (D("100"), D(0), D("100"))
    assert pu.line_vat(None, "22", "included") is None and pu.line_vat(D(1), None, "included") is None
    # Хэш заказа без полей D-33 совпадает с прежним (размещённые до D-33 заказы остаются «целыми»).
    ln = pu.Line("l", "i", D(1), D(2), D(2), None, None, None)
    assert pu.content_hash("s", "RUB", [ln]) == pu.content_hash("s", "RUB", [ln], None, None)
    assert pu.content_hash("s", "RUB", [ln], None, "included") != pu.content_hash("s", "RUB", [ln])


# ====================================================================== реквизиты
def test_requisites_are_checked_and_shown(world, st):  # noqa: F811
    sup = supplier(st.pr, code="ДЕМО", **REQ, vat_rate="22")
    assert (sup["kpp"], sup["bank_account"], sup["vat_rate"], sup["requisites_complete"]) == \
        ("000001001", "40702810700000000101", "22", True)
    bad = {"kpp": "12345", "ogrn": "1020000001011", "bik": "04000000", "bank_account": "40702810700000000102"}
    for k, v in bad.items():
        r = st.pr.cmd("supplier.create", {"id": new_id(), "code": f"Б-{k}", "name": "Х", **{**REQ, k: v}})
        assert r["status"] == "rejected", k
    # Счёт без БИК не проверить; у ИП нет КПП; ОГРН организации — 13 цифр.
    r = st.pr.cmd("supplier.create", {"id": new_id(), "code": "Б1", "name": "Х", "bank_account": REQ["bank_account"]})
    assert r["code"] == "bik_required"
    r = st.pr.cmd("supplier.create", {"id": new_id(), "code": "Б2", "name": "ИП", "inn": "500100732259", "kpp": "000001001"})
    assert r["code"] == "kpp_for_ip"
    # Частичная правка проверяет итог: новый БИК без нового счёта — ключ счёта не сходится.
    r = st.pr.cmd("supplier.update", {"id": sup["id"], "bik": "040000002"}, expected_version=1)
    assert r["code"] == "invalid_account"
    s = st.pr.ok("supplier.update", {"id": sup["id"], "bank_name": "АО «Другой банк»"}, expected_version=1)["supplier"]
    assert (s["bank_name"], s["bik"], s["version"]) == ("АО «Другой банк»", "040000001", 2)
    assert st.pr.cmd("supplier.update", {"id": sup["id"], "bank_name": "АО «Другой банк»"}, expected_version=2)["code"] == "no_changes"
    h = st.pr.query("history", object_type="supplier", object_id=sup["id"])["result"]
    assert h[-1]["details"] == {"before": {"bank_name": "АО «Демо-Банк»"}, "after": {"bank_name": "АО «Другой банк»"}}
    assert supplier(st.pr, code="ПУСТО")["requisites_complete"] is False


# ====================================================================== договоры
def test_contracts_belong_to_supplier_and_must_be_valid(world, st):  # noqa: F811
    sup, other = supplier(st.pr), supplier(st.pr, code="ДРУГОЙ")
    cid = new_id()
    c = st.pr.ok("contract.create", {"id": cid, "supplier_id": sup["id"], "number": "П-1", "signed_on": "2026-01-10",
                                     "valid_until": "2026-12-31", "subject": "Кабель"})["supplier_contract"]
    assert (c["number"], c["valid_until"], c["active"], c["version"]) == ("П-1", "2026-12-31", True, 1)
    assert st.pr.cmd("contract.create", {"id": new_id(), "supplier_id": sup["id"], "number": "П-1",
                                         "signed_on": "2026-01-10"})["code"] == "contract_exists"
    assert st.pr.cmd("contract.create", {"id": new_id(), "supplier_id": sup["id"], "number": "П-2", "signed_on": "2026-05-01",
                                         "valid_until": "2026-04-01"})["code"] == "invalid_period"
    assert st.wh.cmd("contract.create", {"id": new_id(), "supplier_id": sup["id"], "number": "П-3",
                                         "signed_on": "2026-01-10"})["status"] == "denied"
    # Договор — только своего поставщика (и при смене поставщика в черновике).
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": other["id"], "contract_id": cid, "lines": []})
    assert r["code"] == "contract_supplier_mismatch"
    o = draft(st, sup, [line(st.item, "1", "10")], contract_id=cid)
    assert (o["contract_number"], o["contract_valid_until"]) == ("П-1", "2026-12-31")
    assert st.pr.cmd("po.update", {"id": o["id"], "supplier_id": other["id"]}, expected_version=1)["code"] == \
        "contract_supplier_mismatch"
    # Истёк — не согласовать; продлили — можно; отключили после согласования — не разместить.
    st.pr.ok("contract.update", {"id": cid, "valid_until": "2026-11-01"}, expected_version=1)
    assert st.pr.cmd("po.submit", {"id": o["id"]}, expected_version=1)["code"] == "contract_not_valid"
    st.pr.ok("contract.update", {"id": cid, "valid_until": None}, expected_version=2)
    o = approved(st, o)
    st.pr.ok("contract.update", {"id": cid, "active": False}, expected_version=3)
    assert st.pr.cmd("po.place", {"id": o["id"]}, expected_version=o["version"])["code"] == "contract_inactive"
    # Договоры видят те, кто видит справочник поставщиков; история — по supplier.read.
    pulled = st.wh.query("pull")["result"]["supplier_contracts"]
    assert [(x["number"], x["active"]) for x in pulled] == [("П-1", False)]
    h = st.pr.query("history", object_type="supplier_contract", object_id=cid)["result"]
    assert [x["command"] for x in h] == ["contract.create", "contract.update", "contract.update", "contract.update"]
    assert h[1]["details"] == {"before": {"valid_until": "2026-12-31"}, "after": {"valid_until": "2026-11-01"}}
