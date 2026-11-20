"""Сквозные процессы проекта: лиды, потоки, риск-разрешения, события, архив (D-56, blueprint 52).

Каждый тест закрепляет правило из server/delivery.py (чистые правила — vector.core.delivery); запускается на
SQLite и на встроенном PostgreSQL. Кто есть кто: rp1 — РП (глобальная роль), gip1 — ГИП, eng1 — инженер,
head1 — руководитель отдела, rp2 — второй РП. Роль В ПРОЕКТе назначается отдельно (project.assign).
"""
from datetime import timedelta

import pytest
import sqlalchemy as sa

from conftest import new_id
from vector.server import db


def pull(s):
    """Снимок pull пользователя."""
    r = s.query("pull")
    assert r["status"] == "accepted", r
    return r["result"]


def make_project(world, rp, code="П-2026-901"):
    """Выигранный тендер → проект; rp назначен РП проекта (роль в проекте даёт ГИП/администратор — здесь admin)."""
    _cust, tender, _ = world.won_tender(rp)
    pid = new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": code, "title": "Шкафы АСУ ТП"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"],
                                      "project_role": "rp", "reason": "приказ"})
    return pid


def streams(pull_data, pid):
    """Потоки проекта из pull как {kind: запись}."""
    return {w["kind"]: w for w in pull_data["workstreams"] if w["project_id"] == pid}


def events(pull_data, pid):
    """События проекта из pull по порядку."""
    return [e for e in pull_data["project_events"] if e["project_id"] == pid]


def set_status(s, ws, to, reason=None):
    """workstream.set_status с актуальной версией потока."""
    p = {"id": ws["id"], "to": to}
    if reason:
        p["reason"] = reason
    return s.cmd("workstream.set_status", p, expected_version=ws["version"])


def approve_rkd(s, pid, data):
    """Провести поток РКД до «Согласована заказчиком» (с основанием — номером письма)."""
    ws = streams(data, pid)["rkd"]
    for to in ("in_design", "internal_review", "customer_review"):
        ws = set_status(s, ws, to)["result"]["workstream"]
    return set_status(s, ws, "approved_for_use", reason="Письмо заказчика № 77 от 10.11.2026")["result"]["workstream"]


# ====================================================================== создание проекта: потоки и событие
def test_project_start_creates_six_workstreams_and_event(world):
    rp = world.login("rp1")
    pid = make_project(world, rp)
    data = pull(rp)
    st = streams(data, pid)
    assert set(st) == {"contract", "rkd", "procurement", "production", "shipping", "site"}
    assert {k: w["status"] for k, w in st.items()} == {
        "contract": "planned", "rkd": "planned", "procurement": "not_started",
        "production": "blocked", "shipping": "planned", "site": "planned"}
    assert {w["kind_label"] for w in st.values()} == {"Договор", "РКД", "Закупка", "Производство", "Отгрузка",
                                                      "ПНР и ШМР на площадке"}
    ev = events(data, pid)
    assert [e["kind"] for e in ev] == ["project_created"]
    assert ev[0]["actor_id"] == world.users["rp1"]


# ====================================================================== лиды
def test_opportunity_lifecycle_and_convert_to_tender(world):
    rp, eng = world.login("rp1"), world.login("eng1")
    cust = new_id()
    rp.ok("customer.create", {"id": cust, "name": "ООО Заказчик"})
    oid = new_id()
    # Права: инженер (без роли продаж) лид не создаёт — A-57.
    r = eng.cmd("opportunity.create", {"id": new_id(), "title": "Шкаф", "source": "manager", "customer_id": cust})
    assert r["status"] == "denied"
    # Заказчик обязателен: из справочника или текстом.
    assert rp.cmd("opportunity.create", {"id": new_id(), "title": "Без заказчика", "source": "platform"})["code"] == \
        "customer_required"
    r = rp.ok("opportunity.create", {"id": oid, "title": "Шкафы АВР для ТЭЦ", "source": "manager",
                                     "customer_id": cust})
    opp = r["opportunity"]
    assert opp["status"] == "new" and opp["source_label"] == "Личный канал менеджера"
    assert opp["owner_user_id"] == world.users["rp1"]   # по умолчанию — автор
    # В тендер — только квалифицированный лид и только через convert (не transition).
    assert rp.cmd("opportunity.convert_to_tender", {"id": oid, "tender_id": new_id()},
                  expected_version=1)["code"] == "opportunity_not_qualified"
    assert rp.cmd("opportunity.transition", {"id": oid, "to": "tendered"},
                  expected_version=1)["code"] == "use_convert_command"
    rp.ok("opportunity.transition", {"id": oid, "to": "qualified"}, expected_version=1)
    tid = new_id()
    r = rp.ok("opportunity.convert_to_tender", {"id": oid, "tender_id": tid}, expected_version=2)
    assert r["tender"]["status"] == "draft" and r["tender"]["owner_user_id"] == world.users["rp1"]
    assert r["opportunity"]["status"] == "tendered" and r["opportunity"]["converted_tender_id"] == tid
    # Тендер из лида — обычный тендер (его видно в pull); повторная конвертация запрещена, лид закрыт для правок.
    assert any(t["id"] == tid for t in pull(rp)["tenders"])
    assert rp.cmd("opportunity.convert_to_tender", {"id": oid, "tender_id": new_id()},
                  expected_version=3)["code"] == "opportunity_not_qualified"
    assert rp.cmd("opportunity.update", {"id": oid, "note": "поздно"}, expected_version=3)["code"] == "opportunity_closed"
    # Отклонение — только с причиной.
    oid2 = new_id()
    rp.ok("opportunity.create", {"id": oid2, "title": "Поставка щитов", "source": "platform",
                                 "customer_name": "Новый заказчик (ещё не в справочнике)"})
    assert rp.cmd("opportunity.transition", {"id": oid2, "to": "declined"},
                  expected_version=1)["code"] == "reason_required"
    r = rp.ok("opportunity.transition", {"id": oid2, "to": "declined", "reason": "не наш профиль"},
              expected_version=1)
    assert r["opportunity"]["status_reason"] == "не наш профиль"


def test_opportunity_visible_only_to_sales_roles(world):
    rp, eng = world.login("rp1"), world.login("eng1")
    rp.ok("opportunity.create", {"id": new_id(), "title": "Лид", "source": "inbound",
                                 "customer_name": "Заказчик"})
    assert len(pull(rp)["opportunities"]) == 1
    assert pull(eng)["opportunities"] == []          # у инженера нет права opportunity.read
    # История лида — тем, кто читает лиды; инженеру — отказ.
    assert eng.query("history", object_type="sales_opportunity",
                     object_id=pull(rp)["opportunities"][0]["id"])["status"] == "denied"


# ====================================================================== потоки: переходы, причины, права
def test_workstream_flow_reasons_and_rights(world):
    rp, gip = world.login("rp1"), world.login("gip1")
    pid = make_project(world, rp)
    ws = streams(pull(rp), pid)["rkd"]
    # Потоки ведёт РП проекта (ответ владельца п. 1); ГИП без роли РП — отказ.
    assert set_status(gip, ws, "in_design")["status"] == "denied"
    # Переходы строго по контракту: planned → customer_review нельзя.
    assert set_status(rp, ws, "customer_review")["code"] == "transition_not_allowed"
    ws = approve_rkd(rp, pid, pull(rp))
    assert ws["status"] == "approved_for_use"
    # Согласование заказчиком требует основания (номер письма/протокола) — проверено выше отсутствием отказа;
    # теперь явно: без причины — отказ.
    pid2 = make_project(world, rp, code="П-2026-902")
    ws2 = streams(pull(rp), pid2)["rkd"]
    for to in ("in_design", "internal_review", "customer_review"):
        ws2 = set_status(rp, ws2, to)["result"]["workstream"]
    assert set_status(rp, ws2, "approved_for_use")["code"] == "reason_required"
    # Возврат на доработку требует причины и ведёт на повторную разработку.
    assert set_status(rp, ws2, "revision_required")["code"] == "reason_required"
    ws2 = set_status(rp, ws2, "revision_required", reason="замечания по штампам")["result"]["workstream"]
    assert set_status(rp, ws2, "in_design")["status"] == "accepted"
    # Каждый переход — событие проекта.
    kinds = [e["kind"] for e in events(pull(rp), pid2)]
    assert kinds.count("workstream_status") >= 5


# ====================================================================== шлюзы и риск-разрешения
def test_procurement_gate_and_risk_acceptance(world):
    rp, gip = world.login("rp1"), world.login("gip1")
    pid = make_project(world, rp)
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["gip1"],
                                      "project_role": "gip", "reason": "приказ"})
    data = pull(rp)
    ws = streams(data, pid)["procurement"]
    ws = set_status(rp, ws, "needs_ready")["result"]["workstream"]
    ws = set_status(rp, ws, "consolidating")["result"]["workstream"]
    # «Заказано» без согласованной заказчиком РКД — отказ (шлюз, ответ владельца пп. 2–3).
    r = set_status(rp, ws, "ordered")
    assert r["code"] == "gate_requires_rkd_approval" and r["details"]["risk_kind"] == "procurement_before_rkd_approval"
    # Риск-разрешение принимает РП проекта; ГИП — нет (ответ владельца п. 1).
    rid = new_id()
    assert gip.cmd("risk.accept", {"id": rid, "project_id": pid, "kind": "procurement_before_rkd_approval",
                                   "reason": "сроки тендера", "due_on": "2026-11-20"})["status"] == "denied"
    # Обязательны причина и срок не в прошлом.
    assert rp.cmd("risk.accept", {"id": new_id(), "project_id": pid, "kind": "procurement_before_rkd_approval",
                                  "reason": "  ", "due_on": "2026-11-20"})["code"] == "field_required"
    assert rp.cmd("risk.accept", {"id": new_id(), "project_id": pid, "kind": "procurement_before_rkd_approval",
                                  "reason": "сроки", "due_on": "2026-11-10"})["code"] == "date_in_past"
    r = rp.ok("risk.accept", {"id": rid, "project_id": pid, "kind": "procurement_before_rkd_approval",
                              "reason": "сроки поставки критичны, техника не изменится",
                              "due_on": (world.clock.now() + timedelta(days=4)).date().isoformat()})
    assert r["risk"]["state"] == "active"
    # ГИП получил уведомление о принятом риске.
    assert any(n["kind"] == "risk_accepted" for n in pull(gip)["notifications"])
    # Шлюз открыт: заказ разрешён. Второе действующее разрешение того же вида — отказ.
    ws = streams(pull(rp), pid)["procurement"]
    assert set_status(rp, ws, "ordered")["status"] == "accepted"
    assert rp.cmd("risk.accept", {"id": new_id(), "project_id": pid, "kind": "procurement_before_rkd_approval",
                                  "reason": "ещё одно", "due_on": "2026-12-01"})["code"] == "risk_already_active"
    # Производство по-прежнему заблокировано: риск закупки его не открывает.
    wsp = streams(pull(rp), pid)["production"]
    wsp = set_status(rp, wsp, "ready")["result"]["workstream"]
    assert set_status(rp, wsp, "released")["code"] == "gate_requires_rkd_approval"


def test_risk_expiry_and_resolve(world):
    rp = world.login("rp1")
    pid = make_project(world, rp)
    rid = new_id()
    rp.ok("risk.accept", {"id": rid, "project_id": pid, "kind": "production_before_rkd_approval",
                          "reason": "сборка по черновой РКД", "due_on": "2026-11-17"})
    wsp = streams(pull(rp), pid)["production"]
    wsp = set_status(rp, wsp, "ready")["result"]["workstream"]
    assert set_status(rp, wsp, "released")["status"] == "accepted"     # разрешение действует
    # Срок истёк — разрешение больше не открывает шлюз и требует явного снятия.
    world.clock.advance(days=3)
    rp = world.login("rp1")   # сессия привязана к времени входа: после сдвига часов — новый вход
    risk = pull(rp)["project_risks"][0]
    assert risk["state"] == "expired" and risk["status"] == "active"
    ws = streams(pull(rp), pid)["procurement"]
    ws = set_status(rp, ws, "needs_ready")["result"]["workstream"]
    ws = set_status(rp, ws, "consolidating")["result"]["workstream"]
    assert set_status(rp, ws, "ordered")["code"] == "gate_requires_rkd_approval"
    # Снятие — только с причиной; после снятия разрешение в истории, но не действует.
    assert rp.cmd("risk.resolve", {"id": rid, "resolution": "withdrawn"},
                  expected_version=1)["code"] == "field_required"
    r = rp.ok("risk.resolve", {"id": rid, "resolution": "withdrawn", "reason": "заказчик запросил изменения"},
              expected_version=1)
    assert r["risk"]["status"] == "withdrawn"
    assert rp.cmd("risk.resolve", {"id": rid, "resolution": "resolved", "reason": "повторно"},
                  expected_version=2)["code"] == "risk_not_active"


# ====================================================================== архив проекта
def _finish_all_streams(rp, pid):
    """Довести все потоки до конечных статусов (РКД — с основанием согласования заказчиком)."""
    data = pull(rp)
    approve_rkd(rp, pid, data)
    data = pull(rp)
    for kind, path in (("contract", ["in_progress", "signed", "active", "completed"]),
                       ("procurement", ["needs_ready", "consolidating", "ordered", "supplied"]),
                       ("production", ["ready", "released", "assembling", "internal_commissioning", "pre_fat",
                                         "final_fat", "packed", "shipped"]),
                       ("shipping", ["ready", "shipped", "delivered"]),
                       ("site", ["scheduled", "in_progress", "accepted"])):
        ws = streams(data, pid)[kind]
        for to in path:
            ws = set_status(rp, ws, to, reason="доработка по замечаниям" if to == "assembling" else None)
            assert ws["status"] == "accepted", (kind, to, ws)
            ws = ws["result"]["workstream"]


def test_archive_requires_finished_streams_and_no_open_risks(world):
    rp = world.login("rp1")
    pid = make_project(world, rp)
    # Открытые потоки не дают уйти в архив.
    assert rp.cmd("project.transition", {"id": pid, "to": "archived"},
                  expected_version=1)["code"] == "transition_not_allowed"   # сразу в архив нельзя: через «Завершается»
    rp.ok("project.transition", {"id": pid, "to": "closing"}, expected_version=1)
    r = rp.cmd("project.transition", {"id": pid, "to": "archived"}, expected_version=2)
    assert r["code"] == "streams_open" and "Закупка" in r["message_ru"]
    # Возврат в работу — только с причиной.
    assert rp.cmd("project.transition", {"id": pid, "to": "rkd_in_progress"},
                  expected_version=2)["code"] == "reason_required"
    rp.ok("project.transition", {"id": pid, "to": "rkd_in_progress", "reason": "заказчик вернулся к переговорам"},
          expected_version=2)
    # Действующее риск-разрешение блокирует архив даже при завершённых потоках.
    _finish_all_streams(rp, pid)
    rid = new_id()
    rp.ok("risk.accept", {"id": rid, "project_id": pid, "kind": "procurement_before_rkd_approval",
                          "reason": "контрольный пример", "due_on": "2026-12-31"})
    rp.ok("project.transition", {"id": pid, "to": "closing"}, expected_version=3)
    ver = pull(rp)["projects"][[p["id"] for p in pull(rp)["projects"]].index(pid)]["version"]
    r = rp.cmd("project.transition", {"id": pid, "to": "archived"}, expected_version=ver)
    assert r["code"] == "risks_open"
    rp.ok("risk.resolve", {"id": rid, "resolution": "resolved", "reason": "условие выполнено: РКД согласована"},
          expected_version=1)
    r = rp.ok("project.transition", {"id": pid, "to": "archived"}, expected_version=ver)
    assert r["project"]["status"] == "archived"
    # Снимок закрытия создан, неизменяем и доступен в pull; контрольная сумма совпадает с содержимым.
    data = pull(rp)
    co = [c for c in data["project_closeouts"] if c["project_id"] == pid]
    assert len(co) == 1
    import hashlib
    assert hashlib.sha256(co[0]["snapshot"].encode()).hexdigest() == co[0]["content_sha256"]
    # Архив — только чтение: ни потоки, ни комплекты не меняются.
    ws = streams(data, pid)["rkd"]
    assert rp.cmd("workstream.set_status", {"id": ws["id"], "to": "in_design"},
                  expected_version=ws["version"])["code"] == "project_state"
    assert rp.cmd("kit.create", {"id": new_id(), "project_id": pid, "code": "ШАВР",
                                 "title": "Шкаф АВР"})["code"] == "project_state"


def test_cancel_project_requires_reason_and_is_terminal(world):
    rp = world.login("rp1")
    pid = make_project(world, rp)
    assert rp.cmd("project.transition", {"id": pid, "to": "cancelled"},
                  expected_version=1)["code"] == "reason_required"
    r = rp.ok("project.transition", {"id": pid, "to": "cancelled", "reason": "заказчик отказался"},
              expected_version=1)
    assert r["project"]["status"] == "cancelled"
    assert rp.cmd("project.transition", {"id": pid, "to": "closing"},
                  expected_version=2)["code"] == "transition_not_allowed"


# ====================================================================== журнал: события и аудит
def test_events_and_audit_and_immutability(world, engine):
    rp = world.login("rp1")
    pid = make_project(world, rp)
    rp.ok("project.transition", {"id": pid, "to": "closing"}, expected_version=1)
    data = pull(rp)
    ev = events(data, pid)
    assert [e["kind"] for e in ev] == ["project_created", "project_status"]
    # История проекта по аудиту: project.transition записан с основанием (роль РП в проекте).
    h = rp.query("history", object_type="project", object_id=pid)
    assert h["status"] == "accepted"
    cmds = [r["command"] for r in h["result"]]
    assert "project.start_rkd" in cmds and "project.transition" in cmds
    # Журналы событий и снимок архива неизменяемы (триггеры БД, коды 50043/50044).
    with engine.connect() as conn:
        with conn.begin():
            with pytest.raises(sa.exc.DBAPIError):
                conn.execute(sa.update(db.project_events).where(db.project_events.c.project_id == pid)
                             .values(summary="подмена"))
    # Версия потока защищает от гонки: правка по устаревшей версии — конфликт.
    ws = streams(pull(rp), pid)["contract"]
    assert rp.cmd("workstream.set_status", {"id": ws["id"], "to": "in_progress"},
                  expected_version=ws["version"] + 5)["code"] == "version_conflict"


def test_workstream_plan_and_responsible(world):
    rp, eng = world.login("rp1"), world.login("eng1")
    pid = make_project(world, rp)
    ws = streams(pull(rp), pid)["rkd"]
    # Ответственный за поток — только участник проекта.
    assert rp.cmd("workstream.update", {"id": ws["id"], "responsible_user_id": world.users["eng1"]},
                  expected_version=1)["code"] == "not_project_member"
    rp.ok("project.assign", {"project_id": pid, "user_id": world.users["eng1"], "project_role": "participant",
                             "reason": "распределение"})
    r = rp.ok("workstream.update", {"id": ws["id"], "responsible_user_id": world.users["eng1"],
                                    "planned_start": "2026-11-17", "planned_due": "2026-12-01"},
              expected_version=1)
    assert r["workstream"]["responsible_user_id"] == world.users["eng1"]
    # Дата окончания раньше даты начала — отказ.
    assert rp.cmd("workstream.update", {"id": ws["id"], "planned_due": "2026-11-01"},
                  expected_version=2)["code"] == "bad_dates"
    # Инженер получил уведомление о статусе потока, когда он ответственный.
    rp.cmd("workstream.set_status", {"id": ws["id"], "to": "in_design"}, expected_version=2)
    assert any(n["kind"] == "workstream_status" for n in pull(eng)["notifications"])
