"""Расчёт комплекта на клиенте (Class A, SQLite): офлайн-сохранения через очередь, цепочка версий, конфликт без
слияния, фиксация только онлайн, неизменяемые версии и ревизии в зашифрованном кэше, представление для интерфейса."""
import sqlite3
import uuid

import pytest

from conftest import new_id
from test_offline_sync import BACKENDS, FAST_KDF, SERVER, count, mk  # noqa: F401  (фикстура mk и общие константы)
from vector.client import calc_view

_ = (BACKENDS, FAST_KDF, SERVER)


def line(name="Кабель", qty="10", price="12.5", **kw):
    return {"line_id": kw.pop("line_id", str(uuid.uuid4())), "name": name, "unit": "м", "qty": qty, "unit_price": price, **kw}


@pytest.fixture
def team(world, mk):
    """Проект: gip1 — ГИП, rp1 — РП, eng1 — участник; один комплект. Клиенты eng1, gip1 и rp1."""
    s = world.login("rp1")
    _, tender, _ = world.won_tender(s)
    pid, kit = new_id(), new_id()
    s.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-9", "title": "Проект"})
    for who, role in (("gip1", "gip"), ("rp1", "rp")):
        world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users[who], "project_role": role, "reason": "Приказ"})
    s.ok("project.assign", {"project_id": pid, "user_id": world.users["eng1"], "project_role": "participant", "reason": "Работы"})
    s.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Автоматизация"})
    eng, gip, rp = mk("eng1"), mk("gip1"), mk("rp1")
    for c in (eng, gip, rp):
        c.pull()
    return pid, kit, eng, gip, rp


def test_offline_saves_are_chained_and_sync_without_conflict(world, team):
    pid, kit, eng, gip, rp = team
    eng.t.online = False
    a = line(name="Шкаф", qty="1", price="50000")
    assert eng.save_calc_draft(kit, "RUB", None, [a]).status == "queued"
    # Второе и третье сохранения без связи: каждое основано на предыдущем (ev 0 → 1 → 2).
    assert eng.save_calc_draft(kit, "RUB", None, [a, line()]).status == "queued"
    assert eng.save_calc_draft(kit, "RUB", "уточнено", [a, line(qty="12")]).status == "queued"
    assert [q["command"] for q in eng.queue()] == ["calc.save_draft"] * 3
    local = eng.get_local("calc_draft", kit)
    assert local["_pending"] and local["note"] == "уточнено" and len(local["lines"]) == 2
    assert count(world, "calc_drafts") == 0
    eng.t.online = True
    rep = eng.sync()
    assert (rep.accepted, rep.stopped_on) == (3, None)
    d = eng.get_local("calc_draft", kit)
    assert d["version"] == 3 and not d["_pending"] and d["total"] == "50150.0000"


def test_concurrent_offline_edit_is_conflict_not_merge(world, team):
    pid, kit, eng, gip, rp = team
    eng.save_calc_draft(kit, "RUB", None, [line(name="Исходная")])
    rp.pull()
    rp.t.online = False
    rp.save_calc_draft(kit, "RUB", None, [line(name="Правка РП")])            # основана на версии 1
    eng.save_calc_draft(kit, "RUB", None, [line(name="Правка инженера")])     # онлайн: сервер → версия 2
    rp.t.online = True
    rep = rp.sync()
    assert rep.stop_status == "conflict"
    prob = rp.problems()[0]
    assert prob["server_current"]["lines"][0]["name"] == "Правка инженера"   # видно, что на сервере
    # Решение за пользователем: «повторить поверх» — новая операция на текущей версии.
    r = rp.reapply_on_current(prob["operation_id"])
    assert r.status == "accepted"
    assert rp.get_local("calc_draft", kit)["lines"][0]["name"] == "Правка РП"


def test_freeze_is_online_only_and_results_cached_encrypted(world, team):
    pid, kit, eng, gip, rp = team
    eng.save_calc_draft(kit, "RUB", None, [line(qty="2", price="100")])
    gip.pull()
    gip.t.online = False
    r = gip.execute("calc.freeze", {"id": kit, "reason": "v1"}, expected_version=1)
    assert r.status == "refused_offline" and gip.pending_count() == 0
    gip.t.online = True
    r = gip.execute("calc.freeze", {"id": kit, "reason": "Расчёт к ТКП"}, expected_version=1)
    assert r.status == "accepted"
    # Версия и ревизия сразу в локальных списках (без pull) и видны офлайн.
    gip.t.online = False
    assert gip.get_meta("calc_versions")[0]["version_no"] == 1
    assert gip.get_meta("calc_revisions")[0]["totals"][0]["total"] == "200.0000"
    raw = sqlite3.connect(str(gip.ov.path / "local.db")).execute("SELECT v_enc FROM meta WHERE k='calc_revisions'").fetchone()[0]
    assert "Расчёт к ТКП".encode() not in (raw if isinstance(raw, bytes) else raw.encode())
    # Участник получает версии, но не ревизии итога (матрица прав).
    eng.pull()
    assert len(eng.get_meta("calc_versions")) == 1 and eng.get_meta("calc_revisions") == []


def test_kit_calc_view_diff_and_labels(world, team):
    pid, kit, eng, gip, rp = team
    keep, change = line(name="Шкаф", qty="1", price="1000"), line(name="Кабель", qty="10", price="5")
    eng.save_calc_draft(kit, "RUB", None, [keep, change])
    gip.pull()
    gip.execute("calc.freeze", {"id": kit, "reason": "v1"}, expected_version=1)
    eng.pull()
    eng.t.online = False
    eng.save_calc_draft(kit, "RUB", None, [keep, dict(change, qty="12"), line(name="Реле", qty="2", price=None)])
    people = {p["id"]: p["display_name"] for p in eng.get_meta("people")}
    v = calc_view.kit_calc({"id": kit}, eng.get_local("calc_draft", kit), eng.get_meta("calc_versions"), people)
    assert v["pending"] and v["last"]["label"] == "v1" and v["last"]["author_name"] == "GIP1"
    assert v["preview"]["total_label"] == "1 060,00 RUB · 1 позиция без цены (итог неполный)"
    kinds = {r["name"]: r["kind"] for r in v["diff_rows"]}
    assert kinds == {"Шкаф": "unchanged", "Кабель": "changed", "Реле": "added"}
    ch = next(r for r in v["diff_rows"] if r["name"] == "Кабель")
    assert "Количество: 10 → 12" in ch["changes_label"]
    assert "добавлено 1, изменено 1" in v["diff_summary"] and "Разница итога неизвестна" in v["diff_summary"]
    assert not v["same_as_last"]


def test_preview_reports_line_problems_without_server():
    rows = [line(qty="0"), line(qty="0.333", price="100.01"), line(qty="2", price="3")]
    pv = calc_view.preview(rows, "RUB")
    assert not pv["valid"] and pv["problem_count"] == 2
    assert "больше нуля" in pv["rows"][0]["problem"] and "округления" in pv["rows"][1]["problem"]
    assert pv["rows"][2]["amount_label"] == "6,00" and pv["total"] == "6.0000"
    assert calc_view.preview([line()], "")["general_problems"] == [
        "Для расчёта с ценами укажите валюту — значение по умолчанию не подставляется."]


def test_project_calc_and_finance_views():
    revs = [{"id": 1, "project_id": "p", "project_code": "П-1", "project_title": "Т", "revision_no": 1, "reason": "a",
             "created_by": "u", "created_at": "2026-11-16T09:00:00.000000Z",
             "items": [{"kit_code": "АТХ", "version_no": 1, "total": "10.0000", "currency": "RUB", "unknown_price_count": 0}],
             "totals": [{"currency": "RUB", "total": "10.0000", "unknown_price_count": 0, "kits": 1, "complete": True}]},
            {"id": 2, "project_id": "p", "project_code": "П-1", "project_title": "Т", "revision_no": 2, "reason": "b",
             "created_by": "u", "created_at": "2026-11-17T09:00:00.000000Z",
             "items": [{"kit_code": "АТХ", "version_no": 2, "total": "12.0000", "currency": "RUB", "unknown_price_count": 0},
                       {"kit_code": "ЭМ", "version_no": 1, "total": "5.0000", "currency": "EUR", "unknown_price_count": 2}],
             "totals": [{"currency": "EUR", "total": "5.0000", "unknown_price_count": 2, "kits": 1, "complete": False},
                        {"currency": "RUB", "total": "12.0000", "unknown_price_count": 0, "kits": 1, "complete": True}]}]
    pc = calc_view.project_calc("p", revs, {"u": "Орлов"})
    assert pc["latest"]["label"] == "Ревизия r2" and pc["latest"]["composition"] == "АТХ v2, ЭМ v1"
    assert pc["latest"]["mixed_currencies"] and pc["latest"]["totals"][0]["label"].startswith("5,00 EUR · 2 позиции без цены")
    fo = calc_view.finance_overview(revs, {})
    assert len(fo) == 1 and fo[0]["revision_count"] == 2 and fo[0]["latest"]["revision_no"] == 2


@pytest.mark.parametrize("value,expected", [("1234567.5000", "1 234 567,5"), ("0.0000", "0"), ("-12.3400", "−12,34"), (None, "—")])
def test_fmt_decimal(value, expected):
    assert calc_view.fmt_decimal(value) == expected


def test_unchanged_save_is_not_queued(world, team):
    pid, kit, eng, gip, rp = team
    a = line(qty="2", price="10")
    eng.save_calc_draft(kit, "RUB", None, [a])
    eng.t.online = False
    r = eng.save_calc_draft(kit, "RUB", None, [dict(a, qty="2.0")])   # то же число в другой записи
    assert r.status == "accepted" and r.response == {"unchanged": True} and eng.pending_count() == 0
    assert eng.save_calc_draft(kit, "RUB", None, [dict(a, qty="3")]).status == "queued"
