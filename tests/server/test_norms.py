"""Нормы трудоёмкости (этап 7 blueprint 52, D-72): предложение по агрегированным данным и ручное утверждение.

Правила, закреплённые тестами:
* norm_stats — запрос (только онлайн): агрегированные диапазоны по завершённым операциям + действующие нормы из
  утверждённых ТП; право norm.read (РП, ГИП, руководитель отдела, ведущий инженер — не исполнителям).
* norm.propose — предложение по операции; диапазон сервер пересчитывает сам в момент команды (снимок в записи);
  нужна выборка ≥ 2 завершённых одноимённых операций (sample_too_small); норма не применяется автоматически.
* norm.decide — утвердить/отклонить; решает НЕ автор (self_decision), решение одноразовое (decision_final),
  отклонение — с причиной (reason_required); автору предложения — уведомление.
"""
from datetime import timedelta

import pytest

from conftest import new_id
from test_production import OPS, Pd, approved_tp, ev, ops_of, pd, pull, release  # noqa: F401
from test_stock import st  # noqa: F401
from test_tasks_documents import proj  # noqa: F401


def _finish_op(s, op_id, t0, minutes, qty_done=2):
    """Завершить операцию событиями исполнителя: start → finish через minutes минут (факт — интервал «в работе»)."""
    ev(s, op_id, "start", t0)
    return ev(s, op_id, "finish", t0 + timedelta(minutes=minutes), qty_done=qty_done)["result"]


def _two_finished_samples(world, pd):
    """Два наряда с завершённой операцией 10 («Резка кабеля», РЦ «Участок 1»): 30 и 60 минут факта."""
    tp = approved_tp(pd)
    t0 = world.clock.now() - timedelta(hours=5)
    for minutes in (30, 60):
        res = release(pd, tp, qty=2)["result"]
        ops = ops_of(res)
        _finish_op(pd.prod, ops[10]["id"], t0, minutes)
        t0 += timedelta(hours=2)
    return tp


def test_norm_stats_query_rights_and_current_norms(world, pd):
    tp = _two_finished_samples(world, pd)
    # Исполнителю статистика недоступна (агрегат — производственному руководству).
    assert pd.prod.query("norm_stats")["status"] == "denied"
    # Ведущему инженеру (технолог) — диапазон по операции и действующая норма из утверждённого ТП (1.5 мин).
    lead = world.login("lead1")
    r = lead.query("norm_stats")
    assert r["status"] == "accepted", r
    stats = {s["op_name"]: s for s in r["result"]["stats"]}
    st10 = stats["Резка кабеля"]
    assert (st10["sample_size"], st10["min_min"], st10["median_min"], st10["max_min"]) == (2, 30.0, 45.0, 60.0)
    # Действующая норма из утверждённого ТП (OPS, операция 10 — 1.5 мин; возможны хвостовые нули из БД).
    assert [float(x) for x in st10["current_norms"]] == [1.5]
    assert tp["status"] == "approved"


def test_norm_propose_requires_two_samples_and_is_decided_by_another(world, pd):
    tp = approved_tp(pd)
    t0 = world.clock.now() - timedelta(hours=5)
    res = release(pd, tp, qty=2)["result"]
    _finish_op(pd.prod, ops_of(res)[10]["id"], t0, 30)
    lead, head = world.login("lead1"), world.login("head1")
    # По одному факту норму не предлагают.
    r = lead.cmd("norm.propose", {"id": new_id(), "op_name": "Резка кабеля", "work_center": "Участок 1",
                                  "proposed_min": "45"})
    assert (r["status"], r["code"]) == ("rejected", "sample_too_small")
    # Вторая завершённая операция — предложение создаётся; диапазон посчитал сервер.
    res = release(pd, tp, qty=2)["result"]
    _finish_op(pd.prod, ops_of(res)[10]["id"], t0 + timedelta(hours=2), 60)
    r = lead.ok("norm.propose", {"id": new_id(), "op_name": "Резка кабеля", "work_center": "Участок 1",
                                 "proposed_min": "45", "comment": "По замерам ноября"})["proposal"]
    assert (r["status"], r["sample_size"]) == ("proposed", 2)
    # Диапазон посчитал сервер (десятичные строки, возможно с хвостовыми нулями из БД).
    assert [float(r["min_min"]), float(r["median_min"]), float(r["max_min"])] == [30.0, 45.0, 60.0]
    # Автор не решает своё предложение.
    d = lead.cmd("norm.decide", {"id": r["id"], "resolution": "approved"})
    assert (d["status"], d["code"]) == ("rejected", "self_decision")
    # Отклонение без причины — отказ; с причиной — решение; автору — уведомление.
    d = head.cmd("norm.decide", {"id": r["id"], "resolution": "rejected"})
    assert (d["status"], d["code"]) == ("rejected", "reason_required")
    d = head.ok("norm.decide", {"id": r["id"], "resolution": "rejected", "reason": "Проверить на следующем наряде"})["proposal"]
    assert d["status"] == "rejected" and d["decision_reason"] == "Проверить на следующем наряде"
    assert "norm_decided" in [n["kind"] for n in pull(lead)["notifications"]]
    # Решение одноразовое.
    d = head.cmd("norm.decide", {"id": r["id"], "resolution": "approved"})
    assert (d["status"], d["code"]) == ("rejected", "decision_final")


def test_norm_propose_rights_and_pull_visibility(world, pd):
    _two_finished_samples(world, pd)
    # Предложить норму исполнитель не может; утвердить (без ведущей роли) РП проекта не может.
    payload = {"id": new_id(), "op_name": "Резка кабеля", "work_center": "Участок 1", "proposed_min": "45"}
    assert pd.prod.cmd("norm.propose", payload)["status"] == "denied"
    lead = world.login("lead1")
    prop = lead.ok("norm.propose", payload)["proposal"]
    assert pd.st.proj.rp.cmd("norm.decide", {"id": prop["id"], "resolution": "approved"})["status"] == "denied"
    # Предложение приходит в pull тем, у кого norm.read (РП, руководитель отдела, ведущий инженер), и не приходит
    # исполнителю. ФИО автора подставлены.
    rp_pull, prod_pull = pull(pd.st.proj.rp), pull(pd.prod)
    assert [p["op_name"] for p in rp_pull["norm_proposals"]] == ["Резка кабеля"]
    assert rp_pull["norm_proposals"][0]["created_by_name"] != ""
    assert prod_pull["norm_proposals"] == []
