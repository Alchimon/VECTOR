"""UI-проверки заказов поставщикам, согласования и факта поставки (С4, шаг 5; D-32, D-33): настоящий QML,
сервер in-process, демо-данные.

Демо (vector.server.demo): поставщики ШКАФЫ-ПРО и ЭЛЕКТРОСНАБ (с реквизитами и договорами ШП-3 / П-14),
КИП-КОМПЛЕКТ (без реквизитов); заказ № 1 — согласован и размещён, ждём 1 шт ШУ-1 (под строку потребности АТХ);
№ 2 — размещён, кабеля поставлено 1200 м из 2000, цена хомутов неизвестна; № 3 — черновик без валюты; № 4 — отменён;
№ 5 — на согласовании (цены без НДС). s.morozov — закупка, v.zaitseva — согласование закупок, i.belova — склад,
o.nikitina — бухгалтерия.
"""
DEMO_PWD = "Демо-пароль-2026"
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
    assert (pov["counts"]["draft"], pov["counts"]["waiting"], pov["counts"]["on_approval"]) == (2, 2, 1)
    assert [o["order_no"] for o in pov["orders"]] == [5, 3, 2, 1]           # «В работе»: новые сверху
    o2 = _order(pane, 2)
    assert (o2["status_label"], o2["delivery_label"]) == ("Размещён", "Поставлен частично")
    assert o2["total_label"] == "285 200,00 RUB · 1 позиция без цены (итог неполный)"
    # НДС раздельно (A-35): цены с НДС 22 %, у хомутов цены нет — НДС считается по строкам с ценой.
    assert (o2["vat"]["net_label"], o2["vat"]["vat_label"], o2["vat"]["gross_label"]) == \
        ("233 770,49 RUB", "51 429,51 RUB", "285 200,00 RUB")
    assert o2["contract_label"].startswith("№ П-14 от ") and o2["approval_label"] == "Согласован"
    # № 5 на согласовании: закупка его не правит, может только отозвать.
    o5 = _order(pane, 5)
    assert (o5["approval_label"], o5["can_edit"], o5["can_withdraw"], o5["can_approve"]) == ("На согласовании", False, True, False)
    assert o5["vat"]["gross_label"] == "2 348,50 RUB" and o5["vat_mode_label"] == "Цены без НДС"
    cable = o2["lines"][0]
    assert (cable["received_label"], cable["remaining_label"], cable["price_label"]) == ("1 200 м", "800 м", "142,60")
    assert o2["can_close"] is True and o2["can_cancel"] is False and o2["can_receive"] is False   # принимает склад
    o3 = _order(pane, 3)
    assert (o3["status"], o3["currency_label"], o3["can_submit"], o3["can_place"]) == ("draft", "валюта не указана", True, False)
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
    assert _popup(ui, "poEditorVat").property("text") == "НДС не посчитан: нет строк с ценой"
    QMetaObject.invokeMethod(ed, "close")
    ui.wait_idle()
    assert qml_warnings == []


def test_draft_saved_offline_then_approved_and_placed_online(ui):
    ui.login("s.morozov")
    pane = _orders_tab(ui)
    pov = _v(pane, "pov")
    sup = next(s for s in pov["suppliers"] if s["label"].startswith("ЭЛЕКТРОСНАБ"))
    item = next(i for i in pov["items"] if i["label"].startswith("ХМТ-200"))
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    oid = ui.backend.newId()
    con = next(c for c in pov["contracts"] if c["supplier_id"] == sup["id"])
    ui.backend.savePoDraft("t", {"id": oid, "supplier_id": sup["id"], "currency": "rub", "supplier_ref": "", "expected_on": "",
                                 "note": "", "vat_mode": "included", "contract_id": con["id"],
                                 "lines": [{"line_id": ui.backend.newId(), "item_id": item["id"], "qty": "2 000",
                                            "unit_price": "1,5", "vat_rate": "22"}]})
    ui.wait_idle()
    new = next(o for o in _v(pane, "pov")["orders"] if o["id"] == oid)
    assert (new["title"], new["pending"], new["total_label"], new["can_submit"], new["can_place"]) == \
        ("Новый заказ (не отправлен)", True, "3 000,00 RUB", False, False)
    assert new["lines"][0]["qty_label"] == "2 000 шт" and new["vat"]["vat_label"] == "540,98 RUB"
    # Связь вернулась: очередь ушла, сервер дал номер; на согласование — через форму.
    ui.transport.online = True
    ui.backend.syncNow()
    ui.wait_idle()
    new = next(o for o in _v(pane, "pov")["orders"] if o["id"] == oid)
    assert (new["order_no"], new["pending"], new["can_submit"], new["can_place"]) == (6, False, True, False)
    pane.setProperty("selectedId", oid)
    ui.wait_idle()
    sf = _popup(ui, "submitForm")
    _call(sf, "openFor", new)
    ui.wait_idle()
    QMetaObject.invokeMethod(sf, "confirmed")
    ui.wait_idle()
    assert not sf.property("visible"), sf.property("error")
    new = next(o for o in _v(pane, "pov")["orders"] if o["id"] == oid)
    assert (new["approval_label"], new["can_edit"], new["can_place"]) == ("На согласовании", False, False)
    # Согласует другой человек (v.zaitseva) — на сервере; закупка обновляет и размещает.
    ap = ui.svc.handle({"type": "login", "login": "v.zaitseva", "password": DEMO_PWD, "device_id": "t"}, "10.0.0.31")
    r = ui.svc.handle({"type": "command", "token": ap["token"], "operation_id": ui.backend.newId(), "command": "po.approve",
                       "payload": {"id": oid}, "expected_version": new["version"]}, "10.0.0.31")
    assert r["status"] == "accepted", r
    ui.backend.syncNow()
    ui.wait_idle()
    new = next(o for o in _v(pane, "pov")["orders"] if o["id"] == oid)
    assert (new["approval_label"], new["can_place"]) == ("Согласован", True)
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
    assert o1["total_label"] == "цены видят закупка, бухгалтерия и согласующие" and o1["lines"][0]["price_label"] == ""
    assert o1.get("vat") is None and o1["lines"][0]["vat_rate_label"] == "22 %"   # ставку видит, НДС в деньгах — нет
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


def test_approver_approves_and_returns_with_reason(ui, qml_warnings):
    """Согласующий (D-33): видит заказы на согласовании с ценами и НДС, согласует; возврат — только с причиной."""
    ui.login("v.zaitseva")
    pane = _orders_tab(ui)
    pov = _v(pane, "pov")
    assert (pov["is_approver"], pov["sees_prices"], pov["can_create"]) == (True, True, False)
    ui.backend.setOrdersFilter("approval")
    ui.wait_idle()
    o5 = _v(pane, "pov")["orders"]
    assert [o["order_no"] for o in o5] == [5]
    o5 = o5[0]
    assert (o5["can_approve"], o5["can_return"], o5["contract_label"].startswith("№ П-14")) == (True, True, True)
    assert o5["approval_events"][0]["label"].startswith("На согласовании · Морозов Сергей")
    pane.setProperty("selectedId", o5["id"])
    ui.wait_idle()
    # Возврат без причины не отправить.
    rf = _popup(ui, "returnForm")
    _call(rf, "openFor", o5)
    ui.wait_idle()
    assert rf.property("canConfirm") is False
    QMetaObject.invokeMethod(rf, "close")
    ui.wait_idle()
    af = _popup(ui, "approveForm")
    _call(af, "openFor", o5)
    ui.wait_idle()
    assert "с НДС 2 348,50 RUB" in af.property("subtitle")
    _popup(ui, "apComment").setProperty("text", "Цена по договору")
    QMetaObject.invokeMethod(af, "confirmed")
    ui.wait_idle()
    assert not af.property("visible"), af.property("error")
    assert _v(pane, "pov")["orders"] == [] and _v(pane, "pov")["empty_text"] == "Заказов на согласовании нет."
    ui.backend.setOrdersFilter("draft")
    ui.wait_idle()
    o5 = _order(pane, 5)
    assert (o5["approval_label"], o5["can_approve"]) == ("Согласован", False)
    assert o5["approval_events"][-1]["comment"] == "Цена по договору"
    assert qml_warnings == []


def test_supplier_requisites_and_contract_forms(ui, qml_warnings):
    """Реквизиты проверяются в форме (контрольные цифры, ключ счёта от БИК); договор добавляется без связи (A-34)."""
    ui.login("s.morozov")
    ui.go("procurement")
    sec = next(i for i in ui.items("tabsModel") if i.isVisible())
    sec.setProperty("tab", "suppliers")
    ui.wait_idle()
    pane = next(i for i in ui.items("sv") if i.isVisible() and _v(i, "sv") and "suppliers" in _v(i, "sv"))
    rows = {s["code"]: s for s in _v(pane, "sv")["suppliers"]}
    assert (rows["ЭЛЕКТРОСНАБ"]["requisites_label"], rows["КИП-КОМПЛЕКТ"]["requisites_label"]) == \
        ("реквизиты заполнены", "реквизиты не заполнены")
    assert [c["number"] for c in rows["ЭЛЕКТРОСНАБ"]["contracts"]] == ["П-14"]
    kip = rows["КИП-КОМПЛЕКТ"]
    pane.setProperty("selectedId", kip["id"])
    ui.wait_idle()
    f = _popup(ui, "supForm")
    _call(f, "openFor", kip)
    ui.wait_idle()
    _popup(ui, "spBik").setProperty("text", "040000001")
    _popup(ui, "spAcc").setProperty("text", "40702810700000000102")        # ключ счёта не сходится
    ui.wait_idle()
    assert f.property("canConfirm") is False and _popup(ui, "spAcc").property("error") != ""
    _popup(ui, "spAcc").setProperty("text", "40702810700000000101")
    ui.wait_idle()
    assert f.property("canConfirm") is True
    QMetaObject.invokeMethod(f, "confirmed")
    ui.wait_idle()
    assert not f.property("visible"), f.property("error")
    kip = next(s for s in _v(pane, "sv")["suppliers"] if s["code"] == "КИП-КОМПЛЕКТ")
    assert (kip["bik"], kip["bank_account"]) == ("040000001", "40702810700000000101")
    # Договор — без связи (очередь); виден сразу с пометкой «не отправлено».
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    cf = _popup(ui, "conForm")
    _call(cf, "openFor", None)
    ui.wait_idle()
    _popup(ui, "cnNumber").setProperty("text", "К-1")
    _popup(ui, "cnSigned").setProperty("text", "2026-03-01")
    _popup(ui, "cnUntil").setProperty("text", "2026-02-01")
    ui.wait_idle()
    assert cf.property("canConfirm") is False                               # срок раньше даты заключения
    _popup(ui, "cnUntil").setProperty("text", "")
    ui.wait_idle()
    QMetaObject.invokeMethod(cf, "confirmed")
    ui.wait_idle()
    kip = next(s for s in _v(pane, "sv")["suppliers"] if s["code"] == "КИП-КОМПЛЕКТ")
    assert [(c["number"], c["pending"], c["label"]) for c in kip["contracts"]] == [("К-1", True, "№ К-1 от 01.03.2026, бессрочный")]
    assert qml_warnings == []
