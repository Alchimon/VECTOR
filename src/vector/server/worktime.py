"""Рабочее время и дневной отчёт (этап 7, R-20; decision log D-42).

Модуль — примесь (mixin) к `Service`, как quality.py: транзакция, идемпотентность по operation_id, аудит и проверка
версий — в service.py; здесь только правила домена (сами правила — в vector.core.worktime, общие с клиентом).

Поток работы (ответы владельца на A-48 — D-43: отчёт собирает программа, сотрудник проверяет его сам, затем отчёт
уходит руководителю отдела):

    программа сотрудника (клиент, по данным pull «worktime_activity» — свои действия и таймеры за последние дни):
        report.create {origin: "auto"} (можно без связи) — собранный отчёт за прошедший день («На самопроверке»)
    сотрудник (любой вошедший, только свой отчёт):
        report.create (можно без связи) — отчёт вручную: смена, строки «на что ушло время» («Черновик»)
        report.save   (можно без связи) — правка отчёта на самопроверке, черновика или возвращённого
        report.submit (можно без связи) — самопроверка (все отметки) → на проверку руководителю отдела
        report.reopen (при связи)       — исправить отправленный или принятый отчёт: с основанием → «Черновик»
    руководитель отдела (dept_head, в т. ч. замещающий), НЕ автор:
        report.review (при связи) — «Принят» или «Возвращён» (причина обязательна)

Главные инварианты (каждый закреплён тестом в tests/server/test_worktime.py):

* один отчёт на сотрудника за день (уникальность в БД); чужой отчёт не правится и не отправляется;
* отправить можно только полный отчёт и только после самопроверки: смена указана, есть строки, часов в строках не
  больше смены без перерыва, смена не пересекается со сменой соседнего дня (ночные смены), все отметки самопроверки;
* проект строки — тот, где сотрудник сейчас участник (своё назначение или замещение в проекте); задача — этого
  проекта;
* принять или вернуть — только руководитель отдела и не свой отчёт («четыре глаза»); возврат — с причиной;
* исправление после отправки — только с основанием; каждый шаг — в неизменяемом журнале daily_report_events (кто,
  когда, на каком основании, подробности).

Оргструктура (D-44, R-21): отчёт сотрудника, включённого в подразделение, уходит руководителю его подразделения (или
ближайшего вышестоящего) и его замещающим; видят и проверяют его руководители по цепочке вверх (при роли
«Руководитель отдела»). Сотрудники вне оргструктуры — по правилу D-42: все руководители отделов. Черновики чужих
отчётов не видит никто.
"""
from __future__ import annotations

import json
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import org as og
from vector.core import workday as wd
from vector.core import worktime as wt
from vector.core.errors import Conflict, Denied, Rejected

from .authz import active_delegated, project_member
from . import db as _db
from .db import (audit_log, daily_report_auto, daily_report_events, daily_report_line_sources, daily_report_lines,
                 daily_reports, org_members, org_units, overtime_events, overtime_period_config, overtime_request_periods,
                 overtime_requests, projects, tasks,
                 wo_operations, wo_timer_events, work_orders)
from .validation import canon, iso, only_keys, req_uuid

# Подписи шагов журнала отчёта (история карточки).
DECISION_LABELS_RU = {"created": "Создан", "saved": "Изменён", "submitted": "Отправлен на проверку", "accepted": "Принят",
                      "returned": "Возвращён на исправление", "reopened": "Открыт для исправления"}
# За сколько дней (по дате рабочего дня) отчёты приходят в pull: свои и — руководителю отдела — отправленные чужие.
PULL_DAYS = 93
# Поля содержимого отчёта (что заменяет report.save).
CONTENT_KEYS = ("shift_start", "shift_end", "break_minutes", "lines", "comment")
# Действия, которые не считаются работой для собранного отчёта: сам отчёт, прочтение уведомлений, смена своего пароля.
ACTIVITY_SKIP_PREFIXES = ("report.", "notification.", "account.", "overtime.")
ACTIVITY_LIMIT = 3000      # не больше стольких действий в pull (самые свежие)


def _activity_objects() -> dict[str, tuple]:
    """Как по записи журнала найти проект, задачу и название объекта: тип объекта → (таблица, столбец проекта,
    функция «строка → название»). Тип без проекта — работа «не по проекту» (закупка, склад, тендеры…)."""
    t = _db
    return {
        "project": (t.projects, "id", lambda r: r.code),
        "task": (t.tasks, "project_id", lambda r: f"«{r.title}»"),
        "document": (t.documents, "project_id", lambda r: f"{r.code} «{r.title}»"),
        "kit": (t.kits, "project_id", lambda r: r.code),
        "calc_draft": (t.calc_drafts, "project_id", lambda r: "расчёт комплекта"),
        "tech_process": (t.tech_processes, "project_id", lambda r: f"«{r.title}»"),
        "work_order": (t.work_orders, "project_id", lambda r: f"наряд № {r.doc_no}"),
        "product_instance": (t.product_instances, "project_id", lambda r: f"№ {r.serial_no}"),
        "passport": (t.passports, "project_id", lambda r: "паспорт"),
        "claim": (t.claims, "project_id", lambda r: f"рекламация № {r.claim_no}"),
        "change_request": (t.change_requests, "project_id", lambda r: f"запрос № {r.ecr_no}"),
        "change_order": (t.change_orders, "project_id", lambda r: f"извещение № {r.eco_no}"),
        "procurement_need": (t.procurement_needs, "project_id", lambda r: "потребность"),
        "stock_reservation": (t.stock_reservations, "project_id", lambda r: "резерв"),
        "stock_movement": (t.stock_movements, "project_id", lambda r: f"движение № {r.doc_no}"),
        "tender": (t.tenders, None, lambda r: f"«{r.title}»"),
        "tkp": (t.tkp, None, lambda r: f"«{r.label}»"),
        "customer": (t.customers, None, lambda r: f"«{r.name}»"),
        "supplier": (t.suppliers, None, lambda r: f"«{r.name}»"),
        "purchase_order": (t.purchase_orders, None, lambda r: f"заказ № {r.order_no}"),
        "stock_item": (t.stock_items, None, lambda r: f"{r.code}"),
        "warehouse": (t.warehouses, None, lambda r: f"{r.code}"),
    }


def _w(fn, *a, **kw):
    """Вызвать правило vector.core.worktime; WorktimeError → Rejected (ошибка ввода, с полем и замечаниями)."""
    try:
        return fn(*a, **kw)
    except (wt.WorktimeError, wd.WorkdayError) as e:
        raise Rejected(e.code, e.message_ru, e.details)


class WorktimeMixin:
    """Обработчики report.* и дневные отчёты для pull."""

    # ------------------------------------------------------------ журнал шагов
    def _wdecide(self, ctx, rid: str, decision: str, details: dict, auth) -> int:
        """Неизменяемая запись шага отчёта (следующий номер) и основание решения (замещение — D-34)."""
        ev = daily_report_events
        seq = (ctx.conn.execute(sa.select(sa.func.max(ev.c.seq)).where(ev.c.report_id == rid)).scalar() or 0) + 1
        ctx.conn.execute(ev.insert().values(report_id=rid, seq=seq, decision=decision, details_json=canon(details),
                                            authority=auth.describe(), decided_by=ctx.user_id, decided_at=ctx.now))
        self._record_basis(ctx, "daily_report_event", f"{rid}#{seq}", 1, auth)
        return seq

    def _whistory(self, conn, rid: str) -> list[dict]:
        """История отчёта: шаг, подпись, подробности, кто, когда, за кого (замещение руководителя отдела)."""
        ev = daily_report_events
        out = []
        for r in conn.execute(sa.select(ev).where(ev.c.report_id == rid).order_by(ev.c.seq)):
            b = self._basis(conn, "daily_report_event", f"{rid}#{r.seq}")
            d = json.loads(r.details_json)
            label = "Собран программой" if r.decision == "created" and d.get("origin") == "auto" else \
                DECISION_LABELS_RU.get(r.decision, r.decision)
            out.append({"seq": r.seq, "decision": r.decision, "label": label,
                        "details": json.loads(r.details_json), "authority": r.authority, "by": r.decided_by,
                        "at": iso(r.decided_at), "on_behalf_of": b["on_behalf_of"] if b else None})
        return out

    # ------------------------------------------------------------ представление
    def _report_lines(self, conn, rid: str) -> list[dict]:
        """Строки отчёта по порядку — с кодом проекта и названием задачи (руководитель отдела может не видеть сам
        проект, но должен понимать, на что ушло время)."""
        src = {r.line_no: r.source for r in conn.execute(sa.select(daily_report_line_sources)
                                                         .where(daily_report_line_sources.c.report_id == rid))}
        rows = conn.execute(sa.select(daily_report_lines, projects.c.code.label("project_code"),
                                      projects.c.title.label("project_title"), tasks.c.title.label("task_title"))
                            .outerjoin(projects, projects.c.id == daily_report_lines.c.project_id)
                            .outerjoin(tasks, tasks.c.id == daily_report_lines.c.task_id)
                            .where(daily_report_lines.c.report_id == rid).order_by(daily_report_lines.c.line_no))
        return [{"line_no": r.line_no, "project_id": r.project_id, "project_code": r.project_code,
                 "project_title": r.project_title, "task_id": r.task_id, "task_title": r.task_title,
                 "minutes": r.minutes, "note": r.note, "source": src.get(r.line_no)} for r in rows]

    def _report_marks(self, conn, o: dict, cache: Optional[dict] = None) -> dict:
        """Отметки дня отчёта (D-49, A-53 п. 3): работа в праздничный / выходной день, сверхурочно — по «чистому»
        времени смены (без перерыва) против нормы графика автора на эту дату (график и производственный календарь —
        действующие сейчас: отметка пересчитывается, если администратор поправил график или календарь). cache —
        {("s", user_id): график, ("c", год): календарь} на время одного pull, чтобы не читать их на каждый отчёт."""
        cache = {} if cache is None else cache
        key = ("s", o["user_id"])
        if key not in cache:
            cache[key] = self._schedule_for(conn, o["user_id"])
        eff = cache[key]
        day = o["work_date"]
        ckey = ("c", day.year)
        if ckey not in cache:
            cache[ckey] = self._calendar_around(conn, day)
        net = wt.shift_net(o["shift_start"], o["shift_end"], o["break_minutes"])
        return wd.day_marks(eff["schedule"], day, net, cache[ckey], bool(eff["exempt"]))

    def v_daily_report(self, conn, o: dict, cache: Optional[dict] = None) -> dict:
        """Дневной отчёт для клиента: смена «ЧЧ:ММ», строки, итоги, статус, проверка и история; отметки дня
        (праздник, выходной, сверхурочно — D-49)."""
        auto = self._get(conn, daily_report_auto, o["id"], "report_id")
        return {"id": o["id"], "user_id": o["user_id"], "work_date": o["work_date"].isoformat(), "status": o["status"],
                "status_label": wt.STATUS_LABELS_RU[o["status"]], "origin": "auto" if auto else "manual",
                "assembled": json.loads(auto["summary_json"]) if auto else None,
                "overnight": wt.overnight(o["shift_start"], o["shift_end"]),
                "shift_start": wt.time_label(o["shift_start"]) or None, "shift_end": wt.time_label(o["shift_end"]) or None,
                "break_minutes": o["break_minutes"], "total_minutes": o["total_minutes"], "comment": o["comment"],
                "lines": self._report_lines(conn, o["id"]), "submitted_at": iso(o["submitted_at"]),
                "reviewed_by": o["reviewed_by"], "reviewed_at": iso(o["reviewed_at"]), "review_comment": o["review_comment"],
                "version": o["version"], "created_at": iso(o["created_at"]), "updated_at": iso(o["updated_at"]),
                "history": self._whistory(conn, o["id"]), "day_marks": self._report_marks(conn, o, cache)}

    # ------------------------------------------------------------ общие проверки
    def _own_report(self, ctx, rid: Any, ev: Optional[int]) -> dict:
        """Свой отчёт по id с проверкой версии; чужой — отказ (правит и отправляет только автор)."""
        o = self._require(ctx.conn, daily_reports, rid, "Дневной отчёт")
        if o["user_id"] != ctx.user_id:
            raise Denied("not_own_report", "Дневной отчёт правит и отправляет только его автор.")
        self._check_version(o, ev, lambda x: self.v_daily_report(ctx.conn, x))
        return o

    def _report_state(self, conn, o: dict, *allowed: str) -> None:
        """Отчёт в одном из состояний allowed, иначе видимый конфликт с текущим состоянием."""
        if o["status"] not in allowed:
            raise Conflict("report_state", f"Отчёт за {wt.date_label(o['work_date'].isoformat())} уже "
                                           f"«{wt.STATUS_LABELS_RU[o['status']]}» — действие не применено.",
                           {"current": self.v_daily_report(conn, o)})

    def _check_lines(self, conn, uid: str, lines: list[dict], now) -> None:
        """Проект строки — тот, где сотрудник uid сейчас участник; задача — существующая задача этого проекта."""
        for ln in lines:
            if ln["project_id"] is not None:
                if self._get(conn, projects, ln["project_id"]) is None or not project_member(conn, uid, ln["project_id"], now):
                    raise Rejected("not_project_member", f"Строка {ln['line_no']}: выберите проект, где вы участник.",
                                   {"field": "lines", "line": ln["line_no"]})
            if ln["task_id"] is not None:
                t = self._get(conn, tasks, ln["task_id"])
                if t is None or t["project_id"] != ln["project_id"]:
                    raise Rejected("task_invalid", f"Строка {ln['line_no']}: задача — из выбранного проекта.",
                                   {"field": "lines", "line": ln["line_no"]})

    def _write_report_lines(self, conn, rid: str, lines: list[dict]) -> None:
        """Заменить строки отчёта целиком (только черновик / возвращённый — проверено вызывающим)."""
        conn.execute(daily_report_lines.delete().where(daily_report_lines.c.report_id == rid))
        conn.execute(daily_report_line_sources.delete().where(daily_report_line_sources.c.report_id == rid))
        for ln in lines:
            conn.execute(daily_report_lines.insert().values(report_id=rid, **{k: v for k, v in ln.items() if k != "source"}))
            if ln.get("source"):
                conn.execute(daily_report_line_sources.insert().values(report_id=rid, line_no=ln["line_no"], source=ln["source"]))

    @staticmethod
    def _report_content(o: dict, lines: list[dict]) -> dict:
        """Содержимое отчёта в виде, который принимает vector.core.worktime.parse_report (для слияния с правкой)."""
        return {"shift_start": wt.time_label(o["shift_start"]) or None, "shift_end": wt.time_label(o["shift_end"]) or None,
                "break_minutes": o["break_minutes"], "comment": o["comment"],
                "lines": [{k: ln.get(k) for k in ("project_id", "task_id", "minutes", "note", "source")} for ln in lines]}

    @staticmethod
    def _who_day(name: str, o: dict) -> str:
        """«Кузнецов Дмитрий, 17.11.2026, вт» — для текста уведомления."""
        return f"{name}, {wt.date_label(o['work_date'].isoformat())}"

    # ------------------------------------------------------------ команды автора
    def _report_create(self, ctx, p, ev):
        """report.create {id, work_date, shift_start?, shift_end?, break_minutes?, lines?, comment?, origin?, summary?} —
        свой отчёт за день. Один отчёт на сотрудника за день; день — не в будущем и не старше 31 дня.

        origin «auto» — отчёт собрала программа по действиям за день (D-43): статус «На самопроверке», отметка
        daily_report_auto со сводкой summary ({actions, measured_minutes, lines, workday_minutes?} — целые;
        workday_minutes — минут по таймеру рабочего дня, D-46) и уведомление автору
        «проверьте». Без origin — «Черновик», который сотрудник заполняет сам."""
        only_keys(p, {"id", "work_date", "origin", "summary", *CONTENT_KEYS})
        origin = p.get("origin") or "manual"
        if origin not in ("manual", "auto"):
            raise Rejected("invalid_origin", "Источник отчёта — «manual» или «auto».", {"field": "origin"})
        summary = p.get("summary") or {}
        if not isinstance(summary, dict) or set(summary) - {"actions", "measured_minutes", "lines", "workday_minutes"} or \
                any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in summary.values()):
            raise Rejected("invalid_summary", "Сводка собранного отчёта — целые числа actions, measured_minutes, lines.",
                           {"field": "summary"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "report.create", ctx.now)
        rid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, daily_reports, rid)
        day = _w(wt.parse_work_date, p.get("work_date"))
        _w(wt.check_new_date, day, ctx.now.date())
        other = ctx.conn.execute(sa.select(daily_reports).where(daily_reports.c.user_id == ctx.user_id,
                                                                daily_reports.c.work_date == date.fromisoformat(day))).first()
        if other is not None:
            raise Conflict("report_exists", f"Отчёт за {wt.date_label(day)} уже есть — откройте его.",
                           {"current": self.v_daily_report(ctx.conn, dict(other._mapping))})
        c = _w(wt.parse_report, p)
        self._check_lines(ctx.conn, ctx.user_id, c["lines"], ctx.now)
        total = sum(x["minutes"] for x in c["lines"])
        ctx.conn.execute(daily_reports.insert().values(
            id=rid, user_id=ctx.user_id, work_date=date.fromisoformat(day), status="self_check" if origin == "auto" else "draft",
            shift_start=c["shift_start"], shift_end=c["shift_end"], break_minutes=c["break_minutes"], total_minutes=total,
            comment=c["comment"], version=1, created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id,
            updated_at=ctx.now))
        self._write_report_lines(ctx.conn, rid, c["lines"])
        if origin == "auto":
            ctx.conn.execute(daily_report_auto.insert().values(report_id=rid, assembled_at=ctx.now, summary_json=canon(summary)))
        self._wdecide(ctx, rid, "created", {"work_date": day, "total_minutes": total, "lines": len(c["lines"]),
                                            **({"origin": "auto", "summary": summary} if origin == "auto" else {})}, auth)
        ctx.log("daily_report", rid, auth, None, 1, {"event": "ReportCreated", "work_date": day, "total_minutes": total,
                                                     "origin": origin})
        if origin == "auto":
            self._notify(ctx, [ctx.user_id], "report_assembled", f"Отчёт за {wt.date_label(day)} собран — проверьте",
                         body=f"Программа собрала отчёт по вашим действиям за день ({wt.hours_label(total)}). Проверьте "
                              "смену и строки, отметьте пункты самопроверки — и отчёт уйдёт руководителю отдела.",
                         object_type="daily_report", object_id=rid, include_actor=True)
        return {"daily_report": self.v_daily_report(ctx.conn, self._get(ctx.conn, daily_reports, rid))}

    def _report_save(self, ctx, p, ev):
        """report.save {id, shift_start?, shift_end?, break_minutes?, lines?, comment?} — правка своего черновика или
        возвращённого отчёта; переданное заменяется целиком (строки — все). Без изменений — unchanged."""
        only_keys(p, {"id", *CONTENT_KEYS})
        auth = self.policy.check(ctx.conn, ctx.user_id, "report.save", ctx.now)
        o = self._own_report(ctx, req_uuid(p, "id"), ev)
        self._report_state(ctx.conn, o, *wt.EDITABLE)
        old = self._report_content(o, self._report_lines(ctx.conn, o["id"]))
        c = _w(wt.parse_report, {**old, **{k: p[k] for k in CONTENT_KEYS if k in p}})
        if "lines" in p:
            self._check_lines(ctx.conn, ctx.user_id, c["lines"], ctx.now)
        before = _w(wt.parse_report, old)
        changed = sorted(k for k in ("shift_start", "shift_end", "break_minutes", "comment", "lines") if c[k] != before[k])
        if not changed:
            return {"daily_report": self.v_daily_report(ctx.conn, o), "unchanged": True}
        total = sum(x["minutes"] for x in c["lines"])
        n = self._update_versioned(ctx, daily_reports, o, {
            "shift_start": c["shift_start"], "shift_end": c["shift_end"], "break_minutes": c["break_minutes"],
            "comment": c["comment"], "total_minutes": total, "updated_by": ctx.user_id, "updated_at": ctx.now})
        if "lines" in changed:
            self._write_report_lines(ctx.conn, o["id"], c["lines"])
        self._wdecide(ctx, o["id"], "saved", {"changed": changed, "total_minutes": total}, auth)
        ctx.log("daily_report", o["id"], auth, o["version"], n["version"], {"event": "ReportSaved", "changed": changed})
        return {"daily_report": self.v_daily_report(ctx.conn, n)}

    def _report_submit(self, ctx, p, ev):
        """report.submit {id, self_check: {shift, lines, hours}} — самопроверка (все отметки, A-48 п. 4) и свой полный
        отчёт на проверку («На проверке»); уведомление руководителю подразделения автора (D-44) или, если автор вне
        оргструктуры, всем руководителям отделов. Смена не должна пересекаться со сменой
        соседнего дня (ночные смены)."""
        only_keys(p, {"id", "self_check"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "report.submit", ctx.now)
        o = self._own_report(ctx, req_uuid(p, "id"), ev)
        self._report_state(ctx.conn, o, *wt.EDITABLE)
        lines = self._report_lines(ctx.conn, o["id"])
        r = {"shift_start": o["shift_start"], "shift_end": o["shift_end"], "break_minutes": o["break_minutes"], "lines": lines}
        _w(wt.check_submit, r)
        checked = _w(wt.parse_self_check, p.get("self_check"))
        near = {x.work_date: {"work_date": x.work_date.isoformat(), "shift_start": x.shift_start, "shift_end": x.shift_end}
                for x in ctx.conn.execute(sa.select(daily_reports).where(
                    daily_reports.c.user_id == ctx.user_id,
                    daily_reports.c.work_date.in_([o["work_date"] - timedelta(days=1), o["work_date"] + timedelta(days=1)])))}
        clash = wt.shift_overlap(near.get(o["work_date"] - timedelta(days=1)), {**r, "work_date": o["work_date"].isoformat()},
                                 near.get(o["work_date"] + timedelta(days=1)))
        if clash:
            raise Rejected("shift_overlap", clash, {"field": "shift_start"})
        t = wt.totals(r)
        n = self._update_versioned(ctx, daily_reports, o, {"status": "submitted", "submitted_at": ctx.now, "reviewed_by": None,
                                                           "reviewed_at": None, "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._wdecide(ctx, o["id"], "submitted", {"total_minutes": t["total"], "shift_net": t["net"],
                                                  "resubmitted": o["status"] == "returned", "self_check": checked}, auth)
        ctx.log("daily_report", o["id"], auth, o["version"], n["version"], {"event": "ReportSubmitted", "total_minutes": t["total"]})
        who = self._who_day(self._user_name(ctx.conn, ctx.user_id), o)
        # Кому на проверку (D-44): руководителю подразделения автора (или вышестоящего) и его замещающим; автор вне
        # оргструктуры — всем руководителям отделов, как раньше (D-42).
        to = self._report_reviewers(ctx.conn, ctx.user_id, ctx.now)
        self._notify(ctx, to if to is not None else self._users_with_role(ctx.conn, "dept_head", ctx.now), "report_submitted",
                     f"Дневной отчёт на проверке: {who}", body=f"Всего: {wt.hours_label(t['total'])}",
                     object_type="daily_report", object_id=o["id"])
        return {"daily_report": self.v_daily_report(ctx.conn, n)}

    def _report_reopen(self, ctx, p, ev):
        """report.reopen {id, reason} — исправить свой отправленный или принятый отчёт: с основанием → «Черновик».
        Если отчёт уже был принят — уведомление принявшему."""
        only_keys(p, {"id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "report.reopen", ctx.now)
        o = self._own_report(ctx, req_uuid(p, "id"), ev)
        self._report_state(ctx.conn, o, "submitted", "accepted")
        reason = _w(wt._text, p.get("reason"), "reason", "основание исправления", wt.MAX_COMMENT)
        n = self._update_versioned(ctx, daily_reports, o, {"status": "draft", "submitted_at": None, "reviewed_by": None,
                                                           "reviewed_at": None, "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._wdecide(ctx, o["id"], "reopened", {"reason": reason, "from": o["status"]}, auth)
        ctx.log("daily_report", o["id"], auth, o["version"], n["version"], {"event": "ReportReopened", "from": o["status"]})
        if o["status"] == "accepted" and o["reviewed_by"]:
            self._notify(ctx, [o["reviewed_by"]], "report_reopened",
                         f"Принятый отчёт открыт для исправления: {self._who_day(self._user_name(ctx.conn, ctx.user_id), o)}",
                         body=reason, object_type="daily_report", object_id=o["id"])
        return {"daily_report": self.v_daily_report(ctx.conn, n)}

    # ------------------------------------------------------------ проверка руководителем
    def _report_review(self, ctx, p, ev):
        """report.review {id, decision (accept|return), comment?} — руководитель отдела, не автор: «Принят» или
        «Возвращён» (причина обязательна). Уведомление автору. Автор в оргструктуре — проверяет руководитель
        подразделения по его цепочке (роль для этого не нужна — D-45, ответ владельца на A-50 п. 2) или замещающий
        такого руководителя (D-44); вне оргструктуры — любой руководитель отдела по роли (D-42)."""
        only_keys(p, {"id", "decision", "comment"})
        by_role = self.policy.can(ctx.conn, ctx.user_id, "report.review", ctx.now)
        if not by_role and not self._is_org_head(ctx.conn, ctx.user_id):
            self.policy.check(ctx.conn, ctx.user_id, "report.review", ctx.now)   # не руководитель — отказ по матрице прав
        auth = self.policy.check(ctx.conn, ctx.user_id, "report.review", ctx.now) if by_role else None
        o = self._require(ctx.conn, daily_reports, req_uuid(p, "id"), "Дневной отчёт")
        self._check_version(o, ev, lambda x: self.v_daily_report(ctx.conn, x))
        self._report_state(ctx.conn, o, "submitted")
        if o["user_id"] == ctx.user_id:
            raise Denied("four_eyes", "Свой дневной отчёт проверяет другой руководитель («четыре глаза»).")
        auth = self._review_authority(ctx.conn, ctx.user_id, o["user_id"], ctx.now) or auth
        if auth is None:
            # Руководитель подразделения без роли, а автор вне оргструктуры: такой отчёт — руководителям отделов (D-42).
            raise Denied("not_your_unit", "Этот сотрудник не включён в оргструктуру — его отчёт проверяет руководитель "
                                          "отдела (по роли). Сотрудник — не из ваших подразделений.",
                         {"author_id": o["user_id"]})
        dec = p.get("decision")
        if dec not in ("accept", "return"):
            raise Rejected("invalid_decision", "Решение — «Принять» или «Вернуть».", {"field": "decision"})
        comment = _w(wt._text, p.get("comment"), "comment", "причину возврата (что исправить)", wt.MAX_COMMENT,
                     required=(dec == "return")) or None
        st = "accepted" if dec == "accept" else "returned"
        n = self._update_versioned(ctx, daily_reports, o, {"status": st, "reviewed_by": ctx.user_id, "reviewed_at": ctx.now,
                                                           "review_comment": comment, "updated_by": ctx.user_id,
                                                           "updated_at": ctx.now})
        self._wdecide(ctx, o["id"], st, {"comment": comment}, auth)
        ctx.log("daily_report", o["id"], auth, o["version"], n["version"],
                {"event": "ReportAccepted" if st == "accepted" else "ReportReturned"})
        day = wt.date_label(o["work_date"].isoformat())
        self._notify(ctx, [o["user_id"]], "report_accepted" if st == "accepted" else "report_returned",
                     f"Дневной отчёт за {day}: {wt.STATUS_LABELS_RU[st].lower()}", body=comment,
                     object_type="daily_report", object_id=o["id"])
        return {"daily_report": self.v_daily_report(ctx.conn, n)}

    # ------------------------------------------------------------ pull
    # ------------------------------------------------------------ заявки на часы вне обычного графика (A-55/A-56)
    @staticmethod
    def _overtime_period_bounds(day: date, mode: str) -> tuple[date, date]:
        """Календарные границы месяца, квартала или года; любой допустимый период не длиннее года."""
        if mode == "month":
            start = day.replace(day=1)
            end = day.replace(day=monthrange(day.year, day.month)[1])
        elif mode == "quarter":
            month = ((day.month - 1) // 3) * 3 + 1
            start = date(day.year, month, 1)
            last_month = month + 2
            end = date(day.year, last_month, monthrange(day.year, last_month)[1])
        elif mode == "year":
            start, end = date(day.year, 1, 1), date(day.year, 12, 31)
        else:
            raise ValueError("Неизвестный учётный период.")
        if (end - start).days >= 366:
            raise ValueError("Учётный период превышает год.")
        return start, end

    def _overtime_period_snapshot(self, conn, rid: str) -> Optional[dict]:
        """Найти замороженные границы периода заявки, если она уже создана."""
        r = conn.execute(sa.select(overtime_request_periods).where(overtime_request_periods.c.request_id == rid)).first()
        return ({"mode": r.mode, "start": r.period_start.isoformat(), "end": r.period_end.isoformat(),
                 "config_version": r.config_version} if r else None)

    def _vovertime(self, o: dict, period: Optional[dict] = None) -> dict:
        """Стабильное представление заявки; денежные ставки/суммы намеренно отсутствуют (их считает 1С)."""
        out = {k: (iso(o[k]) if k in ("start_at", "end_at", "created_at", "decided_at") and o.get(k) else
                   o[k].isoformat() if k == "work_date" and o.get(k) else o.get(k))
               for k in ("id", "user_id", "work_date", "start_at", "end_at", "minutes", "kind", "mode", "reason",
                         "status", "created_at", "decided_by", "decided_at", "decision_comment", "authority", "version")}
        out["period"] = period
        return out

    def _overtime_period_set(self, ctx, p, ev):
        """Установить компании календарный учётный период; отсутствие настройки блокирует регистрацию часов."""
        only_keys(p, {"mode"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "schedule.manage", ctx.now)
        mode = p.get("mode")
        if mode not in ("month", "quarter", "year"):
            raise Rejected("invalid_period", "Период: календарный месяц, квартал или год.", {"field": "mode"})
        table = overtime_period_config
        old = self._get(ctx.conn, table, 1)
        if old is None:
            ctx.conn.execute(table.insert().values(id=1, mode=mode, version=1, updated_by=ctx.user_id, updated_at=ctx.now))
            before, version = None, 1
        else:
            self._check_version(old, ev, lambda x: x)
            n = self._update_versioned(ctx, table, old, {"mode": mode, "updated_by": ctx.user_id, "updated_at": ctx.now})
            before, version = old["version"], n["version"]
        ctx.log("overtime_period_config", "1", auth, before, version, {"mode": mode})
        return {"overtime_period_config": {"mode": mode, "version": version, "updated_at": iso(ctx.now)}}

    def _overtime_event(self, ctx, rid: str, action: str, details: dict, authority) -> None:
        """Неизменяемая история создания и решения; записывает исполнителя и основание его полномочий."""
        ev = overtime_events
        seq = (ctx.conn.execute(sa.select(sa.func.max(ev.c.seq)).where(ev.c.request_id == rid)).scalar() or 0) + 1
        ctx.conn.execute(ev.insert().values(request_id=rid, seq=seq, event=action, details_json=canon(details),
                                            actor_id=ctx.user_id, authority=authority.describe(), at=ctx.now))
        self._record_basis(ctx, "overtime_event", f"{rid}#{seq}", 1, authority)

    def _overtime_request_create(self, ctx, p, ev):
        """Создать предварительную заявку или срочную запись после факта: минуты и причину вводит сам сотрудник, никакие действия программы не выводятся автоматически."""
        only_keys(p, {"id", "work_date", "start_at", "end_at", "minutes", "kind", "mode", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "worktime.own", ctx.now)
        rid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, overtime_requests, rid)
        try:
            work_date = date.fromisoformat(p.get("work_date", ""))
        except (TypeError, ValueError):
            raise Rejected("invalid_date", "Дата работы должна быть в формате ГГГГ-ММ-ДД.", {"field": "work_date"})
        config = self._get(ctx.conn, overtime_period_config, 1)
        if config is None:
            raise Rejected("period_not_configured", "Администратор должен сначала выбрать учётный период компании.")
        period_start, period_end = self._overtime_period_bounds(work_date, config["mode"])
        start = _w(wd.parse_dt, p.get("start_at"), "start_at")
        end = _w(wd.parse_dt, p.get("end_at"), "end_at")
        minutes = p.get("minutes")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0 or minutes > 1440 or end <= start or minutes > int((end-start).total_seconds() // 60):
            raise Rejected("invalid_duration", "Укажите положительные минуты, не превышающие длительность интервала.", {"field": "minutes"})
        kind, mode = p.get("kind"), p.get("mode")
        if kind not in ("overtime", "weekend", "holiday") or mode not in ("planned", "urgent_after"):
            raise Rejected("invalid_kind", "Вид: сверхурочно / выходной / праздник; режим: заранее / срочно после факта.")
        reason = _w(wd.parse_reason, p.get("reason"))
        # Проверяем категорию по графику и календарю этой даты: клиент не может назвать федеральный праздник обычной
        # сверхурочной работой или объявить запланированный рабочий день выходным.
        eff = self._schedule_for(ctx.conn, ctx.user_id)
        calendar = self._calendar_around(ctx.conn, work_date)
        day_type = wd.day_type(eff["schedule"], work_date, calendar)
        expected = {"holiday": "holiday", "weekend": "weekend"}.get(kind)
        if expected and day_type != expected:
            raise Rejected("day_kind_mismatch", "Вид заявки не совпадает с графиком и календарём этого дня.",
                           {"field": "kind", "day_type": day_type})
        if kind == "overtime" and day_type in ("holiday", "weekend"):
            raise Rejected("day_kind_mismatch", "Для работы в нерабочий день выберите «выходной» или «праздник».",
                           {"field": "kind", "day_type": day_type})
        raw_start = p.get("start_at")
        try:
            local_start = raw_start if isinstance(raw_start, datetime) else datetime.fromisoformat(str(raw_start).replace("Z", "+00:00"))
        except ValueError:
            local_start = start  # parse_dt already returned the canonical invalid-time error above.
        if local_start.date() != work_date:
            raise Rejected("date_mismatch", "Дата учётной записи должна совпадать с местной датой начала интервала.",
                           {"field": "work_date"})
        if mode == "planned" and start <= ctx.now:
            raise Rejected("not_future", "Предварительное согласование нужно запросить до начала работы.", {"field": "start_at"})
        if mode == "urgent_after" and end > ctx.now:
            raise Rejected("not_finished", "Срочную работу после факта фиксируют после окончания интервала.", {"field": "end_at"})
        values = dict(id=rid, user_id=ctx.user_id, work_date=work_date, start_at=start, end_at=end, minutes=minutes,
                      kind=kind, mode=mode, reason=reason, status="pending", created_at=ctx.now, version=1)
        ctx.conn.execute(overtime_requests.insert().values(**values))
        ctx.conn.execute(overtime_request_periods.insert().values(request_id=rid, mode=config["mode"],
            period_start=period_start, period_end=period_end, config_version=config["version"], created_at=ctx.now))
        self._overtime_event(ctx, rid, "created", {"mode": mode, "kind": kind, "minutes": minutes, "reason": reason,
                                                   "accounting_period": config["mode"],
                                                   "period_start": period_start.isoformat(),
                                                   "period_end": period_end.isoformat()}, auth)
        ctx.log("overtime_request", rid, auth, None, 1, {"mode": mode, "kind": kind, "minutes": minutes})
        reviewers = self._report_reviewers(ctx.conn, ctx.user_id, ctx.now)
        if reviewers is None:
            reviewers = [u for u in self._users_with_role(ctx.conn, "dept_head", ctx.now) if u != ctx.user_id]
        self._notify(ctx, reviewers, "overtime_request", "Запрос на работу вне графика требует решения руководителя",
                     body=f"{work_date:%d.%m.%Y}: {minutes} мин.; причина: {reason}",
                     object_type="overtime_request", object_id=rid)
        return {"overtime_request": self._vovertime(values, self._overtime_period_snapshot(ctx.conn, rid))}

    def _overtime_request_decide(self, ctx, p, ev):
        """Решение руководителя — отдельное действие с проверкой версии; ожидающую заявку нельзя незаметно изменить."""
        only_keys(p, {"id", "decision", "comment"})
        by_role = self.policy.can(ctx.conn, ctx.user_id, "report.review", ctx.now)
        if not by_role and not self._is_org_head(ctx.conn, ctx.user_id):
            self.policy.check(ctx.conn, ctx.user_id, "report.review", ctx.now)
        auth = self.policy.check(ctx.conn, ctx.user_id, "report.review", ctx.now) if by_role else None
        o = self._require(ctx.conn, overtime_requests, req_uuid(p, "id"), "Заявка на работу вне графика")
        self._check_version(o, ev, lambda x: self._vovertime(x, self._overtime_period_snapshot(ctx.conn, x["id"])))
        if o["user_id"] == ctx.user_id:
            raise Denied("four_eyes", "Свою заявку согласует руководитель («четыре глаза»).")
        if o["status"] != "pending":
            raise Conflict("already_decided", "По заявке уже принято решение.", {"current": self._vovertime(o)})
        auth = self._review_authority(ctx.conn, ctx.user_id, o["user_id"], ctx.now) or auth
        if auth is None:
            raise Denied("not_your_unit", "Эту заявку решает руководитель подразделения сотрудника.")
        decision = p.get("decision")
        if decision not in ("approve", "reject"):
            raise Rejected("invalid_decision", "Выберите «согласовать» или «отклонить».", {"field": "decision"})
        comment = _w(wd.parse_reason, p.get("comment"), decision == "reject", "comment", "причину отказа")
        status = "approved" if decision == "approve" else "rejected"
        n = self._update_versioned(ctx, overtime_requests, o, {"status": status, "decided_by": ctx.user_id,
            "decided_at": ctx.now, "decision_comment": comment, "authority": auth.describe()})
        self._overtime_event(ctx, o["id"], status, {"comment": comment}, auth)
        ctx.log("overtime_request", o["id"], auth, o["version"], n["version"], {"decision": status})
        self._notify(ctx, [o["user_id"]], "overtime_decided", f"Заявка на работу вне графика: {status}",
                     body=comment or "", object_type="overtime_request", object_id=o["id"])
        return {"overtime_request": self._vovertime(n, self._overtime_period_snapshot(ctx.conn, o["id"]))}

    def _pull_overtime(self, conn, uid: str, now) -> list[dict]:
        """Свои заявки и заявки, видимые проверяющим отчёты сотрудника; скрытые сведения об активности не выдаются."""
        by_role = self.policy.can(conn, uid, "report.review", now)
        visible = self._review_scope(conn, uid, now, by_role) if by_role or self._is_org_head(conn, uid) else None
        rows = conn.execute(sa.select(overtime_requests).order_by(overtime_requests.c.created_at.desc())).all()
        return [self._vovertime(dict(r._mapping), self._overtime_period_snapshot(conn, r.id)) for r in rows
                if r.user_id == uid or (visible is not None and visible(r.user_id))]

    def _pull_worktime(self, conn, uid: str, now) -> dict:
        """Свои отчёты за PULL_DAYS дней; проверяющему — ещё и отправленные (не черновики и не на самопроверке) чужие:
        руководителю подразделения — сотрудников по цепочке вниз (D-44, D-45: роль не нужна), руководителю отдела по
        роли — ещё и вне оргструктуры (D-42); проекты, где сотрудник участник (для строк отчёта); может ли он
        проверять отчёты."""
        since = (now - timedelta(days=PULL_DAYS)).date()
        dr = daily_reports
        by_role = self.policy.can(conn, uid, "worktime.read_all", now)
        reviewer = by_role or self._is_org_head(conn, uid)
        cond = dr.c.user_id == uid
        if reviewer:
            # Черновики и отчёты на самопроверке — личное дело автора, пока он их не отправил.
            cond = sa.or_(cond, dr.c.status.in_(("submitted", "accepted", "returned")))
        rows = list(conn.execute(sa.select(dr).where(cond, dr.c.work_date >= since).order_by(dr.c.work_date.desc(), dr.c.user_id)))
        if reviewer:
            # Оргструктура (D-44): чужие отчёты — только сотрудников своих подразделений (и вне оргструктуры).
            visible = self._review_scope(conn, uid, now, by_role)
            rows = [r for r in rows if r.user_id == uid or visible(r.user_id)]
        mine = [{"id": r.id, "code": r.code, "title": r.title}
                for r in conn.execute(sa.select(projects.c.id, projects.c.code, projects.c.title).order_by(projects.c.code))
                if project_member(conn, uid, r.id, now)]
        cache: dict = {}
        period_cfg = self._get(conn, overtime_period_config, 1)
        return {"daily_reports": [self.v_daily_report(conn, dict(r._mapping), cache) for r in rows],
                "worktime_projects": mine, "worktime_reviewer": reviewer,
                "worktime_activity": self._worktime_activity(conn, uid, now),
                "overtime_requests": self._pull_overtime(conn, uid, now),
                "overtime_period_config": ({"mode": period_cfg["mode"], "version": period_cfg["version"],
                                            "updated_at": iso(period_cfg["updated_at"])} if period_cfg else None),
                "worktime_weekly_metrics": self._weekly_department_metrics(conn, uid, now, by_role)}

    def _weekly_department_metrics(self, conn, uid: str, now, by_role: bool) -> dict:
        """Прозрачная агрегированная сводка за прошлую полную неделю для руководителя и его цепочки вверх.

        В сводку входят только отправленные сотрудниками отчёты, без идентификаторов сотрудников и их действий.
        Значения считаются по оргструктуре на момент запроса; это операционная метрика, не табель и не мониторинг.
        """
        if not (by_role or self._is_org_head(conn, uid)):
            return {"week_start": None, "week_end": None, "units": []}
        monday = now.date() - timedelta(days=now.date().weekday() + 7)
        sunday = monday + timedelta(days=6)
        units = {r.id: dict(r._mapping) for r in conn.execute(
            sa.select(org_units).where(org_units.c.active == sa.true()))}
        memberships = {r.user_id: r.unit_id for r in conn.execute(sa.select(org_members.c.user_id, org_members.c.unit_id))}
        visible = self._review_scope(conn, uid, now, by_role)
        # Build aggregates by unit and every active ancestor: a head sees own department plus rolled-up descendants.
        totals: dict[str, dict[str, int]] = {}
        rows = conn.execute(sa.select(daily_reports).where(
            daily_reports.c.work_date >= monday, daily_reports.c.work_date <= sunday,
            daily_reports.c.status.in_(("submitted", "accepted", "returned"))))
        for row in rows:
            unit_id = memberships.get(row.user_id)
            if unit_id is None or unit_id not in units or not visible(row.user_id):
                continue
            minutes = max(0, (row.shift_end or 0) - (row.shift_start or 0) - (row.break_minutes or 0))
            for ancestor in og.ancestors(units, unit_id):
                stat = totals.setdefault(ancestor, {"reports": 0, "minutes": 0, "submitted": 0,
                                                     "accepted": 0, "returned": 0})
                stat["reports"] += 1
                stat["minutes"] += minutes
                stat[row.status] += 1
        heads = {uid} | {holder for _, role, _, holder in active_delegated(conn, uid, now) if role == "dept_head"}
        roots = {unit for unit, value in units.items() if value.get("head_user_id") in heads}
        # A line manager sees their own unit and its descendants, not sibling units or a partial total for a parent.
        visible_unit_ids = {unit for unit in totals if roots.intersection(og.ancestors(units, unit))}
        # A role-based reviewer with no assigned unit may review unplaced staff; in that case allow whole-company
        # department aggregates, still composed only from reports allowed by _review_scope above.
        if by_role and not roots:
            visible_unit_ids = {unit for unit in totals}
        return {"week_start": monday.isoformat(), "week_end": sunday.isoformat(),
                "units": [{"unit_id": unit, "name": units[unit]["name"], **totals[unit]}
                          for unit in sorted(visible_unit_ids, key=lambda x: (units[x]["name"].casefold(), x))]}

    def _worktime_activity(self, conn, uid: str, now) -> dict:
        """Свои действия и таймеры за последние дни — из них программа сотрудника собирает дневной отчёт (D-43).

        actions — принятые команды сотрудника из журнала аудита (кроме самого отчёта, уведомлений и пароля) и входы
        в программу: время, operation_id (клиент подставит время, когда действие сделано без связи), подпись команды
        из каталога, объект, проект и задача (если объект проектный). timers — интервалы «в работе» операций нарядов
        по часам устройства (start/resume → pause/finish; незакрытый — end = null). Только свои записи; не больше
        ACTIVITY_LIMIT самых свежих действий.
        """
        since = now - timedelta(days=wt.AUTO_LOOKBACK_DAYS + 2)
        cat = self.contracts.catalog["commands"]
        objs = _activity_objects()
        cache: dict[tuple, tuple] = {}
        pcodes = {r.id: r.code for r in conn.execute(sa.select(projects.c.id, projects.c.code))}

        def resolve(otype, oid):
            """(проект, задача, название) объекта записи журнала; неизвестный тип — (None, None, None)."""
            key = (otype, oid)
            if key not in cache:
                spec = objs.get(otype or "")
                res = (None, None, None)
                if spec and oid:
                    table, pcol, name = spec
                    pk = list(table.primary_key.columns)[0]
                    row = conn.execute(sa.select(table).where(pk == oid)).first()
                    if row is not None:
                        res = (getattr(row, pcol) if pcol else None, oid if otype == "task" else None, name(row))
                cache[key] = res
            return cache[key]

        al = audit_log
        rows = conn.execute(sa.select(al.c.at, al.c.operation_id, al.c.command, al.c.object_type, al.c.object_id)
                            .where(al.c.actor_user_id == uid, al.c.at >= since, al.c.result == "accepted")
                            .order_by(al.c.at.desc(), al.c.id.desc()).limit(ACTIVITY_LIMIT * 2)).all()
        actions = []
        for r in rows:
            if r.command.startswith(ACTIVITY_SKIP_PREFIXES) or (r.command not in cat and r.command != "auth.login"):
                continue
            pid, tid, obj = resolve(r.object_type, r.object_id) if r.command != "auth.login" else (None, None, None)
            actions.append({"at": iso(r.at), "operation_id": r.operation_id, "command": r.command,
                            "label": "Вход в программу" if r.command == "auth.login" else cat[r.command].get("label_ru", r.command),
                            "object": obj, "project_id": pid, "project_code": pcodes.get(pid), "task_id": tid})
            if len(actions) >= ACTIVITY_LIMIT:
                break
        actions.reverse()

        te, op, wo = wo_timer_events, wo_operations, work_orders
        evs = conn.execute(sa.select(te.c.wo_operation_id, te.c.kind, te.c.device_time, op.c.name, wo.c.doc_no, wo.c.project_id)
                           .join(op, op.c.id == te.c.wo_operation_id).join(wo, wo.c.id == op.c.work_order_id)
                           .where(te.c.user_id == uid, te.c.device_time >= since)
                           .order_by(te.c.wo_operation_id, te.c.device_time, te.c.id)).all()
        timers, open_ = [], {}
        for e in evs:
            label = f"Наряд № {e.doc_no} · {e.name}"
            if e.kind in ("start", "resume") and e.wo_operation_id not in open_:
                open_[e.wo_operation_id] = {"start": iso(e.device_time), "end": None, "project_id": e.project_id,
                                            "project_code": pcodes.get(e.project_id), "label": label}
            elif e.kind in ("pause", "finish") and e.wo_operation_id in open_:
                it = open_.pop(e.wo_operation_id)
                it["end"] = iso(e.device_time)
                timers.append(it)
        timers += list(open_.values())
        return {"since": iso(since), "actions": actions, "timers": sorted(timers, key=lambda x: x["start"])}
