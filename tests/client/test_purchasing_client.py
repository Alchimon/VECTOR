"""Заказы поставщикам на клиенте (Class A, SQLite; D-32, D-33): черновик офлайн через очередь с цепочкой версий,
согласование и размещение только онлайн, договоры в кэше, представления для интерфейса (подписи, итоги, НДС,
согласование, права-подсказки).
"""
import uuid

from conftest import new_id
from test_offline_sync import BACKENDS, FAST_KDF, SERVER, mk  # noqa: F401  (фикстура mk и общие константы)
from vector.client import purchasing_view as pv

_ = (BACKENDS, FAST_KDF, SERVER)


def _setup(world, mk):
    """Закупщик pr1, кладовщик wh1, поставщик и позиция номенклатуры; клиент закупщика."""
    world.make_user("pr1", "procurement")
    world.make_user("wh1", "warehouse")
    world.make_user("ap1", "po_approver")
    world.clock.advance(seconds=61)               # входы при заведении учёток не должны съесть лимит входов
    wh, pr = world.login("wh1"), world.login("pr1")
    item, sup = new_id(), new_id()
    wh.ok("item.create", {"id": item, "code": "ХМТ-200", "name": "Хомут", "unit": "шт"})
    pr.ok("supplier.create", {"id": sup, "code": "ЭЛС", "name": "Электроснаб"})
    c = mk("pr1")
    c.pull()
    return c, item, sup


def _payload(oid, sup, item, qty="10", price="2.5", **kw):
    """Черновик в форме, которую присылает редактор."""
    return {"id": oid, "supplier_id": sup, "currency": "RUB", "supplier_ref": None, "expected_on": None, "note": None,
            "vat_mode": kw.pop("vat_mode", "included"), "contract_id": kw.pop("contract_id", None),
            "lines": [{"line_id": kw.pop("line_id", str(uuid.uuid4())), "item_id": item, "qty": qty, "unit_price": price,
                       "vat_rate": kw.pop("vat_rate", "22")}], **kw}


def test_offline_draft_saves_are_chained_then_place_online(world, mk):
    c, item, sup = _setup(world, mk)
    oid, lid = new_id(), str(uuid.uuid4())
    c.t.online = False
    assert c.save_po_draft(_payload(oid, sup, item, line_id=lid)).status == "queued"            # po.create
    assert c.save_po_draft(_payload(oid, sup, item, qty="12", line_id=lid)).status == "queued"  # po.update ev=1
    # Без изменений — в очередь не ставится (иначе сервер ответил бы «нет изменений» и цепочка разошлась бы).
    assert c.save_po_draft(_payload(oid, sup, item, qty="12.0", line_id=lid)).response == {"unchanged": True}
    # Режим цен и ставка НДС — тоже содержание черновика (D-33): их смена — правка.
    assert c.save_po_draft(_payload(oid, sup, item, qty="12", line_id=lid, vat_mode="excluded")).status == "queued"
    assert c.save_po_draft(_payload(oid, sup, item, qty="12", line_id=lid)).status == "queued"
    assert [x["command"] for x in c.queue()] == ["po.create", "po.update", "po.update", "po.update"]
    evs = [r[0] for r in c.db.execute("SELECT expected_version FROM outbox ORDER BY seq")]
    assert evs == [None, 1, 2, 3]                 # правка основана на версии, которую даст создание
    # Отправка на согласование без связи не выполняется и в очередь не ставится (сначала очередь — blocked).
    assert c.execute("po.submit", {"id": oid}, 4).status == "blocked" and len(c.queue()) == 4
    c.t.online = True
    rep = c.sync()
    assert (rep.accepted, rep.stopped_on) == (4, None)
    o = c.get_local("purchase_order", oid)
    assert (o["version"], o["order_no"], o["total"], o["_pending"]) == (4, 1, "30.0000", False)
    assert o["vat_totals"]["vat"] == "5.4100"                 # 30 с НДС 22 %: 30 × 22 / 122 = 5,409… → 5,41
    # Без согласования разместить нельзя (D-33); отправили — согласует другой человек — размещаем.
    assert c.execute("po.place", {"id": oid}, 4).response["code"] == "approval_required"
    assert c.execute("po.submit", {"id": oid}, 4).status == "accepted"
    assert c.get_local("purchase_order", oid)["approval"]["state"] == "submit"
    world.login("ap1", addr="10.0.0.9").ok("po.approve", {"id": oid}, expected_version=5)
    c.pull()
    o = c.get_local("purchase_order", oid)
    assert (o["approval"]["state"], o["version"]) == ("approve", 6)
    r = c.execute("po.place", {"id": oid}, 6)
    assert r.status == "accepted" and c.get_local("purchase_order", oid)["status"] == "placed"


def test_contract_and_requisites_offline_then_synced(world, mk):
    """Договор и реквизиты поставщика правятся без связи (очередь), после отправки — в кэше с ответа сервера."""
    c, item, sup = _setup(world, mk)
    c.t.online = False
    cid = new_id()
    assert c.execute("contract.create", {"id": cid, "supplier_id": sup, "number": "П-7", "signed_on": "2026-02-01"}).status == "queued"
    assert c.get_local("supplier_contract", cid)["number"] == "П-7"            # видно сразу, без связи
    assert c.execute("supplier.update", {"id": sup, "bik": "040000001", "bank_account": "40702810700000000101"},
                     1).status == "queued"
    c.t.online = True
    assert c.sync().accepted == 2
    k = c.get_local("supplier_contract", cid)
    assert (k["version"], k["active"], k["valid_until"], k["_pending"]) == (1, True, None, False)
    s = c.get_local("supplier", sup)
    assert (s["bik"], s["requisites_complete"]) == ("040000001", False)        # нет ИНН — «не заполнены»
    # Заказ по договору: черновик офлайн, номер договора виден в карточке.
    oid = new_id()
    assert c.save_po_draft(_payload(oid, sup, item, contract_id=cid)).status == "accepted"
    v = pv.orders_view(c.list_local("purchase_order"), c.list_local("supplier"), c.list_local("stock_item"), [],
                       ["procurement"], True, "", contracts=c.list_local("supplier_contract"))
    assert v["orders"][0]["contract_label"] == "№ П-7 от 01.02.2026, бессрочный"
    assert [x["label"] for x in v["contracts"]] == ["№ П-7 от 01.02.2026, бессрочный"]


I1, S1, L1, L2 = (str(uuid.uuid4()) for _ in range(4))


def test_orders_view_recomputes_pending_draft_and_hints():
    items = [{"id": I1, "code": "ХМТ-200", "name": "Хомут", "unit": "шт", "active": True}]
    sups = [{"id": S1, "code": "ЭЛС", "name": "Электроснаб", "active": True}]
    # Черновик, созданный офлайн: в кэше — форма запроса (без номера, без названий и итогов).
    draft = {"id": "o1", "supplier_id": S1, "currency": "RUB", "lines": [
        {"line_id": L1, "item_id": I1, "qty": "2000", "unit_price": "1.5"},
        {"line_id": L2, "item_id": I1, "qty": "5", "unit_price": None}], "_pending": True}
    placed = {"id": "o2", "order_no": 7, "supplier_id": S1, "status": "placed", "currency": "RUB", "version": 3,
              "hash_ok": False, "lines": [{"line_id": L1, "item_id": I1, "qty": "10.0000", "unit_price": "3.0000",
                                           "received": "4.0000"}]}
    v = pv.orders_view([placed, draft], sups, items, [], ["procurement"], True, "2026-11-16T10:00:00Z")
    d, p = v["orders"]
    assert (d["title"], d["pending"], d["can_edit"], d["can_place"]) == ("Новый заказ (не отправлен)", True, True, False)
    assert d["total_label"] == "3 000,00 RUB · 1 позиция без цены (итог неполный)"
    assert d["lines"][0]["item_label"] == "ХМТ-200 · Хомут" and d["lines"][1]["price_label"] == "неизвестна"
    assert (p["title"], p["delivery_label"], p["can_close"], p["can_cancel"], p["close_needs_reason"]) == \
        ("Заказ № 7", "Поставлен частично", True, False, True)
    assert p["lines"][0]["remaining_label"] == "6 шт" and p["hash_warning"] != ""
    # Склад: цен нет, принять можно; без связи — нельзя.
    w = pv.orders_view([placed], sups, items, [], ["warehouse"], True, "")["orders"][0]
    assert (w["total_label"], w["lines"][0]["price_label"], w["can_receive"]) == \
        ("цены видят закупка, бухгалтерия и согласующий", "", True)
    assert pv.orders_view([placed], sups, items, [], ["warehouse"], False, "")["orders"][0]["can_receive"] is False
    # Фильтр «архив» и текст пустого списка.
    assert pv.orders_view([placed], sups, items, [], ["procurement"], True, "", "closed")["empty_text"] == "Под этот фильтр заказов нет."


def test_preview_and_inn_problems():
    items = [{"id": I1, "code": "Р-1", "name": "Реле", "unit": "шт"}]
    r = pv.preview([{"line_id": str(uuid.uuid4()), "item_id": I1, "qty": "3", "unit_price": "10.12345"},
                    {"line_id": str(uuid.uuid4()), "item_id": I1, "qty": "2", "unit_price": "7"}], "RUB", items)
    assert r["valid"] is False and r["rows"][0]["problem"] and r["rows"][1]["amount_label"] == "14,00"
    assert r["total_label"].endswith("есть ошибки в строках")
    r = pv.preview([{"line_id": str(uuid.uuid4()), "item_id": I1, "qty": "3", "unit_price": "10"}], None, items)
    assert r["first_problem"].startswith("Указаны цены")
    assert pv.inn_problem("") == "" and pv.inn_problem("1234567890") != ""


def test_d33_view_vat_approval_hints_and_requisites():
    """НДС раздельно, подсказки согласования (четыре глаза), договоры и реквизиты в справочнике (D-33)."""
    from datetime import date
    items = [{"id": I1, "code": "НШВИ-1.5", "name": "Наконечник", "unit": "шт", "active": True}]
    sups = [{"id": S1, "code": "ЭЛС", "name": "Электроснаб", "active": True, "inn": "0000001011", "kpp": "000001001",
             "bik": "040000001", "bank_account": "40702810700000000101", "bank_name": "АО «Демо-Банк»", "vat_rate": "22"}]
    cons = [{"id": "c1", "supplier_id": S1, "number": "П-14", "signed_on": "2026-01-20", "valid_until": "2027-06-05", "active": True},
            {"id": "c2", "supplier_id": S1, "number": "П-1", "signed_on": "2025-01-10", "valid_until": "2025-12-31", "active": True}]
    me, other = str(uuid.uuid4()), str(uuid.uuid4())
    on_appr = {"id": "o5", "order_no": 5, "supplier_id": S1, "status": "draft", "currency": "RUB", "version": 2,
               "vat_mode": "excluded", "contract_id": "c1", "created_by": other,
               "approval": {"state": "submit", "submitted_by": other, "hash_ok": None,
                            "events": [{"kind": "submit", "actor_id": other, "at": "2026-11-16T09:00:00Z", "comment": "Срочно"}]},
               "lines": [{"line_id": L1, "item_id": I1, "qty": "500", "unit_price": "3.85", "vat_rate": "22"}]}
    today = date(2026, 11, 16)
    args = (sups, items, [], ["po_approver"], True, "")
    v = pv.orders_view([on_appr], *args, contracts=cons, people={other: "Морозов Сергей"}, me=me, today=today)
    o = v["orders"][0]
    assert (o["approval_label"], o["approval_tone"], o["can_approve"], o["can_return"], o["can_edit"]) == \
        ("На согласовании", "warning", True, True, False)
    assert (o["vat"]["net_label"], o["vat"]["vat_label"], o["vat"]["gross_label"]) == ("1 925,00 RUB", "423,50 RUB", "2 348,50 RUB")
    assert o["amount_label"] == "2 348,50 RUB с НДС"
    # Строка без цены: сумма «с НДС» не прячет неполноту итога.
    part = dict(on_appr, lines=on_appr["lines"] + [{"line_id": str(uuid.uuid4()), "item_id": I1, "qty": "1"}])
    assert pv.orders_view([part], *args, contracts=cons, me=me, today=today)["orders"][0]["amount_label"] == \
        "2 348,50 RUB с НДС · 1 позиция без цены (итог неполный)"
    # Режим цен не указан — НДС не посчитать, показывается сумма строк.
    nomode = dict(on_appr, vat_mode=None)
    assert pv.orders_view([nomode], *args, me=me, today=today)["orders"][0]["amount_label"] == "1 925,00 RUB"
    assert o["lines"][0]["vat_label"] == "НДС 22 %: 423,50" and o["contract_label"] == "№ П-14 от 20.01.2026, до 05.06.2027"
    assert o["approval_events"][0]["label"] == "На согласовании · Морозов Сергей · 2026-11-16 09:00"
    assert v["counts"]["on_approval"] == 1 and v["sees_prices"] is True
    # Истёкший договор в форму не предлагается.
    assert [c["id"] for c in v["contracts"]] == ["c1"]
    # Свой заказ согласующий не согласует (подсказка; окончательно — сервер, self_approval).
    mine = pv.orders_view([dict(on_appr, created_by=me)], *args, me=me, today=today)["orders"][0]
    assert (mine["can_approve"], mine["is_own_for_approver"]) == (False, True)
    # Закупка: на согласовании — только отозвать и уточнить справочные поля; возвращённый — снова правится.
    p = pv.orders_view([on_appr], sups, items, [], ["procurement"], True, "", today=today)["orders"][0]
    assert (p["can_edit"], p["can_edit_info"], p["can_withdraw"], p["can_submit"], p["can_place"]) == (False, True, True, False, False)
    ret = dict(on_appr, approval={"state": "return", "events": [{"kind": "return", "actor_id": me, "comment": "Нет договора"}]})
    p = pv.orders_view([ret], sups, items, [], ["procurement"], True, "", today=today)["orders"][0]
    assert (p["can_edit"], p["can_submit"], p["return_reason"]) == (True, True, "Нет договора")
    # Согласованный, но изменённый в обход программы — разместить нельзя.
    appr = dict(on_appr, approval={"state": "approve", "hash_ok": False, "events": []})
    p = pv.orders_view([appr], sups, items, [], ["procurement"], True, "", today=today)["orders"][0]
    assert (p["approval_stale"], p["can_place"]) == (True, False)
    p = pv.orders_view([dict(appr, approval={"state": "approve", "hash_ok": True})], sups, items, [], ["procurement"], True, "")
    assert p["orders"][0]["can_place"] is True
    # Без режима цен и ставки: черновик сохраняется, но к согласованию не готов; НДС — «—» с причиной.
    raw = dict(on_appr, approval=None, vat_mode=None, lines=[dict(on_appr["lines"][0], vat_rate=None)])
    p = pv.orders_view([raw], sups, items, [], ["procurement"], True, "")["orders"][0]
    assert p["can_submit"] is False and len(p["ready_problems"]) == 2
    assert (p["vat"]["gross_label"], p["vat"]["why"]) == ("—", "не указано, как даны цены (с НДС или без НДС)")
    # Фильтр «на согласовании».
    assert pv.orders_view([raw], *args, flt="approval")["empty_text"] == "Заказов на согласовании нет."
    # Справочник: реквизиты и договоры поставщика.
    s = pv.suppliers_view(sups, ["procurement"], cons, today)["suppliers"][0]
    assert (s["requisites_label"], s["vat_rate_label"], s["bank_label"]) == ("реквизиты заполнены", "22 %", "АО «Демо-Банк» · БИК 040000001")
    assert [(c["number"], c["usable"]) for c in s["contracts"]] == [("П-1", False), ("П-14", True)]
    assert s["contracts"][0]["label"].endswith("· истёк")
    # Проверка реквизитов в форме — по полям; пустые допустимы.
    assert pv.requisites_problems({}, "") == {}
    assert set(pv.requisites_problems({"kpp": "12", "bank_account": "40702810700000000101"}, "0000001011")) == {"kpp", "bank_account"}
    r = pv.preview([{"line_id": L1, "item_id": I1, "qty": "2", "unit_price": "61", "vat_rate": "22"}], "RUB", items, "included")
    assert (r["vat"]["vat_label"], r["rows"][0]["vat_label"], r["ready_problems"]) == ("22,00 RUB", "НДС 22 %: 22,00", [])
