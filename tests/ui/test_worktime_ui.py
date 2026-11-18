"""UI-проверки раздела «Рабочее время» (этап 7, D-42) — настоящий QML, сервер in-process, демо-данные.

Демо (_seed_worktime): у d.kuznetsov отчёт за вчера на проверке и черновик за сегодня; у m.lebedev отчёт за позавчера
принят, за вчера — возвращён с причиной. Проверяет руководитель отдела e.orlova.
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
    assert [r["status"] for r in v["mine"]] == ["draft", "submitted"] and v["tabs"] == []
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
