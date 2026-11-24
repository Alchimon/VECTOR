"""UI-проверки раздела «Рабочее время» (этап 7, D-42, D-43) — настоящий QML, сервер in-process, демо-данные.

Демо (_seed_worktime): у d.kuznetsov отчёт за вчера на проверке, черновик за сегодня и отчёт за позавчера, собранный
программой («На самопроверке»); у m.lebedev отчёт за позавчера принят, за вчера — возвращён с причиной. Проверяет
руководитель отдела e.orlova. Отправка — через форму самопроверки (все отметки обязательны).
"""
import pytest
from PySide6.QtCore import QMetaObject

from tests.ui.test_purchasing_ui import _call, _popup, _v, qml_warnings  # noqa: F401

pytestmark = pytest.mark.ui


def _wv(ui):
    """Текущее представление раздела «Рабочее время»."""
    return _v(ui.backend, "worktimeOverview")


def _confirm(ui, form):
    """Нажать «подтвердить» в форме и дождаться ответа сервера."""
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()


def test_engineer_fills_today_offline_and_submits(ui, qml_warnings):  # noqa: F811
    """Инженер открывает черновик за сегодня, дописывает строку (форма считает итоги), сохраняет и отправляет без связи;
    при связи отчёт уходит руководителю отдела."""
    ui.login("d.kuznetsov")
    assert ui.nav()["Рабочее время"] is True
    v = _wv(ui)
    assert [r["status"] for r in v["mine"]] == ["draft", "submitted", "self_check"] and v["tabs"] == []
    today = v["mine"][0]
    assert today["id"] == v["today_id"] and today["problems"] == [] and today["unallocated_label"] == "5 ч"
    ui.go("worktime", today["id"])
    page = ui.find("worktimePage")
    assert page.property("selectedId") == today["id"]
    ed = _popup(ui, "reportEditor")
    _call(ed, "openFor", today, today["work_date"])
    ui.wait_idle()
    chk = _v(ed, "check")
    assert chk["ok"] is True and chk["totals"]["total_label"] == "3 ч"
    ui.transport.online = False
    form = {"id": today["id"], "work_date": today["work_date"], "shift_start": "09:00", "shift_end": "18:00",
            "break_minutes": "60", "comment": "",
            "lines": [{"project_id": ln["project_id"], "task_id": ln["task_id"], "hours": ln["hours_input"], "note": ln["note"]}
                      for ln in today["lines"]] + [{"project_id": "", "task_id": "", "hours": "5", "note": "Проверка чертежей"}]}
    ui.backend.saveReport("t-save", form)
    ui.wait_idle()
    today = _wv(ui)["mine"][0]
    assert today["pending"] and today["total_label"] == "8 ч" and today["can_submit"]
    sf = _popup(ui, "reportSubmitForm")
    _call(sf, "openFor")
    ui.wait_idle()
    assert sf.property("canConfirm") is False                       # без самопроверки — не отправить
    sf.setProperty("marks", {"shift": True, "lines": True, "hours": True})
    ui.wait_idle()
    assert sf.property("canConfirm") is True
    _confirm(ui, sf)
    today = _wv(ui)["mine"][0]
    assert today["status"] == "submitted" and ui.backend.property("pendingCount") == 2
    ui.transport.online = True
    ui.backend.syncNow()
    ui.wait_idle()
    today = _wv(ui)["mine"][0]
    assert (today["status"], today["pending"]) == ("submitted", False) and ui.backend.property("pendingCount") == 0
    assert qml_warnings == []


def test_dept_head_returns_with_reason_and_accepts(ui, qml_warnings):  # noqa: F811
    """Руководитель отдела: вкладка «На проверке» открывается сама; возврат — только с причиной; принятие."""
    ui.login("e.orlova")
    v = _wv(ui)
    assert [t["id"] for t in v["tabs"]] == ["mine", "review"] and v["default_tab"] == "review"
    assert v["counts"]["to_review"] == 1
    r = next(x for x in v["review"] if x["status"] == "submitted")
    assert r["user_name"] == "Кузнецов Дмитрий" and r["can_review"] and r["line_count"] == 3
    ui.go("worktime", r["id"])
    page = ui.find("worktimePage")
    assert page.property("tab") == "review" and page.property("selectedId") == r["id"]
    rf = _popup(ui, "reportReviewForm")
    _call(rf, "openFor", "return")
    ui.wait_idle()
    assert rf.property("canConfirm") is False                       # возврат — с причиной
    _popup(ui, "reportReviewComment").setProperty("text", "Уточните задачу во второй строке")
    ui.wait_idle()
    assert rf.property("canConfirm") is True
    _confirm(ui, rf)
    r = next(x for x in _wv(ui)["review"] if x["id"] == r["id"])
    assert r["status"] == "returned" and r["review_comment"] == "Уточните задачу во второй строке"
    assert _wv(ui)["counts"]["to_review"] == 0
    assert qml_warnings == []


def test_returned_report_shows_reason_and_reopen_needs_reason(ui, qml_warnings):  # noqa: F811
    """Ведущий инженер видит возвращённый отчёт с причиной; принятый можно исправить только с основанием."""
    ui.login("m.lebedev")
    v = _wv(ui)
    assert [r["status"] for r in v["mine"]] == ["returned", "accepted"] and v["counts"]["returned"] == 1
    ret, acc = v["mine"]
    assert "слишком общо" in ret["review_comment"] and ret["can_edit"] and ret["can_submit"]
    assert acc["may_reopen"] and acc["can_reopen"] and "Орлова Елена" in acc["reviewed_label"]
    ui.go("worktime", acc["id"])
    f = _popup(ui, "reportReopenForm")
    _call(f, "openFor")
    ui.wait_idle()
    assert f.property("canConfirm") is False
    _popup(ui, "reportReopenReason").setProperty("text", "Забыл планёрку")
    ui.wait_idle()
    _confirm(ui, f)
    acc = next(x for x in _wv(ui)["mine"] if x["id"] == acc["id"])
    assert acc["status"] == "draft" and acc["history"][-1]["extra"] == "Забыл планёрку"
    assert qml_warnings == []


def test_everyone_sees_section_without_projects(ui, qml_warnings):  # noqa: F811
    """Раздел виден всем: кладовщик без проектов заполняет отчёт «не по проекту»."""
    ui.login("i.belova")
    assert ui.nav()["Рабочее время"] is True
    v = _wv(ui)
    assert v["mine"] == [] and [p["label"] for p in v["project_options"]] == ["Не по проекту"] and v["tabs"] == []
    ui.go("worktime")
    page = ui.find("worktimePage")
    assert page.property("emptyAction") == "Отчёт за сегодня"
    assert qml_warnings == []


def test_assembled_report_self_check_keeps_sources_and_submits(ui, qml_warnings):  # noqa: F811
    """D-43: собранный программой отчёт — баннер «собран программой», строки «по таймеру» / «оценка программы»;
    в редакторе отметка источника сохраняется у неизменённой строки и снимается у исправленной; отправка — после
    самопроверки (форма с отметками); руководитель отдела видит отчёт только после отправки."""
    ui.login("d.kuznetsov")
    auto = next(r for r in _wv(ui)["mine"] if r["status"] == "self_check")
    assert auto["origin"] == "auto" and auto["assembled_label"].startswith("по 14 действиям")
    assert [ln["source_label"] for ln in auto["lines"]] == ["по таймеру", "оценка программы", "оценка программы"]
    ui.go("worktime", auto["id"])
    assert ui.find("reportAssembled").property("visible") is True
    ed = _popup(ui, "reportEditor")
    _call(ed, "openFor", auto, auto["work_date"])
    ui.wait_idle()
    lines = [{"project_id": ln["project_id"], "task_id": ln["task_id"], "hours": ln["hours_input"], "note": ln["note"],
              "source": ln["source"], "orig": {"project_id": ln["project_id"], "task_id": ln["task_id"],
                                               "minutes": ln["minutes"], "note": ln["note"]}} for ln in auto["lines"]]
    lines[1]["hours"] = "3"                                          # уточнил оценку программы
    form = {"id": auto["id"], "work_date": auto["work_date"], "shift_start": auto["shift_start"],
            "shift_end": auto["shift_end"], "break_minutes": str(auto["break_minutes"]), "comment": "", "lines": lines}
    chk = ui.backend.reportCheck(form)
    assert [ln.get("source") for ln in chk["data"]["lines"]] == ["measured", None, "estimated"]
    ui.backend.saveReport("t-save", form)
    ui.wait_idle()
    auto = next(r for r in _wv(ui)["mine"] if r["id"] == auto["id"])
    assert auto["status"] == "self_check" and [ln["source"] for ln in auto["lines"]] == ["measured", "", "estimated"]
    sf = _popup(ui, "reportSubmitForm")
    _call(sf, "openFor")
    sf.setProperty("marks", {"shift": True, "lines": True, "hours": True})
    ui.wait_idle()
    _confirm(ui, sf)
    assert next(r for r in _wv(ui)["mine"] if r["id"] == auto["id"])["status"] == "submitted"
    assert qml_warnings == []


def test_overtime_period_and_request_form_submit_through_ui(ui, qml_warnings):  # noqa: F811
    """A-55/A-56: administrator configures an accounting period, employee submits an explicit request, UI shows the frozen period."""
    ui.login("admin")
    ui.go("worktime")
    panel = ui.find("overtimePanel")
    assert panel.isVisible()
    # D-76: период уже задан демо-данными (календарный месяц) — виден сразу, вместе с нормой по календарю;
    # администратор может сменить режим через UI (квартал — и обратно месяц, чтобы заявка ниже была в декабре).
    assert _wv(ui)["overtime_period_label"] == "Календарный месяц"
    assert "Норма периода" in (_wv(ui)["overtime_norm_label"] or "")
    ui.backend.setOvertimePeriod("overtime-period-set", "quarter", 0)
    ui.wait_idle()
    assert _wv(ui)["overtime_period_label"] == "Календарный квартал"
    ui.backend.setOvertimePeriod("overtime-period-set", "month", 0)
    ui.wait_idle()
    assert _wv(ui)["overtime_period_label"] == "Календарный месяц"
    ui.backend.logout(); ui.wait_idle()
    ui.login("d.kuznetsov")
    ui.go("worktime")
    form = _popup(ui, "overtimeRequestForm")
    _call(form, "openFor")
    ui.wait_idle()
    ui.find("overtimeWorkDate").setProperty("text", "2026-12-01")
    ui.find("overtimeStartAt").setProperty("text", "2026-12-01T18:00:00+03:00")
    ui.find("overtimeEndAt").setProperty("text", "2026-12-01T20:00:00+03:00")
    ui.find("overtimeMinutes").setProperty("text", "120")
    ui.find("overtimeReason").setProperty("text", "Неотложная подготовка испытаний")
    # D-74: основание обязательно — без выбора форма не активна; с согласием отклонить — активна.
    assert form.property("canConfirm") is False
    ui.find("overtimeGround").setProperty("currentIndex", 0)
    ui.find("overtimeConsent").setProperty("checked", True)
    ui.wait_idle()
    assert form.property("canConfirm") is True
    _confirm(ui, form)
    assert form.property("visible") is False, form.property("error")
    rows = _wv(ui)["overtime_requests"]
    assert len(rows) == 1 and rows[0]["period"]["start"] == "2026-12-01"
    assert rows[0]["ground"] == "finish_urgent" and rows[0]["consent_confirmed"] is True
    assert rows[0]["status_label"] == "Ожидает решения" and "amount" not in rows[0]
    request_id = rows[0]["id"]
    ui.backend.logout(); ui.wait_idle()
    ui.login("e.orlova")
    ui.go("worktime")
    manager_rows = _wv(ui)["overtime_requests"]
    manager_request = next(x for x in manager_rows if x["id"] == request_id)
    assert manager_request["is_own"] is False and manager_request["status"] == "pending"
    approve = next((item for item in ui.items("objectName")
                    if item.objectName() == "overtimeApprove_" + request_id), None)
    assert approve is not None and approve.isVisible()
    ui.click(approve)
    manager_request = next(x for x in _wv(ui)["overtime_requests"] if x["id"] == request_id)
    assert manager_request["status"] == "approved"
    ui.backend.logout(); ui.wait_idle()
    ui.login("admin")
    ui.go("worktime")
    close_button = next((item for item in ui.items("objectName")
                         if item.objectName() == "overtimeClose_" + request_id), None)
    assert close_button is not None and close_button.isVisible()
    ui.click(close_button)
    close_form = _popup(ui, "overtimeCloseForm")
    ui.find("overtimeCloseReason").setProperty("text", "Проверено руководителем по документам")
    ui.wait_idle()
    assert close_form.property("canConfirm") is True
    _confirm(ui, close_form)
    closed = _wv(ui)["overtime_closures"]
    assert len(closed) == 1 and closed[0]["revision"] == 1
    assert closed[0]["snapshot"]["total_minutes"] == 120 and len(closed[0]["content_sha256"]) == 64
    ui.click(close_button)
    ui.find("overtimeCloseReason").setProperty("text", "Исправление после повторной сверки")
    ui.wait_idle()
    assert close_form.property("title") == "Исправить закрытую ведомость"
    _confirm(ui, close_form)
    closed = _wv(ui)["overtime_closures"]
    assert len(closed) == 2 and closed[0]["revision"] == 2
    assert closed[0]["supersedes_id"] == closed[1]["id"]
    assert qml_warnings == []


def test_head_sees_overtime_and_holiday_marks(ui, qml_warnings):  # noqa: F811
    """D-49 (A-53 п. 3): v.zaitseva (руководитель коммерческой службы) видит в отправленных отчётах отметки дня —
    «Сверхурочно» у s.morozov и «Работа в выходной (праздничный) день» у o.nikitina: в списке и баннером в отчёте."""
    ui.login("v.zaitseva")
    v = _wv(ui)
    marked = {r["user_name"]: r for r in v["review"] if r["day_mark"]}
    over = next(r for r in marked.values() if r["day_mark"] == "overtime")
    off = next(r for r in marked.values() if r["day_mark"] in ("weekend", "holiday"))
    assert over["day_mark_label"] == "Сверхурочно — 3 ч 00 мин (норма по графику 8 ч 00 мин)"
    assert off["day_mark_label"].startswith("Работа в ") and off["day_mark_label"].endswith("— 4 ч 00 мин")
    assert over["day_mark_short"] == "сверхурочно 3 ч 00 мин"
    for r in (over, off):
        ui.go("worktime", r["id"])
        page = ui.find("worktimePage")
        page.setProperty("selectedId", r["id"])
        ui.wait_idle()
        banner = ui.find("reportDayMark")
        assert banner.isVisible() and banner.property("text").startswith(r["day_mark_label"])
    assert qml_warnings == []
