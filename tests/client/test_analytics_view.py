# -*- coding: utf-8 -*-
"""Аналитика проекта (этап 7 blueprint 52, D-71): план/факт потоков и сводка ПНР/FAT из снимка pull.

Чистая функция delivery_view.project_analytics_block: фактические даты потоков — из событий workstream_status,
агрегаты — из core.delivery.delivery_facts. Здесь — без Qt и сети.
"""
from vector.client.delivery_view import project_analytics_block

WF = {"workstream_contract": {"initial": "planned", "terminal": ["completed"],
                              "transitions": {"planned": ["in_progress"], "in_progress": ["completed"]},
                              "labels_ru": {"planned": "Планируется", "in_progress": "В работе",
                                            "completed": "Завершён"}},
      "workstream_shipping": {"initial": "planned", "terminal": ["delivered"],
                              "transitions": {"planned": ["ready"], "ready": ["shipped"],
                                              "shipped": ["delivered"]},
                              "labels_ru": {"planned": "Планируется", "ready": "Готова",
                                            "shipped": "Отгружена", "delivered": "Поставлена"}}}


def _ws(kind, **kw):
    return {"id": kind, "project_id": "P", "kind": kind, "version": 1,
            "status": kw.pop("status", "planned"), **kw}


def _ev(seq, kind, to, at, stream="contract"):
    return {"project_id": "P", "seq": seq, "kind": "workstream_status", "at": at + "T10:00:00+00:00",
            "summary": "", "details": {"kind": stream, "to": to}}


def test_plan_vs_fact_streams():
    streams = [_ws("contract", status="completed", planned_start="2026-01-10", planned_due="2026-01-31"),
               _ws("shipping", status="ready", planned_start="2026-03-01", planned_due="2026-03-15")]
    events = [_ev(1, "workstream_status", "in_progress", "2026-01-12"),
              _ev(2, "workstream_status", "completed", "2026-02-02"),
              _ev(3, "workstream_status", "ready", "2026-02-20", stream="shipping")]
    v = project_analytics_block("P", streams, events, [], [], [], [], [], {}, WF)
    by = {r["kind"]: r for r in v["streams"]}
    assert by["contract"]["plan_days"] == 21 and by["contract"]["fact_days"] == 21
    assert by["contract"]["actual_start"] == "2026-01-12" and by["contract"]["actual_finish"] == "2026-02-02"
    assert by["contract"]["terminal"] is True
    # Отгрузка начата, но не завершена: факта окончания и длительности нет.
    assert by["shipping"]["actual_start"] == "2026-02-20" and by["shipping"]["actual_finish"] == ""
    assert by["shipping"]["fact_days"] is None
    assert v["done_streams"] == 1 and v["total_streams"] == 2


def test_delivery_facts_and_closeout():
    fats = [{"id": "f1", "project_id": "P", "kind": "final", "status": "passed"},
            {"id": "f2", "project_id": "P", "kind": "preliminary", "status": "failed"}]
    findings = [{"id": "x1", "fat_id": "f1", "blocking": True, "status": "closed"},
                {"id": "f3", "fat_id": "f2", "blocking": False, "status": "open"},
                {"id": "f9", "fat_id": "чужая", "blocking": True, "status": "open"}]   # чужой проект — не считаем
    pnr = [{"id": "p1", "project_id": "P", "kind": "site", "status": "done"},
           {"id": "p2", "project_id": "P", "kind": "internal", "status": "cancelled"}]
    docs = [{"id": "d1", "project_id": "P"}]
    import json
    import hashlib
    snap = {"project": {"id": "P"}, "facts": {"fat": {"passed": 1}}}
    sha = hashlib.sha256(json.dumps(snap, ensure_ascii=False).encode()).hexdigest()
    closeouts = [{"project_id": "P", "snapshot": json.dumps(snap, ensure_ascii=False), "content_sha256": sha,
                  "closed_by": "u1", "created_at": "2026-11-20T09:00:00+00:00"}]
    v = project_analytics_block("P", [], [], fats, findings, pnr, docs, closeouts, {"u1": "Иванов"}, WF)
    assert v["facts"]["fat"] == {"total": 2, "preliminary": 1, "final": 1, "passed": 1, "failed": 1}
    assert v["facts"]["findings"] == {"total": 2, "blocking": 1, "closed": 1}
    assert v["facts"]["pnr"]["done"] == 1 and v["facts"]["pnr"]["site"] == 1
    assert v["facts"]["delivery_docs"] == 1
    assert v["closeout"]["sha256"] == sha and v["closeout"]["closed_by_name"] == "Иванов"
    assert v["closeout"]["facts"]["fat"]["passed"] == 1
