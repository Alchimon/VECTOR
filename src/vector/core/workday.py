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

Ответы владельца на A-51 (D-47):

* п. 1 — фактическое начало — это когда не отметили начало рабочего дня **согласно графику работы**: просить можно
  не раньше начала по графику (vector.core.schedule), а не «за 16 часов»;
* пп. 3–4 — рабочий день **обязательно** начинать: окно «Начните рабочий день» настаивает (кнопки «Позже» нет);
* п. 5 — после «Завершить» можно «Продолжить», отсчёт идёт дальше; **забыли завершить** — в конце дня по графику
  напоминание («Завершить» / «Работаю дальше» — напомнить через час / «Закончил раньше»); без ответа 30 минут (или
  программа была закрыта) день завершается автоматически — в конце по графику (или когда истекло «работаю дальше»);
  работал дольше — «фактическое окончание» через согласование руководителя, как фактическое начало.

Ответы владельца на A-53 (D-49):

* п. 1 — «да, но надо исключить тот факт, что человек заработался и не нажал, а он по факту работал»: пока за
  компьютером работают (клавиатура, мышь — программа знает только время последнего нажатия, ничего больше), день
  сам не завершается; ушли — день завершается **временем последней активности** (не раньше конца по графику или
  повторного напоминания): reminder_state, auto_finish_time;
* п. 3 — работу в праздничные и выходные дни отметить, время сверх нормы — «сверхурочно»: day_marks;
* п. 5 — «дать возможность менять» интервалы: повтор напоминания и ожидание до автоматического завершения —
  настройки компании (workday.reminder_repeat, workday.auto_finish_wait; settings_minutes).

Чистые функции без БД и Qt — общие для сервера (проверка событий при синхронизации) и клиента (живой таймер,
окно «Начните рабочий день», напоминание о конце дня, заполнение смены отчёта).

События дня (неизменяемый журнал): start — начал рабочий день; pause — перерыв; resume — продолжил (после перерыва
или после «Завершить» — если вернулся к работе); finish — завершил рабочий день. Рабочее время — сумма отрезков
start/resume → pause/finish; всё остальное между началом и концом дня — перерывы (в отчёт — «перерыв, мин»).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from . import schedule as sch
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
REQUEST_WINDOW = timedelta(hours=24)  # согласовать фактическое начало / окончание — в течение суток (A-51 п. 2: «да»)
SNOOZE = timedelta(hours=1)          # «Работаю дальше» — напомнить снова через час (A-51 п. 5)
AUTO_FINISH_AFTER = timedelta(minutes=30)   # напоминание без ответа 30 минут — день завершается автоматически
MAX_REASON = 500
# Настройки компании (D-49, A-53 п. 5): ключ → (значение по умолчанию, допустимые минуты). Значения в каталоге
# настроек — строки («60»), как у всех вариантов перечисления (contracts/settings.slice1.json).
SETTING_REMIND = "workday.reminder_repeat"
SETTING_WAIT = "workday.auto_finish_wait"
SETTING_CHOICES = {SETTING_REMIND: (60, (15, 30, 60, 90, 120)), SETTING_WAIT: (30, (15, 30, 60, 120))}
# Отметки рабочего дня (D-49, A-53 п. 3): тип дня по графику и производственному календарю.
DAY_TYPE_LABELS_RU = {"workday": "рабочий день", "short": "сокращённый день", "weekend": "выходной день",
                      "holiday": "праздничный день"}

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


def day_state(events: Iterable[dict], now: datetime, approved_start: Any = None, approved_end: Any = None) -> dict:
    """Состояние рабочего дня на момент now (для живого таймера и для отчёта).

    approved_start — согласованное руководителем фактическое начало (A-49 п. 4): если оно раньше первого «Начать»,
    первый рабочий отрезок начинается с него — таймер сразу показывает и эти часы. approved_end — согласованное
    фактическое окончание (D-47, A-51 п. 5): день завершён автоматически, а сотрудник работал дольше — последний
    отрезок продлевается до него.

    Возвращает {status, started_at, pressed_at, finished_at, intervals [(начало, конец|None)], worked_seconds,
    break_seconds, breaks (число нажатий «Перерыв»), extra_seconds (добавлено согласованным началом),
    extra_end_seconds (добавлено согласованным окончанием), auto_finished (последнее «Завершить» — автоматическое),
    last_event_at}; времена — UTC.
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
    last = evs[-1] if evs else {}
    auto_finished = last.get("event") == "finish" and bool(last.get("auto"))
    last_event_at = _opt_dt(last.get("device_time")) if last else None
    extra_end = 0.0
    appr_end = _opt_dt(approved_end)
    if finished_at is not None and appr_end is not None and appr_end > finished_at and intervals:
        extra_end = (appr_end - finished_at).total_seconds()
        intervals[-1][1] = appr_end
        finished_at = appr_end
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
            "extra_seconds": int(extra), "extra_end_seconds": int(extra_end), "auto_finished": auto_finished,
            "last_event_at": last_event_at}


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


def check_start_request(pressed_at: Optional[datetime], requested: datetime, now: datetime,
                        schedule_start: Optional[datetime], exempt: bool = False) -> None:
    """Фактическое начало дня (A-49 п. 4; D-47 — ответ на A-51 п. 1: «фактическое начало — это когда не отметили
    начало рабочего дня согласно графика работы»): раньше нажатия «Начать», но **не раньше начала по графику**
    (schedule_start — начало рабочего дня по графику сотрудника, vector.core.schedule.window); просить можно, пока не
    прошли сутки от нажатия. По графику день нерабочий (schedule_start = None) или учёт не ведётся — фактическое
    начало не указывается: таймер считает с нажатия."""
    if pressed_at is None:
        raise WorkdayError("not_started", "Сначала начните рабочий день, затем укажите, во сколько он начался на "
                                          "самом деле.", {"field": "start_at"})
    if exempt:
        raise WorkdayError("no_schedule", "Учёт рабочего времени для вас не ведётся — фактическое начало не "
                                          "указывается.", {"field": "start_at"})
    if schedule_start is None:
        raise WorkdayError("not_schedule_day", "По графику работы этот день нерабочий — фактическое начало не "
                                               "указывается: таймер считает с нажатия «Начать».", {"field": "start_at"})
    if pressed_at <= schedule_start:
        raise WorkdayError("not_late", "Вы начали день не позже начала по графику — указывать фактическое начало не "
                                       "нужно.", {"field": "start_at"})
    if requested >= pressed_at:
        raise WorkdayError("not_earlier", "Фактическое начало должно быть раньше нажатия «Начать рабочий день».",
                           {"field": "start_at"})
    if requested < schedule_start:
        raise WorkdayError("before_schedule", "Фактическое начало — не раньше начала рабочего дня по графику.",
                           {"field": "start_at"})
    if now - pressed_at > REQUEST_WINDOW:
        raise WorkdayError("too_late", "Согласовать фактическое начало можно только для сегодняшнего рабочего дня.",
                           {"field": "start_at"})


def check_end_request(state: dict, requested: datetime, now: datetime) -> None:
    """Фактическое окончание дня (D-47, A-51 п. 5): день завершён **автоматически** (напоминание осталось без
    ответа или программа была закрыта), а сотрудник работал дольше — просит считать конец дня позже. Только позже
    автоматического «Завершить», не в будущем, не дальше суток от начала дня; просить можно в течение суток после
    автоматического завершения. После «Продолжить» запрос не нужен — день снова идёт."""
    fin = state.get("finished_at")
    if state.get("status") != "finished" or fin is None:
        raise WorkdayError("not_finished", "Рабочий день не завершён — фактическое окончание не нужно.",
                           {"field": "end_at"})
    if not state.get("auto_finished"):
        raise WorkdayError("not_auto", "День завершён вами — если работали дальше, нажмите «Продолжить».",
                           {"field": "end_at"})
    if requested <= fin:
        raise WorkdayError("not_later", "Фактическое окончание должно быть позже автоматического завершения.",
                           {"field": "end_at"})
    if requested > now:
        raise WorkdayError("in_future", "Фактическое окончание не может быть в будущем.", {"field": "end_at"})
    started = state.get("started_at")
    if started is not None and requested - started > MAX_DAY:
        raise WorkdayError("day_too_long", "Рабочий день не длиннее суток от начала.", {"field": "end_at"})
    if now - fin > REQUEST_WINDOW:
        raise WorkdayError("too_late", "Фактическое окончание можно указать в течение суток после завершения.",
                           {"field": "end_at"})


def due_at(state: dict, schedule_end: Optional[datetime], schedule_minutes: int,
           snooze_until: Optional[datetime] = None, snooze: timedelta = SNOOZE) -> Optional[datetime]:
    """Когда напомнить «рабочий день по графику закончился» (D-47, A-51 п. 5) для идущего или прерванного дня.

    Конец по графику (schedule_end; день нерабочий по графику — начало + длительность дня по графику). Нажал кнопку
    таймера после конца по графику («Продолжить» в 19:00 — значит, на месте) — напомнить через snooze после нажатия
    (по умолчанию час; настройка компании workday.reminder_repeat, D-49); «Работаю дальше» — в конце отсрочки
    (snooze_until). Не дальше суток от начала. Завершённый или не начатый день — None."""
    if state.get("status") not in ("working", "paused") or not state.get("pressed_at"):
        return None
    start = state["pressed_at"]
    due = schedule_end or (start + timedelta(minutes=schedule_minutes))
    last = state.get("last_event_at")
    if last is not None and last >= due:
        due = last + snooze
    if snooze_until is not None and snooze_until > due:
        due = snooze_until
    return min(due, start + MAX_DAY)


def reminder_state(due: Optional[datetime], now: datetime, wait: timedelta = AUTO_FINISH_AFTER,
                   last_activity: Optional[datetime] = None, hard_stop: Optional[datetime] = None) -> str:
    """none — рано (или день не идёт); remind — показать напоминание о конце дня; auto — завершить день
    автоматически (время — auto_finish_time).

    auto — когда с момента напоминания (due) прошло wait (по умолчанию 30 минут; настройка компании
    workday.auto_finish_wait, D-49) **и** столько же за компьютером никто не работал (D-49, A-53 п. 1: «исключить
    тот факт, что человек заработался и не нажал»): last_activity — время последнего нажатия клавиатуры или мыши
    (None — неизвестно, например программа была закрыта). Работают — напоминание висит, день идёт дальше.
    hard_stop — сутки от начала дня (MAX_DAY): дальше день не продлевается, даже если за компьютером работают.

    Порядок в программе важен: сначала решается, пора ли завершать, по активности, записанной раньше, и только
    потом запоминается новая — иначе утреннее нажатие после выключенного на ночь компьютера «продлило» бы вчерашний
    день до утра."""
    if due is None or now < due:
        return "none"
    if hard_stop is not None and now >= hard_stop:
        return "auto"
    quiet_since = max(due, last_activity) if last_activity is not None else due
    return "auto" if now - quiet_since >= wait else "remind"


def auto_finish_time(state: dict, due: datetime, now: datetime, last_activity: Optional[datetime] = None) -> datetime:
    """Во сколько записать автоматическое «Завершить» (D-47; D-49 — A-53 пп. 1–2).

    * на перерыве — момент «Перерыв» (перерыв считается только по кнопке, работы после него не было — A-49 п. 3);
    * иначе — момент напоминания (конец по графику или конец «Работаю дальше»), а если за компьютером работали и
      после него — **время последней активности** («по факту работал»);
    * не позже «сейчас» и суток от начала дня, не раньше последнего нажатия таймера.
    """
    last_ev = state.get("last_event_at")
    if state.get("status") == "paused" and last_ev is not None:
        return last_ev
    at = max(due, last_activity) if last_activity is not None else due
    at = min(at, now)
    if state.get("pressed_at") is not None:
        at = min(at, state["pressed_at"] + MAX_DAY)
    return max(at, last_ev) if last_ev is not None else at


def settings_minutes(config: Optional[Iterable[dict]], key: str) -> int:
    """Минуты из настройки компании key (pull: config — [{key, value}]) — только из допустимых; не задано или
    неизвестное значение — значение по умолчанию (как решил владелец: час и 30 минут, A-52 п. 1, D-47)."""
    default, allowed = SETTING_CHOICES[key]
    for c in config or []:
        if isinstance(c, dict) and c.get("key") == key:
            try:
                v = int(str(c.get("value")))
            except ValueError:
                return default
            return v if v in allowed else default
    return default


def minutes_label(minutes: int) -> str:
    """60 → «час», 90 → «1 ч 30 мин», 30 → «30 минут» — для текстов напоминания."""
    if minutes == 60:
        return "час"
    if minutes % 60 == 0:
        return f"{minutes // 60} ч"
    return f"{minutes // 60} ч {minutes % 60:02d} мин" if minutes > 60 else f"{minutes} минут"


def day_type(s: Optional[dict], day: date, cal: Optional[dict] = None) -> str:
    """Тип дня day для отметок (D-49, A-53 п. 3): holiday — праздник по производственному календарю (график его
    учитывает); weekend — выходной по графику; short — сокращённый предпраздничный; workday — рабочий."""
    s = s or sch.BUILTIN
    kind = sch.day_kind(s, day, cal)
    if kind == "holiday":
        return "holiday"
    if not sch.is_workday(s, day, cal):
        return "weekend"
    return "short" if kind == "short" else "workday"


def norm_minutes(s: Optional[dict], day: date, cal: Optional[dict] = None) -> int:
    """Норма рабочего дня day по графику — без перерыва по графику; сокращённый день — на час короче (как окно
    vector.core.schedule.window); нерабочий день — 0."""
    s = s or sch.BUILTIN
    t = day_type(s, day, cal)
    if t in ("holiday", "weekend"):
        return 0
    length = sch.length_minutes(s)
    brk = int(s.get("break_minutes") or 0)
    if t == "short" and length - sch.SHORT_DAY_MINUTES > brk:
        length -= sch.SHORT_DAY_MINUTES
    return max(0, length - brk)


def day_marks(s: Optional[dict], day: date, worked_minutes: Optional[int], cal: Optional[dict] = None,
              exempt: bool = False) -> dict:
    """Отметки рабочего дня (D-49, A-53 п. 3: «работа в праздничные дни надо отметить… как и выходные, также
    отмечать время, что человек работал сверхурочно»).

    worked_minutes — сколько отработано (таймер или смена отчёта без перерывов). Праздник — всё время «работа в
    праздничный день»; выходной по графику — «работа в выходной день»; рабочий день — сверх нормы по графику
    («сверхурочно», ст. 99 Трудового кодекса РФ: сверх установленной продолжительности). Без округления — целые
    минуты. Учёт не ведётся или времени нет — пустые отметки.

    Возвращает {day_type, day_type_label, title (название праздника), norm_minutes, worked_minutes,
    offday_minutes, overtime_minutes, mark (holiday | weekend | overtime | ""), label, short_label (для строки
    списка), tone}.
    """
    t = day_type(s, day, cal)
    title = ((cal or {}).get(day.isoformat()) or {}).get("title") or "" if t == "holiday" else ""
    norm = norm_minutes(s, day, cal)
    worked = max(0, int(worked_minutes or 0))
    out = {"day_type": t, "day_type_label": DAY_TYPE_LABELS_RU[t], "title": title, "norm_minutes": norm,
           "worked_minutes": worked, "offday_minutes": 0, "overtime_minutes": 0, "mark": "", "label": "",
           "short_label": "", "tone": "neutral"}
    if exempt or worked <= 0:
        return out
    if t in ("holiday", "weekend"):
        what = "праздничный день" + (f" ({title})" if title else "") if t == "holiday" else "выходной день"
        out.update(offday_minutes=worked, mark=t, label=f"Работа в {what} — {duration_label(worked * 60)}",
                   short_label=("работа в праздник" if t == "holiday" else "работа в выходной"),
                   tone="danger" if t == "holiday" else "warning")
    elif worked > norm:
        over = worked - norm
        out.update(overtime_minutes=over, mark="overtime",
                   label=f"Сверхурочно — {duration_label(over * 60)} (норма по графику {duration_label(norm * 60)})",
                   short_label=f"сверхурочно {duration_label(over * 60)}", tone="warning")
    return out


def check_early_finish(state: dict, at: datetime, now: datetime) -> None:
    """«Закончил раньше — в …» в напоминании о конце дня: завершить день прошедшим временем — не раньше последнего
    нажатия и не в будущем (меньше времени себе — согласование не нужно)."""
    last = state.get("last_event_at")
    if last is not None and at < last:
        raise WorkdayError("time_order", "Время окончания раньше последнего нажатия таймера.", {"field": "end_at"})
    if at > now:
        raise WorkdayError("in_future", "Время окончания не может быть в будущем.", {"field": "end_at"})


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


def need_start_prompt(today_events: Iterable[dict], exempt: bool = False) -> bool:
    """Показать ли окно «Начните рабочий день» (A-49 п. 4; D-47 — A-51 пп. 3–4: «рабочий день надо обязательно
    начинать», окно «настаивает»): сегодня таймер не запускали — окно показывается, пока день не начат (кнопки
    «Позже» нет; можно только начать день или выйти из программы). Учёт рабочего времени не ведётся — не показывать."""
    return not exempt and status_of(today_events) == "not_started"
