"""Оргструктура (этап 7, R-21; D-44 — ответ владельца на A-48 п. 1): подразделения (филиалы, структуры, отделы),
связи «входит в» без кругов, руководители и сотрудники, архив, журнал; маршрут дневного отчёта — руководителю
своего подразделения (или вышестоящего), а не всем руководителям отделов; замещение руководителя; pull."""
import sqlalchemy as sa

from conftest import new_id
from test_tasks_documents import proj  # noqa: F401
from test_worktime import SC, create, notes, pull
from vector.server import db


def unit(admin, kind="department", name="Отдел", parent=None, head=None, code=None, uid=None):
    """Создать подразделение от имени администратора; вернуть его id."""
    uid = uid or new_id()
    admin.ok("org.unit_create", {"id": uid, "kind": kind, "name": name, "parent_id": parent, "head_user_id": head,
                                 "code": code})
    return uid


def org(world):
    """Филиал «Юг» → «Инженерная дирекция» (руководитель dir1) → «Отдел АСУ ТП» (head1, в нём eng1 и lead1) и
    «Конструкторский отдел» (head2). dir1 и head2 — с ролью «Руководитель отдела»."""
    for lg in ("dir1", "head2"):
        world.users[lg] = world.make_user(lg, "dept_head")
    a, u = world.admin, world.users
    f = unit(a, "branch", "Филиал Юг", code="ЮГ")
    s = unit(a, "structure", "Инженерная дирекция", f, u["dir1"])
    d1 = unit(a, "department", "Отдел АСУ ТП", s, u["head1"], code="ОАСУ")
    d2 = unit(a, "department", "Конструкторский отдел", s, u["head2"])
    for lg, d in (("eng1", d1), ("lead1", d1), ("head1", d1), ("head2", d2)):
        a.ok("org.member_set", {"user_id": u[lg], "unit_id": d, "position": "Инженер" if lg == "eng1" else None})
    return {"F": f, "S": s, "D1": d1, "D2": d2}


def test_admin_builds_tree_with_checks(world):
    """Создать, изменить с версией, перенос без круга, код не повторяется; не администратор — отказ; журнал."""
    a = world.admin
    o = org(world)
    r = a.cmd("org.unit_create", {"id": new_id(), "kind": "department", "name": "Ещё", "code": "оасу"})
    assert r["code"] == "code_exists"
    r = a.cmd("org.unit_update", {"id": o["S"], "kind": "structure", "name": "Инженерная дирекция", "parent_id": o["D1"],
                                  "head_user_id": world.users["dir1"]}, expected_version=1)
    assert r["code"] == "cycle"
    r = a.cmd("org.unit_create", {"id": new_id(), "kind": "division", "name": "X"})
    assert r["code"] == "invalid_kind"
    r = a.cmd("org.unit_create", {"id": new_id(), "kind": "department", "name": "X", "parent_id": new_id()})
    assert r["code"] == "parent_not_found"
    assert world.login("head1").cmd("org.unit_create", {"id": new_id(), "kind": "department", "name": "X"})["status"] == "denied"
    # Переименование и перенос второго отдела прямо в филиал.
    u = a.ok("org.unit_update", {"id": o["D2"], "kind": "department", "name": "КБ", "parent_id": o["F"],
                                 "head_user_id": world.users["head2"]}, expected_version=1)["org_unit"]
    assert (u["name"], u["parent_id"], u["version"], u["members"]) == ("КБ", o["F"], 2, 1)
    assert a.cmd("org.unit_update", {"id": o["D2"], "kind": "department", "name": "КБ-2"}, expected_version=1)["code"] == "version_conflict"
    with world.svc.engine.connect() as c:
        ev = [r.event for r in c.execute(sa.select(db.org_events.c.event).where(db.org_events.c.object_id == o["D2"]))]
    assert ev == ["created", "updated"]
    # Руководитель получил уведомление о назначении.
    assert notes(world, world.users["head1"], "org_head")


def test_archive_only_empty_and_restore(world):
    """В архив — только пустое; архивное не правится и в него не включают; вернуть — можно."""
    a, o = world.admin, org(world)
    assert a.cmd("org.unit_archive", {"id": o["S"], "reason": "Реорганизация"}, expected_version=1)["code"] == "has_children"
    assert a.cmd("org.unit_archive", {"id": o["D2"], "reason": "Реорганизация"}, expected_version=1)["code"] == "has_members"
    a.ok("org.member_set", {"user_id": world.users["head2"], "unit_id": o["D1"]})
    assert a.cmd("org.unit_archive", {"id": o["D2"], "reason": ""}, expected_version=1)["code"] == "required"
    u = a.ok("org.unit_archive", {"id": o["D2"], "reason": "Реорганизация"}, expected_version=1)["org_unit"]
    assert u["active"] is False
    assert a.cmd("org.member_set", {"user_id": world.users["eng1"], "unit_id": o["D2"]})["code"] == "archived"
    assert a.cmd("org.unit_update", {"id": o["D2"], "kind": "department", "name": "Y"}, expected_version=2)["code"] == "archived"
    assert a.ok("org.unit_restore", {"id": o["D2"]}, expected_version=2)["org_unit"]["active"] is True


def test_member_move_and_remove_notify(world):
    """Перевод и вывод из оргструктуры; сотрудник узнаёт уведомлением; смена должности — без уведомления."""
    a, o, eng = world.admin, org(world), world.users["eng1"]
    assert len(notes(world, eng, "org_member")) == 1
    m = a.ok("org.member_set", {"user_id": eng, "unit_id": o["D1"], "position": "Ведущий инженер"})["org_member"]
    assert m["position"] == "Ведущий инженер" and len(notes(world, eng, "org_member")) == 1
    a.ok("org.member_set", {"user_id": eng, "unit_id": o["D2"]})
    assert len(notes(world, eng, "org_member")) == 2
    assert a.ok("org.member_set", {"user_id": eng, "unit_id": None})["org_member"] is None
    with world.svc.engine.connect() as c:
        ev = [r.event for r in c.execute(sa.select(db.org_events.c.event).where(db.org_events.c.object_id == eng)
                                         .order_by(db.org_events.c.id))]
    assert ev == ["placed", "position", "moved", "removed"]


def test_report_goes_to_own_unit_head_not_all(world, proj):  # noqa: F811
    """Отчёт инженера — руководителю его отдела (и вышестоящим видно), но не руководителю соседнего отдела; тот не
    видит его в pull и не может принять; свой руководитель принимает (D-44)."""
    o = org(world)
    rid = create(proj.eng)["result"]["daily_report"]["id"]
    proj.eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)
    u = world.users
    assert notes(world, u["head1"], "report_submitted") and not notes(world, u["head2"], "report_submitted")
    assert not notes(world, u["dir1"], "report_submitted")
    head2, dir1 = world.login("head2"), world.login("dir1")
    assert all(x["id"] != rid for x in pull(head2)["daily_reports"])
    assert any(x["id"] == rid for x in pull(dir1)["daily_reports"])          # вышестоящий видит (статистика)
    r = head2.cmd("report.review", {"id": rid, "decision": "accept"}, expected_version=2)
    assert (r["status"], r["code"]) == ("denied", "not_your_unit")
    rep = world.login("head1").ok("report.review", {"id": rid, "decision": "accept"}, expected_version=2)["daily_report"]
    assert rep["status"] == "accepted"
    assert pull(proj.eng)["org_me"]["reviewer_id"] == u["head1"] and o["D1"] == pull(proj.eng)["org_me"]["unit_id"]


def test_head_report_goes_up_and_unplaced_keeps_old_rule(world, proj):  # noqa: F811
    """Отчёт руководителя отдела — руководителю дирекции; сотрудник вне оргструктуры — всем руководителям отделов."""
    org(world)
    u = world.users
    head1 = world.login("head1")
    rid = create(head1)["result"]["daily_report"]["id"]
    head1.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)
    assert notes(world, u["dir1"], "report_submitted") and not notes(world, u["head2"], "report_submitted")
    world.login("dir1").ok("report.review", {"id": rid, "decision": "accept"}, expected_version=2)
    # Вне оргструктуры — бухгалтер acc1: уведомление всем руководителям отделов, проверяет любой.
    acc = world.login("acc1")
    rid = create(acc)["result"]["daily_report"]["id"]
    acc.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)
    assert notes(world, u["head2"], "report_submitted")
    world.login("head2").ok("report.review", {"id": rid, "decision": "accept"}, expected_version=2)


def test_no_head_in_chain_falls_back_and_deputy_reviews(world, proj):  # noqa: F811
    """Нет руководителей по цепочке — общее правило (отчёт не «повисает», A-37); замещающий руководителя отдела
    получает отчёт и принимает его «за» руководителя (D-34)."""
    a, u = world.admin, world.users
    lone = unit(a, "department", "Отдел без руководителя")
    a.ok("org.member_set", {"user_id": u["acc1"], "unit_id": lone})
    acc = world.login("acc1")
    rid = create(acc)["result"]["daily_report"]["id"]
    acc.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)
    assert notes(world, u["head1"], "report_submitted")
    # Замещение: head1 передаёт роль lead1 (тот в его же отделе, но не руководитель).
    o = org(world)
    assert o
    world.login("head1").ok("delegation.create", {"to_user_id": u["lead1"], "role": "dept_head",
                                                  "valid_until": "2026-11-30", "reason": "Отпуск"})
    rid = create(proj.eng)["result"]["daily_report"]["id"]
    proj.eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)
    assert any(n["object_id"] == rid for n in notes(world, u["lead1"], "report_submitted"))
    lead = world.login("lead1")
    assert any(x["id"] == rid for x in pull(lead)["daily_reports"])
    rep = lead.ok("report.review", {"id": rid, "decision": "accept"}, expected_version=2)["daily_report"]
    assert rep["history"][-1]["on_behalf_of"] == u["head1"]


def test_pull_org_for_everyone(world, proj):  # noqa: F811
    """Оргструктура приходит всем вошедшим (с именами людей); право править — только администратору."""
    o = org(world)
    p = pull(proj.eng)
    assert {x["id"] for x in p["org_units"]} == set(o.values())
    assert p["org_me"]["manage"] is False and p["people"]
    d1 = next(x for x in p["org_units"] if x["id"] == o["D1"])
    assert (d1["kind_label"], d1["members"], d1["code"]) == ("Отдел", 3, "ОАСУ")
    assert any(m["user_id"] == world.users["eng1"] and m["position"] == "Инженер" for m in p["org_members"])
    assert p["org_events"][0]["event_label"]
    assert pull(world.admin)["org_me"]["manage"] is True


def test_head_without_review_role_is_skipped(world, proj):  # noqa: F811
    """Руководитель подразделения без роли «Руководитель отдела» (ГИП во главе группы) отчёт принять не может — он
    пропускается, отчёт уходит выше; выше никого — по общему правилу (отчёт не «повисает», A-37)."""
    a, u = world.admin, world.users
    o = org(world)
    grp = unit(a, "department", "Группа ГИП", o["D1"], u["gip1"])
    a.ok("org.member_set", {"user_id": u["eng1"], "unit_id": grp})
    assert pull(proj.eng)["org_me"]["reviewer_id"] == u["head1"]
    rid = create(proj.eng)["result"]["daily_report"]["id"]
    proj.eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)
    assert notes(world, u["head1"], "report_submitted") and not notes(world, u["gip1"], "report_submitted")
    # Корневой отдел, где руководитель — без роли: проверяют все руководители отделов.
    top = unit(a, "department", "Бухгалтерия", None, u["gip1"])
    a.ok("org.member_set", {"user_id": u["acc1"], "unit_id": top})
    acc = world.login("acc1")
    rid = create(acc)["result"]["daily_report"]["id"]
    acc.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)
    assert notes(world, u["head2"], "report_submitted")
    world.login("head2").ok("report.review", {"id": rid, "decision": "accept"}, expected_version=2)
