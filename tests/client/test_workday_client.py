"""Таймер рабочего дня на клиенте (Class A, SQLite; D-46 — ответы владельца на A-49 пп. 2–5): нажатия без связи
(очередь, время — часы компьютера), проверка перехода до очереди, окно «Начните рабочий день», «Я начал
раньше» с согласованием руководителя (решение — только при связи), представление workday_view и смена нового
отчёта из таймера.

D-47 (ответы владельца на A-51): окно «Начните рабочий день» обязательно (без «Позже»), не показывается в выходной по
графику и тем, у кого учёт не ведётся; фактическое начало — не раньше начала по графику (встроенный 09:00–18:00, пока
своих графиков нет); напоминание о конце дня → «Работаю дальше» (через час) → без ответа 30 минут день завершается
автоматически → «Я работал дольше» (фактическое окончание через руководителя, запрос без связи — в очередь).
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
    """Модель таймера из кэша клиента c (часы — FakeClock стенда, пояс — UTC) — как Backend.workdayOverview."""
    return wv.workday_view(c.list_local("workday"), c.list_local("workday_start_request"), c.user_id,
                           {p["id"]: p["display_name"] for p in c.get_meta("people") or []}, c.t.online,
                           c.now(), prompt, UTC, c.list_local("workday_end_request"), c.workday_schedule_info(),
                           c.workday_reminder(UTC), c.get_meta("work_calendar"))


def test_offline_marks_queue_live_timer_and_prompt(world, mk):
    """Без связи: окно «Начните рабочий день» → «Начать» → «Перерыв» → «Продолжить» — в очередь; таймер считает по
    секундам, перерыв — только нажатый; второй «Перерыв» подряд не ставится в очередь; при связи сервер принимает всё."""
    world.clock.advance(seconds=61)
    eng = mk("eng1")
    eng.pull()
    assert eng.workday_prompt_needed(UTC) is True
    v = view(eng, True)
    assert (v["status"], v["prompt"], v["can_start"], v["clock"]) == ("not_started", True, True, "00:00:00")
    # «Позже» нет (A-51 п. 3): окно нужно, пока день не начат, — и после нового pull тоже.
    eng.pull()
    assert eng.workday_prompt_needed(UTC) is True
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
    assert v["schedule_start_label"] == "09:00"                          # встроенный график 09:00–18:00
    sch = eng.workday_schedule_info()
    assert wv.parse_start_time("10:30", day, eng.now(), UTC, sch)["message"].startswith("Фактическое начало должно быть раньше")
    assert wv.parse_start_time("8", day, eng.now(), UTC, sch)["ok"] is False
    # Раньше начала по графику — нельзя (A-51 п. 1: фактическое начало — от графика, правило «16 часов» снято).
    early = wv.parse_start_time("08:00", day, eng.now(), UTC, sch)
    assert early["ok"] is False and "Начало по графику — 09:00" in early["message"]
    assert eng.workday_start_request(day["id"], datetime(2026, 11, 16, 8, 0, tzinfo=UTC), "Был на объекте") \
        .response["code"] == "before_schedule"
    chk = wv.parse_start_time("09:00", day, eng.now(), UTC, sch)
    assert chk["ok"] and chk["message"].startswith("К таймеру добавится 1 ч 00 мин")
    eng.t.online = False
    at = datetime.fromisoformat(chk["start_at"])
    assert eng.workday_start_request(day["id"], at, " ").response["code"] == "field_required"
    assert eng.workday_start_request(day["id"], at, "Был на объекте").status == "queued"
    v = view(eng)
    assert v["pending_request"]["start_label"] == "09:00" and not v["can_request"]
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
    assert v["approved"] and v["started_label"] == "09:00" and v["pressed_label"] == "10:00"
    assert v["worked_seconds"] >= 3600 and "+1 ч 00 мин" in v["approved_label"]
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


def test_no_prompt_on_weekend_or_when_exempt(world, mk):
    """Окно «Начните рабочий день» — только в рабочий день по графику и только тем, у кого учёт ведётся (D-47)."""
    world.clock.advance(days=5, seconds=61)                              # суббота: по встроенному графику выходной
    eng = mk("eng1")
    eng.pull()
    assert eng.workday_prompt_needed(UTC) is False
    assert eng.workday_mark("start", UTC).status == "accepted"           # но начать день кнопкой можно
    world.clock.advance(days=2)                                          # понедельник
    a = world.login("admin")
    a.ok("schedule.assign", {"target_kind": "user", "target_id": eng.user_id, "exempt": True})
    assert eng.login("eng1", PWD).status == "accepted"
    eng.pull()
    info = eng.workday_schedule_info()
    assert info["exempt"] is True and eng.workday_prompt_needed(UTC) is False
    assert eng.workday_reminder(UTC)["state"] == "none"
    assert view(eng)["exempt"] is True


def test_end_reminder_snooze_auto_finish_and_longer_request(world, mk):
    """Забытое «Завершить» (A-51 п. 5): после конца по графику (18:00) — напоминание; «Работаю дальше» — снова через
    час; без ответа 30 минут программа завершает день сама (временем напоминания, пометка auto); «Я работал дольше» —
    запрос без связи в очередь, руководитель согласует при связи, и таймер считает день до фактического окончания."""
    world.clock.advance(seconds=61)                                      # 09:01 UTC, понедельник
    eng = mk("eng1")
    eng.pull()
    assert eng.workday_mark("start", UTC).status == "accepted"
    world.clock.advance(hours=8, minutes=58)                             # 17:59 — ещё рано
    assert eng.workday_reminder(UTC)["state"] == "none"
    world.clock.advance(minutes=2)                                       # 18:01
    r = eng.workday_reminder(UTC)
    assert (r["state"], r["due_label"]) == ("remind", "18:00")
    v = view(eng)
    assert v["reminder"] is True and "18:00" in v["reminder_text"]
    eng.workday_snooze(r["workday_id"], UTC)                             # «Работаю дальше»
    assert eng.workday_reminder(UTC)["state"] == "none"
    assert eng.workday_auto_finish("no_answer", UTC) == []               # нечего завершать
    world.clock.advance(hours=1)                                         # 19:01 — напоминание снова
    r = eng.workday_reminder(UTC)
    assert (r["state"], r["due_label"]) == ("remind", "19:01")
    world.clock.advance(minutes=31)                                      # без ответа 30 минут
    assert eng.workday_reminder(UTC)["state"] == "auto"
    eng.t.online = False
    res = eng.workday_auto_finish("no_answer", UTC)
    assert [x.status for x in res] == ["queued"]
    v = view(eng)
    assert v["status"] == "finished" and v["auto_finished"] is True and v["can_request_end"] is True
    assert "завершён автоматически в 19:01" in v["end_label"]
    day = eng.list_local("workday")[0]
    assert wv.parse_end_time("18:30", day, eng.now(), UTC)["ok"] is False   # раньше автоматического завершения
    assert wv.parse_end_time("23:00", day, eng.now(), UTC)["ok"] is False   # в будущем
    chk = wv.parse_end_time("19:30", day, eng.now(), UTC)
    assert chk["ok"] and chk["message"].startswith("К таймеру добавится 28 мин")  # 19:01:01 → 19:30
    end_at = datetime.fromisoformat(chk["end_at"])
    assert eng.workday_end_request(day["id"], end_at, "").response["code"] == "field_required"
    assert eng.workday_end_request(day["id"], end_at, "Доделывал схему").status == "queued"
    v = view(eng)
    assert v["can_request_end"] is False and v["pending_end_request"]["kind"] == "end"
    assert eng.workday_end_request(day["id"], end_at, "Ещё раз").response["code"] == "already_pending"
    eng.t.online = True
    assert eng.login("eng1", PWD).status == "accepted"                  # сеанс истёк за вечер — войти тем же профилем
    assert eng.sync().sent == 2 and eng.problem_count() == 0             # «Завершить» (auto) и запрос

    world.clock.advance(seconds=61)
    head = mk("head1")
    head.pull()
    rv = [x for x in view(head)["review"] if x["kind"] == "end"][0]
    assert (rv["command"], rv["may_decide"], rv["kind_label"]) == ("workday.end_decide", True, "Окончание дня")
    assert head.execute(rv["command"], {"id": rv["id"], "decision": "approve"}, rv["version"]).status == "accepted"
    eng.pull()
    v = view(eng)
    day = eng.list_local("workday")[0]
    assert day["approved_end_at"] and v["pending_end_request"] == {}
    assert v["worked_seconds"] == int((end_at - datetime(2026, 11, 16, 9, 1, 1, tzinfo=UTC)).total_seconds())


def test_finish_at_past_time_and_closed_app():
    """«Закончил в 17:40» в напоминании — прошедшее время, не раньше последнего нажатия и не в будущем;
    программу закрыли с идущим днём — при следующем запуске день завершится концом по графику (reminder auto)."""
    t0 = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    day = {"id": "d1", "user_id": "u1", "work_date": "2026-11-16",
           "events": [{"seq": 1, "event": "start", "device_time": t0.isoformat()}]}
    now = datetime(2026, 11, 16, 18, 10, tzinfo=UTC)
    ok = wv.parse_finish_time("17:40", day, now, UTC)
    assert ok["ok"] and ok["at"].startswith("2026-11-16T17:40") and ok["message"] == "День будет завершён в 17:40."
    assert wv.parse_finish_time("08:00", day, now, UTC)["ok"] is False   # раньше «Начать»
    assert wv.parse_finish_time("18:30", day, now, UTC)["ok"] is False   # «18:30» сейчас = вчера → раньше «Начать»
    assert wv.parse_finish_time("1740", day, now, UTC)["ok"] is False


def test_production_calendar_moves_prompt_and_reminder(world, mk, tmp_path):
    """Производственный календарь (D-48, A-52 п. 3): в праздник окно «Начните рабочий день» не появляется; в
    сокращённый день напоминание о конце дня — на час раньше (17:00 по встроенному 09:00–18:00); календарь скачивается
    (здесь — из файла по адресу file://) и загружается администратором; неудачная загрузка — понятная причина."""
    from importlib import resources
    world.clock.advance(seconds=61)                                      # понедельник 2026-11-16 09:01 UTC
    a = world.login("admin")
    a.ok("calendar.day_set", {"date": "2026-11-16", "kind": "short", "title": "Канун праздника города"})
    a.ok("calendar.day_set", {"date": "2026-11-17", "kind": "holiday", "title": "Праздник города"})
    eng = mk("eng1")
    eng.pull()
    assert eng.workday_calendar()["2026-11-17"]["kind"] == "holiday"
    assert eng.workday_mark("start", UTC).status == "accepted"
    v = view(eng)
    assert v["calendar_kind"] == "short" and v["schedule_end_label"] == "17:00"
    assert v["calendar_note"].startswith("Сокращённый день")
    world.clock.advance(hours=7, minutes=59)                             # 17:00 — по сокращённому дню конец
    assert eng.workday_reminder(UTC)["due_label"] == "17:00"
    assert eng.workday_mark("finish", UTC).status == "queued"           # сеанс истёк за день — в очередь
    world.clock.advance(hours=16)                                        # вторник 09:00 — праздник
    assert eng.login("eng1", PWD).status == "accepted"
    eng.sync()
    assert eng.workday_prompt_needed(UTC) is False
    # Скачать календарь (адреса — как у xmlcalendar.ru, здесь файлы на диске) и загрузить на сервер.
    src = resources.files("vector.core").joinpath("prodcal_data/ru-2027.xml").read_text(encoding="utf-8")
    (tmp_path / "2027.xml").write_text(src, encoding="utf-8")
    tpl = (tmp_path / "{year}.xml").as_uri().replace("%7B", "{").replace("%7D", "}")
    bad = eng.calendar_download(2028, urls=[tpl])
    assert bad["ok"] is False and "2028" in bad["message"]
    got = eng.calendar_download(2027, urls=[tpl])
    assert got["ok"] is True and got["source"].endswith("2027.xml")
    assert eng.calendar_import(2027, got["xml"], got["source"]).status == "denied"     # не администратор
    world.clock.advance(seconds=61)
    adm = mk("admin")
    assert adm.calendar_import(2027, got["xml"], got["source"]).status == "accepted"
    cal = adm.get_meta("work_calendar")
    assert next(y for y in cal["years"] if y["year"] == 2027)["state"] == "loaded"


def test_activity_postpones_auto_finish_and_company_intervals(world, mk):
    """D-49 (ответы владельца на A-53 пп. 1, 3, 5). Администратор задал в настройках компании: повтор напоминания —
    через 30 минут, автоматическое завершение — через час. Сотрудник нажал «Работаю дальше» и заработался: пока за
    компьютером работают, день не завершается; ушёл в 19:40 — через час тишины день завершается в 19:40 (а не временем
    напоминания); под таймером — «Сверхурочно» (норма 8 ч)."""
    for key, value in (("workday.reminder_repeat", "30"), ("workday.auto_finish_wait", "60")):
        r = world.admin.cmd("config.publish", {"key": key, "value": value, "reason": "Решение руководства"},
                            expected_version=0)
        assert r["status"] == "accepted", r
    assert world.admin.cmd("config.publish", {"key": "workday.auto_finish_wait", "value": "45", "reason": "x"},
                           expected_version=1)["code"] == "bad_value"           # только из каталога
    world.clock.advance(seconds=61)                                      # 09:01 UTC, понедельник
    eng = mk("eng1")
    eng.pull()
    assert eng.workday_intervals() == {"remind": 30, "wait": 60}
    eng.workday_note_activity(datetime(2026, 11, 16, 8, 0, tzinfo=UTC))  # до начала дня — к нему не относится
    assert eng.workday_mark("start", UTC).status == "accepted"
    world.clock.advance(hours=9)                                         # 18:01 — напоминание
    r = eng.workday_reminder(UTC)
    assert (r["state"], r["wait_minutes"], r["active"]) == ("remind", 60, False)
    assert "Без ответа и без работы за компьютером час программа завершит день сама — в 18:00" in view(eng)["reminder_text"]
    eng.workday_snooze(r["workday_id"], UTC)                             # «Работаю дальше» — через 30 минут
    world.clock.advance(minutes=30)                                      # 18:31
    r = eng.workday_reminder(UTC)
    assert (r["state"], r["due_label"]) == ("remind", "18:31")
    world.clock.advance(minutes=69)                                      # 19:40 — работает за компьютером
    eng.workday_note_activity(eng.now())
    eng.workday_note_activity(eng.now() - timedelta(hours=1))           # более раннее время не перезаписывает
    assert eng.workday_last_activity() == eng.now()
    world.clock.advance(minutes=59)                                      # 20:39 — тишина 59 минут: ещё напоминание
    r = eng.workday_reminder(UTC)
    assert (r["state"], r["active"], r["auto_label"]) == ("remind", True, "19:40")
    assert "Вы работаете за компьютером — день сам не завершится" in view(eng)["reminder_text"]
    assert eng.workday_auto_finish("no_answer", UTC) == []
    world.clock.advance(minutes=1)                                       # 20:40 — час без работы
    assert eng.workday_reminder(UTC)["state"] == "auto"
    res = eng.workday_auto_finish("no_answer", UTC)
    assert [x.status for x in res] in (["accepted"], ["queued"])
    v = view(eng)
    assert v["status"] == "finished" and "завершён автоматически в 19:40" in v["end_label"]
    assert "временем последней работы" in v["end_label"]
    # 09:01:01 → 19:40:01 = 10 ч 39 мин; норма встроенного графика 8 ч → сверхурочно 2 ч 39 мин.
    assert (v["day_mark"], v["day_mark_tone"]) == ("overtime", "warning")
    assert v["day_mark_label"] == "Сверхурочно — 2 ч 39 мин (норма по графику 8 ч 00 мин)"
    # Сервер принимает «Завершить» (auto) позже конца по графику: время — последняя работа, а не 18:00.
    assert eng.login("eng1", PWD).status == "accepted"
    eng.sync()
    assert eng.problem_count() == 0
    day = [d for d in eng.list_local("workday") if d["work_date"] == "2026-11-16"][0]
    fin = [e for e in day["events"] if e["event"] == "finish"][0]
    assert fin["device_time"].startswith("2026-11-16T19:40") and fin["auto"] == "no_answer"


def test_weekend_and_holiday_marks_under_timer(world, mk):
    """Работа в выходной день — отметка под таймером (D-49, A-53 п. 3); окна «Начните рабочий день» нет, но начать
    день кнопкой можно."""
    world.clock.advance(days=5, seconds=61)                              # суббота 21.11, 09:01
    eng = mk("eng1")
    eng.pull()
    assert eng.workday_prompt_needed(UTC) is False
    assert eng.workday_mark("start", UTC).status == "accepted"
    world.clock.advance(hours=2)
    v = view(eng)
    assert (v["day_mark"], v["day_type_label"]) == ("weekend", "выходной день")
    assert v["day_mark_label"] == "Работа в выходной день — 2 ч 00 мин"
