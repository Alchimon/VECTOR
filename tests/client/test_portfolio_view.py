"""Сводная аналитика портфеля (этап 7 blueprint 52, D-73) — чистая функция над снимком pull, без сервера и Qt.

portfolio_analytics(projects, streams, events, risks, purchase_orders, work_orders, claims, wf):
* потоки — по каждому виду: сколько завершено (статус в terminal контракта), медианы плановой и фактической
  длительности в днях (факт — из журнала событий workstream_status, как в «Аналитике проекта» D-71);
* закупки — lead-time заказов (размещён → закрыт): медиана по закрытым и опоздавшие к ожидаемой дате;
* производство — число нарядов и замен в их составе; качество — рекламации по источникам.
Денег нет: права на цены отдельные; только сроки и количества.
"""
import json
from pathlib import Path

from vector.client.delivery_view import portfolio_analytics

WF = json.loads((Path(__file__).resolve().parents[2] / "src/vector/core/contracts_data/workflows.slice1.json")
                .read_text(encoding="utf-8"))


def test_portfolio_analytics_streams_procurement_substitutions_and_claims():
    r = portfolio_analytics(
        projects=[{"status": "rkd_in_progress"}, {"status": "archived"}],
        streams=[{"kind": "rkd", "project_id": "p1", "status": "approved_for_use",
                  "planned_start": "2026-01-01", "planned_due": "2026-02-01"},
                 {"kind": "contract", "project_id": "p1", "status": "planned"}],
        events=[{"project_id": "p1", "kind": "workstream_status", "seq": 1, "at": "2026-01-05T10:00:00+00:00",
                 "details": {"kind": "rkd", "to": "in_design"}},
                {"project_id": "p1", "kind": "workstream_status", "seq": 2, "at": "2026-02-10T10:00:00+00:00",
                 "details": {"kind": "rkd", "to": "approved_for_use"}}],
        risks=[{"state": "active"}, {"state": "closed"}],
        purchase_orders=[{"status": "closed", "placed_at": "2026-01-01T00:00:00+00:00",
                          "closed_at": "2026-01-21T00:00:00+00:00", "expected_on": "2026-01-15"},   # опоздал на 6 дней
                         {"status": "placed", "placed_at": "2026-02-01T00:00:00+00:00",
                          "closed_at": None, "expected_on": None}],                                # в работе — не в lead-time
        work_orders=[{"substitutions": [{}, {}]}, {"substitutions": []}],
        claims=[{"source": "customer", "status": "closed"}, {"source": "internal", "status": "analysis"}],
        wf=WF)
    assert r["projects_total"] == 2 and {p["status"]: p["count"] for p in r["project_rows"]} == {
        "archived": 1, "rkd_in_progress": 1}
    assert r["active_risks"] == 1
    rk = next(s for s in r["streams"] if s["kind"] == "rkd")
    assert rk["terminal"] == 1 and rk["plan_median"] == "31" and rk["fact_median"] == "36"
    contract = next(s for s in r["streams"] if s["kind"] == "contract")
    assert contract["terminal"] == 0 and contract["plan_median"] == "—" and contract["fact_median"] == "—"
    pr = r["procurement"]
    assert (pr["placed_total"], pr["lead_median"], pr["lead_n"]) == (2, "20", 1)
    assert (pr["late_count"], pr["late_median"]) == (1, "6")
    assert r["production"] == {"work_orders": 2, "substitutions": 2}
    assert r["claims_total"] == 2
    assert {c["source"]: (c["total"], c["closed"]) for c in r["claims"]} == {"customer": (1, 1), "internal": (1, 0)}


def test_portfolio_analytics_empty_and_median_halves():
    # Пустой снимок: везде нули и «—»; медиана чётной выборки — половинная («15,5»).
    r = portfolio_analytics([], [], [], [], [], [], [], WF)
    assert r["projects_total"] == 0 and r["procurement"]["lead_median"] == "—"
    assert all(s["plan_median"] == "—" for s in r["streams"]) and len(r["streams"]) == 6
    r = portfolio_analytics([], [], [], [],
                            [{"status": "closed", "placed_at": "2026-01-01", "closed_at": "2026-01-11",
                              "expected_on": None},
                             {"status": "closed", "placed_at": "2026-01-01", "closed_at": "2026-01-22",
                              "expected_on": None}],
                            [], [], WF)
    assert r["procurement"]["lead_median"] == "15,5" and r["procurement"]["lead_n"] == 2
    assert r["procurement"]["late_count"] == 0    # без ожидаемой даты опоздания не считаются
