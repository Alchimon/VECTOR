"""Рабочее время на клиенте (Class A, SQLite; D-42): дневной отчёт — создание, правка и отправка без связи (очередь с
цепочкой версий, без пустых сохранений); принять / вернуть и исправление — только при связи; представление
worktime_view (вкладки, флаги, итоги «не распределено») и проверка формы без сервера (часы «1,5», «1:30»).
"""
import sys
from datetime import date
from pathlib import Path

# Фикстуры проекта — из серверных тестов (каталог tests/server добавляется в путь импорта).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from conftest import new_id  # noqa: E402
from test_offline_sync import BACKENDS, FAST_KDF, SERVER, mk  # noqa: E402,F401  (фикстура mk и общие константы)
from test_tasks_documents import proj  # noqa: E402,F401  (фикстура)
from vector.client import worktime_view as wv  # noqa: E402

_ = (BACKENDS, FAST_KDF, SERVER)
TODAY = date(2026, 11, 16)          # дата часов стенда (FakeClock)


def view(c):
    """Представление раздела «Рабочее время» из кэша клиента c."""
    reports = [{**o, "pending": bool(o.get("_pending"))} for o in c.list_local("daily_report")]
    return wv.worktime_view(reports, c.get_meta("worktime_projects") or [], c.list_local("task"),
                            {p["id"]: p["display_name"] for p in c.get_meta("people") or []},
                            c.get_meta("me") or c.get_meta("session") or {}, bool(c.get_meta("worktime_reviewer")),
                            c.t.online, c.get_meta("last_pull") or "", TODAY)


def test_offline_draft_chain_submit_then_online_review(world, proj, mk):  # noqa: F811
    """Инженер без связи: создаёт отчёт, дважды правит, отправляет — всё в очередь, цепочка версий; при связи — сервер
    принимает всё; руководитель отдела возвращает (онлайн), инженер правит и отправляет снова; принятие."""
    world.clock.advance(seconds=61)
    eng = mk("eng1")
    eng.pull()
    v = view(eng)
    assert v["tabs"] == [] and v["today_id"] == "" and [p["label"] for p in v["project_options"][1:]] == ["П-100 · Проект"]
    eng.t.online = False
    rid = new_id()
    base = {"id": rid, "work_date": "2026-11-16", "shift_start": "09:00", "shift_end": "18:00", "break_minutes": 60,
            "comment": None, "lines": [{"project_id": proj.pid, "task_id": None, "minutes": 300, "note": "Схемы"}]}
    assert eng.save_report_draft(base).status == "queued"
    row = view(eng)["mine"][0]
    assert row["pending"] and row["status"] == "draft" and row["total_label"] == "5 ч" and row["unallocated_label"] == "3 ч"
    assert row["lines"][0]["project_label"].startswith("П-100") and view(eng)["today_id"] == rid
    # Без изменений — в очередь не ставится.
    assert eng.save_report_draft(base).response.get("unchanged") is True and len(eng.queue()) == 1
    more = {**base, "lines": base["lines"] + [{"project_id": None, "task_id": None, "minutes": 180, "note": "Совещание"}]}
    assert eng.save_report_draft(more).status == "queued"
    row = view(eng)["mine"][0]
    assert row["total_label"] == "8 ч" and row["can_submit"] and row["unallocated_label"] == "0 мин"
    assert eng.submit_report(rid).status == "queued"
    row = view(eng)["mine"][0]
    assert row["status"] == "submitted" and row["may_reopen"] and not row["can_reopen"]       # исправить — при связи
    assert [q["command"] for q in eng.queue()] == ["report.create", "report.save", "report.submit"]
    assert [r[0] for r in eng.db.execute("SELECT expected_version FROM outbox ORDER BY seq")] == [None, 1, 2]
    eng.t.online = True
    assert eng.sync().sent == 3
    row = view(eng)["mine"][0]
    assert (row["status"], row["version"], row["pending"]) == ("submitted", 3, False)

    world.clock.advance(seconds=61)
    head = mk("head1")
    head.pull()
    hv = view(head)
    assert [t["id"] for t in hv["tabs"]] == ["mine", "review"] and hv["default_tab"] == "review"
    r = hv["review"][0]
    assert r["may_review"] and r["can_review"] and r["user_name"] == "ENG1" and hv["counts"]["to_review"] == 1
    head.t.online = False
    assert head.execute("report.review", {"id": rid, "decision": "accept"}, r["version"]).status == "refused_offline"
    head.t.online = True
    res = head.execute("report.review", {"id": rid, "decision": "return", "comment": "Добавьте задачу"}, r["version"])
    assert res.status == "accepted", res
    eng.pull()
    row = view(eng)["mine"][0]
    assert row["status"] == "returned" and row["review_comment"] == "Добавьте задачу" and row["can_edit"]
    assert view(eng)["counts"]["returned"] == 1
    assert eng.save_report_draft({**more, "comment": "Задача уточнена"}).status == "accepted"
    assert eng.submit_report(rid).status == "accepted"
    head.pull()
    r = view(head)["review"][0]
    assert head.execute("report.review", {"id": rid, "decision": "accept"}, r["version"]).status == "accepted"
    eng.pull()
    row = view(eng)["mine"][0]
    assert row["status"] == "accepted" and "HEAD1" in row["reviewed_label"]
    assert [h["label"] for h in row["history"]][-2:] == ["Отправлен на проверку", "Принят"]


def test_view_flags_four_eyes_and_reviewer_own_report():
    """Руководитель видит свой отчёт на проверке, но не может его проверить — и видит почему."""
    me = {"user_id": "h"}
    reps = [{"id": "1", "user_id": "h", "work_date": "2026-11-16", "status": "submitted", "shift_start": "09:00",
             "shift_end": "18:00", "break_minutes": 60, "lines": [{"minutes": 600, "note": "x"}], "version": 2},
            {"id": "2", "user_id": "e", "work_date": "2026-11-15", "status": "submitted", "shift_start": "09:00",
             "shift_end": "18:00", "break_minutes": 0, "lines": [{"minutes": 60, "note": "x"}], "version": 2},
            {"id": "3", "user_id": "e", "work_date": "2026-11-14", "status": "draft", "lines": [], "version": 1}]
    v = wv.worktime_view(reps, [], [], {"h": "Орлова", "e": "Кузнецов"}, me, True, True, "", TODAY)
    own = v["mine"][0]
    assert not own["may_review"] and "другой руководитель" in own["block_review"]
    assert own["unallocated_label"] == "превышение на 2 ч" and own["unallocated_tone"] == "danger"
    assert [x["id"] for x in v["review"]] == ["2"]              # черновик чужого — не на проверке
    assert v["review"][0]["can_review"] and v["week_label"] == "за 7 дней в отчётах: 10 ч"
    off = wv.worktime_view(reps, [], [], {}, me, True, False, "", TODAY)
    assert off["review"][0]["may_review"] and not off["review"][0]["can_review"]


def test_report_form_rules_without_server():
    """Форма: часы «1,5» / «1:30» / «2» → минуты; пустые строки пропускаются; замечания по строкам; итоги."""
    f = wv.report_form({"shift_start": "08:30", "shift_end": "17:30", "break_minutes": "60", "comment": "",
                        "lines": [{"project_id": "", "task_id": "", "hours": "1,5", "note": "Схема"},
                                  {"project_id": "", "task_id": "", "hours": "1:30", "note": "Совещание"},
                                  {"project_id": "", "task_id": "", "hours": "", "note": ""}]})
    assert f["ok"] and [ln["minutes"] for ln in f["data"]["lines"]] == [90, 90]
    assert f["totals"]["unallocated_label"] == "5 ч" and f["submit_problems"] == []
    f = wv.report_form({"shift_start": "08:30", "shift_end": "17:30", "break_minutes": "60",
                        "lines": [{"hours": "abc", "note": "x"}, {"hours": "2", "note": ""}]})
    assert not f["ok"] and set(f["rows"]) == {"0", "1"}
    f = wv.report_form({"shift_start": "18:00", "shift_end": "09:00", "break_minutes": "0", "lines": []})
    assert not f["ok"] and f["field"] == "shift_end"
    f = wv.report_form({"shift_start": "09:00", "shift_end": "18:00", "break_minutes": "x", "lines": []})
    assert not f["ok"] and f["field"] == "break_minutes"
    f = wv.report_form({"shift_start": "09:00", "shift_end": "18:00", "break_minutes": "60",
                        "lines": [{"hours": "10", "note": "x"}]})
    assert f["ok"] and f["totals"]["tone"] == "danger" and f["submit_problems"]      # сохранить можно, отправить — нет
