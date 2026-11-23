"""UI-проверки потоков проекта, риск-разрешений и лидов (D-65, этап 2 blueprint 52).

Что проверяем «глазами пользователя»:
* карточка проекта показывает шесть потоков со статусами и переходами из контракта, риски и журнал событий;
* принятие риск-разрешения через форму доходит до сервера (обязательные причина и срок), снимок обновляется pull'ом;
* раздел «Лиды» показывает воронку демо-данных, квалификация лида — через диалог до сервера;
* без связи кнопки действий недоступны (команды потоков/рисков/лидов — онлайн).
"""
import pytest

pytestmark = pytest.mark.ui


def _delivery(ui):
    """Срез «Потоки и риски» демо-проекта из backend."""
    return ui.backend.projectDelivery(ui.ids["project"], ui.backend.property("dataRevision"))


def _open_project(ui):
    """Войти РП и открыть демо-проект."""
    ui.login("a.smirnova")
    ui.go("projects", ui.ids["project"])
    ui.wait_idle()


def test_project_shows_streams_risks_and_events(ui):
    _open_project(ui)
    v = _delivery(ui)
    kinds = [s["kind"] for s in v["streams"]]
    assert kinds == ["contract", "rkd", "procurement", "production", "shipping", "site"]
    # Статусы — подписи из контракта workflows; у потока в работе есть кнопки переходов.
    by_kind = {s["kind"]: s for s in v["streams"]}
    assert by_kind["rkd"]["status_label"] != ""
    assert any(a["label"] for a in by_kind["contract"]["actions"]) or by_kind["contract"]["terminal"]
    # Журнал событий демо-проекта не пуст (сид D-56 пишет вехи).
    assert v["counts"]["events"] > 0
    assert v["events"][0]["summary"] != "" and v["events"][0]["at_label"] != ""
    # Риск-разрешения: блок виден, действие «Принять риск» доступно РП онлайн.
    assert v["can_manage"] and len(v["risk_kinds"]) == 2


def test_accept_risk_through_form(ui):
    from PySide6.QtCore import QMetaObject, QObject
    _open_project(ui)
    dlg = next(o for o in ui.win.findChildren(QObject)
               if o.property("confirmText") == "Принять риск")
    QMetaObject.invokeMethod(dlg, "ask")
    ui.wait_idle()
    assert dlg.property("visible")
    body = dlg.findChildren(QObject)
    reason = next(o for o in body if o.property("label") == "Причина")
    due = next(o for o in body if o.property("label") == "Срок действия")
    assert not dlg.property("canConfirm")               # причина и срок обязательны
    reason.setProperty("text", "Заказчик задержал согласование, а поставка на критическом пути")
    due.setProperty("text", "2026-12-31")
    ui.wait_idle()
    assert dlg.property("canConfirm")
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert dlg.property("error") == "" and not dlg.property("visible")
    risks = _delivery(ui)["risks"]
    assert len(risks) == 1
    r = risks[0]
    assert r["kind"] == "procurement_before_rkd_approval" and r["state"] == "active"
    assert r["due_on"] == "2026-12-31" and "критическом пути" in r["reason"]
    assert _delivery(ui)["counts"]["active_risks"] == 1
    # Событие о принятии риска попало в журнал проекта.
    assert any("иск-разрешение" in e["summary"] for e in _delivery(ui)["events"])


def test_resolve_risk_requires_reason(ui):
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject
    test_accept_risk_through_form(ui)
    r = _delivery(ui)["risks"][0]
    assert len(r["actions"]) == 2                        # снять: условие выполнено / отозвано
    dlg = next(o for o in ui.win.findChildren(QObject)
               if o.property("confirmText") == "Снять разрешение")
    QMetaObject.invokeMethod(dlg, "setup", Q_ARG("QVariant", r), Q_ARG("QVariant", r["actions"][0]))
    ui.wait_idle()
    reason = next(o for o in dlg.findChildren(QObject) if o.property("label") == "Причина")
    assert not dlg.property("canConfirm")
    reason.setProperty("text", "РКД согласована заказчиком")
    ui.wait_idle()
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    r = _delivery(ui)["risks"][0]
    assert r["status"] == "resolved" and r["actions"] == []
    assert "согласована" in r["resolution_reason"]


def test_leads_page_funnel_and_qualify(ui):
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject
    ui.login("a.smirnova")
    ui.go("leads")
    ui.wait_idle()
    v = ui.backend.opportunitiesView(ui.backend.property("dataRevision"))
    assert v["counts"]["total"] >= 1 and v["can_manage"]
    lead = next(r for r in v["rows"] if r["status"] == "new")
    # Квалификация лида — через диалог перехода до сервера (узнаём по функции setup с лидом).
    dlg = next(o for o in ui.win.findChildren(QObject)
               if o.property("confirmText") == "Выполнить" and o.property("title") == "")
    act = next(a for a in lead["actions"] if a["to"] == "qualified")
    QMetaObject.invokeMethod(dlg, "setup", Q_ARG("QVariant", lead), Q_ARG("QVariant", act))
    ui.wait_idle()
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    v = ui.backend.opportunitiesView(ui.backend.property("dataRevision"))
    lead = next(r for r in v["rows"] if r["id"] == lead["id"])
    assert lead["status"] == "qualified" and lead["status_label"] == "Квалифицирован"


def test_delivery_offline_disables_actions(ui):
    _open_project(ui)
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    v = _delivery(ui)
    assert not v["can_manage"]
    assert all(s["actions"] == [] for s in v["streams"])
    assert v["project"]["actions"] == []
    # Чтение офлайн работает: потоки и журнал видны из снимка.
    assert len(v["streams"]) == 6 and v["counts"]["events"] > 0
