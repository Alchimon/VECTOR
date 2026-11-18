"""Рабочее время и дневной отчёт — правила, общие для сервера и клиента (этап 7, R-20; decision log D-42).

Сервер применяет эти правила при выполнении команды, клиент — чтобы проверить ввод до отправки и показать черновик
офлайн так же, как его потом увидит сервер (одна правда на обеих сторонах).

Дневной отчёт — один на сотрудника за рабочий день: смена (начало, конец, перерыв) и строки «на что ушло время»
(проект и задача — если работа проектная, сколько времени, что сделано). Отчёт ведёт сам сотрудник, проверяет
руководитель отдела (не автор — «четыре глаза»).

Жизненный цикл (03 § 4; упрощение D-42 — отдельного статуса «на самопроверке» нет: программа сама проверяет отчёт
перед отправкой — смена указана, строки есть, часов не больше смены):

    draft (черновик) → submitted (на проверке) → accepted (принят) | returned (возвращён, с причиной)
    returned → правка → submitted (снова на проверку)
    submitted | accepted → reopen (с основанием) → draft  — исправление после отправки (матрица 04)

Время хранится целыми минутами: начало и конец смены — минуты от полуночи (0…1440), перерыв и строки — минуты.
Смена — в пределах одних суток (ночные смены — вопрос A-48). Строк не больше, чем «чистое» время смены (смена минус
перерыв); меньше — можно (в карточке видно «не распределено»).

Ошибки — WorktimeError с машинным кодом и русским сообщением (как LifecycleError в vector.core.lifecycle).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Optional

from .lifecycle import LifecycleError, parse_date

# Статусы дневного отчёта: подписи и тон метки в интерфейсе.
STATUS_LABELS_RU = {"draft": "Черновик", "submitted": "На проверке", "accepted": "Принят", "returned": "Возвращён"}
STATUS_TONES = {"draft": "neutral", "submitted": "warning", "accepted": "success", "returned": "danger"}
# Где отчёт может править автор (сохранить черновик, отправить на проверку).
EDITABLE = ("draft", "returned")

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
    """Минуты от полуночи → «08:30» (пусто, если не задано)."""
    if minutes is None:
        return ""
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
        extra = set(it) - {"project_id", "task_id", "minutes", "note"}
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
        out.append({"line_no": i, "project_id": pid, "task_id": tid, "minutes": mins, "note": note})
    if sum(x["minutes"] for x in out) > DAY_MINUTES:
        raise WorktimeError("over_day", "В сумме строки дольше суток.", {"field": "lines"})
    return out


def parse_shift(p: dict) -> dict:
    """Смена: shift_start / shift_end «ЧЧ:ММ» (обе или ни одной), break_minutes — целое ≥ 0.

    Конец позже начала (в пределах суток — A-48), перерыв короче смены.
    """
    s, e = p.get("shift_start"), p.get("shift_end")
    if s in (None, "") and e in (None, ""):
        start = end = None
    else:
        start = parse_time(s, "shift_start", "Начало смены")
        end = parse_time(e, "shift_end", "Конец смены")
        if end <= start:
            raise WorktimeError("shift_order", "Конец смены — позже начала (ночная смена через полночь пока не "
                                               "поддерживается).", {"field": "shift_end"})
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
