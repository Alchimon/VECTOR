"""Производственный календарь (этап 7, R-20; decision log D-48 — ответ владельца на A-52 п. 3).

Примесь (mixin) к `Service`, как schedule.py: транзакция, идемпотентность и аудит — в service.py; разбор и проверка
календаря, действующий календарь (загруженный → встроенный → ручные правки) — в vector.core.prodcal, общие с
клиентом.

Команды (только при связи — меняют справочник компании; ведут администраторы, как графики работы — D-47):

    calendar.import  {year, xml, source?}   — загрузить календарь на год (XML xmlcalendar.ru): из интернета (скачивает
                                              программа администратора — серверу интернет не нужен) или из файла;
                                              повторная загрузка года заменяет его дни, ручные правки остаются
    calendar.day_set {date, kind?, title?}  — ручная правка дня (региональный праздник, решение компании); пусто —
                                              убрать правку (день — как в календаре)

Зачем календарь: в праздник окно «Начните рабочий день» не появляется, в рабочую субботу по переносу — появляется, в
предпраздничный день конец по графику (а значит, напоминание и автоматическое завершение дня) на час раньше
(vector.core.schedule.window) — у графиков, которые учитывают календарь (отметка в графике, по умолчанию — да).
"""
from __future__ import annotations

from datetime import date
from typing import Any

import sqlalchemy as sa

from vector.core import prodcal as pc
from vector.core.errors import Rejected

from .db import work_calendar_days, work_calendar_overrides, work_calendar_years
from .validation import iso, only_keys

MAX_SOURCE = 200


def _c(fn, *a, **kw):
    """Вызвать правило vector.core.prodcal; CalendarError → Rejected (ошибка ввода с полем формы)."""
    try:
        return fn(*a, **kw)
    except pc.CalendarError as e:
        raise Rejected(e.code, e.message_ru, e.details)


class CalendarMixin:
    """Команды calendar.*, действующий календарь для графиков, производственный календарь в pull."""

    # ------------------------------------------------------------ чтение
    def _calendar_loaded(self, conn) -> dict[int, dict]:
        """Загруженные в базу годы: {год: {source, source_date, days_count, loaded_by, version, updated_at}}."""
        return {r.year: dict(r._mapping) for r in conn.execute(sa.select(work_calendar_years))}

    def _calendar(self, conn, years) -> dict[str, dict]:
        """Действующий календарь на годы years ({ГГГГ-ММ-ДД: {kind, title, origin}}): загруженный в базу год, иначе
        встроенный в программу; поверх — ручные правки администратора (vector.core.prodcal.effective)."""
        ys = sorted(set(years))
        loaded = {y: x for y, x in self._calendar_loaded(conn).items() if y in ys}
        imported = [{"date": r.day, "kind": r.kind, "title": r.title} for r in conn.execute(
            sa.select(work_calendar_days).where(work_calendar_days.c.year.in_(ys)))] if loaded else []
        manual = [{"date": r.day, "kind": r.kind, "title": r.title}
                  for r in conn.execute(sa.select(work_calendar_overrides))]
        return pc.effective(ys, loaded, imported, manual)

    def _calendar_around(self, conn, day: date) -> dict[str, dict]:
        """Календарь на год дня day и соседние (ночная смена 31 декабря, запрос о вчерашнем дне 1 января)."""
        return self._calendar(conn, (day.year - 1, day.year, day.year + 1))

    def v_calendar_override(self, o: dict) -> dict:
        """Ручная правка дня для клиента."""
        return {"date": o["day"], "kind": o["kind"], "kind_label": pc.KIND_LABELS_RU.get(o["kind"], o["kind"]),
                "title": o["title"], "set_by": o["set_by"], "version": o["version"], "updated_at": iso(o["updated_at"])}

    # ------------------------------------------------------------ calendar.import
    def _calendar_import(self, ctx, p, ev):
        """calendar.import {year, xml, source?} — загрузить календарь на год (администратор, при связи). XML проверяется
        (vector.core.prodcal.parse_xml: размер, без DOCTYPE / ENTITY, год совпадает, дни внутри года). Дни года
        заменяются целиком; ручные правки дней остаются (они в отдельной таблице и важнее загруженного)."""
        only_keys(p, {"year", "xml", "source"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "calendar.import", ctx.now)
        year = _c(pc.parse_year, p.get("year"))
        cal = _c(pc.parse_xml, p.get("xml"), year)
        source = p.get("source")
        if source is not None and not isinstance(source, str):
            raise Rejected("invalid_text", "Источник — текстом.", {"field": "source"})
        source = ((source or "").strip() or "файл")[:MAX_SOURCE]
        cur = self._get(ctx.conn, work_calendar_years, year, "year")
        ctx.conn.execute(work_calendar_days.delete().where(work_calendar_days.c.year == year))
        values = {"source": source, "source_date": cal["source_date"], "days_count": len(cal["days"]),
                  "loaded_by": ctx.user_id, "updated_at": ctx.now}
        if cur is None:
            ctx.conn.execute(work_calendar_years.insert().values(year=year, version=1, created_at=ctx.now, **values))
        else:
            ctx.conn.execute(work_calendar_years.update().where(work_calendar_years.c.year == year)
                             .values(version=cur["version"] + 1, **values))
        ctx.conn.execute(work_calendar_days.insert(), [
            {"day": d["date"], "year": year, "kind": d["kind"], "title": d["title"], "moved_from": d["moved_from"]}
            for d in cal["days"]])
        n = self._get(ctx.conn, work_calendar_years, year, "year")
        ctx.log("work_calendar_year", str(year), auth, cur["version"] if cur else None, n["version"],
                {"event": "WorkCalendarImported", "year": year, "source": source, "source_date": cal["source_date"],
                 **cal["counts"]})
        return {"work_calendar_year": {"year": year, "source": source, "source_date": cal["source_date"],
                                       "days_count": len(cal["days"]), "counts": cal["counts"],
                                       "counts_label": pc.counts_label(cal["counts"]), "version": n["version"],
                                       "replaced": cur is not None}}

    # ------------------------------------------------------------ calendar.day_set
    def _calendar_day_set(self, ctx, p, ev):
        """calendar.day_set {date, kind?, title?} — ручная правка дня (администратор, при связи): holiday / short /
        workday / normal («обычный день по графику» — снять праздник). kind пусто — убрать правку. ev — ожидаемая
        версия правки (у кого открыта старая — конфликт)."""
        only_keys(p, {"date", "kind", "title"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "calendar.day_set", ctx.now)
        f = _c(pc.check_day, p)
        t = work_calendar_overrides
        cur = self._get(ctx.conn, t, f["date"], "day")
        if cur is not None:
            self._check_version(cur, ev, self.v_calendar_override)
        if f["kind"] is None:
            if cur is None:
                return {"work_calendar_override": None}
            ctx.conn.execute(t.delete().where(t.c.day == f["date"]))
            ctx.log("work_calendar_override", f["date"], auth, cur["version"], None,
                    {"event": "WorkCalendarDayCleared", "date": f["date"]})
            return {"work_calendar_override": None}
        if cur is None:
            ctx.conn.execute(t.insert().values(day=f["date"], kind=f["kind"], title=f["title"], set_by=ctx.user_id,
                                               version=1, created_at=ctx.now, updated_at=ctx.now))
        else:
            ctx.conn.execute(t.update().where(t.c.day == f["date"], t.c.version == cur["version"]).values(
                kind=f["kind"], title=f["title"], set_by=ctx.user_id, version=cur["version"] + 1, updated_at=ctx.now))
        n = self._get(ctx.conn, t, f["date"], "day")
        ctx.log("work_calendar_override", f["date"], auth, cur["version"] if cur else None, n["version"],
                {"event": "WorkCalendarDaySet", **f})
        return {"work_calendar_override": self.v_calendar_override(n)}

    # ------------------------------------------------------------ pull
    def _pull_calendar(self, conn, uid: str, now) -> dict[str, Any]:
        """Часть pull: производственный календарь — всем вошедшим (work_calendar): действующие особые дни на прошлый,
        текущий и следующий год (по ним клиент без связи считает окно рабочего дня), откуда календарь на каждый год,
        ручные правки; администратору — какие годы его программе загрузить самой (auto_years)."""
        today = now.date()
        years = (today.year - 1, today.year, today.year + 1)
        loaded = self._calendar_loaded(conn)
        manage = self.policy.can(conn, uid, "calendar.import", now)
        days = self._calendar(conn, years)
        return {"work_calendar": {
            "days": days,
            "years": [{"year": y, **pc.year_status(y, loaded),
                       "counts_label": pc.counts_label(pc.counts(d for k, d in days.items() if k.startswith(f"{y}-"))),
                       "updated_at": iso(loaded[y]["updated_at"]) if y in loaded else None,
                       "version": loaded[y]["version"] if y in loaded else None} for y in years],
            "overrides": [self.v_calendar_override(dict(r._mapping)) for r in
                          conn.execute(sa.select(work_calendar_overrides).order_by(work_calendar_overrides.c.day))],
            "auto_years": pc.needs_auto_years(today, loaded) if manage else [],
            "manage": manage}}
