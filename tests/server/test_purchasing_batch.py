"""Закупка из пакета (этап 4б, D-59): связь строки заказа со строкой пакета, кратность и лимит заказа,
распределение прихода по распределениям (резервы проектов + свободный излишек), сторно.

Каждый тест закрепляет правило из server/purchasing.py (чистые правила — vector.core.consolidation); запускается
на SQLite и на встроенном PostgreSQL. Кто есть кто: pr1 — закупка, wh1 — склад, rp1 — руководитель проекта.
"""
from decimal import Decimal

import pytest
import sqlalchemy as sa

from conftest import new_id
from test_consolidation import _freeze_for, alloc, make_batch, make_need, pull
from test_purchasing import approved, draft, line, placed, pulled_order, receive, supplier  # noqa: F401
from test_stock import calc_line, st  # noqa: F401
from test_tasks_documents import proj  # noqa: F401
from vector.server import db


@pytest.fixture(autouse=True)
def _remember_world(world):
    """Запомнить World теста для помощника approver() из test_purchasing."""
    from test_purchasing import _W
    _W["w"] = world
    yield
    _W.clear()


def pack(st, qty="100"):  # noqa: F811
    """Задать позиции кабеля кратность заказа (барабан 100 м)."""
    st.wh.ok("item.update", {"id": st.item, "pack_qty": qty}, expected_version=1)


def consolidated_batch(st, need, qty, idx=0):  # noqa: F811
    """Пакет в статусе «Сформирован» с распределением qty по строке потребности idx."""
    b = make_batch(st, [alloc(need, idx, qty)])["result"]["batch"]
    st.pr.ok("batch.transition", {"id": b["id"], "to": "consolidated"}, expected_version=b["version"])
    bl = [x for x in pull(st.pr)["procurement_batch_lines"] if x["batch_id"] == b["id"]][0]
    return b, bl


def order_from_batch(st, sup, bl, qty):  # noqa: F811
    """Размещённый заказ со строкой, связанной со строкой пакета."""
    return placed(st, sup, [line(st.item, qty, batch_line_id=bl_id(st, bl))])


def bl_id(st, bl):  # noqa: F811
    return bl["id"]


# ====================================================================== связь строки заказа со строкой пакета
def test_order_line_links_batch_and_hash_recovers(world, st):  # noqa: F811
    need = make_need(st, "100")
    b, bl = consolidated_batch(st, need, "100")
    sup = supplier(st.pr)
    # Черновик: связь принимается, если строка пакета существует и позиция совпадает.
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "currency": "RUB",
                                "lines": [line(st.item, "100", batch_line_id=bl["id"])]})
    assert r["status"] == "accepted"
    o = r["result"]["purchase_order"]
    ln = pulled_order(st.pr, o["id"])["lines"][0]
    assert (ln["batch_id"], ln["batch_line_id"]) == (b["id"], bl["id"]) and ln["batch_no"] == b["batch_no"]
    # Либо batch_line_id, либо need_id — не вместе; чужая позиция и несуществующая строка — отказ.
    nl = need["lines"][0]
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "currency": "RUB",
                                "lines": [line(st.item, "100", batch_line_id=bl["id"], need_id=need["id"],
                                               need_line_id=nl["line_id"])]})
    assert r["code"] == "line_link_ambiguous"
    other = new_id()
    st.wh.ok("item.create", {"id": other, "code": "Р-КНП", "name": "Короб", "unit": "шт"})
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "currency": "RUB",
                                "lines": [line(other, "1", batch_line_id=bl["id"])]})
    assert r["code"] == "batch_item_mismatch"
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "currency": "RUB",
                                "lines": [line(st.item, "100", batch_line_id=new_id())]})
    assert r["code"] == "not_found"
    # Связь входит в хэш содержимого: согласованный заказ изменить нельзя (order_on_approval), а правка связи
    # в обход программы (прямой UPDATE БД) делает согласование недействительным (approval_stale).
    o2 = draft(st, sup, [line(st.item, "100", batch_line_id=bl["id"])])
    o2 = approved(st, o2)
    assert st.pr.cmd("po.update", {"id": o2["id"], "lines": [line(st.item, "100")]},
                     expected_version=o2["version"])["code"] == "order_on_approval"
    with world.svc.engine.begin() as c:
        c.execute(db.purchase_order_batch_lines.delete().where(db.purchase_order_batch_lines.c.order_id == o2["id"]))
    assert pulled_order(st.pr, o2["id"])["approval"]["hash_ok"] is False
    assert st.pr.cmd("po.place", {"id": o2["id"]}, expected_version=o2["version"])["code"] == "approval_stale"
    # Размещённый заказ показывает связь и участвует в «заказано всего» по строке пакета.
    o3 = placed(st, sup, [line(st.item, "100", batch_line_id=bl["id"])])
    ln = pulled_order(st.pr, o3["id"])["lines"][0]
    assert ln["batch_no"] == b["batch_no"]


def test_batch_must_be_consolidated_to_place(world, st):  # noqa: F811
    need = make_need(st, "95")
    b = make_batch(st, [alloc(need, 0, "95")])["result"]["batch"]
    bl = [x for x in pull(st.pr)["procurement_batch_lines"] if x["batch_id"] == b["id"]][0]
    sup = supplier(st.pr)
    # Пакет ещё формируется: разместить заказ по нему нельзя.
    o = approved(st, draft(st, sup, [line(st.item, "100", batch_line_id=bl["id"])]))
    r = st.pr.cmd("po.place", {"id": o["id"]}, expected_version=o["version"])
    assert r["code"] == "batch_not_consolidated"
    # Отменённый пакет — тоже.
    need2 = make_need(st, "95")
    b2 = make_batch(st, [alloc(need2, 0, "95")])["result"]["batch"]
    st.pr.ok("batch.transition", {"id": b2["id"], "to": "cancelled", "reason": "передумали"},
             expected_version=b2["version"])
    bl2 = [x for x in pull(st.pr)["procurement_batch_lines"] if x["batch_id"] == b2["id"]][0]
    o2 = approved(st, draft(st, sup, [line(st.item, "100", batch_line_id=bl2["id"])]))
    assert st.pr.cmd("po.place", {"id": o2["id"]}, expected_version=o2["version"])["code"] == "batch_not_consolidated"


# ====================================================================== кратность и лимит заказа
def test_pack_multiple_and_order_limit(world, st):  # noqa: F811
    pack(st, "100")                       # кабель — барабанами по 100 м
    need = make_need(st, "95")
    b, bl = consolidated_batch(st, need, "95")
    sup = supplier(st.pr)
    # 95 кратно кратности? Нет — отказ; 100 — можно (излишек 5 — только от округления).
    o = approved(st, draft(st, sup, [line(st.item, "95", batch_line_id=bl["id"])]))
    r = st.pr.cmd("po.place", {"id": o["id"]}, expected_version=o["version"])
    assert r["code"] == "pack_multiple" and r["details"]["pack_qty"] == "100.0000"
    o = approved(st, draft(st, sup, [line(st.item, "100", batch_line_id=bl["id"])]))
    r = st.pr.cmd("po.place", {"id": o["id"]}, expected_version=o["version"])
    assert r["status"] == "accepted"
    # Лимит: 100 размещено, распределено 95 → округление до 200 уже превышает лимит (лимит = 100).
    o2 = approved(st, draft(st, sup, [line(st.item, "100", batch_line_id=bl["id"])]))
    r = st.pr.cmd("po.place", {"id": o2["id"]}, expected_version=o2["version"])
    assert r["code"] == "batch_order_exceeded" and r["details"]["limit"] == "100.0000"
    # Отмена размещённого заказа освобождает лимит.
    po = pulled_order(st.pr, o["id"])
    st.pr.ok("po.cancel", {"id": po["id"], "reason": "поставщик сорвал"}, expected_version=po["version"])
    o2r = st.pr.cmd("po.place", {"id": o2["id"]}, expected_version=pulled_order(st.pr, o2["id"])["version"])
    assert o2r["status"] == "accepted"


def test_without_pack_any_qty_but_limit_holds(world, st):  # noqa: F811
    need = make_need(st, "95")
    b, bl = consolidated_batch(st, need, "95")
    sup = supplier(st.pr)
    # Без кратности заказать можно ровно распределённое: 95 — да, 96 — нет.
    assert st.pr.cmd("po.place", {"id": approved(st, draft(st, sup, [line(st.item, "96", batch_line_id=bl["id"])]))["id"]},
                     expected_version=3)["code"] == "batch_order_exceeded"
    o = approved(st, draft(st, sup, [line(st.item, "95", batch_line_id=bl["id"])]))
    assert st.pr.cmd("po.place", {"id": o["id"]}, expected_version=o["version"])["status"] == "accepted"


# ====================================================================== распределение прихода
def test_receipt_distributes_to_project_reserves_and_surplus(world, st):  # noqa: F811
    pack(st, "100")
    need = make_need(st, "95")
    b, bl = consolidated_batch(st, need, "95")
    sup = supplier(st.pr)
    o = placed(st, sup, [line(st.item, "100", batch_line_id=bl["id"])])
    ln = pulled_order(st.pr, o["id"])["lines"][0]
    # Приход 100: 95 — в резерв проекта потребности, 5 — свободный остаток (surplus).
    r = receive(st, o, ln, "100")
    assert r["status"] == "accepted"
    res = r["result"]
    assert res["surplus"] == "5.0000"
    dist = res["distribution"]
    assert len(dist) == 1 and dist[0]["qty"] == "95.0000" and dist[0]["reserved"] == "95.0000"
    assert dist[0]["need_id"] == need["id"]
    data = pull(st.wh)
    bal = [x for x in data["stock_balances"] if x["warehouse_id"] == st.w and x["item_id"] == st.item][0]
    assert (bal["on_hand"], bal["reserved"], bal["available"]) == ("100.0000", "95.0000", "5.0000")
    resv = [x for x in data["stock_reservations"] if x["need_id"] == need["id"]]
    assert len(resv) == 1 and resv[0]["qty"] == "95.0000" and "пакет № 1" in resv[0]["reason"]
    # Распределение прихода неизменяемо в БД: распределено 95 по распределению, резерв связан.
    with world.svc.engine.connect() as c:
        ar = c.execute(sa.select(db.procurement_allocation_receipts)).all()
    assert len(ar) == 1 and str(ar[0].qty) == "95.0000" and ar[0].reservation_id == resv[0]["id"]
    # Поставлено по распределению и строке пакета видно в pull; строка потребности — «в пакетах» 95, «заказано» 0.
    data = pull(st.pr)
    a = [x for x in data["procurement_allocations"] if x["batch_line_id"] == bl["id"]][0]
    assert a["delivered"] == "95.0000"
    blv = [x for x in data["procurement_batch_lines"] if x["id"] == bl["id"]][0]
    assert (blv["allocated"], blv["ordered"], blv["delivered"]) == ("95.0000", "100.0000", "95.0000")
    nd = [x for x in pull(st.pr)["needs"] if x["id"] == need["id"]][0]
    assert nd["lines"][0]["in_batches"] == "95.0000"
    # Руководитель проекта получил уведомление о поставке.
    rp_pull = pull(st.proj.rp)
    n = [x for x in rp_pull["notifications"] if x["kind"] == "batch_received"]
    assert n and "пакету № 1" in n[0]["title"]


def test_receipt_partial_then_rest_keeps_allocation(world, st):  # noqa: F811
    need = make_need(st, "10")
    b, bl = consolidated_batch(st, need, "10")
    sup = supplier(st.pr)
    o = placed(st, sup, [line(st.item, "10", batch_line_id=bl["id"])])
    ln = pulled_order(st.pr, o["id"])["lines"][0]
    # Два частичных прихода: поставлено по распределению копится; излишек — 0.
    r1 = receive(st, o, ln, "4")
    assert r1["status"] == "accepted" and r1["result"]["distribution"][0]["reserved"] == "4.0000"
    r2 = receive(st, o, ln, "6")
    assert r2["status"] == "accepted" and r2["result"]["distribution"][0]["reserved"] == "6.0000"
    a = [x for x in pull(st.pr)["procurement_allocations"] if x["batch_line_id"] == bl["id"]][0]
    assert a["delivered"] == "10.0000"
    # Пакет поставлен полностью: закрывается без остатка.
    po = pulled_order(st.pr, o["id"])
    assert st.pr.cmd("po.close", {"id": po["id"]}, expected_version=po["version"])["status"] == "accepted"
    bv = [x for x in pull(st.pr)["procurement_batches"] if x["id"] == b["id"]][0]
    st.pr.ok("batch.transition", {"id": b["id"], "to": "closed"}, expected_version=bv["version"])
    assert [x for x in pull(st.pr)["procurement_batches"] if x["id"] == b["id"]][0]["status"] == "closed"


def test_receipt_when_need_already_covered_from_stock_goes_to_surplus(world, st):  # noqa: F811
    need = make_need(st, "10")
    b, bl = consolidated_batch(st, need, "10")
    sup = supplier(st.pr)
    o = placed(st, sup, [line(st.item, "10", batch_line_id=bl["id"])])
    ln = pulled_order(st.pr, o["id"])["lines"][0]
    # Пока заказ едет, склад вручную зарезервировал 10 под эту строку из свободного остатка.
    st.wh.ok("stock.receive", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "qty": "10",
                               "source_doc": "ИНВ-1"})
    nl = need["lines"][0]
    st.wh.ok("stock.reserve", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "project_id": st.proj.pid,
                               "qty": "10", "reason": "Из остатка", "need_id": need["id"],
                               "need_line_id": nl["line_id"]})
    # Приход по заказу: потребность уже покрыта — вся поставка уходит в свободный остаток, дублей нет.
    r = receive(st, o, ln, "10")
    assert r["status"] == "accepted"
    assert r["result"]["distribution"][0]["reserved"] == "0.0000"
    assert r["result"]["surplus"] == "0.0000"  # отнесено на распределение (поставлено), но не зарезервировано
    data = pull(st.wh)
    bal = [x for x in data["stock_balances"] if x["warehouse_id"] == st.w and x["item_id"] == st.item][0]
    assert (bal["on_hand"], bal["reserved"]) == ("20.0000", "10.0000")
    a = [x for x in pull(st.pr)["procurement_allocations"] if x["batch_line_id"] == bl["id"]][0]
    assert a["delivered"] == "10.0000"


def test_storno_of_distributed_receipt_releases_reserves(world, st):  # noqa: F811
    need = make_need(st, "10")
    b, bl = consolidated_batch(st, need, "10")
    sup = supplier(st.pr)
    o = placed(st, sup, [line(st.item, "10", batch_line_id=bl["id"])])
    ln = pulled_order(st.pr, o["id"])["lines"][0]
    r = receive(st, o, ln, "10")
    mv = r["result"]["movement"]["id"]
    # Сторно прихода: резерв пакета снят (причина — сторно), «поставлено» по распределению обнулилось.
    rr = st.wh.cmd("stock.reverse", {"id": new_id(), "movement_id": mv, "reason": "Ошибка приёмки"})
    assert rr["status"] == "accepted"
    data = pull(st.wh)
    bal = [x for x in data["stock_balances"] if x["warehouse_id"] == st.w and x["item_id"] == st.item][0]
    assert (bal["on_hand"], bal["reserved"]) == ("0.0000", "0.0000")
    resv = [x for x in data["stock_reservations"] if x["need_id"] == need["id"]]
    assert resv[0]["status"] == "released" and "Сторно" in resv[0]["close_reason"]
    a = [x for x in pull(st.pr)["procurement_allocations"] if x["batch_line_id"] == bl["id"]][0]
    assert a["delivered"] == "0.0000"
    # После сторно по строке заказа снова ждём 10: повторный приход распределяется заново.
    r2 = receive(st, o, ln, "10", src="ТН-78")
    assert r2["status"] == "accepted" and r2["result"]["distribution"][0]["reserved"] == "10.0000"


def test_storno_rejected_after_issue_from_reserve(world, st):  # noqa: F811
    need = make_need(st, "10")
    b, bl = consolidated_batch(st, need, "10")
    sup = supplier(st.pr)
    o = placed(st, sup, [line(st.item, "10", batch_line_id=bl["id"])])
    ln = pulled_order(st.pr, o["id"])["lines"][0]
    r = receive(st, o, ln, "10")
    mv = r["result"]["movement"]["id"]
    resv_id = r["result"]["distribution"][0]["reservation_id"]
    # Выдача под резерв проекта: 4 из 10 израсходовано.
    st.wh.ok("stock.issue", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "qty": "4",
                             "reservation_id": resv_id, "reason": "Монтаж"})
    # Сторнировать приход нельзя, пока по его резерву есть выдача (израсходованное не восстанавливается).
    rr = st.wh.cmd("stock.reverse", {"id": new_id(), "movement_id": mv, "reason": "Ошибка"})
    assert rr["code"] == "receipt_consumed" and rr["details"]["reservation_id"] == resv_id
    # Даже после сторно выдачи приход не сторнируется: израсходованное не возвращается в резерв.
    data = pull(st.wh)
    issue_mv = [x for x in data["stock_movements"] if x["kind"] == "issue" and x["reservation_id"] == resv_id][0]
    st.wh.ok("stock.reverse", {"id": new_id(), "movement_id": issue_mv["id"], "reason": "Вернули"})
    rr = st.wh.cmd("stock.reverse", {"id": new_id(), "movement_id": mv, "reason": "Ошибка приёмки"})
    assert rr["code"] == "receipt_consumed"


# ====================================================================== переходы пакета с заказами
def test_batch_cannot_reopen_or_cancel_with_orders_in_fly(world, st):  # noqa: F811
    need = make_need(st, "10")
    b, bl = consolidated_batch(st, need, "10")
    sup = supplier(st.pr)
    o = placed(st, sup, [line(st.item, "10", batch_line_id=bl["id"])])
    bv = [x for x in pull(st.pr)["procurement_batches"] if x["id"] == b["id"]][0]
    # По пакету размещён заказ: вернуть в работу или отменить нельзя — сначала отменить заказ.
    assert st.pr.cmd("batch.transition", {"id": b["id"], "to": "open", "reason": "пересчёт"},
                     expected_version=bv["version"])["code"] == "batch_has_orders"
    assert st.pr.cmd("batch.transition", {"id": b["id"], "to": "cancelled", "reason": "хотим"},
                     expected_version=bv["version"])["code"] == "batch_has_orders"
    # Закрыть пакет до поставки нельзя (allocations_open).
    assert st.pr.cmd("batch.transition", {"id": b["id"], "to": "closed"}, expected_version=bv["version"]
                     )["code"] == "allocations_open"
    # Отменили заказ → пакет можно вернуть в работу и отменить (покрытие освободилось).
    po = pulled_order(st.pr, o["id"])
    st.pr.ok("po.cancel", {"id": po["id"], "reason": "сорвалось"}, expected_version=po["version"])
    bv = [x for x in pull(st.pr)["procurement_batches"] if x["id"] == b["id"]][0]
    st.pr.ok("batch.transition", {"id": b["id"], "to": "open", "reason": "пересчёт объёма"},
             expected_version=bv["version"])
    bv = [x for x in pull(st.pr)["procurement_batches"] if x["id"] == b["id"]][0]
    st.pr.ok("batch.transition", {"id": b["id"], "to": "cancelled", "reason": "не требуется"},
             expected_version=bv["version"])
    # Покрытие свободно: теперь строку можно заказать напрямую (без пакета).
    nl = need["lines"][0]
    assert st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "currency": "RUB",
                                   "lines": [line(st.item, "10", need_id=need["id"],
                                                  need_line_id=nl["line_id"])]})["status"] == "accepted"


# ====================================================================== кратность позиции (item.pack_qty)
def test_item_pack_qty_crud(world, st):  # noqa: F811
    # Создание с кратностью; смена; снятие; ноль — отказ.
    it = st.wh.ok("item.create", {"id": new_id(), "code": "КГ-3x2.5", "name": "Кабель КГ", "unit": "м",
                                  "pack_qty": "50"})["stock_item"]
    assert it["pack_qty"] == "50.0000"
    it = st.wh.ok("item.update", {"id": it["id"], "pack_qty": "100"}, expected_version=1)["stock_item"]
    assert it["pack_qty"] == "100.0000"
    it = st.wh.ok("item.update", {"id": it["id"], "pack_qty": None}, expected_version=2)["stock_item"]
    assert it["pack_qty"] is None
    assert st.wh.cmd("item.update", {"id": it["id"], "pack_qty": "0"}, expected_version=3)["code"] == "qty_not_positive"
    assert st.wh.cmd("item.update", {"id": it["id"], "pack_qty": "abc"}, expected_version=3)["code"] == "invalid_format"
    # Без изменений — отказ; кратность видна в pull.
    assert st.wh.cmd("item.update", {"id": it["id"]}, expected_version=3)["code"] == "no_changes"
    pack(st, "100")
    got = {x["id"]: x for x in pull(st.wh)["stock_items"]}
    assert got[st.item]["pack_qty"] == "100.0000" and got[it["id"]]["pack_qty"] is None
