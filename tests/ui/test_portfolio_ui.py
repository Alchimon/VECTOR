# -*- coding: utf-8 -*-
"""UI-проверка раздела «Аналитика» (D-73, этап 7 blueprint 52): сводная аналитика портфеля — только чтение.

Демо-стенд: два проекта с потоками, заказы поставщикам (размещённые), наряды № 1 и № 2, рекламации (D-40).
Раздел виден по праву norm.read; закупки в pull попадают по po.read — полную картину видит, например,
руководитель отдела (e.orlova): и потоки, и заказы, и наряды, и рекламации. Работает и офлайн (снимок pull).
"""
import uuid

import pytest

from tests.ui.test_purchasing_ui import _v, qml_warnings  # noqa: F401

pytestmark = pytest.mark.ui


def _open(ui):
    ui.login("e.orlova")
    ui.go("analytics")
    ui.wait_idle()


def _svc_cmd(ui, login, command, payload, ev=None):
    """Команда на сервере стенда от имени демо-пользователя (мини-сценарий подготовки данных)."""
    r = ui.svc.handle({"type": "login", "login": login, "password": "Демо-пароль-2026",
                       "device_id": "ui-test"}, "10.0.0.9")
    msg = {"type": "command", "token": r["token"], "command": command, "operation_id": str(uuid.uuid4()),
           "payload": payload}
    if ev is not None:
        msg["expected_version"] = ev
    if command == "__pull__":
        return ui.svc.handle({"type": "query", "token": r["token"], "query": "pull"}, "10.0.0.9")["result"]
    return ui.svc.handle(msg, "10.0.0.9")


def test_portfolio_analytics_shows_aggregates(ui, qml_warnings):  # noqa: F811
    _open(ui)
    assert ui.find("navAnalytics").property("visible") is True
    v = ui.backend.portfolioAnalyticsView(ui.backend.property("dataRevision"))
    assert v["projects_total"] == 2 and len(v["streams"]) == 6
    rkd = next(s for s in v["streams"] if s["kind"] == "rkd")
    # Поток РКД демо-проекта согласован заказчиком (D-61 в демо-сиде) — завершён, фактическая медиана есть.
    assert rkd["terminal"] >= 1 and rkd["fact_n"] >= 1
    # Плановые даты задаёт РП в карточке потока — зададим план РКД демо-проекта и проверим медиану плана.
    pull_rp = _svc_cmd(ui, "a.smirnova", "__pull__", {})
    pid = ui.ids["project"]
    ws = next(w for w in pull_rp["workstreams"] if w["kind"] == "rkd" and w["project_id"] == pid)
    planned = _svc_cmd(ui, "a.smirnova", "workstream.update",
                       {"id": ws["id"], "planned_start": "2026-10-01", "planned_due": "2026-11-01"}, ws["version"])
    assert planned["status"] == "accepted", planned
    # Закупки: закрываем размещённый демо-заказ (закупка) — появится lead-time «размещён → закрыт».
    pull_buyer = _svc_cmd(ui, "s.morozov", "__pull__", {})
    placed = next(o for o in pull_buyer["purchase_orders"] if o["status"] == "placed")
    closed = _svc_cmd(ui, "s.morozov", "po.close",
                      {"id": placed["id"], "reason": "Поставка завершена"}, placed["version"])
    assert closed["status"] == "accepted", closed
    ui.backend.syncNow()
    ui.wait_idle()
    v = ui.backend.portfolioAnalyticsView(ui.backend.property("dataRevision"))
    rkd = next(s for s in v["streams"] if s["kind"] == "rkd")
    assert rkd["plan_median"] == "31" and rkd["plan_n"] >= 1
    assert v["procurement"]["lead_n"] >= 1 and v["procurement"]["lead_median"] != "—"
    # Производство: два наряда; качество: рекламация от заказчика есть (D-40 в демо-сиде).
    assert v["production"]["work_orders"] >= 2
    assert any(c["source"] == "customer" and c["total"] >= 1 for c in v["claims"])
    assert qml_warnings == []


def test_portfolio_analytics_offline_readonly(ui, qml_warnings):  # noqa: F811
    _open(ui)
    before = ui.backend.portfolioAnalyticsView(ui.backend.property("dataRevision"))
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    v = ui.backend.portfolioAnalyticsView(ui.backend.property("dataRevision"))
    # Чтение офлайн: те же агрегаты из снимка.
    assert v["projects_total"] == before["projects_total"] and len(v["streams"]) == 6
    assert v["procurement"]["lead_median"] == before["procurement"]["lead_median"]
    assert qml_warnings == []
