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
    """Складской набор теста: проект, склады, позиции, сеансы кладовщика и закупщика."""
    proj: object
    wh: object
    wh2: object
    pr: object
    w: str
    item: str


@pytest.fixture
def st(world, proj):  # noqa: F811
    """Фикстура: пользователи склада и закупки, два склада и позиция номенклатуры."""
    world.make_user("wh1", "warehouse")
    world.make_user("wh2", "warehouse")
    world.make_user("pr1", "procurement")
    wh = world.login("wh1")
    w, item = new_id(), new_id()
    wh.ok("warehouse.create", {"id": w, "code": "СК-1", "name": "Центральный склад"})
    wh.ok("item.create", {"id": item, "code": "КВВГ-4x1.5", "name": "Кабель КВВГ 4×1,5", "unit": "м"})
    return St(proj, wh, world.login("wh2"), world.login("pr1"), w, item)


def receive(s, st, qty, item=None, w=None, src="ТН-1"):
    """Приход qty на склад (по умолчанию основной склад и позиция)."""
    return s.cmd("stock.receive", {"id": new_id(), "warehouse_id": w or st.w, "item_id": item or st.item, "qty": qty,
                                   "source_doc": src})


def issue(s, st, qty, **kw):
    """Выдача qty под проект (или под резерв, если передан reservation_id)."""
    p = {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "qty": qty, "reason": "Монтаж"}
    if "reservation_id" not in kw:
        p["project_id"] = st.proj.pid
    p.update(kw)
    return s.cmd("stock.issue", p)


def reserve(s, st, qty, **kw):
    """Резерв qty под проект теста."""
    return s.cmd("stock.reserve", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "project_id": st.proj.pid,
                                   "qty": qty, "reason": "Под монтаж", **kw})


def bal(s, st):
    """(остаток, резерв, доступно) по складу и позиции из pull или None."""
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
    """Запустить команды разных пользователей одновременно (барьер) — проверка, что параллельные проводки не уводят
    остаток в минус.
    """
    results, barrier = [], threading.Barrier(len(tokens))

    def worker(tok, cmd, p):
        """Дождаться остальных потоков и отправить команду."""
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
    """Сохранить черновик расчёта и зафиксировать его ГИП; вернуть версию расчёта."""
    proj.gip.ok("calc.save_draft", {"id": proj.kit, "lines": lines, "currency": "RUB"}, expected_version=0)
    return proj.gip.ok("calc.freeze", {"id": proj.kit, "reason": "Выпуск"}, expected_version=1)["calc_version"]


def calc_line(name, code, unit, qty, price="10"):
    """Позиция расчёта с кодом и единицей (для сопоставления с номенклатурой)."""
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
    for c in ("stock.receive", "stock.issue", "stock.reverse", "stock.transfer", "stock.transfer_accept",
              "stock.transfer_cancel", "stock.reserve", "stock.release",
              "need.generate"):
        assert cat[c]["offline"] == "online_required", c
    for c in ("warehouse.create", "warehouse.update", "item.create", "item.update"):
        assert cat[c]["offline"] == "queued", c


def test_history_of_movement_visible_to_stock_roles_only(st):
    m = receive(st.wh, st, "1")["result"]["movement"]
    h = st.pr.query("history", object_type="stock_movement", object_id=m["id"])
    assert h["status"] == "accepted" and h["result"][0]["details"]["qty"] == "1.0000"
    assert st.proj.gip.query("history", object_type="stock_movement", object_id=m["id"])["status"] == "denied"


# ====================================================================== перемещение между складами (A-28, D-29)
def second_warehouse(st, code="СК-2"):
    """Второй склад для перемещений."""
    w2 = new_id()
    st.wh.ok("warehouse.create", {"id": w2, "code": code, "name": "Склад участка"})
    return w2


def transfer(s, st, qty, src=None, dst=None, **kw):
    """Отгрузка qty позиции теста со склада src (по умолчанию основной) на dst: перемещение «В пути»."""
    return s.cmd("stock.transfer", {"id": new_id(), "item_id": st.item, "from_warehouse_id": src or st.w,
                                    "to_warehouse_id": dst, "qty": qty, "reason": "На участок монтажа", **kw})


def accept(s, t, **kw):
    """Приёмка перемещения t (результат transfer) на складе-получателе, с версией перемещения."""
    return s.cmd("stock.transfer_accept", {"id": t["id"], **kw}, expected_version=t["version"])


def give_back(s, t, reason="Не тот склад", **kw):
    """Возврат перемещения t отправителю (пока оно в пути)."""
    return s.cmd("stock.transfer_cancel", {"id": t["id"], "reason": reason, **kw}, expected_version=t["version"])


def bal_at(s, st, w):
    """(остаток, резерв, доступно) позиции теста на складе w."""
    r = s.query("pull")["result"]
    b = [x for x in r["stock_balances"] if x["warehouse_id"] == w and x["item_id"] == st.item]
    return (b[0]["on_hand"], b[0]["reserved"], b[0]["available"]) if b else None


def test_transfer_ships_in_transit_then_receiver_accepts(world, st):
    w2 = second_warehouse(st)
    receive(st.wh, st, "10")
    r = transfer(st.wh, st, "4", dst=w2, source_doc="Накладная на перемещение № 7")
    assert r["status"] == "accepted", r
    t, (out,) = r["result"]["transfer"], r["result"]["movements"]
    assert (t["status"], t["status_label"], t["qty"], t["version"]) == ("in_transit", "В пути", "4.0000", 1)
    assert (out["kind"], out["warehouse_id"], out["kind_label"]) == ("issue", st.w, "Расход по перемещению")
    assert (out["transfer_direction"], out["transfer_peer_warehouse_id"], out["project_id"]) == ("out", w2, None)
    # В пути: у отправителя уже нет, у получателя ещё нет — выдать или зарезервировать на получателе нечего.
    assert bal_at(st.wh, st, st.w) == ("6.0000", "0.0000", "6.0000")
    assert bal_at(st.wh, st, w2) is None
    assert reserve(st.wh, st, "1", warehouse_id=w2)["code"] == "insufficient_available"
    pulled = st.pr.query("pull")["result"]
    assert [(x["id"], x["status"]) for x in pulled["stock_transfers"]] == [(t["id"], "in_transit")]
    assert world.svc.ledger_mismatches() == []
    # Приёмка: приход на получателе, «Принято»; повторно принять или вернуть — нельзя.
    a = accept(st.wh2, t, comment="Принято без замечаний")
    assert a["status"] == "accepted", a
    t2, (inn,) = a["result"]["transfer"], a["result"]["movements"]
    assert (t2["status"], t2["version"], t2["close_reason"]) == ("received", 2, "Принято без замечаний")
    assert (inn["kind"], inn["warehouse_id"], inn["kind_label"], inn["transfer_peer_warehouse_id"]) == \
        ("receipt", w2, "Приход по перемещению", st.w)
    assert bal_at(st.wh, st, w2) == ("4.0000", "0.0000", "4.0000")
    assert accept(st.wh, t2)["code"] == "transfer_state"
    assert give_back(st.wh, t2)["code"] == "transfer_state"
    assert accept(st.wh, t)["status"] == "conflict"                    # устаревшая версия — конфликт, не повтор
    # pull: оба движения помечены перемещением; история — у ролей склада.
    mv = {m["id"]: m for m in st.pr.query("pull")["result"]["stock_movements"]}
    assert mv[out["id"]]["transfer_id"] == mv[inn["id"]]["transfer_id"] == t["id"]
    h = st.pr.query("history", object_type="stock_transfer", object_id=t["id"])
    assert h["status"] == "accepted" and [x["details"]["status"] for x in h["result"]] == ["in_transit", "received"]
    assert st.proj.gip.query("history", object_type="stock_transfer", object_id=t["id"])["status"] == "denied"
    assert world.svc.ledger_mismatches() == []


def test_transfer_returned_to_sender_while_in_transit(world, st):
    w2 = second_warehouse(st)
    receive(st.wh, st, "5")
    t = transfer(st.wh, st, "5", dst=w2)["result"]["transfer"]
    assert give_back(st.wh, t, reason=" ")["code"] == "field_required"          # причина возврата обязательна
    r = give_back(st.wh, t, reason="Отгрузили не на тот склад")
    assert r["status"] == "accepted", r
    t2, (ret,) = r["result"]["transfer"], r["result"]["movements"]
    assert (t2["status"], t2["status_label"]) == ("returned", "Возвращено отправителю")
    assert (ret["kind"], ret["warehouse_id"], ret["kind_label"]) == ("receipt", st.w, "Возврат по перемещению")
    assert bal_at(st.wh, st, st.w) == ("5.0000", "0.0000", "5.0000") and bal_at(st.wh, st, w2) is None
    assert accept(st.wh, t2)["code"] == "transfer_state"
    assert world.svc.ledger_mismatches() == []


def test_transfer_rules_and_rights(world, st):
    w2 = second_warehouse(st)
    receive(st.wh, st, "5")
    assert reserve(st.wh, st, "3")["status"] == "accepted"
    # Только доступное: резерв закреплён за складом и не «уезжает».
    r = transfer(st.wh, st, "3", dst=w2)
    assert (r["status"], r["code"]) == ("rejected", "insufficient_available")
    assert transfer(st.wh, st, "1", dst=st.w)["code"] == "same_warehouse"
    assert transfer(st.wh, st, "1", dst=w2, reason=" ")["code"] == "field_required"   # причина обязательна
    assert transfer(st.wh, st, "1,5", dst=w2)["status"] == "rejected"           # формат количества — как у выдачи
    for who in (st.pr, world.admin, st.proj.gip):                              # закупка, admin, ГИП — не проводят
        assert transfer(who, st, "1", dst=w2)["status"] == "denied"
    t = transfer(st.wh, st, "1", dst=w2)["result"]["transfer"]
    for who in (st.pr, world.admin):
        assert accept(who, t)["status"] == "denied" and give_back(who, t)["status"] == "denied"
    # Получатель отключили, пока товар в пути: принять нельзя (подсказка — вернуть), вернуть можно.
    st.wh.ok("warehouse.update", {"id": w2, "active": False}, expected_version=1)
    assert transfer(st.wh, st, "1", dst=w2)["code"] == "warehouse_inactive"
    r = accept(st.wh, t)
    assert r["code"] == "warehouse_inactive" and "верните" in r["message_ru"]
    assert give_back(st.wh, t)["status"] == "accepted"
    # С отключённого склада отгружать можно (так освобождают закрываемый склад), и возврат на него тоже проходит.
    st.wh.ok("warehouse.update", {"id": w2, "active": True}, expected_version=2)
    t = transfer(st.wh, st, "2", dst=w2)["result"]["transfer"]
    accept(st.wh, t)
    st.wh.ok("warehouse.update", {"id": w2, "active": False}, expected_version=3)
    back = transfer(st.wh, st, "2", src=w2, dst=st.w)["result"]["transfer"]
    assert accept(st.wh, back)["status"] == "accepted"
    assert bal_at(st.wh, st, w2) == ("0.0000", "0.0000", "0.0000")
    assert bal_at(st.wh, st, st.w) == ("5.0000", "3.0000", "2.0000")
    assert world.svc.ledger_mismatches() == []


def test_transfer_movements_cannot_be_reversed(world, st):
    w2 = second_warehouse(st)
    rc = receive(st.wh, st, "3")["result"]["movement"]
    t = transfer(st.wh, st, "3", dst=w2)["result"]
    out = t["movements"][0]
    r = st.wh.cmd("stock.reverse", {"id": new_id(), "movement_id": out["id"], "reason": "Ошибка"})
    assert (r["code"], "в пути" in r["message_ru"]) == ("transfer_leg", True)
    inn = accept(st.wh, t["transfer"])["result"]["movements"][0]
    for m in (out, inn):
        r = st.wh.cmd("stock.reverse", {"id": new_id(), "movement_id": m["id"], "reason": "Ошибка"})
        assert (r["status"], r["code"]) == ("rejected", "transfer_leg"), r
        assert "СК-2 → СК-1" in r["message_ru"]
    # Приход на отправителе уже не сторнировать: товар уехал (доступно 0). Исправление — обратное перемещение.
    assert st.wh.cmd("stock.reverse", {"id": new_id(), "movement_id": rc["id"], "reason": "Ошибка"})["status"] == "rejected"
    back = transfer(st.wh, st, "3", src=w2, dst=st.w, reason="Ошибочное перемещение")["result"]["transfer"]
    accept(st.wh, back)
    assert st.wh.cmd("stock.reverse", {"id": new_id(), "movement_id": rc["id"], "reason": "Ошибка"})["status"] == "accepted"
    assert bal_at(st.wh, st, st.w) == ("0.0000", "0.0000", "0.0000")
    assert world.svc.ledger_mismatches() == []


def test_transfer_is_idempotent_and_ids_are_unique(world, st):
    w2 = second_warehouse(st)
    receive(st.wh, st, "5")
    p = {"id": new_id(), "item_id": st.item, "from_warehouse_id": st.w, "to_warehouse_id": w2, "qty": "2", "reason": "x"}
    op = new_id()
    a = st.wh.cmd("stock.transfer", p, op_id=op)
    b = st.wh.cmd("stock.transfer", p, op_id=op)        # повтор той же операции (обрыв связи) — тот же итог
    assert a["status"] == b["status"] == "accepted" and a["result"] == b["result"]
    assert st.wh.cmd("stock.transfer", p)["code"] == "id_exists"  # новая операция с тем же id перемещения — отказ
    assert bal_at(st.wh, st, st.w) == ("3.0000", "0.0000", "3.0000")
    op = new_id()
    t = a["result"]["transfer"]
    x = st.wh.cmd("stock.transfer_accept", {"id": t["id"]}, expected_version=1, op_id=op)
    y = st.wh.cmd("stock.transfer_accept", {"id": t["id"]}, expected_version=1, op_id=op)
    assert x["status"] == y["status"] == "accepted" and x["result"] == y["result"]
    assert bal_at(st.wh, st, w2) == ("2.0000", "0.0000", "2.0000")


def test_concurrent_transfers_of_last_units_only_one_wins(world, st):
    w2, w3 = second_warehouse(st), second_warehouse(st, "СК-3")
    receive(st.wh, st, "1")
    mk = lambda dst: ("stock.transfer", {"id": new_id(), "item_id": st.item, "from_warehouse_id": st.w,  # noqa: E731
                                         "to_warehouse_id": dst, "qty": "1", "reason": "Гонка"})
    results = _race(world, [st.wh.token, st.wh2.token], [mk(w2), mk(w3)])
    assert sorted(r["status"] for r in results) == ["accepted", "rejected"], results
    assert world.svc.ledger_mismatches() == []


def test_concurrent_accept_and_return_only_one_wins(world, st):
    w2 = second_warehouse(st)
    receive(st.wh, st, "2")
    t = transfer(st.wh, st, "2", dst=w2)["result"]["transfer"]
    results = _race(world, [st.wh.token, st.wh2.token],
                    [("stock.transfer_accept", {"id": t["id"]}), ("stock.transfer_cancel", {"id": t["id"], "reason": "x"})])
    assert sorted(r["status"] for r in results) in (["accepted", "rejected"], ["accepted", "conflict"]), results
    on_hand = (bal_at(st.wh, st, st.w)[0], (bal_at(st.wh, st, w2) or ("0.0000",))[0])
    assert on_hand in (("0.0000", "2.0000"), ("2.0000", "0.0000"))     # товар ровно в одном месте
    assert world.svc.ledger_mismatches() == []


def test_legacy_one_step_transfers_stay_visible_and_protected(world, st):
    """Перемещения, проведённые версией со схемой v8 (одним шагом, таблица stock_transfers), после обновления видны
    как «Принято» без действий, и их движения по-прежнему нельзя сторнировать по отдельности."""
    w2 = second_warehouse(st)
    a = receive(st.wh, st, "2")["result"]["movement"]
    b = receive(st.wh, st, "2", w=w2)["result"]["movement"]
    tid = new_id()
    with world.svc.engine.begin() as c:     # запись, как её оставила версия v8 (связка двух движений)
        c.execute(db.stock_transfers.insert().values(
            id=tid, item_id=st.item, from_warehouse_id=st.w, to_warehouse_id=w2, qty=Decimal("2"),
            out_movement_id=a["id"], in_movement_id=b["id"], reason="v8", posted_by=st.wh.user_id,
            posted_at=world.clock.now()))
    r = st.wh.query("pull")["result"]
    legacy = [t for t in r["stock_transfers"] if t["id"] == tid]
    assert legacy and (legacy[0]["status"], legacy[0]["legacy"], legacy[0]["version"]) == ("received", True, None)
    mv = {m["id"]: m for m in r["stock_movements"]}
    assert (mv[a["id"]]["kind_label"], mv[b["id"]]["kind_label"]) == ("Расход по перемещению", "Приход по перемещению")
    rv = st.wh.cmd("stock.reverse", {"id": new_id(), "movement_id": a["id"], "reason": "x"})
    assert rv["code"] == "transfer_leg"
