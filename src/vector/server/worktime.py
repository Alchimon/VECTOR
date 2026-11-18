"""Рабочее время и дневной отчёт (этап 7, R-20; decision log D-42).

Модуль — примесь (mixin) к `Service`, как quality.py: транзакция, идемпотентность по operation_id, аудит и проверка
версий — в service.py; здесь только правила домена (сами правила — в vector.core.worktime, общие с клиентом).

Поток работы:

    сотрудник (любой вошедший, только свой отчёт):
        report.create (можно без связи) — отчёт за рабочий день: смена, строки «на что ушло время» («Черновик»)
        report.save   (можно без связи) — правка черновика или возвращённого отчёта
        report.submit (можно без связи) — на проверку руководителю отдела («На проверке»)
        report.reopen (при связи)       — исправить отправленный или принятый отчёт: с основанием → «Черновик»
    руководитель отдела (dept_head, в т. ч. замещающий), НЕ автор:
        report.review (при связи) — «Принят» или «Возвращён» (причина обязательна)

Главные инварианты (каждый закреплён тестом в tests/server/test_worktime.py):

* один отчёт на сотрудника за день (уникальность в БД); чужой отчёт не правится и не отправляется;
* отправить можно только полный отчёт: смена указана, есть строки, часов в строках не больше смены без перерыва;
* проект строки — тот, где сотрудник сейчас участник (своё назначение или замещение в проекте); задача — этого
  проекта;
* принять или вернуть — только руководитель отдела и не свой отчёт («четыре глаза»); возврат — с причиной;
* исправление после отправки — только с основанием; каждый шаг — в неизменяемом журнале daily_report_events (кто,
  когда, на каком основании, подробности).

Отделов в программе пока нет (оргструктура — R-21): руководитель отдела видит и проверяет отчёты всех сотрудников
(временное правило A-48). Черновики чужих отчётов не видит никто.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import worktime as wt
from vector.core.errors import Conflict, Denied, Rejected

from .authz import project_member
from .db import daily_report_events, daily_report_lines, daily_reports, projects, tasks
from .validation import canon, iso, only_keys, req_uuid

# Подписи шагов журнала отчёта (история карточки).
DECISION_LABELS_RU = {"created": "Создан", "saved": "Изменён", "submitted": "Отправлен на проверку", "accepted": "Принят",
                      "returned": "Возвращён на исправление", "reopened": "Открыт для исправления"}
# За сколько дней (по дате рабочего дня) отчёты приходят в pull: свои и — руководителю отдела — отправленные чужие.
PULL_DAYS = 93
# Поля содержимого отчёта (что заменяет report.save).
CONTENT_KEYS = ("shift_start", "shift_end", "break_minutes", "lines", "comment")


def _w(fn, *a, **kw):
    """Вызвать правило vector.core.worktime; WorktimeError → Rejected (ошибка ввода, с полем и замечаниями)."""
    try:
        return fn(*a, **kw)
    except wt.WorktimeError as e:
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
            out.append({"seq": r.seq, "decision": r.decision, "label": DECISION_LABELS_RU.get(r.decision, r.decision),
                        "details": json.loads(r.details_json), "authority": r.authority, "by": r.decided_by,
                        "at": iso(r.decided_at), "on_behalf_of": b["on_behalf_of"] if b else None})
        return out

    # ------------------------------------------------------------ представление
    def _report_lines(self, conn, rid: str) -> list[dict]:
        """Строки отчёта по порядку — с кодом проекта и названием задачи (руководитель отдела может не видеть сам
        проект, но должен понимать, на что ушло время)."""
        rows = conn.execute(sa.select(daily_report_lines, projects.c.code.label("project_code"),
                                      projects.c.title.label("project_title"), tasks.c.title.label("task_title"))
                            .outerjoin(projects, projects.c.id == daily_report_lines.c.project_id)
                            .outerjoin(tasks, tasks.c.id == daily_report_lines.c.task_id)
                            .where(daily_report_lines.c.report_id == rid).order_by(daily_report_lines.c.line_no))
        return [{"line_no": r.line_no, "project_id": r.project_id, "project_code": r.project_code,
                 "project_title": r.project_title, "task_id": r.task_id, "task_title": r.task_title,
                 "minutes": r.minutes, "note": r.note} for r in rows]

    def v_daily_report(self, conn, o: dict) -> dict:
        """Дневной отчёт для клиента: смена «ЧЧ:ММ», строки, итоги, статус, проверка и история."""
        return {"id": o["id"], "user_id": o["user_id"], "work_date": o["work_date"].isoformat(), "status": o["status"],
                "status_label": wt.STATUS_LABELS_RU[o["status"]],
                "shift_start": wt.time_label(o["shift_start"]) or None, "shift_end": wt.time_label(o["shift_end"]) or None,
                "break_minutes": o["break_minutes"], "total_minutes": o["total_minutes"], "comment": o["comment"],
                "lines": self._report_lines(conn, o["id"]), "submitted_at": iso(o["submitted_at"]),
                "reviewed_by": o["reviewed_by"], "reviewed_at": iso(o["reviewed_at"]), "review_comment": o["review_comment"],
                "version": o["version"], "created_at": iso(o["created_at"]), "updated_at": iso(o["updated_at"]),
                "history": self._whistory(conn, o["id"])}

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
        for ln in lines:
            conn.execute(daily_report_lines.insert().values(report_id=rid, **ln))

    @staticmethod
    def _report_content(o: dict, lines: list[dict]) -> dict:
        """Содержимое отчёта в виде, который принимает vector.core.worktime.parse_report (для слияния с правкой)."""
        return {"shift_start": wt.time_label(o["shift_start"]) or None, "shift_end": wt.time_label(o["shift_end"]) or None,
                "break_minutes": o["break_minutes"], "comment": o["comment"],
                "lines": [{k: ln[k] for k in ("project_id", "task_id", "minutes", "note")} for ln in lines]}

    @staticmethod
    def _who_day(name: str, o: dict) -> str:
        """«Кузнецов Дмитрий, 17.11.2026, вт» — для текста уведомления."""
        return f"{name}, {wt.date_label(o['work_date'].isoformat())}"

    # ------------------------------------------------------------ команды автора
    def _report_create(self, ctx, p, ev):
        """report.create {id, work_date, shift_start?, shift_end?, break_minutes?, lines?, comment?} — свой отчёт за
        день («Черновик»). Один отчёт на сотрудника за день; день — не в будущем и не старше 31 дня."""
        only_keys(p, {"id", "work_date", *CONTENT_KEYS})
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
            id=rid, user_id=ctx.user_id, work_date=date.fromisoformat(day), status="draft",
            shift_start=c["shift_start"], shift_end=c["shift_end"], break_minutes=c["break_minutes"], total_minutes=total,
            comment=c["comment"], version=1, created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id,
            updated_at=ctx.now))
        self._write_report_lines(ctx.conn, rid, c["lines"])
        self._wdecide(ctx, rid, "created", {"work_date": day, "total_minutes": total, "lines": len(c["lines"])}, auth)
        ctx.log("daily_report", rid, auth, None, 1, {"event": "ReportCreated", "work_date": day, "total_minutes": total})
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
        """report.submit {id} — свой полный отчёт на проверку («На проверке»); уведомление руководителям отдела."""
        only_keys(p, {"id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "report.submit", ctx.now)
        o = self._own_report(ctx, req_uuid(p, "id"), ev)
        self._report_state(ctx.conn, o, *wt.EDITABLE)
        lines = self._report_lines(ctx.conn, o["id"])
        r = {"shift_start": o["shift_start"], "shift_end": o["shift_end"], "break_minutes": o["break_minutes"], "lines": lines}
        _w(wt.check_submit, r)
        t = wt.totals(r)
        n = self._update_versioned(ctx, daily_reports, o, {"status": "submitted", "submitted_at": ctx.now, "reviewed_by": None,
                                                           "reviewed_at": None, "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._wdecide(ctx, o["id"], "submitted", {"total_minutes": t["total"], "shift_net": t["net"],
                                                  "resubmitted": o["status"] == "returned"}, auth)
        ctx.log("daily_report", o["id"], auth, o["version"], n["version"], {"event": "ReportSubmitted", "total_minutes": t["total"]})
        who = self._who_day(self._user_name(ctx.conn, ctx.user_id), o)
        self._notify(ctx, self._users_with_role(ctx.conn, "dept_head", ctx.now), "report_submitted",
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
        «Возвращён» (причина обязательна). Уведомление автору."""
        only_keys(p, {"id", "decision", "comment"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "report.review", ctx.now)
        o = self._require(ctx.conn, daily_reports, req_uuid(p, "id"), "Дневной отчёт")
        self._check_version(o, ev, lambda x: self.v_daily_report(ctx.conn, x))
        self._report_state(ctx.conn, o, "submitted")
        if o["user_id"] == ctx.user_id:
            raise Denied("four_eyes", "Свой дневной отчёт проверяет другой руководитель («четыре глаза»).")
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
    def _pull_worktime(self, conn, uid: str, now) -> dict:
        """Свои отчёты за PULL_DAYS дней; руководителю отдела — ещё и отправленные (не черновики) чужие; проекты, где
        сотрудник участник (для строк отчёта); может ли он проверять отчёты."""
        since = (now - timedelta(days=PULL_DAYS)).date()
        dr = daily_reports
        reviewer = self.policy.can(conn, uid, "worktime.read_all", now)
        cond = dr.c.user_id == uid
        if reviewer:
            cond = sa.or_(cond, dr.c.status != "draft")
        rows = conn.execute(sa.select(dr).where(cond, dr.c.work_date >= since).order_by(dr.c.work_date.desc(), dr.c.user_id))
        mine = [{"id": r.id, "code": r.code, "title": r.title}
                for r in conn.execute(sa.select(projects.c.id, projects.c.code, projects.c.title).order_by(projects.c.code))
                if project_member(conn, uid, r.id, now)]
        return {"daily_reports": [self.v_daily_report(conn, dict(r._mapping)) for r in rows],
                "worktime_projects": mine, "worktime_reviewer": reviewer}
