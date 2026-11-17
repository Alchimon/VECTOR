"""Заказы поставщикам на клиенте (Class A, SQLite; D-32): черновик офлайн через очередь с цепочкой версий,
размещение только онлайн, представления для интерфейса (подписи, итоги, права-подсказки).
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
            "lines": [{"line_id": kw.pop("line_id", str(uuid.uuid4())), "item_id": item, "qty": qty, "unit_price": price}], **kw}


def test_offline_draft_saves_are_chained_then_place_online(world, mk):
    c, item, sup = _setup(world, mk)
    oid, lid = new_id(), str(uuid.uuid4())
    c.t.online = False
    assert c.save_po_draft(_payload(oid, sup, item, line_id=lid)).status == "queued"            # po.create
    assert c.save_po_draft(_payload(oid, sup, item, qty="12", line_id=lid)).status == "queued"  # po.update ev=1
    # Без изменений — в очередь не ставится (иначе сервер ответил бы «нет изменений» и цепочка разошлась бы).
    assert c.save_po_draft(_payload(oid, sup, item, qty="12.0", line_id=lid)).response == {"unchanged": True}
    assert [x["command"] for x in c.queue()] == ["po.create", "po.update"]
    evs = [r[0] for r in c.db.execute("SELECT expected_version FROM outbox ORDER BY seq")]
    assert evs == [None, 1]                       # правка основана на версии, которую даст создание
    # Размещение без связи не выполняется и в очередь не ставится (сначала нужно отправить очередь — blocked).
    assert c.execute("po.place", {"id": oid}, 2).status == "blocked" and len(c.queue()) == 2
    c.t.online = True
    rep = c.sync()
    assert (rep.accepted, rep.stopped_on) == (2, None)
    o = c.get_local("purchase_order", oid)
    assert (o["version"], o["order_no"], o["total"], o["_pending"]) == (2, 1, "30.0000", False)
    r = c.execute("po.place", {"id": oid}, 2)
    assert r.status == "accepted" and c.get_local("purchase_order", oid)["status"] == "placed"


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
    assert (w["total_label"], w["lines"][0]["price_label"], w["can_receive"]) == ("цены видят закупка и бухгалтерия", "", True)
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
