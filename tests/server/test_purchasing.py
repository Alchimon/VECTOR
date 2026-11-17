"""Заказы поставщикам и факт поставки (сценарий С4, шаг 5; D-32, A-34..A-36).

Каждый тест закрепляет одно правило из server/purchasing.py; запускается на SQLite и на встроенном PostgreSQL.
Кто есть кто: wh1 — склад, pr1 — закупка, acc1 — бухгалтерия; команда проекта — как в test_tasks_documents.
"""
import uuid
from decimal import Decimal

import pytest

from conftest import new_id
from test_stock import _freeze, calc_line, st  # noqa: F401  (фикстура st: склад СК-1, позиция КВВГ, wh1/pr1)
from test_tasks_documents import audit, proj  # noqa: F401
from vector.server import db


def supplier(s, code="ЭТМ", **kw):
    """Создать поставщика; вернуть его представление."""
    return s.ok("supplier.create", {"id": new_id(), "code": code, "name": f"Поставщик {code}", **kw})["supplier"]


def line(item, qty, price=None, **kw):
    """Строка заказа (новый line_id)."""
    return {"line_id": str(uuid.uuid4()), "item_id": item, "qty": qty, "unit_price": price, **kw}


def draft(st, sup, lines, currency="RUB", **kw):  # noqa: F811
    """Черновик заказа от закупки; вернуть представление заказа."""
    return st.pr.ok("po.create", {"id": new_id(), "supplier_id": sup["id"], "currency": currency, "lines": lines,
                                  **kw})["purchase_order"]


def placed(st, sup, lines, **kw):  # noqa: F811
    """Черновик → размещён; вернуть представление."""
    o = draft(st, sup, lines, **kw)
    return st.pr.ok("po.place", {"id": o["id"]}, expected_version=o["version"])["purchase_order"]


def receive(st, o, ln, qty, src="ТН-77"):  # noqa: F811
    """Приём поставки по строке ln заказа o на склад СК-1 (сырой ответ)."""
    return st.wh.cmd("po.receive", {"id": new_id(), "order_id": o["id"], "line_id": ln["line_id"], "warehouse_id": st.w,
                                    "qty": qty, "source_doc": src})


def pulled_order(s, oid):
    """Заказ из pull пользователя s."""
    return next(o for o in s.query("pull")["result"]["purchase_orders"] if o["id"] == oid)


# ====================================================================== поставщики
def test_supplier_directory_rights_codes_and_inn(world, st):  # noqa: F811
    sup = supplier(st.pr, inn="7707083893", contact="+7 495 000-00-00")
    assert (sup["code"], sup["inn"], sup["active"], sup["version"]) == ("ЭТМ", "7707083893", True, 1)
    # Ведёт закупка (A-34); склад, ГИП и admin — нет.
    for s in (st.wh, st.proj.gip, world.admin):
        assert s.cmd("supplier.create", {"id": new_id(), "code": "Х", "name": "Х"})["status"] == "denied"
    # Код уникален и точный; ИНН — с контрольной суммой.
    assert st.pr.cmd("supplier.create", {"id": new_id(), "code": "ЭТМ", "name": "Дубль"})["code"] == "code_exists"
    assert st.pr.cmd("supplier.create", {"id": new_id(), "code": "с пробелом", "name": "Х"})["code"] == "invalid_code"
    r = st.pr.cmd("supplier.create", {"id": new_id(), "code": "Б", "name": "Х", "inn": "7707083894"})
    assert (r["status"], r["code"]) == ("rejected", "invalid_inn")
    # Правка с версией, без изменений — отказ; отключение вместо удаления.
    st.pr.ok("supplier.update", {"id": sup["id"], "active": False}, expected_version=1)
    assert st.pr.cmd("supplier.update", {"id": sup["id"], "active": False}, expected_version=2)["code"] == "no_changes"
    assert st.pr.cmd("supplier.update", {"id": sup["id"], "name": "Y"}, expected_version=1)["status"] == "conflict"
    assert st.pr.cmd("supplier.update", {"id": sup["id"], "code": "Y"}, expected_version=2)["code"] == "unknown_fields"
    # Справочник видят закупка, склад и бухгалтерия; ГИП — нет.
    acc = world.login("acc1")
    for s in (st.pr, st.wh, acc):
        assert [x["code"] for x in s.query("pull")["result"]["suppliers"]] == ["ЭТМ"]
    assert st.proj.gip.query("pull")["result"]["suppliers"] == []
    h = st.pr.query("history", object_type="supplier", object_id=sup["id"])["result"]
    assert [x["command"] for x in h] == ["supplier.create", "supplier.update"]
    assert h[1]["details"] == {"before": {"active": True}, "after": {"active": False}}


# ====================================================================== черновик
def test_draft_numbering_totals_and_unknown_price(world, st):  # noqa: F811
    sup = supplier(st.pr)
    o1 = draft(st, sup, [line(st.item, "100", "12.5"), line(st.item, "3", None)], expected_on="2026-12-01",
               supplier_ref="Счёт 15")
    o2 = draft(st, sup, [], currency=None)
    assert (o1["order_no"], o2["order_no"]) == (1, 2)
    assert (o1["status"], o1["status_label"], o1["delivery"], o1["hash_ok"]) == ("draft", "Черновик", None, None)
    # Итог только по известным ценам; неизвестная цена — не ноль, а «без цены» (итог неполный).
    assert (o1["total"], o1["unpriced_count"]) == ("1250.0000", 1)
    assert [(x["qty"], x["unit_price"], x["amount"], x["unit"]) for x in o1["lines"]] == \
        [("100.0000", "12.5000", "1250.0000", "м"), ("3.0000", None, None, "м")]
    assert (o2["total"], o2["unpriced_count"], o2["currency"]) == (None, 0, None)
    # Цена без валюты — отказ; валюта по умолчанию не подставляется.
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "lines": [line(st.item, "1", "5")]})
    assert (r["status"], r["code"]) == ("rejected", "currency_required")
    # Все замечания к строкам — сразу списком.
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "currency": "RUB",
                                "lines": [line(st.item, "0"), line(st.item, "1", "-1")]})
    assert [p["code"] for p in r["details"]["problems"]] == ["qty_not_positive", "negative_price"]
    # Несуществующая позиция — отказ с номером строки.
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "lines": [line(new_id(), "1")]})
    assert (r["code"], r["details"]["line"]) == ("not_found", 1)
    # Создаёт только закупка.
    assert st.wh.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "lines": []})["status"] == "denied"


def test_draft_update_replaces_lines_and_needs_version(st):  # noqa: F811
    sup = supplier(st.pr)
    o = draft(st, sup, [line(st.item, "10", "2")])
    lines = [line(st.item, "4", "2.5"), line(st.item, "1")]
    n = st.pr.ok("po.update", {"id": o["id"], "lines": lines, "note": "срочно"}, expected_version=1)["purchase_order"]
    assert (n["version"], n["total"], n["note"], len(n["lines"])) == (2, "10.0000", "срочно", 2)
    assert st.pr.cmd("po.update", {"id": o["id"], "note": "x"}, expected_version=1)["status"] == "conflict"
    assert st.pr.cmd("po.update", {"id": o["id"], "lines": lines}, expected_version=2)["code"] == "no_changes"
    # Снять валюту при ценах — отказ (строки перепроверяются с новой валютой).
    assert st.pr.cmd("po.update", {"id": o["id"], "currency": None}, expected_version=2)["code"] == "currency_required"


# ====================================================================== размещение
def test_place_freezes_order_and_rechecks_everything(world, st):  # noqa: F811
    sup = supplier(st.pr)
    empty = draft(st, sup, [])
    assert st.pr.cmd("po.place", {"id": empty["id"]}, expected_version=1)["code"] == "no_lines"
    o = draft(st, sup, [line(st.item, "10", "2")])
    # Размещает закупка, нужна версия; склад — нет.
    assert st.wh.cmd("po.place", {"id": o["id"]}, expected_version=1)["status"] == "denied"
    assert st.pr.cmd("po.place", {"id": o["id"]})["code"] == "expected_version_required"
    # Отключённому поставщику новые заказы не размещают.
    st.pr.ok("supplier.update", {"id": sup["id"], "active": False}, expected_version=1)
    assert st.pr.cmd("po.place", {"id": o["id"]}, expected_version=1)["code"] == "supplier_inactive"
    st.pr.ok("supplier.update", {"id": sup["id"], "active": True}, expected_version=2)
    # Отключённую позицию не заказывают.
    st.wh.ok("item.update", {"id": st.item, "active": False}, expected_version=1)
    assert st.pr.cmd("po.place", {"id": o["id"]}, expected_version=1)["code"] == "item_inactive"
    st.wh.ok("item.update", {"id": st.item, "active": True}, expected_version=2)
    p = st.pr.ok("po.place", {"id": o["id"]}, expected_version=1)["purchase_order"]
    assert (p["status"], p["delivery"], p["delivery_label"], p["hash_ok"]) == ("placed", "none", "Ждём поставку", True)
    assert st.pr.cmd("po.place", {"id": o["id"]}, expected_version=2)["code"] == "order_state"
    # Размещённый: условия не меняются, справочные поля — можно.
    r = st.pr.cmd("po.update", {"id": o["id"], "lines": [line(st.item, "11", "2")]}, expected_version=2)
    assert (r["code"], r["details"]["field"]) == ("order_placed", "lines")
    p = st.pr.ok("po.update", {"id": o["id"], "supplier_ref": "Счёт 99", "expected_on": "2026-12-20"},
                 expected_version=2)["purchase_order"]
    assert (p["supplier_ref"], p["expected_on"], p["hash_ok"]) == ("Счёт 99", "2026-12-20", True)
    # Правка строк в обход программы видна: контрольная сумма не сходится.
    with world.svc.engine.begin() as c:
        c.execute(db.purchase_order_lines.update().where(db.purchase_order_lines.c.order_id == o["id"]).values(qty=Decimal(12)))
    assert pulled_order(st.pr, o["id"])["hash_ok"] is False
    h = st.pr.query("history", object_type="purchase_order", object_id=o["id"])["result"]
    assert [x["command"] for x in h] == ["po.create", "po.place", "po.update"]
    assert len(h[1]["details"]["placed_hash"]) == 64


# ====================================================================== факт поставки
def test_receive_partial_full_storno_and_close(world, st):  # noqa: F811
    sup = supplier(st.pr)
    o = placed(st, sup, [line(st.item, "100", "12.5")])
    ln = o["lines"][0]
    # Принимает склад; закупка — нет. Основание обязательно. Больше, чем ждём, — нельзя (A-36).
    r = st.pr.cmd("po.receive", {"id": new_id(), "order_id": o["id"], "line_id": ln["line_id"], "warehouse_id": st.w,
                                 "qty": "1", "source_doc": "ТН"})
    assert r["status"] == "denied"
    r = st.wh.cmd("po.receive", {"id": new_id(), "order_id": o["id"], "line_id": ln["line_id"], "warehouse_id": st.w,
                                 "qty": "1"})
    assert r["code"] == "field_required"
    r = receive(st, o, ln, "100.0001")
    assert (r["code"], r["details"]["remaining"]) == ("exceeds_ordered", "100.0000")
    first = receive(st, o, ln, "60")["result"]
    assert (first["movement"]["kind"], first["movement"]["source_doc"], first["balance"]["on_hand"]) == ("receipt", "ТН-77", "60.0000")
    po = first["purchase_order"]
    assert (po["delivery"], po["lines"][0]["received"], po["lines"][0]["remaining"]) == ("partial", "60.0000", "40.0000")
    assert receive(st, o, ln, "41")["code"] == "exceeds_ordered"
    # Отменить заказ с поставками нельзя.
    assert st.pr.cmd("po.cancel", {"id": o["id"], "reason": "x"}, expected_version=po["version"])["code"] == "order_has_receipts"
    # Ошибочный приход сторнируют штатно — «поставлено» уменьшается.
    st.wh.ok("stock.reverse", {"id": new_id(), "movement_id": first["movement"]["id"], "reason": "Не та партия"})
    po = pulled_order(st.pr, o["id"])
    assert (po["delivery"], po["lines"][0]["received"], po["lines"][0]["remaining"]) == ("none", "0.0000", "100.0000")
    receive(st, o, ln, "70")
    # Недопоставка: закрыть можно, но только с причиной.
    v = pulled_order(st.pr, o["id"])["version"]
    assert st.pr.cmd("po.close", {"id": o["id"]}, expected_version=v)["code"] == "field_required"
    c = st.pr.ok("po.close", {"id": o["id"], "reason": "Поставщик снял позицию"}, expected_version=v)["purchase_order"]
    assert (c["status"], c["delivery"], c["close_reason"]) == ("closed", "partial", "Поставщик снял позицию")
    # После закрытия поставки по заказу не принимаются.
    assert receive(st, o, ln, "1")["code"] == "order_state"
    assert world.svc.ledger_mismatches() == []


def test_full_delivery_closes_without_reason_and_cancel_rules(st):  # noqa: F811
    sup = supplier(st.pr)
    o = placed(st, sup, [line(st.item, "5")])
    po = receive(st, o, o["lines"][0], "5")["result"]["purchase_order"]
    assert po["delivery"] == "full"
    assert st.pr.ok("po.close", {"id": o["id"]}, expected_version=po["version"])["purchase_order"]["close_reason"] is None
    # Черновик и размещённый без поставок отменяются с причиной; отменённый — конечный.
    d = draft(st, sup, [line(st.item, "1")])
    assert st.pr.cmd("po.cancel", {"id": d["id"]}, expected_version=1)["code"] == "field_required"
    x = st.pr.ok("po.cancel", {"id": d["id"], "reason": "Дубль"}, expected_version=1)["purchase_order"]
    assert (x["status"], x["status_label"]) == ("cancelled", "Отменён")
    assert st.pr.cmd("po.update", {"id": d["id"], "note": "x"}, expected_version=2)["code"] == "order_state"
    assert receive(st, x, d["lines"][0], "1")["code"] == "order_state"
    # Черновик принять нельзя.
    d2 = draft(st, sup, [line(st.item, "1")])
    assert receive(st, d2, d2["lines"][0], "1")["code"] == "order_state"


def test_prices_visible_to_procurement_and_accounting_only(world, st):  # noqa: F811
    sup = supplier(st.pr)
    o = placed(st, sup, [line(st.item, "10", "3")])
    acc = world.login("acc1")
    assert pulled_order(acc, o["id"])["total"] == "30.0000"
    w = pulled_order(st.wh, o["id"])
    assert (w["prices_visible"], w["total"], w["lines"][0]["unit_price"], w["lines"][0]["amount"]) == (False, None, None, None)
    assert w["lines"][0]["qty"] == "10.0000"
    # Ответ на приём поставки складу — тоже без цен.
    r = receive(st, o, o["lines"][0], "1")["result"]["purchase_order"]
    assert r["total"] is None
    assert st.proj.gip.query("pull")["result"]["purchase_orders"] == []
    assert st.proj.gip.query("history", object_type="purchase_order", object_id=o["id"])["status"] == "denied"


# ====================================================================== связь с потребностью
def test_need_link_must_match_line_item_and_shows_on_order(world, st):  # noqa: F811
    ver = _freeze(st.proj, [calc_line("Кабель", "КВВГ-4x1.5", "м", "120"), calc_line("Шкаф", "ШК-1", "шт", "1")])
    need = st.pr.ok("need.generate", {"id": new_id(), "calc_version_id": ver["id"]})["need"]
    l0, l1 = need["lines"]
    sup = supplier(st.pr)
    # Строка потребности не сопоставлена (ШК-1 нет в номенклатуре) — заказывать под неё КВВГ нельзя.
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"],
                                "lines": [line(st.item, "1", need_id=need["id"], need_line_id=l1["line_id"])]})
    assert r["code"] == "need_item_mismatch"
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"],
                                "lines": [line(st.item, "1", need_id=need["id"], need_line_id=new_id())]})
    assert (r["code"], r["details"]["field"]) == ("not_found", "need_line_id")
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"],
                                "lines": [line(st.item, "1", need_id=need["id"])]})
    assert r["code"] == "need_pair"
    o = placed(st, sup, [line(st.item, "100", need_id=need["id"], need_line_id=l0["line_id"])])
    # В потребности видно «заказано, ждём» — только по размещённым заказам.
    draft(st, sup, [line(st.item, "7", need_id=need["id"], need_line_id=l0["line_id"])])
    n = next(x for x in st.pr.query("pull")["result"]["needs"] if x["id"] == need["id"])
    assert (n["lines"][0]["on_order"], n["lines"][1]["on_order"]) == ("100.0000", "0.0000")
    receive(st, o, o["lines"][0], "30")
    n = next(x for x in st.proj.gip.query("pull")["result"]["needs"] if x["id"] == need["id"])
    assert n["lines"][0]["on_order"] == "70.0000"


def test_purchasing_commands_offline_classes(world):
    cat = world.svc.contracts.catalog["commands"]
    for c in ("supplier.create", "supplier.update", "po.create", "po.update"):
        assert cat[c]["offline"] == "queued", c
    for c in ("po.place", "po.cancel", "po.close", "po.receive"):
        assert cat[c]["offline"] == "online_required", c


def test_receipts_are_immutable(world, st):  # noqa: F811
    sup = supplier(st.pr)
    o = placed(st, sup, [line(st.item, "5")])
    receive(st, o, o["lines"][0], "2")
    with pytest.raises(Exception):
        with world.svc.engine.begin() as c:
            c.execute(db.purchase_receipts.update().values(qty=Decimal(5)))
    with pytest.raises(Exception):
        with world.svc.engine.begin() as c:
            c.execute(db.purchase_receipts.delete())
