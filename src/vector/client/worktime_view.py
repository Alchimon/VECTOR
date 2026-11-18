"""Представление раздела «Время» — рабочее время и дневной отчёт (этап 7, R-20; D-42). Чистые функции без Qt.

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

from datetime import date
from typing import Any, Optional

from vector.core import worktime as wt

from .production_view import when
from .stock_view import as_of_label

# Что дальше по отчёту (подсказка над кнопками карточки).
NEXT_OWN = {"draft": "Дальше: заполнить смену и строки и отправить руководителю отдела на проверку.",
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


def _shift_label(r: dict) -> str:
    """«08:30–17:30, перерыв 1 ч» или «смена не указана»."""
    if not r.get("shift_start") or not r.get("shift_end"):
        return "смена не указана"
    brk = int(r.get("break_minutes") or 0)
    return f"{r['shift_start']}–{r['shift_end']}" + (f", перерыв {wt.hours_label(brk)}" if brk else ", без перерыва")


def _core(r: dict) -> dict:
    """Отчёт в виде для правил ядра: смена в минутах, строки с минутами."""
    return {"shift_start": _minutes_of(r.get("shift_start")), "shift_end": _minutes_of(r.get("shift_end")),
            "break_minutes": int(r.get("break_minutes") or 0), "lines": r.get("lines") or []}


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
                      "note": ln.get("note") or ""})
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
                  reviewer: bool, online: bool, last_pull: str, today: Optional[date] = None) -> dict[str, Any]:
    """Раздел «Время»: свои отчёты, отчёты на проверку (руководителю отдела), варианты проектов и задач для строк."""
    today = today or date.today()
    me_id = me.get("user_id") or ""
    pmap = {p["id"]: p for p in projects or []}
    tmap = {t["id"]: t for t in tasks or []}
    rows = [_row(r, me_id, reviewer, online, people, pmap, tmap) for r in reports or []]
    rows.sort(key=lambda x: x["work_date"], reverse=True)
    mine = [x for x in rows if x["is_own"]]
    order = {"submitted": 0, "returned": 1, "accepted": 2}
    others = sorted((x for x in rows if not x["is_own"] and x["status"] != "draft"),
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
    return {
        "tabs": tabs if len(tabs) > 1 else [], "default_tab": "review" if reviewer and to_review else "mine",
        "mine": mine, "review": others, "reviewer": reviewer, "online": online, "as_of": as_of_label(last_pull),
        "today": t_iso, "today_label": wt.date_label(t_iso), "today_id": today_row["id"] if today_row else "",
        "counts": {"draft": sum(1 for x in mine if x["status"] == "draft"),
                   "returned": sum(1 for x in mine if x["status"] == "returned"), "to_review": to_review},
        "week_label": "за 7 дней в отчётах: " + wt.hours_label(sum(x["total_minutes"] for x in week)),
        "project_options": [{"id": "", "label": "Не по проекту"}] + [{"id": p["id"], "label": f"{p['code']} · {p['title']}"}
                                                                    for p in projects or []],
        "task_options": task_opts,
        "empty_mine": "Отчётов пока нет. Нажмите «Отчёт за сегодня» — смену и строки можно заполнить и без связи.",
        "empty_review": "Отчётов на проверке нет. Когда сотрудник отправит отчёт, он появится здесь, а вам придёт "
                        "уведомление.",
        "backdate_days": wt.BACKDATE_DAYS,
        "backdate_label": wt.days_label(wt.BACKDATE_DAYS),  # «31 дня» — для подписи формы
    }


def report_form(payload: dict) -> dict[str, Any]:
    """Форма отчёта → данные команды и проверка без сервера (те же правила ядра).

    payload — {work_date?, shift_start, shift_end, break_minutes (строка, минуты), comment, lines: [{project_id,
    task_id, hours, note}]}. Возвращает {ok, message, field, rows: {номер строки (с 0): замечание}, data (для
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
        lines.append({"project_id": pid, "task_id": tid if pid else None, "minutes": mins or 0, "note": note})
    brk_s = str(payload.get("break_minutes") if payload.get("break_minutes") is not None else "").strip()
    data: dict[str, Any] = {"shift_start": (payload.get("shift_start") or "").strip() or None,
                            "shift_end": (payload.get("shift_end") or "").strip() or None,
                            "break_minutes": int(brk_s) if brk_s.isdigit() else (0 if not brk_s else -1),
                            "comment": (payload.get("comment") or "").strip() or None, "lines": lines}
    out: dict[str, Any] = {"ok": True, "message": "", "field": "", "rows": rows, "data": None, "submit_problems": []}
    tot = wt.totals({"shift_start": _minutes_of(data["shift_start"]), "shift_end": _minutes_of(data["shift_end"]),
                     "break_minutes": max(0, data["break_minutes"]), "lines": lines})
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
