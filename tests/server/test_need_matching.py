"""Ручное сопоставление строк потребности с номенклатурой (need.match_line, С4 часть 2, A-25).

Стенд — как в test_stock: wh1 — склад, pr1 — закупка, проект с ГИП/РП (фикстуры st, proj). Потребность из
четырёх строк: кабель в метрах (сопоставлен автоматически), тот же кабель в километрах (единица не совпала),
шкаф без номенклатуры, строка без обозначения.
"""
import json

import pytest
import sqlalchemy as sa

from conftest import new_id
from test_stock import _freeze, calc_line, receive, reserve, st  # noqa: F401  (фикстура st)
from test_tasks_documents import audit, proj  # noqa: F401  (фикстура proj)
from vector.server import db


@pytest.fixture
def nd(st):  # noqa: F811
    """Потребность и позиции номенклатуры для ручного сопоставления: (st, need, шкаф шт, реле шт)."""
    ver = _freeze(st.proj, [calc_line("Кабель", "КВВГ-4x1.5", "м", "120"), calc_line("Кабель, км", "КВВГ-4x1.5", "км", "1"),
                            calc_line("Шкаф", "ШК-1", "шт", "1"), calc_line("Без кода", None, "шт", "2")])
    need = st.pr.ok("need.generate", {"id": new_id(), "calc_version_id": ver["id"]})["need"]
    cab, rel = new_id(), new_id()
    st.wh.ok("item.create", {"id": cab, "code": "ШК-1-И", "name": "Шкаф ШК-1 (исп. 2)", "unit": "шт"})
    st.wh.ok("item.create", {"id": rel, "code": "РЕЛЕ-1", "name": "Реле", "unit": "шт"})
    return st, need, cab, rel


def match(s, need, line_no, item, reason="Аналог по паспорту", version=None, **kw):
    """need.match_line для строки №line_no (0…3) потребности need."""
    return s.cmd("need.match_line", {"id": new_id(), "need_id": need["id"], "line_id": need["lines"][line_no]["line_id"],
                                     "item_id": item, "reason": reason, **kw},
                 expected_version=need["version"] if version is None else version)


def test_procurement_matches_unmatched_line_and_reserve_becomes_possible(world, nd):
    st, need, cab, _ = nd
    assert (need["matched_count"], need["lines"][2]["item_id"], need["lines"][2]["match_source"]) == (1, None, None)
    assert need["lines"][0]["match_source"] == "auto"
    r = match(st.pr, need, 2, cab, reason="Шкаф в номенклатуре заведён как исполнение 2")
    assert r["status"] == "accepted", r
    n = r["result"]["need"]
    ln = n["lines"][2]
    assert (n["matched_count"], n["version"]) == (2, need["version"] + 1)
    assert (ln["item_id"], ln["match_source"], ln["match_note"], ln["match_count"]) == (cab, "manual", None, 1)
    assert ln["match_reason"] == "Шкаф в номенклатуре заведён как исполнение 2"
    assert "Нет номенклатуры" in ln["auto_match_note"]          # причина автомата сохранена для истории
    # Теперь склад может резервировать под строку — только этой позицией.
    receive(st.wh, st, "3", item=cab)
    ok = st.wh.cmd("stock.reserve", {"id": new_id(), "warehouse_id": st.w, "item_id": cab, "project_id": st.proj.pid,
                                     "qty": "1", "reason": "Под шкаф", "need_id": n["id"], "need_line_id": ln["line_id"]})
    assert ok["status"] == "accepted", ok
    wrong = reserve(st.wh, st, "1", need_id=n["id"], need_line_id=ln["line_id"])      # другая позиция (кабель)
    assert wrong["code"] == "need_mismatch"
    # Аудит: что было, что стало, почему, номер решения.
    a = [json.loads(x["details_json"]) for x in audit(world, otype="procurement_need", oid=need["id"])
         if x["command"] == "need.match_line"]
    assert a[0]["item_code"] == "ШК-1-И" and a[0]["previous_item_code"] is None
    assert a[0]["seq"] == 1 and "исполнение 2" in a[0]["reason"]


def test_only_procurement_may_match(world, nd):
    st, need, cab, _ = nd
    for s in (st.wh, st.proj.gip, st.proj.rp, world.admin):
        assert match(s, need, 2, cab)["status"] == "denied"


def test_unit_must_match_and_item_must_be_active(nd):
    st, need, cab, _ = nd
    # Строка 1 — кабель в км; номенклатура кабеля в м: пересчёт единиц не придумываем.
    r = match(st.pr, need, 1, st.item)
    assert r["code"] == "unit_mismatch" and "км" in r["message_ru"]
    off = st.wh.ok("item.update", {"id": cab, "active": False}, expected_version=1)["stock_item"]
    assert off["active"] is False
    assert match(st.pr, need, 2, cab)["code"] == "item_inactive"


def test_reason_version_and_same_item_are_checked(nd):
    st, need, cab, rel = nd
    assert match(st.pr, need, 2, cab, reason="")["code"] == "field_required"
    assert match(st.pr, need, 2, cab, version=need["version"] + 5)["code"] == "version_conflict"
    assert st.pr.cmd("need.match_line", {"id": new_id(), "need_id": need["id"], "line_id": need["lines"][2]["line_id"],
                                         "item_id": cab, "reason": "x"})["code"] == "expected_version_required"
    n = match(st.pr, need, 2, cab)["result"]["need"]
    assert match(st.pr, n, 2, cab)["code"] == "already_matched"
    assert match(st.pr, n, 0, st.item)["code"] == "already_matched"            # автоматическое — то же самое
    assert match(st.pr, n, 2, cab, extra=1)["code"] == "unknown_fields"


def test_rematch_keeps_history_and_is_blocked_while_reserved(world, nd):
    st, need, cab, rel = nd
    n1 = match(st.pr, need, 3, cab, reason="Первое решение")["result"]["need"]
    n2 = match(st.pr, n1, 3, rel, reason="Ошиблись: это реле")["result"]["need"]
    ln = n2["lines"][3]
    assert (ln["item_id"], ln["match_count"], ln["match_reason"]) == (rel, 2, "Ошиблись: это реле")
    assert n2["matched_count"] == 2
    # Пересопоставить автоматически сопоставленную строку тоже можно (ошибка в коде расчёта) — с причиной.
    kab_m = new_id()
    st.wh.ok("item.create", {"id": kab_m, "code": "КВВГнг-4x1.5", "name": "Кабель КВВГнг", "unit": "м"})
    n3 = match(st.pr, n2, 0, kab_m, reason="В проекте нужен негорючий")["result"]["need"]
    assert n3["lines"][0]["match_source"] == "manual" and n3["lines"][0]["item_id"] == kab_m
    # Под строку есть резерв → пересопоставление запрещено, пока резерв не снят.
    receive(st.wh, st, "5", item=rel)
    res = st.wh.ok("stock.reserve", {"id": new_id(), "warehouse_id": st.w, "item_id": rel, "project_id": st.proj.pid,
                                     "qty": "2", "reason": "x", "need_id": n3["id"], "need_line_id": ln["line_id"]})
    r = match(st.pr, n3, 3, cab)
    assert r["code"] == "need_line_reserved" and r["details"]["reserved"] == "2.0000"
    st.wh.ok("stock.release", {"id": res["reservation"]["id"], "reason": "Пересмотр"}, expected_version=1)
    assert match(st.pr, n3, 3, cab, reason="Вернули шкаф")["status"] == "accepted"
    # История в БД неизменяема: три записи по строке 3 нельзя исправить или удалить.
    t = db.procurement_need_line_matches
    with world.svc.engine.connect() as c:
        seqs = [r.seq for r in c.execute(sa.select(t.c.seq).where(t.c.line_id == ln["line_id"]).order_by(t.c.seq))]
    assert seqs == [1, 2, 3]
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as c:
            c.execute(t.update().values(reason="подмена"))
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as c:
            c.execute(t.delete())


def test_project_leads_see_manual_match_without_stock(nd):
    st, need, cab, _ = nd
    match(st.pr, need, 2, cab)
    g = [n for n in st.proj.gip.query("pull")["result"]["needs"] if n["id"] == need["id"]][0]
    ln = g["lines"][2]
    assert (ln["item_id"], ln["match_source"]) == (cab, "manual")
    assert ln["coverage"]["available"] is None                 # склад не виден ГИП: «неизвестно», а не 0


def test_match_is_online_only_and_idempotent(world, nd):
    st, need, cab, _ = nd
    assert world.svc.contracts.catalog["commands"]["need.match_line"]["offline"] == "online_required"
    op = new_id()
    p = {"id": new_id(), "need_id": need["id"], "line_id": need["lines"][2]["line_id"], "item_id": cab, "reason": "x"}
    r1 = st.pr.cmd("need.match_line", p, expected_version=need["version"], op_id=op)
    r2 = st.pr.cmd("need.match_line", p, expected_version=need["version"], op_id=op)
    assert r1["status"] == "accepted" and r2.get("replayed") is True and r2["result"] == r1["result"]
