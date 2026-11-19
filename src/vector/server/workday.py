"""Таймер рабочего дня (этап 7, R-20; decision log D-46 — ответы владельца на A-49 пп. 2–4; D-47 — на A-51).

Примесь (mixin) к `Service`, как worktime.py: транзакция, идемпотентность, аудит и проверка версий — в service.py;
правила таймера (переходы, время, согласование начала) — в vector.core.workday, общие с клиентом.

Команды:

    workday.mark          {id, work_date, event, device_time, auto?} — нажатие кнопки таймера: start (id — новый
                                                                    рабочий день) / pause / resume / finish; можно без
                                                                    связи (очередь): время — по часам устройства (A-49
                                                                    п. 5); auto — «Завершить» записала программа (забыли
                                                                    завершить день, D-47)
    workday.start_request {id, workday_id, start_at, reason}    — «я начал раньше»: фактическое начало дня — не раньше
                                                                    начала по графику работы (A-51 п. 1); очередь
    workday.start_decide  {id, decision (approve|reject), comment?} — решение руководителя (только при связи)
    workday.end_request   {id, workday_id, end_at, reason}      — «я работал дольше»: день завершён автоматически,
                                                                    фактическое окончание — на согласование (D-47);
                                                                    очередь
    workday.end_decide    {id, decision (approve|reject), comment?} — решение руководителя (только при связи)

Кто решает — тот же, кто проверяет дневные отчёты сотрудника (D-44, D-45): руководитель его подразделения по
цепочке вверх или замещающий руководителя по роли; сотрудник вне оргструктуры — руководитель отдела по роли (D-42).
Свой запрос — не решает («четыре глаза»). Согласовано — таймер считает день с фактического начала (approved_start_at):
начал в 8, нажал «Начать» в 10 — сразу видно 2 часа (A-49 п. 4). Согласованное фактическое окончание — последний
рабочий отрезок продлевается до него (approved_end_at — из последнего согласованного запроса).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import schedule as sch
from vector.core import workday as wd
from vector.core.errors import Conflict, Denied, Rejected

from .db import workday_auto_finishes, workday_end_requests, workday_events, workday_start_requests, workdays
from .validation import iso, only_keys, req_uuid

PULL_DAYS = 31                       # свои рабочие дни за столько дней (как дневные отчёты)
DEVICE_CLOCK_SKEW = timedelta(minutes=15)   # время устройства «из будущего» больше чем на 15 минут — отказ


def _d(fn, *a, **kw):
    """Вызвать правило vector.core.workday; WorkdayError → Rejected (ошибка ввода с полем)."""
    try:
        return fn(*a, **kw)
    except wd.WorkdayError as e:
        raise Rejected(e.code, e.message_ru, e.details)


class WorkdayMixin:
    """Команды workday.*, представления рабочего дня и запросов, часть pull."""

    # ------------------------------------------------------------ чтение
    def _wd_events(self, conn, workday_id: str) -> list[dict]:
        """Журнал нажатий дня по порядку записи; auto — «Завершить» записала программа (no_answer | closed, D-47)."""
        auto = {r.seq: r.reason for r in conn.execute(sa.select(workday_auto_finishes.c.seq, workday_auto_finishes.c.reason)
                                                      .where(workday_auto_finishes.c.workday_id == workday_id))}
        return [{"seq": r.seq, "event": r.event, "device_time": iso(r.device_time), "server_time": iso(r.server_time),
                 **({"auto": auto[r.seq]} if r.seq in auto else {})}
                for r in conn.execute(sa.select(workday_events).where(workday_events.c.workday_id == workday_id)
                                      .order_by(workday_events.c.seq))]

    def _wd_approved_end(self, conn, workday_id: str):
        """Согласованное фактическое окончание дня — из последнего согласованного запроса (или None)."""
        rq = workday_end_requests
        return conn.execute(sa.select(rq.c.requested_end_at).where(rq.c.workday_id == workday_id, rq.c.status == "approved")
                            .order_by(rq.c.decided_at.desc()).limit(1)).scalar()

    def v_workday(self, conn, o: dict) -> dict:
        """Рабочий день для клиента: дата, состояние, нажатия, согласованное фактическое начало, версия. Сколько
        «натикало» клиент считает сам (живой таймер) по тем же правилам vector.core.workday."""
        return {"id": o["id"], "user_id": o["user_id"], "work_date": o["work_date"].isoformat(), "status": o["status"],
                "started_at": iso(o["started_at"]), "finished_at": iso(o["finished_at"]),
                "approved_start_at": iso(o["approved_start_at"]),
                "approved_end_at": iso(self._wd_approved_end(conn, o["id"])), "events": self._wd_events(conn, o["id"]),
                "version": o["version"], "updated_at": iso(o["updated_at"])}

    def v_start_request(self, conn, o: dict, work_date: Optional[date] = None) -> dict:
        """Запрос «фактическое начало дня» (requested_start_at) или «фактическое окончание дня» (requested_end_at)
        для клиента — и автору, и руководителю; kind — start | end."""
        if work_date is None:
            work_date = conn.execute(sa.select(workdays.c.work_date).where(workdays.c.id == o["workday_id"])).scalar()
        kind = "end" if "requested_end_at" in o else "start"
        return {"id": o["id"], "workday_id": o["workday_id"], "user_id": o["user_id"], "kind": kind,
                "work_date": work_date.isoformat() if work_date else None,
                f"requested_{kind}_at": iso(o[f"requested_{kind}_at"]), "reason": o["reason"], "status": o["status"],
                "created_at": iso(o["created_at"]), "decided_by": o["decided_by"], "decided_at": iso(o["decided_at"]),
                "decision_comment": o["decision_comment"], "authority": o["authority"], "version": o["version"]}

    # ------------------------------------------------------------ workday.mark
    def _workday_mark(self, ctx, p, ev):
        """workday.mark {id, work_date, event, device_time} — нажатие кнопки таймера рабочего дня.

        start — новый рабочий день с id из клиента (один день на сотрудника и дату; дата — по часам компьютера, не
        дальше суток от времени события); остальное — по id дня, только своего. Переход и порядок времени —
        vector.core.workday.check_event; не по порядку — ВИДИМЫЙ конфликт в очереди клиента (сервер ничего не
        «додумывает»). Время из будущего (больше 15 минут) — отказ: проверьте часы компьютера.
        """
        only_keys(p, {"id", "work_date", "event", "device_time", "auto"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "workday.mark", ctx.now)
        wid = req_uuid(p, "id")
        event = p.get("event")
        at = _d(wd.parse_dt, p.get("device_time"))
        if at > ctx.now + DEVICE_CLOCK_SKEW:
            raise Rejected("device_time_future", "Время в будущем — проверьте часы компьютера и повторите.",
                           {"field": "device_time"})
        auto = p.get("auto")
        if auto is not None and (event != "finish" or auto not in ("no_answer", "closed")):
            raise Rejected("invalid_auto", "Автоматически программа только завершает день: auto — no_answer или closed.",
                           {"field": "auto"})
        if event == "start":
            try:
                day = date.fromisoformat(str(p.get("work_date") or ""))
            except ValueError:
                raise Rejected("invalid_date", "Дата рабочего дня — ГГГГ-ММ-ДД.", {"field": "work_date"})
            if abs((at.date() - day).days) > 1:
                raise Rejected("invalid_date", "Дата рабочего дня не совпадает со временем нажатия — проверьте часы "
                                               "компьютера.", {"field": "work_date"})
            if self._get(ctx.conn, workdays, wid) is not None:
                raise Conflict("already_started", "Этот рабочий день уже начат.",
                               {"current": self.v_workday(ctx.conn, self._get(ctx.conn, workdays, wid))})
            same = ctx.conn.execute(sa.select(workdays).where(workdays.c.user_id == ctx.user_id,
                                                              workdays.c.work_date == day)).first()
            if same is not None:
                raise Conflict("already_started", f"Рабочий день {day:%d.%m.%Y} уже начат (на другом компьютере?).",
                               {"current": self.v_workday(ctx.conn, dict(same._mapping))})
            ctx.conn.execute(workdays.insert().values(
                id=wid, user_id=ctx.user_id, work_date=day, status="working", started_at=at, finished_at=None,
                approved_start_at=None, version=1, created_at=ctx.now, updated_at=ctx.now))
            self._wd_event(ctx, wid, 1, event, at)
            n = self._get(ctx.conn, workdays, wid)
            ctx.log("workday", wid, auth, None, 1, {"event": event, "work_date": day.isoformat(), "device_time": iso(at)})
            return {"workday": self.v_workday(ctx.conn, n)}
        o = self._require(ctx.conn, workdays, wid, "Рабочий день")
        if o["user_id"] != ctx.user_id:
            raise Denied("not_owner", "Это рабочий день другого сотрудника.")
        events = self._wd_events(ctx.conn, wid)
        if auto and wd.status_of(events) == "finished":
            # Забытый день уже завершён (на другом компьютере или вручную) — автоматическому «Завершить» делать нечего:
            # принимается без изменений, чтобы в очереди не висел конфликт, который пользователю нечем решить.
            return {"workday": self.v_workday(ctx.conn, o)}
        try:
            wd.check_event(events, event, at)
        except wd.WorkdayError as e:
            if e.code == "invalid_event":
                raise Rejected(e.code, e.message_ru, e.details)
            raise Conflict(e.code, e.message_ru, {**e.details, "current": self.v_workday(ctx.conn, o)})
        self._wd_event(ctx, wid, len(events) + 1, event, at)
        if auto:
            ctx.conn.execute(workday_auto_finishes.insert().values(workday_id=wid, seq=len(events) + 1, reason=auto,
                                                                   created_at=ctx.now))
        st = wd.status_of(events + [{"seq": len(events) + 1, "event": event}])
        n = self._update_versioned(ctx, workdays, o, {"status": st, "updated_at": ctx.now,
                                                      "finished_at": at if event == "finish" else None})
        ctx.log("workday", wid, auth, o["version"], n["version"], {"event": event, "device_time": iso(at), "status": st,
                                                                   **({"auto": auto} if auto else {})})
        return {"workday": self.v_workday(ctx.conn, n)}

    def _wd_event(self, ctx, wid: str, seq: int, event: str, at) -> None:
        """Записать нажатие в неизменяемый журнал (время устройства и время приёма сервером)."""
        ctx.conn.execute(workday_events.insert().values(workday_id=wid, seq=seq, event=event, device_time=at,
                                                        server_time=ctx.now, device_id=ctx.device_id,
                                                        operation_id=ctx.operation_id))

    # ------------------------------------------------------------ фактическое начало дня (A-49 п. 4)
    def _workday_reviewers(self, conn, author_id: str, now) -> list[str]:
        """Кому решать о фактическом начале: тем же, кто проверяет дневные отчёты автора (D-44, D-45) — руководитель
        по оргструктуре и его замещающие; вне оргструктуры — руководители отделов по роли (D-42)."""
        to = self._report_reviewers(conn, author_id, now)
        return to if to is not None else [u for u in self._users_with_role(conn, "dept_head", now) if u != author_id]

    def _wd_schedule_start(self, conn, user_id: str, day: date, tz) -> tuple[Optional[Any], bool]:
        """Начало рабочего дня day по графику сотрудника (UTC) в поясе tz его компьютера и «учёт не ведётся».
        Праздник по производственному календарю (D-48) — день нерабочий: начала по графику нет."""
        eff = self._schedule_for(conn, user_id)
        win = sch.window(eff["schedule"], day, tz, self._calendar_around(conn, day))
        return (win[0] if win else None), eff["exempt"]

    def _workday_start_request(self, ctx, p, ev):
        """workday.start_request {id, workday_id, start_at, reason} — сотрудник пропустил «Начать рабочий день» и
        просит считать день с более раннего времени (A-49 п. 4). Только свой день, пока не прошли сутки от нажатия
        «Начать»; время — раньше нажатия, но не раньше начала рабочего дня по своему графику (D-47, ответ на A-51
        п. 1: «фактическое начало — это когда не отметили начало рабочего дня согласно графика работы»). Пояс — из
        start_at (часы компьютера сотрудника). Причина обязательна; один запрос на согласовании за день.
        Уведомление — руководителю (как для дневного отчёта)."""
        only_keys(p, {"id", "workday_id", "start_at", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "workday.start_request", ctx.now)
        rid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, workday_start_requests, rid)
        o = self._require(ctx.conn, workdays, req_uuid(p, "workday_id"), "Рабочий день")
        if o["user_id"] != ctx.user_id:
            raise Denied("not_owner", "Фактическое начало можно указать только для своего рабочего дня.")
        requested = _d(wd.parse_dt, p.get("start_at"), "start_at")
        reason = _d(wd.parse_reason, p.get("reason"))
        sched_start, exempt = self._wd_schedule_start(ctx.conn, ctx.user_id, o["work_date"], sch.tz_of(p.get("start_at")))
        _d(wd.check_start_request, o["started_at"], requested, ctx.now, sched_start, exempt)
        self._wd_request_insert(ctx, auth, workday_start_requests, rid, o, "start", requested, reason)
        return {"workday_start_request": self.v_start_request(ctx.conn, self._get(ctx.conn, workday_start_requests, rid),
                                                              o["work_date"])}

    def _workday_end_request(self, ctx, p, ev):
        """workday.end_request {id, workday_id, end_at, reason} — день завершён автоматически (сотрудник забыл
        «Завершить», напоминание осталось без ответа или программа была закрыта — D-47, A-51 п. 5), а работал он
        дольше: фактическое окончание — на согласование тому же руководителю, что и фактическое начало (A-51 п. 6).
        Правила — vector.core.workday.check_end_request (позже автоматического завершения, не в будущем, не дальше суток
        от начала; просить — в течение суток)."""
        only_keys(p, {"id", "workday_id", "end_at", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "workday.end_request", ctx.now)
        rid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, workday_end_requests, rid)
        o = self._require(ctx.conn, workdays, req_uuid(p, "workday_id"), "Рабочий день")
        if o["user_id"] != ctx.user_id:
            raise Denied("not_owner", "Фактическое окончание можно указать только для своего рабочего дня.")
        requested = _d(wd.parse_dt, p.get("end_at"), "end_at")
        reason = _d(wd.parse_reason, p.get("reason"))
        state = wd.day_state(self._wd_events(ctx.conn, o["id"]), ctx.now, o["approved_start_at"])
        _d(wd.check_end_request, state, requested, ctx.now)
        self._wd_request_insert(ctx, auth, workday_end_requests, rid, o, "end", requested, reason)
        return {"workday_end_request": self.v_start_request(ctx.conn, self._get(ctx.conn, workday_end_requests, rid),
                                                            o["work_date"])}

    def _wd_request_insert(self, ctx, auth, rq, rid: str, o: dict, kind: str, requested, reason: str) -> None:
        """Записать запрос (начало / окончание) «На согласовании» и уведомить руководителя. Второй запрос того же вида
        на согласовании за день — конфликт (дождитесь решения)."""
        what = "начало" if kind == "start" else "окончание"
        if ctx.conn.execute(sa.select(rq.c.id).where(rq.c.workday_id == o["id"], rq.c.status == "pending")).first():
            raise Conflict("already_pending", f"Запрос на фактическое {what} этого дня уже на согласовании — дождитесь "
                                              "решения руководителя.", {"current": self.v_workday(ctx.conn, o)})
        ctx.conn.execute(rq.insert().values(id=rid, workday_id=o["id"], user_id=ctx.user_id,
                                            **{f"requested_{kind}_at": requested}, reason=reason, status="pending",
                                            created_at=ctx.now, version=1))
        otype = f"workday_{kind}_request"
        ctx.log(otype, rid, auth, None, 1, {"workday_id": o["id"], f"{kind}_at": iso(requested)})
        who = self._user_name(ctx.conn, ctx.user_id)
        self._notify(ctx, self._workday_reviewers(ctx.conn, ctx.user_id, ctx.now), otype,
                     f"Фактическое {what} дня: {who}, {o['work_date']:%d.%m.%Y}",
                     body=f"Причина: {reason}", object_type=otype, object_id=rid)

    def _workday_start_decide(self, ctx, p, ev):
        """workday.start_decide {id, decision (approve|reject), comment?} — руководитель согласует или отклоняет
        фактическое начало дня сотрудника (только при связи, с версией запроса). Право — как на проверку дневного
        отчёта этого сотрудника (D-44, D-45); свой запрос — нет. Отклонить — только с причиной. Согласовано —
        approved_start_at дня: таймер сразу считает от фактического начала; уведомление автору."""
        return self._wd_decide(ctx, p, ev, "start")

    def _workday_end_decide(self, ctx, p, ev):
        """workday.end_decide {id, decision, comment?} — то же для фактического окончания дня (D-47, A-51 п. 6: решает
        тот же руководитель). Согласовано — последний рабочий отрезок дня продлевается до фактического окончания."""
        return self._wd_decide(ctx, p, ev, "end")

    def _wd_decide(self, ctx, p, ev, kind: str):
        """Общее решение по запросу «фактическое начало / окончание дня» (kind — start | end)."""
        only_keys(p, {"id", "decision", "comment"})
        action = f"workday.{kind}_decide"
        by_role = self.policy.can(ctx.conn, ctx.user_id, action, ctx.now)
        if not by_role and not self._is_org_head(ctx.conn, ctx.user_id):
            self.policy.check(ctx.conn, ctx.user_id, action, ctx.now)   # отказ по матрице прав
        auth = self.policy.check(ctx.conn, ctx.user_id, action, ctx.now) if by_role else None
        rq = workday_start_requests if kind == "start" else workday_end_requests
        what = "начало" if kind == "start" else "окончание"
        otype = f"workday_{kind}_request"
        o = self._require(ctx.conn, rq, req_uuid(p, "id"), f"Запрос на фактическое {what} дня")
        self._check_version(o, ev, lambda x: self.v_start_request(ctx.conn, x))
        if o["status"] != "pending":
            raise Conflict("already_decided", f"Запрос уже «{wd.REQUEST_LABELS_RU[o['status']]}».",
                           {"current": self.v_start_request(ctx.conn, o)})
        if o["user_id"] == ctx.user_id:
            raise Denied("four_eyes", f"Своё фактическое {what} дня согласует руководитель («четыре глаза»).")
        auth = self._review_authority(ctx.conn, ctx.user_id, o["user_id"], ctx.now) or auth
        if auth is None:
            raise Denied("not_your_unit", "Этот сотрудник не включён в оргструктуру — его запрос решает руководитель "
                                          "отдела (по роли).", {"author_id": o["user_id"]})
        dec = p.get("decision")
        if dec not in ("approve", "reject"):
            raise Rejected("invalid_decision", "Решение — «Согласовать» или «Отклонить».", {"field": "decision"})
        comment = _d(wd.parse_reason, p.get("comment"), dec == "reject", "comment", "причину отказа")
        st = "approved" if dec == "approve" else "rejected"
        n = self._update_versioned(ctx, rq, o, {"status": st, "decided_by": ctx.user_id, "decided_at": ctx.now,
                                                "decision_comment": comment, "authority": auth.describe()})
        self._record_basis(ctx, otype, o["id"], 1, auth)
        day = self._get(ctx.conn, workdays, o["workday_id"])
        if st == "approved":
            # Начало — в строке дня; окончание выводится из согласованного запроса — версия дня всё равно растёт,
            # чтобы клиент увидел изменение таймера.
            self._update_versioned(ctx, workdays, day, {"updated_at": ctx.now, **(
                {"approved_start_at": o["requested_start_at"]} if kind == "start" else {})})
        ctx.log(otype, o["id"], auth, o["version"], n["version"], {"decision": st, "comment": comment})
        self._notify(ctx, [o["user_id"]], f"workday_{kind}_decided",
                     f"Фактическое {what} дня {day['work_date']:%d.%m.%Y}: {wd.REQUEST_LABELS_RU[st].lower()}",
                     body=comment, object_type=otype, object_id=o["id"])
        return {otype: self.v_start_request(ctx.conn, n, day["work_date"])}

    # ------------------------------------------------------------ pull
    def _pull_workday(self, conn, uid: str, now) -> dict[str, Any]:
        """Часть pull: свои рабочие дни за PULL_DAYS дней (workdays) и запросы на фактическое начало и окончание
        (workday_start_requests, workday_end_requests) — свои и, проверяющему, запросы сотрудников, которых он вправе
        решать (та же область, что у дневных отчётов: D-44, D-45, D-42). Одним списком, как daily_reports: клиент
        различает по user_id."""
        since = (now - timedelta(days=PULL_DAYS)).date()
        days = [self.v_workday(conn, dict(r._mapping)) for r in conn.execute(
            sa.select(workdays).where(workdays.c.user_id == uid, workdays.c.work_date >= since)
            .order_by(workdays.c.work_date.desc()))]
        by_role = self.policy.can(conn, uid, "workday.start_decide", now)
        visible = self._review_scope(conn, uid, now, by_role) if by_role or self._is_org_head(conn, uid) else None
        out: dict[str, Any] = {"workdays": days}
        for key, rq in (("workday_start_requests", workday_start_requests), ("workday_end_requests", workday_end_requests)):
            rows = conn.execute(sa.select(rq, workdays.c.work_date).join(workdays, workdays.c.id == rq.c.workday_id)
                                .where(workdays.c.work_date >= since).order_by(rq.c.created_at.desc())).fetchall()
            out[key] = [self.v_start_request(conn, {k: v for k, v in r._mapping.items() if k != "work_date"}, r.work_date)
                        for r in rows if r.user_id == uid or (visible is not None and visible(r.user_id))]
        return out
