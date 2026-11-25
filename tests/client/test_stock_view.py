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
    assert ov["summary"] == {"items": 4, "with_stock": 2, "active_reservations": 1, "in_transit": 0, "movements": 3,
                                  "adjust_pending": 0}
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


def test_transfer_legs_show_route_and_transfer_needs_two_active_warehouses():
    legs = [{**MV[0], "id": "t-out", "doc_no": 10, "kind": "issue", "qty": "4.0000", "transfer_id": "t1",
             "transfer_direction": "out", "transfer_peer_warehouse_id": "w2"},
            {**MV[0], "id": "t-in", "doc_no": 11, "kind": "receipt", "warehouse_id": "w2", "qty": "4.0000",
             "transfer_id": "t1", "transfer_direction": "in", "transfer_peer_warehouse_id": "w1"}]
    ov = sv.overview(WH, ITEMS, BAL, RES, MV + legs, PROJ, PEOPLE, PULL, True, ["warehouse"])
    rows = {m["id"]: m for m in ov["movements"]}
    assert (rows["t-out"]["transfer_label"], rows["t-in"]["transfer_label"]) == ("СК-1 → СК-2", "СК-1 → СК-2")
    assert rows["t-out"]["is_transfer"] and rows["t-out"]["tone"] == "info" and rows["t-out"]["qty_label"] == "−4 м"
    assert rows["m1"]["is_transfer"] is False and rows["m1"]["transfer_label"] == ""
    assert ov["can_transfer"] is True and _ov(False)["can_transfer"] is False
    assert _ov(True, ("procurement",))["can_transfer"] is False
    one = sv.overview(WH[:1] + [{**WH[1], "active": False}], ITEMS, BAL, RES, MV, PROJ, PEOPLE, PULL, True, ["warehouse"])
    assert one["can_transfer"] is False                                       # некуда перемещать


def test_in_transit_is_shown_apart_from_stock_and_only_warehouse_online_can_act():
    """D-29: «в пути» не входит ни в остаток, ни в доступное — отдельная сумма на карточке и список с действиями;
    принять/вернуть — склад при связи; закрытые и исторические (v8) — только история; возврат — маршрут исходный."""
    i1 = ITEMS[0]["id"]
    trs = [{"id": "t1", "item_id": i1, "from_warehouse_id": "w1", "to_warehouse_id": "w2", "qty": "4.0000",
            "status": "in_transit", "status_label": "В пути", "version": 1, "reason": "бригаде", "created_by": "u1",
            "created_at": "2026-11-17T10:00:00+00:00", "legacy": False},
           {"id": "t2", "item_id": i1, "from_warehouse_id": "w1", "to_warehouse_id": "w2", "qty": "1.5000",
            "status": "in_transit", "status_label": "В пути", "version": 1, "reason": "ещё", "legacy": False},
           {"id": "t3", "item_id": i1, "from_warehouse_id": "w2", "to_warehouse_id": "w1", "qty": "2.0000",
            "status": "received", "status_label": "Принято", "version": None, "legacy": True}]
    ov = sv.overview(WH, ITEMS, BAL, RES, MV, PROJ, PEOPLE, PULL, True, ["warehouse"], trs)
    item = next(x for x in ov["items"] if x["id"] == i1)
    plain = next(x for x in _ov()["items"] if x["id"] == i1)
    assert (item["on_hand_label"], item["available"]) == (plain["on_hand_label"], plain["available"])   # остаток не меняется
    assert item["in_transit_label"] == "5,5 м" and [t["id"] for t in item["transfers"]] == ["t1", "t2"]
    assert ov["summary"]["in_transit"] == 2 and plain["in_transit"] == "0"
    rows = {t["id"]: t for t in ov["transfers"]}
    assert rows["t1"]["route_label"] == "СК-1 → СК-2" and rows["t1"]["status_tone"] == "warning"
    assert (rows["t1"]["can_act"], rows["t3"]["can_act"]) == (True, False)    # история — без действий
    off = sv.overview(WH, ITEMS, BAL, RES, MV, PROJ, PEOPLE, PULL, False, ["warehouse"], trs)
    buyer = sv.overview(WH, ITEMS, BAL, RES, MV, PROJ, PEOPLE, PULL, True, ["procurement"], trs)
    assert not any(t["can_act"] for t in off["transfers"] + buyer["transfers"])
    ret = {**MV[0], "id": "t-ret", "doc_no": 12, "kind": "receipt", "qty": "4.0000", "transfer_id": "t1",
           "transfer_direction": "return", "transfer_peer_warehouse_id": "w2"}
    row = next(m for m in sv.overview(WH, ITEMS, BAL, RES, MV + [ret], PROJ, PEOPLE, PULL, True, ["warehouse"])["movements"]
               if m["id"] == "t-ret")
    assert row["transfer_label"] == "СК-1 → СК-2" and row["is_transfer"] is True


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


def test_need_line_inbound_shows_ordered_and_caps_reserve():
    """D-60: «уже идёт по закупке» (заказы + непоставленные пакеты) видно в состоянии строки, а форма резерва
    предлагает только непокрытое минус идущее — больше сервер не примет (exceeds_need)."""
    n = _need([_line("a", "i1", "3.0000", "2.0000", "1.0000", "0.0000", False)])
    v = sv.needs_view([n], [], ITEMS, PULL, True, ["warehouse"])
    ln = v["needs"][0]["lines"][0]
    assert (ln["state_label"], ln["reserve_open_label"]) == ("Нужна закупка", "1 шт")   # без снимка закупки — как раньше
    ln_in = {**_line("a", "i1", "3.0000", "2.0000", "1.0000", "0.0000", False), "on_order": "1.0000"}
    ln = sv.needs_view([_need([ln_in])], [], ITEMS, PULL, True, ["warehouse"])["needs"][0]["lines"][0]
    assert (ln["state_label"], ln["reserve_open_label"]) == ("Заказана, ждём", "0 шт")
    ln_b = {**_line("a", "i1", "3.0000", "2.0000", "1.0000", "5.0000", True), "in_batches": "1.5000"}
    ln = sv.needs_view([_need([ln_b])], [], ITEMS, PULL, True, ["warehouse"])["needs"][0]["lines"][0]
    assert (ln["state_label"], ln["reserve_open_label"]) == ("Заказана, ждём", "0 шт")   # 1 непокрыто − 1,5 идёт
    ln_b2 = {**_line("a", "i1", "3.0000", "2.0000", "1.0000", "5.0000", True), "in_batches": "0.2500"}
    ln = sv.needs_view([_need([ln_b2])], [], ITEMS, PULL, True, ["warehouse"])["needs"][0]["lines"][0]
    assert (ln["state_label"], ln["reserve_open_label"]) == ("Заказана, ждём", "0,75 шт")


def test_need_hints_by_role_and_connection():
    assert sv.needs_view([], [], [], PULL, True, ["procurement"])["can_generate"] is True
    assert sv.needs_view([], [], [], PULL, False, ["procurement"])["can_generate"] is False
    wh = sv.needs_view([], [], [], PULL, True, ["warehouse"])
    assert (wh["can_generate"], wh["can_reserve"], wh["sees_stock"]) == (False, True, True)
    gip = sv.needs_view([], [], [], PULL, True, ["gip"])
    assert (gip["can_generate"], gip["can_reserve"], gip["sees_stock"]) == (False, False, False)


def test_manual_match_labels_and_rematch_hint():
    auto = _line("a", "i1", "2.0000", "0.0000", "2.0000", "10.0000", True)
    auto["match_source"] = "auto"
    manual = _line("b", "i3", "1.0000", "1.0000", "0.0000", "0.0000", True)
    manual.update(match_source="manual", match_reason="Аналог по паспорту", match_count=2)
    none = _line("c", None, "1.0000", "0.0000", "1.0000", None, None)
    v = sv.needs_view([_need([auto, manual, none])], [], ITEMS, PULL, True, ["procurement"])
    ln = {x["line_id"]: x for x in v["needs"][0]["lines"]}
    assert ln["a"]["match_label"].startswith("Сопоставлена автоматически")
    assert ln["b"]["match_label"] == "Сопоставлена вручную (решение №2): Аналог по паспорту"
    assert ln["c"]["match_label"] == ""
    # Пересопоставить можно только без резервов под строку (как на сервере).
    assert (ln["a"]["can_rematch"], ln["b"]["can_rematch"], ln["c"]["can_rematch"]) == (True, False, True)
    assert v["can_match"] is True
    assert sv.needs_view([], [], [], PULL, False, ["procurement"])["can_match"] is False
    assert sv.needs_view([], [], [], PULL, True, ["warehouse"])["can_match"] is False


# ------------------------------------------------------------------ аналоги, пересчёт, настройки (D-24..D-26, A-27)
def _conv_line(**kw):
    """Строка потребности в форме сервера (v_need) для проверок кандидатов и подписей."""
    base = {"pos": 1, "line_id": "L1", "code": "К-1", "name": "Кабель", "unit": "км", "qty": "1.8500", "item_id": None,
            "match_source": None, "match_note": "Единица не совпала", "auto_match_note": None, "match_count": 0,
            "item_unit": "км", "item_qty": "1.8500", "factor": None,
            "coverage": {"required": "1.8500", "reserved": "0.0000", "open": "1.8500", "available": None, "can_cover": None}}
    return {**base, **kw}


def test_match_candidates_same_unit_and_conversion_exact_or_flagged():
    from vector.client.stock_view import match_candidates
    items = [{"id": "m", "code": "КВВГ", "name": "Кабель", "unit": "м", "active": True},
             {"id": "k", "code": "КВВГ-км", "name": "Кабель в км", "unit": "км", "active": True},
             {"id": "u", "code": "УП", "name": "Упаковка", "unit": "упак", "active": True},
             {"id": "x", "code": "OFF", "name": "Отключена", "unit": "км", "active": False},
             {"id": "t", "code": "Т", "name": "Без пересчёта", "unit": "т", "active": True}]
    convs = [{"id": "g", "item_id": None, "from_unit": "км", "to_unit": "м", "factor": "1000.0000", "active": True},
             {"id": "s", "item_id": "m", "from_unit": "км", "to_unit": "м", "factor": "1001.0000", "active": True},
             {"id": "p", "item_id": None, "from_unit": "км", "to_unit": "упак", "factor": "0.0001", "active": True}]
    c = {x["id"]: x for x in match_candidates(_conv_line(), items, convs)}
    assert set(c) == {"m", "k", "u"}                                    # отключённая и без пересчёта — не предлагаются
    assert c["k"]["conversion_id"] == "" and c["k"]["problem"] == ""     # та же единица — без пересчёта
    assert (c["m"]["conversion_id"], c["m"]["factor"]) == ("s", "1001.0000")   # для позиции важнее общего
    assert c["m"]["item_qty_label"] == "1 851,85 м" and "1 км = 1 001 м" in c["m"]["label"]
    assert c["u"]["problem"] and "4 знаков" in c["u"]["problem"]         # 1,85 × 0,0001 — неточно, не округляем


def test_needs_view_labels_coverage_in_item_units_and_rematch_by_setting():
    from vector.client.stock_view import needs_view
    ln = _conv_line(item_id="m", match_source="manual", match_count=1, match_reason="В метрах", factor="1000.0000",
               item_unit="м", item_qty="1850.0000", match_note=None,
               coverage={"required": "1850.0000", "reserved": "100.0000", "open": "1750.0000", "available": "5000.0000",
                         "can_cover": True})
    need = {"id": "N", "lines": [ln], "calc_version_no": 1, "project_code": "П", "kit_code": "К"}
    items = [{"id": "m", "code": "КВВГ", "name": "Кабель", "unit": "м", "active": True}]
    v = needs_view([need], [], items, "", True, ["procurement"])
    out = v["needs"][0]["lines"][0]
    assert out["reserved_label"] == "100 м" and out["open_label"] == "1 750 м" and out["qty_label"] == "1,85 км"
    assert out["item_qty_label"] == "1 850 м"
    assert out["match_label"] == "Сопоставлена вручную; пересчёт 1 км = 1 000 м: В метрах"
    assert out["can_rematch"] is False                                   # не задано → самое строгое (запрет)
    cfg = [{"key": "procurement.rematch_reserved_line", "value": "release_active"}]
    out = needs_view([need], [], items, "", True, ["procurement"], [], cfg)["needs"][0]["lines"][0]
    assert out["can_rematch"] is True and "будут сняты" in out["rematch_warning"]


def test_alias_label_and_directory_and_settings_views():
    from vector.client.stock_view import _match_label, directory_view, settings_view
    assert _match_label({"match_source": "alias", "auto_match_note": "По другому обозначению «ШК-1» → позиция «ШК-1-И»."}) \
        == "Сопоставлена автоматически. По другому обозначению «ШК-1» → позиция «ШК-1-И»."
    items = [{"id": "m", "code": "КВВГ", "name": "Кабель", "unit": "м", "active": True}]
    d = directory_view([{"id": "a", "item_id": "m", "alias": "K-1", "note": None, "active": False, "version": 2}],
                       [{"id": "g", "item_id": None, "from_unit": "км", "to_unit": "м", "factor": "1000.0000", "note": None,
                         "active": True, "version": 1}], items, ["warehouse"], True)
    assert d["can_edit"] is True and d["aliases"][0]["item_label"] == "КВВГ · Кабель"
    assert d["aliases"][0]["state_label"] == "Отключено"
    assert d["conversions"][0]["rule_label"] == "1 км = 1 000 м" and d["conversions"][0]["scope_label"].startswith("Общий")
    assert directory_view([], [], items, ["warehouse"], False)["can_edit"] is False      # только при связи
    assert directory_view([], [], items, ["gip"], True)["can_edit"] is False
    cfg = [{"key": "k", "label_ru": "Настройка", "value": "forbid", "is_set": False, "version": 0,
            "values": [{"value": "forbid", "label_ru": "Запрещено"}, {"value": "release_active", "label_ru": "Снимать"}]}]
    s = settings_view(cfg, ["admin"], True)
    assert s["can_publish"] is True and s["items"][0]["value_label"] == "Запрещено"
    assert s["items"][0]["state_label"].startswith("Не задано")
    assert [o["id"] for o in s["items"][0]["options"]] == ["forbid", "release_active"]
    assert settings_view(cfg, ["warehouse"], True)["can_publish"] is False
    assert settings_view(cfg, ["admin"], False)["can_publish"] is False


def test_shortage_is_decimal_and_follows_server_rules():
    """D-30: недостача в форме приёмки — Decimal (без float), пусто = пришло всё, больше отгруженного — ошибка."""
    assert sv.shortage("10.0000", "", "шт") == {"problem": "", "shortage": "0", "shortage_label": "", "has_shortage": False}
    r = sv.shortage("10.0000", "7,5", "шт")
    assert (r["has_shortage"], r["shortage"], r["shortage_label"], r["problem"]) == (True, "2.5000", "2,5 шт", "")
    assert sv.shortage("0.3000", "0.1", "м")["shortage"] == "0.2000"             # 0,3 − 0,1 без ошибки float
    assert sv.shortage("10", "10")["has_shortage"] is False
    assert "Больше отгруженного" in sv.shortage("10", "10.0001")["problem"]
    assert sv.shortage("10", "1.23456")["problem"] != "" and sv.shortage("10", "0")["problem"] != ""


def test_transfer_rows_show_act_and_expected_date_for_reference():
    """D-30: строка перемещения показывает акт недостачи (тон «danger») и срок прибытия; «срок прошёл» — по дате
    снимка, только у «в пути»."""
    i1 = ITEMS[0]["id"]
    act = {"id": "a1", "act_no": 3, "shipped_qty": "10.0000", "accepted_qty": "8.0000", "shortage_qty": "2.0000",
           "reason": "Бой", "signed_by": "u1", "signer_name": "Белова И.", "signed_at": "2026-11-17T09:00:00+00:00"}
    trs = [{"id": "t1", "item_id": i1, "from_warehouse_id": "w1", "to_warehouse_id": "w2", "qty": "10.0000",
            "status": "received", "status_label": "Принято с недостачей", "accepted_qty": "8.0000", "act": act,
            "version": 2, "legacy": False},
           {"id": "t2", "item_id": i1, "from_warehouse_id": "w1", "to_warehouse_id": "w2", "qty": "1.0000",
            "status": "in_transit", "status_label": "В пути", "expected_on": "2000-01-01", "version": 1, "legacy": False},
           {"id": "t3", "item_id": i1, "from_warehouse_id": "w1", "to_warehouse_id": "w2", "qty": "1.0000",
            "status": "in_transit", "status_label": "В пути", "expected_on": "2999-12-31", "version": 1, "legacy": False}]
    rows = {t["id"]: t for t in sv.overview(WH, ITEMS, BAL, RES, MV, PROJ, PEOPLE, PULL, True, ["warehouse"], trs)["transfers"]}
    assert rows["t1"]["status_tone"] == "danger" and rows["t1"]["accepted_label"] == "8 м"
    assert rows["t1"]["act_label"] == "Акт недостачи № 3: отгружено 10 м, принято 8 м, недостача 2 м"
    assert rows["t1"]["act_signed_label"] == "Бой  ·  подписал приёмщик Белова И., 2026-11-17 09:00 UTC"
    assert (rows["t2"]["expected_label"], rows["t2"]["overdue"], rows["t3"]["overdue"]) == ("01.01.2000", True, False)
    assert rows["t1"]["overdue"] is False and rows["t1"]["act_label"] and rows["t2"]["act_label"] == ""
    no_pull = sv.overview(WH, ITEMS, BAL, RES, MV, PROJ, PEOPLE, "", True, ["warehouse"], trs)
    assert not any(t["overdue"] for t in no_pull["transfers"])                    # нет снимка — срок не оцениваем


def test_signature_mode_and_lost_rows():
    """D-31: форма подписи по настройке (нет в снимке → пароль; КЭП → подписать нельзя), строка утраты — «danger»,
    без действий; подпись показывает способ, а несовпадение содержания — предупреждение."""
    cfg = lambda v: [{"key": "stock.act_signature", "value": v}]  # noqa: E731
    assert sv.signature_mode([])["needs_password"] is True
    assert (sv.signature_mode(cfg("confirm"))["needs_password"], sv.signature_mode(cfg("confirm"))["blocked"]) == (False, False)
    q = sv.signature_mode(cfg("qes"))
    assert q["blocked"] is True and "КЭП" in q["hint"]
    i1 = ITEMS[0]["id"]
    sig = {"method": "password", "method_label": "с вводом пароля", "signer_name": "Белова И.",
           "signed_at": "2026-11-17T09:00:00+00:00", "content_ok": False}
    trs = [{"id": "t9", "item_id": i1, "from_warehouse_id": "w1", "to_warehouse_id": "w2", "qty": "4.0000",
            "status": "lost", "status_label": "Утрачено в пути", "version": 2, "legacy": False,
            "loss": {"act_no": 5, "qty": "4.0000", "reason": "Не прибыло", "signed_by": "u1", "signer_name": "Белова И.",
                     "signed_at": "2026-11-17T09:00:00+00:00", "signature": sig}}]
    ov = sv.overview(WH, ITEMS, BAL, RES, MV, PROJ, PEOPLE, PULL, True, ["warehouse"], trs, cfg("confirm"))
    row = ov["transfers"][0]
    assert (row["status_tone"], row["can_act"], row["loss_label"]) == ("danger", False, "Акт об утрате № 5: утрачено 4 м")
    assert "с вводом пароля" in row["loss_signed_label"] and "не совпадает с подписанным" in row["loss_signed_label"]
    assert ov["act_signature"]["method"] == "confirm" and ov["summary"]["in_transit"] == 0
    s = sv.settings_view([{"key": "stock.act_signature", "value": "password", "is_set": False,
                           "values": [{"value": "confirm", "label_ru": "Флажок"}, {"value": "password", "label_ru": "Пароль"},
                                      {"value": "qes", "label_ru": "КЭП", "available": False, "unavailable_ru": "не подключена"}]}],
                         ["admin"], True)
    it = s["items"][0]
    assert [o["id"] for o in it["options"]] == ["confirm", "password"] and it["unavailable"] == ["КЭП — не подключена"]
