"""Производственный календарь (этап 7, R-20; D-48 — ответ владельца на A-52 п. 3).

Часы сервиса — FakeClock (2026-11-16 09:00 UTC, понедельник); компьютер сотрудника — +03:00. Календари — встроенные
в программу файлы xmlcalendar.ru (vector/core/prodcal_data).
"""

from conftest import new_id
from test_org import org
from test_schedule import sched
from test_workday import at, start
from test_worktime import pull

from vector.core import prodcal as pc


def xml_of(year):
    """Текст встроенного календаря на год (как скачанный с xmlcalendar.ru)."""
    from importlib import resources
    return resources.files("vector.core").joinpath(f"prodcal_data/ru-{year}.xml").read_text(encoding="utf-8")


def cal(s):
    """Производственный календарь из pull сессии s."""
    return pull(s)["work_calendar"]


def test_import_admin_only_checks_replace_and_pull(world):
    """Загрузить календарь на год — администратор; проверки файла (год, DOCTYPE, мусор); повторная загрузка заменяет
    дни; pull — всем: особые дни на три года, откуда календарь на год; администратору — какие годы загрузить самой."""
    a = world.admin
    eng = world.login("eng1")
    x26 = xml_of(2026)
    assert eng.cmd("calendar.import", {"year": 2026, "xml": x26})["status"] == "denied"
    for p, code in (({"year": 2027}, "wrong_year"), ({"xml": "<html/>"}, "invalid_xml"),
                    ({"xml": "<!DOCTYPE x [<!ENTITY a 'b'>]><calendar year='2026'/>"}, "unsafe_xml"),
                    ({"xml": "<calendar year='2026'><days><day d='02.30' t='1'/></days></calendar>"}, "invalid_day"),
                    ({"xml": "<calendar year='2026'><days/></calendar>"}, "empty"),
                    ({"year": 1999}, "invalid_year"), ({"xml": "x" * 70000}, "too_large")):
        assert a.cmd("calendar.import", {"year": 2026, "xml": x26, **p})["code"] == code, p
    # До загрузки — встроенный календарь; администратору программа предлагает загрузить 2026 и (с октября) 2027.
    c = cal(eng)
    assert [y["state"] for y in c["years"]] == ["builtin", "builtin", "builtin"]
    assert c["days"]["2026-01-01"]["kind"] == "holiday" and c["days"]["2026-04-30"]["kind"] == "short"
    assert (c["auto_years"], c["manage"]) == ([], False)
    assert cal(a)["auto_years"] == [2026, 2027] and cal(a)["manage"] is True
    r = a.ok("calendar.import", {"year": 2026, "xml": x26, "source": "https://xmlcalendar.ru"})["work_calendar_year"]
    assert (r["days_count"], r["counts"]["short"], r["replaced"], r["version"]) == (22, 4, False, 1)
    c = cal(eng)
    y26 = next(y for y in c["years"] if y["year"] == 2026)
    assert y26["state"] == "loaded" and "xmlcalendar.ru" in y26["label"] and "30.09.2025" in y26["label"]
    assert c["days"]["2026-01-01"] == {"kind": "holiday", "title": "Новогодние каникулы", "origin": "import"}
    assert cal(a)["auto_years"] == [2027]
    # Повторная загрузка (исправленный файл без 31 декабря) заменяет дни года целиком.
    fixed = x26.replace('<day d="12.31" t="1" f="01.04"/>', "").replace('<day d="12.31" t="1" f="01.04" />', "")
    assert fixed != x26
    r = a.ok("calendar.import", {"year": 2026, "xml": fixed})["work_calendar_year"]
    assert (r["days_count"], r["replaced"], r["version"]) == (21, True, 2)
    assert "2026-12-31" not in cal(eng)["days"]
    import sqlalchemy as sa
    from vector.server import db
    with world.svc.engine.connect() as c:
        rows = c.execute(sa.select(db.audit_log.c.details_json).where(db.audit_log.c.command == "calendar.import",
                                                                      db.audit_log.c.result == "accepted")).all()
    assert len(rows) == 2 and all("WorkCalendarImported" in r[0] for r in rows)


def test_manual_days_survive_reimport(world):
    """Ручная правка дня — важнее календаря и остаётся после повторной загрузки; normal снимает праздник; пусто —
    убирает правку; проверки; версия правки."""
    a = world.admin
    eng = world.login("eng1")
    assert eng.cmd("calendar.day_set", {"date": "2026-12-30", "kind": "short"})["status"] == "denied"
    for p, code in (({"date": "30.12.2026"}, "invalid_date"), ({"kind": "party"}, "invalid_kind"),
                    ({"title": "x" * 121}, "too_long")):
        assert a.cmd("calendar.day_set", {"date": "2026-12-30", "kind": "short", **p})["code"] == code, p
    r = a.ok("calendar.day_set", {"date": "2026-12-30", "kind": "short", "title": "Решение директора"})
    assert r["work_calendar_override"]["kind_label"] == "сокращённый на 1 час"
    a.ok("calendar.day_set", {"date": "2026-11-04", "kind": "normal"})               # компания работает 4 ноября
    a.ok("calendar.import", {"year": 2026, "xml": xml_of(2026)})
    d = cal(eng)["days"]
    assert d["2026-12-30"] == {"kind": "short", "title": "Решение директора", "origin": "manual"}
    assert "2026-11-04" not in d
    assert a.cmd("calendar.day_set", {"date": "2026-12-30", "kind": "holiday"}, expected_version=7)["code"] == \
        "version_conflict"
    a.ok("calendar.day_set", {"date": "2026-11-04"})                                  # убрать правку
    d = cal(eng)["days"]
    assert d["2026-11-04"]["kind"] == "holiday" and len(cal(eng)["overrides"]) == 1


def test_calendar_moves_schedule_day(world):
    """Окно рабочего дня по графику с календарём: праздник — нерабочий (фактическое начало не указывается); рабочая
    суббота по переносу — рабочая; график без календаря (смена) праздников не знает."""
    a, u = world.admin, world.users
    org(world)
    s = sched(a, "Пятидневка", "08:00", "17:00", is_default=True)
    a.ok("calendar.day_set", {"date": "2026-11-16", "kind": "holiday", "title": "Праздник города"})
    eng = world.login("eng1")
    wid = new_id()
    start(eng, world, wid)                                       # понедельник 12:00 по часам компьютера, но праздник
    p = {"id": new_id(), "workday_id": wid, "reason": "Был на объекте", "start_at": at(world, hours=-3)}
    assert eng.cmd("workday.start_request", p)["code"] == "not_schedule_day"
    # График без календаря: тот же день — рабочий по дням недели.
    a.ok("schedule.save", {"id": s, "name": "Пятидневка", "start_time": "08:00", "end_time": "17:00",
                           "break_minutes": 60, "weekdays": "1111100", "is_default": True, "use_calendar": False},
         expected_version=1)
    lst = {x["id"]: x for x in pull(eng)["work_schedules"]}
    assert lst[s]["use_calendar"] is False and "без производственного календаря" in lst[s]["label"]
    eng.ok("workday.start_request", {**p, "id": new_id()})
    # Рабочая суббота по переносу (21 ноября) — у графика с календарём рабочая.
    a.ok("schedule.save", {"id": s, "name": "Пятидневка", "start_time": "08:00", "end_time": "17:00",
                           "break_minutes": 60, "weekdays": "1111100", "is_default": True, "use_calendar": True},
         expected_version=2)
    assert {x["id"]: x for x in pull(eng)["work_schedules"]}[s]["use_calendar"] is True
    a.ok("calendar.day_set", {"date": "2026-11-21", "kind": "workday"})
    world.clock.advance(days=5)                                   # суббота 2026-11-21 12:00 (+03:00)
    eng = world.login("eng1")
    w2 = new_id()
    start(eng, world, w2, day="2026-11-21")
    r = eng.ok("workday.start_request", {"id": new_id(), "workday_id": w2, "reason": "Перенос",
                                         "start_at": at(world, hours=-4)})["workday_start_request"]
    assert r["requested_start_at"] == "2026-11-21T05:00:00.000000Z"
    assert u["eng1"]


def test_builtin_years_cover_now():
    """Встроенные календари покрывают текущий и следующий год (иначе выпуск программы без свежего календаря)."""
    ys = pc.builtin_years()
    assert {2025, 2026, 2027} <= set(ys)
