"""UI-проверки этапа 5 «Производство» (С5 шаги 1–6, D-36) — настоящий QML, сервер in-process, демо-данные.

Демо: ТП АТХ ред. 1 (составила t.fedorova, утвердил a.grishin), черновик ТП ЭМ (t.fedorova); наряд № 1 на 1 комплект
(выпустила РП a.smirnova): операция 10 выполнена, 20 в работе с проблемой (k.pavlov), 30 — r.sergeev, 40 — без
исполнителя; материалы со склада на наряд ещё не выдавались.
"""
import pytest
from PySide6.QtCore import QMetaObject

from tests.ui.test_purchasing_ui import _call, _popup, _v, qml_warnings  # noqa: F401

pytestmark = pytest.mark.ui


def _visible(ui, prop):
    """Видимый элемент со свойством prop (вкладка раздела)."""
    return next(i for i in ui.items(prop) if i.isVisible())


def _tab(ui, tab):
    """Раздел «Производство» → вкладка tab (my / orders / tps)."""
    ui.go("production")
    page = ui.find("productionPage")
    page.setProperty("tab", tab)
    ui.wait_idle()
    return page


def test_worker_starts_with_my_operations_and_runs_timer_offline_rules(ui, qml_warnings):  # noqa: F811
    """Исполнитель начинает с «Мои операции»: операция 20 в работе (таймер), 30 чужая; «Пауза» — через очередь."""
    ui.login("k.pavlov")
    assert ui.page() == "production"
    assert ui.nav()["Производство"] is True and ui.nav().get("Тендеры и ТКП") is False
    pv = _v(ui.backend, "productionOverview")
    assert pv["default_tab"] == "my" and [t["id"] for t in pv["tabs"]] == ["my", "orders"]
    my = pv["my_ops"]
    # Выполненная 10 в «Мои операции» не попадает (кнопок нет), 30 — у r.sergeev.
    assert [(o["op_no"], o["state"]) for o in my] == [(20, "running")]
    op = my[0]
    assert [a["kind"] for a in op["actions"]] == ["pause", "finish"] and op["can_problem"] is True
    assert op["problem_count"] == 1 and op["fact_seconds"] == 0 and op["running_since"]
    assert ui.find("navProduction").property("badge") == 1
    timer = next(i for i in ui.items("text") if i.objectName() == "opTimer" and i.isVisible())
    assert timer.property("text").count(":") == 2 and timer.property("text") != "0:00:00"
    # «Пауза»: событие с временем этого компьютера; есть связь — сразу принято, таймер остановлен, факт накоплен.
    ui.backend.opEvent("t-pause", op["id"], "pause", "", "")
    ui.wait_idle()
    op2 = _v(ui.backend, "productionOverview")["my_ops"][0]
    assert op2["state"] == "paused" and op2["fact_seconds"] > 0 and not op2["pending"]
    assert [a["kind"] for a in op2["actions"]] == ["resume", "finish"]
    # Недопустимое событие (продолжить дважды не получится — сначала «Продолжить», потом снова «Продолжить»)
    # отсекается локально тем же правилом ядра и в очередь не попадает.
    ui.backend.opEvent("t-resume", op["id"], "resume", "", "")
    ui.wait_idle()
    before = ui.backend._pending
    ui.backend.opEvent("t-resume2", op["id"], "resume", "", "")
    ui.wait_idle()
    assert ui.backend._pending == before == 0
    assert qml_warnings == []


def test_worker_finish_and_problem_forms(ui, qml_warnings):  # noqa: F811
    """Формы «Завершить» (выполнено изделий) и «Проблема» (описание обязательно)."""
    ui.login("k.pavlov")
    pv = _v(ui.backend, "productionOverview")
    op = pv["my_ops"][0]
    pf = _popup(ui, "opProblemForm")
    _call(pf, "openFor", op)
    ui.wait_idle()
    assert pf.property("canConfirm") is False
    _popup(ui, "opProblemNote").setProperty("text", "Сломан обжимной инструмент")
    ui.wait_idle()
    assert pf.property("canConfirm") is True
    QMetaObject.invokeMethod(pf, "confirmed")
    ui.wait_idle()
    assert _v(ui.backend, "productionOverview")["my_ops"][0]["problem_count"] == 2
    ff = _popup(ui, "opFinishForm")
    _call(ff, "openFor", op)
    ui.wait_idle()
    _popup(ui, "opQtyDone").setProperty("text", "5")       # больше, чем в наряде (1)
    ui.wait_idle()
    assert ff.property("canConfirm") is False
    _popup(ui, "opQtyDone").setProperty("text", "")        # пусто — весь наряд
    ui.wait_idle()
    QMetaObject.invokeMethod(ff, "confirmed")
    ui.wait_idle()
    assert _v(ui.backend, "productionOverview")["my_ops"] == []    # выполненная пропала из «моих»
    wo = _v(ui.backend, "productionOverview")["work_orders"][0]
    assert [o["state"] for o in wo["operations"]][:2] == ["done", "done"]
    assert qml_warnings == []


def test_master_sees_work_order_snapshot_materials_and_cannot_close(ui, qml_warnings):  # noqa: F811
    """РП (мастер, A-42): наряд № 1 — снимок цел, операции с исполнителями, материалы не выданы → закрыть нельзя."""
    ui.login("a.smirnova")
    _tab(ui, "orders")
    pane = _visible(ui, "all")
    wos = _v(pane, "all")
    assert [w["doc_no"] for w in wos] == [1]
    pane.setProperty("selectedId", wos[0]["id"])
    ui.wait_idle()
    sel = _v(pane, "sel")
    assert sel["snapshot_ok"] is True and sel["status"] == "in_progress" and sel["manage"] is True
    assert [(o["op_no"], o["assignee_name"]) for o in sel["operations"]] == [
        (10, "Павлов Кирилл"), (20, "Павлов Кирилл"), (30, "Сергеев Роман"), (40, "не назначен")]
    mats = {m["code"]: m["state"] for m in sel["materials"] if m["code"]}
    assert mats["ШУ-1"] == "short" and mats["АРМ"] == "short" and mats["ШУ-0"] == "not_stock"
    assert sel["can_close"] is False and "Не все операции выполнены." in sel["close_problems"]
    assert ui.find("woSnapshotChip").property("text") == "Снимок цел"
    assert ui.find("btnCloseWo").property("enabled") is False
    assert ui.find("btnCancelWo").property("enabled") is True
    # Проблема исполнителя — уведомлением РП, «Открыть» ведёт в «Производство».
    nov = _v(ui.backend, "notificationsOverview")
    prob = next(n for n in nov["items"] if n["kind"] == "wo_problem")
    assert prob["page"] == "production" and prob["object_id"] == wos[0]["id"]
    # Назначить исполнителя операции 40 (только при связи).
    form = _popup(ui, "woAssignForm")
    op40 = next(o for o in sel["operations"] if o["op_no"] == 40)
    _call(form, "openFor", op40)
    ui.wait_idle()
    staff = _v(pane, "pv")["staff_options"]
    _popup(ui, "woAssignee").setProperty("currentIndex", next(i for i, s in enumerate(staff) if s["label"] == "Сергеев Роман"))
    ui.wait_idle()
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert not form.property("visible"), form.property("error")
    sel = _v(pane, "sel")
    assert next(o for o in sel["operations"] if o["op_no"] == 40)["assignee_name"] == "Сергеев Роман"
    assert qml_warnings == []


def test_master_releases_second_work_order_from_form(ui, qml_warnings):  # noqa: F811
    """«Выпустить наряд»: утверждённый ТП, количество, исполнитель первой операции → наряд № 2 со снимком × 2."""
    ui.login("a.smirnova")
    _tab(ui, "orders")
    pane = _visible(ui, "all")
    pv = _v(pane, "pv")
    assert pv["can_release"] is True and len(pv["release_tps"]) == 1     # черновик ЭМ не предлагается
    form = _popup(ui, "woReleaseForm")
    _call(form, "openFor")
    ui.wait_idle()
    _popup(ui, "woQty").setProperty("text", "2")
    ui.wait_idle()
    assert form.property("canConfirm") is True
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert not form.property("visible"), form.property("error")
    sel = _v(pane, "sel")
    assert sel["doc_no"] == 2 and sel["qty"] == 2 and sel["status"] == "released"
    shu = next(ln for ln in sel["spec"] if ln["code"] == "ШУ-1")
    assert shu["qty_label"] == "6 шт"                                      # 3 на изделие × 2
    assert next(o for o in sel["operations"] if o["op_no"] == 10)["norm_label"] == "8 ч 00 мин"
    assert qml_warnings == []


def test_technologist_edits_draft_and_cannot_approve_own(ui, qml_warnings):  # noqa: F811
    """Технолог: черновик ЭМ — правка в редакторе (проверка операций ядром), утвердить свой нельзя."""
    ui.login("t.fedorova")
    assert ui.page() == "production"
    _tab(ui, "tps")
    pane = _visible(ui, "all")
    tps = _v(pane, "all")
    assert sorted((t["kit_code"], t["status"]) for t in tps) == [("АТХ", "approved"), ("ЭМ", "draft")]
    draft = next(t for t in tps if t["status"] == "draft")
    assert draft["can_edit"] is True and draft["may_approve"] is False and draft["is_own"] is True
    pane.setProperty("selectedId", draft["id"])
    ui.wait_idle()
    assert ui.find("btnApproveTp").property("visible") is False
    # «Новый ТП»: у ЭМ уже есть черновик, у АТХ — утверждённый ТП (новая ревизия); прочие комплекты проекта — доступны.
    pv = _v(pane, "pv")
    assert pv["can_tp_create"] is True and ui.find("btnNewTp").property("enabled") is True
    assert not any(" · ЭМ · " in k["label"] for k in pv["kit_options"])
    ed = _popup(ui, "tpEditor")
    _call(ed, "openFor", draft, False)
    ui.wait_idle()
    chk = _v(ed, "check")
    assert chk["problems"] == [] and chk["norm_label"] == "6 ч 00 мин на изделие"
    # Ошибка в строке (норма с буквами) — видна сразу, сохранить нельзя.
    _call(ed, "setCell", 0, "norm_min", "три")
    ui.wait_idle()
    assert _v(ed, "check")["problems"] and ui.find("tpSave").property("enabled") is False
    _call(ed, "setCell", 0, "norm_min", "310,5")
    ui.wait_idle()
    assert _v(ed, "check")["problems"] == [] and ui.find("tpSave").property("enabled") is True
    QMetaObject.invokeMethod(ed, "save")
    ui.wait_idle()
    assert not ed.property("visible"), ed.property("error")
    d2 = next(t for t in _v(pane, "all") if t["id"] == draft["id"])
    assert d2["operations"][0]["norm_min"].startswith("310.5") and d2["version"] == 2
    assert qml_warnings == []


def test_second_technologist_approves(ui, qml_warnings):  # noqa: F811
    """Другой технолог утверждает черновик (четыре глаза); после этого «Изменить» пропадает."""
    ui.login("a.grishin")
    _tab(ui, "tps")
    pane = _visible(ui, "all")
    draft = next(t for t in _v(pane, "all") if t["status"] == "draft")
    assert draft["can_approve"] is True
    pane.setProperty("selectedId", draft["id"])
    ui.wait_idle()
    QMetaObject.invokeMethod(_popup(ui, "tpApproveForm"), "confirmed")
    ui.wait_idle()
    t = next(t for t in _v(pane, "all") if t["id"] == draft["id"])
    assert t["status"] == "approved" and t["approved_label"].startswith("Утвердил(а) Гришин Алексей")
    assert ui.find("btnEditTp").property("visible") is False
    assert qml_warnings == []


def test_warehouse_issue_form_offers_work_orders(ui, qml_warnings):  # noqa: F811
    """Склад: в форме выдачи на проект — выбор наряда этого проекта; выдача на наряд видна в его материалах."""
    ui.login("i.belova")
    pv = _v(ui.backend, "productionOverview")
    wo = pv["work_orders"][0]
    assert wo["doc_no"] == 1 and wo["manage"] is False                  # склад наряды только видит
    ui.go("stock")
    stock = next(i for i in ui.items("projectOptions") if i.isVisible())
    item = next(i for i in _v(stock, "ov")["items"] if i["code"] == "ХМТ-200")   # свободный остаток есть
    form = _popup(ui, "issueForm")
    _call(form, "openFor", item)
    ui.wait_idle()
    projs = _v(stock, "projectOptions")
    _popup(ui, "iProj").setProperty("currentIndex", next(i for i, p in enumerate(projs) if p["id"] == wo["project_id"]))
    ui.wait_idle()
    iwo = _popup(ui, "iWo")
    opts = _v(iwo, "model")
    assert [o["id"] for o in opts] == ["", wo["id"]] and iwo.property("visible") is True
    assert qml_warnings == []
