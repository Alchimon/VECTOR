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
from datetime import date, timedelta
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import worktime as wt
from vector.core.errors import Conflict, Denied, Rejected

from .authz import project_member
from . import db as _db
from .db import (audit_log, daily_report_auto, daily_report_events, daily_report_line_sources, daily_report_lines,
                 daily_reports, projects, tasks, wo_operations, wo_timer_events, work_orders)
from .validation import canon, iso, only_keys, req_uuid

# Подписи шагов журнала отчёта (история карточки).
DECISION_LABELS_RU = {"created": "Создан", "saved": "Изменён", "submitted": "Отправлен на проверку", "accepted": "Принят",
                      "returned": "Возвращён на исправление", "reopened": "Открыт для исправления"}
# За сколько дней (по дате рабочего дня) отчёты приходят в pull: свои и — руководителю отдела — отправленные чужие.
PULL_DAYS = 93
# Поля содержимого отчёта (что заменяет report.save).
CONTENT_KEYS = ("shift_start", "shift_end", "break_minutes", "lines", "comment")
# Действия, которые не считаются работой для собранного отчёта: сам отчёт, прочтение уведомлений, смена своего пароля.
ACTIVITY_SKIP_PREFIXES = ("report.", "notification.", "account.")
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

    def v_daily_report(self, conn, o: dict) -> dict:
        """Дневной отчёт для клиента: смена «ЧЧ:ММ», строки, итоги, статус, проверка и история."""
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
        daily_report_auto со сводкой summary ({actions, measured_minutes, lines} — целые) и уведомление автору
        «проверьте». Без origin — «Черновик», который сотрудник заполняет сам."""
        only_keys(p, {"id", "work_date", "origin", "summary", *CONTENT_KEYS})
        origin = p.get("origin") or "manual"
        if origin not in ("manual", "auto"):
            raise Rejected("invalid_origin", "Источник отчёта — «manual» или «auto».", {"field": "origin"})
        summary = p.get("summary") or {}
        if not isinstance(summary, dict) or set(summary) - {"actions", "measured_minutes", "lines"} or \
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
        подразделения по его цепочке или замещающий такого руководителя (D-44); вне оргструктуры — любой
        руководитель отдела (D-42)."""
        only_keys(p, {"id", "decision", "comment"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "report.review", ctx.now)
        o = self._require(ctx.conn, daily_reports, req_uuid(p, "id"), "Дневной отчёт")
        self._check_version(o, ev, lambda x: self.v_daily_report(ctx.conn, x))
        self._report_state(ctx.conn, o, "submitted")
        if o["user_id"] == ctx.user_id:
            raise Denied("four_eyes", "Свой дневной отчёт проверяет другой руководитель («четыре глаза»).")
        auth = self._review_authority(ctx.conn, ctx.user_id, o["user_id"], ctx.now) or auth
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
        """Свои отчёты за PULL_DAYS дней; руководителю отдела — ещё и отправленные (не черновики и не на самопроверке)
        чужие — сотрудников его подразделений и вне оргструктуры (D-44); проекты, где
        сотрудник участник (для строк отчёта); может ли он проверять отчёты."""
        since = (now - timedelta(days=PULL_DAYS)).date()
        dr = daily_reports
        reviewer = self.policy.can(conn, uid, "worktime.read_all", now)
        cond = dr.c.user_id == uid
        if reviewer:
            # Черновики и отчёты на самопроверке — личное дело автора, пока он их не отправил.
            cond = sa.or_(cond, dr.c.status.in_(("submitted", "accepted", "returned")))
        rows = list(conn.execute(sa.select(dr).where(cond, dr.c.work_date >= since).order_by(dr.c.work_date.desc(), dr.c.user_id)))
        if reviewer:
            # Оргструктура (D-44): чужие отчёты — только сотрудников своих подразделений (и вне оргструктуры).
            visible = self._review_scope(conn, uid, now)
            rows = [r for r in rows if r.user_id == uid or visible(r.user_id)]
        mine = [{"id": r.id, "code": r.code, "title": r.title}
                for r in conn.execute(sa.select(projects.c.id, projects.c.code, projects.c.title).order_by(projects.c.code))
                if project_member(conn, uid, r.id, now)]
        return {"daily_reports": [self.v_daily_report(conn, dict(r._mapping)) for r in rows],
                "worktime_projects": mine, "worktime_reviewer": reviewer,
                "worktime_activity": self._worktime_activity(conn, uid, now)}

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
