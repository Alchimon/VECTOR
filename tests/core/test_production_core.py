"""Правила технологии и производства, общие для сервера и клиента (vector.core.production, D-36).

Клиент применяет те же функции офлайн, чтобы показать таймер и состояние операции до синхронизации; поэтому
правила проверяются здесь без сервера и БД.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from vector.core import production as prod

T0 = datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc)


def op(**kw):
    """Операция в начальном состоянии (как после выпуска наряда)."""
    return {"state": "pending", "running_since": None, "last_device_time": None, "fact_seconds": 0, "qty_done": None, **kw}


def run(o, kind, minutes, **kw):
    """Применить событие через minutes минут после T0 и вернуть операцию с изменениями."""
    return {**o, **prod.apply_event(o, kind, T0 + timedelta(minutes=minutes), 3, **kw)}


def test_timer_counts_only_running_intervals():
    o = run(op(), "start", 0)
    o = run(o, "pause", 15)
    o = run(o, "problem", 20)                 # проблема состояние не меняет
    assert o["state"] == "paused"
    o = run(o, "resume", 30)
    o = run(o, "finish", 40, qty_done=2)
    assert (o["state"], o["fact_seconds"], o["qty_done"]) == ("done", 25 * 60, 2)
    assert prod.fact_minutes(o["fact_seconds"]) == "25.0"
    # Завершение с паузы: открытого интервала нет — факт не растёт; количество по умолчанию — весь наряд.
    p = run(run(run(op(), "start", 0), "pause", 10), "finish", 50)
    assert (p["fact_seconds"], p["qty_done"]) == (600, 3)


def test_conflicts_and_validation():
    o = run(op(), "start", 10)
    with pytest.raises(prod.ProductionError) as e:
        run(o, "resume", 11)
    assert (e.value.code, e.value.conflict) == ("invalid_transition", True)
    with pytest.raises(prod.ProductionError) as e:
        run(o, "pause", 5)                          # раньше предыдущего события
    assert (e.value.code, e.value.conflict) == ("device_time_order", True)
    with pytest.raises(prod.ProductionError) as e:
        run(o, "finish", 12, qty_done=4)            # больше наряда
    assert (e.value.code, e.value.conflict) == ("invalid_qty_done", False)
    done = run(o, "finish", 12)
    with pytest.raises(prod.ProductionError) as e:
        run(done, "problem", 13)
    assert e.value.code == "operation_done"
    with pytest.raises(prod.ProductionError):
        prod.parse_device_time("2026-11-16T08:00:00")      # без часового пояса


def test_parse_operations_sorted_and_strict():
    ops = prod.parse_operations([{"op_no": 20, "name": " Монтаж ", "norm_min": "30"},
                                 {"op_no": 10, "name": "Резка", "norm_min": "0", "work_center": "У1"}])
    assert [(o["op_no"], o["name"], o["norm_min"]) for o in ops] == [(10, "Резка", "0.0000"), (20, "Монтаж", "30.0000")]
    with pytest.raises(prod.ProductionError) as e:
        prod.parse_operations([{"op_no": 10, "name": "А", "norm_min": None, "extra": 1}])
    codes = {p["code"] for p in e.value.details["problems"]}
    assert codes == {"field_required", "unknown_fields"}


def test_snapshot_effective_spec_and_materials():
    spec = {"calc_version_id": 7, "version_no": 2, "content_hash": "h",
            "lines": [{"line_id": "a", "code": "К1", "name": "Кабель", "unit": "м", "qty": "1.5000"},
                      {"line_id": "b", "code": None, "name": "Наладка", "unit": "ч", "qty": "2"}]}
    snap = prod.build_snapshot(qty=4, project={"id": "p", "code": "П"}, kit={"id": "k", "code": "К"},
                               tp={"id": "t", "revision_no": 1, "title": "ТП", "content_hash": "x"}, spec=spec,
                               operations=[{"op_no": 10, "name": "Резка", "norm_min": "1.2500"}])
    assert snap["spec"]["lines"][0]["qty"] == "6.0000" and snap["operations"][0]["norm_total_min"] == "5.0000"
    h = prod.snapshot_hash(snap)
    assert h == prod.snapshot_hash(dict(snap)) and len(h) == 64
    eff = prod.effective_spec(snap["spec"]["lines"], [{"seq": 1, "line_id": "a",
                                                        "new": {"code": "К2", "name": "Кабель нг", "unit": "м", "qty": "7"}}])
    assert (eff[0]["code"], eff[0]["substituted"], snap["spec"]["lines"][0]["code"]) == ("К2", True, "К1")
    assert prod.spec_hash(eff) != prod.spec_hash(prod.effective_spec(snap["spec"]["lines"], []))
    m = prod.materials(eff, {"К2": {"id": "i2", "unit": "М"}}, {"i2": Decimal("7")})
    assert [x["state"] for x in m] == ["ok", "not_stock"]           # единица сравнивается без учёта регистра
    m = prod.materials(eff, {"К2": {"id": "i2", "unit": "бухта"}}, {})
    assert m[0]["state"] == "unit_mismatch"
    with pytest.raises(prod.ProductionError):
        prod.build_snapshot(qty=0, project={}, kit={}, tp={}, spec=spec, operations=[])
