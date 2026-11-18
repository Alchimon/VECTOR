"""Таймер рабочего дня (этап 7, R-20; decision log D-46 — ответы владельца на A-49 пп. 2–4).

Примесь (mixin) к `Service`, как worktime.py: транзакция, идемпотентность, аудит и проверка версий — в service.py;
правила таймера (переходы, время, согласование начала) — в vector.core.workday, общие с клиентом.

Команды:

    workday.mark          {id, work_date, event, device_time}   — нажатие кнопки таймера: start (id — новый рабочий
                                                                    день) / pause / resume / finish; можно без связи
                                                                    (очередь): время — по часам устройства (A-49 п. 5)
    workday.start_request {id, workday_id, start_at, reason}    — «я начал раньше»: фактическое начало сегодняшнего
                                                                    дня на согласование руководителю (A-49 п. 4);
                                                                    можно без связи (очередь)
    workday.start_decide  {id, decision (approve|reject), comment?} — решение руководителя (только при связи)

Кто решает — тот же, кто проверяет дневные отчёты сотрудника (D-44, D-45): руководитель его подразделения по
цепочке вверх или замещающий руководителя по роли; сотрудник вне оргструктуры — руководитель отдела по роли (D-42).
Свой запрос — не решает («четыре глаза»). Согласовано — таймер считает день с фактического начала (approved_start_at):
начал в 8, нажал «Начать» в 10 — сразу видно 2 часа (A-49 п. 4).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import workday as wd
from vector.core.errors import Conflict, Denied, Rejected

from .db import workday_events, workday_start_requests, workdays
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
        """Журнал нажатий дня по порядку записи."""
        return [{"seq": r.seq, "event": r.event, "device_time": iso(r.device_time), "server_time": iso(r.server_time)}
                for r in conn.execute(sa.select(workday_events).where(workday_events.c.workday_id == workday_id)
                                      .order_by(workday_events.c.seq))]

    def v_workday(self, conn, o: dict) -> dict:
        """Рабочий день для клиента: дата, состояние, нажатия, согласованное фактическое начало, версия. Сколько
        «натикало» клиент считает сам (живой таймер) по тем же правилам vector.core.workday."""
        return {"id": o["id"], "user_id": o["user_id"], "work_date": o["work_date"].isoformat(), "status": o["status"],
                "started_at": iso(o["started_at"]), "finished_at": iso(o["finished_at"]),
                "approved_start_at": iso(o["approved_start_at"]), "events": self._wd_events(conn, o["id"]),
                "version": o["version"], "updated_at": iso(o["updated_at"])}

    def v_start_request(self, conn, o: dict, work_date: Optional[date] = None) -> dict:
        """Запрос «фактическое начало дня» для клиента (и автору, и руководителю)."""
        if work_date is None:
            work_date = conn.execute(sa.select(workdays.c.work_date).where(workdays.c.id == o["workday_id"])).scalar()
        return {"id": o["id"], "workday_id": o["workday_id"], "user_id": o["user_id"],
                "work_date": work_date.isoformat() if work_date else None,
                "requested_start_at": iso(o["requested_start_at"]), "reason": o["reason"], "status": o["status"],
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
        only_keys(p, {"id", "work_date", "event", "device_time"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "workday.mark", ctx.now)
        wid = req_uuid(p, "id")
        event = p.get("event")
        at = _d(wd.parse_dt, p.get("device_time"))
        if at > ctx.now + DEVICE_CLOCK_SKEW:
            raise Rejected("device_time_future", "Время в будущем — проверьте часы компьютера и повторите.",
                           {"field": "device_time"})
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
        try:
            wd.check_event(events, event, at)
        except wd.WorkdayError as e:
            if e.code == "invalid_event":
                raise Rejected(e.code, e.message_ru, e.details)
            raise Conflict(e.code, e.message_ru, {**e.details, "current": self.v_workday(ctx.conn, o)})
        self._wd_event(ctx, wid, len(events) + 1, event, at)
        st = wd.status_of(events + [{"seq": len(events) + 1, "event": event}])
        n = self._update_versioned(ctx, workdays, o, {"status": st, "updated_at": ctx.now,
                                                      "finished_at": at if event == "finish" else None})
        ctx.log("workday", wid, auth, o["version"], n["version"], {"event": event, "device_time": iso(at), "status": st})
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

    def _workday_start_request(self, ctx, p, ev):
        """workday.start_request {id, workday_id, start_at, reason} — сотрудник пропустил «Начать рабочий день» и
        просит считать день с более раннего времени (A-49 п. 4). Только свой день, пока не прошли сутки от его
        начала; время — раньше нажатия «Начать», не раньше чем за 16 часов; причина обязательна. Один запрос на
        согласовании за день. Уведомление — руководителю (как для дневного отчёта)."""
        only_keys(p, {"id", "workday_id", "start_at", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "workday.start_request", ctx.now)
        rid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, workday_start_requests, rid)
        o = self._require(ctx.conn, workdays, req_uuid(p, "workday_id"), "Рабочий день")
        if o["user_id"] != ctx.user_id:
            raise Denied("not_owner", "Фактическое начало можно указать только для своего рабочего дня.")
        requested = _d(wd.parse_dt, p.get("start_at"), "start_at")
        reason = _d(wd.parse_reason, p.get("reason"))
        _d(wd.check_start_request, o["started_at"], requested, ctx.now)
        rq = workday_start_requests
        if ctx.conn.execute(sa.select(rq.c.id).where(rq.c.workday_id == o["id"], rq.c.status == "pending")).first():
            raise Conflict("already_pending", "Запрос на фактическое начало этого дня уже на согласовании — дождитесь "
                                              "решения руководителя.", {"current": self.v_workday(ctx.conn, o)})
        ctx.conn.execute(rq.insert().values(id=rid, workday_id=o["id"], user_id=ctx.user_id,
                                            requested_start_at=requested, reason=reason, status="pending",
                                            created_at=ctx.now, version=1))
        n = self._get(ctx.conn, rq, rid)
        ctx.log("workday_start_request", rid, auth, None, 1, {"workday_id": o["id"], "start_at": iso(requested)})
        who = self._user_name(ctx.conn, ctx.user_id)
        self._notify(ctx, self._workday_reviewers(ctx.conn, ctx.user_id, ctx.now), "workday_start_request",
                     f"Фактическое начало дня: {who}, {o['work_date']:%d.%m.%Y}",
                     body=f"Причина: {reason}", object_type="workday_start_request", object_id=rid)
        return {"workday_start_request": self.v_start_request(ctx.conn, n, o["work_date"])}

    def _workday_start_decide(self, ctx, p, ev):
        """workday.start_decide {id, decision (approve|reject), comment?} — руководитель согласует или отклоняет
        фактическое начало дня сотрудника (только при связи, с версией запроса). Право — как на проверку дневного
        отчёта этого сотрудника (D-44, D-45); свой запрос — нет. Отклонить — только с причиной. Согласовано —
        approved_start_at дня: таймер сразу считает от фактического начала; уведомление автору."""
        only_keys(p, {"id", "decision", "comment"})
        by_role = self.policy.can(ctx.conn, ctx.user_id, "workday.start_decide", ctx.now)
        if not by_role and not self._is_org_head(ctx.conn, ctx.user_id):
            self.policy.check(ctx.conn, ctx.user_id, "workday.start_decide", ctx.now)   # отказ по матрице прав
        auth = self.policy.check(ctx.conn, ctx.user_id, "workday.start_decide", ctx.now) if by_role else None
        rq = workday_start_requests
        o = self._require(ctx.conn, rq, req_uuid(p, "id"), "Запрос на фактическое начало дня")
        self._check_version(o, ev, lambda x: self.v_start_request(ctx.conn, x))
        if o["status"] != "pending":
            raise Conflict("already_decided", f"Запрос уже «{wd.REQUEST_LABELS_RU[o['status']]}».",
                           {"current": self.v_start_request(ctx.conn, o)})
        if o["user_id"] == ctx.user_id:
            raise Denied("four_eyes", "Своё фактическое начало дня согласует руководитель («четыре глаза»).")
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
        self._record_basis(ctx, "workday_start_request", o["id"], 1, auth)
        day = self._get(ctx.conn, workdays, o["workday_id"])
        if st == "approved":
            self._update_versioned(ctx, workdays, day, {"approved_start_at": o["requested_start_at"], "updated_at": ctx.now})
        ctx.log("workday_start_request", o["id"], auth, o["version"], n["version"], {"decision": st, "comment": comment})
        self._notify(ctx, [o["user_id"]], "workday_start_decided",
                     f"Фактическое начало дня {day['work_date']:%d.%m.%Y}: {wd.REQUEST_LABELS_RU[st].lower()}",
                     body=comment, object_type="workday_start_request", object_id=o["id"])
        return {"workday_start_request": self.v_start_request(ctx.conn, n, day["work_date"])}

    # ------------------------------------------------------------ pull
    def _pull_workday(self, conn, uid: str, now) -> dict[str, Any]:
        """Часть pull: свои рабочие дни за PULL_DAYS дней (workdays) и запросы на фактическое начало
        (workday_start_requests) — свои и, проверяющему, запросы сотрудников, которых он вправе решать (та же область,
        что у дневных отчётов: D-44, D-45, D-42). Одним списком, как daily_reports: клиент различает по user_id."""
        since = (now - timedelta(days=PULL_DAYS)).date()
        days = [self.v_workday(conn, dict(r._mapping)) for r in conn.execute(
            sa.select(workdays).where(workdays.c.user_id == uid, workdays.c.work_date >= since)
            .order_by(workdays.c.work_date.desc()))]
        rq = workday_start_requests
        rows = conn.execute(sa.select(rq, workdays.c.work_date).join(workdays, workdays.c.id == rq.c.workday_id)
                            .where(workdays.c.work_date >= since).order_by(rq.c.created_at.desc())).fetchall()
        by_role = self.policy.can(conn, uid, "workday.start_decide", now)
        visible = self._review_scope(conn, uid, now, by_role) if by_role or self._is_org_head(conn, uid) else None
        reqs = [self.v_start_request(conn, dict(r._mapping), r.work_date) for r in rows
                if r.user_id == uid or (visible is not None and visible(r.user_id))]
        return {"workdays": days, "workday_start_requests": reqs}
