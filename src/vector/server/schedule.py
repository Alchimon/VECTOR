"""Графики работы (этап 7, R-20; decision log D-47 — ответы владельца на A-51).

Примесь (mixin) к `Service`, как org.py: транзакция, идемпотентность, аудит и проверка версий — в service.py; правила
графика (время, рабочие дни, какой график действует для сотрудника) — в vector.core.schedule, общие с клиентом.

Команды (только при связи — меняют справочник компании; ведут администраторы, как оргструктуру — A-50 п. 1):

    schedule.save   {id, name, start_time, end_time, break_minutes, weekdays, is_default?, active?, use_calendar?}
                    — создать график (новый id) или изменить (с ожидаемой версией); active = false — в архив;
                      use_calendar — учитывать производственный календарь (D-48; по умолчанию — да)
    schedule.assign {target_kind (unit|user), target_id, schedule_id?, exempt?}
                    — назначить график подразделению или сотруднику; пусто — снять (действует график выше по
                      оргструктуре или «по умолчанию»); exempt — «учёт рабочего времени не ведётся» (только сотруднику)

Зачем график: от него считается фактическое начало дня (не раньше начала по графику — A-51 п. 1), напоминание о конце
дня и автоматическое завершение забытого дня (A-51 п. 5) — см. workday.py и vector.core.workday.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import schedule as sch
from vector.core.errors import Rejected

from .db import org_members, org_units, users, work_schedule_calendar, work_schedule_links, work_schedules
from .validation import iso, only_keys, req_uuid


def _s(fn, *a, **kw):
    """Вызвать правило vector.core.schedule; ScheduleError → Rejected (ошибка ввода с полем формы)."""
    try:
        return fn(*a, **kw)
    except sch.ScheduleError as e:
        raise Rejected(e.code, e.message_ru, e.details)


class ScheduleMixin:
    """Команды schedule.*, представления графиков и назначений, «мой график» в pull."""

    # ------------------------------------------------------------ чтение
    @staticmethod
    def v_schedule(o: dict) -> dict:
        """График для клиента: поля, подпись правила («08:00–17:00, пн–пт, перерыв 60 мин»), версия."""
        return {"id": o["id"], "name": o["name"], "start_time": o["start_time"], "end_time": o["end_time"],
                "break_minutes": o["break_minutes"], "weekdays": o["weekdays"], "is_default": bool(o["is_default"]),
                "active": bool(o["active"]), "use_calendar": sch.uses_calendar(o), "label": sch.label(o),
                "version": o["version"],
                "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_schedule_link(o: dict) -> dict:
        """Назначение графика для клиента."""
        return {"id": o["id"], "target_kind": o["target_kind"], "target_id": o["target_id"],
                "schedule_id": o["schedule_id"], "exempt": bool(o["exempt"]), "version": o["version"],
                "updated_at": iso(o["updated_at"])}

    def _schedules(self, conn) -> list[dict]:
        """Все графики (с архивными), по названию; use_calendar — учитывает ли график производственный календарь
        (отдельная таблица work_schedule_calendar, D-48: строки нет — учитывает)."""
        cal = {r.schedule_id: bool(r.use_calendar) for r in conn.execute(sa.select(work_schedule_calendar))}
        return [{**dict(r._mapping), "use_calendar": cal.get(r.id, True)}
                for r in conn.execute(sa.select(work_schedules).order_by(work_schedules.c.name))]

    def _schedule_get(self, conn, sid: str) -> Optional[dict]:
        """Один график с отметкой use_calendar (или None)."""
        return next((s for s in self._schedules(conn) if s["id"] == sid), None)

    def _schedule_set_calendar(self, conn, sid: str, use: bool, now) -> None:
        """Записать отметку «учитывать производственный календарь» (одна строка на график)."""
        t = work_schedule_calendar
        if conn.execute(t.update().where(t.c.schedule_id == sid).values(use_calendar=use, updated_at=now)).rowcount == 0:
            conn.execute(t.insert().values(schedule_id=sid, use_calendar=use, updated_at=now))

    def _schedule_links(self, conn) -> list[dict]:
        """Все назначения графиков."""
        return [dict(r._mapping) for r in conn.execute(sa.select(work_schedule_links))]

    def _schedule_for(self, conn, user_id: str) -> dict[str, Any]:
        """Какой график действует для сотрудника (vector.core.schedule.resolve): свой → подразделения (по цепочке
        вверх) → «по умолчанию» → встроенный."""
        units = {r.id: (r.parent_id, r.name) for r in conn.execute(sa.select(org_units.c.id, org_units.c.parent_id,
                                                                               org_units.c.name))}
        unit = conn.execute(sa.select(org_members.c.unit_id).where(org_members.c.user_id == user_id)).scalar()
        return sch.resolve(user_id, unit, {k: v[0] for k, v in units.items()}, self._schedule_links(conn),
                           self._schedules(conn), {k: v[1] for k, v in units.items()})

    # ------------------------------------------------------------ schedule.save
    def _schedule_save(self, ctx, p, ev):
        """schedule.save {id, name, start_time, end_time, break_minutes, weekdays, is_default?, active?,
        use_calendar?} — создать или изменить график работы (администратор, при связи). «По умолчанию» в компании —
        один график: отметка снимается с прежнего. В архив нельзя график «по умолчанию» (сначала отметьте другой).
        use_calendar (D-48): учитывать производственный календарь; не передано — у нового графика «да», у
        существующего — как было."""
        only_keys(p, {"id", "name", "start_time", "end_time", "break_minutes", "weekdays", "is_default", "active",
                      "use_calendar"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "schedule.save", ctx.now)
        sid = req_uuid(p, "id")
        f = _s(sch.check_schedule, p)
        is_default = p.get("is_default", False)
        active = p.get("active", True)
        use_cal = p.get("use_calendar")
        for k, v in (("is_default", is_default), ("active", active)) + ((("use_calendar", use_cal),) if use_cal is not None else ()):
            if not isinstance(v, bool):
                raise Rejected("invalid_value", "Ожидается да или нет.", {"field": k})
        if is_default and not active:
            raise Rejected("default_archived", "График «по умолчанию» нельзя отправить в архив — сначала отметьте "
                                               "«по умолчанию» другой график.", {"field": "active"})
        # Название не повторяется без учёта регистра; сравнение — в Python: lower() в SQLite не знает кириллицу.
        if any(r.name.casefold() == f["name"].casefold() for r in ctx.conn.execute(
                sa.select(work_schedules.c.name).where(work_schedules.c.id != sid))):
            raise Rejected("name_taken", f"График «{f['name']}» уже есть — выберите другое название.", {"field": "name"})
        o = self._get(ctx.conn, work_schedules, sid)
        if is_default:
            # Прежний «по умолчанию» перестаёт им быть (с увеличением версии — у кого открыта форма, увидит конфликт).
            for r in ctx.conn.execute(sa.select(work_schedules).where(work_schedules.c.is_default == sa.true(),
                                                                      work_schedules.c.id != sid)).fetchall():
                self._update_versioned(ctx, work_schedules, dict(r._mapping), {"is_default": False, "updated_at": ctx.now})
        if o is None:
            ctx.conn.execute(work_schedules.insert().values(id=sid, **f, is_default=is_default, active=active,
                                                            version=1, created_at=ctx.now, updated_at=ctx.now))
            self._schedule_set_calendar(ctx.conn, sid, True if use_cal is None else use_cal, ctx.now)
            n = self._schedule_get(ctx.conn, sid)
            ctx.log("work_schedule", sid, auth, None, 1, {"event": "WorkScheduleCreated", **f, "is_default": is_default,
                                                         "use_calendar": n["use_calendar"]})
        else:
            self._check_version(o, ev, self.v_schedule)
            if o["is_default"] and not is_default and not any(
                    r.is_default for r in ctx.conn.execute(sa.select(work_schedules.c.is_default)
                                                           .where(work_schedules.c.id != sid))):
                # Снять «по умолчанию» можно, только отметив другой — иначе молча включится встроенный 09:00–18:00.
                raise Rejected("default_required", "Чтобы снять «по умолчанию», отметьте «по умолчанию» другой "
                                                   "график.", {"field": "is_default"})
            n = self._update_versioned(ctx, work_schedules, o, {**f, "is_default": is_default, "active": active,
                                                                "updated_at": ctx.now})
            if use_cal is not None:
                self._schedule_set_calendar(ctx.conn, sid, use_cal, ctx.now)
            n = self._schedule_get(ctx.conn, sid)
            ctx.log("work_schedule", sid, auth, o["version"], n["version"],
                    {"event": "WorkScheduleUpdated", **f, "is_default": is_default, "active": active,
                     "use_calendar": n["use_calendar"]})
        return {"work_schedule": self.v_schedule(n)}

    # ------------------------------------------------------------ schedule.assign
    def _schedule_assign(self, ctx, p, ev):
        """schedule.assign {target_kind, target_id, schedule_id?, exempt?} — назначить график подразделению или
        сотруднику (администратор, при связи). schedule_id пусто и exempt нет — снять назначение. exempt — только
        сотруднику: «учёт рабочего времени не ведётся» (служебная учётная запись — окно «Начните рабочий день» не
        показывается). Сотрудник получает уведомление о своём графике."""
        only_keys(p, {"target_kind", "target_id", "schedule_id", "exempt"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "schedule.assign", ctx.now)
        kind = p.get("target_kind")
        if kind not in ("unit", "user"):
            raise Rejected("invalid_target", "Кому назначить: подразделению (unit) или сотруднику (user).",
                           {"field": "target_kind"})
        tid = req_uuid(p, "target_id")
        self._require(ctx.conn, org_units if kind == "unit" else users, tid,
                               "Подразделение" if kind == "unit" else "Сотрудник")
        exempt = p.get("exempt", False)
        if not isinstance(exempt, bool):
            raise Rejected("invalid_value", "Ожидается да или нет.", {"field": "exempt"})
        if exempt and kind != "user":
            raise Rejected("exempt_user_only", "«Учёт рабочего времени не ведётся» отмечается только сотруднику.",
                           {"field": "exempt"})
        sid = p.get("schedule_id") or None
        sched: Optional[dict] = None
        if sid is not None:
            sid = req_uuid(p, "schedule_id")
            sched = self._require(ctx.conn, work_schedules, sid, "График работы")
            if not sched["active"]:
                raise Rejected("archived", f"График «{sched['name']}» в архиве — выберите действующий.",
                               {"field": "schedule_id"})
        if exempt:
            sid = None                                   # учёт не ведётся — график не нужен
        lk = work_schedule_links
        cur = ctx.conn.execute(sa.select(lk).where(lk.c.target_kind == kind, lk.c.target_id == tid)).first()
        cur = dict(cur._mapping) if cur else None
        if sid is None and not exempt:
            if cur is not None:
                ctx.conn.execute(lk.delete().where(lk.c.id == cur["id"]))
                ctx.log("work_schedule_link", cur["id"], auth, cur["version"], None,
                        {"event": "WorkScheduleUnassigned", "target_kind": kind, "target_id": tid})
            link = None
        elif cur is None:
            lid = str(uuid.uuid4())
            ctx.conn.execute(lk.insert().values(id=lid, target_kind=kind, target_id=tid, schedule_id=sid, exempt=exempt,
                                                version=1, created_at=ctx.now, updated_at=ctx.now))
            link = self._get(ctx.conn, lk, lid)
            ctx.log("work_schedule_link", lid, auth, None, 1, {"event": "WorkScheduleAssigned", "target_kind": kind,
                                                               "target_id": tid, "schedule_id": sid, "exempt": exempt})
        else:
            if cur["schedule_id"] == sid and bool(cur["exempt"]) == exempt:
                return {"work_schedule_link": self.v_schedule_link(cur)}
            link = self._update_versioned(ctx, lk, cur, {"schedule_id": sid, "exempt": exempt, "updated_at": ctx.now})
            ctx.log("work_schedule_link", cur["id"], auth, cur["version"], link["version"],
                    {"event": "WorkScheduleAssigned", "target_kind": kind, "target_id": tid, "schedule_id": sid,
                     "exempt": exempt})
        if kind == "user":
            eff = self._schedule_for(ctx.conn, tid)
            self._notify(ctx, [tid], "work_schedule", "Ваш график работы: " + sch.label(eff["schedule"]),
                         body=f"График {eff['source_label']}. От него считаются фактическое начало дня и напоминание "
                              "о конце дня.", object_type="work_schedule", object_id=sid)
        return {"work_schedule_link": self.v_schedule_link(link) if link else None}

    # ------------------------------------------------------------ pull
    def _pull_schedule(self, conn, uid: str, now) -> dict[str, Any]:
        """Часть pull: графики (справочник компании — всем вошедшим), назначения, мой действующий график
        (workday_schedule: график, откуда он, учёт не ведётся) — по нему клиент без связи считает окно «Начните
        рабочий день», напоминание о конце дня и границу фактического начала."""
        eff = self._schedule_for(conn, uid)
        s = eff["schedule"]
        return {"work_schedules": [self.v_schedule(o) for o in self._schedules(conn)],
                "work_schedule_links": [self.v_schedule_link(o) for o in self._schedule_links(conn)],
                "workday_schedule": {"schedule": ({**s, "label": sch.label(s)} if s and s.get("builtin")
                                                  else (self.v_schedule(s) if s else None)),
                                     "exempt": eff["exempt"], "source": eff["source"],
                                     "source_label": eff["source_label"], "label": sch.full_label(s),
                                     "manage": self.policy.can(conn, uid, "schedule.manage", now)}}
