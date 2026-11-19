"""Ответы владельца на A-53 (D-49) и уточнение A-54, чистые функции ядра vector.core.workday:

* A-54 — ввод с клавиатуры/мыши не отслеживается; после ожидания без ответа «Завершить» записывается по времени
  напоминания; на перерыве — моментом «Перерыв»; дальше суток от начала день не продлевается;
* п. 3 — отметки дня: работа в праздничный день, в выходной по графику, сверхурочно (сверх нормы по графику);
* п. 5 — интервалы напоминания и ожидания — настройки компании (только допустимые значения).
"""
from datetime import date, datetime, timedelta, timezone

from vector.core import prodcal as pc
from vector.core import schedule as sch
from vector.core import workday as wd

UTC = timezone.utc
S = dict(sch.BUILTIN)                                   # 09:00–18:00, пн–пт, перерыв 60 мин → норма 8 ч
CAL = pc.effective((2025, 2026, 2027), {}, [], [])      # встроенный календарь России


def test_day_marks_overtime_weekend_holiday_short_and_exempt():
    """Норма — длительность по графику без перерыва (сокращённый день — на час короче); сверх неё — сверхурочно;
    в выходной по графику и в праздник всё время — работа в выходной / праздничный день; учёт не ведётся — пусто."""
    mon = date(2026, 11, 16)
    m = wd.day_marks(S, mon, 540, CAL)
    assert (m["day_type"], m["norm_minutes"], m["overtime_minutes"], m["mark"], m["tone"]) == \
        ("workday", 480, 60, "overtime", "warning")
    assert m["label"] == "Сверхурочно — 1 ч 00 мин (норма по графику 8 ч 00 мин)"
    assert m["short_label"] == "сверхурочно 1 ч 00 мин"
    assert wd.day_marks(S, mon, 480, CAL)["mark"] == ""                  # ровно норма — без отметки
    assert wd.day_marks(S, mon, 543, CAL)["overtime_minutes"] == 63      # без округления: целые минуты как есть
    short = wd.day_marks(S, date(2026, 11, 3), 450, CAL)                 # сокращённый предпраздничный
    assert (short["day_type"], short["norm_minutes"], short["overtime_minutes"]) == ("short", 420, 30)
    hol = wd.day_marks(S, date(2026, 11, 4), 125, CAL)                   # День народного единства
    assert (hol["day_type"], hol["mark"], hol["offday_minutes"], hol["overtime_minutes"], hol["tone"]) == \
        ("holiday", "holiday", 125, 0, "danger")
    assert hol["label"] == "Работа в праздничный день (День народного единства) — 2 ч 05 мин"
    assert hol["short_label"] == "работа в праздник"
    sat = wd.day_marks(S, date(2026, 11, 21), 190, CAL)
    assert (sat["day_type"], sat["mark"], sat["label"]) == ("weekend", "weekend", "Работа в выходной день — 3 ч 10 мин")
    # Федеральный праздник отмечается и без календаря графика, но само окно/расписание смены не сдвигается.
    no_calendar = {**S, "use_calendar": False}
    holiday_without_schedule_shift = wd.day_marks(no_calendar, date(2026, 11, 4), 480, CAL)
    assert (holiday_without_schedule_shift["day_type"], holiday_without_schedule_shift["mark"],
            holiday_without_schedule_shift["offday_minutes"]) == ("holiday", "holiday", 480)
    assert sch.window(no_calendar, date(2026, 11, 4), cal=CAL) == sch.window(no_calendar, date(2026, 11, 4))
    # Ежедневный график: суббота — рабочий день.
    assert wd.day_marks({**S, "weekdays": "1111111"}, date(2026, 11, 21), 480, CAL)["mark"] == ""
    assert wd.day_marks(S, date(2026, 11, 21), 190, CAL, exempt=True)["mark"] == ""
    assert wd.day_marks(S, mon, None, CAL)["mark"] == "" and wd.day_marks(None, mon, 600)["overtime_minutes"] == 120


def test_accounting_period_norm_uses_production_calendar_not_personal_shift():
    """Норма рассчитывается по рабочей неделе и производственному календарю; индивидуальный график не подменяет её."""
    start, end = date(2026, 11, 2), date(2026, 11, 6)  # 4 рабочих дня, 3 ноября сокращён, 4 ноября праздник
    assert wd.period_norm_minutes(start, end, CAL) == 1860
    assert wd.period_norm_minutes(start, end, CAL, weekly_minutes=2160) == 1668
    assert wd.period_norm_minutes(date(2026, 11, 16), date(2026, 11, 22), {}) == 2400
    try:
        wd.period_norm_minutes(start, start + timedelta(days=366), CAL)
    except ValueError:
        pass
    else:
        raise AssertionError("period longer than a year must be rejected")


def test_auto_finish_uses_reminder_boundary_without_input_monitoring():
    """Без отслеживания клавиатуры и мыши: после ожидания завершаем по напоминанию; на перерыве — по кнопке «Перерыв»."""
    t0 = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    due = datetime(2026, 11, 16, 18, 0, tzinfo=UTC)
    st = wd.day_state([{"seq": 1, "event": "start", "device_time": t0.isoformat()}], due)
    at = lambda h, m: datetime(2026, 11, 16, h, m, tzinfo=UTC)          # noqa: E731
    assert wd.reminder_state(due, at(17, 59)) == "none"
    assert wd.reminder_state(due, at(18, 29)) == "remind"
    assert wd.reminder_state(due, at(18, 30)) == "auto"
    assert wd.auto_finish_time(st, due, at(18, 30)) == due
    # Настройка «ждать час»: в 18:59 ещё напоминание, в 19:00 — завершить.
    assert wd.reminder_state(due, at(18, 59), timedelta(hours=1)) == "remind"
    assert wd.reminder_state(due, at(19, 0), timedelta(hours=1)) == "auto"
    # Сутки от начала — предел.
    nxt = t0 + wd.MAX_DAY
    assert wd.reminder_state(due, nxt, hard_stop=nxt) == "auto"
    assert wd.auto_finish_time(st, due, nxt + timedelta(minutes=5), nxt) == nxt
    # На перерыве — моментом «Перерыв» (перерыв только по кнопке, A-49 п. 3).
    paused = wd.day_state([{"seq": 1, "event": "start", "device_time": t0.isoformat()},
                           {"seq": 2, "event": "pause", "device_time": at(17, 30).isoformat()}], due)
    assert wd.auto_finish_time(paused, due, at(20, 10)) == at(17, 30)


def test_settings_minutes_and_snooze_interval():
    """Настройки компании: не задано / чужое значение — час и 30 минут; допустимые — как задано; «Работаю дальше»
    после нажатия таймера уже после конца дня — через заданный интервал."""
    assert wd.settings_minutes([], wd.SETTING_REMIND) == 60
    assert wd.settings_minutes(None, wd.SETTING_WAIT) == 30
    cfg = [{"key": wd.SETTING_REMIND, "value": "30"}, {"key": wd.SETTING_WAIT, "value": "120"}]
    assert (wd.settings_minutes(cfg, wd.SETTING_REMIND), wd.settings_minutes(cfg, wd.SETTING_WAIT)) == (30, 120)
    assert wd.settings_minutes([{"key": wd.SETTING_WAIT, "value": "7"}], wd.SETTING_WAIT) == 30
    assert wd.settings_minutes([{"key": wd.SETTING_WAIT, "value": "много"}], wd.SETTING_WAIT) == 30
    assert [wd.minutes_label(m) for m in (15, 30, 60, 90, 120)] == ["15 минут", "30 минут", "час", "1 ч 30 мин", "2 ч"]
    t0 = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    late = datetime(2026, 11, 16, 19, 0, tzinfo=UTC)
    st = wd.day_state([{"seq": 1, "event": "start", "device_time": t0.isoformat()},
                       {"seq": 2, "event": "pause", "device_time": late.isoformat()},
                       {"seq": 3, "event": "resume", "device_time": late.isoformat()}], late)
    end = datetime(2026, 11, 16, 18, 0, tzinfo=UTC)
    assert wd.due_at(st, end, 540) == late + timedelta(hours=1)
    assert wd.due_at(st, end, 540, snooze=timedelta(minutes=30)) == late + timedelta(minutes=30)
