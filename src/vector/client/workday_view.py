"""Представление таймера рабочего дня (этап 7, R-20; decision log D-46 — ответы владельца на A-49 пп. 2–5).
Чистые функции без Qt.

Что видит сотрудник:

* в боковой панели — свой рабочий день: состояние («Идёт», «Перерыв», «Завершён»), «сколько натикало» (живой таймер
  ЧЧ:ММ:СС, без округления — п. 2) и кнопки «Начать рабочий день» / «Перерыв» / «Продолжить» / «Завершить день»;
  перерыв считается, только если нажата кнопка «Перерыв» (п. 3);
* окно «Начните рабочий день», если сегодня таймер не запускали (кнопка «Позже» откладывает его до завтра, п. 4);
* в разделе «Рабочее время» — подробности дня (нажатия, работа и перерывы) и «Я начал раньше»: фактическое начало
  сегодняшнего дня на согласование руководителю; после согласования таймер считает от него — день начался в 8,
  «Начать» нажали в 10 — сразу видно 2 часа (п. 4);
* руководителю (тому, кто проверяет дневные отчёты сотрудника, D-44/D-45) — запросы на фактическое начало:
  «Согласовать» или «Отклонить» с причиной (при связи).

День — по часам компьютера сотрудника (п. 5, «как есть»). Флаги can_* — подсказки интерфейса; решает сервер.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from vector.core import workday as wd

# Что подсказать под таймером в каждом состоянии.
NEXT = {"not_started": "Нажмите «Начать рабочий день», когда приступили к работе: таймер считает время по часам "
                       "компьютера, без округления.",
        "working": "Таймер идёт. Уходите на перерыв — нажмите «Перерыв»: перерыв считается только по кнопке.",
        "paused": "Перерыв. Вернулись — «Продолжить»; закончили работу — «Завершить день».",
        "finished": "Рабочий день завершён — смена попадёт в дневной отчёт. Вернулись к работе — «Продолжить»."}


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
    """Запрос «фактическое начало дня»: кто, за какой день, с какого времени, причина, решение; можно ли решить."""
    st = r.get("status") or "pending"
    own = r.get("user_id") == me_id
    req = wd._opt_dt(r.get("requested_start_at"))
    may = (not own) and st == "pending" and not r.get("_pending")
    return {
        "id": r.get("id") or "", "workday_id": r.get("workday_id") or "", "user_id": r.get("user_id") or "",
        "user_name": people.get(r.get("user_id") or "", "—"), "is_own": own,
        "date_label": _date_label(r.get("work_date")), "start_label": _hm(req, tz),
        "title": f"{people.get(r.get('user_id') or '', '—')}: начало дня {_date_label(r.get('work_date'))} — с {_hm(req, tz)}",
        "reason": r.get("reason") or "", "status": st, "status_label": wd.REQUEST_LABELS_RU.get(st, st),
        "status_tone": wd.REQUEST_TONES.get(st, "neutral"), "comment": r.get("decision_comment") or "",
        "decided_label": (f"{people.get(r.get('decided_by') or '', '—')}" if r.get("decided_by") else ""),
        "pending": bool(r.get("_pending")), "version": r.get("version") or 0,
        "may_decide": may, "can_decide": may and online,
        "block": "" if not may or online else "Решение по началу дня — только при связи с сервером.",
    }


def _date_label(iso_d: Optional[str]) -> str:
    """ГГГГ-ММ-ДД → ДД.ММ.ГГГГ (пусто — пусто)."""
    try:
        return f"{date.fromisoformat(iso_d or ''):%d.%m.%Y}"
    except ValueError:
        return iso_d or ""


def workday_view(days: list[dict], requests: list[dict], me_id: str, people: dict[str, str], online: bool,
                 now: Optional[datetime] = None, prompt: bool = False, tz=None) -> dict[str, Any]:
    """Модель таймера и запросов для боковой панели и раздела «Рабочее время».

    days — свои рабочие дни (кэш с неотправленными нажатиями); requests — запросы на фактическое начало (свои и,
    руководителю, сотрудников); prompt — показать окно «Начните рабочий день» (решает SyncClient.workday_prompt_needed).
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = _local(now, tz).date()
    d = _today_day(days, me_id, today)
    events = (d or {}).get("events") or []
    state = wd.day_state(events, now, (d or {}).get("approved_start_at"))
    st = state["status"]
    reqs = [_request_row(r, me_id, people, online, tz) for r in requests or []]
    reqs.sort(key=lambda x: x["status"] != "pending")      # на согласовании — сверху (дальше порядок сервера)
    day_reqs = [r for r in reqs if d and r["workday_id"] == d.get("id")]
    pending_req = next((r for r in day_reqs if r["status"] == "pending"), None)
    window_ok = bool(state["pressed_at"]) and now - state["pressed_at"] <= wd.REQUEST_WINDOW
    can_request = bool(d) and window_ok and pending_req is None
    if not d:
        why_not = "Сначала начните рабочий день."
    elif pending_req:
        why_not = "Запрос уже на согласовании у руководителя."
    elif not window_ok:
        why_not = "Фактическое начало можно указать только для сегодняшнего рабочего дня."
    else:
        why_not = ""
    # Нажатия дня — по часам компьютера; неотправленные помечены.
    ev_rows = [{"label": wd.EVENT_LABELS_RU.get(e.get("event"), e.get("event") or ""),
                "time": _hm(wd._opt_dt(e.get("device_time")), tz), "local": bool(e.get("local")),
                "event": e.get("event") or ""} for e in wd._sorted(events)]
    pressed = state["pressed_at"]
    default_start = _local(pressed - timedelta(hours=1), tz) if pressed else None
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
        "prompt": bool(prompt) and st == "not_started",
        "my_requests": [r for r in reqs if r["is_own"]],
        "review": [r for r in reqs if not r["is_own"]],
        "to_decide": sum(1 for r in reqs if r["may_decide"]),
        "online": online,
    }


def parse_start_time(text: str, workday: dict, now: Optional[datetime] = None, tz=None) -> dict[str, Any]:
    """Поле «Фактическое начало» формы «Я начал раньше» («08:00» по часам компьютера) → время ISO с поясом для
    workday.start_request, с проверками ядра (раньше нажатия, не раньше чем за 16 ч). Если «ЧЧ:ММ» позже нажатия, а
    день начат после полуночи, — это вчерашний вечер (ночная смена). {ok, start_at, message}."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    t = (text or "").strip().replace(".", ":")
    try:
        h, m = (int(x) for x in t.split(":"))
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError
    except ValueError:
        return {"ok": False, "start_at": "", "message": "Время — ЧЧ:ММ, например 08:00."}
    st = wd.day_state(workday.get("events") or [], now)
    pressed = st["pressed_at"]
    if pressed is None:
        return {"ok": False, "start_at": "", "message": "Сначала начните рабочий день."}
    loc = _local(pressed, tz)
    cand = loc.replace(hour=h, minute=m, second=0, microsecond=0)
    if cand >= loc:
        # «23:00» при нажатии в 01:30 — вчерашний вечер (ночная смена); иначе время просто позже нажатия.
        if loc - (cand - timedelta(days=1)) <= wd.MAX_EARLIER:
            cand -= timedelta(days=1)
        else:
            return {"ok": False, "start_at": "", "message": "Фактическое начало должно быть раньше нажатия «Начать "
                                                            f"рабочий день» ({loc:%H:%M})."}
    try:
        wd.check_start_request(pressed, cand.astimezone(timezone.utc), now)
    except wd.WorkdayError as e:
        return {"ok": False, "start_at": "", "message": e.message_ru}
    gain = int((pressed - cand.astimezone(timezone.utc)).total_seconds())
    return {"ok": True, "start_at": cand.isoformat(), "message": f"К таймеру добавится {wd.duration_label(gain)} после "
                                                                   "согласования руководителем."}


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
    st = wd.day_state(d.get("events") or [], now, d.get("approved_start_at"))
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
    return {"from_timer": True, "shift_start": wt.time_label(s["shift_start"]), "shift_end": wt.time_label(s["shift_end"]),
            "break_minutes": s["break_minutes"], "note": note}
