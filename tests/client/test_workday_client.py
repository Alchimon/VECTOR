"""Таймер рабочего дня на клиенте (Class A, SQLite; D-46 — ответы владельца на A-49 пп. 2–5): нажатия без связи
(очередь, время — часы компьютера), проверка перехода до очереди, окно «Начните рабочий день» и «Позже», «Я начал
раньше» с согласованием руководителя (решение — только при связи), представление workday_view и смена нового
отчёта из таймера.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Фикстуры — из серверных тестов (каталог tests/server добавляется в путь импорта).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from conftest import PWD  # noqa: E402
from test_offline_sync import BACKENDS, FAST_KDF, SERVER, mk  # noqa: E402,F401  (фикстура mk и общие константы)
from vector.client import workday_view as wv  # noqa: E402

_ = (BACKENDS, FAST_KDF, SERVER)
UTC = timezone.utc


def view(c, prompt=False):
    """Модель таймера из кэша клиента c (часы — FakeClock стенда, пояс — UTC)."""
    return wv.workday_view(c.list_local("workday"), c.list_local("workday_start_request"), c.user_id,
                           {p["id"]: p["display_name"] for p in c.get_meta("people") or []}, c.t.online,
                           c.now(), prompt, UTC)


def test_offline_marks_queue_live_timer_and_prompt(world, mk):
    """Без связи: окно «Начните рабочий день» → «Начать» → «Перерыв» → «Продолжить» — в очередь; таймер считает по
    секундам, перерыв — только нажатый; второй «Перерыв» подряд не ставится в очередь; при связи сервер принимает всё."""
    world.clock.advance(seconds=61)
    eng = mk("eng1")
    eng.pull()
    assert eng.workday_prompt_needed(UTC) is True
    v = view(eng, True)
    assert (v["status"], v["prompt"], v["can_start"], v["clock"]) == ("not_started", True, True, "00:00:00")
    eng.workday_prompt_later(UTC)
    assert eng.workday_prompt_needed(UTC) is False                      # «Позже» — сегодня окно не показывать
    eng.t.online = False
    assert eng.workday_mark("pause", UTC).response["code"] == "not_started"
    assert eng.workday_mark("start", UTC).status == "queued"
    world.clock.advance(hours=2, seconds=15)
    assert eng.workday_mark("pause", UTC).status == "queued"
    r = eng.workday_mark("pause", UTC)
    assert (r.status, r.response["code"]) == ("rejected", "invalid_transition")
    world.clock.advance(minutes=20)
    v = view(eng)
    assert (v["status"], v["can_resume"], v["can_finish"], v["running"]) == ("paused", True, True, False)
    assert v["clock"] == "02:00:15" and v["break_label"] == "20 мин" and v["unsent"] is True
    assert eng.workday_mark("resume", UTC).status == "queued"
    world.clock.advance(minutes=30)
    v = view(eng)
    assert v["clock"] == "02:30:15" and v["running"] is True
    assert [e["label"] for e in v["events"]] == ["Начал рабочий день", "Перерыв", "Продолжил работу"]
    assert [q["command"] for q in eng.queue()] == ["workday.mark"] * 3
    eng.t.online = True
    world.clock.advance(seconds=61)
    assert eng.login("eng1", PWD).status == "accepted"                  # сеанс истёк — снова войти тем же профилем
    eng.sync()
    assert eng.pending_count() == 0 and eng.problem_count() == 0
    v = view(eng)
    assert v["unsent"] is False and v["status"] == "working" and v["clock"] == "02:31:16"
    assert eng.workday_prompt_needed(UTC) is False


def test_earlier_start_request_offline_and_online_decision(world, mk):
    """«Я начал раньше»: проверка времени формы без сервера, запрос в очередь без связи; руководитель отдела (сотрудник
    вне оргструктуры) решает только при связи; после согласования таймер сотрудника — с фактического начала."""
    world.clock.advance(hours=1)                                         # 10:00 UTC
    eng = mk("eng1")
    eng.pull()
    assert eng.workday_mark("start", UTC).status == "accepted"
    v = view(eng)
    day = eng.list_local("workday")[0]
    assert v["can_request"] and v["request_default"] == "09:00"
    assert wv.parse_start_time("10:30", day, eng.now(), UTC)["message"].startswith("Фактическое начало должно быть раньше")
    assert wv.parse_start_time("8", day, eng.now(), UTC)["ok"] is False
    chk = wv.parse_start_time("08:00", day, eng.now(), UTC)
    assert chk["ok"] and chk["message"].startswith("К таймеру добавится 2 ч 00 мин")
    eng.t.online = False
    at = datetime.fromisoformat(chk["start_at"])
    assert eng.workday_start_request(day["id"], at, " ").response["code"] == "field_required"
    assert eng.workday_start_request(day["id"], at, "Был на объекте").status == "queued"
    v = view(eng)
    assert v["pending_request"]["start_label"] == "08:00" and not v["can_request"]
    assert eng.workday_start_request(day["id"], at, "Ещё раз").response["code"] == "already_pending"
    eng.t.online = True
    assert eng.sync().sent == 1

    world.clock.advance(seconds=61)                                      # вход head1 — не чаще раза в минуту
    head = mk("head1")
    head.pull()
    hv = view(head)
    r = hv["review"][0]
    assert (r["may_decide"], r["can_decide"], r["user_name"], hv["to_decide"]) == (True, True, "ENG1", 1)
    head.t.online = False
    assert head.execute("workday.start_decide", {"id": r["id"], "decision": "approve"}, r["version"]).status == "refused_offline"
    assert view(head)["review"][0]["block"] != ""
    head.t.online = True
    assert head.execute("workday.start_decide", {"id": r["id"], "decision": "approve"}, r["version"]).status == "accepted"
    eng.pull()
    v = view(eng)
    assert v["approved"] and v["started_label"] == "08:00" and v["pressed_label"] == "10:00"
    assert v["worked_seconds"] >= 2 * 3600 and "+2 ч 00 мин" in v["approved_label"]
    assert v["my_requests"][0]["status_label"] == "Согласовано"


def test_report_defaults_from_timer():
    """Смена нового отчёта из таймера: завершённый день — как натикало; идущий — до «сейчас»; без таймера — пусто."""
    t0 = datetime(2026, 11, 16, 8, 3, 40, tzinfo=UTC)
    ev = [{"seq": 1, "event": "start", "device_time": t0.isoformat()},
          {"seq": 2, "event": "pause", "device_time": (t0 + timedelta(hours=4)).isoformat()},
          {"seq": 3, "event": "resume", "device_time": (t0 + timedelta(hours=4, minutes=45)).isoformat()}]
    days = [{"id": "d1", "user_id": "u1", "work_date": "2026-11-16", "events": ev}]
    now = t0 + timedelta(hours=9)
    d = wv.report_defaults(days, "u1", "2026-11-16", now, UTC)
    assert (d["from_timer"], d["shift_start"], d["shift_end"], d["break_minutes"]) == (True, "08:03", "17:03", 45)
    assert "День ещё идёт" in d["note"]
    fin = days[0]["events"] + [{"seq": 4, "event": "finish", "device_time": (t0 + timedelta(hours=8, minutes=50)).isoformat()}]
    d = wv.report_defaults([{**days[0], "events": fin}], "u1", "2026-11-16", now, UTC)
    assert (d["shift_end"], d["break_minutes"]) == ("16:53", 45) and "День ещё идёт" not in d["note"]
    assert wv.report_defaults(days, "u1", "2026-11-15", now, UTC)["from_timer"] is False


def test_assembled_label_mentions_workday_timer():
    """D-46: отчёт, собранный программой по дню, отмеченному таймером, говорит, откуда смена — «по таймеру рабочего
    дня», с точным временем (без округления); без таймера — как в D-43."""
    from vector.client.worktime_view import _assembled_label
    assert _assembled_label({"actions": 14, "measured_minutes": 90, "lines": 3, "workday_minutes": 0}) == \
        "по 14 действиям в программе; по таймерам операций 1 ч 30 мин"
    label = _assembled_label({"actions": 1, "measured_minutes": 0, "lines": 1, "workday_minutes": 487})
    assert label == "по 1 действию в программе; смена — по таймеру рабочего дня, 8 ч 7 мин"
