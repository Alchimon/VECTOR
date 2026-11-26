# -*- coding: utf-8 -*-
"""UI-проверка блока «Аналитика проекта» (D-71, этап 7 blueprint 52).

Блок читает снимок pull (и офлайн): план/факт потоков, сводка ПНР/FAT/замечаний, снимок закрытия.
"""
import uuid

import pytest

pytestmark = pytest.mark.ui


def _analytics(ui):
    return ui.backend.projectAnalytics(ui.ids["project"], ui.backend.property("dataRevision"))


def _open_project(ui):
    ui.login("a.smirnova")
    ui.go("projects", ui.ids["project"])
    ui.wait_idle()


def test_analytics_shows_streams_and_facts(ui):
    _open_project(ui)
    v = _analytics(ui)
    # Шесть потоков демо-проекта в каноническом порядке; у начатых есть фактическая дата из журнала.
    kinds = [s["kind"] for s in v["streams"]]
    assert kinds == ["contract", "rkd", "procurement", "production", "shipping", "site"]
    started = [s for s in v["streams"] if s["actual_start"] != ""]
    assert started, "в демо-проекте потоки уже двигались — фактические даты должны быть из журнала событий"
    # Сводка этапа 6 присутствует; в демо уже есть ПНР/FAT/документ (D-76) — дальше меряем дельту.
    assert set(v["facts"]) >= {"pnr", "fat", "findings", "delivery_docs"}
    base_facts = v["facts"]
    # Шаблон FAT и одно испытание через клиент — сводка обновится снимком.
    svc = ui.svc
    r = svc.handle({"type": "login", "login": "e.orlova", "password": "Демо-пароль-2026",
                    "device_id": "ui-test"}, "10.0.0.9")
    tpl = str(uuid.uuid4())
    svc.handle({"type": "command", "token": r["token"], "command": "checklist.create",
                "operation_id": str(uuid.uuid4()),
                "payload": {"id": tpl, "kind": "fat", "name": "FAT (аналитика)",
                            "sections": [{"code": "a", "title": "Общие"}]}}, "10.0.0.9")
    ui.backend.syncNow()
    ui.wait_idle()
    r = ui.backend.client.execute("fat.create", {"id": str(uuid.uuid4()), "project_id": ui.ids["project"],
                                                 "kind": "final", "template_id": tpl})
    ui.backend.syncNow()
    ui.wait_idle()
    assert r.status == "accepted", r
    v = _analytics(ui)
    assert v["facts"]["fat"]["final"] == base_facts["fat"]["final"] + 1
    assert v["facts"]["fat"]["total"] == base_facts["fat"]["total"] + 1
    assert v["facts"]["findings"]["total"] == base_facts["findings"]["total"]


def test_analytics_offline_readonly(ui):
    _open_project(ui)
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    v = _analytics(ui)
    # Чтение офлайн работает: потоки и сводка из снимка.
    assert len(v["streams"]) == 6 and set(v["facts"]) >= {"pnr", "fat", "findings"}
