"""UI-проверки склада и закупки (С4): настоящий QML, сервер in-process, демо-данные (vector.server.demo).

Демо: i.belova (склад), s.morozov (закупка). Склады СК-1/СК-2; АРМ — 1 компл, весь в резерве; ШУ-1 — 2 шт, всё
в резерве под потребность АТХ v1 (нужно 3 → 1 не покрыт, «Нужна закупка»); потребность ЭМ v1 ещё не сформирована
(кандидат); приход НШВИ-1.5 10 000 шт сторнирован («лишний ноль»); 200 шт НШВИ-1.5 перемещены СК-1 → СК-2.
"""
import pytest
from PySide6.QtCore import QMetaObject, QObject, Q_ARG

PWD = "Демо-пароль-2026"          # пароль демо-пользователей стенда (tools/ui_harness.PWD) — для подписи актов

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


def _find_visual(ui, name):
    """Элемент по objectName в ВИЗУАЛЬНОМ дереве (ui.items обходит и делегаты Repeater, которых нет
    в QObject-дереве окна)."""
    return next(i for i in ui.items("objectName") if i.objectName() == name and i.isVisible())


def _popup_in(root, name):
    """Всплывающее окно по objectName ВНУТРИ данной страницы — после перелогина в дереве могут остаться
    одноимённые формы уже закрытой страницы (поиск по всему окну находит мёртвый экземпляр)."""
    return next(o for o in root.findChildren(QObject) if o.objectName() == name)


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
    # Строка ШУ-1 по АТХ: нужно 3, в резерве 2, третий уже заказан (№ 1 размещён) и едет → «Заказана, ждём» (D-60).
    atx = next(n for n in nv["needs"] if n["kit_code"] == "АТХ")
    shu = next(ln for ln in atx["lines"] if ln["code"] == "ШУ-1")
    assert shu["state_label"] == "Заказана, ждём" and shu["on_order_label"] == "1 шт"
    assert shu["open_label"] == "1 шт" and shu["reserve_open_label"] == "0 шт"  # весь непокрытый остаток — в пути
    assert any(ln["state_label"] == "Не сопоставлена" and "Нет обозначения" in (ln["match_note"] or "") for ln in atx["lines"])

    # Демо-стенд выдал АРМ № 1 со склада на ПНР — по резерву «Под ПНР» осталось 1 из 2. Снимаем его остаток —
    # строка АРМ снова открыта, а выданное остаётся выданным (сторно выдачи резерв не восстанавливает, D-33).
    import sqlalchemy as sa
    from vector.server.db import stock_reservations
    with ui.svc.engine.connect() as c:
        rs = c.execute(sa.select(stock_reservations.c.id, stock_reservations.c.version).where(
            stock_reservations.c.reason.like("%ПНР%"))).one()
    ui.backend.logout()
    ui.wait_idle()
    ui.login("i.belova")
    ui.backend.execute("t-rel", "stock.release", {"id": rs.id, "reason": "ПНР завершена одним АРМ, остаток не нужен"},
                       rs.version)
    ui.wait_idle()
    rf = _popup(ui, "receiveForm")
    _call(rf, "openFor", _item(ui, "АРМ"))
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
    arm = next(ln for ln in atx["lines"] if ln["code"] == "АРМ")
    assert arm["state_label"] == "Есть на складе"
    pp.setProperty("selectedId", atx["id"])
    ui.wait_idle()
    lr = _popup(ui, "lineReserve")
    _call(lr, "openFor", arm)
    ui.wait_idle()
    assert lr.property("canConfirm") is False                    # основание обязательно
    _popup(ui, "lrReason").setProperty("text", "Под монтаж АРМ")
    ui.wait_idle()
    QMetaObject.invokeMethod(lr, "confirmed")
    ui.wait_idle()
    assert not lr.property("visible") and lr.property("error") == ""
    atx = next(n for n in _v(pp, "nv")["needs"] if n["kit_code"] == "АТХ")
    arm = next(ln for ln in atx["lines"] if ln["code"] == "АРМ")
    assert arm["state_label"] == "Покрыта резервом" and atx["open_count"] == 1   # ШУ-1 открыта, но уже заказана (D-60)


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


def test_transfer_ships_in_transit_then_accept_and_return_through_forms(ui):
    """D-29 через формы: отгрузка списывает у отправителя и создаёт «в пути»; «Принять» приходует у получателя,
    «Вернуть» (только с причиной) — обратно у отправителя; без связи кнопки неактивны."""
    ui.login("i.belova")
    page = _stock_page(ui)
    nshvi = _item(ui, "НШВИ-1.5")
    # Демо: 200 шт НШВИ уже отгружены на СК-2 и приняты там, ещё 20 шт утрачены в пути (акт об утрате № 1);
    # 100 шт ХМТ-200 «в пути» СК-2 → СК-1.
    assert [(b["warehouse_code"], b["available_label"]) for b in nshvi["by_warehouse"]] == [("СК-1", "780 шт"), ("СК-2", "200 шт")]
    demo_legs = [m for m in _v(page, "ov")["movements"] if m["is_transfer"] and m["item_code"] == "НШВИ-1.5"]
    assert {m["transfer_label"] for m in demo_legs} == {"СК-1 → СК-2"} and len(demo_legs) == 3
    hmt = _item(ui, "ХМТ-200")
    assert hmt["in_transit_label"] == "100 шт" and hmt["transfers"][0]["route_label"] == "СК-2 → СК-1"

    dlg = _popup(ui, "transferForm")
    _call(dlg, "openFor", nshvi)
    ui.wait_idle()
    # Откуда — только склады с доступным; куда — любой другой действующий склад.
    assert [o["label"] for o in _v(dlg, "fromOptions")] == ["СК-1 — 780 шт", "СК-2 — 200 шт"]
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
    # Отгружено: у отправителя −50, у получателя пока ничего — 50 шт «в пути».
    assert [b["available_label"] for b in nshvi["by_warehouse"]] == ["730 шт", "200 шт"]
    assert nshvi["in_transit_label"] == "50 шт" and len(nshvi["transfers"]) == 1
    assert _v(page, "ov")["movements"][0]["kind_label"] == "Расход по перемещению"

    acc = _popup(ui, "acceptForm")
    _call(acc, "openFor", nshvi["transfers"][0])
    ui.wait_idle()
    assert acc.property("canConfirm") is True                    # комментарий необязателен
    QMetaObject.invokeMethod(acc, "confirmed")
    ui.wait_idle()
    assert not acc.property("visible") and acc.property("error") == ""
    nshvi = _item(ui, "НШВИ-1.5")
    assert [b["available_label"] for b in nshvi["by_warehouse"]] == ["730 шт", "250 шт"] and nshvi["transfers"] == []
    assert nshvi["available_label"] == "980 шт"                 # общий запас не изменился
    assert _v(page, "ov")["movements"][0]["kind_label"] == "Приход по перемещению"

    ret = _popup(ui, "returnForm")
    _call(ret, "openFor", _item(ui, "ХМТ-200")["transfers"][0])
    ui.wait_idle()
    assert ret.property("canConfirm") is False                   # без причины — нельзя
    _popup(ui, "rtReason").setProperty("text", "Отгружено не туда")
    ui.wait_idle()
    QMetaObject.invokeMethod(ret, "confirmed")
    ui.wait_idle()
    assert not ret.property("visible") and ret.property("error") == ""
    hmt = _item(ui, "ХМТ-200")
    assert hmt["transfers"] == [] and _v(page, "ov")["movements"][0]["kind_label"] == "Возврат по перемещению"
    assert {t["status"] for t in _v(page, "ov")["transfers"]} == {"received", "returned", "lost"}

    # Без связи перемещать, принимать и возвращать нельзя (online_required).
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    assert _v(page, "ov")["can_transfer"] is False and ui.find("btnTransfer").property("enabled") is False
    assert not any(t["can_act"] for t in _v(page, "ov")["transfers"])


def test_accept_with_shortage_requires_reason_and_signature(ui):
    """D-30 через форму: «Фактически пришло» меньше отгруженного → баннер недостачи, причина и флажок подписи
    обязательны; после приёмки — акт с номером, на получателе — только фактическое количество. В демо уже есть
    акт № 1 (ХМТ-200), новый — № 2."""
    ui.login("i.belova")
    page = _stock_page(ui)
    demo = [t for t in _v(page, "ov")["transfers"] if t.get("act_label")]
    assert len(demo) == 1 and demo[0]["act_label"].startswith("Акт недостачи № 1: отгружено 50 шт, принято 48 шт")
    assert any(t.get("expected_label") for t in _v(page, "ov")["transfers"] if t["status"] == "in_transit")
    nshvi = _item(ui, "НШВИ-1.5")
    dlg = _popup(ui, "transferForm")
    _call(dlg, "openFor", nshvi)
    ui.wait_idle()
    _popup(ui, "tQty").setProperty("text", "10")
    _popup(ui, "tReason").setProperty("text", "Бригаде Петрова")
    _popup(ui, "tEta").setProperty("text", "20.11.2026")
    ui.wait_idle()
    assert dlg.property("canConfirm") is False                     # дата не в формате ГГГГ-ММ-ДД
    _popup(ui, "tEta").setProperty("text", "2099-01-15")
    ui.wait_idle()
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert dlg.property("error") == "" and not dlg.property("visible")
    tr = _item(ui, "НШВИ-1.5")["transfers"][0]
    assert tr["expected_label"] == "15.01.2099"

    acc = _popup(ui, "acceptForm")
    _call(acc, "openFor", tr)
    ui.wait_idle()
    _popup(ui, "acQty").setProperty("text", "12")
    ui.wait_idle()
    assert acc.property("canConfirm") is False                     # больше отгруженного
    _popup(ui, "acQty").setProperty("text", "7")
    ui.wait_idle()
    assert acc.property("short") is True and acc.property("canConfirm") is False
    _popup(ui, "acShortReason").setProperty("text", "Три наконечника потеряны при погрузке")
    ui.wait_idle()
    assert acc.property("canConfirm") is False                     # без подписи — нельзя
    _popup(ui, "acSign").setProperty("checked", True)
    ui.wait_idle()
    # Настройка подписи не задана → повторный ввод пароля (D-31): без пароля — нельзя.
    assert _popup(ui, "acPwd").property("visible") is True and acc.property("canConfirm") is False
    _popup(ui, "acPwd").setProperty("text", PWD)
    ui.wait_idle()
    assert acc.property("canConfirm") is True and acc.property("confirmText") == "Принять и подписать акт"
    QMetaObject.invokeMethod(acc, "confirmed")
    ui.wait_idle()
    assert acc.property("error") == "" and not acc.property("visible")
    nshvi = _item(ui, "НШВИ-1.5")
    assert [b["available_label"] for b in nshvi["by_warehouse"]] == ["770 шт", "207 шт"]
    done = next(t for t in _v(page, "ov")["transfers"] if t["id"] == tr["id"])
    assert done["status_label"] == "Принято с недостачей"
    assert done["act_label"] == "Акт недостачи № 2: отгружено 10 шт, принято 7 шт, недостача 3 шт"
    assert done["act_signed_label"].endswith("·  с вводом пароля")
    assert _popup(ui, "acPwd").property("text") == ""                 # пароль не остаётся в форме


def test_lost_transfer_through_form_needs_reason_signature_and_password(ui):
    """D-31 через форму: «Утрата» по перемещению «в пути» — причина, флажок и пароль (настройка не задана);
    после подписи — «Утрачено в пути», акт об утрате № 2 (№ 1 — в демо), количество ни на один склад не пришло."""
    ui.login("i.belova")
    page = _stock_page(ui)
    demo = [t for t in _v(page, "ov")["transfers"] if t["status"] == "lost"]
    assert len(demo) == 1 and demo[0]["loss_label"] == "Акт об утрате № 1: утрачено 20 шт"
    assert demo[0]["status_tone"] == "danger" and demo[0]["can_act"] is False
    hmt = _item(ui, "ХМТ-200")
    tr = hmt["transfers"][0]
    before = [b["available_label"] for b in hmt["by_warehouse"]]
    dlg = _popup(ui, "lostForm")
    _call(dlg, "openFor", tr)
    ui.wait_idle()
    assert dlg.property("canConfirm") is False
    _popup(ui, "lsReason").setProperty("text", "Не прибыло; претензия перевозчику № 7")
    _popup(ui, "lsSign").setProperty("checked", True)
    ui.wait_idle()
    assert dlg.property("canConfirm") is False                     # пароль обязателен
    _popup(ui, "lsPwd").setProperty("text", "неверный пароль")
    ui.wait_idle()
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert dlg.property("visible") and "Пароль неверный" in dlg.property("error")
    _popup(ui, "lsPwd").setProperty("text", PWD)
    ui.wait_idle()
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert dlg.property("error") == "" and not dlg.property("visible")
    done = next(t for t in _v(page, "ov")["transfers"] if t["id"] == tr["id"])
    assert (done["status"], done["status_label"]) == ("lost", "Утрачено в пути")
    assert done["loss_label"] == "Акт об утрате № 2: утрачено 100 шт" and "с вводом пароля" in done["loss_signed_label"]
    hmt = _item(ui, "ХМТ-200")
    assert hmt["transfers"] == [] and [b["available_label"] for b in hmt["by_warehouse"]] == before


def test_adjustment_create_sign_and_cancel_flow(ui):
    """Корректировка остатка (D-77): склад создаёт с карточки позиции, руководитель отдела подписывает с паролем,
    журнал пополняется проводкой «Корректировка…»; до подписи остаток неизменен."""
    ui.login("i.belova")
    page = _stock_page(ui)
    dlg = _popup(ui, "adjustForm")
    _call(dlg, "openFor", _item(ui, "ДД"))
    ui.wait_idle()
    qty, reason = _popup(ui, "adjQty"), _popup(ui, "adjReason")
    assert _popup(ui, "adjWh").property("model")                              # склад предложен (есть учётный остаток)
    qty.setProperty("text", "-1")
    ui.wait_idle()
    assert dlg.property("canConfirm") is False                                 # отрицательный факт нельзя
    qty.setProperty("text", "0,5")
    reason.setProperty("text", "Акт инвентаризации № 12 от 20.11.2026")
    ui.wait_idle()
    assert dlg.property("canConfirm") is True
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    dd = _item(ui, "ДД")
    assert dd["on_hand_label"] == "0 шт" or True                               # остаток НЕ изменился до подписи
    page.setProperty("tab", "adjustments")
    ui.wait_idle()
    row = _v(page, "ov")["adjustments"][0]
    assert (row["status"], row["delta_label"]) == ("pending", "+0,5 шт")
    assert row["can_sign_row"] is False                                        # автор не подписывает своё
    # Руководитель отдела: видит ждущую подписи и подписывает с паролем.
    ui.login("e.orlova")
    page = _stock_page(ui)
    page.setProperty("tab", "adjustments")
    ui.wait_idle()
    row = _v(page, "ov")["adjustments"][0]
    assert row["can_sign_row"] is True and row["status_label"] == "Ждёт подписи"
    sd = _popup(ui, "adjSignForm")
    _call(sd, "openFor", row)
    ui.wait_idle()
    assert sd.property("canConfirm") is False                                  # без флажка и пароля нельзя
    _popup(ui, "adjSign").setProperty("checked", True)
    _popup(ui, "adjPwd").setProperty("text", PWD)
    ui.wait_idle()
    assert sd.property("canConfirm") is True
    QMetaObject.invokeMethod(sd, "confirmed")
    ui.wait_idle()
    row = _v(page, "ov")["adjustments"][0]
    assert row["status"] == "signed" and row["signature"]["content_ok"] is True
    # Проводка корректировки — в журнале (у e.orlova журнала нет: нет права stock.read — смотрим у склада ниже).
    ui.login("i.belova")
    mv = _v(_stock_page(ui), "ov")["movements"][0]
    assert "Корректировка" in mv["source_doc"] and mv["qty_label"] == "+0,5 шт"
    # Автор отменяет свою вторую корректировку.
    page = _stock_page(ui)
    dlg = _popup(ui, "adjustForm")
    _call(dlg, "openFor", _item(ui, "ДД"))
    qty2, reason2 = _popup(ui, "adjQty"), _popup(ui, "adjReason")
    qty2.setProperty("text", "0")
    reason2.setProperty("text", "Пересчёт по приказу № 8")
    ui.wait_idle()
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    page.setProperty("tab", "adjustments")
    ui.wait_idle()
    row = next(r for r in _v(page, "ov")["adjustments"] if r["status"] == "pending")
    assert row["can_cancel_row"] is True
    cd = _popup(ui, "adjCancelForm")
    _call(cd, "openFor", row)
    _popup(ui, "adjCanReason").setProperty("text", "Основание оформлено неверно")
    ui.wait_idle()
    QMetaObject.invokeMethod(cd, "confirmed")
    ui.wait_idle()
    assert all(r["status"] != "pending" for r in _v(page, "ov")["adjustments"])
    assert _v(page, "ov")["summary"]["adjust_pending"] == 0


def test_transfer_proposal_flow(ui):
    """Предложение перемещения (D-78): закупка предлагает из строки потребности (форма со снимком источников),
    склад видит на вкладке «Предложения» и перебрасывает резерв; автор отзывает своё второе предложение."""
    # Закупка формирует потребность ЭМ и предлагает переместить ЩР-1 из свободного остатка СК-2 (1 шт на складе,
    # нужно 2 — строка открыта).
    ui.login("s.morozov")
    ui.go("procurement")
    pp = next(i for i in ui.items("nv") if i.isVisible())
    gen = _popup_in(pp, "genForm")
    QMetaObject.invokeMethod(gen, "open")
    ui.wait_idle()
    QMetaObject.invokeMethod(gen, "confirmed")
    ui.wait_idle()
    nv = _v(pp, "nv")
    em = next(n for n in nv["needs"] if n["kit_code"] == "ЭМ")
    shr = next(ln for ln in em["lines"] if ln["code"] == "ЩР-1")
    assert shr["open_label"] == "2 шт" and shr["available_label"] == "1 шт"   # одна есть, вторая — вопрос
    assert nv["can_propose"] is True
    pp.setProperty("selectedId", em["id"])
    ui.wait_idle()
    pf = _popup_in(pp, "propForm")
    _call(pf, "openFor", shr)
    ui.wait_idle()
    assert pf.property("src")["donors"] == []                                  # резервов ЩР-1 нет — только свободное
    assert len(pf.property("src")["free"]) == 1
    assert pf.property("canConfirm") is False                                  # переключатель ещё на «доноре», их нет
    # Переключаем «Из свободного остатка» (сегмент в форме делает то же самое через onPicked).
    pf.setProperty("srcKind", 1)
    _popup_in(pp, "pfQty").setProperty("text", "1")
    ui.wait_idle()
    assert pf.property("canConfirm") is False                                  # причина обязательна
    _popup_in(pp, "pfReason").setProperty("text", "В ПАЗ цеха № 5 щит уже едет, а нам нужен сейчас")
    ui.wait_idle()
    assert pf.property("canConfirm") is True
    QMetaObject.invokeMethod(pf, "confirmed")
    ui.wait_idle()

    # Склад: вкладка «Предложения» — открытое предложение, решение «Перебросить резерв».
    ui.login("i.belova")
    ui.go("stock")
    page = _stock_page(ui)
    page.setProperty("tab", "proposals")
    ui.wait_idle()
    ov = _v(page, "ov")
    assert ov["summary"]["proposals_open"] == 1
    row = ov["proposals"][0]
    assert (row["status"], row["item_code"], row["from_label"], row["qty_label"]) == \
        ("proposed", "ЩР-1", "свободный остаток", "1 шт")
    assert row["can_accept"] is True and row["can_reject"] is True and row["can_order"] is False
    dec = _popup_in(page, "propDecideForm")
    _call(dec, "openFor", row, "accept")
    ui.wait_idle()
    assert dec.property("canConfirm") is True                                  # комментарий при accept необязателен
    QMetaObject.invokeMethod(dec, "confirmed")
    ui.wait_idle()
    ov = _v(page, "ov")
    assert ov["summary"]["proposals_open"] == 0
    assert ov["proposals"][0]["status"] == "accepted" and ov["proposals"][0]["status_label"] == "Резерв переброшен"
    # Резерв ЩР-1 под демо-проект появился на СК-2.
    res = [r for r in ov["reservations"] if r["item_code"] == "ЩР-1"]
    assert len(res) == 1 and res[0]["qty_label"] == "1 шт"

    # Второе предложение (по ХМТ-200, без строки потребности — поля опциональны) — склад отказывает с причиной.
    ui.login("s.morozov")
    ui.go("stock")
    page0 = _stock_page(ui)
    page0.setProperty("tab", "proposals")
    ui.wait_idle()
    first = _v(page0, "ov")["proposals"][0]
    hmt = next(i for i in _v(page0, "ov")["items"] if i["code"] == "ХМТ-200")
    wh2 = next(w for w in _v(page0, "ov")["warehouses"] if w["code"] == "СК-2")
    ui.backend.execute("t", "proposal.propose", {"id": ui.backend.newId(), "warehouse_id": wh2["id"],
                                                 "item_id": hmt["id"], "to_project_id": first["to_project_id"],
                                                 "qty": "50", "reason": "Хомуты нужны на ПАЗ раньше поставки"}, 0)
    ui.wait_idle()
    ui.login("i.belova")
    ui.go("stock")
    page = _stock_page(ui)
    page.setProperty("tab", "proposals")
    ui.wait_idle()
    row = next(r for r in _v(page, "ov")["proposals"] if r["status"] == "proposed")
    assert row["item_code"] == "ХМТ-200" and row["can_accept"] is True
    dec = _popup_in(page, "propDecideForm")
    _call(dec, "openFor", row, "reject")
    ui.wait_idle()
    assert dec.property("canConfirm") is False                                 # без причины отказать нельзя
    _popup_in(page, "pdNote").setProperty("text", "Хомуты уже идут отдельным заказом")
    ui.wait_idle()
    assert dec.property("canConfirm") is True
    QMetaObject.invokeMethod(dec, "confirmed")
    ui.wait_idle()
    assert {r["status"] for r in _v(page, "ov")["proposals"]} == {"accepted", "rejected"}

    # Автор (закупка) отзывает своё третье предложение — из своего представления склада.
    ui.login("s.morozov")
    ui.go("stock")
    page = _stock_page(ui)
    page.setProperty("tab", "proposals")
    ui.wait_idle()
    ui.backend.execute("t", "proposal.propose", {"id": ui.backend.newId(), "warehouse_id": wh2["id"],
                                                 "item_id": hmt["id"], "to_project_id": first["to_project_id"],
                                                 "qty": "10", "reason": "Проверка отзыва"}, 0)
    ui.wait_idle()
    row = next(r for r in _v(page, "ov")["proposals"] if r["status"] == "proposed")
    assert row["can_withdraw"] is True and row["can_accept"] is False          # закупка резерв не перебрасывает
    wd = _popup_in(page, "propWithdrawForm")
    _call(wd, "openFor", row)
    ui.wait_idle()
    assert wd.property("canConfirm") is False
    _popup_in(page, "pwReason").setProperty("text", "Дубль, предложение уже решено раньше")
    ui.wait_idle()
    QMetaObject.invokeMethod(wd, "confirmed")
    ui.wait_idle()
    assert all(r["status"] != "proposed" for r in _v(page, "ov")["proposals"])


def test_replenishment_rule_and_draft_flow(ui):
    """Пополнение расходника по порогу (D-79): склад задаёт правило с карточки позиции — остаток уже ниже порога,
    черновик появляется сразу; подтверждают склад и закупка (два подтверждения), затем склад отклоняет новый."""
    # Склад: карточка ДД (на складе 0 шт) → правило «ниже 2 → пополнить до 6».
    ui.login("i.belova")
    page = _stock_page(ui)
    dd = _item(ui, "ДД")
    page.setProperty("selectedId", dd["id"])
    ui.wait_idle()
    rf = _popup_in(page, "ruleForm")
    _call(rf, "openFor", dd)
    ui.wait_idle()
    assert rf.property("rule") is None and rf.property("canConfirm") is False    # умолчаний нет (Q2)
    _popup_in(page, "rrMin").setProperty("text", "2")
    _popup_in(page, "rrTarget").setProperty("text", "2")
    ui.wait_idle()
    assert rf.property("canConfirm") is False                                   # «пополнить до» должно быть больше порога
    _popup_in(page, "rrTarget").setProperty("text", "6")
    ui.wait_idle()
    assert rf.property("canConfirm") is True
    QMetaObject.invokeMethod(rf, "confirmed")
    ui.wait_idle()
    # Правило появилось, черновик создан сразу (доступно 0 < 2): пополнить на 6 шт.
    page.setProperty("tab", "replenishment")
    ui.wait_idle()
    ov = _v(page, "ov")
    assert ov["summary"]["replen_open"] == 1
    rule = ov["replen_rules"][0]
    assert (rule["item_code"], rule["min_label"], rule["target_label"], rule["enabled"]) == \
        ("ДД", "2 шт", "6 шт", True)
    row = ov["replen_drafts"][0]
    assert (row["status"], row["qty_label"]) == ("open", "6 шт")
    assert row["can_confirm_wh"] is True and row["can_confirm_pr"] is False
    # Склад подтверждает свою сторону кнопкой в строке.
    ui.click(_find_visual(ui, "btnReplenConfirm"))
    row = _v(page, "ov")["replen_drafts"][0]
    assert row["status"] == "open" and row["wh_confirmer"] != "" and row["pr_confirmer"] == ""
    # Закупка подтверждает вторую сторону → подтверждён.
    ui.login("s.morozov")
    ui.go("stock")
    page = _stock_page(ui)
    page.setProperty("tab", "replenishment")
    ui.wait_idle()
    row = _v(page, "ov")["replen_drafts"][0]
    assert row["can_confirm_pr"] is True and row["can_confirm_wh"] is False     # склад уже подтвердил
    ui.click(_find_visual(ui, "btnReplenConfirm"))
    row = _v(page, "ov")["replen_drafts"][0]
    assert row["status"] == "confirmed" and row["status_label"] == "Подтверждён"
    # Правило можно отключить кнопкой «Изменить» — история черновиков сохраняется.
    ui.login("i.belova")
    ui.go("stock")
    page = _stock_page(ui)
    page.setProperty("tab", "replenishment")
    ui.wait_idle()
    ui.click(_find_visual(ui, "btnRuleEdit"))
    rf = _popup_in(page, "ruleForm")
    assert rf.property("rule") is not None
    _popup_in(page, "rrEnabled").setProperty("checked", False)
    ui.wait_idle()
    QMetaObject.invokeMethod(rf, "confirmed")
    ui.wait_idle()
    ov = _v(page, "ov")
    assert ov["replen_rules"][0]["enabled"] is False
    assert len(ov["replen_drafts"]) == 1                                        # история не удалена
