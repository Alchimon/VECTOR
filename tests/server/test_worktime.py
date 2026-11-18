"""Рабочее время и дневной отчёт (этап 7, R-20; D-42): создание, правка, отправка, проверка руководителем отдела,
исправление после отправки, права, «четыре глаза», журнал шагов, уведомления и pull.
"""
import sqlalchemy as sa

from conftest import new_id
from test_tasks_documents import proj  # noqa: F401
from vector.server import db


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
    rep = eng.ok("report.submit", {"id": rid}, expected_version=1)["daily_report"]
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
    eng.ok("report.submit", {"id": rid}, expected_version=4)
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
    assert create(eng, day=next(d), shift_end="08:00")["code"] == "shift_order"
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
    r = eng.cmd("report.submit", {"id": rid}, expected_version=1)
    assert r["code"] == "shift_required" and len(r["details"]["problems"]) == 2
    eng.ok("report.save", {"id": rid, "shift_start": "09:00", "shift_end": "18:00", "break_minutes": 60}, expected_version=1)
    assert eng.cmd("report.submit", {"id": rid}, expected_version=2)["code"] == "no_lines"
    eng.ok("report.save", {"id": rid, "lines": [{"minutes": 500, "note": "Работа"}]}, expected_version=2)
    assert eng.cmd("report.submit", {"id": rid}, expected_version=3)["code"] == "over_shift"
    eng.ok("report.save", {"id": rid, "lines": [{"minutes": 420, "note": "Работа"}]}, expected_version=3)
    rep = eng.ok("report.submit", {"id": rid}, expected_version=4)["daily_report"]
    assert rep["status"] == "submitted" and rep["total_minutes"] == 420     # меньше смены — можно


def test_only_author_edits_and_four_eyes_review(world, proj):  # noqa: F811
    """Чужой отчёт не правится и не отправляется; проверяет только руководитель отдела и не свой отчёт."""
    eng, head = proj.eng, world.login("head1")
    rid = create(eng)["result"]["daily_report"]["id"]
    lead = world.login("lead1")
    assert lead.cmd("report.save", {"id": rid, "comment": "x"}, expected_version=1)["code"] == "not_own_report"
    assert head.cmd("report.submit", {"id": rid}, expected_version=1)["code"] == "not_own_report"
    # Черновик не проверяется.
    assert head.cmd("report.review", {"id": rid, "decision": "accept"}, expected_version=1)["code"] == "report_state"
    eng.ok("report.submit", {"id": rid}, expected_version=1)
    assert lead.cmd("report.review", {"id": rid, "decision": "accept"}, expected_version=2)["status"] == "denied"
    assert proj.rp.cmd("report.review", {"id": rid, "decision": "accept"}, expected_version=2)["status"] == "denied"
    assert head.cmd("report.review", {"id": rid, "decision": "maybe"}, expected_version=2)["code"] == "invalid_decision"
    # Свой отчёт руководитель отдела не принимает.
    hid = create(head)["result"]["daily_report"]["id"]
    head.ok("report.submit", {"id": hid}, expected_version=1)
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
    eng.ok("report.submit", {"id": rid}, expected_version=1)
    head.ok("report.review", {"id": rid, "decision": "accept"}, expected_version=2)
    assert eng.cmd("report.reopen", {"id": rid, "reason": ""}, expected_version=3)["code"] == "required"
    rep = eng.ok("report.reopen", {"id": rid, "reason": "Забыл совещание"}, expected_version=3)["daily_report"]
    assert rep["status"] == "draft" and rep["reviewed_by"] is None
    assert rep["history"][-1]["details"] == {"reason": "Забыл совещание", "from": "accepted"}
    assert notes(world, world.users["head1"], "report_reopened")
    eng.ok("report.save", {"id": rid, "lines": [{"minutes": 420, "note": "Схемы"}, {"minutes": 60, "note": "Совещание"}]},
           expected_version=4)
    assert eng.ok("report.submit", {"id": rid}, expected_version=5)["daily_report"]["total_minutes"] == 480
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
    eng.ok("report.submit", {"id": rid}, expected_version=1)
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
