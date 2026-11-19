"""Рабочее время и дневной отчёт (этап 7, R-20; D-42, D-43): создание, правка, самопроверка и отправка, проверка
руководителем отдела, исправление после отправки, права, «четыре глаза», журнал шагов, уведомления и pull; ночная
смена, отчёт, собранный программой, и действия сотрудника для него (ответы владельца на A-48).
"""
import sqlalchemy as sa

from conftest import new_id
from test_tasks_documents import proj  # noqa: F401
from vector.server import db


# Самопроверка перед отправкой — все отметки (D-43, A-48 п. 4).
SC = {"shift": True, "lines": True, "hours": True}


def pull(s):
    """Снимок pull пользователя."""
    r = s.query("pull")
    assert r["status"] == "accepted", r
    return r["result"]


def create(s, rid=None, day="2026-11-16", **kw):
    """Сырой ответ report.create (по умолчанию — полный отчёт 08:30–17:30, перерыв 60 мин, одна строка 8 ч)."""
    p = {"id": rid or new_id(), "work_date": day, "shift_start": "08:30", "shift_end": "17:30", "break_minutes": 60,
         "lines": [{"minutes": 480, "note": "Разработка схем"}]}
    p.update(kw)
    return s.cmd("report.create", p)


def notes(world, uid, kind):
    """Уведомления пользователя uid вида kind."""
    t = db.notifications
    with world.svc.engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(sa.select(t).where(t.c.user_id == uid, t.c.kind == kind))]


def test_weekly_metrics_are_aggregate_and_manager_scoped(world, proj):  # noqa: F811
    """D-50: прошлую полную неделю видит руководитель; обычный сотрудник получает пустой агрегат без личных данных."""
    unit_id = new_id()
    world.admin.ok("org.unit_create", {"id": unit_id, "kind": "department", "name": "Инженерный отдел",
                                       "head_user_id": world.users["head1"]})
    world.admin.ok("org.member_set", {"user_id": world.users["eng1"], "unit_id": unit_id})
    manager = pull(world.login("head1"))["worktime_weekly_metrics"]
    assert (manager["week_start"], manager["week_end"]) == ("2026-11-09", "2026-11-15")
    assert isinstance(manager["units"], list)
    assert all("user_id" not in unit and "employee" not in unit for unit in manager["units"])
    employee = pull(proj.eng)["worktime_weekly_metrics"]
    assert employee == {"week_start": None, "week_end": None, "units": []}
    rid = new_id()
    assert create(proj.eng, rid, day="2026-11-12")["status"] == "accepted"
    proj.eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)
    updated = pull(world.login("head1"))["worktime_weekly_metrics"]
    row = next((unit for unit in updated["units"] if unit["reports"] == 1), None)
    assert row is not None, updated
    assert row["minutes"] == 480 and row["submitted"] == 1
    assert all("user_id" not in unit for unit in updated["units"])


def test_full_cycle_submit_return_resubmit_accept(world, proj):  # noqa: F811
    """Черновик → на проверке → возвращён (с причиной) → правка → снова на проверке → принят; журнал и уведомления."""
    eng, head = proj.eng, world.login("head1")
    rid = new_id()
    r = create(eng, rid, lines=[{"project_id": proj.pid, "minutes": 300, "note": "Схема"},
                                {"minutes": 120, "note": "Совещание"}])
    assert r["status"] == "accepted", r
    rep = r["result"]["daily_report"]
    assert (rep["status"], rep["total_minutes"], rep["shift_start"], rep["version"]) == ("draft", 420, "08:30", 1)
    assert rep["lines"][0]["project_code"] == "П-100"
    # Черновик руководитель не видит.
    assert all(x["id"] != rid for x in pull(head)["daily_reports"])
    rep = eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)["daily_report"]
    assert rep["status"] == "submitted"
    assert notes(world, world.users["head1"], "report_submitted")
    assert [x["id"] for x in pull(head)["daily_reports"]] == [rid]
    # Отправленный отчёт автор не правит — только через исправление.
    assert eng.cmd("report.save", {"id": rid, "comment": "x"}, expected_version=2)["code"] == "report_state"
    # Возврат без причины — отказ; с причиной — уведомление автору.
    assert head.cmd("report.review", {"id": rid, "decision": "return"}, expected_version=2)["code"] == "required"
    rep = head.ok("report.review", {"id": rid, "decision": "return", "comment": "Укажите задачу"}, expected_version=2)["daily_report"]
    assert (rep["status"], rep["review_comment"]) == ("returned", "Укажите задачу")
    assert notes(world, world.users["eng1"], "report_returned")
    rep = eng.ok("report.save", {"id": rid, "comment": "Уточнил"}, expected_version=3)["daily_report"]
    assert rep["status"] == "returned" and rep["comment"] == "Уточнил"
    eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=4)
    rep = head.ok("report.review", {"id": rid, "decision": "accept"}, expected_version=5)["daily_report"]
    assert rep["status"] == "accepted" and rep["reviewed_by"] == world.users["head1"]
    assert notes(world, world.users["eng1"], "report_accepted")
    assert [h["decision"] for h in rep["history"]] == ["created", "submitted", "returned", "saved", "submitted", "accepted"]
    assert rep["history"][4]["details"]["resubmitted"] is True


def test_one_report_per_day_and_date_limits(world, proj):  # noqa: F811
    """Один отчёт на сотрудника за день; день не в будущем и не старше 31 дня; у другого сотрудника — свой отчёт."""
    eng = proj.eng
    assert create(eng)["status"] == "accepted"
    r = create(eng)
    assert r["code"] == "report_exists" and r["details"]["current"]["work_date"] == "2026-11-16"
    assert create(world.login("lead1"))["status"] == "accepted"
    assert create(eng, day="2026-11-18")["code"] == "date_in_future"
    assert create(eng, day="2026-11-17")["status"] == "accepted"      # запас — сутки (часовые пояса)
    assert create(eng, day="2026-10-15")["code"] == "date_too_old"
    assert create(eng, day="2026-10-16")["status"] == "accepted"
    assert create(eng, day="16.11.2026")["code"] == "invalid_date"


def test_validation_of_shift_and_lines(world, proj):  # noqa: F811
    """Смена — обе границы, конец позже начала, перерыв короче смены; строки — минуты, «что сделано», задача с проектом."""
    eng = proj.eng
    d = iter(f"2026-11-{n:02d}" for n in range(1, 16))
    assert create(eng, day=next(d), shift_end=None)["code"] == "invalid_time"
    assert create(eng, day=next(d), shift_start="25:00")["code"] == "invalid_time"
    assert create(eng, day=next(d), shift_end="08:30")["code"] == "shift_order"        # конец = начало
    assert create(eng, day=next(d), break_minutes=540)["code"] == "break_too_long"
    assert create(eng, day=next(d), break_minutes=-5)["code"] == "invalid_break"
    assert create(eng, day=next(d), lines=[{"minutes": 0, "note": "x"}])["code"] == "invalid_minutes"
    assert create(eng, day=next(d), lines=[{"minutes": 30, "note": " "}])["code"] == "required"
    assert create(eng, day=next(d), lines=[{"minutes": 30, "note": "x", "task_id": new_id()}])["code"] == "task_without_project"
    assert create(eng, day=next(d), lines=[{"minutes": 30, "note": "x", "extra": 1}])["code"] == "unknown_field"
    # Пустой черновик допустим (заполнить позже).
    r = eng.cmd("report.create", {"id": new_id(), "work_date": next(d)})
    assert r["status"] == "accepted" and r["result"]["daily_report"]["shift_start"] is None


def test_lines_only_in_own_projects_and_their_tasks(world, proj):  # noqa: F811
    """Проект строки — где сотрудник участник; задача — из этого проекта."""
    tid = new_id()
    proj.rp.ok("task.create", {"id": tid, "kit_id": proj.kit, "title": "Схема"})
    lead = world.login("lead1")                         # не в команде проекта
    r = create(lead, lines=[{"project_id": proj.pid, "minutes": 60, "note": "x"}])
    assert r["code"] == "not_project_member"
    assert pull(lead)["worktime_projects"] == []
    assert [p["code"] for p in pull(proj.eng)["worktime_projects"]] == ["П-100"]
    r = create(proj.eng, lines=[{"project_id": proj.pid, "task_id": new_id(), "minutes": 60, "note": "x"}])
    assert r["code"] == "task_invalid"
    r = create(proj.eng, lines=[{"project_id": proj.pid, "task_id": tid, "minutes": 60, "note": "x"}])
    assert r["result"]["daily_report"]["lines"][0]["task_title"] == "Схема"


def test_submit_requires_complete_report(world, proj):  # noqa: F811
    """Отправить можно только полный отчёт: смена, строки, часов не больше смены без перерыва."""
    eng = proj.eng
    rid = new_id()
    eng.ok("report.create", {"id": rid, "work_date": "2026-11-16"})
    r = eng.cmd("report.submit", {"id": rid, "self_check": SC}, expected_version=1)
    assert r["code"] == "shift_required" and len(r["details"]["problems"]) == 2
    eng.ok("report.save", {"id": rid, "shift_start": "09:00", "shift_end": "18:00", "break_minutes": 60}, expected_version=1)
    assert eng.cmd("report.submit", {"id": rid, "self_check": SC}, expected_version=2)["code"] == "no_lines"
    eng.ok("report.save", {"id": rid, "lines": [{"minutes": 500, "note": "Работа"}]}, expected_version=2)
    assert eng.cmd("report.submit", {"id": rid, "self_check": SC}, expected_version=3)["code"] == "over_shift"
    eng.ok("report.save", {"id": rid, "lines": [{"minutes": 420, "note": "Работа"}]}, expected_version=3)
    rep = eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=4)["daily_report"]
    assert rep["status"] == "submitted" and rep["total_minutes"] == 420     # меньше смены — можно


def test_only_author_edits_and_four_eyes_review(world, proj):  # noqa: F811
    """Чужой отчёт не правится и не отправляется; проверяет только руководитель отдела и не свой отчёт."""
    eng, head = proj.eng, world.login("head1")
    rid = create(eng)["result"]["daily_report"]["id"]
    lead = world.login("lead1")
    assert lead.cmd("report.save", {"id": rid, "comment": "x"}, expected_version=1)["code"] == "not_own_report"
    assert head.cmd("report.submit", {"id": rid, "self_check": SC}, expected_version=1)["code"] == "not_own_report"
    # Черновик не проверяется.
    assert head.cmd("report.review", {"id": rid, "decision": "accept"}, expected_version=1)["code"] == "report_state"
    eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)
    assert lead.cmd("report.review", {"id": rid, "decision": "accept"}, expected_version=2)["status"] == "denied"
    assert proj.rp.cmd("report.review", {"id": rid, "decision": "accept"}, expected_version=2)["status"] == "denied"
    assert head.cmd("report.review", {"id": rid, "decision": "maybe"}, expected_version=2)["code"] == "invalid_decision"
    # Свой отчёт руководитель отдела не принимает.
    hid = create(head)["result"]["daily_report"]["id"]
    head.ok("report.submit", {"id": hid, "self_check": SC}, expected_version=1)
    r = head.cmd("report.review", {"id": hid, "decision": "accept"}, expected_version=2)
    assert (r["status"], r["code"]) == ("denied", "four_eyes")
    # Никто чужих отчётов, кроме руководителя отдела, не видит.
    assert [x["id"] for x in pull(lead)["daily_reports"]] == []
    assert pull(lead)["worktime_reviewer"] is False and pull(head)["worktime_reviewer"] is True


def test_reopen_after_submit_needs_reason_and_notifies_reviewer(world, proj):  # noqa: F811
    """Исправление после отправки — только с основанием; принятый — с уведомлением принявшему; черновик снова."""
    eng, head = proj.eng, world.login("head1")
    rid = create(eng)["result"]["daily_report"]["id"]
    assert eng.cmd("report.reopen", {"id": rid, "reason": "x"}, expected_version=1)["code"] == "report_state"
    eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)
    head.ok("report.review", {"id": rid, "decision": "accept"}, expected_version=2)
    assert eng.cmd("report.reopen", {"id": rid, "reason": ""}, expected_version=3)["code"] == "required"
    rep = eng.ok("report.reopen", {"id": rid, "reason": "Забыл совещание"}, expected_version=3)["daily_report"]
    assert rep["status"] == "draft" and rep["reviewed_by"] is None
    assert rep["history"][-1]["details"] == {"reason": "Забыл совещание", "from": "accepted"}
    assert notes(world, world.users["head1"], "report_reopened")
    eng.ok("report.save", {"id": rid, "lines": [{"minutes": 420, "note": "Схемы"}, {"minutes": 60, "note": "Совещание"}]},
           expected_version=4)
    assert eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=5)["daily_report"]["total_minutes"] == 480
    # Исправление — только при связи (в каталоге), остальное автора — в очередь.
    cat = world.svc.contracts.catalog["commands"]
    assert cat["report.reopen"]["offline"] == "online_required" and cat["report.review"]["offline"] == "online_required"
    assert {cat[c]["offline"] for c in ("report.create", "report.save", "report.submit")} == {"queued"}


def test_deputy_dept_head_reviews_with_basis(world, proj):  # noqa: F811
    """Замещающий руководителя отдела проверяет отчёты; в истории — «за кого» (D-34)."""
    eng, head = proj.eng, world.login("head1")
    head.ok("delegation.create", {"to_user_id": world.users["lead1"], "role": "dept_head", "valid_until": "2026-11-30",
                                  "reason": "Отпуск"})
    rid = create(eng)["result"]["daily_report"]["id"]
    eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)
    lead = world.login("lead1")
    assert notes(world, world.users["lead1"], "report_submitted")
    rep = lead.ok("report.review", {"id": rid, "decision": "accept"}, expected_version=2)["daily_report"]
    assert rep["history"][-1]["on_behalf_of"] == world.users["head1"]


def test_save_unchanged_and_version_conflict(world, proj):  # noqa: F811
    """Сохранение без изменений — unchanged без новой версии; устаревшая версия — конфликт."""
    eng = proj.eng
    rid = create(eng)["result"]["daily_report"]["id"]
    r = eng.ok("report.save", {"id": rid, "shift_start": "08:30"}, expected_version=1)
    assert r.get("unchanged") is True and r["daily_report"]["version"] == 1
    eng.ok("report.save", {"id": rid, "break_minutes": 30}, expected_version=1)
    assert eng.cmd("report.save", {"id": rid, "break_minutes": 45}, expected_version=1)["status"] == "conflict"


def test_history_query_and_immutable_events(world, proj):  # noqa: F811
    """Журнал шагов неизменяем (триггер БД); история через audit — автору."""
    eng = proj.eng
    rid = create(eng)["result"]["daily_report"]["id"]
    h = eng.query("history", object_type="daily_report", object_id=rid)
    assert h["status"] == "accepted", h
    assert world.login("lead1").query("history", object_type="daily_report", object_id=rid)["status"] == "denied"
    import pytest
    with pytest.raises(Exception):
        with world.svc.engine.begin() as c:
            c.execute(db.daily_report_events.update().values(decision="x"))


def test_night_shift_and_overlap_with_next_day(world, proj):  # noqa: F811
    """Ночная смена (A-48 п. 2): конец раньше начала — до утра следующего дня; смены соседних дней не пересекаются."""
    eng = proj.eng
    night, day2 = new_id(), new_id()
    rep = create(eng, night, day="2026-11-14", shift_start="22:00", shift_end="06:00", break_minutes=30,
                 lines=[{"minutes": 450, "note": "Пусконаладка ночью"}])["result"]["daily_report"]
    assert (rep["shift_start"], rep["shift_end"], rep["overnight"]) == ("22:00", "06:00", True)
    eng.ok("report.submit", {"id": night, "self_check": SC}, expected_version=1)
    # Следующий день начинается раньше, чем закончилась ночная смена, — отправить нельзя, пока не исправлено.
    create(eng, day2, day="2026-11-15", shift_start="05:00", shift_end="13:00", break_minutes=0,
           lines=[{"minutes": 480, "note": "Работа"}])
    r = eng.cmd("report.submit", {"id": day2, "self_check": SC}, expected_version=1)
    assert r["code"] == "shift_overlap" and "06:00" in r["message_ru"]
    eng.ok("report.save", {"id": day2, "shift_start": "07:00", "shift_end": "15:00",
                           "lines": [{"minutes": 480, "note": "Работа"}]}, expected_version=1)
    assert eng.ok("report.submit", {"id": day2, "self_check": SC}, expected_version=2)["daily_report"]["status"] == "submitted"
    with world.svc.engine.connect() as c:
        assert c.execute(sa.select(db.daily_reports.c.shift_end).where(db.daily_reports.c.id == night)).scalar() == 1800


def test_submit_requires_self_check(world, proj):  # noqa: F811
    """Без самопроверки (всех отметок) отчёт не отправляется; отметки — в журнале шагов."""
    eng = proj.eng
    rid = new_id()
    create(eng, rid)
    assert eng.cmd("report.submit", {"id": rid}, expected_version=1)["code"] == "self_check_required"
    r = eng.cmd("report.submit", {"id": rid, "self_check": {"shift": True, "lines": True}}, expected_version=1)
    assert r["code"] == "self_check_required" and "Часы в строках" in r["message_ru"]
    assert eng.cmd("report.submit", {"id": rid, "self_check": {**SC, "x": True}}, expected_version=1)["code"] == "unknown_field"
    rep = eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=1)["daily_report"]
    assert rep["history"][-1]["details"]["self_check"] == SC


def test_assembled_report_goes_to_self_check_then_head(world, proj):  # noqa: F811
    """Отчёт, собранный программой (A-48 п. 3): «На самопроверке», источники строк, уведомление автору; после правки и
    самопроверки — руководителю отдела; руководитель не видит отчёт до отправки."""
    eng, head = proj.eng, world.login("head1")
    rid = new_id()
    r = create(eng, rid, origin="auto", summary={"actions": 7, "measured_minutes": 90, "lines": 2},
               lines=[{"project_id": proj.pid, "minutes": 390, "note": "Изменить задачу: «Схема»", "source": "estimated"},
                      {"minutes": 90, "note": "Наряд № 2 · Сборка", "source": "measured"}])
    rep = r["result"]["daily_report"]
    assert (rep["status"], rep["origin"], rep["assembled"]["actions"]) == ("self_check", "auto", 7)
    assert [ln["source"] for ln in rep["lines"]] == ["estimated", "measured"]
    assert rep["history"][0]["label"] == "Собран программой"
    assert notes(world, world.users["eng1"], "report_assembled")
    assert all(x["id"] != rid for x in pull(head)["daily_reports"])
    assert head.query("history", object_type="daily_report", object_id=rid)["status"] == "denied"
    assert create(eng, origin="robot", day="2026-11-10")["code"] == "invalid_origin"
    assert create(eng, origin="auto", summary={"actions": -1}, day="2026-11-10")["code"] == "invalid_summary"
    assert create(eng, day="2026-11-10", lines=[{"minutes": 5, "note": "x", "source": "guess"}])["code"] == "invalid_source"
    # Сотрудник поправил оценку: строка стала «своей» (без источника); статус — всё ещё самопроверка.
    rep = eng.ok("report.save", {"id": rid, "lines": [{"project_id": proj.pid, "minutes": 360, "note": "Схема насосной"},
                                                    {"minutes": 90, "note": "Наряд № 2 · Сборка", "source": "measured"}]},
                 expected_version=1)["daily_report"]
    assert rep["status"] == "self_check" and [ln["source"] for ln in rep["lines"]] == [None, "measured"]
    eng.ok("report.submit", {"id": rid, "self_check": SC}, expected_version=2)
    assert [x["id"] for x in pull(head)["daily_reports"]] == [rid]
    with world.svc.engine.begin() as c:                        # отметка «собран программой» неизменяема
        try:
            with c.begin_nested():
                c.execute(db.daily_report_auto.update().values(summary_json="{}"))
            raise AssertionError("daily_report_auto изменилась")
        except sa.exc.DBAPIError:
            pass


def test_pull_activity_has_own_actions_projects_and_timers(world, proj):  # noqa: F811
    """pull «worktime_activity»: свои принятые действия (с проектом и задачей), без самого отчёта и уведомлений;
    чужих действий нет; таймеры — интервалы «в работе»."""
    rp, head = proj.rp, world.login("head1")
    tid = new_id()
    rp.ok("task.create", {"id": tid, "kit_id": proj.kit, "title": "Схема насосной"})
    create(rp)
    act = pull(rp)["worktime_activity"]
    cmds = [a["command"] for a in act["actions"]]
    assert "task.create" in cmds and not any(c.startswith("report.") for c in cmds)
    t = next(a for a in act["actions"] if a["command"] == "task.create")
    assert (t["project_id"], t["task_id"], t["project_code"], t["object"]) == (proj.pid, tid, "П-100", "«Схема насосной»")
    assert t["label"] == "Создать задачу" and t["operation_id"]
    assert all(a["command"] != "task.create" for a in pull(head)["worktime_activity"]["actions"])
    assert act["timers"] == []


def test_day_marks_holiday_weekend_overtime(world, proj):  # noqa: F811
    """Отметки дня в отчёте (D-49, A-53 п. 3) — считает сервер по графику автора (встроенный 09:00–18:00, пн–пт,
    перерыв 60 мин — норма 8 ч) и производственному календарю: праздник, выходной, сверхурочно; руководитель видит их
    в отправленном отчёте."""
    eng, head = proj.eng, world.login("head1")
    plain = create(eng)["result"]["daily_report"]["day_marks"]                       # 08:30–17:30 − 60 мин = 8 ч
    assert (plain["day_type"], plain["mark"], plain["norm_minutes"], plain["worked_minutes"]) == ("workday", "", 480, 480)
    rid = new_id()
    assert create(eng, rid, day="2026-11-17")["status"] == "accepted"
    eng.ok("report.save", {"id": rid, "shift_end": "19:30"}, expected_version=1)      # 08:30–19:30 − 60 мин = 10 ч
    over = [x for x in pull(eng)["daily_reports"] if x["id"] == rid][0]["day_marks"]
    assert (over["mark"], over["overtime_minutes"]) == ("overtime", 120)
    assert over["label"] == "Сверхурочно — 2 ч 00 мин (норма по графику 8 ч 00 мин)"
    sat = create(eng, day="2026-11-14", shift_start="10:00", shift_end="13:10", break_minutes=0,
                 lines=[{"minutes": 190, "note": "Срочная правка"}])["result"]["daily_report"]
    assert (sat["day_marks"]["mark"], sat["day_marks"]["label"]) == ("weekend", "Работа в выходной день — 3 ч 10 мин")
    hol_id = new_id()
    hol = create(eng, hol_id, day="2026-11-04", shift_start="10:00", shift_end="12:00", break_minutes=0,
                 lines=[{"minutes": 120, "note": "Авария на объекте"}])["result"]["daily_report"]["day_marks"]
    assert (hol["mark"], hol["offday_minutes"], hol["tone"]) == ("holiday", 120, "danger")
    assert hol["label"] == "Работа в праздничный день (День народного единства) — 2 ч 00 мин"
    eng.ok("report.submit", {"id": hol_id, "self_check": SC}, expected_version=1)
    seen = [x for x in pull(head)["daily_reports"] if x["id"] == hol_id][0]
    assert seen["day_marks"]["mark"] == "holiday"
