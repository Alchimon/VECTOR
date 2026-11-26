"""Технология и производство на клиенте (Class A, SQLite; D-36): черновик ТП офлайн через очередь с цепочкой версий,
события исполнителя без связи (время устройства, локальная проверка теми же правилами ядра, наложение на кэш),
выпуск наряда только онлайн и представление production_view для интерфейса.
"""
import json
import uuid
from decimal import Decimal

import pytest

from conftest import new_id
from test_offline_sync import BACKENDS, FAST_KDF, SERVER, mk  # noqa: F401  (фикстура mk и общие константы)
from vector.client import production_view as pv

_ = (BACKENDS, FAST_KDF, SERVER)

OPS = [{"op_no": 10, "name": "Сборка шкафа", "work_center": "Участок 1", "norm_min": "90"},
       {"op_no": 20, "name": "Наладка", "norm_min": "30"}]


@pytest.fixture
def plant(world, mk):
    """Проект (rp1 — РП, gip1 — ГИП) с комплектом и зафиксированным расчётом; технологи tech1/tech2, исполнитель prod1.

    Возвращает (kit, spec_version_id, клиент tech1, клиент prod1, клиент rp1, сеанс tech2 на сервере).
    """
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    pid, kit = new_id(), new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-5", "title": "Проект"})
    for who, role in (("gip1", "gip"), ("rp1", "rp")):
        world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users[who], "project_role": role, "reason": "Приказ"})
    rp.ok("project.assign", {"project_id": pid, "user_id": world.users["eng1"], "project_role": "participant", "reason": "Работы"})
    rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Автоматизация"})
    # D-61: наряд выпускается только после согласования РКД заказчиком (или по риск-разрешению).
    ws = {w["kind"]: w for w in rp.query("pull")["result"]["workstreams"] if w["project_id"] == pid}["rkd"]
    for to in ("in_design", "internal_review", "customer_review"):
        ws = rp.ok("workstream.set_status", {"id": ws["id"], "to": to}, expected_version=ws["version"])["workstream"]
    rp.ok("workstream.set_status", {"id": ws["id"], "to": "approved_for_use",
                                    "reason": "Письмо заказчика № 77"}, expected_version=ws["version"])
    eng, gip = world.login("eng1"), world.login("gip1")
    line = {"line_id": str(uuid.uuid4()), "code": None, "name": "Шкаф ШУ", "unit": "шт", "qty": "2", "unit_price": "100"}
    eng.ok("calc.save_draft", {"id": kit, "lines": [line], "currency": "RUB"}, expected_version=0)
    spec = gip.ok("calc.freeze", {"id": kit, "reason": "В производство"}, expected_version=1)["calc_version"]["id"]
    world.clock.advance(seconds=61)               # ограничение частоты входа: заведение учёток — тоже входы
    for login, role in (("tech1", "technologist"), ("tech2", "technologist"), ("prod1", "production")):
        world.users[login] = world.make_user(login, role)
    world.clock.advance(seconds=61)
    t1, p1, r1 = mk("tech1"), mk("prod1"), mk("rp1")
    for c in (t1, p1, r1):
        c.pull()
    return kit, spec, t1, p1, r1, world.login("tech2")


def _tp(oid, kit, spec, ops=OPS, title="Сборка шкафа"):
    """Черновик ТП в форме, которую присылает редактор."""
    return {"id": oid, "kit_id": kit, "title": title, "spec_version_id": spec, "operations": ops, "note": None}


def _approved_and_released(world, plant):
    """Утверждённый ТП (tech1 → tech2) и выпущенный rp1 наряд на 2 изделия (операции 10 и 20 — prod1)."""
    kit, spec, t1, p1, r1, tech2 = plant
    oid = new_id()
    assert t1.save_tp_draft(_tp(oid, kit, spec)).status == "accepted"
    tech2.ok("tp.approve", {"id": oid}, expected_version=1)
    r1.pull()
    uid = world.users["prod1"]
    r = r1.execute("wo.release", {"id": new_id(), "tp_id": oid, "qty": 2, "assignees": {"10": uid, "20": uid}})
    assert r.status == "accepted", r
    p1.pull()
    return oid, r.response["result"]["work_order"]["id"]


def test_tp_draft_offline_chain_unchanged_and_sync(world, plant):
    kit, spec, t1, *_ = plant
    oid = new_id()
    t1.t.online = False
    assert t1.save_tp_draft(_tp(oid, kit, spec)).status == "queued"                              # tp.create
    # То же содержимое (норма «90.0» = «90») — в очередь не ставится, иначе сервер ответил бы «нет изменений».
    same = _tp(oid, kit, spec, ops=[dict(OPS[0], norm_min="90.0"), OPS[1]])
    assert t1.save_tp_draft(same).response == {"unchanged": True}
    assert t1.save_tp_draft(_tp(oid, kit, spec, title="Сборка шкафа ШУ")).status == "queued"     # tp.update ev=1
    assert [q["command"] for q in t1.queue()] == ["tp.create", "tp.update"]
    evs = [r[0] for r in t1.db.execute("SELECT expected_version FROM outbox ORDER BY seq")]
    assert evs == [None, 1]
    op_id, enc = t1.db.execute("SELECT operation_id, payload_enc FROM outbox WHERE command='tp.update'").fetchone()
    assert "kit_id" not in json.loads(t1._open("outbox", op_id, enc))    # комплект ТП после создания не меняется
    local = t1.get_local("tech_process", oid)
    assert local["title"] == "Сборка шкафа ШУ" and local["_pending"]
    t1.t.online = True
    rep = t1.sync()
    assert (rep.accepted, rep.stopped_on) == (2, None)
    tp = t1.get_local("tech_process", oid)
    assert (tp["version"], tp["status"], tp["_pending"]) == (2, "draft", False)


def test_op_events_offline_use_device_time_local_rules_and_overlay(world, plant):
    _, wo_id = _approved_and_released(world, plant)
    kit, spec, t1, p1, r1, tech2 = plant
    ops = {o["op_no"]: o for o in p1.list_local("wo_operation") if o["work_order_id"] == wo_id}
    op = ops[10]
    p1.t.online = False
    # Недопустимое по правилам ядра («пауза» до «старта») в очередь не попадает — причина видна сразу.
    r = p1.op_event(op["id"], "pause")
    assert r.status == "rejected" and r.response["code"] == "invalid_transition" and p1.queue() == []
    assert p1.op_event(op["id"], "start").status == "queued"
    world.clock.advance(minutes=40)
    assert p1.op_event(op["id"], "pause").status == "queued"
    loc = p1.get_local("wo_operation", op["id"])
    assert loc["state"] == "paused" and loc["fact_seconds"] == 40 * 60 and loc["_pending"]
    assert [e["kind"] for e in loc["events"]][-2:] == ["start", "pause"] and loc["events"][-1]["local"] is True
    # Завершить больше, чем в наряде, нельзя уже на клиенте.
    assert p1.op_event(op["id"], "finish", qty_done=3).status == "rejected"
    assert p1.op_event(op["id"], "problem", note="Нет наконечников").status == "queued"
    p1.t.online = True
    rep = p1.sync()
    assert (rep.accepted, rep.stopped_on) == (3, None)
    srv = p1.get_local("wo_operation", op["id"])
    assert (srv["state"], srv["fact_seconds"], srv["problem_count"], srv["_pending"]) == ("paused", 2400, 1, False)
    # РП видит проблему уведомлением.
    r1.pull()
    assert any(n["kind"] == "wo_problem" for n in r1.get_meta("notifications") or [])


def test_release_is_online_only(world, plant):
    kit, spec, t1, p1, r1, tech2 = plant
    oid = new_id()
    t1.save_tp_draft(_tp(oid, kit, spec))
    tech2.ok("tp.approve", {"id": oid}, expected_version=1)
    r1.pull()
    r1.t.online = False
    r = r1.execute("wo.release", {"id": new_id(), "tp_id": oid, "qty": 1, "assignees": {}})
    assert r.status == "refused_offline" and r1.queue() == []


def test_production_view_for_worker_and_master(world, plant):
    _, wo_id = _approved_and_released(world, plant)
    kit, spec, t1, p1, r1, tech2 = plant

    def view(c, me):
        """Представление раздела «Производство» из кэша клиента c."""
        g = c.list_local
        return pv.production_view(g("tech_process"), c.get_meta("tech_specs") or [], g("work_order"), g("wo_operation"),
                                  c.get_meta("production_staff") or [], g("project"), g("kit"),
                                  c.get_meta("assignments") or [], c.get_meta("project_deputies") or [],
                                  {p["id"]: p["display_name"] for p in c.get_meta("people") or []},
                                  c.get_meta("me") or {"id": world.users[me]}, True, None, now=world.clock.now())
    w = view(p1, "prod1")
    assert w["default_tab"] == "my" and len(w["my_ops"]) == 2
    first = next(o for o in w["my_ops"] if o["op_no"] == 10)
    assert [a["kind"] for a in first["actions"]] == ["start"] and w["can_release"] is False
    m = view(r1, "rp1")
    wo = next(x for x in m["work_orders"] if x["id"] == wo_id)
    assert wo["manage"] is True and wo["snapshot_ok"] is True and wo["can_close"] is False
    assert "Не все операции выполнены." in wo["close_problems"]
    assert next(o for o in wo["operations"] if o["op_no"] == 10)["norm_label"] == "3 ч 00 мин"   # 90 мин × 2


def test_wo_row_readiness_risks_formatted():
    """Снимок готовности с риск-разрешением (D-61): карточка наряда показывает его с датой и причиной.

    Регрессия: ветка с непустым readiness.risks использовала _date_ru без импорта — падала бы с NameError
    на любом наряде, выпущенном по риск-разрешению (прогон линтером F821)."""
    wo = {"id": "wo1", "doc_no": 7, "status": "released", "qty": 2, "tp_revision_no": 1,
          "readiness": {"verdict": "ready_with_deviations", "verdict_label": "Готов с отклонениями",
                        "readiness_ok": True,
                        "risks": [{"label_ru": "Сборка по неутверждённой РКД", "due_on": "2026-12-01",
                                   "reason": "Заказчик подтвердил письмом"}],
                        "materials": [{"state": "short"}, {"state": "short"}]},
          "materials_ready": True}
    row = pv._wo_row(wo, [], True, False, True, {})
    assert row["readiness_deviations"] == ["Сборка по неутверждённой РКД — до 01.12.2026: Заказчик подтвердил письмом",
                                           "Материалы обеспечены не полностью (2 поз.)"]
    assert row["readiness_label"] == "Готов с отклонениями" and row["readiness_tone"] == "warning"


def test_clean_operations_and_problems():
    rows = [{"op_no": "10", "name": " Сборка ", "work_center": "", "norm_min": "1,5", "note": ""},
            {"op_no": "", "name": "", "work_center": "", "norm_min": "", "note": ""},       # пустая строка формы — пропуск
            {"op_no": "20", "name": "Наладка", "work_center": "Стенд", "norm_min": "30", "note": "ПО"}]
    clean = pv.clean_operations(rows)
    assert clean == [{"op_no": 10, "name": "Сборка", "work_center": None, "norm_min": "1.5", "note": None},
                     {"op_no": 20, "name": "Наладка", "work_center": "Стенд", "norm_min": "30", "note": "ПО"}]
    ok = pv.operations_problems(rows)
    assert ok["problems"] == [] and ok["norm_label"] == "31,5 мин на изделие"
    bad = pv.operations_problems([{"op_no": "10", "name": "А", "norm_min": "x"}, {"op_no": "10", "name": "Б", "norm_min": "1"}])
    assert bad["by_line"] == {"0": "Операция 1: «Норма» — число минут, например 90 или 1,5.", "1": "Операция 2: № 10 уже есть в ТП."}
    assert len(bad["problems"]) == 2 and bad["norm_label"] == "—"
    assert Decimal(clean[0]["norm_min"]) == Decimal("1.5")
