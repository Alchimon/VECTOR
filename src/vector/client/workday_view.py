"""Представление таймера рабочего дня (этап 7, R-20; decision log D-46 — ответы владельца на A-49 пп. 2–5; D-47 —
ответы на A-51). Чистые функции без Qt.

Что видит сотрудник:

* в боковой панели — свой рабочий день: состояние («Идёт», «Перерыв», «Завершён»), «сколько натикало» (живой таймер
  ЧЧ:ММ:СС, без округления — п. 2) и кнопки «Начать рабочий день» / «Перерыв» / «Продолжить» / «Завершить день»;
  перерыв считается, только если нажата кнопка «Перерыв» (п. 3);
* окно «Начните рабочий день», если сегодня рабочий день по графику, а таймер не запускали — обязательно, без
  «Позже» (D-47, A-51 пп. 3–4: «рабочий день надо обязательно начинать»);
* свой график работы (откуда он: назначен лично, подразделения, по умолчанию) — от него фактическое начало (не раньше
  начала по графику, A-51 п. 1) и напоминание в конце дня: «Завершить» / «Работаю дальше» / «Закончил раньше»; без
  ответа после заданного ожидания день завершается автоматически временем напоминания. Ввод с клавиатуры и мыши не
  отслеживается; если работали дольше, можно отправить запрос руководителю (A-51 п. 5, решение A-54);
* отметки дня (D-49, A-53 п. 3): «Сверхурочно» сверх нормы по графику, «Работа в выходной день», «Работа в
  праздничный день» — под таймером и в отчёте;
* в разделе «Рабочее время» — подробности дня (нажатия таймера, работа и перерывы; компьютерная активность не
  собирается) и «Я начал раньше»: фактическое начало
  сегодняшнего дня на согласование руководителю; после согласования таймер считает от него — день начался в 8,
  «Начать» нажали в 10 — сразу видно 2 часа (п. 4);
* руководителю (тому, кто проверяет дневные отчёты сотрудника, D-44/D-45) — запросы на фактическое начало и
  окончание дня: «Согласовать» или «Отклонить» с причиной (при связи; A-51 п. 6 — решает тот же руководитель);
* D-49 (A-53) и уточнение A-54: под таймером — отметка дня: «Работа в праздничный день», «Работа в выходной день»,
  «Сверхурочно» (сверх нормы по графику); компьютерная активность не отслеживается; после настроенного ожидания без
  ответа забытый день завершается по времени напоминания; интервалы напоминания — из настроек компании.

День — по часам компьютера сотрудника (п. 5, «как есть»). Флаги can_* — подсказки интерфейса; решает сервер.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from vector.core import prodcal as pc
from vector.core import schedule as sch
from vector.core import workday as wd

# Что подсказать под таймером в каждом состоянии.
NEXT = {"not_started": "Нажмите «Начать рабочий день», когда приступили к работе: таймер считает время по часам "
                       "компьютера, без округления.",
        "working": "Таймер идёт. Уходите на перерыв — нажмите «Перерыв»: перерыв считается только по кнопке.",
        "paused": "Перерыв. Вернулись — «Продолжить»; закончили работу — «Завершить день».",
        "finished": "Рабочий день завершён — смена попадёт в дневной отчёт. Вернулись к работе — «Продолжить»: "
                    "отсчёт пойдёт дальше."}
AUTO_REASON_RU = {"no_answer": "напоминание о конце дня осталось без ответа — день завершён по времени напоминания",
                  "closed": "программа была закрыта — день завершён по времени напоминания"}


def _local(dt: Optional[datetime], tz=None) -> Optional[datetime]:
    """Время UTC → время компьютера (tz — для тестов)."""
    if dt is None:
        return None
    return dt.astimezone(tz) if tz is not None else dt.astimezone()


def _hm(dt: Optional[datetime], tz=None) -> str:
    """«08:05» (по часам компьютера) или «—»."""
    loc = _local(dt, tz)
    return f"{loc:%H:%M}" if loc else "—"


def _today_day(days: list[dict], me_id: str, today: date) -> Optional[dict]:
    """Текущий рабочий день: сегодняшний или вчерашний незавершённый (ночная смена через полночь)."""
    mine = [d for d in days or [] if d.get("user_id") in (None, "", me_id)]
    cur = next((d for d in mine if d.get("work_date") == today.isoformat()), None)
    if cur is None:
        prev = next((d for d in mine if d.get("work_date") == (today - timedelta(days=1)).isoformat()), None)
        if prev and prev.get("status") != "finished":
            return prev
    return cur


def _request_row(r: dict, me_id: str, people: dict[str, str], online: bool, tz=None) -> dict:
    """Запрос «фактическое начало / окончание дня» (kind start | end): кто, за какой день, время, причина, решение;
    можно ли решить."""
    st = r.get("status") or "pending"
    own = r.get("user_id") == me_id
    kind = "end" if r.get("kind") == "end" or r.get("requested_end_at") else "start"
    req = wd._opt_dt(r.get(f"requested_{kind}_at"))
    what = f"начало дня {_date_label(r.get('work_date'))} — с" if kind == "start" else \
        f"окончание дня {_date_label(r.get('work_date'))} — в"
    may = (not own) and st == "pending" and not r.get("_pending")
    return {
        "id": r.get("id") or "", "workday_id": r.get("workday_id") or "", "user_id": r.get("user_id") or "",
        "user_name": people.get(r.get("user_id") or "", "—"), "is_own": own, "kind": kind,
        "kind_label": "Начало дня" if kind == "start" else "Окончание дня",
        "command": f"workday.{kind}_decide",
        "date_label": _date_label(r.get("work_date")), "start_label": _hm(req, tz), "time_label": _hm(req, tz),
        "title": f"{people.get(r.get('user_id') or '', '—')}: {what} {_hm(req, tz)}",
        "reason": r.get("reason") or "", "status": st, "status_label": wd.REQUEST_LABELS_RU.get(st, st),
        "status_tone": wd.REQUEST_TONES.get(st, "neutral"), "comment": r.get("decision_comment") or "",
        "decided_label": (f"{people.get(r.get('decided_by') or '', '—')}" if r.get("decided_by") else ""),
        "pending": bool(r.get("_pending")), "version": r.get("version") or 0,
        "may_decide": may, "can_decide": may and online,
        "block": "" if not may or online else "Решение по рабочему дню — только при связи с сервером.",
    }


def _date_label(iso_d: Optional[str]) -> str:
    """ГГГГ-ММ-ДД → ДД.ММ.ГГГГ (пусто — пусто)."""
    try:
        return f"{date.fromisoformat(iso_d or ''):%d.%m.%Y}"
    except ValueError:
        return iso_d or ""


def calendar_days(calendar: Optional[dict], today: date) -> dict[str, dict]:
    """Действующий производственный календарь из pull (work_calendar.days); до первого pull — встроенный в программу
    на прошлый, текущий и следующий год (D-48)."""
    days = (calendar or {}).get("days") if isinstance(calendar, dict) else None
    if isinstance(days, dict):
        return days
    return pc.effective((today.year - 1, today.year, today.year + 1), {}, [], [])


def _schedule_part(info: Optional[dict], day: date, tz=None, cal: Optional[dict] = None) -> dict[str, Any]:
    """Мой график в модели: подпись, откуда, учёт не ведётся, окно дня day по часам компьютера; особый день по
    производственному календарю (D-48) — подпись «праздник: …» / «сокращённый день…», если график его учитывает."""
    info = info if isinstance(info, dict) and info else {"schedule": dict(sch.BUILTIN), "exempt": False,
                                                         "source_label": "встроенный"}
    s = info.get("schedule")
    win = sch.window(s, day, tz, cal)
    kind = sch.day_kind(s, day, cal)
    note = pc.day_note((cal or {}).get(day.isoformat())) if kind else ""
    return {"schedule_label": sch.full_label(s) if s else sch.label(None),
            "schedule_source": info.get("source_label") or "", "exempt": bool(info.get("exempt")),
            "schedule_day": win is not None,
            "schedule_start_label": _hm(win[0], tz) if win else "", "schedule_end_label": _hm(win[1], tz) if win else "",
            "calendar_kind": kind or "", "calendar_note": (note[:1].upper() + note[1:]) if note else "",
            "schedule_start": win[0] if win else None, "_schedule": s}


def workday_view(days: list[dict], requests: list[dict], me_id: str, people: dict[str, str], online: bool,
                 now: Optional[datetime] = None, prompt: bool = False, tz=None, end_requests: Optional[list] = None,
                 schedule: Optional[dict] = None, reminder: Optional[dict] = None,
                 calendar: Optional[dict] = None) -> dict[str, Any]:
    """Модель таймера и запросов для боковой панели и раздела «Рабочее время».

    days — свои рабочие дни (кэш с неотправленными нажатиями); requests / end_requests — запросы на фактическое
    начало / окончание (свои и, руководителю, сотрудников); prompt — показать окно «Начните рабочий день» (решает
    SyncClient.workday_prompt_needed); schedule — мой действующий график (pull: workday_schedule); reminder —
    напоминание о конце дня (SyncClient.workday_reminder; показывается, если state = remind); calendar —
    производственный календарь (pull: work_calendar, D-48).
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = _local(now, tz).date()
    d = _today_day(days, me_id, today)
    events = (d or {}).get("events") or []
    state = wd.day_state(events, now, (d or {}).get("approved_start_at"), (d or {}).get("approved_end_at"))
    st = state["status"]
    try:
        day_date = date.fromisoformat((d or {}).get("work_date") or "")
    except ValueError:
        day_date = today
    sp = _schedule_part(schedule, day_date, tz, calendar_days(calendar, today))
    reqs = [_request_row(r, me_id, people, online, tz) for r in (requests or []) + (end_requests or [])]
    reqs.sort(key=lambda x: x["status"] != "pending")      # на согласовании — сверху (дальше порядок сервера)
    day_reqs = [r for r in reqs if d and r["workday_id"] == d.get("id") and r["kind"] == "start"]
    pending_req = next((r for r in day_reqs if r["status"] == "pending"), None)
    window_ok = bool(state["pressed_at"]) and now - state["pressed_at"] <= wd.REQUEST_WINDOW
    late = bool(state["pressed_at"]) and sp["schedule_start"] is not None and state["pressed_at"] > sp["schedule_start"]
    can_request = bool(d) and window_ok and pending_req is None and late and not sp["exempt"]
    if not d:
        why_not = "Сначала начните рабочий день."
    elif pending_req:
        why_not = "Запрос уже на согласовании у руководителя."
    elif not window_ok:
        why_not = "Фактическое начало можно указать только для сегодняшнего рабочего дня."
    elif sp["exempt"]:
        why_not = "Учёт рабочего времени для вас не ведётся."
    elif sp["schedule_start"] is None:
        why_not = ("По производственному календарю этот день нерабочий — таймер считает с нажатия «Начать»."
                   if sp["calendar_kind"] == "holiday" else
                   "По графику работы этот день нерабочий — таймер считает с нажатия «Начать».")
    elif not late:
        why_not = f"Вы начали день не позже начала по графику ({sp['schedule_start_label']}) — указывать не нужно."
    else:
        why_not = ""
    # Нажатия дня — по часам компьютера; неотправленные помечены; автоматическое «Завершить» — с пометкой.
    ev_rows = [{"label": wd.EVENT_LABELS_RU.get(e.get("event"), e.get("event") or "")
                + (" (автоматически)" if e.get("auto") else ""),
                "time": _hm(wd._opt_dt(e.get("device_time")), tz), "local": bool(e.get("local")),
                "event": e.get("event") or "", "auto": bool(e.get("auto"))} for e in wd._sorted(events)]
    pressed = state["pressed_at"]
    # По умолчанию в форме «Я начал раньше» — начало по графику (A-51 п. 1), иначе час до нажатия.
    default_start = (_local(sp["schedule_start"], tz) if late else None) or (
        _local(pressed - timedelta(hours=1), tz) if pressed else None)
    end = _end_part(days, reqs, me_id, now, tz)
    rem = reminder if isinstance(reminder, dict) else {}
    remind = rem.get("state") == "remind" and not sp["exempt"]
    wait = wd.minutes_label(int(rem.get("wait_minutes") or 30))
    tail = (f"Без ответа {wait} программа завершит день автоматически по времени напоминания — в "
            f"{rem.get('auto_label') or rem.get('due_label')}. Если вы работали дольше, отправьте запрос руководителю.")
    # Отметка дня (D-49, A-53 п. 3): праздник, выходной по графику, сверхурочно — по таймеру, без округления.
    marks = wd.day_marks(sp["_schedule"], day_date, state["worked_seconds"] // 60, calendar_days(calendar, today),
                         sp["exempt"]) if d else wd.day_marks(None, day_date, 0)
    return {
        "has_day": bool(d), "id": (d or {}).get("id") or "", "work_date": (d or {}).get("work_date") or "",
        "date_label": _date_label((d or {}).get("work_date")) or f"{today:%d.%m.%Y}",
        "night": bool(d) and d.get("work_date") != today.isoformat(),
        "status": st, "status_label": wd.STATUS_LABELS_RU[st], "status_tone": wd.STATUS_TONES[st],
        "running": st == "working",
        # Живой таймер: QML прибавляет секунды, прошедшие с момента as_of (своими часами), пока running.
        "worked_seconds": state["worked_seconds"], "clock": wd.clock_label(state["worked_seconds"]),
        "worked_label": wd.duration_label(state["worked_seconds"]),
        "break_label": wd.duration_label(state["break_seconds"]) if state["breaks"] or state["break_seconds"] else "нет",
        "breaks": state["breaks"],
        "started_label": _hm(state["started_at"], tz), "pressed_label": _hm(pressed, tz),
        "finished_label": _hm(state["finished_at"], tz) if state["finished_at"] else "",
        "approved": state["extra_seconds"] > 0,
        "approved_label": (f"Фактическое начало {_hm(state['started_at'], tz)} согласовано: +"
                           f"{wd.duration_label(state['extra_seconds'])} к таймеру") if state["extra_seconds"] else "",
        "next": NEXT[st], "events": ev_rows, "unsent": any(e["local"] for e in ev_rows),
        "can_start": st == "not_started", "can_pause": st == "working", "can_resume": st in ("paused", "finished"),
        "can_finish": st in ("working", "paused"),
        "can_request": can_request, "request_block": why_not,
        "pending_request": pending_req or {},
        "request_default": f"{default_start:%H:%M}" if default_start else "",
        # Окно «Начните рабочий день» — обязательно (без «Позже»), пока день не начат (A-51 пп. 3–4).
        "prompt": bool(prompt) and st == "not_started" and not sp["exempt"],
        # График работы (D-47).
        **{k: v for k, v in sp.items() if k not in ("schedule_start", "_schedule")},
        # Отметка дня (D-49): «Работа в праздничный день — …», «Работа в выходной день — …», «Сверхурочно — …».
        "day_mark": marks["mark"], "day_mark_label": marks["label"], "day_mark_tone": marks["tone"],
        "day_type_label": marks["day_type_label"],
        # Напоминание о конце дня (A-51 п. 5): «Завершить» / «Работаю дальше» / «Закончил раньше».
        "reminder": remind, "reminder_workday_id": (rem.get("workday_id") or "") if remind else "",
        "reminder_due_label": (rem.get("due_label") or "") if remind else "",
        "reminder_text": (f"Рабочий день по графику закончился в {rem.get('due_label')}, а таймер ещё идёт: "
                          f"{wd.clock_label(state['worked_seconds'])}. Завершите день или отметьте, что работаете "
                          f"дальше. {tail}") if remind else "",
        # Фактическое окончание дня, завершённого автоматически (A-51 п. 5).
        **end,
        "my_requests": [r for r in reqs if r["is_own"]],
        "review": [r for r in reqs if not r["is_own"]],
        "to_decide": sum(1 for r in reqs if r["may_decide"]),
        "online": online,
    }


def _end_part(days: list[dict], reqs: list[dict], me_id: str, now: datetime, tz=None) -> dict[str, Any]:
    """«Я работал дольше»: последний свой день, завершённый автоматически, — пока не прошли сутки (A-51 п. 5)."""
    best = None
    for d in days or []:
        if d.get("user_id") not in (None, "", me_id) or d.get("status") != "finished":
            continue
        st = wd.day_state(d.get("events") or [], now, d.get("approved_start_at"), d.get("approved_end_at"))
        if not st["auto_finished"] or st["finished_at"] is None or now - st["finished_at"] > wd.REQUEST_WINDOW:
            continue
        if best is None or st["finished_at"] > best[1]["finished_at"]:
            best = (d, st)
    empty = {"end_day_id": "", "end_label": "", "can_request_end": False, "end_block": "", "end_default": "",
             "pending_end_request": {}, "auto_finished": False}
    if best is None:
        return empty
    d, st = best
    auto = next((e.get("auto") for e in reversed(wd._sorted(d.get("events") or [])) if e.get("event") == "finish"), "")
    pend = next((r for r in reqs if r["kind"] == "end" and r["workday_id"] == d.get("id") and r["status"] == "pending"
                 and r["is_own"]), None)
    return {"end_day_id": d.get("id") or "", "auto_finished": True,
            "end_label": (f"День {_date_label(d.get('work_date'))} завершён автоматически в "
                          f"{_hm(st['finished_at'], tz)}: {AUTO_REASON_RU.get(auto, 'забыли «Завершить»')}. Работали "
                          "дольше — «Я работал дольше»: руководитель согласует, и таймер посчитает день до фактического "
                          "окончания."),
            "can_request_end": pend is None, "end_block": "Запрос уже на согласовании у руководителя." if pend else "",
            "end_default": f"{_local(min(now, st['finished_at'] + timedelta(hours=1)), tz):%H:%M}",
            "pending_end_request": pend or {}}


def _parse_hhmm(text: str) -> Optional[tuple[int, int]]:
    """«08:00» / «8.00» → (8, 0) или None."""
    t = (text or "").strip().replace(".", ":")
    try:
        h, m = (int(x) for x in t.split(":"))
    except ValueError:
        return None
    return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None


def parse_start_time(text: str, workday: dict, now: Optional[datetime] = None, tz=None,
                     schedule: Optional[dict] = None, calendar: Optional[dict] = None) -> dict[str, Any]:
    """Поле «Фактическое начало» формы «Я начал раньше» («08:00» по часам компьютера) → время ISO с поясом для
    workday.start_request, с проверками ядра (раньше нажатия, не раньше начала по графику — D-47, A-51 п. 1). Если
    «ЧЧ:ММ» позже нажатия, а день начат после полуночи, — это вчерашний вечер (ночная смена). {ok, start_at, message}."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hm = _parse_hhmm(text)
    if hm is None:
        return {"ok": False, "start_at": "", "message": "Время — ЧЧ:ММ, например 08:00."}
    st = wd.day_state(workday.get("events") or [], now)
    pressed = st["pressed_at"]
    if pressed is None:
        return {"ok": False, "start_at": "", "message": "Сначала начните рабочий день."}
    try:
        day = date.fromisoformat(workday.get("work_date") or "")
    except ValueError:
        day = _local(pressed, tz).date()
    sp = _schedule_part(schedule, day, tz, calendar_days(calendar, day))
    loc = _local(pressed, tz)
    cand = loc.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    if cand >= loc:
        # «23:00» при нажатии в 01:30 — вчерашний вечер (ночная смена), если по графику день начался вчера.
        if sp["schedule_start"] is not None and (cand - timedelta(days=1)).astimezone(timezone.utc) >= sp["schedule_start"]:
            cand -= timedelta(days=1)
        else:
            return {"ok": False, "start_at": "", "message": "Фактическое начало должно быть раньше нажатия «Начать "
                                                            f"рабочий день» ({loc:%H:%M})."}
    try:
        wd.check_start_request(pressed, cand.astimezone(timezone.utc), now, sp["schedule_start"], sp["exempt"])
    except wd.WorkdayError as e:
        msg = e.message_ru + (f" Начало по графику — {sp['schedule_start_label']}." if e.code == "before_schedule" else "")
        return {"ok": False, "start_at": "", "message": msg}
    gain = int((pressed - cand.astimezone(timezone.utc)).total_seconds())
    return {"ok": True, "start_at": cand.isoformat(), "message": f"К таймеру добавится {wd.duration_label(gain)} после "
                                                                   "согласования руководителем."}


def _at_after(text: str, after: datetime, tz=None) -> Optional[datetime]:
    """«ЧЧ:ММ» по часам компьютера → ближайший такой момент не раньше дня after (через полночь — следующий день).
    None — неверный формат."""
    hm = _parse_hhmm(text)
    if hm is None:
        return None
    loc = _local(after, tz)
    cand = loc.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    if cand < loc - timedelta(hours=12):
        cand += timedelta(days=1)                         # «01:30» после «18:00» — уже следующие сутки
    return cand


def parse_end_time(text: str, workday: dict, now: Optional[datetime] = None, tz=None) -> dict[str, Any]:
    """Поле «Фактическое окончание» формы «Я работал дольше» → время ISO с поясом для workday.end_request, с
    проверками ядра (позже автоматического завершения, не в будущем; D-47, A-51 п. 5). {ok, end_at, message}."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    st = wd.day_state(workday.get("events") or [], now, workday.get("approved_start_at"))
    if st["finished_at"] is None:
        return {"ok": False, "end_at": "", "message": "Рабочий день не завершён."}
    cand = _at_after(text, st["finished_at"], tz)
    if cand is None:
        return {"ok": False, "end_at": "", "message": "Время — ЧЧ:ММ, например 19:30."}
    try:
        wd.check_end_request(st, cand.astimezone(timezone.utc), now)
    except wd.WorkdayError as e:
        return {"ok": False, "end_at": "", "message": e.message_ru}
    gain = int((cand.astimezone(timezone.utc) - st["finished_at"]).total_seconds())
    return {"ok": True, "end_at": cand.isoformat(), "message": f"К таймеру добавится {wd.duration_label(gain)} после "
                                                               "согласования руководителем."}


def parse_finish_time(text: str, workday: dict, now: Optional[datetime] = None, tz=None) -> dict[str, Any]:
    """Поле «Закончил в» напоминания о конце дня → прошедшее время «Завершить» (не раньше последнего нажатия, не в
    будущем; меньше времени себе — согласование не нужно). {ok, at, message}."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    st = wd.day_state(workday.get("events") or [], now, workday.get("approved_start_at"))
    if st["status"] not in ("working", "paused"):
        return {"ok": False, "at": "", "message": "Рабочий день не идёт."}
    hm = _parse_hhmm(text)
    if hm is None:
        return {"ok": False, "at": "", "message": "Время — ЧЧ:ММ, например 17:40."}
    loc = _local(now, tz)
    cand = loc.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    if cand > loc:
        cand -= timedelta(days=1)                         # «23:50» после полуночи — вчерашний вечер
    try:
        wd.check_early_finish(st, cand.astimezone(timezone.utc), now)
    except wd.WorkdayError as e:
        return {"ok": False, "at": "", "message": e.message_ru}
    return {"ok": True, "at": cand.isoformat(), "message": f"День будет завершён в {cand:%H:%M}."}


def report_defaults(days: list[dict], me_id: str, work_date: str, now: Optional[datetime] = None, tz=None) -> dict:
    """Смена нового дневного отчёта из таймера рабочего дня (D-46, A-49 пп. 2–3): {from_timer, shift_start,
    shift_end («ЧЧ:ММ»), break_minutes, note}. День ещё идёт — конец смены «сейчас». Таймера за день нет —
    from_timer = False (форма подставит обычные 09:00–18:00, и сотрудник укажет смену сам)."""
    from vector.core import worktime as wt
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    d = next((x for x in days or [] if x.get("work_date") == work_date and x.get("user_id") in (None, "", me_id)), None)
    empty = {"from_timer": False, "shift_start": "", "shift_end": "", "break_minutes": 0, "note": ""}
    if not d:
        return empty
    try:
        day = date.fromisoformat(work_date)
    except ValueError:
        return empty
    st = wd.day_state(d.get("events") or [], now, d.get("approved_start_at"), d.get("approved_end_at"))
    running = st["finished_at"] is None
    if running:
        st = {**st, "finished_at": now}
    s = wd.shift_from_day(st, day, tz)
    if not s:
        return empty
    note = (f"Смена — по таймеру рабочего дня: работа {wd.duration_label(st['worked_seconds'])}, перерывы "
            f"{s['break_minutes']} мин (только нажатые «Перерыв»).")
    if running:
        note += " День ещё идёт — конец смены «сейчас»."
    elif st["auto_finished"]:
        note += (" День завершён автоматически — если работали дольше, «Я работал дольше» в разделе «Рабочее время»"
                 " (после согласования смена продлится).")
    return {"from_timer": True, "shift_start": wt.time_label(s["shift_start"]), "shift_end": wt.time_label(s["shift_end"]),
            "break_minutes": s["break_minutes"], "note": note}
