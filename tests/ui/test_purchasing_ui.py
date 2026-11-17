"""UI-проверки заказов поставщикам и факта поставки (С4, шаг 5; D-32): настоящий QML, сервер in-process, демо-данные.

Демо (vector.server.demo): поставщики ШКАФЫ-ПРО, ЭЛЕКТРОСНАБ, КИП-КОМПЛЕКТ; заказ № 1 — размещён, ждём 1 шт ШУ-1
(под строку потребности АТХ); № 2 — размещён, кабеля поставлено 1200 м из 2000, цена хомутов неизвестна;
№ 3 — черновик без валюты; № 4 — отменён. s.morozov — закупка, i.belova — склад, o.nikitina — бухгалтерия.
"""
import pytest
from PySide6.QtCore import QMetaObject, QObject, Q_ARG, qInstallMessageHandler

pytestmark = pytest.mark.ui


def _v(item, prop):
    """Значение свойства QML как обычное значение Python (QJSValue → toVariant)."""
    v = item.property(prop)
    return v.toVariant() if hasattr(v, "toVariant") else v


def _popup(ui, name):
    """Всплывающее окно QML по objectName (Popup не виден в дереве элементов — ищем среди всех QObject)."""
    return next(o for o in ui.win.findChildren(QObject) if o.objectName() == name)


def _call(obj, method, *args):
    """Вызвать функцию QML объекта с аргументами QVariant."""
    QMetaObject.invokeMethod(obj, method, *[Q_ARG("QVariant", a) for a in args])


def _orders_tab(ui):
    """Открыть раздел «Закупка» → вкладку «Заказы поставщикам»; вернуть видимую панель заказов."""
    ui.go("procurement")
    sec = next(i for i in ui.items("tabsModel") if i.isVisible())
    sec.setProperty("tab", "orders")
    ui.wait_idle()
    return next(i for i in ui.items("pov") if i.isVisible())


def _order(pane, no):
    """Заказ из обзора панели по номеру."""
    return next(o for o in _v(pane, "pov")["orders"] if o.get("order_no") == no)


@pytest.fixture
def qml_warnings():
    """Собирать предупреждения QML (ошибки привязок, неизвестные свойства) — тест проверит, что их нет."""
    seen = []

    def handler(mode, ctx, msg):
        """Запомнить предупреждения из файлов QML."""
        if ".qml" in (ctx.file or "") or "qml" in msg.lower():
            seen.append(msg)
    prev = qInstallMessageHandler(handler)
    yield seen
    qInstallMessageHandler(prev)


def test_procurement_sees_orders_with_delivery_and_prices(ui, qml_warnings):
    ui.login("s.morozov")
    pane = _orders_tab(ui)
    pov = _v(pane, "pov")
    assert pov["can_create"] is True and pov["sees_prices"] is True
    assert (pov["counts"]["draft"], pov["counts"]["waiting"]) == (1, 2)
    assert [o["order_no"] for o in pov["orders"]] == [3, 2, 1]              # «В работе»: новые сверху
    o2 = _order(pane, 2)
    assert (o2["status_label"], o2["delivery_label"]) == ("Размещён", "Поставлен частично")
    assert o2["total_label"] == "285 200,00 RUB · 1 позиция без цены (итог неполный)"
    cable = o2["lines"][0]
    assert (cable["received_label"], cable["remaining_label"], cable["price_label"]) == ("1 200 м", "800 м", "142,60")
    assert o2["can_close"] is True and o2["can_cancel"] is False and o2["can_receive"] is False   # принимает склад
    o3 = _order(pane, 3)
    assert (o3["status"], o3["currency_label"], o3["can_place"]) == ("draft", "валюта не указана", True)
    # Архив: отменённый заказ с причиной.
    ui.backend.setOrdersFilter("closed")
    ui.wait_idle()
    arch = _v(pane, "pov")["orders"]
    assert [(o["order_no"], o["status_label"], o["close_reason"]) for o in arch] == [(4, "Отменён", "Дубль заказа № 3")]
    # Потребность показывает «заказано, ждём» по строке ШУ-1.
    nv = ui.backend.property("needsOverview")
    nv = nv.toVariant() if hasattr(nv, "toVariant") else nv
    shu = next(ln for ln in nv["needs"][0]["lines"] if ln["code"] == "ШУ-1")
    assert shu["on_order_label"] == "1 шт"
    # Редактор черновика: итог считается сразу (цены неизвестны — итог неполный).
    ui.backend.setOrdersFilter("active")
    ui.wait_idle()
    ed = _popup(ui, "poEditor")
    _call(ed, "openFor", _order(pane, 3))
    ui.wait_idle()
    assert ed.property("visible")
    total = _popup(ui, "poEditorTotal").property("text")
    assert total == "Итог: — · 1 позиция без цены (итог неполный)", total
    QMetaObject.invokeMethod(ed, "close")
    ui.wait_idle()
    assert qml_warnings == []


def test_draft_saved_offline_then_placed_online(ui):
    ui.login("s.morozov")
    pane = _orders_tab(ui)
    pov = _v(pane, "pov")
    sup = next(s for s in pov["suppliers"] if s["label"].startswith("ЭЛЕКТРОСНАБ"))
    item = next(i for i in pov["items"] if i["label"].startswith("ХМТ-200"))
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    oid = ui.backend.newId()
    ui.backend.savePoDraft("t", {"id": oid, "supplier_id": sup["id"], "currency": "rub", "supplier_ref": "", "expected_on": "",
                                 "note": "", "lines": [{"line_id": ui.backend.newId(), "item_id": item["id"], "qty": "2 000",
                                                        "unit_price": "1,5"}]})
    ui.wait_idle()
    new = next(o for o in _v(pane, "pov")["orders"] if o["id"] == oid)
    assert (new["title"], new["pending"], new["total_label"], new["can_place"]) == \
        ("Новый заказ (не отправлен)", True, "3 000,00 RUB", False)
    assert new["lines"][0]["qty_label"] == "2 000 шт"
    # Связь вернулась: очередь ушла, сервер дал номер; размещение — через форму.
    ui.transport.online = True
    ui.backend.syncNow()
    ui.wait_idle()
    new = next(o for o in _v(pane, "pov")["orders"] if o["id"] == oid)
    assert (new["order_no"], new["pending"], new["can_place"]) == (5, False, True)
    pane.setProperty("selectedId", oid)
    ui.wait_idle()
    pf = _popup(ui, "placeForm")
    QMetaObject.invokeMethod(pf, "open")
    ui.wait_idle()
    QMetaObject.invokeMethod(pf, "confirmed")
    ui.wait_idle()
    assert not pf.property("visible"), pf.property("error")
    new = next(o for o in _v(pane, "pov")["orders"] if o["id"] == oid)
    assert (new["status_label"], new["delivery_label"], new["can_edit"]) == ("Размещён", "Ждём поставку", False)


def test_warehouse_receives_line_and_sees_no_prices(ui):
    ui.login("i.belova")
    pane = _orders_tab(ui)
    pov = _v(pane, "pov")
    assert pov["can_create"] is False and pov["sees_prices"] is False
    o1 = _order(pane, 1)
    assert o1["total_label"] == "цены видят закупка и бухгалтерия" and o1["lines"][0]["price_label"] == ""
    assert o1["can_receive"] is True
    pane.setProperty("selectedId", o1["id"])
    ui.wait_idle()
    rf = _popup(ui, "poReceiveForm")
    _call(rf, "openFor", o1["lines"][0])
    ui.wait_idle()
    assert _popup(ui, "prQty").property("text") == "1"                      # по умолчанию — сколько ждём
    assert rf.property("canConfirm") is False                                # накладная обязательна
    _popup(ui, "prSrc").setProperty("text", "ТН № 230 от ООО «Шкафы-Про»")
    ui.wait_idle()
    QMetaObject.invokeMethod(rf, "confirmed")
    ui.wait_idle()
    assert not rf.property("visible"), rf.property("error")
    o1 = _order(pane, 1)
    assert (o1["delivery_label"], o1["lines"][0]["received_label"], o1["can_receive"]) == ("Поставлен полностью", "1 шт", False)


def test_accountant_sees_orders_tab_and_engineer_does_not(ui):
    ui.login("o.nikitina")
    assert ui.nav()["Закупка"]
    pane = _orders_tab(ui)
    assert _order(pane, 2)["total_label"].startswith("285 200,00 RUB")
    sec = next(i for i in ui.items("tabsModel") if i.isVisible())
    assert [t["id"] for t in _v(sec, "tabsModel")] == ["orders", "suppliers"]  # потребностей бухгалтерия не видит
    ui.backend.logout()
    ui.wait_idle()
    ui.login("d.kuznetsov")
    assert not ui.nav()["Закупка"]


def test_supplier_form_checks_inn_and_creates_offline(ui):
    ui.login("s.morozov")
    ui.go("procurement")
    sec = next(i for i in ui.items("tabsModel") if i.isVisible())
    sec.setProperty("tab", "suppliers")
    ui.wait_idle()
    pane = next(i for i in ui.items("sv") if i.isVisible() and _v(i, "sv") and "suppliers" in _v(i, "sv"))
    assert len(_v(pane, "sv")["suppliers"]) == 3
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    f = _popup(ui, "supForm")
    _call(f, "openFor", None)
    ui.wait_idle()
    _popup(ui, "spCode").setProperty("text", "КАБЕЛЬ-ТОРГ")
    _popup(ui, "spName").setProperty("text", "ООО «Кабель-Торг» (вымышленный)")
    _popup(ui, "spInn").setProperty("text", "1234567890")
    ui.wait_idle()
    assert f.property("canConfirm") is False                                  # контрольная сумма ИНН не сходится
    _popup(ui, "spInn").setProperty("text", "")
    ui.wait_idle()
    QMetaObject.invokeMethod(f, "confirmed")
    ui.wait_idle()
    rows = _v(pane, "sv")["suppliers"]
    new = next(s for s in rows if s["code"] == "КАБЕЛЬ-ТОРГ")
    assert new["pending"] is True and new["inn_label"] == "ИНН не указан" and pane.property("selectedId") == new["id"]
