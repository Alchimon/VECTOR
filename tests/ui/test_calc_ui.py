"""UI-проверки расчёта комплекта и итога проекта (С3): настоящий QML, сервер in-process, демо-данные.

Демо (vector.server.demo): АТХ — зафиксирована v1, затем инженер внёс правки (кол-во кабеля, удалены
резервные модули, добавлен датчик без цены); ЭМ — зафиксирована v1; итог проекта — ревизия r2.
"""
import pytest
from PySide6.QtCore import QMetaObject, QObject

pytestmark = pytest.mark.ui


def _calc(ui, login="a.smirnova"):
    ui.login(login)
    ui.go("projects", ui.ids["project"])
    ui.kit_work(ui.ids["kits"]["АТХ"], "calc")
    kc = next(i for i in ui.items("freezeBlocker") if i.isVisible())
    return kc


def _v(item, prop):
    v = item.property(prop)
    return v.toVariant() if hasattr(v, "toVariant") else v


def _popup(ui, name):
    return next(o for o in ui.win.findChildren(QObject) if o.objectName() == name)


def test_calc_tab_shows_draft_versions_and_diff(ui):
    kc = _calc(ui)
    v = _v(kc, "v")
    assert v["last"]["label"] == "v1" and v["draft_version"] == 2
    assert v["preview"]["unknown_price_count"] == 1
    assert ui.find("calcDraftTotal").property("text") == "3 580 164,00 RUB"
    assert ui.find("calcDraftIncomplete").property("text") == "1 поз. без цены — итог неполный"
    kinds = sorted(r["kind"] for r in v["diff_rows"])
    assert kinds.count("added") == 1 and kinds.count("removed") == 1 and kinds.count("changed") == 1
    assert "По сравнению с v1: добавлено 1, изменено 1, удалено 1." in ui.find("calcDiffSummary").property("text")
    # РП проекта не фиксирует (только ГИП): кнопки нет.
    assert not ui.find("calcFreezeButton").isVisible()
    kit = next(k for k in ui.backend.kits if k["code"] == "АТХ")
    assert kit["calc_label"] == "расчёт v1 · есть правки"


def test_gip_freezes_through_dialog_and_project_total_updates(ui):
    kc = _calc(ui, "p.volkov")
    btn = ui.find("calcFreezeButton")
    assert btn.isVisible() and btn.property("enabled")
    dlg = _popup(ui, "calcFreezeDialog")
    QMetaObject.invokeMethod(dlg, "ask")
    ui.wait_idle()
    assert dlg.property("canConfirm") is False            # без основания — нельзя
    reason = _popup(ui, "calcFreezeReason")
    reason.setProperty("text", "Уточнённый расчёт после изм. 2")
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert not dlg.property("visible")
    v = _v(kc, "v")
    assert v["last"]["label"] == "v2" and v["same_as_last"] is True
    assert "не менялся после последней фиксации" in kc.property("freezeBlocker")
    pc = ui.backend.projectCalc(ui.ids["project"], 0)
    assert pc["latest"]["revision_no"] == 3 and pc["latest"]["composition"] == "АТХ v2, ЭМ v1"
    assert pc["latest"]["totals"][0]["complete"] is False   # датчик без цены → итог проекта неполный


def test_editor_validates_locally_and_saves_offline_into_queue(ui):
    kc = _calc(ui, "d.kuznetsov")
    ed = _popup(ui, "calcEditor")
    QMetaObject.invokeMethod(ed, "openFor", _arg(_v(kc, "kit")), _arg(_v(kc, "v")))
    ui.wait_idle()
    assert ed.property("visible")
    n = len(_v(ed, "pv")["rows"])
    # Ошибка в строке: количество с 5 знаками после запятой → замечание у строки, «Сохранить» недоступна.
    QMetaObject.invokeMethod(ed, "setCell", _arg(0), _arg("qty"), _arg("1,00001"))
    ui.wait_idle()
    pv = _v(ed, "pv")
    assert pv["valid"] is False and "4 знаков" in pv["rows"][0]["problem"]
    assert ui.find("calcSave").property("enabled") is False
    QMetaObject.invokeMethod(ed, "setCell", _arg(0), _arg("qty"), _arg("4"))
    ui.wait_idle()
    assert _v(ed, "pv")["valid"] is True
    # Без связи: сохранение уходит в очередь, черновик помечен «Не отправлено», фиксация объясняет, почему нельзя.
    ui.transport.online = False
    QMetaObject.invokeMethod(ed, "save")
    ui.wait_idle()
    assert not ed.property("visible") and ui.backend.pendingCount == 1
    v = _v(kc, "v")
    assert v["pending"] is True and len(v["lines"]) == n and v["lines"][0]["qty"] == "4"
    ui.transport.online = True
    ui.backend.syncNow()
    ui.wait_idle()
    assert ui.backend.pendingCount == 0 and _v(kc, "v")["draft_version"] == 3


def test_paste_from_excel_adds_rows_without_rounding(ui):
    kc = _calc(ui, "d.kuznetsov")
    ed = _popup(ui, "calcEditor")
    QMetaObject.invokeMethod(ed, "openFor", _arg(_v(kc, "kit")), _arg({"lines": [], "currency": "RUB"}))
    ui.wait_idle()
    QMetaObject.invokeMethod(ed, "pasteRows", _arg("К-1\tКлапан\tшт\t2\t1 500,25\nК-2\tПривод\tшт\t1\t\n"))
    ui.wait_idle()
    pv = _v(ed, "pv")
    assert [r["name"] for r in pv["rows"]] == ["Клапан", "Привод"]
    assert pv["rows"][0]["amount_label"] == "3 000,50" and pv["rows"][1]["price_unknown"] is True
    assert pv["total_label"] == "3 000,50 RUB · 1 позиция без цены (итог неполный)"


def test_accountant_sees_only_project_totals(ui):
    ui.login("o.nikitina")
    nav = ui.nav()
    assert nav["Итоги проектов"] is True
    assert ui.backend.projects == [] and ui.backend.kits == []
    fo = ui.backend.financeOverview
    assert [p["project_code"] for p in fo] == ["П-2026-014"] and fo[0]["latest"]["revision_no"] == 2
    ui.go("finance")
    assert ui.page() == "finance"


def test_engineer_without_project_role_has_no_finance_section(ui):
    ui.login("d.kuznetsov")
    assert ui.nav()["Итоги проектов"] is False and ui.backend.financeOverview == []


def _arg(value):
    from PySide6.QtCore import Q_ARG
    return Q_ARG("QVariant", value)
