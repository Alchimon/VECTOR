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


def test_admin_schedules_popup_new_schedule_and_assign(ui, qml_warnings):  # noqa: F811
    """Графики работы (D-47, A-51 п. 1): администратор открывает «Графики работы» — три демо-графика; заводит новый
    через форму (с проверкой времени на сервере), назначает его «Сервисному отделу» и отмечает сотруднику «учёт не
    ведётся»; карточки подразделения и сотрудника показывают действующий график."""
    ui.login("admin")
    page = _page(ui)
    ov = _v(page, "ov")
    assert (ov["schedule_manage"], ov["can_schedule"]) == (True, True)
    assert [s["name"] for s in ov["schedules"]] == ["Производство", "Пятидневка", "Смена наладчиков"]
    assert ov["default_schedule"].startswith("Пятидневка: 08:00–17:00, пн–пт")
    ui.click(ui.find("btnSchedules"))
    pop = _popup(ui, "schedulesPopup")
    assert pop.property("visible") is True
    form = _popup(ui, "scheduleForm")
    _call(form, "openFor", None)
    ui.wait_idle()
    assert form.property("title") == "Новый график работы" and form.property("canConfirm") is False
    _popup(ui, "sName").setProperty("text", "Сервис 10–19")
    _popup(ui, "sStart").setProperty("text", "10:00")
    _popup(ui, "sEnd").setProperty("text", "25:00")                    # неверное время — отказ сервера в форме
    ui.wait_idle()
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert form.property("visible") is True and form.property("error")
    _popup(ui, "sEnd").setProperty("text", "19:00")
    ui.wait_idle()
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert not form.property("visible"), form.property("error")
    ov = _v(page, "ov")
    new = next(s for s in ov["schedules"] if s["name"] == "Сервис 10–19")
    assert new["label"] == "10:00–19:00, пн–пт, перерыв 60 мин" and new["is_default"] is False
    pop.close()
    # Назначить подразделению.
    svc_unit = next(r for r in ov["rows"] if r["name"] == "Сервисный отдел")
    page.setProperty("selectedId", svc_unit["id"])
    ui.wait_idle()
    assert ui.find("btnUnitSchedule").isVisible()
    af = _popup(ui, "scheduleAssignForm")
    _call(af, "openFor", "unit", svc_unit["id"], "Сервисный отдел", "")
    ui.wait_idle()
    assert af.property("title") == "График подразделения"
    opts = _v(page, "ov")["schedule_unit_options"]
    _popup(ui, "saSchedule").setProperty("currentIndex", next(i for i, o in enumerate(opts) if o["id"] == new["id"]))
    ui.wait_idle()
    QMetaObject.invokeMethod(af, "confirmed")
    ui.wait_idle()
    assert not af.property("visible"), af.property("error")
    card = _v(page, "ov")["cards"][svc_unit["id"]]
    assert card["schedule_label"].startswith("Сервис 10–19") and card["schedule_own_id"] == new["id"]
    assert ui.find("orgUnitSchedule").property("v").startswith("Сервис 10–19")
    # Сотруднику — «учёт рабочего времени не ведётся».
    asu = next(r for r in ov["rows"] if r["name"] == "Отдел АСУ ТП")
    page.setProperty("selectedId", asu["id"])
    ui.wait_idle()
    m = next(x for x in _v(page, "sel")["members_list"] if x["name"] == "Лебедев Максим")
    assert "ежедневно" in m["schedule"]                                # «Смена наладчиков» отдела
    _call(af, "openFor", "user", m["user_id"], m["name"], m["schedule_own_id"])
    ui.wait_idle()
    assert af.property("title") == "График сотрудника"
    uopts = _v(page, "ov")["schedule_user_options"]
    _popup(ui, "saSchedule").setProperty("currentIndex", next(i for i, o in enumerate(uopts) if o["id"] == "__exempt__"))
    ui.wait_idle()
    QMetaObject.invokeMethod(af, "confirmed")
    ui.wait_idle()
    assert not af.property("visible"), af.property("error")
    m = next(x for x in _v(page, "sel")["members_list"] if x["name"] == "Лебедев Максим")
    assert m["schedule"] == "учёт рабочего времени не ведётся" and m["schedule_own_id"] == "__exempt__"
    assert qml_warnings == []


def test_engineer_sees_schedules_read_only(ui, qml_warnings):  # noqa: F811
    """Инженер видит справочник графиков и свой график в карточке, но не может их менять."""
    ui.login("d.kuznetsov")
    page = _page(ui)
    ov = _v(page, "ov")
    assert ov["schedule_manage"] is False and len(ov["schedules"]) == 3
    assert ui.find("btnSchedules").isVisible() and not ui.find("btnNewSchedule").property("visible")
    asu = next(r for r in ov["rows"] if r["name"] == "Отдел АСУ ТП")
    page.setProperty("selectedId", asu["id"])
    ui.wait_idle()
    assert not ui.find("btnUnitSchedule").isVisible()
    assert ui.find("orgUnitSchedule").property("v").startswith("Смена наладчиков")
    assert qml_warnings == []


def test_production_calendar_popup_file_import_and_day_edit(ui, qml_warnings):  # noqa: F811
    """Производственный календарь (D-48, A-52 п. 3): из «Графиков работы» администратор открывает календарь — текущий
    год загружен (демо), следующий встроенный; загружает следующий год из файла; правит день через форму (неверная
    дата — кнопка неактивна); у нового графика отметка «учитывать календарь» включена; у «Смены наладчиков» — нет."""
    from importlib import resources
    from pathlib import Path
    ui.login("admin")
    page = _page(ui)
    ov = _v(page, "ov")
    shift = next(s for s in ov["schedules"] if s["name"] == "Смена наладчиков")
    assert shift["use_calendar"] is False and "без производственного календаря" in shift["label"]
    form = _popup(ui, "scheduleForm")
    _call(form, "openFor", None)
    ui.wait_idle()
    assert _popup(ui, "sCalendar").property("checked") is True
    form.close()
    ui.click(ui.find("btnSchedules"))
    ui.click(ui.find("btnCalendar"))
    pop = _popup(ui, "calendarPopup")
    assert pop.property("visible") is True
    cv = _v(pop, "cv")
    cur = cv["year"]
    assert [y["selected"] for y in cv["years"]] == [False, True, False] and cv["year_state"] == "loaded"
    assert cv["manage"] is True and cv["can_manage"] is True and cv["rows"]
    assert any(r["kind"] == "holiday" for r in cv["rows"]) and ui.find("btnCalendarDownload").property("visible")
    # Следующий год — из файла (тот же формат xmlcalendar.ru, что скачивается из интернета).
    nxt = cur + 1
    src = resources.files("vector.core").joinpath(f"prodcal_data/ru-{nxt}.xml")
    ui.backend.setCalendarYear(nxt)
    ui.wait_idle()
    assert _v(pop, "cv")["year_state"] in ("builtin", "missing")
    if src.is_file():
        ui.backend.calendarImportFile("calendar-file", nxt, str(Path(str(src))))
        ui.wait_idle()
        cv = _v(pop, "cv")
        assert cv["year"] == nxt and cv["year_state"] == "loaded" and "файл ru-" in cv["year_label"]
    # Правка дня: неверная дата — нельзя сохранить; верная — день в списке с пометкой «правка администратора».
    ui.backend.setCalendarYear(cur)
    ui.wait_idle()
    df = _popup(ui, "calendarDayForm")
    _call(df, "openFor", None)
    ui.wait_idle()
    _popup(ui, "calDayDate").setProperty("text", "31.02." + str(cur))
    ui.wait_idle()
    assert df.property("canConfirm") is False
    _popup(ui, "calDayDate").setProperty("text", f"15.07.{cur}")
    _popup(ui, "calDayTitle").setProperty("text", "День компании")
    ui.wait_idle()
    QMetaObject.invokeMethod(df, "confirmed")
    ui.wait_idle()
    assert not df.property("visible"), df.property("error")
    row = next(r for r in _v(pop, "cv")["rows"] if r["date"] == f"{cur}-07-15")
    assert (row["kind"], row["title"], row["manual"], row["origin_label"]) == ("holiday", "День компании", True,
                                                                              "правка администратора")
    assert qml_warnings == []


def test_engineer_sees_calendar_read_only(ui, qml_warnings):  # noqa: F811
    """Инженер видит календарь (годы, особые дни), но кнопок загрузки и правки у него нет."""
    ui.login("d.kuznetsov")
    _page(ui)
    ui.click(ui.find("btnSchedules"))
    ui.click(ui.find("btnCalendar"))
    pop = _popup(ui, "calendarPopup")
    cv = _v(pop, "cv")
    assert cv["manage"] is False and cv["rows"] and cv["auto_note"] == ""
    assert not ui.find("btnCalendarDownload").property("visible") and not ui.find("btnCalendarDay").property("visible")
    assert qml_warnings == []
