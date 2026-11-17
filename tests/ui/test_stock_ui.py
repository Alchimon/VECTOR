"""UI-проверки склада и закупки (С4): настоящий QML, сервер in-process, демо-данные (vector.server.demo).

Демо: i.belova (склад), s.morozov (закупка). Склады СК-1/СК-2; АРМ — 1 компл, весь в резерве; ШУ-1 — 2 шт, всё
в резерве под потребность АТХ v1 (нужно 3 → 1 не покрыт, «Нужна закупка»); потребность ЭМ v1 ещё не сформирована
(кандидат); приход НШВИ-1.5 10 000 шт сторнирован («лишний ноль»); 200 шт НШВИ-1.5 перемещены СК-1 → СК-2.
"""
import pytest
from PySide6.QtCore import QMetaObject, QObject, Q_ARG

pytestmark = pytest.mark.ui


def _v(item, prop):
    """Значение свойства QML как обычное значение Python (QJSValue → toVariant)."""
    v = item.property(prop)
    return v.toVariant() if hasattr(v, "toVariant") else v


def _popup(ui, name):
    """Всплывающее окно QML по objectName (Popup не виден через дерево элементов, поэтому ищем среди всех QObject).
    """
    return next(o for o in ui.win.findChildren(QObject) if o.objectName() == name)


def _stock_page(ui):
    """Видимая страница «Склад»."""
    return next(i for i in ui.items("whOptions") if i.isVisible())


def _call(obj, method, *args):
    """Вызвать функцию QML объекта с аргументами QVariant."""
    QMetaObject.invokeMethod(obj, method, *[Q_ARG("QVariant", a) for a in args])


def _item(ui, code):
    """Строка позиции из обзора склада по коду."""
    return next(i for i in _v(_stock_page(ui), "ov")["items"] if i["code"] == code)


def test_warehouse_starts_on_stock_and_sees_snapshot(ui):
    ui.login("i.belova")
    assert ui.page() == "stock"                                 # у склада нет тендеров — стартовый раздел «Склад»
    nav = ui.nav()
    assert nav["Склад"] and nav["Закупка"] and not nav["Тендеры и ТКП"] and not nav["Администрирование"]
    ov = _v(_stock_page(ui), "ov")
    assert ov["can_post"] is True and ov["as_of"].startswith("на момент ")
    arm = _item(ui, "АРМ")
    assert (arm["available_label"], arm["state_label"]) == ("0 компл", "Всё в резерве")
    storno = next(m for m in ov["movements"] if m["is_storno"])
    assert storno["kind_label"] == "Сторно прихода" and storno["reverses_doc_no"] is not None


def test_receive_through_form_updates_balance_and_journal(ui):
    ui.login("i.belova")
    page = _stock_page(ui)
    before = len(_v(page, "ov")["movements"])
    dlg = _popup(ui, "receiveForm")
    _call(dlg, "openFor", _item(ui, "ДД"))
    ui.wait_idle()
    qty, src = _popup(ui, "rQty"), _popup(ui, "rSrc")
    qty.setProperty("text", "1,23456")                          # 5 знаков — клиент подсказывает ошибку до отправки
    ui.wait_idle()
    assert qty.property("error") != "" and dlg.property("canConfirm") is False
    qty.setProperty("text", "4,5")
    src.setProperty("text", "ТН № 500")
    ui.wait_idle()
    assert dlg.property("canConfirm") is True
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert not dlg.property("visible") and dlg.property("error") == ""
    dd = _item(ui, "ДД")
    assert (dd["on_hand_label"], dd["state_label"]) == ("4,5 шт", "Свободно")
    mv = _v(page, "ov")["movements"]
    assert len(mv) == before + 1 and mv[0]["source_doc"] == "ТН № 500" and mv[0]["qty_label"] == "+4,5 шт"


def test_offline_postings_disabled_refs_queue(ui):
    ui.login("i.belova")
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    page = _stock_page(ui)
    ov = _v(page, "ov")
    assert ov["online"] is False and ov["can_post"] is False and ov["can_edit_refs"] is True
    page.setProperty("selectedId", _item(ui, "НШВИ-1.5")["id"])
    ui.wait_idle()
    assert ui.find("btnReceive").property("enabled") is False
    # Новая позиция номенклатуры офлайн — в очередь, видна с пометкой «не отправлено».
    dlg = _popup(ui, "itemForm")
    _call(dlg, "openFor", None)
    ui.wait_idle()
    _popup(ui, "itCode").setProperty("text", "ПЛК-ВВ")
    _popup(ui, "itName").setProperty("text", "Модуль ввода-вывода")
    _popup(ui, "itUnit").setProperty("text", "шт")
    ui.wait_idle()
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert not dlg.property("visible")
    assert _item(ui, "ПЛК-ВВ")["pending"] is True and ui.backend.client.pending_count() == 1
    ui.transport.online = True
    ui.backend.syncNow()
    ui.wait_idle()
    assert _item(ui, "ПЛК-ВВ")["pending"] is False


def test_procurement_generates_need_and_warehouse_reserves_line(ui):
    ui.login("s.morozov")
    assert ui.page() == "stock"                                 # закупка тоже видит склад (право stock.read)
    ui.go("procurement")
    pp = next(i for i in ui.items("nv") if i.isVisible())
    nv = _v(pp, "nv")
    assert nv["can_generate"] is True and len(nv["candidates"]) == 1
    btn = ui.find("btnGenerate")
    assert btn.isVisible() and btn.property("enabled")
    dlg = _popup(ui, "genForm")
    QMetaObject.invokeMethod(dlg, "open")
    ui.wait_idle()
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert not dlg.property("visible")
    nv = _v(pp, "nv")
    assert len(nv["needs"]) == 2 and nv["candidates"] == [] and pp.property("selectedId") != ""
    assert btn.property("enabled") is False                     # кандидатов больше нет
    # Строка ШУ-1 по АТХ: нужно 3, в резерве 2, на складе свободного нет → «Нужна закупка».
    atx = next(n for n in nv["needs"] if n["kit_code"] == "АТХ")
    shu = next(ln for ln in atx["lines"] if ln["code"] == "ШУ-1")
    assert shu["state_label"] == "Нужна закупка" and shu["open_label"] == "1 шт"
    assert any(ln["state_label"] == "Не сопоставлена" and "Нет обозначения" in (ln["match_note"] or "") for ln in atx["lines"])

    # Склад принимает ещё один шкаф и резервирует его под строку — строка становится «Покрыта резервом».
    ui.backend.logout()
    ui.wait_idle()
    ui.login("i.belova")
    rf = _popup(ui, "receiveForm")
    _call(rf, "openFor", _item(ui, "ШУ-1"))
    ui.wait_idle()
    _popup(ui, "rQty").setProperty("text", "1")
    _popup(ui, "rSrc").setProperty("text", "ТН № 501")
    ui.wait_idle()
    QMetaObject.invokeMethod(rf, "confirmed")
    ui.wait_idle()
    ui.go("procurement")
    pp = next(i for i in ui.items("nv") if i.isVisible())
    nv = _v(pp, "nv")
    assert nv["can_reserve"] is True and nv["can_generate"] is False
    atx = next(n for n in nv["needs"] if n["kit_code"] == "АТХ")
    shu = next(ln for ln in atx["lines"] if ln["code"] == "ШУ-1")
    assert shu["state_label"] == "Есть на складе"
    pp.setProperty("selectedId", atx["id"])
    ui.wait_idle()
    lr = _popup(ui, "lineReserve")
    _call(lr, "openFor", shu)
    ui.wait_idle()
    assert lr.property("canConfirm") is False                    # основание обязательно
    _popup(ui, "lrReason").setProperty("text", "Под монтаж ШУ")
    ui.wait_idle()
    QMetaObject.invokeMethod(lr, "confirmed")
    ui.wait_idle()
    assert not lr.property("visible") and lr.property("error") == ""
    atx = next(n for n in _v(pp, "nv")["needs"] if n["kit_code"] == "АТХ")
    shu = next(ln for ln in atx["lines"] if ln["code"] == "ШУ-1")
    assert shu["state_label"] == "Покрыта резервом" and atx["open_count"] == 0


def test_gip_sees_need_without_stock_numbers(ui):
    ui.login("p.volkov")
    nav = ui.nav()
    assert not nav["Склад"] and nav["Закупка"]
    ui.go("procurement")
    pp = next(i for i in ui.items("nv") if i.isVisible())
    nv = _v(pp, "nv")
    assert nv["sees_stock"] is False and nv["can_generate"] is False and nv["can_reserve"] is False
    lines = nv["needs"][0]["lines"]
    matched = [ln for ln in lines if ln.get("item_id")]      # null-ключи при конвертации из QML пропадают
    assert matched and all(ln["available_label"] == "неизвестно" for ln in matched)
    assert not ui.find("btnGenerate").isVisible()


def test_engineer_has_no_supply_section(ui):
    ui.login("d.kuznetsov")
    nav = ui.nav()
    assert not nav["Склад"] and not nav["Закупка"]


def test_procurement_matches_uncoded_line_by_hand(ui):
    """Кабель в расчёте АТХ записан без обозначения → «Не сопоставлена»; закупка сопоставляет его вручную (A-25)."""
    ui.login("s.morozov")
    ui.go("procurement")
    pp = next(i for i in ui.items("nv") if i.isVisible())
    nv = _v(pp, "nv")
    assert nv["can_match"] is True
    atx = next(n for n in nv["needs"] if n["kit_code"] == "АТХ")
    cable = next(ln for ln in atx["lines"] if ln["name"].startswith("Кабель контрольный"))
    assert cable["state_label"] == "Не сопоставлена" and cable["can_rematch"] is True
    pp.setProperty("selectedId", atx["id"])
    ui.wait_idle()
    dlg = _popup(ui, "lineMatch")
    _call(dlg, "openFor", cable)
    ui.wait_idle()
    cands = _v(dlg, "candidates")
    # Кандидаты — только позиции в метрах (единица строки): пересчёт единиц не выполняется.
    assert [c["label"] for c in cands] == ["КВВГнг-7x1.5 · Кабель контрольный КВВГнг(А)-LS 7×1,5"]
    assert dlg.property("canConfirm") is False                  # причина обязательна
    _popup(ui, "lmReason").setProperty("text", "Тот же кабель, в расчёте без обозначения")
    ui.wait_idle()
    assert dlg.property("canConfirm") is True
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert not dlg.property("visible") and dlg.property("error") == ""
    atx = next(n for n in _v(pp, "nv")["needs"] if n["kit_code"] == "АТХ")
    cable = next(ln for ln in atx["lines"] if ln["name"].startswith("Кабель контрольный"))
    assert cable["match_source"] == "manual" and cable["state_label"] == "Нужна закупка"
    assert cable["match_label"] == "Сопоставлена вручную: Тот же кабель, в расчёте без обозначения"
    # Без связи кнопки сопоставления нет (команда только онлайн).
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    assert _v(pp, "nv")["can_match"] is False


def test_directory_tab_and_match_with_unit_conversion(ui):
    """Закупка заводит позицию «в бухтах» и пересчёт «м → бухта», затем сопоставляет кабель с пересчётом (A-27)."""
    ui.login("s.morozov")
    ui.go("stock")
    page = _stock_page(ui)
    page.setProperty("tab", "directory")
    ui.wait_idle()
    dv = _v(page, "dv")
    assert dv["can_edit"] is True
    assert [c["rule_label"] for c in dv["conversions"]] == ["1 км = 1 000 м"]           # из демо-данных
    assert [a["alias"] for a in dv["aliases"]] == ["ХС-200"]
    ui.backend.execute("t-item", "item.create", {"id": ui.backend.newId(), "code": "КВВГнг-7x1.5-Б",
                                                 "name": "Кабель КВВГнг 7×1,5 (бухта 100 м)", "unit": "бухта"}, 0)
    ui.wait_idle()
    dlg = _popup(ui, "convForm")
    _call(dlg, "openFor", None)
    ui.wait_idle()
    for name, text in (("cvFrom", "м"), ("cvTo", "бухта"), ("cvFactor", "0,01")):
        _popup(ui, name).setProperty("text", text)
    ui.wait_idle()
    assert dlg.property("canConfirm") is True
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert dlg.property("error") == "" and not dlg.property("visible")
    assert "1 м = 0,01 бухта" in [c["rule_label"] for c in _v(page, "dv")["conversions"]]
    # Закупка: у кабеля (1850 м) теперь два кандидата — та же единица и «бухта» с пересчётом 18,5 бухты.
    ui.go("procurement")
    pp = next(i for i in ui.items("nv") if i.isVisible())
    atx = next(n for n in _v(pp, "nv")["needs"] if n["kit_code"] == "АТХ")
    cable = next(ln for ln in atx["lines"] if ln["name"].startswith("Кабель контрольный"))
    pp.setProperty("selectedId", atx["id"])
    ui.wait_idle()
    m = _popup(ui, "lineMatch")
    _call(m, "openFor", cable)
    ui.wait_idle()
    cands = _v(m, "candidates")
    idx = next(i for i, c in enumerate(cands) if c["conversion_id"])
    assert cands[idx]["item_qty_label"] == "18,5 бухта" and cands[idx]["problem"] == ""
    _popup(ui, "lmItem").setProperty("currentIndex", idx)
    _popup(ui, "lmReason").setProperty("text", "Поставляется бухтами по 100 м")
    ui.wait_idle()
    assert "18,5 бухта" in _popup(ui, "lmConversion").property("text")
    assert m.property("confirmText") == "Сопоставить с пересчётом"
    QMetaObject.invokeMethod(m, "confirmed")
    ui.wait_idle()
    assert m.property("error") == "" and not m.property("visible")
    atx = next(n for n in _v(pp, "nv")["needs"] if n["kit_code"] == "АТХ")
    cable = next(ln for ln in atx["lines"] if ln["name"].startswith("Кабель контрольный"))
    assert cable["item_qty_label"] == "18,5 бухта" and cable["open_label"] == "18,5 бухта"
    assert "пересчёт 1 м = 0,01 бухта" in cable["match_label"]


def test_admin_publishes_company_setting(ui):
    """Администратор: вкладка «Настройки компании», публикация «Пересопоставление при резервах» (D-26, A-26)."""
    ui.login("admin")
    ui.go("admin")
    page = next(i for i in ui.items("sv") if i.isVisible())
    page.setProperty("tab", "settings")
    ui.wait_idle()
    sv = _v(page, "sv")
    item = next(x for x in sv["items"] if x["key"] == "procurement.rematch_reserved_line")
    assert sv["can_publish"] is True and item["is_set"] is False and item["value"] == "forbid"
    dlg = _popup(ui, "publishForm")
    _call(dlg, "openFor", item)
    ui.wait_idle()
    _popup(ui, "pValue").setProperty("currentIndex", [o["id"] for o in item["options"]].index("release_active"))
    _popup(ui, "pReason").setProperty("text", "Решение совещания склада и закупки")
    ui.wait_idle()
    assert dlg.property("canConfirm") is True
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert dlg.property("error") == "" and not dlg.property("visible")
    item = next(x for x in _v(page, "sv")["items"] if x["key"] == "procurement.rematch_reserved_line")
    assert (item["value"], item["is_set"], item["version"]) == ("release_active", True, 1)
    assert item["reason"] == "Решение совещания склада и закупки"


def test_transfer_through_form_moves_between_warehouses_without_storno(ui):
    ui.login("i.belova")
    page = _stock_page(ui)
    nshvi = _item(ui, "НШВИ-1.5")
    assert [(b["warehouse_code"], b["available_label"]) for b in nshvi["by_warehouse"]] == [("СК-1", "800 шт"), ("СК-2", "200 шт")]
    demo_legs = [m for m in _v(page, "ov")["movements"] if m["is_transfer"]]
    assert {m["transfer_label"] for m in demo_legs} == {"СК-1 → СК-2"} and len(demo_legs) == 2
    dlg = _popup(ui, "transferForm")
    _call(dlg, "openFor", nshvi)
    ui.wait_idle()
    # Откуда — только склады с доступным; куда — любой другой действующий склад.
    assert [o["label"] for o in _v(dlg, "fromOptions")] == ["СК-1 — доступно 800 шт", "СК-2 — доступно 200 шт"]
    assert [o["label"].split(" · ")[0] for o in _v(dlg, "toOptions")] == ["СК-2"]
    _popup(ui, "tQty").setProperty("text", "50")
    ui.wait_idle()
    assert dlg.property("canConfirm") is False                   # без причины — нельзя
    _popup(ui, "tReason").setProperty("text", "Бригаде Петрова")
    ui.wait_idle()
    assert dlg.property("canConfirm") is True
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert not dlg.property("visible") and dlg.property("error") == ""
    nshvi = _item(ui, "НШВИ-1.5")
    assert [b["available_label"] for b in nshvi["by_warehouse"]] == ["750 шт", "250 шт"]
    assert nshvi["available_label"] == "1 000 шт"                 # общий запас не изменился
    mv = _v(page, "ov")["movements"]
    assert (mv[0]["kind_label"], mv[1]["kind_label"]) == ("Приход по перемещению", "Расход по перемещению")
    # Без связи перемещать нельзя (online_required).
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    assert _v(page, "ov")["can_transfer"] is False and ui.find("btnTransfer").property("enabled") is False
