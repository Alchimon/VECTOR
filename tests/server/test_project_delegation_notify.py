"""Замещение ролей в проекте и уведомления (D-35: ответ владельца на A-40 — «да» и «да»).

Правила (server/delegation.py, server/authz.py, server/work.py, server/notify.py):
* ГИП, РП или участник передаёт свою роль В ПРОЕКТЕ на срок с причиной; за отсутствующего — руководитель отдела или
  администратор; замещение действует только в этом проекте и никогда не даёт глобальной роли;
* оно действует, пока у носителя есть назначение с этой ролью (иначе — «приостановлено»);
* замещающему участника можно передать задачи, и он ведёт задачи носителя («свои» переходы — за него);
* покрытие ролей показывает ГИП и РП каждого проекта;
* уведомления: заказ на согласовании → согласующим и руководителям отдела (и их замещающим); решение →
  создателю/отправившему и руководителям; замещение → замещающему, носителю и руководителям; автор действия
  уведомление о своём действии не получает; отказ команды — уведомления нет; денег в тексте нет.
"""
import sqlalchemy as sa

from conftest import new_id
from test_delegation import deleg, me
from test_purchasing import _W, _remember_world, approver, draft, line, supplier  # noqa: F401
from test_stock import st  # noqa: F401
from test_tasks_documents import audit, proj  # noqa: F401
from vector.server import db


def pdeleg(s, to, role, pid, **kw):
    """Сырой ответ delegation.create для роли в проекте pid."""
    return deleg(s, to, role, project_id=pid, **kw)


def notes(s, kind=None):
    """Уведомления пользователя s из pull (новые сверху), по желанию — только вида kind."""
    r = s.query("pull")["result"]
    return [n for n in r["notifications"] if kind is None or n["kind"] == kind]


def _assignment(world, pid, login, role):
    """id действующего назначения пользователя login с ролью role в проекте pid."""
    t = db.project_assignments
    with world.svc.engine.connect() as c:
        return c.execute(sa.select(t.c.id).where(t.c.project_id == pid, t.c.user_id == world.users[login],
                                                 t.c.project_role == role, t.c.revoked_at.is_(None))).scalar_one()


# ====================================================================== замещение в проекте
def test_project_role_rules_and_errors(world, proj):  # noqa: F811
    u = world.users
    pid = proj.pid
    # Роль в проекте — только gip / rp / participant; проект должен существовать.
    assert pdeleg(proj.gip, u["lead1"], "dept_head", pid)["code"] == "bad_project_role"
    assert pdeleg(proj.gip, u["lead1"], "gip", new_id())["code"] == "not_found"
    # Передать можно только свою роль в этом проекте (у eng1 — участник, не ГИП).
    r = pdeleg(proj.eng, u["lead1"], "gip", pid)
    assert r["code"] == "role_not_held" and "ГИП в проекте П-100" in r["message_ru"]
    # У замещающего уже есть такая роль в этом проекте — замещение не нужно.
    proj.rp.ok("project.assign", {"project_id": pid, "user_id": u["acc1"], "project_role": "participant", "reason": "x"})
    assert pdeleg(proj.eng, u["acc1"], "participant", pid)["code"] == "already_has_role"
    d = proj.gip.ok("delegation.create", {"to_user_id": u["lead1"], "role": "gip", "project_id": pid,
                                          "valid_until": "2026-11-30", "reason": "Отпуск"})["delegation"]
    assert (d["project_id"], d["project_code"], d["role"], d["state"]) == (pid, "П-100", "gip", "active")
    assert pdeleg(proj.gip, u["lead1"], "gip", pid, until="2026-11-20")["code"] == "delegation_exists"
    # Цепочек нет: полученную замещением роль в проекте дальше не передают.
    assert pdeleg(proj.lead, u["rp2"], "gip", pid)["code"] == "role_not_held"
    # Посторонний за другого не оформляет.
    r = proj.eng.cmd("delegation.create", {"from_user_id": u["gip1"], "to_user_id": u["rp2"], "role": "gip",
                                           "project_id": pid, "valid_until": "2026-11-30", "reason": "x"})
    assert r["status"] == "denied"


def test_project_deputy_acts_only_in_that_project_and_never_globally(world, proj):  # noqa: F811
    u = world.users
    # Второй проект — замещение в первом не должно открывать его.
    _, tender2, _ = world.won_tender(proj.rp)
    pid2 = new_id()
    proj.rp.ok("project.start_rkd", {"id": pid2, "tender_id": tender2, "code": "П-200", "title": "Другой"})
    kit = {"id": new_id(), "project_id": proj.pid, "code": "ЭМ", "title": "Электромонтаж"}
    assert proj.lead.cmd("kit.create", kit)["status"] == "denied"
    d = proj.gip.ok("delegation.create", {"to_user_id": u["lead1"], "role": "gip", "project_id": proj.pid,
                                          "valid_until": "2026-11-30", "reason": "Командировка"})["delegation"]
    proj.lead.ok("kit.create", kit)
    a = audit(world, "kit.create")[-1]
    assert a["authority"] == (f"delegated_project_role:gip#assignment:{_assignment(world, proj.pid, 'gip1', 'gip')}"
                              f"#delegation:{d['id']}")
    pulled = proj.lead.query("pull")["result"]
    assert [p["id"] for p in pulled["projects"]] == [proj.pid]          # П-200 не видно
    m = pulled["me"]
    assert "gip" not in m["roles"]                                     # глобальной роли ГИП нет
    assert [(x["role"], x["project_code"], x["from_user_id"]) for x in m["delegated_project_roles"]] == \
        [("gip", "П-100", u["gip1"])]
    assert proj.lead.cmd("kit.create", {**kit, "id": new_id(), "project_id": pid2, "code": "К"})["status"] == "denied"
    # Команда проекта видит, кто сейчас замещает.
    deps = proj.rp.query("pull")["result"]["project_deputies"]
    assert [(x["user_id"], x["for_user_id"], x["project_role"]) for x in deps] == [(u["lead1"], u["gip1"], "gip")]
    # Носителя сняли с проекта — замещение приостановлено, права у замещающего нет.
    world.admin.ok("project.revoke_assignment", {"assignment_id": _assignment(world, proj.pid, "gip1", "gip"),
                                                 "reason": "Перевод"})
    assert proj.lead.cmd("kit.create", {**kit, "id": new_id(), "code": "Э2"})["status"] == "denied"
    got = next(x for x in proj.lead.query("pull")["result"]["delegations"] if x["id"] == d["id"])
    assert got["state"] == "suspended"
    assert proj.lead.query("pull")["result"]["me"]["delegated_project_roles"] == []


def test_deputy_of_participant_takes_over_tasks(world, proj):  # noqa: F811
    u = world.users
    tid = new_id()
    proj.rp.ok("task.create", {"id": tid, "kit_id": proj.kit, "title": "Схема", "assignee_user_id": u["eng1"]})
    # lead1 вне команды: задачу ему не передать и вести её он не может.
    tid2 = new_id()
    r = proj.rp.cmd("task.create", {"id": tid2, "kit_id": proj.kit, "title": "Т2", "assignee_user_id": u["lead1"]})
    assert r["code"] == "assignee_not_in_project"
    assert proj.lead.cmd("task.transition", {"id": tid, "to": "in_progress"}, 1)["status"] == "denied"
    d = proj.eng.ok("delegation.create", {"to_user_id": u["lead1"], "role": "participant", "project_id": proj.pid,
                                          "valid_until": "2026-11-30", "reason": "Больничный"})["delegation"]
    # Замещающий ведёт задачи носителя — только «свои» переходы и с основанием «за кого».
    proj.lead.ok("task.transition", {"id": tid, "to": "in_progress"}, 1)
    assert audit(world, "task.transition")[-1]["authority"].endswith(f"#delegation:{d['id']}")
    assert proj.lead.cmd("task.transition", {"id": tid, "to": "cancelled", "reason": "x"}, 2)["status"] == "denied"
    # И задачу можно передать замещающему.
    proj.rp.ok("task.create", {"id": tid2, "kit_id": proj.kit, "title": "Т2", "assignee_user_id": u["lead1"]})


def test_head_delegates_project_role_and_sees_project_coverage(world, proj):  # noqa: F811
    head, u = world.login("head1"), world.users

    def cov():
        rows = head.query("pull")["result"]["role_coverage"]
        return {(c["role"], c["project_code"]): c for c in rows if c["scope"] == "project"}
    c = cov()
    assert c[("gip", "П-100")]["state"] == "single" and c[("rp", "П-100")]["holders"] == [u["rp1"]]
    # ГИП заболел — руководитель отдела оформляет замещение за него (не себе).
    assert pdeleg(head, u["head1"], "gip", proj.pid, from_user_id=u["gip1"])["code"] == "self_assign"
    d = head.ok("delegation.create", {"from_user_id": u["gip1"], "to_user_id": u["lead1"], "role": "gip",
                                      "project_id": proj.pid, "valid_until": "2026-11-27",
                                      "reason": "Больничный"})["delegation"]
    c = cov()[("gip", "П-100")]
    assert c["state"] == "ok" and c["deputies"][0]["delegation_id"] == d["id"]
    # Глобальная роль и роль в проекте с тем же кодом — разные замещения (не «уже оформлено»).
    assert {x["scope"] for x in head.query("pull")["result"]["role_coverage"]} == {"global", "project"}


# ====================================================================== уведомления
def test_po_notifications_go_to_approvers_heads_and_deputies(world, st):  # noqa: F811
    u = world.users
    head, ap = world.login("head1"), approver()
    # На время отпуска руководителя отдела его замещает lead1 — уведомления приходят и ему.
    head.ok("delegation.create", {"to_user_id": u["lead1"], "role": "dept_head", "valid_until": "2026-11-30",
                                  "reason": "Отпуск"})
    lead = world.login("lead1", addr="10.0.0.31")
    assert [n["kind"] for n in notes(lead)] == ["delegation_created"]     # «Вам передана роль…»
    sup = supplier(st.pr)
    o = draft(st, sup, [line(st.item, "10", "122")])
    # Отказ команды — уведомления нет (у второго заказа расхождение со счётом без причины).
    o2 = draft(st, sup, [line(st.item, "3", "33.33")], invoice_gross="99.99", invoice_vat="18.04")
    assert st.pr.cmd("po.submit", {"id": o2["id"]}, expected_version=1)["code"] == "invoice_reason_required"
    assert notes(ap, "po_submitted") == []
    o = st.pr.ok("po.submit", {"id": o["id"], "comment": "Срочно"}, expected_version=1)["purchase_order"]
    for s in (ap, head, lead):
        n = notes(s, "po_submitted")
        assert len(n) == 1 and n[0]["title"] == f"Заказ № {o['order_no']} ждёт согласования"
        assert n[0]["object_type"] == "purchase_order" and n[0]["object_id"] == o["id"] and n[0]["read_at"] is None
        assert "Поставщик ЭТМ" in n[0]["body"] and "Срочно" in n[0]["body"]
        assert "1220" not in n[0]["body"] and "1 220" not in n[0]["body"]          # денег в тексте нет
    assert notes(st.pr) == [] and notes(st.wh) == []       # автор и посторонние — нет
    # Решение согласующего — закупке (создатель и отправивший) и руководителям; самому согласующему — нет.
    o = ap.ok("po.approve", {"id": o["id"], "comment": "Ок"}, expected_version=o["version"])["purchase_order"]
    n = notes(st.pr, "po_approved")
    assert len(n) == 1 and "Согласовал(а): AP1. Комментарий: Ок. Можно" in n[0]["body"]
    assert len(notes(head, "po_approved")) == 1 and notes(ap, "po_approved") == []
    # Отзыв с согласования — согласующим и руководителям.
    st.pr.ok("po.withdraw", {"id": o["id"], "reason": "Меняем количество"}, expected_version=o["version"])
    assert "Меняем количество" in notes(ap, "po_withdrawn")[0]["body"]
    # Возврат с причиной.
    o3 = draft(st, sup, [line(st.item, "1", "122")])
    o3 = st.pr.ok("po.submit", {"id": o3["id"]}, expected_version=1)["purchase_order"]
    ap.ok("po.return", {"id": o3["id"], "reason": "Нет договора"}, expected_version=o3["version"])
    assert "Причина: Нет договора" in notes(st.pr, "po_returned")[0]["body"]


def test_delegation_notifications_and_mark_read(world):
    u = world.users
    head, lead, eng = world.login("head1"), world.login("lead1"), world.login("eng1")
    # Руководитель отдела оформляет замещение за ведущего инженера: замещающему и носителю — уведомления.
    d = head.ok("delegation.create", {"from_user_id": u["lead1"], "to_user_id": u["eng1"], "role": "lead_engineer",
                                      "valid_until": "2026-11-30", "reason": "Отпуск"})["delegation"]
    got = notes(eng, "delegation_created")
    assert got[0]["title"] == "Вам передана роль «Ведущий инженер» с 16.11.2026 по 30.11.2026"
    assert "Вы замещаете: LEAD1" in got[0]["body"] and "Оформил(а): HEAD1" in got[0]["body"]
    assert notes(lead, "delegation_created")[0]["title"].startswith("За вас оформлено замещение")
    assert notes(head) == []                                # автор не уведомляется
    # Носитель отменяет — замещающему и руководителю.
    lead.ok("delegation.revoke", {"id": d["id"], "reason": "Вышел"})
    assert "Причина отмены: Вышел" in notes(eng, "delegation_revoked")[0]["body"]
    assert len(notes(head, "delegation_revoked")) == 1
    # Прочитать: только свои; чужие id молча пропускаются; all — все.
    r = eng.query("pull")["result"]
    assert r["notifications_unread"] == 2
    head_ids = [n["id"] for n in notes(head)]
    first = r["notifications"][0]["id"]
    res = eng.ok("notification.read", {"ids": [first] + head_ids})
    assert (res["read"], res["unread"]) == (1, 1)
    assert notes(head)[0]["read_at"] is None
    assert eng.ok("notification.read", {"all": True}) == {"read": 1, "unread": 0}
    assert eng.query("pull")["result"]["notifications_unread"] == 0
    assert eng.cmd("notification.read", {})["code"] == "field_required"
    assert eng.cmd("notification.read", {"ids": ["1"]})["code"] == "field_required"
    assert eng.cmd("notification.read", {"all": "yes"})["code"] == "invalid_type"
    cat = world.svc.contracts.catalog["commands"]
    assert cat["notification.read"]["offline"] == "online_required"


def test_notifications_expire_from_pull_after_90_days(world):
    u = world.users
    head, eng = world.login("head1"), world.login("eng1")
    head.ok("delegation.create", {"to_user_id": u["eng1"], "role": "dept_head", "valid_until": "2026-11-20",
                                  "reason": "Отпуск"})
    assert len(notes(eng)) == 1
    world.clock.advance(days=91)
    eng = world.login("eng1", addr="10.0.0.40")
    r = eng.query("pull")["result"]
    assert (r["notifications"], r["notifications_unread"]) == ([], 0)
    with world.svc.engine.connect() as c:                  # запись не удалена — просто не приходит
        assert c.execute(sa.select(sa.func.count()).select_from(db.notifications)).scalar_one() >= 1
