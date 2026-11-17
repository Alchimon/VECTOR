"""UI-проверки D-35 (ответы владельца на A-40): замещение ролей в проекте и уведомления — настоящий QML, сервер
in-process, демо-данные.

Демо: заказ № 5 отправлен на согласование — уведомления согласующему (v.zaitseva) и руководителю отдела (e.orlova);
p.volkov (ГИП П-2026-014) передал роль ГИП m.lebedev на 7 дней — уведомления m.lebedev и e.orlova;
a.smirnova — РП в двух проектах.
"""
import pytest
from PySide6.QtCore import QMetaObject

from tests.ui.test_purchasing_ui import _call, _popup, _v, qml_warnings  # noqa: F401

pytestmark = pytest.mark.ui


def _visible(ui, prop):
    """Видимый элемент со свойством prop (страница или панель)."""
    return next(i for i in ui.items(prop) if i.isVisible())


def test_head_gets_notification_and_opens_order(ui, qml_warnings):  # noqa: F811
    """Руководитель отдела видит «заказ на согласовании», «Открыть» ведёт к заказу и отмечает прочитанным."""
    ui.login("e.orlova")
    nov = _v(ui.backend, "notificationsOverview")
    assert nov["unread"] >= 2
    kinds = [n["kind"] for n in nov["items"]]
    assert "po_submitted" in kinds and "delegation_created" in kinds
    sub = next(n for n in nov["items"] if n["kind"] == "po_submitted")
    assert sub["page"] == "procurement" and sub["unread"] and "№ 5" in sub["title"]
    # Без денег в тексте уведомления (сумму видят только те, кому положено, — в карточке заказа).
    assert "RUB" not in sub["title"] + (sub["body"] or "")
    ui.go("notifications")
    assert ui.page() == "notifications" and ui.find("navNotifications").property("badge") == nov["unread"]
    page = _visible(ui, "nv")
    _call(page, "openItem", sub)
    ui.wait_idle()
    # Раздел «Закупка», вкладка заказов, выбран заказ № 5.
    assert ui.page() == "procurement"
    pane = _visible(ui, "pov")
    sel = _v(pane, "sel")
    assert sel and sel["order_no"] == 5 and sel["id"] == sub["object_id"]
    after = _v(ui.backend, "notificationsOverview")
    assert after["unread"] == nov["unread"] - 1
    assert not next(n for n in after["items"] if n["id"] == sub["id"])["unread"]
    assert qml_warnings == []


def test_read_all_and_offline_is_read_only(ui, qml_warnings):  # noqa: F811
    ui.login("v.zaitseva")
    ui.go("notifications")
    page = _visible(ui, "nv")
    nv = _v(page, "nv")
    assert nv["unread"] >= 1 and nv["can_mark"] is True
    assert any(n["kind"] == "po_submitted" for n in nv["items"])
    btn = ui.find("btnReadAll")
    assert btn.property("enabled") is True
    ui.click(btn)
    nv = _v(page, "nv")
    assert (nv["unread"], nv["can_mark"]) == (0, False) and nv["items"]
    assert ui.find("navNotifications").property("badge") == 0
    assert qml_warnings == []


def test_deputy_sees_project_role_and_team_shows_deputy(ui, qml_warnings):  # noqa: F811
    """Замещающий ГИП: роль в профиле, карточка «ГИП в проекте …», в команде проекта — «замещает»."""
    ui.login("m.lebedev")
    me = _v(ui.backend, "me")
    dpr = me["delegated_project_roles"]
    assert [d["role"] for d in dpr] == ["gip"] and dpr[0]["project_code"]
    code = dpr[0]["project_code"]
    dv = _v(_visible_after_go(ui, "delegations", "dv"), "dv")
    rec = next(d for d in dv["received"] if d["role"] == "gip")
    assert (rec["role_label"], rec["from_name"], rec["state"]) == (f"ГИП в проекте {code}", "Волков Павел", "active")
    # Уведомление о назначении — замещающему.
    nov = _v(ui.backend, "notificationsOverview")
    assert any(n["kind"] == "delegation_created" and n["page"] == "delegations" for n in nov["items"])
    # Команда проекта: кто замещает.
    deps = _v(ui.backend, "projectDeputies")
    assert [(d["user_name"], d["for_name"], d["role_label"]) for d in deps] == \
        [("Лебедев Максим", "Волков Павел", "ГИП проекта")]
    ui.go("projects", dpr[0]["project_id"])
    pp = _visible(ui, "deputies")
    assert len(_v(pp, "deputies")) == 1 and ui.find("projectDeputies").isVisible()
    assert qml_warnings == []


def _visible_after_go(ui, page, prop):
    ui.go(page)
    return _visible(ui, prop)


def test_rp_delegates_project_role_from_form(ui, qml_warnings):  # noqa: F811
    """РП передаёт свою роль в одном проекте: в форме — «РП в проекте …», на сервер уходят role и project_id."""
    ui.login("a.smirnova")
    page = _visible_after_go(ui, "delegations", "dv")
    dv = _v(page, "dv")
    proj = [o for o in dv["role_options"] if o["id"].startswith("rp@")]
    assert len(proj) == 2 and all(o["label"].startswith("РП в проекте П-") for o in proj)
    form = _popup(ui, "delegationForm")
    _call(form, "openFor", False, "", proj[0]["id"])
    ui.wait_idle()
    assert _popup(ui, "dRole").property("currentValue") == proj[0]["id"]
    _popup(ui, "dTo").setProperty("currentIndex", next(i for i, p in enumerate(dv["people_options"])
                                                       if p["label"] == "Кузнецов Дмитрий"))
    _popup(ui, "dReason").setProperty("text", "Отпуск (приказ № 120-о)")
    ui.wait_idle()
    assert form.property("canConfirm") is True
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert not form.property("visible"), form.property("error")
    g = next(d for d in _v(page, "dv")["given"] if d["role"] == "rp")
    assert (g["role_label"], g["to_name"], g["state"]) == (proj[0]["label"], "Кузнецов Дмитрий", "active")
    assert qml_warnings == []
