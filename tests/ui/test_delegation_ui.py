"""UI-проверки замещения и сверки со счётом (D-34): настоящий QML, сервер in-process, демо-данные.

Демо: e.orlova — руководитель отдела, заранее передала роль m.lebedev на отпуск (запланировано, через 10 дней);
v.zaitseva — единственный согласующий закупки; заказ № 5 на согласовании, суммы счёта совпадают с расчётом.
"""
import pytest
from PySide6.QtCore import QMetaObject

from tests.ui.test_purchasing_ui import _call, _orders_tab, _order, _popup, _v, qml_warnings  # noqa: F401

pytestmark = pytest.mark.ui


def _page(ui):
    """Открыть «Замещения»; вернуть страницу (элемент с dv)."""
    ui.go("delegations")
    return next(i for i in ui.items("dv") if i.isVisible())


def test_head_sees_coverage_and_delegates_for_absent_approver(ui, qml_warnings):  # noqa: F811
    ui.login("e.orlova")
    page = _page(ui)
    dv = _v(page, "dv")
    assert (dv["manage"], dv["can_manage"], dv["can_create"]) == (True, True, True)
    # Своё запланированное замещение — в «Меня замещают».
    g = dv["given"][0]
    assert (g["role_label"], g["to_name"], g["state"], g["state_label"]) == \
        ("Руководитель отдела", "Лебедев Максим", "planned", "Запланировано")
    # Покрытие: согласующий закупки — один человек.
    cov = {c["role"]: c for c in dv["coverage"]}
    assert cov["po_approver"]["state"] == "single" and cov["po_approver"]["holder_names"] == ["Зайцева Вера"]
    assert dv["warnings"] == len(dv["coverage"]) > 0
    # Согласующий заболел — оформляем замещение за него (форма предзаполнена из строки покрытия).
    form = _popup(ui, "delegationForm")
    _call(form, "openFor", True, cov["po_approver"]["holder_id"], "po_approver")
    ui.wait_idle()
    assert form.property("forOther") is True and form.property("canConfirm") is False
    to = _popup(ui, "dTo")
    to.setProperty("currentIndex", next(i for i, p in enumerate(dv["people_options"]) if p["label"] == "Никитина Ольга"))
    _popup(ui, "dReason").setProperty("text", "Больничный")
    ui.wait_idle()
    assert form.property("canConfirm") is True
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert not form.property("visible"), form.property("error")
    dv = _v(page, "dv")
    new = next(d for d in dv["others"] if d["role"] == "po_approver")
    assert (new["from_name"], new["to_name"], new["state"], new["on_behalf"], new["can_revoke"]) == \
        ("Зайцева Вера", "Никитина Ольга", "active", True, True)
    assert "po_approver" not in [c["role"] for c in dv["coverage"]]
    assert qml_warnings == []


def test_head_sees_orders_on_approval_and_invoice_check(ui, qml_warnings):  # noqa: F811
    ui.login("e.orlova")
    pane = _orders_tab(ui)
    pov = _v(pane, "pov")
    assert (pov["is_approver"], pov["sees_prices"], pov["can_create"]) == (True, True, False)
    o5 = _order(pane, 5)
    assert (o5["can_approve"], o5["invoice_gross_label"]) == (True, "2 348,50 RUB")
    inv = next(c for c in o5["control"]["checks"] if c["code"] == "invoice")
    assert (inv["state"], o5["control"]["needs_reason"]) == ("ok", False)
    assert o5["approval_events"][0]["invoice"] == "Сходится со счётом поставщика до копейки"
    assert qml_warnings == []


def test_editor_invoice_fields_and_reason_on_submit(ui, qml_warnings):  # noqa: F811
    ui.login("s.morozov")
    pane = _orders_tab(ui)
    o3 = _order(pane, 3)                     # черновик без цен: сверить нельзя
    ed = _popup(ui, "poEditor")
    _call(ed, "openFor", o3)
    ui.wait_idle()
    g = _popup(ui, "poInvoiceGross")
    g.setProperty("text", "100,005")
    ui.wait_idle()
    assert "два знака" in g.property("error")
    g.setProperty("text", "1 000,00")
    ed.setProperty("editRev", ed.property("editRev") + 1)
    ui.wait_idle()
    assert g.property("error") == ""
    pv = _v(ed, "pv")
    assert pv["control"] is None or pv["control"]["invoice_state"] == "unknown"
    QMetaObject.invokeMethod(ed, "close")
    ui.wait_idle()
    # Отправка с расхождением: форма просит причину (по локальной проверке).
    o5 = dict(_order(pane, 5))
    o5["control"] = dict(o5["control"], needs_reason=True)
    sf = _popup(ui, "submitForm")
    _call(sf, "openFor", o5)
    ui.wait_idle()
    assert sf.property("needReason") is True and sf.property("canConfirm") is False
    _popup(ui, "sbReason").setProperty("text", "НДС в счёте от итога")
    ui.wait_idle()
    assert sf.property("canConfirm") is True
    QMetaObject.invokeMethod(sf, "close")
    ui.wait_idle()
    assert qml_warnings == []


def test_everyone_has_delegations_page(ui, qml_warnings):  # noqa: F811
    ui.login("o.nikitina")
    page = _page(ui)
    dv = _v(page, "dv")
    assert (dv["manage"], dv["coverage"], dv["can_create"]) == (False, [], True)
    assert [r["id"] for r in dv["role_options"]] == ["accountant"]
    assert qml_warnings == []
