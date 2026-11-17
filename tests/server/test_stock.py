"""Склад и закупочная потребность (сценарий С4, первая часть; R-13, R-14).

Каждый тест закрепляет одно правило из server/stock.py; запускается на SQLite и на встроенном PostgreSQL
(конкурентные тесты особенно важны на PostgreSQL: там транзакции действительно идут параллельно).
Кто есть кто: wh1 — склад (глобальная роль), pr1 — закупка, wh2 — второй кладовщик; команда проекта
как в test_tasks_documents (gip1 — ГИП, rp1 — РП, eng1 — участник).
"""
import threading
import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest
import sqlalchemy as sa

from conftest import new_id
from test_tasks_documents import audit, proj  # noqa: F401  (фикстура proj переиспользует стенд)
from vector.server import db


@dataclass
class St:
    proj: object
    wh: object
    wh2: object
    pr: object
    w: str
    item: str


@pytest.fixture
def st(world, proj):  # noqa: F811
    world.make_user("wh1", "warehouse")
    world.make_user("wh2", "warehouse")
    world.make_user("pr1", "procurement")
    wh = world.login("wh1")
    w, item = new_id(), new_id()
    wh.ok("warehouse.create", {"id": w, "code": "СК-1", "name": "Центральный склад"})
    wh.ok("item.create", {"id": item, "code": "КВВГ-4x1.5", "name": "Кабель КВВГ 4×1,5", "unit": "м"})
    return St(proj, wh, world.login("wh2"), world.login("pr1"), w, item)


def receive(s, st, qty, item=None, w=None, src="ТН-1"):
    return s.cmd("stock.receive", {"id": new_id(), "warehouse_id": w or st.w, "item_id": item or st.item, "qty": qty,
                                   "source_doc": src})


def issue(s, st, qty, **kw):
    p = {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "qty": qty, "reason": "Монтаж"}
    if "reservation_id" not in kw:
        p["project_id"] = st.proj.pid
    p.update(kw)
    return s.cmd("stock.issue", p)


def reserve(s, st, qty, **kw):
    return s.cmd("stock.reserve", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "project_id": st.proj.pid,
                                   "qty": qty, "reason": "Под монтаж", **kw})


def bal(s, st):
    r = s.query("pull")["result"]
    b = [x for x in r["stock_balances"] if x["warehouse_id"] == st.w and x["item_id"] == st.item]
    return (b[0]["on_hand"], b[0]["reserved"], b[0]["available"]) if b else None


# ====================================================================== справочники и права
def test_reference_data_rights_and_codes(world, st):
    # Номенклатуру ведут склад и закупка; склад — только склад; admin бизнес-прав не получает.
    assert st.pr.cmd("item.create", {"id": new_id(), "code": "Р-1", "name": "Реле", "unit": "шт"})["status"] == "accepted"
    assert st.pr.cmd("warehouse.create", {"id": new_id(), "code": "СК-2", "name": "Х"})["status"] == "denied"
    assert world.admin.cmd("item.create", {"id": new_id(), "code": "Р-2", "name": "Реле", "unit": "шт"})["status"] == "denied"
    assert st.proj.gip.cmd("item.create", {"id": new_id(), "code": "Р-3", "name": "Реле", "unit": "шт"})["status"] == "denied"
    # Код уникален и проверяется (без пробелов).
    r = st.wh.cmd("item.create", {"id": new_id(), "code": "КВВГ-4x1.5", "name": "Дубль", "unit": "м"})
    assert (r["status"], r["code"]) == ("rejected", "code_exists")
    r = st.wh.cmd("item.create", {"id": new_id(), "code": "с пробелом", "name": "Х", "unit": "м"})
    assert r["code"] == "invalid_code"
    # Правка с версией; повтор без изменений — отказ «нет изменений».
    it = st.wh.ok("item.update", {"id": st.item, "name": "Кабель КВВГнг 4×1,5"}, expected_version=1)["stock_item"]
    assert it["version"] == 2
    assert st.wh.cmd("item.update", {"id": st.item, "name": it["name"]}, expected_version=2)["code"] == "no_changes"
    assert st.wh.cmd("item.update", {"id": st.item, "name": "Y"}, expected_version=1)["status"] == "conflict"


def test_unit_is_locked_once_stock_exists(st):
    st.wh.ok("item.update", {"id": st.item, "unit": "км"}, expected_version=1)        # движений нет — можно
    assert receive(st.wh, st, "5")["status"] == "accepted"
    r = st.wh.cmd("item.update", {"id": st.item, "unit": "м"}, expected_version=2)
    assert (r["status"], r["code"]) == ("rejected", "unit_locked")


def test_only_warehouse_posts_and_reads(world, st):
    assert receive(st.pr, st, "1")["status"] == "denied"            # закупка видит, но не проводит
    assert receive(st.proj.gip, st, "1")["status"] == "denied"
    receive(st.wh, st, "3")
    assert bal(st.pr, st) == ("3.0000", "0.0000", "3.0000")
    # Посторонние не видят склад вообще (а не «пустой склад»).
    r = st.proj.eng.query("pull")["result"]
    assert r["warehouses"] == [] and r["stock_balances"] == [] and r["stock_movements"] == []


# ====================================================================== проводки
def test_receipt_issue_and_ledger_is_immutable(world, st):
    r = receive(st.wh, st, "10.5")
    assert r["status"] == "accepted", r
    m = r["result"]["movement"]
    assert (m["kind"], m["qty"], m["doc_no"], m["source_doc"]) == ("receipt", "10.5000", 1, "ТН-1")
    r = issue(st.wh, st, "0.5")
    assert r["result"]["balance"]["available"] == "10.0000" and r["result"]["movement"]["project_id"] == st.proj.pid
    assert bal(st.wh, st) == ("10.0000", "0.0000", "10.0000")
    # Журнал неизменяем на уровне БД.
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as c:
            c.execute(db.stock_movements.update().values(qty=Decimal("1")))
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as c:
            c.execute(db.stock_movements.delete())
    assert world.svc.ledger_mismatches() == []


@pytest.mark.parametrize("qty,code", [("0", "qty_not_positive"), ("-1", "qty_not_positive"), ("1.00001", "too_many_fraction_digits"),
                                      (1.5, "float_forbidden"), (None, "invalid_type")])
def test_bad_quantities_are_rejected(st, qty, code):
    r = receive(st.wh, st, qty)
    assert (r["status"], r["code"]) == ("rejected", code)


def test_receipt_requires_source_document(st):
    r = st.wh.cmd("stock.receive", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "qty": "1"})
    assert (r["status"], r["code"]) == ("rejected", "field_required")


def test_cannot_issue_more_than_available(st):
    receive(st.wh, st, "5")
    reserve(st.wh, st, "3")
    r = issue(st.wh, st, "2.0001")
    assert (r["status"], r["code"]) == ("rejected", "insufficient_available")
    assert r["details"]["balance"] == {"on_hand": "5.0000", "reserved": "3.0000", "available": "2.0000"}
    assert issue(st.wh, st, "2")["status"] == "accepted"
    assert bal(st.wh, st) == ("3.0000", "3.0000", "0.0000")


def test_storno_once_and_never_below_reserved(world, st):
    m = receive(st.wh, st, "5")["result"]["movement"]
    reserve(st.wh, st, "2")
    r = st.wh.cmd("stock.reverse", {"id": new_id(), "movement_id": m["id"], "reason": "Ошибка ввода"})
    assert (r["status"], r["code"]) == ("rejected", "insufficient_available")      # 2 из 5 уже в резерве
    res = st.wh.query("pull")["result"]["stock_reservations"][0]
    st.wh.ok("stock.release", {"id": res["id"], "reason": "Пересчёт"}, expected_version=1)
    s = st.wh.ok("stock.reverse", {"id": new_id(), "movement_id": m["id"], "reason": "Ошибка ввода"})["movement"]
    assert (s["kind"], s["reverses_id"], s["kind_label"]) == ("issue", m["id"], "Сторно прихода")
    r = st.wh.cmd("stock.reverse", {"id": new_id(), "movement_id": m["id"], "reason": "ещё раз"})
    assert r["code"] == "already_reversed"
    r = st.wh.cmd("stock.reverse", {"id": new_id(), "movement_id": s["id"], "reason": "сторно сторно"})
    assert r["code"] == "cannot_reverse_storno"
    assert bal(st.wh, st) == ("0.0000", "0.0000", "0.0000")
    pulled = {x["id"]: x for x in st.wh.query("pull")["result"]["stock_movements"]}
    assert pulled[m["id"]]["reversed_by"] == s["id"]
    assert world.svc.ledger_mismatches() == []


def test_inactive_reference_blocks_new_stock_but_not_issue(st):
    receive(st.wh, st, "2")
    st.wh.ok("item.update", {"id": st.item, "active": False}, expected_version=1)
    assert receive(st.wh, st, "1")["code"] == "item_inactive"
    assert reserve(st.wh, st, "1")["code"] == "item_inactive"
    assert issue(st.wh, st, "2")["status"] == "accepted"          # остаток можно выдать


# ====================================================================== резервы
def test_reservation_lifecycle_consumed_by_issues(world, st):
    receive(st.wh, st, "10")
    res = reserve(st.wh, st, "4")["result"]["reservation"]
    assert (res["status"], res["remaining"]) == ("active", "4.0000")
    assert bal(st.wh, st) == ("10.0000", "4.0000", "6.0000")
    r = issue(st.wh, st, "5", reservation_id=res["id"])
    assert r["code"] == "exceeds_reservation"
    r = issue(st.wh, st, "1.5", reservation_id=res["id"])["result"]
    assert (r["reservation"]["status"], r["reservation"]["remaining"]) == ("active", "2.5000")
    assert r["movement"]["project_id"] == st.proj.pid                    # проект берётся из резерва
    r = issue(st.wh, st, "2.5", reservation_id=res["id"])["result"]
    assert r["reservation"]["status"] == "consumed" and r["reservation"]["remaining"] == "0.0000"
    assert issue(st.wh, st, "1", reservation_id=res["id"])["code"] == "reservation_state"
    assert bal(st.wh, st) == ("6.0000", "0.0000", "6.0000")
    assert world.svc.ledger_mismatches() == []


def test_release_returns_remaining_and_needs_version(st):
    receive(st.wh, st, "3")
    res = reserve(st.wh, st, "3")["result"]["reservation"]
    issue(st.wh, st, "1", reservation_id=res["id"])
    assert st.wh.cmd("stock.release", {"id": res["id"], "reason": "x"})["code"] == "expected_version_required"
    assert st.wh.cmd("stock.release", {"id": res["id"], "reason": "x"}, expected_version=1)["status"] == "conflict"
    r = st.wh.ok("stock.release", {"id": res["id"], "reason": "Отмена монтажа"}, expected_version=2)["reservation"]
    assert (r["status"], r["consumed"], r["remaining"]) == ("released", "1.0000", "0.0000")
    assert bal(st.wh, st) == ("2.0000", "0.0000", "2.0000")
    assert st.wh.cmd("stock.release", {"id": res["id"], "reason": "x"}, expected_version=3)["code"] == "reservation_state"


def test_reserve_cannot_exceed_available_and_procurement_cannot_reserve(st):
    receive(st.wh, st, "2")
    assert reserve(st.wh, st, "2.0001")["code"] == "insufficient_available"
    assert reserve(st.pr, st, "1")["status"] == "denied"       # A-21: резервирует склад
    other = new_id()
    st.wh.ok("warehouse.create", {"id": other, "code": "СК-2", "name": "Второй"})
    r = issue(st.wh, st, "1", warehouse_id=other)                # на другом складе остатка нет
    assert r["code"] == "insufficient_available"


def test_reservation_issue_must_match_place(st):
    other = new_id()
    st.wh.ok("item.create", {"id": other, "code": "Р-1", "name": "Реле", "unit": "шт"})
    receive(st.wh, st, "5")
    res = reserve(st.wh, st, "2")["result"]["reservation"]
    assert issue(st.wh, st, "1", reservation_id=res["id"], item_id=other)["code"] == "reservation_mismatch"


# ====================================================================== конкуренция
def _race(world, tokens, payloads):
    results, barrier = [], threading.Barrier(len(tokens))

    def worker(tok, cmd, p):
        barrier.wait()
        results.append(world.svc.handle({"type": "command", "token": tok, "operation_id": new_id(), "command": cmd,
                                         "payload": p}))
    ts = [threading.Thread(target=worker, args=(t, c, p)) for t, (c, p) in zip(tokens, payloads)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return results


def test_concurrent_issues_of_last_units_only_one_wins(world, st):
    receive(st.wh, st, "1")
    mk = lambda: ("stock.issue", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "qty": "1",  # noqa: E731
                                  "project_id": st.proj.pid, "reason": "Гонка"})
    results = _race(world, [st.wh.token, st.wh2.token, st.wh.token], [mk(), mk(), mk()])
    assert sorted(r["status"] for r in results) == ["accepted", "rejected", "rejected"], results
    assert {r.get("code") for r in results if r["status"] == "rejected"} == {"insufficient_available"}
    assert bal(st.wh, st) == ("0.0000", "0.0000", "0.0000")
    assert world.svc.ledger_mismatches() == []


def test_concurrent_reserve_and_issue_never_oversell(world, st):
    receive(st.wh, st, "3")
    results = _race(world, [st.wh.token, st.wh2.token], [
        ("stock.reserve", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "project_id": st.proj.pid,
                           "qty": "2", "reason": "Резерв"}),
        ("stock.issue", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "project_id": st.proj.pid,
                         "qty": "2", "reason": "Выдача"})])
    assert sorted(r["status"] for r in results) == ["accepted", "rejected"], results
    on_hand, reserved, avail = bal(st.wh, st)
    assert avail == "1.0000" and Decimal(reserved) <= Decimal(on_hand)
    assert world.svc.ledger_mismatches() == []


# ====================================================================== потребность
def _freeze(proj, lines):
    proj.gip.ok("calc.save_draft", {"id": proj.kit, "lines": lines, "currency": "RUB"}, expected_version=0)
    return proj.gip.ok("calc.freeze", {"id": proj.kit, "reason": "Выпуск"}, expected_version=1)["calc_version"]


def calc_line(name, code, unit, qty, price="10"):
    return {"line_id": str(uuid.uuid4()), "name": name, "code": code, "unit": unit, "qty": qty, "unit_price": price}


def test_need_from_latest_frozen_version_matches_items_exactly(world, st):
    lines = [calc_line("Кабель", "КВВГ-4x1.5", "м", "120"), calc_line("Кабель, но в км", "КВВГ-4x1.5", "км", "1"),
             calc_line("Шкаф", "ШК-1", "шт", "1"), calc_line("Без кода", None, "шт", "2")]
    ver = _freeze(st.proj, lines)
    # Закупка видит кандидата (последняя версия без потребности) — без цен; склад кандидатов не видит.
    cand = st.pr.query("pull")["result"]["need_candidates"]
    assert [(c["calc_version_id"], c["kit_code"], c["version_no"]) for c in cand] == [(ver["id"], "АТХ", 1)]
    assert "total" not in cand[0] and st.wh.query("pull")["result"]["need_candidates"] == []
    # Права: закупка формирует; склад и ГИП — нет.
    assert st.wh.cmd("need.generate", {"id": new_id(), "calc_version_id": ver["id"]})["status"] == "denied"
    assert st.proj.gip.cmd("need.generate", {"id": new_id(), "calc_version_id": ver["id"]})["status"] == "denied"
    need = st.pr.ok("need.generate", {"id": new_id(), "calc_version_id": ver["id"]})["need"]
    assert (need["calc_version_no"], need["line_count"], need["matched_count"], need["superseded"]) == (1, 4, 1, False)
    ln = need["lines"]
    assert ln[0]["item_id"] == st.item and ln[0]["match_note"] is None
    assert "пересчёт не выполняется" in ln[1]["match_note"] and ln[1]["item_id"] is None
    assert "Нет номенклатуры" in ln[2]["match_note"] and "Нет обозначения" in ln[3]["match_note"]
    # Цен в потребности нет (A-22), покрытие: неизвестно ≠ 0 для несопоставленных.
    assert "unit_price" not in ln[0] and "amount" not in ln[0]
    assert ln[0]["coverage"] == {"required": "120.0000", "reserved": "0.0000", "open": "120.0000", "available": "0.0000",
                                 "can_cover": False}
    assert ln[2]["coverage"]["available"] is None and ln[2]["coverage"]["can_cover"] is None
    # Одна потребность на версию.
    r = st.pr.cmd("need.generate", {"id": new_id(), "calc_version_id": ver["id"]})
    assert (r["code"], r["details"]["need_id"]) == ("need_exists", need["id"])
    assert st.pr.query("pull")["result"]["need_candidates"] == [] and need["project_code"] == "П-100"
    # ГИП/РП проекта видят потребность (без складских остатков — «неизвестно»), посторонний — нет.
    g = st.proj.gip.query("pull")["result"]["needs"]
    assert len(g) == 1 and g[0]["lines"][0]["coverage"]["available"] is None
    assert st.proj.lead.query("pull")["result"]["needs"] == []
    assert audit(world, otype="procurement_need", oid=need["id"])[0]["command"] == "need.generate"


def test_need_rejects_superseded_version_and_marks_old_need(st):
    v1 = _freeze(st.proj, [calc_line("Кабель", "КВВГ-4x1.5", "м", "10")])
    need = st.pr.ok("need.generate", {"id": new_id(), "calc_version_id": v1["id"]})["need"]
    st.proj.gip.ok("calc.save_draft", {"id": st.proj.kit, "lines": [calc_line("Кабель", "КВВГ-4x1.5", "м", "12")],
                                       "currency": "RUB"}, expected_version=1)
    st.proj.gip.ok("calc.freeze", {"id": st.proj.kit, "reason": "Изм. 1"}, expected_version=2)
    r = st.pr.cmd("need.generate", {"id": new_id(), "calc_version_id": v1["id"]})
    assert r["code"] == "need_exists"
    old = [n for n in st.pr.query("pull")["result"]["needs"] if n["id"] == need["id"]][0]
    assert (old["superseded"], old["latest_calc_version_no"]) == (True, 2)


def test_reserve_under_need_line_is_capped_by_open_quantity(world, st):
    ver = _freeze(st.proj, [calc_line("Кабель", "КВВГ-4x1.5", "м", "10"), calc_line("Шкаф", "ШК-1", "шт", "1")])
    need = st.pr.ok("need.generate", {"id": new_id(), "calc_version_id": ver["id"]})["need"]
    l0, l1 = need["lines"][0]["line_id"], need["lines"][1]["line_id"]
    receive(st.wh, st, "50")
    assert reserve(st.wh, st, "11", need_id=need["id"], need_line_id=l0)["code"] == "exceeds_need"
    res = reserve(st.wh, st, "6", need_id=need["id"], need_line_id=l0)["result"]["reservation"]
    issue(st.wh, st, "2", reservation_id=res["id"])
    assert reserve(st.wh, st, "4.0001", need_id=need["id"], need_line_id=l0)["code"] == "exceeds_need"
    assert reserve(st.wh, st, "1", need_id=need["id"], need_line_id=l1)["code"] == "need_line_unmatched"
    assert reserve(st.wh, st, "1", need_id=need["id"])["code"] == "invalid_id"     # строка обязательна вместе с потребностью
    cov = [n for n in st.pr.query("pull")["result"]["needs"]][0]["lines"][0]["coverage"]
    # Покрыто 6 (2 выдано по резерву + 4 в резерве), открыто 4; доступно на складах 50 − 2 − 4 = 44.
    assert cov == {"required": "10.0000", "reserved": "6.0000", "open": "4.0000", "available": "44.0000", "can_cover": True}
    # Резерв под чужой проект по этой потребности — отказ.
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    other = new_id()
    rp.ok("project.start_rkd", {"id": other, "tender_id": tender, "code": "П-200", "title": "Другой"})
    r = st.wh.cmd("stock.reserve", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "project_id": other,
                                    "qty": "1", "reason": "x", "need_id": need["id"], "need_line_id": l0})
    assert r["code"] == "need_mismatch"


def test_stock_commands_are_online_only_in_contract(world):
    cat = world.svc.contracts.catalog["commands"]
    for c in ("stock.receive", "stock.issue", "stock.reverse", "stock.reserve", "stock.release", "need.generate"):
        assert cat[c]["offline"] == "online_required", c
    for c in ("warehouse.create", "warehouse.update", "item.create", "item.update"):
        assert cat[c]["offline"] == "queued", c


def test_history_of_movement_visible_to_stock_roles_only(st):
    m = receive(st.wh, st, "1")["result"]["movement"]
    h = st.pr.query("history", object_type="stock_movement", object_id=m["id"])
    assert h["status"] == "accepted" and h["result"][0]["details"]["qty"] == "1.0000"
    assert st.proj.gip.query("history", object_type="stock_movement", object_id=m["id"])["status"] == "denied"
