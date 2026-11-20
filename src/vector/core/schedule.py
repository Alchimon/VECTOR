"""Графики работы (этап 7, R-20; decision log D-47 — ответы владельца на A-51).

Ответ владельца на A-51 п. 1: «фактическое начало — это когда не отметили начало рабочего дня согласно графика
работы». Значит, у каждого сотрудника есть **график работы**: во сколько начинается и заканчивается рабочий день, по
каким дням недели и сколько длится перерыв. От графика считаются:

* граница «фактического начала дня» — не раньше начала по графику (vector.core.workday.check_start_request);
* напоминание «рабочий день по графику закончился» и автоматическое завершение забытого дня (A-51 п. 5);
* подсказки в окне «Начните рабочий день».

Праздники и переносы выходных — производственный календарь (D-48, ответ на A-52 п. 3; vector.core.prodcal): если
график его учитывает (по умолчанию — да), в праздник день нерабочий, в рабочую субботу по переносу — рабочий, в
предпраздничный день конец на час раньше.

Откуда берётся график сотрудника (resolve): свой (назначен лично) → подразделения (своего, а если не назначен —
ближайшего вышестоящего по оргструктуре) → график «по умолчанию» компании → встроенный BUILTIN (09:00–18:00, пн–пт,
перерыв 60 мин), пока администратор не завёл ни одного. Лично можно отметить и «учёт рабочего времени не ведётся» —
для служебных учётных записей: такому сотруднику не показывается окно «Начните рабочий день».

Времена графика — «настенные» (ЧЧ:ММ по часам компьютера сотрудника, как и сам таймер, A-49 п. 5): окно дня
переводится в момент времени с поясом компьютера. Ночной график — конец раньше начала (22:00–06:00): день
заканчивается утром следующего дня.

Чистые функции без БД и Qt — общие для сервера и клиента.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Iterable, Optional

from .lifecycle import LifecycleError
from .prodcal import SHORT_MINUTES  # одно значение на ядро: календарь и график

MAX_NAME = 120
MAX_BREAK = 240                      # перерыв по графику — не больше 4 часов
WEEKDAY_SHORT_RU = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
SHORT_DAY_MINUTES = SHORT_MINUTES    # предпраздничный (сокращённый) день — на час короче (ст. 95 ТК РФ; D-48)

# Встроенный график — действует, пока администратор не завёл своих (и не отметил ни один «по умолчанию»).
BUILTIN = {"id": "", "name": "Стандартный", "start_time": "09:00", "end_time": "18:00", "break_minutes": 60,
           "weekdays": "1111100", "is_default": True, "active": True, "builtin": True, "use_calendar": True}


class ScheduleError(LifecycleError):
    """Нарушение правила графика работы (код, сообщение по-русски, детали — поле формы)."""


def parse_hhmm(v: Any, field: str) -> int:
    """«ЧЧ:ММ» → минуты от полуночи (0…1439). Иначе — ошибка с полем формы."""
    s = v.strip() if isinstance(v, str) else ""
    parts = s.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts) or len(parts[1]) != 2:
        raise ScheduleError("invalid_time", "Время — ЧЧ:ММ, например 08:00.", {"field": field})
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ScheduleError("invalid_time", "Время — от 00:00 до 23:59.", {"field": field})
    return h * 60 + m


def hhmm(minutes: int) -> str:
    """Минуты от полуночи → «ЧЧ:ММ» (после полуночи — по модулю суток)."""
    m = int(minutes) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def length_minutes(s: dict) -> int:
    """Длительность рабочего дня по графику в минутах (вместе с перерывом); ночной — через полночь."""
    a, b = parse_hhmm(s["start_time"], "start_time"), parse_hhmm(s["end_time"], "end_time")
    return (b - a) % (24 * 60)


def is_night(s: dict) -> bool:
    """Ночной ли график (конец раньше начала: день заканчивается утром следующего дня)."""
    return parse_hhmm(s["end_time"], "end_time") < parse_hhmm(s["start_time"], "start_time")


def check_schedule(p: dict) -> dict:
    """Проверить поля графика из формы администратора и вернуть нормализованные: name, start_time, end_time,
    break_minutes, weekdays (7 знаков «1/0», пн…вс). Правила: название обязательно; начало ≠ конец; перерыв —
    целые минуты от 0 до 240 и короче рабочего дня; хотя бы один рабочий день недели."""
    name = (p.get("name") or "").strip() if isinstance(p.get("name"), str) or p.get("name") is None else None
    if not name:
        raise ScheduleError("field_required", "Укажите название графика.", {"field": "name"})
    if len(name) > MAX_NAME:
        raise ScheduleError("too_long", f"Название — не больше {MAX_NAME} символов.", {"field": "name"})
    a = parse_hhmm(p.get("start_time"), "start_time")
    b = parse_hhmm(p.get("end_time"), "end_time")
    if a == b:
        raise ScheduleError("same_time", "Начало и конец рабочего дня совпадают.", {"field": "end_time"})
    brk = p.get("break_minutes", 0)
    if isinstance(brk, bool) or not isinstance(brk, int) or not 0 <= brk <= MAX_BREAK:
        raise ScheduleError("invalid_break", f"Перерыв — целые минуты от 0 до {MAX_BREAK}.", {"field": "break_minutes"})
    if brk >= (b - a) % (24 * 60):
        raise ScheduleError("break_too_long", "Перерыв длиннее рабочего дня.", {"field": "break_minutes"})
    wd = p.get("weekdays")
    if not isinstance(wd, str) or len(wd) != 7 or set(wd) - {"0", "1"}:
        raise ScheduleError("invalid_weekdays", "Рабочие дни — семь отметок с понедельника по воскресенье.",
                            {"field": "weekdays"})
    if "1" not in wd:
        raise ScheduleError("no_weekdays", "Отметьте хотя бы один рабочий день недели.", {"field": "weekdays"})
    return {"name": name, "start_time": hhmm(a), "end_time": hhmm(b), "break_minutes": brk, "weekdays": wd}


def weekdays_label(wd: str) -> str:
    """«1111100» → «пн–пт»; «1111111» → «ежедневно»; «1010100» → «пн, ср, пт»; подряд идущие — диапазоном."""
    if wd == "1111111":
        return "ежедневно"
    days = [i for i, c in enumerate(wd or "") if c == "1"]
    out, i = [], 0
    while i < len(days):
        j = i
        while j + 1 < len(days) and days[j + 1] == days[j] + 1:
            j += 1
        if j - i >= 2:
            out.append(f"{WEEKDAY_SHORT_RU[days[i]]}–{WEEKDAY_SHORT_RU[days[j]]}")
        else:
            out.extend(WEEKDAY_SHORT_RU[d] for d in days[i:j + 1])
        i = j + 1
    return ", ".join(out) or "—"


def label(s: Optional[dict]) -> str:
    """«08:00–17:00, пн–пт, перерыв 60 мин» (ночной — с пометкой). None — «учёт рабочего времени не ведётся»."""
    if s is None:
        return "учёт рабочего времени не ведётся"
    brk = int(s.get("break_minutes") or 0)
    night = " (ночной — до утра)" if is_night(s) else ""
    cal = "" if uses_calendar(s) else ", без производственного календаря"
    return (f"{s['start_time']}–{s['end_time']}{night}, {weekdays_label(s['weekdays'])}"
            + (f", перерыв {brk} мин" if brk else ", без перерыва") + cal)


def full_label(s: Optional[dict]) -> str:
    """«Пятидневка: 08:00–17:00, пн–пт, перерыв 60 мин» — название и правило."""
    return label(None) if s is None else f"{s['name']}: {label(s)}"


def uses_calendar(s: Optional[dict]) -> bool:
    """Учитывает ли график производственный календарь (D-48, A-52 п. 3). Нет отметки — учитывает (пятидневка);
    круглосуточным сменам администратор её снимает."""
    return bool(s) and s.get("use_calendar", True) is not False


def day_kind(s: Optional[dict], day: date, cal: Optional[dict] = None) -> Optional[str]:
    """Особый ли день day по производственному календарю для графика s: holiday | short | workday | None (обычный
    день или график календарь не учитывает). cal — действующий календарь {ГГГГ-ММ-ДД: {kind, title}}
    (vector.core.prodcal.effective)."""
    if not cal or not uses_calendar(s):
        return None
    info = cal.get(day.isoformat())
    return info.get("kind") if info else None


def is_workday(s: dict, day: date, cal: Optional[dict] = None) -> bool:
    """Рабочий ли день day по графику: по дням недели графика, а если график учитывает производственный календарь —
    праздник нерабочий, рабочий выходной (перенос) рабочий, сокращённый — рабочий (D-48)."""
    kind = day_kind(s, day, cal)
    if kind == "holiday":
        return False
    if kind in ("workday", "short"):
        return True
    return (s.get("weekdays") or "0000000")[day.weekday()] == "1"


def window(s: Optional[dict], day: date, tz: Optional[tzinfo] = None,
           cal: Optional[dict] = None) -> Optional[tuple[datetime, datetime]]:
    """Рабочий день day по графику — (начало, конец) с поясом tz (по умолчанию — пояс этого компьютера), в UTC.
    None — по графику день нерабочий (или учёт не ведётся). Ночной график: конец — утром следующего дня.

    cal — действующий производственный календарь (D-48, A-52 п. 3): в праздник — None; в рабочий выходной по
    переносу — окно по времени графика; в сокращённый (предпраздничный) день конец на час раньше (ст. 95 ТК РФ), если
    день после этого длиннее перерыва."""
    if s is None or not is_workday(s, day, cal):
        return None
    a = parse_hhmm(s["start_time"], "start_time")
    tzi = tz or datetime.now().astimezone().tzinfo
    start = datetime.combine(day, datetime.min.time(), tzinfo=tzi) + timedelta(minutes=a)
    minutes = length_minutes(s)
    if day_kind(s, day, cal) == "short" and minutes - SHORT_DAY_MINUTES > int(s.get("break_minutes") or 0):
        minutes -= SHORT_DAY_MINUTES
    end = start + timedelta(minutes=minutes)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def resolve(user_id: str, unit_id: Optional[str], parent_of: dict[str, Optional[str]], links: Iterable[dict],
            schedules: Iterable[dict], unit_names: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """График сотрудника: {schedule (dict | None — учёт не ведётся), exempt, source (user|unit|default|builtin),
    source_label («назначен лично» / «подразделения «…»» / «по умолчанию в компании» / «встроенный»)}.

    links — назначения {target_kind (user|unit), target_id, schedule_id, exempt}; parent_of — «входит в» для
    подразделений (оргструктура); schedules — все графики (в архиве не назначаются: такое назначение пропускается).
    """
    by_id = {s["id"]: s for s in schedules if s.get("active", True)}
    lk = {(x["target_kind"], x["target_id"]): x for x in links}
    names = unit_names or {}
    own = lk.get(("user", user_id))
    if own is not None:
        if own.get("exempt"):
            return {"schedule": None, "exempt": True, "source": "user", "source_label": "отмечено лично"}
        if own.get("schedule_id") in by_id:
            return {"schedule": by_id[own["schedule_id"]], "exempt": False, "source": "user",
                    "source_label": "назначен лично"}
    seen: set[str] = set()
    u = unit_id
    while u and u not in seen:            # защита от круга (сервер его не допускает, но данные читаются как есть)
        seen.add(u)
        x = lk.get(("unit", u))
        if x is not None and x.get("schedule_id") in by_id:
            return {"schedule": by_id[x["schedule_id"]], "exempt": False, "source": "unit", "unit_id": u,
                    "source_label": f"подразделения «{names.get(u, '—')}»"}
        u = parent_of.get(u)
    default = next((s for s in by_id.values() if s.get("is_default")), None)
    if default is not None:
        return {"schedule": default, "exempt": False, "source": "default", "source_label": "по умолчанию в компании"}
    return {"schedule": dict(BUILTIN), "exempt": False, "source": "builtin",
            "source_label": "встроенный (своих графиков в компании пока нет)"}


def tz_of(v: Any) -> Optional[tzinfo]:
    """Пояс из строки ISO-8601 («…+03:00») — так сервер узнаёт «настенное» время компьютера сотрудника (в базе времена
    хранятся в UTC). Нет пояса или неверный формат — None."""
    if isinstance(v, datetime):
        return v.tzinfo
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).tzinfo
    except ValueError:
        return None
