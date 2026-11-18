"""Рабочее время и дневной отчёт — правила, общие для сервера и клиента (этап 7, R-20; decision log D-42).

Сервер применяет эти правила при выполнении команды, клиент — чтобы проверить ввод до отправки и показать черновик
офлайн так же, как его потом увидит сервер (одна правда на обеих сторонах).

Дневной отчёт — один на сотрудника за рабочий день: смена (начало, конец, перерыв) и строки «на что ушло время»
(проект и задача — если работа проектная, сколько времени, что сделано). Проверяет руководитель отдела (не автор —
«четыре глаза»).

Ответы владельца на A-48 (D-43): отчёт **собирает программа** по тому, что сотрудник делал за день (assemble_report:
действия в программе и таймеры операций), сотрудник **проверяет его сам** (самопроверка — обязательные отметки
SELF_CHECK_ITEMS), после чего отчёт уходит руководителю отдела. Ночные смены поддерживаются: конец раньше начала —
смена заканчивается на следующий день.

Жизненный цикл (03 § 4):

    self_check (на самопроверке — собран программой) ─┐
    draft (черновик — заведён вручную) ────────────────┴→ самопроверка → submitted (на проверке)
    submitted → accepted (принят) | returned (возвращён, с причиной); returned → правка → самопроверка → submitted
    submitted | accepted → reopen (с основанием) → draft  — исправление после отправки (матрица 04)

Время хранится целыми минутами: начало смены — минуты от полуночи (0…1439), конец — минуты от полуночи дня начала
(больше 1440 — следующий день; смена не длиннее суток). Строк не больше, чем «чистое» время смены (смена минус
перерыв); меньше — можно (в карточке видно «не распределено») — A-48 п. 5, «пусть остаётся так».

Ошибки — WorktimeError с машинным кодом и русским сообщением (как LifecycleError в vector.core.lifecycle).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, tzinfo
from typing import Any, Iterable, Optional

from .lifecycle import LifecycleError, parse_date

# Статусы дневного отчёта: подписи и тон метки в интерфейсе.
STATUS_LABELS_RU = {"draft": "Черновик", "self_check": "На самопроверке", "submitted": "На проверке", "accepted": "Принят",
                    "returned": "Возвращён"}
STATUS_TONES = {"draft": "neutral", "self_check": "info", "submitted": "warning", "accepted": "success", "returned": "danger"}
# Где отчёт может править автор (сохранить, пройти самопроверку и отправить на проверку).
EDITABLE = ("draft", "self_check", "returned")

# Самопроверка перед отправкой (A-48 п. 4): все отметки обязательны. Ключ → что сотрудник подтверждает.
SELF_CHECK_ITEMS = {
    "shift": "Начало, конец смены и перерыв — верны",
    "lines": "Учтены все работы за день, лишнего нет",
    "hours": "Часы в строках проверены, в том числе оценки программы",
}
# Откуда строка отчёта: measured — время по таймеру операции, estimated — оценка программы по действиям за день;
# нет отметки — строку написал или исправил сам сотрудник.
LINE_SOURCES = {"measured": "по таймеру", "estimated": "оценка программы"}

DAY_MINUTES = 24 * 60
MAX_LINES = 40            # строк в одном отчёте
MAX_NOTE = 500            # «что сделано» в строке
MAX_COMMENT = 1000        # комментарий к отчёту, причина возврата / исправления
BACKDATE_DAYS = 31        # новый отчёт — не старше стольких дней (A-48); возвращённый старый править можно

_TIME_RE = re.compile(r"^([01]?\d|2[0-4]):([0-5]\d)$")
_HOURS_RE = re.compile(r"^(\d{1,2})(?:[.,](\d{1,2}))?$")
_HM_RE = re.compile(r"^(\d{1,2}):([0-5]\d)$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class WorktimeError(LifecycleError):
    """Нарушение правила дневного отчёта: code — машинный, message_ru — для пользователя, details — поле."""


# ---------------------------------------------------------------- время
def days_label(n: int) -> str:
    """«1 дня», «31 дня», «5 дней» — родительный падеж после «не старше» (для сообщений)."""
    return f"{n} дня" if n % 10 == 1 and n % 100 != 11 else f"{n} дней"


def overnight(start: Optional[int], end: Optional[int]) -> bool:
    """Смена заканчивается на следующий день (конец хранится как минуты от полуночи дня начала, больше 1440)."""
    return start is not None and end is not None and end > DAY_MINUTES


def parse_time(v: Any, field: str, what: str) -> int:
    """«ЧЧ:ММ» → минуты от полуночи (0…1440; «24:00» — конец суток)."""
    if not isinstance(v, str) or not _TIME_RE.match(v.strip()):
        raise WorktimeError("invalid_time", f"{what}: время в виде ЧЧ:ММ, например 08:30.", {"field": field})
    h, m = (int(x) for x in v.strip().split(":"))
    total = h * 60 + m
    if total > DAY_MINUTES:
        raise WorktimeError("invalid_time", f"{what}: не позже 24:00.", {"field": field})
    return total


def time_label(minutes: Optional[int]) -> str:
    """Минуты от полуночи → «08:30» (пусто, если не задано). Конец ночной смены (больше 1440) — время следующего дня
    («06:00»); «24:00» — конец суток."""
    if minutes is None:
        return ""
    if minutes > DAY_MINUTES:
        minutes -= DAY_MINUTES
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_hours(v: Any) -> Optional[int]:
    """Длительность, как её вводит человек, → минуты: «2», «1,5», «1.25», «1:30». Пусто или ошибка — None.

    Дробные часы округляются до минуты («0,1» = 6 мин). Больше суток — None (такого в дневном отчёте не бывает).
    """
    s = str(v if v is not None else "").strip().replace(" ", "")
    if not s:
        return None
    m = _HM_RE.match(s)
    if m:
        total = int(m.group(1)) * 60 + int(m.group(2))
    else:
        m = _HOURS_RE.match(s)
        if not m:
            return None
        frac = m.group(2) or ""
        total = int(m.group(1)) * 60 + (round(int(frac) * 60 / (10 ** len(frac))) if frac else 0)
    return total if 0 < total <= DAY_MINUTES else None


def hours_label(minutes: Optional[int]) -> str:
    """Минуты → «7 ч 30 мин», «45 мин», «8 ч», «0 мин»."""
    m = int(minutes or 0)
    h, rest = divmod(abs(m), 60)
    sign = "−" if m < 0 else ""
    if h and rest:
        return f"{sign}{h} ч {rest} мин"
    return f"{sign}{h} ч" if h else f"{sign}{rest} мин"


def hours_input(minutes: Optional[int]) -> str:
    """Минуты → строка для поля ввода: «1:30», «8:00» (обратимо через parse_hours)."""
    if not minutes:
        return ""
    return f"{minutes // 60}:{minutes % 60:02d}"


def shift_net(start: Optional[int], end: Optional[int], brk: Optional[int]) -> Optional[int]:
    """«Чистое» время смены (конец − начало − перерыв) в минутах; None — смена не указана полностью."""
    if start is None or end is None:
        return None
    return end - start - int(brk or 0)


# ---------------------------------------------------------------- разбор отчёта
def _text(v: Any, field: str, what: str, max_len: int, required: bool = True) -> str:
    """Текст без пробелов по краям; обязательный — непустой; длина ограничена."""
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise WorktimeError("invalid_type", "Ожидается текст.", {"field": field})
    v = v.strip()
    if required and not v:
        raise WorktimeError("required", f"Укажите {what}.", {"field": field})
    if len(v) > max_len:
        raise WorktimeError("too_long", f"Не длиннее {max_len} символов.", {"field": field})
    return v


def _opt_id(v: Any, field: str) -> Optional[str]:
    """Необязательный идентификатор (UUID) или None."""
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not _UUID_RE.match(v):
        raise WorktimeError("invalid_id", "Неверный идентификатор.", {"field": field})
    return v


def parse_work_date(v: Any) -> str:
    """Дата рабочего дня ГГГГ-ММ-ДД."""
    try:
        return parse_date(v, "work_date", "дату рабочего дня")
    except LifecycleError as e:
        raise WorktimeError(e.code, e.message_ru, e.details) from None


def check_new_date(work_date: str, today: date) -> None:
    """Новый отчёт: день не в будущем (запас — сутки на часовые пояса) и не старше BACKDATE_DAYS дней."""
    d = date.fromisoformat(work_date)
    if d > today + timedelta(days=1):
        raise WorktimeError("date_in_future", "Отчёт за день, который ещё не наступил, завести нельзя.",
                            {"field": "work_date"})
    if d < today - timedelta(days=BACKDATE_DAYS):
        raise WorktimeError("date_too_old", f"Новый отчёт — не старше {days_label(BACKDATE_DAYS)}. За более ранний день "
                                            "обратитесь к руководителю отдела.", {"field": "work_date"})


def parse_lines(items: Any) -> list[dict]:
    """Строки отчёта: [{project_id?, task_id?, minutes, note}] → проверенный список с номерами строк.

    Задача указывается только вместе с проектом; минуты — целое 1…1440; «что сделано» обязательно. Пустой список
    допустим в черновике (отправить такой отчёт нельзя — submit_problems).
    """
    if items is None:
        items = []
    if not isinstance(items, list):
        raise WorktimeError("invalid_type", "Строки отчёта — список.", {"field": "lines"})
    if len(items) > MAX_LINES:
        raise WorktimeError("too_many", f"Не больше {MAX_LINES} строк в отчёте.", {"field": "lines"})
    out = []
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            raise WorktimeError("invalid_type", f"Строка {i}: ожидается объект.", {"field": "lines", "line": i})
        extra = set(it) - {"project_id", "task_id", "minutes", "note", "source"}
        if extra:
            raise WorktimeError("unknown_field", f"Строка {i}: неизвестные поля {', '.join(sorted(extra))}.",
                                {"field": "lines", "line": i})
        pid = _opt_id(it.get("project_id"), "lines")
        tid = _opt_id(it.get("task_id"), "lines")
        if tid and not pid:
            raise WorktimeError("task_without_project", f"Строка {i}: задачу выбирают вместе с проектом.",
                                {"field": "lines", "line": i})
        mins = it.get("minutes")
        if isinstance(mins, bool) or not isinstance(mins, int) or not 0 < mins <= DAY_MINUTES:
            raise WorktimeError("invalid_minutes", f"Строка {i}: время — от 1 минуты до 24 часов.",
                                {"field": "lines", "line": i})
        try:
            note = _text(it.get("note"), "lines", "что сделано", MAX_NOTE)
        except WorktimeError as e:
            raise WorktimeError(e.code, f"Строка {i}: {e.message_ru[:1].lower() + e.message_ru[1:]}",
                                {"field": "lines", "line": i}) from None
        src = it.get("source") or None
        if src is not None and src not in LINE_SOURCES:
            raise WorktimeError("invalid_source", f"Строка {i}: неизвестный источник времени.", {"field": "lines", "line": i})
        out.append({"line_no": i, "project_id": pid, "task_id": tid, "minutes": mins, "note": note, "source": src})
    if sum(x["minutes"] for x in out) > DAY_MINUTES:
        raise WorktimeError("over_day", "В сумме строки дольше суток.", {"field": "lines"})
    return out


def parse_shift(p: dict) -> dict:
    """Смена: shift_start / shift_end «ЧЧ:ММ» (обе или ни одной), break_minutes — целое ≥ 0.

    Конец раньше начала — ночная смена, она заканчивается на следующий день (A-48 п. 2: «приложение универсальное»);
    конец хранится как минуты от полуночи дня начала (больше 1440). Начало «24:00» и конец, равный началу, — ошибка.
    Перерыв короче смены.
    """
    s, e = p.get("shift_start"), p.get("shift_end")
    if s in (None, "") and e in (None, ""):
        start = end = None
    else:
        start = parse_time(s, "shift_start", "Начало смены")
        end = parse_time(e, "shift_end", "Конец смены")
        if start >= DAY_MINUTES:
            raise WorktimeError("invalid_time", "Начало смены: не позже 23:59.", {"field": "shift_start"})
        if end == start:
            raise WorktimeError("shift_order", "Конец смены совпадает с началом.", {"field": "shift_end"})
        if end < start:
            end += DAY_MINUTES                       # ночная смена: до утра следующего дня
    brk = p.get("break_minutes", 0)
    if brk in (None, ""):
        brk = 0
    if isinstance(brk, bool) or not isinstance(brk, int) or brk < 0 or brk > DAY_MINUTES:
        raise WorktimeError("invalid_break", "Перерыв — целое число минут, не меньше 0.", {"field": "break_minutes"})
    if start is not None and brk >= end - start:
        raise WorktimeError("break_too_long", "Перерыв должен быть короче смены.", {"field": "break_minutes"})
    return {"shift_start": start, "shift_end": end, "break_minutes": brk}


def parse_report(p: dict) -> dict:
    """Содержимое отчёта (без даты): смена, строки, комментарий. Используется при создании и при сохранении."""
    out = parse_shift(p)
    out["lines"] = parse_lines(p.get("lines"))
    out["comment"] = _text(p.get("comment"), "comment", "комментарий", MAX_COMMENT, required=False) or None
    return out


def totals(r: dict) -> dict:
    """Итоги отчёта: всего по строкам, «чистое» время смены, не распределено (может быть < 0 — превышение)."""
    total = sum(int(x.get("minutes") or 0) for x in r.get("lines") or [])
    net = shift_net(r.get("shift_start"), r.get("shift_end"), r.get("break_minutes"))
    return {"total": total, "net": net, "unallocated": (net - total) if net is not None else None}


def submit_problems(r: dict) -> list[dict]:
    """Что мешает отправить отчёт на проверку: [{code, message_ru, field}] (пусто — можно отправлять).

    Смена указана; есть хотя бы одна строка; часов в строках не больше «чистого» времени смены.
    """
    out = []
    if r.get("shift_start") is None or r.get("shift_end") is None:
        out.append({"code": "shift_required", "message_ru": "Укажите начало и конец смены.", "field": "shift_start"})
    if not r.get("lines"):
        out.append({"code": "no_lines", "message_ru": "Добавьте хотя бы одну строку: на что ушло время.", "field": "lines"})
    t = totals(r)
    if t["net"] is not None and t["total"] > t["net"]:
        out.append({"code": "over_shift", "message_ru": f"В строках {hours_label(t['total'])} — больше, чем смена без "
                                                        f"перерыва ({hours_label(t['net'])}). Исправьте время в строках "
                                                        "или смену.", "field": "lines"})
    return out


def shift_overlap(prev: Optional[dict], cur: dict, nxt: Optional[dict] = None) -> Optional[str]:
    """Пересекается ли смена отчёта cur со сменой соседнего дня (prev — за день до, nxt — за день после).

    Возможно только с ночной сменой: смена накануне закончилась в 06:00, а сегодняшняя начинается в 05:00. Отчёты —
    {work_date, shift_start, shift_end} в минутах. Возвращает текст замечания или None.
    """
    s, e = cur.get("shift_start"), cur.get("shift_end")
    if s is None or e is None:
        return None
    if prev and overnight(prev.get("shift_start"), prev.get("shift_end")) and prev["shift_end"] - DAY_MINUTES > s:
        return (f"Смена начинается в {time_label(s)}, а смена за {date_label(prev.get('work_date'))} закончилась в "
                f"{time_label(prev['shift_end'])} — смены пересекаются. Исправьте начало смены.")
    if nxt and overnight(s, e) and nxt.get("shift_start") is not None and e - DAY_MINUTES > nxt["shift_start"]:
        return (f"Смена заканчивается в {time_label(e)} следующего дня, а смена за {date_label(nxt.get('work_date'))} "
                f"начинается в {time_label(nxt['shift_start'])} — смены пересекаются. Исправьте конец смены.")
    return None


def parse_self_check(v: Any) -> dict:
    """Самопроверка перед отправкой: {shift: true, lines: true, hours: true} — все отметки SELF_CHECK_ITEMS.

    Без самопроверки отчёт не отправляется (A-48 п. 4). Возвращает отметки как есть (для журнала шагов).
    """
    if not isinstance(v, dict):
        raise WorktimeError("self_check_required", "Перед отправкой проверьте отчёт сами: отметьте все пункты "
                                                   "самопроверки.", {"field": "self_check"})
    extra = set(v) - set(SELF_CHECK_ITEMS)
    if extra:
        raise WorktimeError("unknown_field", "Самопроверка: неизвестные пункты " + ", ".join(sorted(extra)) + ".",
                            {"field": "self_check"})
    missing = [k for k in SELF_CHECK_ITEMS if v.get(k) is not True]
    if missing:
        raise WorktimeError("self_check_required", "Самопроверка не пройдена: " +
                            "; ".join(f"«{SELF_CHECK_ITEMS[k]}»" for k in missing) + ".", {"field": "self_check"})
    return {k: True for k in SELF_CHECK_ITEMS}


def check_submit(r: dict) -> None:
    """Отказ с первой причиной из submit_problems (сервер — при report.submit)."""
    probs = submit_problems(r)
    if probs:
        p = probs[0]
        raise WorktimeError(p["code"], p["message_ru"], {"field": p["field"], "problems": probs})


def date_label(iso_d: Optional[str]) -> str:
    """ГГГГ-ММ-ДД → «17.11.2026, вт» (день недели помогает не перепутать день)."""
    if not iso_d:
        return ""
    try:
        d = date.fromisoformat(iso_d)
    except ValueError:
        return iso_d
    return f"{d:%d.%m.%Y}, {('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')[d.weekday()]}"


# ---------------------------------------------------------------- отчёт, собранный программой (D-43, A-48 п. 3)
AUTO_LOOKBACK_DAYS = 7    # за сколько прошедших дней программа сама собирает отчёт, если его нет (день с действиями)
ROUND_TO = 15             # смена округляется до 15 минут (начало — вниз, конец — вверх)
BREAK_MIN_GAP = 30        # самый долгий промежуток без действий от 30 минут считается перерывом…
BREAK_MAX = 60            # …но не больше часа (остальное — работа без записей в программе: чертёж, совещание)


def _local(v: Any, tz: Optional[tzinfo]) -> Optional[datetime]:
    """ISO-время (с часовым поясом) → время в часовом поясе tz (по умолчанию — пояс компьютера). Ошибка — None."""
    if isinstance(v, datetime):
        dt = v
    else:
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        return None                                   # время без пояса не угадываем
    return dt.astimezone(tz) if tz is not None else dt.astimezone()


def _minute_of(dt: datetime, day: date) -> float:
    """Минуты от полуночи дня day (дробные — секунды учитываются)."""
    base = datetime.combine(day, datetime.min.time(), tzinfo=dt.tzinfo)
    return (dt - base).total_seconds() / 60


def _spread(total: int, weights: list[int]) -> list[int]:
    """Разделить total минут пропорционально весам: квантами по ROUND_TO (если хватает каждому, иначе по минуте),
    методом наибольшего остатка; остаток меньше кванта — самой «тяжёлой» строке. Сумма — ровно total."""
    n = len(weights)
    if n == 0 or total <= 0:
        return [0] * n
    q = ROUND_TO if total >= ROUND_TO * n else 1
    units, wsum = total // q, sum(weights) or n
    raw = [units * (w or 1) / wsum for w in weights]
    out = [int(x) for x in raw]
    for i in sorted(range(n), key=lambda i: raw[i] - out[i], reverse=True)[:units - sum(out)]:
        out[i] += 1
    out = [x * q for x in out]
    out[max(range(n), key=lambda i: weights[i])] += total - sum(out)
    return out


def _cut(text: str, n: int = MAX_NOTE) -> str:
    """Текст не длиннее n символов (с многоточием)."""
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def assemble_report(day: date, activity: dict, member_projects: Iterable[str], now: datetime,
                    tz: Optional[tzinfo] = None, local_times: Optional[dict] = None) -> Optional[dict]:
    """Собрать дневной отчёт за день day по тому, что сотрудник делал в программе (A-48 п. 3).

    activity — данные pull «worktime_activity» (сервер, только свои записи):

    * actions — [{at, operation_id, command, label, object, project_id, project_code, task_id}] — принятые команды
      сотрудника (command «auth.login» — вход: только граница смены, без строки);
    * timers — [{start, end|None, project_id, project_code, label}] — интервалы «в работе» операций нарядов по
      часам устройства (end = None — таймер ещё идёт).

    local_times — {operation_id: ISO} — когда действие на самом деле сделано на этом компьютере (очередь клиента):
    сделанное без связи сервер записал при синхронизации, а в отчёт оно должно попасть днём, когда сделано.

    Правила (каждое видно сотруднику при самопроверке; ничего не отправляется без неё):

    * границы смены — первое и последнее действие / интервал таймера дня (начало — вниз, конец — вверх до 15 мин);
    * перерыв — самый долгий промежуток без действий, если он от 30 минут, но не больше часа;
    * строки «по таймеру» (source = measured) — фактическое время операций, по проекту и операции;
    * остальное время смены делится между остальными работами (по проекту и задаче) пропорционально числу действий —
      строки «оценка программы» (source = estimated); сумма строк не больше смены без перерыва;
    * проект строки — только тот, где сотрудник участник (member_projects); иначе строка «не по проекту» с кодом
      проекта в тексте (сервер не примет чужой проект).

    Возвращает {shift_start, shift_end (минуты, ночная — больше 1440), break_minutes, lines, comment, summary} или
    None, если за день нет ни одного действия.
    """
    members = set(member_projects or [])
    local_times = local_times or {}
    day_end = datetime.combine(day + timedelta(days=1), datetime.min.time())
    points: list[float] = []
    busy: list[tuple[float, float]] = []
    groups: dict[tuple, dict] = {}
    measured: dict[tuple, dict] = {}
    n_actions = 0

    def proj(pid: Optional[str], code: Optional[str]) -> tuple[Optional[str], str]:
        """Проект строки (только где участник) и префикс текста для чужого проекта."""
        if pid and pid in members:
            return pid, ""
        return None, (f"{code}: " if pid and code else "")

    for a in activity.get("actions") or []:
        dt = _local(local_times.get(a.get("operation_id") or "") or a.get("at"), tz)
        if dt is None or dt.date() != day:
            continue
        m = _minute_of(dt, day)
        points.append(m)
        busy.append((m, m))
        if a.get("command") == "auth.login":
            continue
        n_actions += 1
        pid, prefix = proj(a.get("project_id"), a.get("project_code"))
        tid = a.get("task_id") if pid else None
        g = groups.setdefault((pid, tid), {"first": m, "count": 0, "items": {}})
        g["count"] += 1
        what = prefix + (a.get("label") or a.get("command") or "")
        if a.get("object"):
            what += f": {a['object']}"
        g["items"][what] = g["items"].get(what, 0) + 1

    for t in activity.get("timers") or []:
        st = _local(t.get("start"), tz)
        en = _local(t.get("end"), tz) if t.get("end") else _local(now, tz)
        if st is None or en is None or en <= st:
            continue
        tzi = st.tzinfo
        lo = max(st, datetime.combine(day, datetime.min.time(), tzinfo=tzi))
        hi = min(en, day_end.replace(tzinfo=tzi))
        if hi <= lo:
            continue
        a_m, b_m = _minute_of(lo, day), _minute_of(hi, day)
        points += [a_m, b_m]
        busy.append((a_m, b_m))
        pid, prefix = proj(t.get("project_id"), t.get("project_code"))
        key = (pid, prefix + (t.get("label") or "Операция наряда"))
        g = measured.setdefault(key, {"first": a_m, "seconds": 0.0})
        g["seconds"] += (hi - lo).total_seconds()

    if not points:
        return None
    start = int(min(points) // ROUND_TO * ROUND_TO)
    end = int(-(-max(points) // ROUND_TO) * ROUND_TO)
    end = min(max(end, start + ROUND_TO), DAY_MINUTES)
    start = min(start, end - ROUND_TO)

    # Перерыв — самый долгий промежуток между занятыми отрезками (таймер — сплошная работа).
    gap = 0.0
    edge = None
    for a_m, b_m in sorted(busy):
        if edge is not None and a_m > edge:
            gap = max(gap, a_m - edge)
        edge = b_m if edge is None else max(edge, b_m)
    brk = min(int(gap // ROUND_TO * ROUND_TO), BREAK_MAX) if gap >= BREAK_MIN_GAP else 0
    if brk >= end - start:
        brk = 0
    net = end - start - brk

    lines: list[dict] = []
    for (pid, label), g in sorted(measured.items(), key=lambda kv: kv[1]["first"]):
        mins = max(1, round(g["seconds"] / 60))
        lines.append({"project_id": pid, "task_id": None, "minutes": min(mins, DAY_MINUTES),
                      "note": _cut(label), "source": "measured", "_first": g["first"]})
    measured_total = sum(x["minutes"] for x in lines)

    est = sorted(groups.items(), key=lambda kv: kv[1]["first"])
    room = MAX_LINES - len(lines)
    if len(est) > room > 0:                      # лишние группы — одной строкой «прочее» (не по проекту)
        keep, rest = est[:room - 1], est[room - 1:]
        merged = {"first": rest[0][1]["first"], "count": sum(g["count"] for _, g in rest), "items": {}}
        for _, g in rest:
            for k, v in g["items"].items():
                merged["items"][k] = merged["items"].get(k, 0) + v
        est = keep + [((None, None), merged)]
    remaining = net - measured_total
    extra_note = []
    if est and remaining > 0 and room > 0:
        shares = _spread(remaining, [g["count"] for _, g in est])
        for ((pid, tid), g), mins in zip(est, shares):
            text = "; ".join(f"{k} (×{v})" if v > 1 else k for k, v in g["items"].items())
            if mins <= 0:
                extra_note.append(text)
                continue
            lines.append({"project_id": pid, "task_id": tid, "minutes": mins, "note": _cut(text), "source": "estimated",
                          "_first": g["first"]})
    elif est:
        extra_note = ["; ".join(g["items"]) for _, g in est]
    lines.sort(key=lambda x: x.pop("_first"))
    for i, ln in enumerate(lines, 1):
        ln["line_no"] = i
    comment = _cut("Также в программе: " + "; ".join(extra_note), MAX_COMMENT) if extra_note else None
    return {"shift_start": start, "shift_end": end, "break_minutes": brk, "lines": lines, "comment": comment,
            "summary": {"actions": n_actions, "measured_minutes": measured_total, "lines": len(lines)}}
