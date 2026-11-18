"""UI-проверки раздела «Оргструктура» (D-44): настоящий QML, сервер in-process, демо-данные.

Демо (vector.server.demo._seed_org): Головной офис → Инженерная дирекция (руководитель e.orlova) → Отдел АСУ ТП
(без руководителя: d.kuznetsov, m.lebedev); Производственный участок; Коммерческая служба (v.zaitseva); Филиал
Астрахань → Сервисный отдел (пустой); n.sokolov — вне оргструктуры.
"""
import pytest
from PySide6.QtCore import QMetaObject

from tests.ui.test_purchasing_ui import _call, _popup, _v, qml_warnings  # noqa: F401

pytestmark = pytest.mark.ui


def _page(ui):
    """Открыть «Оргструктура»; вернуть страницу (элемент с ov)."""
    ui.go("org")
    return next(i for i in ui.items("ov") if i.isVisible())


def test_engineer_sees_tree_and_who_reviews_reports(ui, qml_warnings):  # noqa: F811
    """Инженер: пункт меню есть, дерево по уровням, «отчёты проверяет Орлова Елена», править нельзя; поиск."""
    ui.login("d.kuznetsov")
    assert ui.nav()["Оргструктура"] is True
    page = _page(ui)
    ov = _v(page, "ov")
    names = [(r["name"], r["depth"]) for r in ov["rows"]]
    # Корни и подчинённые — по алфавиту: головной офис, его подразделения, затем филиал.
    assert names == [("Головной офис", 0), ("Инженерная дирекция", 1), ("Отдел АСУ ТП", 2), ("Коммерческая служба", 1),
                     ("Производственный участок", 1), ("Филиал Астрахань", 0), ("Сервисный отдел", 1)]
    assert ov["mine"]["text"].endswith("Дневные отчёты проверяет Орлова Елена.")
    assert "Отдел АСУ ТП" in page.property("subtitle")
    assert ov["manage"] is False and not ui.find("btnNewUnit").isVisible()
    asu = next(r for r in ov["rows"] if r["name"] == "Отдел АСУ ТП")
    page.setProperty("selectedId", asu["id"])
    ui.wait_idle()
    assert ui.find("orgUnitTitle").property("text") == "Отдел АСУ ТП"
    assert ui.find("orgUnitPath").property("v") == "Головной офис › Инженерная дирекция › Отдел АСУ ТП"
    assert [m["name"] for m in _v(page, "sel")["members_list"]] == ["Кузнецов Дмитрий", "Лебедев Максим"]
    page.setProperty("search", "Белова")
    ui.wait_idle()
    assert [r["name"] for r in _v(page, "ov")["rows"]] == ["Головной офис", "Коммерческая служба"]
    assert qml_warnings == []


def test_admin_creates_unit_and_places_employee(ui, qml_warnings):  # noqa: F811
    """Администратор: новое подразделение в филиале через форму, затем включает в него сотрудника вне оргструктуры."""
    ui.login("admin")
    page = _page(ui)
    ov = _v(page, "ov")
    assert (ov["manage"], ov["can_manage"]) == (True, True)
    assert "Соколов Никита" in [p["label"] for p in ov["unplaced"]]
    astr = next(r for r in ov["rows"] if r["name"] == "Филиал Астрахань")
    form = _popup(ui, "orgUnitForm")
    _call(form, "openFor", None, astr["id"])
    ui.wait_idle()
    assert form.property("canConfirm") is False
    _popup(ui, "uName").setProperty("text", "Отдел наладки")
    _popup(ui, "uCode").setProperty("text", "02.2")
    ui.wait_idle()
    assert form.property("canConfirm") is True
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert not form.property("visible"), form.property("error")
    ov = _v(page, "ov")
    new = next(r for r in ov["rows"] if r["name"] == "Отдел наладки")
    assert new["depth"] == 1 and page.property("selectedId") == new["id"]
    mf = _popup(ui, "orgMemberForm")
    _call(mf, "openFor", "", new["id"], "")
    ui.wait_idle()
    users = _v(page, "ov")["people_options"]
    _popup(ui, "mUser").setProperty("currentIndex", next(i for i, p in enumerate(users) if p["label"] == "Соколов Никита"))
    _popup(ui, "mPos").setProperty("text", "Наладчик")
    ui.wait_idle()
    QMetaObject.invokeMethod(mf, "confirmed")
    ui.wait_idle()
    assert not mf.property("visible"), mf.property("error")
    card = _v(page, "ov")["cards"][new["id"]]
    assert [(m["name"], m["position"]) for m in card["members_list"]] == [("Соколов Никита", "Наладчик")]
    assert "Соколов Никита" not in [p["label"] for p in _v(page, "ov")["unplaced"]]
    assert qml_warnings == []


def test_unit_head_without_role_reviews_own_staff(ui, qml_warnings):  # noqa: F811
    """D-45 (ответ на A-50 п. 2): v.zaitseva руководит «Коммерческой службой» без роли «Руководитель отдела» — у неё
    в «Рабочем времени» есть вкладка «На проверке»; отчёты i.belova теперь проверяет она."""
    ui.login("v.zaitseva")
    ui.go("worktime")
    wv = _v(ui.backend, "worktimeOverview")
    assert wv["reviewer"] is True and [t["id"] for t in wv["tabs"]] == ["mine", "review"]
    ui.backend.logout()
    ui.wait_idle()
    ui.login("i.belova")
    page = _page(ui)
    assert _v(page, "ov")["mine"]["text"].endswith("Дневные отчёты проверяет Зайцева Вера.")
    assert qml_warnings == []
