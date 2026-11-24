# -*- coding: utf-8 -*-
"""UI-проверка норм трудоёмкости (D-72, этап 7 blueprint 52) — блок «Нормы трудоёмкости» в карточке наряда.

Сценарий: ведущий инженер (m.lebedev) видит агрегированную статистику (в демо — завершённые операции
«Сборка шкафа управления» нарядов № 1 и № 2), предлагает норму по ней (диапазон пересчитает сервер);
руководитель отдела (e.orlova) утверждает предложение — она не автор; норма в техпроцессы автоматически
не заносится (ручное утверждение + правка черновика ТП).
"""
import pytest
from PySide6.QtCore import QMetaObject

from tests.ui.test_purchasing_ui import _call, _popup, _v, qml_warnings  # noqa: F401

pytestmark = pytest.mark.ui


def _pane(ui):
    """Вкладка «Наряды» раздела «Производство» с выбранным нарядом № 1."""
    ui.go("production")
    page = ui.find("productionPage")
    page.setProperty("tab", "orders")
    ui.wait_idle()
    pane = next(i for i in ui.items("all") if i.isVisible())
    wo1 = next(w for w in _v(pane, "all") if w["doc_no"] == 1)
    pane.setProperty("selectedId", wo1["id"])
    ui.wait_idle()
    return pane


def test_norm_propose_stats_and_approval(ui, qml_warnings):  # noqa: F811
    # Ведущий инженер: статистика операций (серверный запрос), предложение нормы по завершённой операции.
    ui.login("m.lebedev")
    pane = _pane(ui)
    nv = pane.property("normsView")
    assert nv["can_propose"] is True
    # Демо-данные (D-76): уже есть утверждённое предложение этого же пользователя — оно не мешает новому.
    assert len(nv["rows"]) == 1 and nv["rows"][0]["status"] == "approved"
    # Статистика: «Сборка шкафа управления» завершена в нарядах № 1 (190 мин: 60+40+90 без обеда) и № 2 (120 мин).
    dlg = _popup(ui, "normStatsForm")
    _call(dlg, "openFor")
    ui.wait_idle()
    rows = {r["op_name"]: r for r in _v(dlg, "rows")}
    st = rows["Сборка шкафа управления"]
    assert (st["sample_size"], st["min_min"], st["max_min"]) == (2, 120.0, 190.0)
    assert any(float(x) == 240.0 for x in st["current_norms"])   # норма 240 из утверждённого ТП АТХ
    dlg.close()
    # Предложить норму по операции 10 (завершена; диапазон пересчитает сервер).
    form = _popup(ui, "normProposeForm")
    _call(form, "openFor")
    ui.wait_idle()
    ops = _v(form, "opOptions")
    assert any("Сборка шкафа управления" in o["label"] for o in ops)
    sel_op = _popup(ui, "normOp")
    sel_op.setProperty("currentIndex", next(i for i, o in enumerate(ops) if "Сборка шкафа управления" in o["label"]))
    ui.wait_idle()
    _popup(ui, "normMinutes").setProperty("text", "210")
    _popup(ui, "normComment").setProperty("text", "По факту двух нарядов")
    ui.wait_idle()
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert not form.property("visible"), form.property("error")
    nv = pane.property("normsView")
    row = next(r for r in nv["rows"] if r["status"] == "proposed")
    assert row["proposed_label"] == "210 мин"
    assert row["author_name"] == "Лебедев Максим" and row["can_decide"] is False   # автор сам не решает
    # Руководитель отдела: видит предложение, утверждает (не автор), уведомление автору.
    ui.login("e.orlova")
    pane = _pane(ui)
    nv = pane.property("normsView")
    proposed = [r for r in nv["rows"] if r["status"] == "proposed"]
    assert len(proposed) == 1 and proposed[0]["can_decide"] is True
    decide = _popup(ui, "normDecideForm")
    _call(decide, "openFor", proposed[0], "approved")
    ui.wait_idle()
    assert decide.property("title") == "Утвердить норму"
    QMetaObject.invokeMethod(decide, "confirmed")
    ui.wait_idle()
    assert not decide.property("visible"), decide.property("error")
    nv = pane.property("normsView")
    row = next(r for r in nv["rows"] if r["id"] == proposed[0]["id"])
    assert row["status"] == "approved" and "Орлова Елена" in row["decision_label"]
    # Норма в ТП не изменилась автоматически (ручное утверждение — решение, не подмена работы технолога).
    tps = _v(ui.backend, "productionOverview")["tps"]
    op10 = next(o for t in tps if t["status"] == "approved" for o in t["operations"] if o["op_no"] == 10)
    assert op10["norm_min"] == "240" or float(op10["norm_min"]) == 240.0
    assert qml_warnings == []


def test_norm_reject_needs_reason_and_self_decision_blocked(ui, qml_warnings):  # noqa: F811
    # Ведущий инженер предлагает; сам решить не может (сервер — self_decision); отклонение без причины не пройдёт
    # (кнопка «Отклонить» неактивна), с причиной — решает руководитель отдела, автору приходит уведомление.
    ui.login("m.lebedev")
    pane = _pane(ui)
    form = _popup(ui, "normProposeForm")
    _call(form, "openFor")
    ui.wait_idle()
    ops = _v(form, "opOptions")
    sel_op = _popup(ui, "normOp")
    sel_op.setProperty("currentIndex", next(i for i, o in enumerate(ops) if "Сборка шкафа управления" in o["label"]))
    _popup(ui, "normMinutes").setProperty("text", "200")
    ui.wait_idle()
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert not form.property("visible"), form.property("error")
    # Свой предложения не решает: кнопок решения у автора нет, а сервер ответит self_decision.
    nv = pane.property("normsView")
    prop = next(r for r in nv["rows"] if r["status"] == "proposed")
    assert prop["can_decide"] is False
    r = ui.backend.client.execute("norm.decide", {"id": prop["id"], "resolution": "approved"})
    assert r.status == "rejected" and r.response.get("code") == "self_decision"
    # Руководитель отдела: «Отклонить» без причины — кнопка неактивна; с причиной — отклонено.
    ui.login("e.orlova")
    pane = _pane(ui)
    prop = next(r for r in pane.property("normsView")["rows"] if r["status"] == "proposed")
    decide = _popup(ui, "normDecideForm")
    _call(decide, "openFor", prop, "rejected")
    ui.wait_idle()
    assert decide.property("title") == "Отклонить предложение" and decide.property("canConfirm") is False
    _popup(ui, "normDecisionReason").setProperty("text", "Норма 210 из прошлого решения точнее")
    ui.wait_idle()
    assert decide.property("canConfirm") is True
    QMetaObject.invokeMethod(decide, "confirmed")
    ui.wait_idle()
    assert not decide.property("visible"), decide.property("error")
    row = next(r for r in pane.property("normsView")["rows"] if r["id"] == prop["id"])
    assert row["status"] == "rejected" and row["decision_reason"] == "Норма 210 из прошлого решения точнее"
    # Уведомление автору предложения.
    ui.login("m.lebedev")
    ui.wait_idle()
    nov = _v(ui.backend, "notificationsOverview")
    assert any(n["kind"] == "norm_decided" for n in nov["items"])
    assert qml_warnings == []
