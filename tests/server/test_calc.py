"""Расчёт комплекта → фиксация → ревизия проектного итога (сценарий С3; R-04, R-11, R-12).

Каждый тест закрепляет одно правило из server/calc.py; запускается на SQLite и на встроенном PostgreSQL.
Команда проекта (как в test_tasks_documents): gip1 — ГИП, rp1 — РП, eng1 — участник; lead1 — вне команды;
acc1 — бухгалтерия (глобальная роль, в проект не назначена).
"""
import uuid

import pytest
import sqlalchemy as sa

from conftest import new_id
from test_tasks_documents import Proj, audit  # noqa: F401  (фикстура proj ниже переиспользует стенд)
from test_tasks_documents import proj  # noqa: F401
from vector.server import db


def line(name="Кабель КВВГ 4×1,5", qty="10", price="125.5", unit="м", **kw):
    """Позиция расчёта для теста."""
    return {"line_id": kw.pop("line_id", str(uuid.uuid4())), "name": name, "unit": unit, "qty": qty, "unit_price": price, **kw}


def save(s, kit, lines, ev, currency="RUB", note=None):
    """Сохранить черновик расчёта комплекта с ожидаемой версией ev."""
    p = {"id": kit, "lines": lines, "currency": currency}
    if note is not None:
        p["note"] = note
    return s.cmd("calc.save_draft", p, expected_version=ev)


def pull(s):
    """Снимок pull пользователя (запрос обязан пройти)."""
    r = s.query("pull")
    assert r["status"] == "accepted", r
    return r["result"]


# ====================================================================== черновик
def test_participant_saves_draft_and_totals_are_exact(world, proj):
    r = save(proj.eng, proj.kit, [line(qty="2.5", price="1234.56"), line(name="Реле", qty="4", price=None)], ev=0)
    assert r["status"] == "accepted", r
    d = r["result"]["calc_draft"]
    assert d["id"] == proj.kit and d["version"] == 1 and d["currency"] == "RUB"
    # Неизвестная цена ≠ 0: в сумму не входит, итог помечен неполным.
    assert (d["total"], d["unknown_price_count"], d["complete"]) == ("3086.4000", 1, False)
    assert d["lines"][1]["unit_price"] is None and d["lines"][1]["amount"] is None
    # Черновик виден команде через pull; посторонний (вне проекта) не может его менять.
    assert pull(proj.rp)["calc_drafts"][0]["version"] == 1
    r = save(proj.lead, proj.kit, [line()], ev=1)
    assert r["status"] == "denied"


def test_draft_conflicts_are_not_merged(world, proj):
    a = save(proj.eng, proj.kit, [line(name="А")], ev=0)["result"]["calc_draft"]
    # Второй пользователь тоже думал, что черновика нет (офлайн) — конфликт, его данные не применены.
    r = save(proj.rp, proj.kit, [line(name="Б")], ev=0)
    assert r["status"] == "conflict" and r["details"]["current"]["lines"][0]["name"] == "А"
    # Правка поверх устаревшей версии — тоже конфликт.
    save(proj.eng, proj.kit, [line(name="А2")], ev=a["version"])
    r = save(proj.rp, proj.kit, [line(name="Б")], ev=a["version"])
    assert r["status"] == "conflict" and r["details"]["current"]["version"] == 2
    assert [x["name"] for x in pull(proj.rp)["calc_drafts"][0]["lines"]] == ["А2"]


def test_unchanged_save_is_accepted_without_new_version(world, proj):
    ln = line()
    save(proj.eng, proj.kit, [ln], ev=0)
    r = save(proj.eng, proj.kit, [dict(ln, qty="10.0000", unit_price="125.50")], ev=1)
    assert r["status"] == "accepted" and r["result"]["unchanged"] is True and r["result"]["calc_draft"]["version"] == 1
    assert len(audit(world, "calc.save_draft")) == 1   # повтор без изменений журнал не засоряет


def test_invalid_lines_rejected_with_line_numbers_and_nothing_changes(world, proj):
    save(proj.eng, proj.kit, [line(name="Было")], ev=0)
    r = save(proj.eng, proj.kit, [line(), line(qty="0"), line(qty="0.333", price="100.01")], ev=1)
    assert r["status"] == "rejected" and r["code"] == "calc_invalid"
    codes = {(p["line"], p["code"]) for p in r["details"]["problems"]}
    assert codes == {(2, "qty_not_positive"), (3, "amount_needs_rounding")}
    assert pull(proj.eng)["calc_drafts"][0]["lines"][0]["name"] == "Было"
    # Цена без валюты: валюта не подставляется.
    r = save(proj.eng, proj.kit, [line()], ev=1, currency=None)
    assert r["code"] == "calc_invalid" and r["details"]["problems"][0]["code"] == "currency_required"
    # float на входе запрещён.
    r = save(proj.eng, proj.kit, [line(qty=1.5)], ev=1)
    assert r["details"]["problems"][0]["code"] == "float_forbidden"


def test_expected_version_is_required_and_missing_draft(world, proj):
    r = proj.eng.cmd("calc.save_draft", {"id": proj.kit, "lines": [], "currency": None})
    assert r["code"] == "expected_version_required"
    r = save(proj.eng, proj.kit, [line()], ev=3)
    assert r["code"] == "calc_draft_missing"
    r = save(proj.eng, new_id(), [line()], ev=0)
    assert r["status"] == "rejected" and r["code"] == "not_found"


# ====================================================================== фиксация
def test_freeze_rights_reason_and_result(world, proj):
    save(proj.eng, proj.kit, [line(qty="2", price="100"), line(name="Шкаф", qty="1", price="50000")], ev=0)
    for s in (proj.eng, proj.rp, proj.lead):      # участник, РП, посторонний — не фиксируют (A-18)
        r = s.cmd("calc.freeze", {"id": proj.kit, "reason": "Согласовано"}, expected_version=1)
        assert r["status"] == "denied", r
    r = proj.gip.cmd("calc.freeze", {"id": proj.kit}, expected_version=1)
    assert r["code"] == "field_required" and r["details"]["field"] == "reason"     # причина обязательна
    res = proj.gip.ok("calc.freeze", {"id": proj.kit, "reason": "Расчёт к ТКП"}, expected_version=1)
    v, rev = res["calc_version"], res["calc_revision"]
    assert (v["version_no"], v["total"], v["line_count"], v["complete"]) == (1, "50200.0000", 2, True)
    assert len(v["content_hash"]) == 64 and v["content_hash"] == res["calc_draft"]["content_hash"]
    assert rev["revision_no"] == 1 and rev["items"][0]["version_no"] == 1
    assert rev["totals"] == [{"currency": "RUB", "total": "50200.0000", "unknown_price_count": 0, "kits": 1, "complete": True}]
    # Тот же черновик повторно не фиксируется — новая версия без изменений не нужна.
    r = proj.gip.cmd("calc.freeze", {"id": proj.kit, "reason": "Ещё раз"}, expected_version=1)
    assert r["code"] == "calc_nothing_to_freeze"


def test_freeze_fixes_exactly_the_seen_draft(world, proj):
    save(proj.eng, proj.kit, [line(name="А")], ev=0)
    save(proj.eng, proj.kit, [line(name="Б")], ev=1)            # коллега изменил черновик
    r = proj.gip.cmd("calc.freeze", {"id": proj.kit, "reason": "Фиксирую"}, expected_version=1)
    assert r["status"] == "conflict" and r["details"]["current"]["version"] == 2
    assert not pull(proj.gip)["calc_versions"]
    r = proj.gip.cmd("calc.freeze", {"id": proj.kit, "reason": "Фиксирую"})
    assert r["code"] == "expected_version_required"


def test_freeze_empty_or_missing_draft(world, proj):
    r = proj.gip.cmd("calc.freeze", {"id": proj.kit, "reason": "x"}, expected_version=0)
    assert r["code"] == "calc_draft_missing"
    save(proj.eng, proj.kit, [], ev=0, currency=None)
    r = proj.gip.cmd("calc.freeze", {"id": proj.kit, "reason": "x"}, expected_version=1)
    assert r["code"] == "calc_empty"


def test_project_total_only_from_frozen_versions_and_revisions_are_history(world, proj):
    kit_b = new_id()
    proj.rp.ok("kit.create", {"id": kit_b, "project_id": proj.pid, "code": "ЭМ", "title": "Электромонтаж"})
    save(proj.eng, proj.kit, [line(qty="1", price="1000")], ev=0)
    r1 = proj.gip.ok("calc.freeze", {"id": proj.kit, "reason": "АТХ v1"}, expected_version=1)["calc_revision"]
    # Второй комплект в другой валюте и с неизвестной ценой: итоги по валютам не смешиваются.
    save(proj.eng, kit_b, [line(qty="2", price="10"), line(name="Щит", qty="1", price=None)], ev=0, currency="EUR")
    r2 = proj.gip.ok("calc.freeze", {"id": kit_b, "reason": "ЭМ v1"}, expected_version=1)["calc_revision"]
    assert r2["revision_no"] == 2 and {i["kit_code"] for i in r2["items"]} == {"АТХ", "ЭМ"}
    assert r2["totals"] == [
        {"currency": "EUR", "total": "20.0000", "unknown_price_count": 1, "kits": 1, "complete": False},
        {"currency": "RUB", "total": "1000.0000", "unknown_price_count": 0, "kits": 1, "complete": True}]
    assert r2["complete"] is False
    # Черновик АТХ меняется — официальный итог (последняя ревизия) не меняется, пока нет фиксации.
    save(proj.eng, proj.kit, [line(qty="1", price="999999")], ev=1)
    revs = pull(proj.rp)["calc_revisions"]
    assert [r["revision_no"] for r in revs] == [1, 2] and revs[-1]["totals"][1]["total"] == "1000.0000"
    # Фиксация АТХ v2 → ревизия 3 из АТХ v2 + ЭМ v1; ревизия 1 осталась прежней (история).
    r3 = proj.gip.ok("calc.freeze", {"id": proj.kit, "reason": "Изменение ТЗ"}, expected_version=2)["calc_revision"]
    assert {(i["kit_code"], i["version_no"]) for i in r3["items"]} == {("АТХ", 2), ("ЭМ", 1)}
    revs = pull(proj.rp)["calc_revisions"]
    assert revs[0] == r1 and revs[0]["totals"][0]["total"] == "1000.0000"
    assert revs[2]["totals"][1]["total"] == "999999.0000" and revs[2]["reason"] == "Изменение ТЗ"


def test_versions_and_revisions_are_immutable_in_db(world, proj):
    save(proj.eng, proj.kit, [line()], ev=0)
    proj.gip.ok("calc.freeze", {"id": proj.kit, "reason": "v1"}, expected_version=1)
    eng = world.svc.engine
    for stmt in (db.calc_versions.update().values(reason="подмена"), db.calc_version_lines.update().values(name="подмена"),
                 db.project_calc_revisions.delete(), db.project_calc_revision_items.update().values(version_no=9),
                 db.calc_version_lines.delete()):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.execute(stmt)
    with eng.connect() as c:
        assert c.execute(sa.select(db.calc_versions.c.reason)).scalar() == "v1"


def test_freeze_replay_is_idempotent(world, proj):
    save(proj.eng, proj.kit, [line()], ev=0)
    op = new_id()
    a = proj.gip.cmd("calc.freeze", {"id": proj.kit, "reason": "v1"}, expected_version=1, op_id=op)
    b = proj.gip.cmd("calc.freeze", {"id": proj.kit, "reason": "v1"}, expected_version=1, op_id=op)
    assert a["status"] == b["status"] == "accepted" and b.get("replayed") is True
    assert len(pull(proj.gip)["calc_versions"]) == 1 and len(pull(proj.gip)["calc_revisions"]) == 1


# ====================================================================== видимость и журнал
def test_visibility_of_drafts_versions_and_revisions(world, proj):
    save(proj.eng, proj.kit, [line(qty="3", price="7")], ev=0)
    proj.gip.ok("calc.freeze", {"id": proj.kit, "reason": "v1"}, expected_version=1)
    eng = pull(proj.eng)
    # Участник видит черновик и версии (он их готовит и сравнивает), но не официальный итог проекта (матрица 04).
    assert len(eng["calc_drafts"]) == 1 and len(eng["calc_versions"]) == 1 and eng["calc_revisions"] == []
    assert len(pull(proj.rp)["calc_revisions"]) == 1
    acc = pull(world.login("acc1"))
    # Бухгалтерия: суммы ревизий (с кодом проекта), без черновиков и позиций.
    assert acc["calc_drafts"] == [] and acc["calc_versions"] == [] and acc["projects"] == []
    assert acc["calc_revisions"][0]["project_code"] == "П-100" and acc["calc_revisions"][0]["totals"][0]["total"] == "21.0000"
    assert pull(proj.lead)["calc_drafts"] == []


def test_history_and_audit(world, proj):
    save(proj.eng, proj.kit, [line(name="А")], ev=0)
    save(proj.eng, proj.kit, [line(name="Б")], ev=1)
    proj.gip.ok("calc.freeze", {"id": proj.kit, "reason": "Расчёт v1"}, expected_version=2)
    h = proj.eng.query("history", object_type="calc_draft", object_id=proj.kit)
    assert h["status"] == "accepted"
    cmds = [x["command"] for x in h["result"]]
    assert cmds == ["calc.save_draft", "calc.save_draft", "calc.freeze"]
    assert h["result"][1]["details"]["added"] == 1 and h["result"][1]["details"]["removed"] == 1
    assert h["result"][2]["authority"].startswith("project_role:gip#assignment:")
    # История ревизий итога — только тем, кто вправе видеть итог.
    assert proj.eng.query("history", object_type="project_calc", object_id=proj.pid)["status"] == "denied"
    hr = proj.rp.query("history", object_type="project_calc", object_id=proj.pid)
    assert hr["status"] == "accepted" and hr["result"][0]["details"]["calc_revision_no"] == 1
    assert all(a["result"] == "accepted" for a in audit(world, "calc.freeze"))
