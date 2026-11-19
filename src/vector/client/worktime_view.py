"""Представление раздела «Время» — рабочее время и дневной отчёт (этап 7, R-20; D-42, D-43). Чистые функции без Qt.

Отчёт за прошедший день, где сотрудник работал в программе, но отчёт не написал, программа собирает сама (A-48
п. 3): смена — по первому и последнему действию, строки — по таймерам операций («по таймеру») и по действиям
(«оценка программы»). Такой отчёт — «На самопроверке»: сотрудник проверяет, правит и отмечает пункты самопроверки
(A-48 п. 4) — только тогда он уходит руководителю отдела. Самопроверка обязательна и для отчёта, написанного вручную.
Смена может идти через полночь (ночная, A-48 п. 2): конец раньше начала — значит, до утра следующего дня.

Две вкладки:

* «Мои отчёты» — у каждого сотрудника: отчёт за день (смена, перерыв, строки «на что ушло время» — проект и задача,
  если работа проектная, часы, что сделано). Черновик и отправка на проверку — можно без связи (очередь);
  исправление уже отправленного — при связи и с основанием;
* «На проверке» — руководителю отдела (и замещающему): отправленные отчёты сотрудников — «Принять» или «Вернуть» с
  причиной (при связи; свой отчёт — не проверяет).

Флаги can_* / may_* — подсказки интерфейса; решает всегда сервер. Если действие недоступно, рядом объясняется почему.
Часы в форме вводятся как человеку удобно: «2», «1,5», «1:30» — в отчёт уходят целые минуты (vector.core.worktime).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from vector.core import worktime as wt

from .production_view import when
from .stock_view import as_of_label

# Что дальше по отчёту (подсказка над кнопками карточки).
NEXT_OWN = {"draft": "Дальше: заполнить смену и строки, пройти самопроверку и отправить руководителю отдела.",
            "self_check": "Отчёт собрала программа по вашим действиям за день. Проверьте смену и строки (оценки "
                          "программы — особенно), при необходимости «Изменить», затем «Самопроверка и отправка».",
            "returned": "Отчёт вернули — исправьте по причине возврата и отправьте снова.",
            "submitted": "Отчёт у руководителя отдела. Если нашли ошибку — «Исправить» (с основанием).",
            "accepted": ""}
NEXT_REVIEW = "Проверьте строки и итоги: «Принять» или «Вернуть» с причиной — что исправить."


def _who(people: dict[str, str], uid: Optional[str], behalf: Optional[str] = None) -> str:
    """Имя сотрудника («—», если нет) и «(замещает: …)», если решение принято за другого."""
    name = people.get(uid or "", "—")
    return f"{name} (замещает: {people.get(behalf, '—')})" if behalf else name


def _history(rows: list[dict], people: dict[str, str]) -> list[dict]:
    """История отчёта: шаг, кто и когда, подробности (причина возврата / основание исправления / итог)."""
    out = []
    for h in rows or []:
        d = h.get("details") or {}
        extra = d.get("reason") or d.get("comment") or ""
        if not extra and d.get("total_minutes") is not None and h.get("decision") in ("created", "submitted"):
            extra = "в строках " + wt.hours_label(d["total_minutes"])
        out.append({"label": h.get("label") or h.get("decision"), "who": _who(people, h.get("by"), h.get("on_behalf_of")),
                    "when": when(h.get("at")), "extra": extra})
    return out


def _minutes_of(v: Any) -> Optional[int]:
    """«ЧЧ:ММ» из отчёта → минуты от полуночи (None — не задано или не разобрать)."""
    try:
        return wt.parse_time(v, "", "") if v else None
    except wt.WorktimeError:
        return None


def _shift_minutes(start: Any, end: Any) -> tuple[Optional[int], Optional[int]]:
    """Смена «ЧЧ:ММ»–«ЧЧ:ММ» → минуты от полуночи дня начала; конец раньше начала — ночная смена, конец + 24 ч."""
    s, e = _minutes_of(start), _minutes_of(end)
    if s is not None and e is not None and e < s:
        e += wt.DAY_MINUTES
    return s, e


def _shift_label(r: dict) -> str:
    """«08:30–17:30, перерыв 1 ч», «22:00–06:00 (до утра следующего дня), …» или «смена не указана»."""
    if not r.get("shift_start") or not r.get("shift_end"):
        return "смена не указана"
    brk = int(r.get("break_minutes") or 0)
    night = " (ночная — до утра следующего дня)" if wt.overnight(*_shift_minutes(r["shift_start"], r["shift_end"])) else ""
    return f"{r['shift_start']}–{r['shift_end']}{night}" + (f", перерыв {wt.hours_label(brk)}" if brk else ", без перерыва")


def _core(r: dict) -> dict:
    """Отчёт в виде для правил ядра: смена в минутах (ночная — конец больше 24 ч), строки с минутами."""
    s, e = _shift_minutes(r.get("shift_start"), r.get("shift_end"))
    return {"work_date": r.get("work_date"), "shift_start": s, "shift_end": e,
            "break_minutes": int(r.get("break_minutes") or 0), "lines": r.get("lines") or []}


def _assembled_label(a: Optional[dict]) -> str:
    """«по 12 действиям в программе; по таймерам операций 1 ч 30 мин; смена — по таймеру рабочего дня, 8 ч» — из чего
    программа собрала отчёт (D-43, D-46)."""
    if not a:
        return ""
    n = int(a.get("actions") or 0)
    word = "действию" if n % 10 == 1 and n % 100 != 11 else "действиям"
    out = f"по {n} {word} в программе"
    if a.get("measured_minutes"):
        out += "; по таймерам операций " + wt.hours_label(a["measured_minutes"])
    if a.get("workday_minutes"):
        # D-46: смена и перерывы взяты из таймера рабочего дня — «сколько натикало», без округления.
        out += "; смена — по таймеру рабочего дня, " + wt.hours_label(a["workday_minutes"])
    return out


def _row(r: dict, me_id: str, reviewer: bool, online: bool, people: dict[str, str], projects: dict[str, dict],
         tasks: dict[str, dict]) -> dict:
    """Строка списка и карточка отчёта: подписи, строки с проектом и задачей, итоги, что мешает, доступные действия."""
    st = r.get("status") or "draft"
    own = r.get("user_id") == me_id
    pending = bool(r.get("pending"))
    core = _core(r)
    t = wt.totals(core)
    lines = []
    for ln in r.get("lines") or []:
        p = projects.get(ln.get("project_id") or "") or {}
        code = ln.get("project_code") or p.get("code") or ""
        title = ln.get("project_title") or p.get("title") or ""
        task = ln.get("task_title") or (tasks.get(ln.get("task_id") or "") or {}).get("title") or ""
        lines.append({"line_no": ln.get("line_no"), "project_id": ln.get("project_id") or "", "task_id": ln.get("task_id") or "",
                      "project_label": f"{code} · {title}" if code else ("Не по проекту" if not ln.get("project_id") else "Проект"),
                      "task_label": task, "minutes": int(ln.get("minutes") or 0),
                      "hours_label": wt.hours_label(ln.get("minutes")), "hours_input": wt.hours_input(ln.get("minutes")),
                      "note": ln.get("note") or "", "source": ln.get("source") or "",
                      # Откуда время строки (D-43): «по таймеру», «оценка программы»; пусто — написал сам сотрудник.
                      "source_label": wt.LINE_SOURCES.get(ln.get("source") or "", "")})
    problems = [p["message_ru"] for p in wt.submit_problems(core)] if st in wt.EDITABLE else []
    un = t["unallocated"]
    editable = own and st in wt.EDITABLE
    may_review = reviewer and not own and st == "submitted"
    return {
        "id": r["id"], "work_date": r.get("work_date") or "", "date_label": wt.date_label(r.get("work_date")),
        "status": st, "status_label": wt.STATUS_LABELS_RU.get(st, st), "status_tone": wt.STATUS_TONES.get(st, "neutral"),
        "user_id": r.get("user_id") or "", "user_name": people.get(r.get("user_id") or "", "—"), "is_own": own,
        "shift_label": _shift_label(r), "shift_start": r.get("shift_start") or "", "shift_end": r.get("shift_end") or "",
        "break_minutes": int(r.get("break_minutes") or 0),
        "total_minutes": t["total"], "total_label": wt.hours_label(t["total"]),
        "net_label": wt.hours_label(t["net"]) if t["net"] is not None else "—",
        "unallocated_label": ("—" if un is None else (wt.hours_label(un) if un >= 0 else "превышение на " + wt.hours_label(-un))),
        "unallocated_tone": "neutral" if un is None or un == 0 else ("warning" if un > 0 else "danger"),
        "lines": lines, "line_count": len(lines), "comment": r.get("comment") or "",
        "review_comment": r.get("review_comment") or "",
        "reviewed_label": f"{_who(people, r.get('reviewed_by'))}, {when(r.get('reviewed_at'))}" if r.get("reviewed_by") else "",
        "submitted_label": when(r.get("submitted_at")) if r.get("submitted_at") else "",
        "history": _history(r.get("history"), people), "pending": pending, "version": int(r.get("version") or 0),
        "origin": r.get("origin") or "manual", "assembled_label": _assembled_label(r.get("assembled")),
        "overnight": wt.overnight(core["shift_start"], core["shift_end"]), "_core": core,
        # Отметка дня (D-49, A-53 п. 3) — считает сервер по графику автора и производственному календарю: работа в
        # праздничный / выходной день, сверхурочно (сверх нормы по графику). Старый кэш без отметки — пусто.
        "day_mark": (r.get("day_marks") or {}).get("mark") or "",
        "day_mark_label": (r.get("day_marks") or {}).get("label") or "",
        "day_mark_tone": (r.get("day_marks") or {}).get("tone") or "neutral",
        "day_mark_short": (r.get("day_marks") or {}).get("short_label") or "",
        "problems": problems,
        "next": NEXT_REVIEW if may_review else (NEXT_OWN.get(st, "") if own else ""),
        "can_edit": editable,
        "may_submit": editable, "can_submit": editable and not problems,
        "may_reopen": own and st in ("submitted", "accepted"), "can_reopen": own and st in ("submitted", "accepted") and online and not pending,
        "may_review": may_review, "can_review": may_review and online,
        "block_review": "Свой отчёт проверяет другой руководитель отдела («четыре глаза»)." if reviewer and own and st == "submitted" else "",
    }


def _ordinal(iso_d: str) -> int:
    """Дата ГГГГ-ММ-ДД → порядковый номер дня (для сортировки «новые сверху»; не разобрать — 0)."""
    try:
        return date.fromisoformat(iso_d).toordinal()
    except (TypeError, ValueError):
        return 0


def worktime_view(reports: list[dict], projects: list[dict], tasks: list[dict], people: dict[str, str], me: dict,
                  reviewer: bool, online: bool, last_pull: str, today: Optional[date] = None,
                  weekly_metrics: Optional[dict] = None, overtime_requests: Optional[list[dict]] = None,
                  overtime_period_config: Optional[dict] = None) -> dict[str, Any]:
    """Раздел «Время»: свои отчёты, отчёты на проверку (руководителю отдела), варианты проектов и задач для строк."""
    today = today or date.today()
    me_id = me.get("user_id") or ""
    pmap = {p["id"]: p for p in projects or []}
    tmap = {t["id"]: t for t in tasks or []}
    rows = [_row(r, me_id, reviewer, online, people, pmap, tmap) for r in reports or []]
    rows.sort(key=lambda x: x["work_date"], reverse=True)
    mine = [x for x in rows if x["is_own"]]
    # Ночная смена (D-43): своя смена не должна пересекаться со сменой соседнего дня — видно сразу, решает сервер.
    by_day = {x["work_date"]: x["_core"] for x in mine}
    for x in mine:
        if x["status"] in wt.EDITABLE and x["work_date"]:
            d = date.fromisoformat(x["work_date"])
            msg = wt.shift_overlap(by_day.get((d - timedelta(days=1)).isoformat()), x["_core"],
                                   by_day.get((d + timedelta(days=1)).isoformat()))
            if msg:
                x["problems"].append(msg)
                x["can_submit"] = False
    for x in rows:
        x.pop("_core", None)
    order = {"submitted": 0, "returned": 1, "accepted": 2}
    others = sorted((x for x in rows if not x["is_own"] and x["status"] not in ("draft", "self_check")),
                    key=lambda x: (order.get(x["status"], 3), -_ordinal(x["work_date"]), x["user_name"]))
    to_review = sum(1 for x in others if x["status"] == "submitted")
    tabs = [{"id": "mine", "label": "Мои отчёты", "icon": "clock"}]
    if reviewer:
        tabs.append({"id": "review", "label": f"На проверке ({to_review})" if to_review else "На проверке", "icon": "user-check"})
    t_iso = today.isoformat()
    today_row = next((x for x in mine if x["work_date"] == t_iso), None)
    week = [x for x in mine if 0 <= today.toordinal() - _ordinal(x["work_date"]) < 7]
    task_opts = [{"id": t["id"], "project_id": t.get("project_id") or "", "label": t.get("title") or ""}
                 for t in sorted(tasks or [], key=lambda t: t.get("title") or "")
                 if t.get("project_id") in pmap and t.get("status") not in ("cancelled",)]
    weekly = weekly_metrics or {}
    overtime_rows = []
    for request in overtime_requests or []:
        row = dict(request)
        row["is_own"] = row.get("user_id") == me_id
        row["kind_label"] = {"overtime": "Сверхурочная работа", "weekend": "Работа в выходной день",
                             "holiday": "Работа в федеральный праздник"}.get(row.get("kind"), "—")
        row["mode_label"] = {"planned": "Согласовано заранее", "urgent_after": "Срочная работа — после факта"}.get(
            row.get("mode"), "—")
        row["status_label"] = {"pending": "Ожидает решения", "approved": "Согласовано", "rejected": "Отклонено"}.get(
            row.get("status"), "—")
        overtime_rows.append(row)
    weekly_units = [{**u, "hours_label": wt.hours_label(int(u.get("minutes") or 0))}
                    for u in weekly.get("units", [])]
    return {
        "weekly_metrics": {**weekly, "units": weekly_units},
        "overtime_requests": overtime_rows,
        "overtime_period_config": overtime_period_config or {},
        "overtime_period_label": {"month": "Календарный месяц", "quarter": "Календарный квартал",
                                  "year": "Календарный год"}.get((overtime_period_config or {}).get("mode"), "Не настроен"),
        "tabs": tabs if len(tabs) > 1 else [], "default_tab": "review" if reviewer and to_review else "mine",
        "mine": mine, "review": others, "reviewer": reviewer, "online": online, "as_of": as_of_label(last_pull),
        "today": t_iso, "today_label": wt.date_label(t_iso), "today_id": today_row["id"] if today_row else "",
        "counts": {"draft": sum(1 for x in mine if x["status"] == "draft"),
                   "self_check": sum(1 for x in mine if x["status"] == "self_check"),
                   "returned": sum(1 for x in mine if x["status"] == "returned"), "to_review": to_review},
        "week_label": "за 7 дней в отчётах: " + wt.hours_label(sum(x["total_minutes"] for x in week)),
        "project_options": [{"id": "", "label": "Не по проекту"}] + [{"id": p["id"], "label": f"{p['code']} · {p['title']}"}
                                                                    for p in projects or []],
        "task_options": task_opts,
        "empty_mine": "Отчётов пока нет. Нажмите «Отчёт за сегодня» — смену и строки можно заполнить и без связи.",
        "empty_review": "Отчётов на проверке нет. Когда сотрудник отправит отчёт, он появится здесь, а вам придёт "
                        "уведомление.",
        # Собранные программой отчёты ждут самопроверки — подсказка над списком (D-43).
        "self_check_banner": ("Программа собрала отчёты по вашим действиям за прошедшие дни, где отчёта не было: "
                              f"{sum(1 for x in mine if x['status'] == 'self_check')} шт. Проверьте каждый и отправьте "
                              "руководителю отдела.") if any(x["status"] == "self_check" for x in mine) else "",
        # Пункты самопроверки перед отправкой (A-48 п. 4) — все обязательны.
        "self_check_items": [{"id": k, "label": v} for k, v in wt.SELF_CHECK_ITEMS.items()],
        "auto_days_label": wt.days_label(wt.AUTO_LOOKBACK_DAYS),
        "backdate_days": wt.BACKDATE_DAYS,
        "backdate_label": wt.days_label(wt.BACKDATE_DAYS),  # «31 дня» — для подписи формы
    }


def report_form(payload: dict) -> dict[str, Any]:
    """Форма отчёта → данные команды и проверка без сервера (те же правила ядра).

    payload — {work_date?, shift_start, shift_end, break_minutes (строка, минуты), comment, lines: [{project_id,
    task_id, hours, note, source?, orig?}]}. source — откуда время строки, собранной программой (D-43); оно
    сохраняется, только пока строка не изменена (orig — {project_id, task_id, minutes, note} как было), иначе строка
    становится «своей». Возвращает {ok, message, field, rows: {номер строки (с 0): замечание}, data (для
    report.create / report.save, если ok), totals: {total_label, net_label, unallocated_label, tone}, submit_problems}.
    """
    rows: dict[str, str] = {}
    lines = []
    for i, ln in enumerate(payload.get("lines") or []):
        hours, note = str(ln.get("hours") or "").strip(), str(ln.get("note") or "").strip()
        pid, tid = ln.get("project_id") or None, ln.get("task_id") or None
        if not hours and not note and not pid:
            continue                                            # пустая строка формы — пропускаем
        mins = wt.parse_hours(hours)
        if mins is None:
            rows[str(i)] = "Время: например 2, 1,5 или 1:30 (не больше 24 ч)."
        elif not note:
            rows[str(i)] = "Напишите, что сделано."
        line = {"project_id": pid, "task_id": tid if pid else None, "minutes": mins or 0, "note": note}
        orig = ln.get("orig") or {}
        if ln.get("source") and all((orig.get(k) or None) == (line[k] or None) for k in line):
            line["source"] = ln["source"]
        lines.append(line)
    brk_s = str(payload.get("break_minutes") if payload.get("break_minutes") is not None else "").strip()
    data: dict[str, Any] = {"shift_start": (payload.get("shift_start") or "").strip() or None,
                            "shift_end": (payload.get("shift_end") or "").strip() or None,
                            "break_minutes": int(brk_s) if brk_s.isdigit() else (0 if not brk_s else -1),
                            "comment": (payload.get("comment") or "").strip() or None, "lines": lines}
    out: dict[str, Any] = {"ok": True, "message": "", "field": "", "rows": rows, "data": None, "submit_problems": []}
    s_min, e_min = _shift_minutes(data["shift_start"], data["shift_end"])
    tot = wt.totals({"shift_start": s_min, "shift_end": e_min, "break_minutes": max(0, data["break_minutes"]), "lines": lines})
    out["overnight"] = wt.overnight(s_min, e_min)
    un = tot["unallocated"]
    out["totals"] = {"total_label": wt.hours_label(tot["total"]), "net_label": wt.hours_label(tot["net"]) if tot["net"] is not None else "—",
                     "unallocated_label": "—" if un is None else (wt.hours_label(un) if un >= 0 else "превышение на " + wt.hours_label(-un)),
                     "tone": "neutral" if un is None or un == 0 else ("warning" if un > 0 else "danger")}
    if rows:
        return {**out, "ok": False, "message": "Исправьте строки, отмеченные красным.", "field": "lines"}
    try:
        if payload.get("work_date") is not None:
            wt.parse_work_date(payload.get("work_date"))
        parsed = wt.parse_report(data)
    except wt.WorktimeError as e:
        line = (e.details or {}).get("line")
        if line:
            rows[str(line - 1)] = e.message_ru
        return {**out, "ok": False, "message": e.message_ru, "field": (e.details or {}).get("field", ""), "rows": rows}
    out["data"] = data
    out["submit_problems"] = [p["message_ru"] for p in wt.submit_problems(parsed)]
    return out
