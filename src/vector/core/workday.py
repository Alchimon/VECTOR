"""Таймер рабочего дня (этап 7, R-20; decision log D-46 — ответы владельца на A-49 пп. 2–4).

Ответы владельца, из которых сделан модуль:

* п. 2 — «без округления: сколько натикало, столько натикало» — время считается по секундам событий, ничего не
  округляется (в дневной отчёт смена уходит с точностью до минуты: формат смены — «ЧЧ:ММ»);
* п. 3 — перерыв считается, только если нажата кнопка «Перерыв» (программа сама перерывы не угадывает);
* п. 4 — не нажал таймер — окно «Начните рабочий день»; пропустил — позже, через согласование руководителя, выбирает
  фактическое начало сегодняшнего дня, и таймер считает от него: день начался в 8, таймер запустили в 10 — после
  согласования уже видно 2 часа;
* п. 5 — день по часам компьютера сотрудника (как есть): время события — время устройства (как у таймера операций
  наряда, A-44), сервер отклоняет только время из будущего.

Чистые функции без БД и Qt — общие для сервера (проверка событий при синхронизации) и клиента (живой таймер,
окно «Начните рабочий день», заполнение смены отчёта).

События дня (неизменяемый журнал): start — начал рабочий день; pause — перерыв; resume — продолжил (после перерыва
или после «Завершить» — если вернулся к работе); finish — завершил рабочий день. Рабочее время — сумма отрезков
start/resume → pause/finish; всё остальное между началом и концом дня — перерывы (в отчёт — «перерыв, мин»).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .lifecycle import LifecycleError

EVENTS = ("start", "pause", "resume", "finish")
EVENT_LABELS_RU = {"start": "Начал рабочий день", "pause": "Перерыв", "resume": "Продолжил работу",
                   "finish": "Завершил рабочий день"}
STATUSES = ("not_started", "working", "paused", "finished")
STATUS_LABELS_RU = {"not_started": "Не начат", "working": "Идёт", "paused": "Перерыв", "finished": "Завершён"}
STATUS_TONES = {"not_started": "neutral", "working": "success", "paused": "warning", "finished": "info"}
# Какие события допустимы в каждом состоянии. После «Завершить» можно вернуться к работе («Продолжить»): время
# между концом и возвращением — не работа (как перерыв).
TRANSITIONS = {"not_started": ("start",), "working": ("pause", "finish"), "paused": ("resume", "finish"),
               "finished": ("resume",)}

MAX_DAY = timedelta(hours=24)        # рабочий день — не длиннее суток от начала (ночная смена укладывается)
MAX_EARLIER = timedelta(hours=16)    # фактическое начало — не раньше, чем за 16 часов до нажатия «Начать»
REQUEST_WINDOW = timedelta(hours=24)  # согласовать фактическое начало — пока не прошли сутки от начала дня
MAX_REASON = 500

REQUEST_STATUSES = ("pending", "approved", "rejected")
REQUEST_LABELS_RU = {"pending": "На согласовании", "approved": "Согласовано", "rejected": "Отклонено"}
REQUEST_TONES = {"pending": "warning", "approved": "success", "rejected": "danger"}


class WorkdayError(LifecycleError):
    """Нарушение правила таймера рабочего дня (код, сообщение по-русски, детали — поле формы)."""


def parse_dt(v: Any, field: str = "device_time") -> datetime:
    """ISO-8601 С часовым поясом (или datetime) → datetime UTC. Без пояса — ошибка: чьё это «10:00»?"""
    if isinstance(v, datetime):
        dt = v
    else:
        if not isinstance(v, str):
            raise WorkdayError("invalid_datetime", "Время: ожидается ISO-8601 с часовым поясом.", {"field": field})
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise WorkdayError("invalid_datetime", "Время: неверный формат.", {"field": field})
    if dt.tzinfo is None:
        raise WorkdayError("invalid_datetime", "Время: укажите часовой пояс.", {"field": field})
    return dt.astimezone(timezone.utc)


def _opt_dt(v: Any) -> Optional[datetime]:
    """Время из события / строки БД или None (неверное — None: состояние считается по тому, что можно прочесть)."""
    if v is None:
        return None
    try:
        return parse_dt(v)
    except WorkdayError:
        return None


def _sorted(events: Iterable[dict]) -> list[dict]:
    """События по порядку записи (seq), а при равенстве — по времени: так их принимал сервер."""
    return sorted(events or [], key=lambda e: (e.get("seq") or 0, str(e.get("device_time") or e.get("at") or "")))


def status_of(events: Iterable[dict]) -> str:
    """Состояние дня по событиям: не начат / идёт / перерыв / завершён."""
    st = "not_started"
    for e in _sorted(events):
        ev = e.get("event")
        st = {"start": "working", "resume": "working", "pause": "paused", "finish": "finished"}.get(ev, st)
    return st


def check_event(events: Iterable[dict], event: str, at: datetime) -> None:
    """Можно ли записать событие event в момент at после уже записанных events (сервер — при приёме из очереди,
    клиент — до постановки в очередь). Порядок и время — строго по журналу: сервер ничего не «додумывает»."""
    if event not in EVENTS:
        raise WorkdayError("invalid_event", "Событие — start, pause, resume или finish.", {"field": "event"})
    evs = _sorted(events)
    st = status_of(evs)
    if event not in TRANSITIONS[st]:
        raise WorkdayError("invalid_transition",
                           f"Рабочий день «{STATUS_LABELS_RU[st]}» — «{EVENT_LABELS_RU[event]}» сейчас нельзя.",
                           {"field": "event", "status": st})
    if evs:
        last = _opt_dt(evs[-1].get("device_time"))
        if last is not None and at < last:
            raise WorkdayError("time_order", "Время события раньше предыдущего — проверьте часы компьютера.",
                               {"field": "device_time"})
        first = _opt_dt(evs[0].get("device_time"))
        if first is not None and at - first > MAX_DAY:
            raise WorkdayError("day_too_long", "Рабочий день длиннее суток: начните новый день («Начать рабочий "
                                               "день» завтрашним числом).", {"field": "device_time"})


def day_state(events: Iterable[dict], now: datetime, approved_start: Any = None) -> dict:
    """Состояние рабочего дня на момент now (для живого таймера и для отчёта).

    approved_start — согласованное руководителем фактическое начало (A-49 п. 4): если оно раньше первого «Начать»,
    первый рабочий отрезок начинается с него — таймер сразу показывает и эти часы.

    Возвращает {status, started_at, pressed_at, finished_at, intervals [(начало, конец|None)], worked_seconds,
    break_seconds, breaks (число нажатий «Перерыв»), extra_seconds (добавлено согласованным началом)}; времена — UTC.
    """
    evs = _sorted(events)
    intervals: list[list] = []
    breaks = 0
    finished_at = None
    for e in evs:
        at = _opt_dt(e.get("device_time"))
        ev = e.get("event")
        if at is None:
            continue
        if ev in ("start", "resume"):
            if not intervals or intervals[-1][1] is not None:
                intervals.append([at, None])
            finished_at = None
        elif ev in ("pause", "finish"):
            if intervals and intervals[-1][1] is None:
                intervals[-1][1] = at
            if ev == "pause":
                breaks += 1
            else:
                finished_at = at
    pressed = intervals[0][0] if intervals else None
    extra = 0.0
    appr = _opt_dt(approved_start)
    if intervals and appr is not None and appr < intervals[0][0]:
        extra = (intervals[0][0] - appr).total_seconds()
        intervals[0][0] = appr
    now = now.astimezone(timezone.utc)
    worked = sum(((b or now) - a).total_seconds() for a, b in intervals if (b or now) > a)
    # Конец дня — «Завершить»; пока день не завершён — сейчас (идущий перерыв тоже виден как перерыв).
    span_end = finished_at or (now if intervals else None)
    span = (span_end - intervals[0][0]).total_seconds() if intervals and span_end else 0.0
    return {"status": status_of(evs), "started_at": intervals[0][0] if intervals else None, "pressed_at": pressed,
            "finished_at": finished_at, "intervals": [(a, b) for a, b in intervals],
            "worked_seconds": max(0, int(worked)), "break_seconds": max(0, int(span - worked)), "breaks": breaks,
            "extra_seconds": int(extra)}


def duration_label(seconds: Optional[int]) -> str:
    """Секунды → «7 ч 05 мин» / «45 мин» / «0 мин» (для итогов; живой таймер — clock_label)."""
    s = max(0, int(seconds or 0))
    h, m = s // 3600, s % 3600 // 60
    return f"{h} ч {m:02d} мин" if h else f"{m} мин"


def clock_label(seconds: Optional[int]) -> str:
    """Секунды → «ЧЧ:ММ:СС» для живого таймера («сколько натикало»)."""
    s = max(0, int(seconds or 0))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def shift_from_day(state: dict, day: date, tz=None) -> Optional[dict]:
    """Смена дневного отчёта из таймера: {shift_start, shift_end (минуты от полуночи day; после полуночи — больше
    1440), break_minutes}. Без округления: начало и конец — минута события (секунды отбрасываются), перерыв —
    сумма перерывов в целых минутах. None — день не начат или ещё не завершён (смену заполнит сотрудник / отчёт
    соберётся, когда день завершён)."""
    if not state.get("started_at") or not state.get("finished_at"):
        return None

    def minute(dt: datetime) -> int:
        loc = dt.astimezone(tz) if tz is not None else dt.astimezone()
        base = datetime.combine(day, datetime.min.time(), tzinfo=loc.tzinfo)
        return int((loc - base).total_seconds() // 60)
    start, end = minute(state["started_at"]), minute(state["finished_at"])
    if start < 0 or start >= 24 * 60 or end <= start:
        return None
    end = min(end, start + 24 * 60)
    brk = min(state.get("break_seconds", 0) // 60, end - start)
    return {"shift_start": start, "shift_end": end, "break_minutes": int(brk)}


def check_start_request(pressed_at: Optional[datetime], requested: datetime, now: datetime) -> None:
    """Фактическое начало дня (A-49 п. 4): только раньше нажатия «Начать», не раньше чем за MAX_EARLIER до него и не в
    будущем; просить можно, пока не прошли сутки от начала дня («сегодняшний день»)."""
    if pressed_at is None:
        raise WorkdayError("not_started", "Сначала начните рабочий день, затем укажите, во сколько он начался на "
                                          "самом деле.", {"field": "start_at"})
    if requested >= pressed_at:
        raise WorkdayError("not_earlier", "Фактическое начало должно быть раньше нажатия «Начать рабочий день».",
                           {"field": "start_at"})
    if pressed_at - requested > MAX_EARLIER:
        raise WorkdayError("too_early", "Фактическое начало — не раньше чем за 16 часов до нажатия «Начать».",
                           {"field": "start_at"})
    if now - pressed_at > REQUEST_WINDOW:
        raise WorkdayError("too_late", "Согласовать фактическое начало можно только для сегодняшнего рабочего дня.",
                           {"field": "start_at"})


def parse_reason(v: Any, required: bool = True, field: str = "reason", what: str = "причину") -> Optional[str]:
    """Причина / комментарий: обрезка пробелов, длина; пусто и обязательно — ошибка с полем формы."""
    s = (v or "").strip() if isinstance(v, str) or v is None else None
    if s is None:
        raise WorkdayError("invalid_text", f"Укажите {what} текстом.", {"field": field})
    if not s:
        if required:
            raise WorkdayError("field_required", f"Укажите {what}.", {"field": field})
        return None
    if len(s) > MAX_REASON:
        raise WorkdayError("too_long", f"Слишком длинно: не больше {MAX_REASON} символов.", {"field": field})
    return s


def need_start_prompt(today_events: Iterable[dict], dismissed: bool) -> bool:
    """Показать ли окно «Начните рабочий день» (A-49 п. 4): сегодня таймер не запускали и окно сегодня ещё не
    закрывали кнопкой «Позже»."""
    return status_of(today_events) == "not_started" and not dismissed
