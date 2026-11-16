"""Сквозные UI-проверки среза 1: вход, видимость разделов по правам, навигация, офлайн-очередь и конфликт."""
import uuid

import pytest

from ui_harness import PWD

pytestmark = pytest.mark.ui


def test_wrong_password_stays_on_login_with_generic_error(ui):
    ui.login("a.smirnova", "не-тот-пароль-123")
    assert ui.backend.screen == "login"
    assert ui.backend.loginError  # единое сообщение, без указания, что именно неверно
    assert "логин" in ui.backend.loginError.lower() or "парол" in ui.backend.loginError.lower()


def test_rp_sees_business_sections_but_not_admin(ui):
    ui.login("a.smirnova")
    assert ui.backend.screen == "main"
    nav = ui.nav()
    assert nav["Заказчики"] and nav["Тендеры и ТКП"] and nav["Проекты"] and nav["Синхронизация"]
    assert nav.get("Администрирование") is False


def test_admin_has_no_implicit_business_sections(ui):
    ui.login("admin")
    nav = ui.nav()
    assert nav["Администрирование"]
    assert nav["Заказчики"] is False and nav["Тендеры и ТКП"] is False


def test_navigation_selects_object_and_click_switches_page(ui):
    ui.login("a.smirnova")
    ui.go("tenders", ui.ids["tender_won"])
    sel = [i for i in ui.items("selectedId") if i.isVisible()]
    assert [i.property("selectedId") for i in sel] == [ui.ids["tender_won"]]
    projects = next(i for i in ui.items("iconName") if i.property("text") == "Проекты" and i.property("active") is not None)
    ui.click(projects)
    assert ui.page() == "projects"


def test_keyboard_shortcut_switches_page(ui):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    ui.login("a.smirnova")
    ui.win.requestActivate()
    ui.pump(0.2)
    QTest.keyClick(ui.win, Qt.Key.Key_4, Qt.KeyboardModifier.ControlModifier)
    ui.wait_idle()
    assert ui.page() == "sync"


def test_offline_edit_then_conflict_is_shown_and_never_merged(ui):
    ui.login("a.smirnova")
    cid = ui.ids["customers"]["ООО «ТеплоСеть-Юг»"]
    ver = ui.backend.client.get_local("customer", cid)["version"]
    ui.transport.online = False
    ui.backend.execute("t", "customer.update", {"id": cid, "note": "Моя правка без связи"}, ver)
    ui.wait_idle()
    assert ui.backend.pendingCount == 1 and ui.backend.problemCount == 0
    # коллега меняет тот же объект на сервере
    tok = ui.svc.handle({"type": "login", "login": "e.orlova", "password": PWD, "device_id": "x"}, "x")["token"]
    r = ui.svc.handle({"type": "command", "token": tok, "operation_id": str(uuid.uuid4()), "command": "customer.update",
                       "payload": {"id": cid, "note": "Правка коллеги"}, "expected_version": ver}, "x")
    assert r["status"] == "accepted"
    ui.transport.online = True
    ui.backend.syncNow()
    ui.wait_idle()
    assert ui.backend.problemCount == 1
    ui.go("sync")
    ui.backend.loadQueue()
    ui.wait_idle()
    items = ui.backend.queueItems
    assert len(items) == 1 and items[0]["state"] == "conflict"
    row = next(d for d in items[0]["diff"] if d["field"] == "note")
    assert row["field_label"] == "Примечание" and row["mine"] == "Моя правка без связи" and row["server"] == "Правка коллеги"
    # на сервере правка коллеги не перезаписана молча; локально видна своя правка с пометкой «не отправлено»
    pulled = ui.svc.handle({"type": "query", "query": "pull", "token": tok}, "x")
    assert pulled["status"] == "accepted"
    srv = next(c for c in pulled["result"]["customers"] if c["id"] == cid)
    assert srv["note"] == "Правка коллеги" and srv["version"] == ver + 1
    ui.backend.discard(items[0]["operation_id"])
    ui.wait_idle()
    assert ui.backend.problemCount == 0 and ui.backend.pendingCount == 0
    assert ui.backend.client.get_local("customer", cid)["note"] == "Правка коллеги"


def test_history_uses_human_labels(ui):
    ui.login("a.smirnova")
    assert ui.backend.commandLabel("tender.transition") == "Сменить статус тендера"
    assert ui.backend.authorityLabel("global_role:rp#grant:2") == "роль «РП» (выдача №2)"
    assert ui.backend.authorityLabel("project_role:gip#assignment:7") == "«ГИП проекта» (назначение №7)"
