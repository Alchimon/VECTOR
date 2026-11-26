"""Технология и производство (этап 5, сценарий С5 шаги 1–6; D-36).

Каждый тест закрепляет правило из server/production.py (правила — vector.core.production); запускается на SQLite
и на встроенном PostgreSQL. Кто есть кто: команда проекта как в test_tasks_documents (gip1 — ГИП, rp1 — РП,
eng1 — участник; lead1 — вне команды), head1 — руководитель отдела; tech1, tech2 — технологи; prod1, prod2 —
производство; wh1 — склад (из фикстуры st).
"""
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta

import pytest
import sqlalchemy as sa

from conftest import new_id
from test_stock import St, issue, receive, st  # noqa: F401
from test_tasks_documents import audit, proj  # noqa: F401
from vector.server import db

CABLE = "КВВГ-4x1.5"     # позиция номенклатуры склада из фикстуры st (единица — «м»)


@dataclass
class Pd:
    """Набор теста: склад, технологи, исполнители, зафиксированная версия расчёта комплекта, утверждённый ТП."""
    st: St
    tech: object
    tech2: object
    prod: object
    prod2: object
    head: object
    spec_id: str
    line_cable: str
    line_work: str


def _line(name, qty, unit, code=None, price="100"):
    """Позиция расчёта комплекта (с ценой — чтобы проверить, что цены в снимок не попадают)."""
    return {"line_id": str(uuid.uuid4()), "code": code, "name": name, "unit": unit, "qty": qty, "unit_price": price}


@pytest.fixture
def pd(world, st):  # noqa: F811
    """Технологи, исполнители и зафиксированный расчёт комплекта: кабель (складская позиция) и наладка (работа)."""
    world.clock.advance(seconds=61)      # ограничение частоты входа (20 в минуту с адреса) — стенд большой
    for login, role in (("tech1", "technologist"), ("tech2", "technologist"), ("prod1", "production"),
                        ("prod2", "production")):
        world.make_user(login, role)
    cable, work = _line("Кабель КВВГ 4×1,5", "10.5", "м", CABLE), _line("Наладка", "2", "ч", None, "1500")
    st.proj.eng.ok("calc.save_draft", {"id": st.proj.kit, "lines": [cable, work], "currency": "RUB"}, expected_version=0)
    v = st.proj.gip.ok("calc.freeze", {"id": st.proj.kit, "reason": "К производству"}, expected_version=1)["calc_version"]
    world.clock.advance(seconds=61)
    approve_rkd_stream(st.proj.rp, st.proj.pid)     # D-61: без согласованной заказчиком РКД наряд не выпускается
    return Pd(st, world.login("tech1"), world.login("tech2"), world.login("prod1"), world.login("prod2"),
              world.login("head1"), v["id"], cable["line_id"], work["line_id"])


OPS = [{"op_no": 10, "name": "Резка кабеля", "work_center": "Участок 1", "norm_min": "1.5"},
       {"op_no": 20, "name": "Монтаж шкафа", "norm_min": "30"}]


def tp_draft(pd, ops=OPS, spec=True, **kw):
    """Черновик ТП технологом tech1 (ответ сервера)."""
    p = {"id": new_id(), "kit_id": pd.st.proj.kit, "title": "Шкаф автоматики", "operations": ops, **kw}
    if spec:
        p["spec_version_id"] = pd.spec_id
    return pd.tech.cmd("tp.create", p)


def approved_tp(pd) -> dict:
    """Утверждённый ТП (создал tech1, утвердил tech2)."""
    t = tp_draft(pd)["result"]["tech_process"]
    return pd.tech2.ok("tp.approve", {"id": t["id"]}, expected_version=t["version"])["tech_process"]


def approve_rkd_stream(s, pid):
    """Провести поток РКД проекта до «Согласована заказчиком» (с основанием — номером письма; D-56/D-61)."""
    ws = {w["kind"]: w for w in pull(s)["workstreams"] if w["project_id"] == pid}["rkd"]
    for to in ("in_design", "internal_review", "customer_review"):
        ws = s.ok("workstream.set_status", {"id": ws["id"], "to": to}, expected_version=ws["version"])["workstream"]
    return s.ok("workstream.set_status", {"id": ws["id"], "to": "approved_for_use",
                                          "reason": "Письмо заказчика № 77 от 10.11.2026"},
                expected_version=ws["version"])["workstream"]


def release(pd, tp, qty=2, assignees=None, s=None):
    """Выпустить наряд (по умолчанию РП проекта; операция 10 — prod1, 20 — prod2)."""
    u = pd.st.proj
    asg = assignees if assignees is not None else {"10": _uid(pd.prod), "20": _uid(pd.prod2)}
    return (s or u.rp).cmd("wo.release", {"id": new_id(), "tp_id": tp["id"], "qty": qty, "assignees": asg})


def _uid(s):
    """id пользователя сеанса."""
    return s.user_id


def ops_of(res) -> dict:
    """Операции наряда из результата wo.release по номеру."""
    return {o["op_no"]: o for o in res["wo_operations"]}


def ev(s, op_id, kind, at, **kw):
    """Событие исполнителя (как из офлайн-очереди: без expected_version, со временем устройства)."""
    return s.cmd("wo.op_event", {"id": op_id, "kind": kind, "device_time": at.isoformat(), **kw})


def pull(s):
    """Снимок pull пользователя."""
    r = s.query("pull")
    assert r["status"] == "accepted", r
    return r["result"]


# ====================================================================== ТП
def test_tp_draft_rights_validation_and_single_draft(world, pd):
    u = pd.st.proj
    # Создаёт и правит только технолог (матрица 04: ТП — C/U — G: technologist).
    for s in (u.gip, u.rp, u.eng, pd.head, pd.prod):
        r = s.cmd("tp.create", {"id": new_id(), "kit_id": u.kit, "title": "ТП", "operations": []})
        assert r["status"] == "denied", r
    # Все замечания по операциям сразу, с номерами строк.
    r = tp_draft(pd, ops=[{"op_no": 10, "name": "", "norm_min": 1.5}, {"op_no": 10, "name": "Б", "norm_min": "-1"},
                          {"op_no": 0, "name": "В"}])
    assert r["code"] == "tp_invalid"
    codes = {(p["line"], p["code"]) for p in r["details"]["problems"]}
    assert {(1, "field_required"), (1, "float_forbidden"), (2, "duplicate_op_no"), (2, "norm_range"),
            (3, "invalid_op_no"), (3, "field_required")} <= codes
    # Спецификация — только зафиксированная версия расчёта ЭТОГО комплекта.
    assert tp_draft(pd, spec=False, spec_version_id=pd.spec_id + 999)["code"] == "spec_version_invalid"
    assert tp_draft(pd, spec=False, spec_version_id=str(pd.spec_id))["code"] == "invalid_type"
    t = tp_draft(pd, ops=list(reversed(OPS)))["result"]["tech_process"]
    assert (t["revision_no"], t["status"], [o["op_no"] for o in t["operations"]]) == (1, "draft", [10, 20])
    assert t["norm_per_unit_min"] == "31.5000" and len(t["content_hash"]) == 64
    # Состав изделия — без цен (технологу и производству деньги не нужны).
    assert t["spec"]["lines"][0] == {"line_id": pd.line_cable, "code": CABLE, "name": "Кабель КВВГ 4×1,5", "unit": "м",
                                     "qty": "10.5000"}
    assert "unit_price" not in t["spec"]["lines"][1] and "amount" not in t["spec"]["lines"][1]
    # Один черновик на комплект.
    assert tp_draft(pd)["code"] == "tp_draft_exists"
    # Правка черновика: версия растёт; без изменений — «unchanged»; чужая версия — конфликт.
    p = {"id": t["id"], "title": "Шкаф автоматики", "spec_version_id": pd.spec_id, "operations": OPS}
    assert pd.tech.ok("tp.update", p, expected_version=1).get("unchanged") is True
    t2 = pd.tech2.ok("tp.update", {**p, "note": "Уточнение"}, expected_version=1)["tech_process"]
    assert (t2["version"], t2["note"]) == (2, "Уточнение")
    assert pd.tech.cmd("tp.update", p, expected_version=1)["code"] == "version_conflict"


def test_tp_approval_four_eyes_hash_and_immutability(world, pd):
    t = tp_draft(pd, spec=False)["result"]["tech_process"]
    # Автор (создал или последним правил) не утверждает; посторонние роли — тоже.
    assert pd.tech.cmd("tp.approve", {"id": t["id"]}, expected_version=1)["code"] == "self_approval"
    for s in (pd.st.proj.rp, pd.st.proj.gip, pd.prod):
        assert s.cmd("tp.approve", {"id": t["id"]}, expected_version=1)["status"] == "denied"
    # Без спецификации утверждать нельзя.
    assert pd.tech2.cmd("tp.approve", {"id": t["id"]}, expected_version=1)["code"] == "tp_no_spec"
    t = pd.tech.ok("tp.update", {"id": t["id"], "title": t["title"], "spec_version_id": pd.spec_id, "operations": OPS},
                   expected_version=1)["tech_process"]
    # Утверждается та версия, которую видел утверждающий.
    assert pd.tech2.cmd("tp.approve", {"id": t["id"]}, expected_version=1)["code"] == "version_conflict"
    # Руководитель отдела тоже утверждает (резерв, A-37).
    a = pd.head.ok("tp.approve", {"id": t["id"]}, expected_version=2)["tech_process"]
    assert (a["status"], a["approval"]["approved_by"], a["approval"]["content_hash"], a["approval_valid"]) == (
        "approved", pd.head.user_id, t["content_hash"], True)
    assert a["approval"]["authority"].startswith("global_role:dept_head#grant:")
    # Утверждённый ТП не меняется: правка из очереди — видимый конфликт; новая ревизия — новым черновиком.
    r = pd.tech.cmd("tp.update", {"id": t["id"], "title": "Другое", "operations": OPS}, expected_version=3)
    assert r["status"] == "conflict" and r["code"] == "tp_approved" and r["details"]["current"]["status"] == "approved"
    assert pd.tech2.cmd("tp.approve", {"id": t["id"]}, expected_version=3)["code"] == "tp_state"
    t3 = tp_draft(pd)["result"]["tech_process"]
    assert t3["revision_no"] == 2
    # Автор получил уведомление об утверждении.
    n = [x for x in pull(pd.tech)["notifications"] if x["kind"] == "tp_approved"]
    assert n and "ред. 1 утверждён" in n[0]["title"]


# ====================================================================== выпуск наряда и снимок
def test_release_rights_snapshot_and_assignees(world, pd):
    u = pd.st.proj
    draft = tp_draft(pd)["result"]["tech_process"]
    assert release(pd, draft)["code"] == "tp_not_approved"
    tp = pd.tech2.ok("tp.approve", {"id": draft["id"]}, expected_version=1)["tech_process"]
    # Выпускает РП проекта (мастер, A-42) или руководитель отдела (резерв); ГИП, участник, технолог — нет.
    for s in (u.gip, u.eng, pd.tech, pd.prod):
        assert release(pd, tp, s=s)["status"] == "denied"
    # Исполнитель — только с ролью «Производство»; операция — из ТП; qty — целое ≥ 1.
    assert release(pd, tp, assignees={"10": u.eng.user_id})["code"] == "assignee_not_producer"
    assert release(pd, tp, assignees={"30": pd.prod.user_id})["code"] == "op_not_found"
    assert release(pd, tp, qty=0)["code"] == "invalid_qty"
    assert release(pd, tp, qty="2")["code"] == "invalid_qty"
    res = release(pd, tp, qty=2)["result"]
    wo, ops = res["work_order"], ops_of(res)
    assert (wo["doc_no"], wo["status"], wo["qty"], wo["snapshot_ok"], wo["operations_total"]) == (1, "released", 2, True, 2)
    snap = wo["snapshot"]
    # Снимок: состав × qty без цен, операции с нормой на наряд, ссылки на ТП и версию расчёта.
    assert snap["spec"]["lines"][0]["qty_per_unit"] == "10.5000" and snap["spec"]["lines"][0]["qty"] == "21.0000"
    assert all("unit_price" not in ln and "amount" not in ln for ln in snap["spec"]["lines"])
    assert snap["operations"][1]["norm_total_min"] == "60.0000" and snap["tp"]["content_hash"] == tp["content_hash"]
    assert (ops[10]["assignee_id"], ops[20]["assignee_id"], ops[10]["state"]) == (pd.prod.user_id, pd.prod2.user_id, "pending")
    # Руководитель отдела — резервный «мастер».
    assert release(pd, tp, qty=1, s=pd.head)["status"] == "accepted"
    # Снимок неизменяем на уровне БД.
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as c:
            c.execute(db.work_order_snapshots.update().values(snapshot_json="{}"))
    # Исполнители получили уведомление.
    assert any(n["kind"] == "wo_assigned" for n in pull(pd.prod)["notifications"])


# ====================================================================== исполнение операций (офлайн)
def test_events_timer_fact_and_status_follow_operations(world, pd):
    res = release(pd, approved_tp(pd), qty=2)["result"]
    ops, wid = ops_of(res), res["work_order"]["id"]
    t0 = world.clock.now() - timedelta(hours=3)          # события «из офлайна» — по часам устройства, раньше синхронизации
    o = ev(pd.prod, ops[10]["id"], "start", t0)["result"]
    assert (o["wo_operation"]["state"], o["work_order"]["status"]) == ("running", "in_progress")
    ev(pd.prod, ops[10]["id"], "pause", t0 + timedelta(minutes=20))
    ev(pd.prod, ops[10]["id"], "problem", t0 + timedelta(minutes=25), note="Нет наконечников")
    ev(pd.prod, ops[10]["id"], "resume", t0 + timedelta(minutes=40))
    r = ev(pd.prod, ops[10]["id"], "finish", t0 + timedelta(minutes=50), qty_done=2)["result"]["wo_operation"]
    # Факт = интервалы «в работе»: 20 + 10 минут; пауза не считается.
    assert (r["state"], r["fact_seconds"], r["fact_min"], r["qty_done"], r["problem_count"]) == ("done", 1800, "30.0", 2, 1)
    assert [e["kind"] for e in r["events"]] == ["start", "pause", "problem", "resume", "finish"]
    # Вторая операция: завершение без паузы, количество по умолчанию — весь наряд → наряд «Выполнен».
    ev(pd.prod2, ops[20]["id"], "start", t0 + timedelta(minutes=55))
    r = ev(pd.prod2, ops[20]["id"], "finish", t0 + timedelta(minutes=115))["result"]
    assert (r["wo_operation"]["qty_done"], r["work_order"]["status"], r["work_order"]["fact_seconds"]) == (2, "completed", 5400)
    # РП получил уведомления о проблеме и о выполнении.
    kinds = [n["kind"] for n in pull(pd.st.proj.rp)["notifications"]]
    assert "wo_problem" in kinds and "wo_completed" in kinds
    # События неизменяемы и хранят время устройства, сервера и operation_id очереди.
    with world.svc.engine.connect() as c:
        rows = c.execute(sa.select(db.wo_timer_events).order_by(db.wo_timer_events.c.id)).all()
    assert len(rows) == 7 and len({r.operation_id for r in rows}) == 7 and rows[0].device_time.replace(tzinfo=None) == t0.replace(tzinfo=None)
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as c:
            c.execute(db.wo_timer_events.update().values(note="правка"))
    assert json.loads(audit(world, "wo.op_event", "wo_operation", ops[10]["id"])[-1]["details_json"])["state"] == "done"
    assert wid


def test_event_conflicts_are_visible_not_merged(world, pd):
    res = release(pd, approved_tp(pd), qty=1)["result"]
    ops = ops_of(res)
    t0 = world.clock.now() - timedelta(hours=1)
    # Только назначенный исполнитель и только роль «Производство».
    r = ev(pd.prod2, ops[10]["id"], "start", t0)
    assert (r["status"], r["code"], r["details"]["current"]["state"]) == ("conflict", "not_assignee", "pending")
    assert ev(pd.st.proj.rp, ops[10]["id"], "start", t0)["status"] == "denied"
    # Недопустимый переход — конфликт с текущим состоянием.
    r = ev(pd.prod, ops[10]["id"], "pause", t0)
    assert (r["status"], r["code"]) == ("conflict", "invalid_transition")
    op_id = new_id()
    first = pd.prod.cmd("wo.op_event", {"id": ops[10]["id"], "kind": "start", "device_time": t0.isoformat()}, op_id=op_id)
    assert first["status"] == "accepted"
    # Повтор того же события из очереди (тот же operation_id) не применяется дважды.
    again = pd.prod.cmd("wo.op_event", {"id": ops[10]["id"], "kind": "start", "device_time": t0.isoformat()}, op_id=op_id)
    assert again["status"] == "accepted" and again.get("replayed")
    # Время раньше предыдущего события — конфликт; время из будущего — отказ.
    r = ev(pd.prod, ops[10]["id"], "pause", t0 - timedelta(minutes=5))
    assert (r["status"], r["code"]) == ("conflict", "device_time_order")
    assert ev(pd.prod, ops[10]["id"], "pause", world.clock.now() + timedelta(hours=1))["code"] == "device_time_future"
    r = pd.prod.cmd("wo.op_event", {"id": ops[10]["id"], "kind": "pause", "device_time": "2026-11-16T08:30:00"})
    assert r["code"] == "invalid_datetime"
    assert ev(pd.prod, ops[10]["id"], "problem", t0)["code"] == "field_required"        # проблема — с описанием
    assert ev(pd.prod, ops[10]["id"], "finish", t0 + timedelta(minutes=5), qty_done=2)["code"] == "invalid_qty_done"
    ev(pd.prod, ops[10]["id"], "finish", t0 + timedelta(minutes=5))
    # Выполненная операция событий не принимает (например, второе устройство прислало «пауза» позже).
    r = ev(pd.prod, ops[10]["id"], "pause", t0 + timedelta(minutes=6))
    assert (r["status"], r["code"]) == ("conflict", "operation_done")
    # Отменённый наряд — конфликт для любых событий из очереди.
    wo = pull(pd.st.proj.rp)["work_orders"][0]
    pd.st.proj.rp.ok("wo.cancel", {"id": wo["id"], "reason": "Заказчик отказался"}, expected_version=wo["version"])
    pd.st.proj.rp.cmd("wo.assign", {"id": ops[20]["id"], "assignee_id": pd.prod.user_id}, expected_version=1)
    r = ev(pd.prod2, ops[20]["id"], "start", t0 + timedelta(minutes=10))
    assert (r["status"], r["code"]) == ("conflict", "work_order_closed")


def test_assign_rules(world, pd):
    res = release(pd, approved_tp(pd), qty=1, assignees={})["result"]
    ops = ops_of(res)
    assert ops[10]["assignee_id"] is None
    rp = pd.st.proj.rp
    assert pd.st.proj.eng.cmd("wo.assign", {"id": ops[10]["id"], "assignee_id": pd.prod.user_id},
                              expected_version=1)["status"] == "denied"
    assert rp.cmd("wo.assign", {"id": ops[10]["id"], "assignee_id": pd.st.proj.eng.user_id},
                  expected_version=1)["code"] == "assignee_not_producer"
    o = rp.ok("wo.assign", {"id": ops[10]["id"], "assignee_id": pd.prod.user_id}, expected_version=1)["wo_operation"]
    assert (o["assignee_id"], o["version"]) == (pd.prod.user_id, 2)
    assert rp.ok("wo.assign", {"id": ops[10]["id"], "assignee_id": pd.prod.user_id}, expected_version=2).get("unchanged")
    assert rp.cmd("wo.assign", {"id": ops[10]["id"], "assignee_id": None}, expected_version=1)["code"] == "version_conflict"


# ====================================================================== замена в снимке
def test_substitution_needs_preview_hash_reason_authority_and_keeps_snapshot(world, pd):
    res = release(pd, approved_tp(pd), qty=2)["result"]
    wo = res["work_order"]
    u = pd.st.proj
    sub = {"id": wo["id"], "line_id": pd.line_cable, "code": "КВВГнг-4x1.5", "name": "Кабель КВВГнг 4×1,5", "unit": "м",
           "qty": "22", "reason": "Нет на складе, согласовано с ГИП", "preview_hash": wo["spec_hash"]}
    # Права: ГИП проекта или технолог; РП и исполнитель — нет.
    for s in (u.rp, pd.prod, u.eng):
        assert s.cmd("wo.substitute", sub, expected_version=1)["status"] == "denied"
    assert u.gip.cmd("wo.substitute", {**sub, "reason": " "}, expected_version=1)["code"] == "field_required"
    assert u.gip.cmd("wo.substitute", {**sub, "line_id": "нет"}, expected_version=1)["code"] == "line_not_found"
    assert u.gip.cmd("wo.substitute", {**sub, "preview_hash": "0" * 64}, expected_version=1)["code"] == "preview_stale"
    w2 = u.gip.ok("wo.substitute", sub, expected_version=1)["work_order"]
    # Снимок не изменился; действующий состав — с заменой; запись замены хранит было/стало и хэши.
    assert w2["snapshot"] == wo["snapshot"] and w2["snapshot_hash"] == wo["snapshot_hash"] and w2["snapshot_ok"]
    line = next(x for x in w2["spec"] if x["line_id"] == pd.line_cable)
    assert (line["code"], line["qty"], line["substituted"], line["substitution_seq"]) == ("КВВГнг-4x1.5", "22.0000", True, 1)
    s1 = w2["substitutions"][0]
    assert (s1["old"]["code"], s1["new"]["qty"], s1["preview_hash"], s1["result_hash"]) == (
        CABLE, "22.0000", wo["spec_hash"], w2["spec_hash"])
    assert s1["authority"].startswith("project_role:gip#assignment:")
    # Устаревший предпросмотр (технолог видел состав до замены ГИП) — конфликт, а не замена «вслепую».
    r = pd.tech.cmd("wo.substitute", {**sub, "qty": "23"}, expected_version=2)
    assert (r["status"], r["code"]) == ("conflict", "preview_stale")
    w3 = pd.tech.ok("wo.substitute", {**sub, "qty": "23", "preview_hash": w2["spec_hash"]}, expected_version=2)["work_order"]
    assert len(w3["substitutions"]) == 2 and w3["substitutions"][1]["authority"].startswith("global_role:technologist")
    assert pd.tech.cmd("wo.substitute", {**sub, "qty": "23", "preview_hash": w3["spec_hash"]},
                       expected_version=3)["code"] == "substitution_same"
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as c:
            c.execute(db.wo_substitutions.delete())


# ====================================================================== закрытие наряда
def _finish_all(world, pd, ops):
    """Выполнить обе операции наряда (по часам устройства — час назад)."""
    t0 = world.clock.now() - timedelta(hours=1)
    for s, no in ((pd.prod, 10), (pd.prod2, 20)):
        ev(s, ops[no]["id"], "start", t0)
        ev(s, ops[no]["id"], "finish", t0 + timedelta(minutes=10))


def test_close_requires_all_operations_and_issued_materials(world, pd):
    res = release(pd, approved_tp(pd), qty=2)["result"]
    wo, ops = res["work_order"], ops_of(res)
    rp, wh, st_ = pd.st.proj.rp, pd.st.wh, pd.st
    # Материалы: кабель — складская позиция (нужно 21 м), наладка — не складская (закрытию не мешает).
    m = {x["line_id"]: x for x in wo["materials"]}
    assert (m[pd.line_cable]["state"], m[pd.line_cable]["required"], m[pd.line_work]["state"]) == ("short", "21.0000", "not_stock")
    r = rp.cmd("wo.close", {"id": wo["id"]}, expected_version=1)
    assert r["code"] == "operations_not_done" and [x["op_no"] for x in r["details"]["operations"]] == [10, 20]
    _finish_all(world, pd, ops)
    wo = pull(rp)["work_orders"][0]
    assert wo["status"] == "completed"
    r = rp.cmd("wo.close", {"id": wo["id"]}, expected_version=wo["version"])
    assert r["code"] == "materials_not_issued" and r["details"]["materials"][0]["issued"] == "0.0000"
    # Выдача на наряд: наряд того же проекта и открытый.
    assert receive(wh, st_, "100")["status"] == "accepted"
    assert issue(wh, st_, "5", work_order_id=new_id())["code"] == "not_found"
    out = issue(wh, st_, "20", work_order_id=wo["id"])
    assert out["status"] == "accepted" and out["result"]["work_order"]["materials"][0]["issued"] == "20.0000"
    extra = issue(wh, st_, "1", work_order_id=wo["id"])["result"]["movement"]
    # Сторно выдачи уменьшает выданное — снова не хватает.
    assert wh.cmd("stock.reverse", {"id": new_id(), "movement_id": extra["id"], "reason": "Ошибка"})["status"] == "accepted"
    wo = pull(rp)["work_orders"][0]
    assert (wo["materials"][0]["issued"], wo["materials_ready"]) == ("20.0000", False)
    assert rp.cmd("wo.close", {"id": wo["id"]}, expected_version=wo["version"])["code"] == "materials_not_issued"
    issue(wh, st_, "1", work_order_id=wo["id"])
    # Закрывает РП; чужие роли — нет.
    assert pd.st.proj.gip.cmd("wo.close", {"id": wo["id"]}, expected_version=wo["version"])["status"] == "denied"
    c = rp.ok("wo.close", {"id": wo["id"]}, expected_version=wo["version"])["work_order"]
    assert (c["status"], c["closed_by"], c["materials_ready"]) == ("closed", rp.user_id, True)
    assert json.loads(audit(world, "wo.close")[-1]["details_json"])["event"] == "WorkOrderClosed"
    # Закрытый наряд: выдача и замены невозможны.
    assert issue(wh, st_, "1", work_order_id=wo["id"])["code"] == "wo_state"
    assert pd.st.proj.gip.cmd("wo.substitute", {"id": wo["id"], "line_id": pd.line_cable, "name": "x", "unit": "м",
                                                "qty": "1", "reason": "x", "preview_hash": c["spec_hash"]},
                              expected_version=c["version"])["code"] == "wo_state"


def test_unit_mismatch_blocks_close_until_substitution(world, pd):
    res = release(pd, approved_tp(pd), qty=1)["result"]
    wo, ops = res["work_order"], ops_of(res)
    u = pd.st.proj
    # Замена на позицию склада в другой единице — закрыть нельзя, пока единица не совпадёт со складом.
    w = u.gip.ok("wo.substitute", {"id": wo["id"], "line_id": pd.line_cable, "code": CABLE, "name": "Кабель", "unit": "бухта",
                                   "qty": "1", "reason": "Бухтой", "preview_hash": wo["spec_hash"]}, expected_version=1)["work_order"]
    assert next(x for x in w["materials"] if x["line_id"] == pd.line_cable)["state"] == "unit_mismatch"
    _finish_all(world, pd, ops)
    receive(pd.st.wh, pd.st, "50")
    issue(pd.st.wh, pd.st, "11", work_order_id=wo["id"])
    w = pull(u.rp)["work_orders"][0]
    r = u.rp.cmd("wo.close", {"id": wo["id"]}, expected_version=w["version"])
    assert r["code"] == "materials_not_issued" and r["details"]["materials"][0]["state"] == "unit_mismatch"
    w = u.gip.ok("wo.substitute", {"id": wo["id"], "line_id": pd.line_cable, "code": CABLE, "name": "Кабель", "unit": "м",
                                   "qty": "11", "reason": "В метрах", "preview_hash": w["spec_hash"]},
                 expected_version=w["version"])["work_order"]
    assert u.rp.ok("wo.close", {"id": wo["id"]}, expected_version=w["version"])["work_order"]["status"] == "closed"


def test_cancel_needs_reason_and_state(world, pd):
    res = release(pd, approved_tp(pd), qty=1)["result"]
    wo = res["work_order"]
    rp = pd.st.proj.rp
    assert rp.cmd("wo.cancel", {"id": wo["id"]}, expected_version=1)["code"] == "field_required"
    c = rp.ok("wo.cancel", {"id": wo["id"], "reason": "Перенос"}, expected_version=1)["work_order"]
    assert (c["status"], c["cancel_reason"]) == ("cancelled", "Перенос")
    assert rp.cmd("wo.cancel", {"id": wo["id"], "reason": "x"}, expected_version=2)["code"] == "wo_state"
    assert rp.cmd("wo.close", {"id": wo["id"]}, expected_version=2)["code"] == "wo_state"


# ====================================================================== видимость
def test_pull_visibility_production_sees_only_assigned_orders(world, pd):
    tp = approved_tp(pd)
    res = release(pd, tp, qty=1, assignees={"10": pd.prod.user_id})["result"]
    world.clock.advance(seconds=61)
    world.make_user("prod3", "production")
    p3 = world.login("prod3")
    a = pull(pd.prod)
    assert [w["id"] for w in a["work_orders"]] == [res["work_order"]["id"]] and len(a["wo_operations"]) == 2
    assert a["tech_processes"] == [] and a["tech_specs"] == []      # ТП исполнителю не нужен — у него снимок
    assert pull(p3)["work_orders"] == []
    # Склад видит наряды (выдача материалов); технолог — ТП и состав без цен; РП — справочник исполнителей.
    assert len(pull(pd.st.wh)["work_orders"]) == 1
    t = pull(pd.tech)
    assert [x["id"] for x in t["tech_processes"]] == [tp["id"]]
    assert t["tech_specs"][0]["id"] == pd.spec_id and "unit_price" not in t["tech_specs"][0]["lines"][0]
    # Проект технологу не виден целиком (задачи, документы), но комплекты для «Нового ТП» — с кодом и статусом проекта.
    assert t["projects"] == [] and [(k["code"], k["project_code"], k["project_status"]) for k in t["production_kits"]] == [
        ("АТХ", "П-100", "rkd_in_progress")]
    assert a["production_kits"] == []
    staff = {x["display_name"] for x in pull(pd.st.proj.rp)["production_staff"]}
    assert staff == {"PROD1", "PROD2", "PROD3"}
    assert pull(pd.prod)["production_staff"] == []
    # Посторонний (вне команды, без ролей производства) не видит ни ТП, ни наряды.
    o = pull(pd.st.proj.lead)
    assert (o["tech_processes"], o["work_orders"]) == ([], [])
    # История операции доступна её исполнителю.
    h = pd.prod.query("history", object_type="wo_operation", object_id=res["wo_operations"][0]["id"])
    assert h["status"] == "accepted"
    assert p3.query("history", object_type="wo_operation", object_id=res["wo_operations"][0]["id"])["status"] == "denied"
