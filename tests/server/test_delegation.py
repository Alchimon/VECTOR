"""Замещение, основания решений и сверка со счётом (D-34: ответы владельца на A-37 и A-38).

Правила (server/delegation.py, core/delegation.py, server/authz.py):
* носитель передаёт СВОЮ глобальную роль на срок с причиной; за отсутствующего — руководитель отдела или
  администратор (delegation.manage), но не себе; «Администратор» не передаётся; цепочек нет;
* замещающий действует ролью носителя, пока замещение действует и у носителя есть своя роль (иначе — «приостановлено»);
* каждое решение хранит основание: чьим правом (своя роль / замещение — за кого), неизменяемо;
* руководитель отдела видит заказы и согласования и может согласовать сам (резерв, «в курсе»);
* сверка со счётом: расхождение с расчётом — только с причиной, её видит согласующий.
"""
import pytest

from conftest import new_id
from test_purchasing import _W, _remember_world, approver, draft, line, pulled_order, supplier  # noqa: F401
from test_stock import st  # noqa: F401
from test_tasks_documents import proj  # noqa: F401
from vector.server import db


def deleg(s, to, role, until="2026-11-30", **kw):
    """Сырой ответ delegation.create от сеанса s."""
    return s.cmd("delegation.create", {"to_user_id": to, "role": role, "valid_until": until,
                                       "reason": kw.pop("reason", "Отпуск"), **kw})


def me(s):
    """Блок me из pull."""
    return s.query("pull")["result"]["me"]


def _tkp(world, rp):
    """Выигранный тендер и черновик ТКП от РП; вернуть id ТКП."""
    _, tender, _ = world.won_tender(rp)
    tkp = new_id()
    rp.ok("tkp.create", {"id": tkp, "tender_id": tender, "label": "ТКП-1", "amount": "100.00", "currency": "RUB"})
    return tkp


# ====================================================================== правила оформления
def test_create_rules_and_errors(world):
    head, lead, eng = world.login("head1"), world.login("lead1"), world.login("eng1")
    u = world.users
    assert deleg(head, u["head1"], "dept_head")["code"] == "self_delegation"
    assert deleg(head, u["lead1"], "admin")["code"] == "role_not_delegable"
    assert deleg(head, u["lead1"], "nope")["code"] == "bad_role"
    # Передать можно только свою роль.
    assert deleg(eng, u["lead1"], "dept_head")["code"] == "role_not_held"
    assert deleg(head, u["rp1"], "dept_head", until="2026-11-15")["code"] == "invalid_period"
    assert deleg(head, u["rp1"], "dept_head", until="2027-12-31")["code"] == "invalid_period"
    assert deleg(head, u["rp1"], "dept_head", reason="")["code"] == "field_required"
    r = head.cmd("delegation.create", {"to_user_id": u["lead1"], "role": "dept_head", "reason": "Отпуск"})
    assert r["code"] == "field_required"
    # За другого — только с delegation.manage; руководитель отдела не назначает замещающим себя.
    r = eng.cmd("delegation.create", {"from_user_id": u["lead1"], "to_user_id": u["eng1"], "role": "lead_engineer",
                                      "valid_until": "2026-11-30", "reason": "Болеет"})
    assert r["status"] == "denied"
    assert deleg(head, u["head1"], "lead_engineer", from_user_id=u["lead1"])["code"] == "self_assign"
    # Уже есть своя такая роль — замещение не нужно.
    assert deleg(world.login("rp1"), u["rp2"], "rp")["code"] == "already_has_role"
    d = head.ok("delegation.create", {"to_user_id": u["lead1"], "role": "dept_head", "valid_until": "2026-11-30",
                                      "reason": "Отпуск"})["delegation"]
    assert (d["state"], d["state_label"], d["valid_from"], d["from_user_id"]) == \
        ("active", "Действует", "2026-11-16", u["head1"])
    assert deleg(head, u["lead1"], "dept_head", until="2026-11-20")["code"] == "delegation_exists"
    # Цепочек нет: полученную замещением роль дальше не передают.
    assert deleg(lead, u["eng1"], "dept_head")["code"] == "role_not_held"
    # Онлайн-команды.
    cat = world.svc.contracts.catalog["commands"]
    assert cat["delegation.create"]["offline"] == cat["delegation.revoke"]["offline"] == "online_required"


def test_delegate_acts_with_basis_and_loses_right_when_holder_does(world):
    rp, head, lead = world.login("rp1"), world.login("head1"), world.login("lead1")
    u = world.users
    tkp = _tkp(world, rp)
    assert lead.cmd("tkp.approve", {"id": tkp}, 1)["status"] == "denied"
    head.ok("delegation.create", {"to_user_id": u["lead1"], "role": "dept_head", "valid_until": "2026-11-30",
                                  "reason": "Командировка"})
    m = me(lead)
    assert "dept_head" in m["roles"] and "dept_head" not in m["own_roles"]
    assert [(x["role"], x["from_user_id"], x["valid_until"]) for x in m["delegated_roles"]] == \
        [("dept_head", u["head1"], "2026-11-30")]
    lead.ok("tkp.approve", {"id": tkp}, 1)
    # Основание решения: замещение — за кого, по какой выдаче носителя.
    with world.svc.engine.begin() as c:
        b = c.execute(db.approval_bases.select().where(db.approval_bases.c.object_type == "tkp_approval")).one()
    assert (b.actor_id, b.role, b.on_behalf_of, b.grant_id, b.delegation_id) == \
        (u["lead1"], "dept_head", u["head1"], world.grants["head1"], 1)
    assert b.authority.startswith("delegated_role:dept_head")
    with pytest.raises(Exception):                       # основания неизменяемы
        with world.svc.engine.begin() as c:
            c.execute(db.approval_bases.delete())
    # Носитель лишился роли — согласование по его праву недействительно, замещение приостановлено.
    world.admin.ok("admin.revoke_role", {"grant_id": world.grants["head1"], "reason": "перевод"})
    r = rp.cmd("tkp.record_sent", {"id": tkp, "sent_on": "2026-11-16"}, 2)
    assert (r["status"], r["code"]) == ("rejected", "approval_invalid")
    assert "dept_head" not in me(lead)["roles"]
    d = next(x for x in lead.query("pull")["result"]["delegations"])
    assert (d["state"], d["state_label"]) == ("suspended", "Приостановлено: у носителя нет этой роли")


def test_planned_revoked_and_ended(world):
    head, lead, eng = world.login("head1"), world.login("lead1"), world.login("eng1")
    u = world.users
    d = head.ok("delegation.create", {"to_user_id": u["lead1"], "role": "dept_head", "valid_from": "2026-11-20",
                                      "valid_until": "2026-11-25", "reason": "Отпуск"})["delegation"]
    assert d["state"] == "planned" and "dept_head" not in me(lead)["roles"]
    world.clock.advance(days=4)
    head, lead, eng = world.login("head1"), world.login("lead1"), world.login("eng1")   # сеансы истекли
    assert "dept_head" in me(lead)["roles"]
    # Отменить может носитель, замещающий, оформивший или руководитель; посторонний — нет. Причина обязательна.
    assert eng.cmd("delegation.revoke", {"id": d["id"], "reason": "x"})["status"] == "denied"
    assert lead.cmd("delegation.revoke", {"id": d["id"]})["code"] == "field_required"
    assert lead.cmd("delegation.revoke", {"id": 999999, "reason": "x"})["code"] == "not_found"
    r = lead.ok("delegation.revoke", {"id": d["id"], "reason": "Вышел раньше"})["delegation"]
    assert r["state"] == "revoked" and "dept_head" not in me(lead)["roles"]
    assert head.cmd("delegation.revoke", {"id": d["id"], "reason": "x"})["code"] == "already_revoked"
    # Завершившееся отменять нечего.
    d2 = head.ok("delegation.create", {"to_user_id": u["eng1"], "role": "dept_head", "valid_until": "2026-11-21",
                                       "reason": "Отпуск"})["delegation"]
    world.clock.advance(days=2)
    head = world.login("head1", addr="10.0.0.8")
    assert head.cmd("delegation.revoke", {"id": d2["id"], "reason": "x"})["code"] == "delegation_ended"
    assert next(x for x in head.query("pull")["result"]["delegations"] if x["id"] == d2["id"])["state"] == "ended"


def test_head_delegates_for_absent_person_and_sees_coverage(world, st):  # noqa: F811
    head = world.login("head1")
    ap = approver()
    u = world.users
    pulled = head.query("pull")["result"]
    cov = {c["role"]: c for c in pulled["role_coverage"]}
    assert cov["po_approver"]["state"] == "single" and cov["admin"]["delegable"] is False
    assert world.login("eng1").query("pull")["result"]["role_coverage"] == []
    # Согласующий заболел и сам оформить не может — оформляет руководитель отдела (за него, не себе).
    d = head.ok("delegation.create", {"from_user_id": ap.user_id, "to_user_id": u["acc1"], "role": "po_approver",
                                      "valid_until": "2026-11-27", "reason": "Больничный"})["delegation"]
    assert d["created_by"] == u["head1"]
    cov = {c["role"]: c for c in head.query("pull")["result"]["role_coverage"]}
    assert cov["po_approver"]["state"] == "ok" and cov["po_approver"]["deputies"][0]["user_id"] == u["acc1"]
    # Замещающий видит своё замещение, посторонний — нет; руководитель — все.
    assert [x["id"] for x in world.login("acc1", addr="10.0.0.9").query("pull")["result"]["delegations"]] == [d["id"]]
    assert world.login("eng1", addr="10.0.0.9").query("pull")["result"]["delegations"] == []
    # Замещающий согласует заказ; в событии — за кого.
    sup = supplier(st.pr)
    o = draft(st, sup, [line(st.item, "10", "122")])
    o = st.pr.ok("po.submit", {"id": o["id"]}, expected_version=1)["purchase_order"]
    acc = world.login("acc1", addr="10.0.0.10")
    o = acc.ok("po.approve", {"id": o["id"]}, expected_version=o["version"])["purchase_order"]
    e = o["approval"]["events"][-1]
    assert (e["kind"], e["actor_id"], e["role"], e["on_behalf_of"], e["delegation_id"]) == \
        ("approve", u["acc1"], "po_approver", ap.user_id, d["id"])
    assert o["approval"]["events"][0]["role"] == "procurement"
    h = st.pr.query("history", object_type="purchase_order", object_id=o["id"])["result"]
    assert h[-1]["details"]["on_behalf_of"] == ap.user_id


def test_dept_head_sees_and_can_approve_as_backup(world, st):  # noqa: F811
    head = world.login("head1")
    sup = supplier(st.pr)
    o = draft(st, sup, [line(st.item, "10", "122")])
    o = st.pr.ok("po.submit", {"id": o["id"]}, expected_version=1)["purchase_order"]
    got = pulled_order(head, o["id"])
    assert got["approval"]["state"] == "submit" and got["total"] == "1220.0000"
    o = head.ok("po.approve", {"id": o["id"]}, expected_version=o["version"])["purchase_order"]
    e = o["approval"]["events"][-1]
    assert (e["role"], e["on_behalf_of"]) == ("dept_head", None)


# ====================================================================== сверка со счётом (A-38)
def test_invoice_reconciliation_requires_reason_on_mismatch(world, st):  # noqa: F811
    sup = supplier(st.pr)
    # 10 × 122 с НДС 22 %: 1220,00, НДС 220,00. Счёт совпадает — причина не нужна.
    o = draft(st, sup, [line(st.item, "10", "122")], invoice_gross="1220.00", invoice_vat="220")
    cc = o["calc_control"]
    assert (cc["invoice"]["state"], cc["needs_reason"], cc["check_failed"]) == ("match", False, False)
    assert (o["invoice_gross"], o["invoice_vat"]) == ("1220.0000", "220.0000")
    o = st.pr.ok("po.submit", {"id": o["id"]}, expected_version=1)["purchase_order"]
    assert o["approval"]["events"][-1]["invoice_state"] == "match"
    # Расхождение на копейку (поставщик считает НДС от итога) — только с причиной; её видит согласующий.
    o2 = draft(st, sup, [line(st.item, "3", "33.33")], invoice_gross="99.99", invoice_vat="18.04")
    assert o2["calc_control"]["invoice"]["state"] == "mismatch"
    r = st.pr.cmd("po.submit", {"id": o2["id"]}, expected_version=1)
    assert (r["code"], r["details"]["field"], r["details"]["invoice"]["state"]) == \
        ("invoice_reason_required", "invoice_reason", "mismatch")
    o2 = st.pr.ok("po.submit", {"id": o2["id"], "invoice_reason": "НДС в счёте от итога"},
                  expected_version=1)["purchase_order"]
    seen = pulled_order(approver(), o2["id"])["approval"]["events"][-1]
    assert (seen["invoice_state"], seen["invoice_reason"]) == ("mismatch", "НДС в счёте от итога")
    assert seen["diff_vat"] is not None
    # Склад денег не видит, в том числе разниц по счёту.
    w = pulled_order(st.wh, o2["id"])
    assert w["invoice_gross"] is None and w["calc_control"] is None
    assert w["approval"]["events"][-1]["diff_vat"] is None
    # Сумма по счёту — неотрицательная, до копеек; на согласовании не меняется.
    r = st.pr.cmd("po.create", {"id": new_id(), "supplier_id": sup["id"], "currency": "RUB", "vat_mode": "included",
                                "lines": [], "invoice_gross": "-1"})
    assert r["status"] == "rejected"
    r = st.pr.cmd("po.update", {"id": o2["id"], "invoice_gross": "100.00"}, expected_version=o2["version"])
    assert r["code"] == "order_on_approval"
