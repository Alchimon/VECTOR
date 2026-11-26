"""Снимок готовности сборки при выпуске наряда (этап 5 blueprint 52, D-61).

Каждый тест закрепляет правило (запуск на SQLite и на встроенном PostgreSQL):

* наряд выпускается только по РКД, согласованной заказчиком (поток «РКД» → «Согласована заказчиком»), либо при
  действующем риск-разрешении «Сборка по неутверждённой РКД», охватывающем комплект; истёкшее или назначенное
  на другой комплект разрешение шлюз не открывает;
* в той же транзакции фиксируется НЕИЗМЕНЯЕМЫЙ снимок готовности (триггеры БД): основания (ревизия ТП, версия
  расчёта, статус РКД, потребности), обеспеченность материалами «у проекта» и риск-разрешения; позднейшие
  изменения (резервы, согласование РКД) снимок не переписывают — условное исключение остаётся видно в наряде;
* снимок сверяется по SHA-256 при показе (readiness_ok); вердикт «Готово к сборке» — только без отклонений;
* выпуск по риск-разрешению уведомляет руководителей проекта.
"""
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from conftest import new_id
from test_production import CABLE, OPS, approved_tp, pd, pull, release  # noqa: F401
from test_stock import St, receive, st  # noqa: F401
from test_tasks_documents import proj  # noqa: F401
from vector.server import db


def _line(code, name, unit, qty):
    """Позиция расчёта (цена есть, но в снимки производства цены не входят)."""
    return {"line_id": str(uuid.uuid4()), "code": code, "name": name, "unit": unit, "qty": qty, "unit_price": "100"}


@pytest.fixture
def pd2(world, st):  # noqa: F811
    """Второй проект с утверждённым ТП, но БЕЗ согласования РКД заказчиком (шлюз D-61 закрыт)."""
    world.clock.advance(seconds=61)
    for login, role in (("tech1", "technologist"), ("tech2", "technologist"), ("prod1", "production")):
        world.make_user(login, role)
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    pid, kit = new_id(), new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-200", "title": "Второй проект"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp",
                                      "reason": "Приказ"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["gip1"], "project_role": "gip",
                                      "reason": "Приказ"})
    rp.ok("project.assign", {"project_id": pid, "user_id": world.users["eng1"], "project_role": "participant",
                             "reason": "Работы"})
    rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ-2", "title": "Автоматика"})
    eng, gip = world.login("eng1"), world.login("gip1")
    cable = _line(CABLE, "Кабель КВВГ 4×1,5", "м", "10.5")
    eng.ok("calc.save_draft", {"id": kit, "lines": [cable], "currency": "RUB"}, expected_version=0)
    v = gip.ok("calc.freeze", {"id": kit, "reason": "К производству"}, expected_version=1)["calc_version"]
    world.clock.advance(seconds=61)
    tech, tech2 = world.login("tech1"), world.login("tech2")
    t = tech.ok("tp.create", {"id": new_id(), "kit_id": kit, "title": "Сборка шкафа", "spec_version_id": v["id"],
                              "operations": OPS})["tech_process"]
    tp = tech2.ok("tp.approve", {"id": t["id"]}, expected_version=t["version"])["tech_process"]
    world.clock.advance(seconds=61)
    return SimpleNamespace(st=st, rp=rp, gip=gip, pid=pid, kit=kit, tp=tp, prod=world.login("prod1"))


def _release(pd2, **kw):
    """Попытка выпуска наряда РП второго проекта (ответ сервера)."""
    return pd2.rp.cmd("wo.release", {"id": new_id(), "tp_id": pd2.tp["id"], "qty": 1,
                                     "assignees": {"10": pd2.prod.user_id}, **kw})


def _risk(s, pid, due, **kw):
    """risk.accept «Сборка по неутверждённой РКД» от имени s (ответ сервера)."""
    return s.cmd("risk.accept", {"id": new_id(), "project_id": pid, "kind": "production_before_rkd_approval",
                                 "reason": "Заказчик подтвердил устно, протокол в пути", "due_on": due, **kw})


# ====================================================================== шлюз РКД
def test_release_blocked_without_rkd_approval(world, pd2):
    r = _release(pd2)
    assert r["status"] == "rejected" and r["code"] == "gate_requires_rkd_approval"
    assert "Согласована заказчиком" in r["message_ru"] and "риск-разрешен" in r["message_ru"]
    # Наряд не создан: в pull его нет.
    assert pull(pd2.rp)["work_orders"] == []


def test_risk_acceptance_opens_gate_and_is_recorded(world, pd2):
    due = (world.clock.now().date() + timedelta(days=14)).isoformat()
    risk = _risk(pd2.rp, pd2.pid, due)
    assert risk["status"] == "accepted"
    r = _release(pd2)
    assert r["status"] == "accepted", r
    rd = r["result"]["work_order"]["readiness"]
    assert (rd["verdict"], rd["readiness_ok"]) == ("ready_with_deviations", True)
    assert rd["rkd"]["ok"] is False and rd["rkd"]["status"] == "planned"
    assert [x["id"] for x in rd["risks"]] == [risk["result"]["risk"]["id"]]
    assert rd["risks"][0]["due_on"] == due and rd["risks"][0]["label_ru"] == "Сборка по неутверждённой РКД"
    # Риск виден в наряде из pull (приёмка этапа 5: условное исключение видно в наряде).
    wo = pull(pd2.rp)["work_orders"][0]
    assert wo["readiness"]["risks"][0]["reason"].startswith("Заказчик подтвердил")
    # Команда проекта уведомлена о выпуске по риск-разрешению (автор действия — РП — уведомлений о своих
    # действиях не получает, поэтому проверяем ГИПа).
    assert any(n["kind"] == "wo_released_risk" for n in pull(pd2.gip)["notifications"])


def test_expired_risk_does_not_open_gate(world, pd2):
    today = world.clock.now().date().isoformat()
    assert _risk(pd2.rp, pd2.pid, today)["status"] == "accepted"
    world.clock.advance(days=2, seconds=61)
    rp = world.login("rp1")                      # старые сеансы инвалидированы сдвигом часов
    r = rp.cmd("wo.release", {"id": new_id(), "tp_id": pd2.tp["id"], "qty": 1, "assignees": {}})
    assert r["code"] == "gate_requires_rkd_approval"


def test_kit_scoped_risk_covers_only_listed_kits(world, pd2):
    kit2 = new_id()
    pd2.rp.ok("kit.create", {"id": kit2, "project_id": pd2.pid, "code": "ЭМ-2", "title": "Электрика"})
    due = (world.clock.now().date() + timedelta(days=14)).isoformat()
    assert _risk(pd2.rp, pd2.pid, due, kit_ids=[kit2])["status"] == "accepted"
    assert _release(pd2)["code"] == "gate_requires_rkd_approval"      # разрешение — на другой комплект
    # Разрешение с охватом ЭТОГО комплекта открывает шлюз (прежнее снимаем — два действующих одного вида нельзя).
    risks = pull(pd2.rp)["project_risks"]
    risk = next(r for r in risks if r["project_id"] == pd2.pid)
    pd2.rp.ok("risk.resolve", {"id": risk["id"], "resolution": "withdrawn", "reason": "Переоформляем охват"},
              expected_version=risk["version"])
    assert _risk(pd2.rp, pd2.pid, due, kit_ids=[pd2.kit])["status"] == "accepted"
    assert _release(pd2)["status"] == "accepted"


# ====================================================================== содержимое снимка
def test_readiness_records_basis_and_is_not_rewritten(world, pd):  # noqa: F811
    """Согласованная РКД + материал не обеспечен → «Готово с отклонениями»; резерв ПОСЛЕ выпуска снимок не меняет."""
    wo = release(pd, approved_tp(pd))["result"]["work_order"]
    rd = wo["readiness"]
    assert (rd["verdict"], rd["readiness_ok"]) == ("ready_with_deviations", True)
    assert rd["rkd"] == {"status": "approved_for_use", "label_ru": "Согласована заказчиком", "ok": True}
    assert rd["risks"] == []
    assert (rd["tp"]["id"], rd["tp"]["revision_no"]) == (wo["tp_id"], wo["tp_revision_no"])
    assert rd["spec"]["calc_version_id"] == pd.spec_id and rd["spec"]["version_no"] == 1
    cable = next(m for m in rd["materials"] if m["code"] == CABLE)
    # Требуется 10,5 м × 2 изделия = 21 м; «у проекта» ничего нет → не обеспечено (честная нехватка в снимке).
    assert (cable["required"], cable["secured"], cable["state"]) == ("21.0000", "0.0000", "short")
    work = next(m for m in rd["materials"] if m["code"] is None or m["state"] == "not_stock")
    assert work["state_label"] == "Не складская позиция"
    # Основание выпуска неизменяемо: резерв после выпуска его не переписывает.
    receive(pd.st.wh, pd.st, "100")
    pd.st.wh.ok("stock.reserve", {"id": new_id(), "warehouse_id": pd.st.w, "item_id": pd.st.item,
                                  "project_id": pd.st.proj.pid, "qty": "21", "reason": "Под наряд"})
    rd2 = pull(pd.st.proj.rp)["work_orders"][0]["readiness"]
    assert rd2["content_hash"] == rd["content_hash"]
    assert next(m for m in rd2["materials"] if m["code"] == CABLE)["state"] == "short"


def test_verdict_ready_when_rkd_approved_and_materials_secured(world, pd):  # noqa: F811
    """Согласованная РКД и материал обеспечен (резерв проекта) → вердикт «Готово к сборке»."""
    receive(pd.st.wh, pd.st, "100")
    pd.st.wh.ok("stock.reserve", {"id": new_id(), "warehouse_id": pd.st.w, "item_id": pd.st.item,
                                  "project_id": pd.st.proj.pid, "qty": "21", "reason": "Под производство"})
    wo = release(pd, approved_tp(pd))["result"]["work_order"]
    rd = wo["readiness"]
    assert rd["verdict"] == "ready" and rd["materials_covered"] is True
    cable = next(m for m in rd["materials"] if m["code"] == CABLE)
    assert (cable["secured"], cable["state"], cable["state_label"]) == ("21.0000", "covered", "Обеспечено")


def test_readiness_snapshot_immutable_and_hash_checked(world, pd):  # noqa: F811
    wo = release(pd, approved_tp(pd))["result"]["work_order"]
    # Запись неизменяема (триггер БД, код 50048) — как снимок наряда.
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as c:
            c.execute(sa.update(db.assembly_readiness_snapshots)
                      .where(db.assembly_readiness_snapshots.c.work_order_id == wo["id"]).values(content_json="{}"))
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as c:
            c.execute(sa.delete(db.assembly_readiness_snapshots)
                      .where(db.assembly_readiness_snapshots.c.work_order_id == wo["id"]))
    # Содержимое сходится с хэшем (readiness_ok), потребности по версии расчёта записаны (здесь — пусто: потребность
    # закупки для проекта не формировали).
    rd = pull(pd.st.proj.rp)["work_orders"][0]["readiness"]
    assert rd["readiness_ok"] is True and rd["needs"] == []


def test_readiness_records_needs_of_spec_version(world, pd):  # noqa: F811
    """Потребность, сформированная по версии расчёта, попадает в снимок со статусом на момент выпуска."""
    need = pd.st.pr.ok("need.generate", {"id": new_id(), "calc_version_id": pd.spec_id})["need"]
    wo = release(pd, approved_tp(pd))["result"]["work_order"]
    rd = wo["readiness"]
    assert [(n["id"], n["status"], n["line_count"]) for n in rd["needs"]] == [(need["id"], "open", 2)]
