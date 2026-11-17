"""Представление склада/закупки на клиенте (vector.client.stock_view) — без Qt, на синтетических снимках.

Проверяем то, что видит пользователь и на что опираются кнопки: подписи количеств (неизвестно ≠ 0), состояния
позиций и строк потребности, подсказки прав (проводки — только склад и только онлайн), «на момент …».
"""
from vector.client import stock_view as sv

WH = [{"id": "w1", "code": "СК-1", "name": "Центральный", "active": True},
      {"id": "w2", "code": "СК-2", "name": "Участок", "active": True}]
ITEMS = [{"id": "i1", "code": "КАБ", "name": "Кабель", "unit": "м", "active": True},
         {"id": "i2", "code": "ДД", "name": "Датчик", "unit": "шт", "active": True},
         {"id": "i3", "code": "ШУ", "name": "Шкаф", "unit": "шт", "active": True},
         {"id": "i4", "code": "ОЛД", "name": "Снятая позиция", "unit": "шт", "active": False, "_pending": True}]
BAL = [{"warehouse_id": "w1", "item_id": "i1", "on_hand": "100.5000", "reserved": "20.0000", "available": "80.5000"},
       {"warehouse_id": "w2", "item_id": "i1", "on_hand": "10.0000", "reserved": "0.0000", "available": "10.0000"},
       {"warehouse_id": "w1", "item_id": "i3", "on_hand": "2.0000", "reserved": "2.0000", "available": "0.0000"}]
RES = [{"id": "r1", "warehouse_id": "w1", "item_id": "i1", "project_id": "p1", "need_id": None, "need_line_id": None,
        "qty": "20.0000", "consumed": "0.0000", "remaining": "20.0000", "status": "active", "reason": "монтаж",
        "close_reason": None, "version": 1, "created_by": "u1", "created_at": "2026-11-17T05:00:00Z"},
       {"id": "r2", "warehouse_id": "w1", "item_id": "i3", "project_id": "p-hidden", "need_id": None, "need_line_id": None,
        "qty": "2.0000", "consumed": "2.0000", "remaining": "0.0000", "status": "consumed", "reason": "ПНР",
        "close_reason": None, "version": 2, "created_by": "u1", "created_at": "2026-11-17T04:00:00Z"}]
MV = [{"id": "m1", "doc_no": 1, "kind": "receipt", "warehouse_id": "w1", "item_id": "i1", "qty": "100.5000", "project_id": None,
       "reservation_id": None, "reverses_id": None, "reversed_by": None, "source_doc": "ТН 1", "reason": None,
       "posted_by": "u1", "posted_at": "2026-11-17T05:00:00Z"},
      {"id": "m2", "doc_no": 2, "kind": "receipt", "warehouse_id": "w1", "item_id": "i2", "qty": "5.0000", "project_id": None,
       "reservation_id": None, "reverses_id": None, "reversed_by": "m3", "source_doc": "ТН 2", "reason": None,
       "posted_by": "u1", "posted_at": "2026-11-17T05:01:00Z"},
      {"id": "m3", "doc_no": 3, "kind": "issue", "warehouse_id": "w1", "item_id": "i2", "qty": "5.0000", "project_id": None,
       "reservation_id": None, "reverses_id": "m2", "reversed_by": None, "source_doc": "ТН 2", "reason": "ошибка",
       "posted_by": "u1", "posted_at": "2026-11-17T05:02:00Z"}]
PROJ = [{"id": "p1", "code": "П-1", "title": "Котельная"}]
PEOPLE = {"u1": "Белова Ирина"}
PULL = "2026-11-17T05:21:33.123456+00:00"


def _ov(online=True, roles=("warehouse",)):
    """Обзор склада по тестовым данным модуля для заданного режима связи и ролей."""
    return sv.overview(WH, ITEMS, BAL, RES, MV, PROJ, PEOPLE, PULL, online, list(roles))


def test_labels_unknown_is_not_zero_and_snapshot_time_is_explicit():
    assert sv.qty_label("1850.0000", "м") == "1 850 м"
    assert sv.qty_label("0.0000", "шт") == "0 шт"
    assert sv.qty_label(None, "шт") == "—"
    assert sv.as_of_label(PULL) == "на момент 17.11.2026 05:21 UTC"
    assert sv.as_of_label("") == "данные склада ещё не загружались"


def test_item_states_totals_and_per_warehouse_rows():
    ov = _ov()
    by = {r["code"]: r for r in ov["items"]}
    cab = by["КАБ"]
    assert (cab["on_hand_label"], cab["reserved_label"], cab["available_label"]) == ("110,5 м", "20 м", "90,5 м")
    assert (cab["state"], cab["state_label"]) == ("partly_reserved", "Есть резерв")
    assert [b["warehouse_code"] for b in cab["by_warehouse"]] == ["СК-1", "СК-2"]
    assert [r["id"] for r in cab["reservations"]] == ["r1"]                     # только действующие
    assert by["ДД"]["state_label"] == "Нет на складе"                          # приход сторнирован → 0
    assert by["ШУ"]["state_label"] == "Всё в резерве"
    assert by["ОЛД"]["state_label"] == "Отключена" and by["ОЛД"]["pending"] is True
    assert ov["summary"] == {"items": 4, "with_stock": 2, "active_reservations": 1, "movements": 3}
    assert {w["code"]: w["item_count"] for w in ov["warehouses"]} == {"СК-1": 2, "СК-2": 1}


def test_journal_newest_first_with_signs_and_storno_link():
    mv = _ov()["movements"]
    assert [m["doc_no"] for m in mv] == [3, 2, 1]
    st = mv[0]
    assert st["is_storno"] is True and st["reverses_doc_no"] == 2 and st["qty_label"] == "−5 шт"
    assert mv[1]["reversed"] is True and mv[1]["qty_label"] == "+5 шт"
    assert mv[2]["author"] == "Белова Ирина" and mv[2]["posted_label"] == "2026-11-17 05:00"


def test_hidden_project_is_named_as_hidden_not_blank():
    res = {r["id"]: r for r in _ov()["reservations"]}
    assert res["r1"]["project_code"] == "П-1"
    assert res["r2"]["project_code"] == "проект скрыт"
    assert _ov()["reservations"][0]["id"] == "r1"                              # действующие — первыми


def test_action_hints_by_role_and_connection():
    on, off = _ov(True), _ov(False)
    assert (on["can_post"], off["can_post"]) == (True, False)                 # проводки — только онлайн
    assert off["can_edit_refs"] is True and off["can_edit_warehouses"] is True  # справочники — через очередь
    buyer = _ov(True, ("procurement",))
    assert (buyer["can_post"], buyer["can_edit_refs"], buyer["can_edit_warehouses"]) == (False, True, False)
    gip = _ov(True, ("gip",))
    assert not any((gip["can_post"], gip["can_edit_refs"], gip["is_warehouse"]))


def _need(lines):
    """Потребность-заготовка с заданными строками."""
    return {"id": "n1", "project_id": "p1", "project_code": "П-1", "kit_code": "АТХ", "calc_version_no": 1,
            "latest_calc_version_no": 1, "superseded": False, "lines": lines}


def _line(lid, item_id, qty, reserved, open_, available, can_cover):
    """Строка потребности с заданным покрытием (требуется / в резерве / открыто / доступно)."""
    return {"line_id": lid, "pos": 1, "code": "X", "name": "x", "unit": "шт", "qty": qty, "item_id": item_id,
            "coverage": {"required": qty, "reserved": reserved, "open": open_, "available": available, "can_cover": can_cover}}


def test_need_line_states_and_counts():
    n = _need([_line("a", None, "1.0000", "0.0000", "1.0000", None, None),
               _line("b", "i1", "2.0000", "2.0000", "0.0000", "0.0000", True),
               _line("c", "i2", "3.0000", "0.0000", "3.0000", "5.0000", True),
               _line("d", "i3", "3.0000", "2.0000", "1.0000", "0.0000", False),
               _line("e", "i3", "1.0000", "0.0000", "1.0000", None, None)])
    v = sv.needs_view([n], [], ITEMS, PULL, True, ["procurement"])
    ln = {x["line_id"]: x for x in v["needs"][0]["lines"]}
    assert [ln[k]["state_label"] for k in "abcde"] == ["Не сопоставлена", "Покрыта резервом", "Есть на складе",
                                                       "Нужна закупка", "Остаток неизвестен"]
    assert ln["e"]["available_label"] == "неизвестно" and ln["d"]["available_label"] == "0 шт"
    assert ln["c"]["item_label"] == "ДД · Датчик"
    nv = v["needs"][0]
    assert (nv["open_count"], nv["unmatched_count"], nv["covered_count"]) == (3, 1, 1)
    assert nv["title"] == "П-1 · АТХ v1"


def test_need_hints_by_role_and_connection():
    assert sv.needs_view([], [], [], PULL, True, ["procurement"])["can_generate"] is True
    assert sv.needs_view([], [], [], PULL, False, ["procurement"])["can_generate"] is False
    wh = sv.needs_view([], [], [], PULL, True, ["warehouse"])
    assert (wh["can_generate"], wh["can_reserve"], wh["sees_stock"]) == (False, True, True)
    gip = sv.needs_view([], [], [], PULL, True, ["gip"])
    assert (gip["can_generate"], gip["can_reserve"], gip["sees_stock"]) == (False, False, False)
