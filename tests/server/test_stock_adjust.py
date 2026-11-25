"""Корректировка остатка с подписью руководителя отдела (D-77, сценарий С4).

Каждый тест закрепляет одно правило из server/stock.py (команды stock.adjust, stock.adjust_sign,
stock.adjust_cancel): инвентаризационную корректировку создаёт склад со снимком остатка, но движение журнала
появляется только после подписи руководителя отдела (четыре глаза — автор подписать не может). Предохранители в
момент подписи: остаток не изменился с момента создания (stale_balance) и факт не ниже активных резервов.
"""
import uuid

from conftest import PWD, new_id
from test_tasks_documents import audit, proj  # noqa: F401
from test_stock import SIGN_KEY, bal, receive, reserve, set_signature, st  # noqa: F401


def adj(s, st, new_qty, **kw):
    """Создание корректировки: склад+позиция фикстуры, фактический остаток new_qty."""
    return s.cmd("stock.adjust", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "new_qty": new_qty,
                                  "reason": "Акт инвентаризации № 4", **kw})


def adj_sign(s, a, **kw):
    """Подпись корректировки (по умолчанию — с паролем, действующая настройка по умолчанию)."""
    return s.cmd("stock.adjust_sign", {"id": a["id"], "sign_act": True, **kw}, expected_version=a["version"])


def adj_pull(s, aid):
    """Корректировка aid из снимка (актуальная версия и статус) или None."""
    return next((x for x in s.query("pull")["result"]["stock_adjustments"] if x["id"] == aid), None)


def head(world):
    """Руководитель отдела (подписант, D-77)."""
    world.make_user("dh1", "dept_head")
    return world.login("dh1")


# ====================================================================== создание
def test_adjust_create_no_movement_and_notification(world, st):
    """Создание ничего не проводит: остаток и журнал не меняются до подписи; руководители получают уведомление."""
    dh = head(world)
    receive(st.wh, st, "10")
    a = st.wh.ok("stock.adjust", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "new_qty": "8.5",
                                  "reason": "Акт инвентаризации № 4"})["adjustment"]
    assert (a["status"], a["on_hand"], a["new_qty"], a["delta"], a["movement_id"]) == \
        ("pending", "10.0000", "8.5000", "-1.5000", None)
    assert bal(st.wh, st) == ("10.0000", "0.0000", "10.0000")                          # остаток не изменился
    journal = st.wh.query("pull")["result"]["stock_movements"]
    assert all("Корректировка" not in m["source_doc"] for m in journal)
    # Видят и склад, и руководитель отдела; закупка — тоже (stock.read), ГИП — нет.
    for viewer in (st.wh, dh, st.pr):
        rows = [x for x in viewer.query("pull")["result"]["stock_adjustments"] if x["id"] == a["id"]]
        assert rows and rows[0]["status_label"] == "Ждёт подписи"
    assert st.proj.gip.query("pull")["result"]["stock_adjustments"] == []
    n = [x for x in dh.query("pull")["result"]["notifications"] if x.get("object_id") == a["id"]]
    assert n and n[0]["kind"] == "stock_adjust_pending"
    # Запись аудита: история объекта читается со складским правом (как у движений).
    h = st.pr.query("history", object_type="stock_adjustment", object_id=a["id"])
    assert h["status"] == "accepted" and h["result"][0]["details"]["new_qty"] == "8.5000"


def test_adjust_validation(world, st):
    """Валидация полей и отказы создания; права — только склад (A-61)."""
    receive(st.wh, st, "10")
    # Права: закупка, ГИП и даже админ корректировку не создают.
    for who in (st.pr, st.proj.gip, world.admin):
        assert who.cmd("stock.adjust", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "new_qty": "9",
                                        "reason": "x"})["status"] == "denied"
    # new_qty: обязательная десятичная строка, ≥ 0, не равна учётному остатку.
    assert adj(st.wh, st, None)["code"] == "invalid_type"
    assert adj(st.wh, st, 8)["code"] == "invalid_type"                   # не строка
    assert adj(st.wh, st, "-1")["code"] == "qty_negative"
    assert adj(st.wh, st, "10")["code"] == "no_change"
    assert adj(st.wh, st, "1.00001")["code"] == "too_many_fraction_digits"
    assert adj(st.wh, st, "9")["status"] == "accepted"
    # Одна пара склад+позиция — одна ждущая подписи корректировка.
    assert adj(st.wh, st, "9.5")["code"] == "adjust_pending_exists"
    # Повтор с тем же id (другой operation_id) — отказ, а не новая запись: идемпотентность по operation_id,
    # уникальность id защищает реестр.
    aid = [x for x in st.wh.query("pull")["result"]["stock_adjustments"] if x["status"] == "pending"][0]["id"]
    r = st.wh.cmd("stock.adjust", {"id": aid, "warehouse_id": st.w, "item_id": st.item, "new_qty": "9",
                                   "reason": "Акт инвентаризации № 4"})
    assert r["status"] == "rejected"
    assert len([x for x in st.wh.query("pull")["result"]["stock_adjustments"]]) == 1
    # Без основания нельзя.
    st.wh2.ok("warehouse.create", {"id": new_id(), "code": "СК-2", "name": "Площадка"})
    assert st.wh.cmd("stock.adjust", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "new_qty": "9",
                                      "reason": "  "})["code"] == "field_required"
    # Позиция без остатка: снимок 0, можно создать корректировку «появилось».
    w2 = [x for x in st.wh2.query("pull")["result"]["warehouses"] if x["code"] == "СК-2"][0]
    a = st.wh.ok("stock.adjust", {"id": new_id(), "warehouse_id": w2["id"], "item_id": st.item, "new_qty": "3",
                                  "reason": "Найдено при ревизии"})["adjustment"]
    assert (a["on_hand"], a["delta"]) == ("0.0000", "3.0000")


# ====================================================================== подпись
def test_adjust_sign_posts_movement(world, st):
    """Подпись проводит движение на |delta| с источником «Корректировка…», меняет остаток, пишет подпись."""
    dh = head(world)
    receive(st.wh, st, "10")
    a = st.wh.ok("stock.adjust", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "new_qty": "12", "reason": "Акт инвентаризации № 4"})["adjustment"]
    r = adj_sign(dh, a, sign_password=PWD)
    assert r["status"] == "accepted"
    out = r["result"]
    assert out["adjustment"]["status"] == "signed" and out["adjustment"]["signed_by"] is not None
    mv = out["movement"]
    assert (mv["kind"], mv["qty"]) == ("receipt", "2.0000")
    assert "Корректировка" in mv["source_doc"]
    assert bal(st.wh, st) == ("12.0000", "0.0000", "12.0000")
    assert out["balance"]["on_hand"] == "12.0000"
    # Подпись приехала в снимке, её содержание совпадает с подписанным (content_ok).
    row = [x for x in st.wh.query("pull")["result"]["stock_adjustments"] if x["id"] == a["id"]][0]
    sig = row["signature"]
    assert sig and sig["method"] == "password" and sig["content_ok"] is True and sig["signer_name"]
    # Автор получил уведомление.
    n = [x for x in st.wh.query("pull")["result"]["notifications"] if x.get("object_id") == a["id"]]
    assert n and n[0]["kind"] == "stock_adjust_signed"
    # Повторная подпись — отказ состояния; подпись повторяемой команды не дублирует движение.
    assert adj_sign(dh, out["adjustment"])["code"] == "adjust_state"
    assert len([m for m in st.wh.query("pull")["result"]["stock_movements"]
                if "Корректировка" in m["source_doc"]]) == 1


def test_adjust_sign_downward_issue(world, st):
    """Корректировка в минус — движение «Выдача» без проекта (списание по акту)."""
    dh = head(world)
    receive(st.wh, st, "10")
    a = st.wh.ok("stock.adjust", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "new_qty": "7", "reason": "Акт инвентаризации № 4"})["adjustment"]
    mv = adj_sign(dh, a, sign_password=PWD)["result"]["movement"]
    assert (mv["kind"], mv["qty"], mv["project_id"]) == ("issue", "3.0000", None)
    assert bal(st.wh, st) == ("7.0000", "0.0000", "7.0000")


def test_adjust_sign_guards(world, st):
    """Предохранители: четыре глаза, подписант — только руководитель отдела, остаток не «уплыл», не ниже резервов."""
    dh = head(world)
    receive(st.wh, st, "10")
    reserve(st.wh, st, "4")
    a = st.wh.ok("stock.adjust", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "new_qty": "3",
                                  "reason": "Акт инвентаризации № 4"})["adjustment"]   # факт 3 < резерв 4
    assert adj_sign(st.wh, a, sign_password=PWD)["code"] == "self_decision"        # автор не подписывает
    assert adj_sign(st.pr, a, sign_password=PWD)["status"] == "denied"             # закупка — нет
    assert adj_sign(dh, a, sign_password=PWD)["code"] == "adjust_below_reserved"   # ниже резерва — нельзя
    # Сняли резерв — подпись проходит; доступное не уходит в минус.
    res = [x for x in st.wh.query("pull")["result"]["stock_reservations"] if x["status"] == "active"][0]
    st.wh.ok("stock.release", {"id": res["id"], "reason": "Пересчёт"}, expected_version=res["version"])
    # После отказа adjust_below_reserved версия не менялась — подписываем ту же запись.
    assert adj_sign(dh, a, sign_password=PWD)["status"] == "accepted"
    assert bal(st.wh, st) == ("3.0000", "0.0000", "3.0000")
    # Остаток «уплыл» между созданием и подписью — stale_balance: создавайте заново.
    r2 = adj(st.wh, st, "2")
    assert r2["status"] == "accepted", r2
    b = r2["result"]["adjustment"]
    receive(st.wh, st, "1")
    assert adj_sign(dh, b, sign_password=PWD)["code"] == "stale_balance"
    assert bal(st.wh, st) == ("4.0000", "0.0000", "4.0000")                   # ничего не проведено
    assert [x for x in st.wh.query("pull")["result"]["stock_adjustments"]
            if x["id"] == b["id"]][0]["status"] == "pending"


def test_adjust_sign_methods(world, st):
    """Способ подписи — действующая настройка stock.act_signature: confirm без пароля, неверный пароль — отказ."""
    dh = head(world)
    receive(st.wh, st, "10")
    a = st.wh.ok("stock.adjust", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "new_qty": "9", "reason": "Акт инвентаризации № 4"})["adjustment"]
    assert adj_sign(dh, a, sign_password="неверно")["code"] == "bad_sign_password"
    assert adj_sign(dh, a)["code"] == "sign_password_required"                 # по умолчанию нужен пароль
    assert adj_sign(dh, a, sign_password=PWD)["status"] == "accepted"
    set_signature(world, "confirm")
    b = st.wh.ok("stock.adjust", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "new_qty": "9.5", "reason": "Акт инвентаризации № 4"})["adjustment"]
    assert adj_sign(dh, b)["status"] == "accepted"                             # confirm — пароль не нужен
    # Без флажка «подписываю» — отказ ещё до всего.
    c = st.wh.ok("stock.adjust", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "new_qty": "8", "reason": "Акт инвентаризации № 4"})["adjustment"]
    assert dh.cmd("stock.adjust_sign", {"id": c["id"], "sign_act": False},
                  expected_version=c["version"])["code"] == "act_not_signed"
    # Версия защищает от гонки подписи.
    assert dh.cmd("stock.adjust_sign", {"id": c["id"], "sign_act": True},
                  expected_version=99)["status"] == "conflict"
    # Повторная подпись той же (уже подписанной) — отказ состояния.
    b = adj_pull(st.wh, b["id"])
    assert adj_sign(dh, b)["code"] == "adjust_state"


# ====================================================================== отмена
def test_adjust_cancel_only_author_pending(world, st):
    """Отменяет автор и только до подписи; причина обязательна; подписанная не отменяется."""
    dh = head(world)
    receive(st.wh, st, "10")
    a = st.wh.ok("stock.adjust", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "new_qty": "8", "reason": "Акт инвентаризации № 4"})["adjustment"]
    cancel = lambda s, x, **kw: s.cmd("stock.adjust_cancel", {"id": x["id"], **kw}, expected_version=x["version"])
    assert cancel(st.wh, a)["code"] == "field_required"                        # причина обязательна
    assert cancel(st.wh2, a, reason="x")["code"] == "not_author"               # чужой складской — нет
    assert cancel(dh, a, reason="x")["status"] == "denied"                     # руководитель не отменяет
    out = cancel(st.wh, a, reason="Ошибка в основании")["result"]["adjustment"]
    assert out["status"] == "cancelled" and out["cancel_reason"] == "Ошибка в основании"
    assert cancel(st.wh, out, reason="ещё раз")["code"] == "adjust_state"
    # После отмены можно создать новую корректировку по той же паре.
    b = st.wh.ok("stock.adjust", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "new_qty": "11", "reason": "Акт инвентаризации № 4"})["adjustment"]
    assert b["status"] == "pending"
    adj_sign(dh, b, sign_password=PWD)
    b = [x for x in st.wh.query("pull")["result"]["stock_adjustments"] if x["id"] == b["id"]][0]
    assert st.wh.cmd("stock.adjust_cancel", {"id": b["id"], "reason": "передумал"},
                     expected_version=b["version"])["code"] == "adjust_state"
    assert bal(st.wh, st) == ("11.0000", "0.0000", "11.0000")
    # История сохранена: и отменённая, и подписанная — в снимке.
    rows = st.wh.query("pull")["result"]["stock_adjustments"]
    assert sorted({x["status"] for x in rows}) == ["cancelled", "signed"]
    # Восстановление: uuid5-движение той же корректировки не может быть проведено дважды (защита реестра).
    mid = str(uuid.uuid5(uuid.UUID(b["id"]), "movement"))
    r = st.wh.cmd("stock.receive", {"id": mid, "warehouse_id": st.w, "item_id": st.item, "qty": "1",
                                    "source_doc": "ТН-9"})
    assert r["status"] == "rejected"
