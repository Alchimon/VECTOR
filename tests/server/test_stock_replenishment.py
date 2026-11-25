# Пополнение расходников по порогу (D-79, С4 шаг 7).
#
# Правила, которые закрепляют тесты (server/stock.py, команды stock.replen_rule_set / stock.replen_rule_update /
# stock.replen_confirm / stock.replen_dismiss и триггер _maybe_replenish в _save_balance):
# правило задаёт склад (порог и «пополнить до» — без умолчаний, Q2; target > min); когда доступное опускается
# ниже порога, система создаёт черновую позицию (target − доступное) или обновляет её — идемпотентно, одна
# открытая на пару; подтверждают ОБЕ стороны (склад и закупка, каждая один раз); отклоняет любая с причиной;
# восстановление остатка выше порога закрывает открытый черновик (resolved), подтверждённый остаётся;
# отключение правила не удаляет историю черновиков.
import json

from conftest import new_id
from test_stock import bal, receive, reserve, st  # noqa: F401
from test_tasks_documents import audit, proj  # noqa: F401


def rule_set(s, st, min_qty, target):
    """Задать правило пополнения пары «склад+позиция» фикстуры (статус не проверяется)."""
    return s.cmd("stock.replen_rule_set", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item,
                                           "min_qty": min_qty, "target_qty": target})


def _pull(s):
    return s.query("pull")["result"]


def _drafts(s):
    return _pull(s)["replenishment_drafts"]


def _rules(s):
    return _pull(s)["replenishment_rules"]


# ====================================================================== правило и срабатывание
def test_rule_and_draft_lifecycle(world, st, proj):  # noqa: F811
    receive(st.wh, st, "10")
    # Правило: ниже 3 → пополнить до 10. Склад задаёт; порогов по умолчанию нет.
    r = rule_set(st.wh, st, "3", "10")
    assert r["status"] == "accepted"
    rule = r["result"]["rule"]
    assert (rule["min_qty"], rule["target_qty"], rule["enabled"], rule["version"]) == ("3.0000", "10.0000", True, 1)
    assert _drafts(st.wh) == []                                        # доступное 10 ≥ 3 — черновика нет
    # Выдача 8 → доступное 2 < 3: система создаёт черновик на 8 (target − доступное).
    reserve(st.wh, st, "8", project_id=proj.pid)
    d = _drafts(st.wh)
    assert len(d) == 1 and (d[0]["status"], d[0]["qty"]) == ("open", "8.0000")
    # Остаток падает ещё (резерв +1) — количество черновика обновляется, нового не создаётся (идемпотентно).
    reserve(st.wh, st, "1", project_id=proj.pid)
    d = _drafts(st.wh)
    assert len(d) == 1 and d[0]["qty"] == "9.0000" and d[0]["version"] == 2
    # Уведомление складу и закупке о черновике.
    kinds = [n["kind"] for n in _pull(st.pr)["notifications"]]
    assert "replen_draft_open" in kinds
    # Аудит системного создания.
    ev = audit(world, otype="stock_replenishment_draft", oid=d[0]["id"])
    assert json.loads(ev[0]["details_json"])["automatic"] is True


def test_rule_validation_and_permissions(world, st):  # noqa: F811
    # Порог обязателен и положителен; «пополнить до» строго больше порога; одна пара — одно правило.
    r = rule_set(st.wh, st, "0", "10")
    assert r["status"] == "rejected"
    r = rule_set(st.wh, st, "5", "5")
    assert (r["status"], r["code"]) == ("rejected", "target_not_above_min")
    r = rule_set(st.wh, st, "3", "10")
    assert r["status"] == "accepted"
    r = rule_set(st.wh, st, "4", "12")
    assert (r["status"], r["code"]) == ("rejected", "rule_exists")
    # Правила задаёт склад: закупке — denied.
    r = rule_set(st.pr, st, "1", "2")
    assert r["status"] == "denied"
    # ГИП правила и черновики не видит (нет stock.read).
    assert _rules(st.proj.gip) == [] and _drafts(st.proj.gip) == []


def test_update_disable_keeps_history(world, st, proj):  # noqa: F811
    receive(st.wh, st, "2")
    rule = rule_set(st.wh, st, "3", "10")["result"]["rule"]            # остаток уже ниже порога → черновик сразу
    d = _drafts(st.wh)
    assert len(d) == 1 and d[0]["qty"] == "8.0000"
    # Изменение порога: 0.5 — доступное 2 выше нового порога → открытый черновик закрывается системой.
    r = st.wh.ok("stock.replen_rule_update", {"id": rule["id"], "min_qty": "0.5"}, expected_version=1)
    assert r["rule"]["min_qty"] == "0.5000" and r["rule"]["enabled"] is True
    assert _drafts(st.wh)[0]["status"] == "resolved"
    # Отключение правила: история черновиков сохраняется, новые не создаются.
    r = st.wh.ok("stock.replen_rule_update", {"id": rule["id"], "enabled": False}, expected_version=2)
    assert r["rule"]["enabled"] is False
    reserve(st.wh, st, "1.9", project_id=proj.pid)                     # доступное 0.1 — но правило отключено
    assert len(_drafts(st.wh)) == 1                                    # черновик остался только старый (resolved)
    assert audit(world, "stock.replen_rule_update", "stock_replenishment_rule", rule["id"])


# ====================================================================== подтверждения и отклонение
def test_two_confirmations(world, st, proj):  # noqa: F811
    receive(st.wh, st, "2")
    rule_set(st.wh, st, "3", "10")
    d = _drafts(st.wh)[0]
    # Склад подтверждает → статус всё ещё open (ждём закупку); повтор склада — отказ.
    r = st.wh.ok("stock.replen_confirm", {"id": d["id"]}, expected_version=1)
    assert r["draft"]["status"] == "open" and r["draft"]["warehouse_confirmed_by"] is not None
    r = st.wh.cmd("stock.replen_confirm", {"id": d["id"]}, expected_version=r["draft"]["version"])
    assert (r["status"], r["code"]) == ("rejected", "already_confirmed")
    # Закупка подтверждает → confirmed; уведомление обеим сторонам.
    r = st.pr.ok("stock.replen_confirm", {"id": d["id"]}, expected_version=2)
    assert r["draft"]["status"] == "confirmed"
    ver = r["draft"]["version"]
    kinds = [n["kind"] for n in _pull(st.wh)["notifications"]]
    assert "replen_confirmed" in kinds
    # Подтверждённый не отклонить и не подтвердить снова; восстановление остатка его не закрывает.
    receive(st.wh, st, "20")
    assert _drafts(st.wh)[0]["status"] == "confirmed"
    r = st.pr.cmd("stock.replen_dismiss", {"id": d["id"], "reason": "Поздно"}, expected_version=ver)
    assert (r["status"], r["code"]) == ("rejected", "replen_state")


def test_dismiss_with_reason(world, st, proj):  # noqa: F811
    receive(st.wh, st, "2")
    rule_set(st.wh, st, "3", "10")
    d = _drafts(st.wh)[0]
    r = st.pr.cmd("stock.replen_dismiss", {"id": d["id"]}, expected_version=1)
    assert (r["status"], r["code"]) == ("rejected", "field_required")    # причина обязательна
    r = st.pr.ok("stock.replen_dismiss", {"id": d["id"], "reason": "Позицию снимают с производства"},
                 expected_version=1)
    assert r["draft"]["status"] == "dismissed" and r["draft"]["decision_note"] == "Позицию снимают с производства"
    # Новый цикл: остаток снова ниже порога → новый черновик (старый остаётся в истории).
    reserve(st.wh, st, "1", project_id=proj.pid)
    d = _drafts(st.wh)
    assert len(d) == 2 and {x["status"] for x in d} == {"open", "dismissed"}


def test_resolved_on_recovery(world, st, proj):  # noqa: F811
    receive(st.wh, st, "5")
    rule_set(st.wh, st, "3", "10")
    reserve(st.wh, st, "4", project_id=proj.pid)                       # доступное 1 < 3 → черновик
    d = _drafts(st.wh)[0]
    assert d["status"] == "open"
    receive(st.wh, st, "10")                                           # восстановление выше порога
    d = _drafts(st.wh)[0]
    assert d["status"] == "resolved"
    # Решать закрытый черновик нельзя.
    r = st.wh.cmd("stock.replen_confirm", {"id": d["id"]}, expected_version=d["version"])
    assert (r["status"], r["code"]) == ("rejected", "replen_state")
    # История черновика доступна складу.
    h = st.wh.query("history", object_type="stock_replenishment_draft", object_id=d["id"])
    assert h["status"] == "accepted"
    assert any(e.get("details", {}).get("automatic") for e in h["result"])
