"""Расчёт комплекта → фиксация → ревизия проектного итога (сценарий С3; R-04, R-11, R-12, R-30).

Модуль — примесь (mixin) к `Service`, как и work.py: общая инфраструктура (транзакция SERIALIZABLE,
идемпотентность по operation_id, аудит, проверка версий) — в service.py; здесь только правила расчёта.

Жизненный цикл (03-modules-data-lifecycles §4):

    черновик (один на комплект, изменяемый) ──calc.freeze──▶ неизменяемая версия K.n
                                                     └──▶ новая неизменяемая ревизия проектного итога P.r

Главные инварианты (каждый закреплён тестом в tests/server/test_calc.py):

* Официальный итог проекта считается ТОЛЬКО из зафиксированных версий (последней версии каждого комплекта)
  и только в момент фиксации. Черновики в итог не входят; «поправить итог» вручную не может никто.
* Фиксация — online_required и фиксирует ровно ту версию черновика, которую видел пользователь
  (expected_version): если коллега успел изменить черновик, будет конфликт, а не фиксация чужих правок.
* Версии, их позиции, ревизии и состав ревизий неизменяемы (триггеры БД, db._immutable).
* Деньги — Decimal без молчаливого округления; «цена неизвестна» ≠ 0; валюты не смешиваются
  (правила — vector.core.calc, общие с клиентом).
* Черновик сохраняется и офлайн (queued), но конфликт правок не сливается автоматически: деньги.
* Права — по ролям в проекте в момент эффекта (A-18): черновик — участники проекта, фиксация — ГИП
  проекта, просмотр проектного итога — ГИП/РП проекта и бухгалтерия.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Optional

import sqlalchemy as sa

from vector.core import calc, money
from vector.core.errors import Conflict, Rejected

from .db import (calc_draft_lines, calc_drafts, calc_version_lines, calc_versions, kits, project_calc_revision_items,
                 project_calc_revisions, projects)
from .validation import iso, only_keys, opt_str, req_str, req_uuid

def _line_json(r: dict) -> dict:
    return {"line_id": r["line_id"], "code": r["code"], "name": r["name"], "unit": r["unit"],
            "qty": money.to_json(r["qty"]), "unit_price": money.to_json(r["unit_price"]),
            "amount": money.to_json(r["amount"]), "note": r["note"]}


def _line_row(ln: calc.Line, pos: int) -> dict:
    return {"pos": pos, "line_id": ln.line_id, "code": ln.code, "name": ln.name, "unit": ln.unit, "qty": ln.qty,
            "unit_price": ln.unit_price, "amount": ln.amount, "note": ln.note}


class CalcMixin:
    """Обработчики calc.save_draft / calc.freeze и данные расчётов для pull."""

    # ------------------------------------------------------------ чтение
    def _draft_lines(self, conn, kit_id: str) -> list[dict]:
        return [_line_json(dict(r._mapping)) for r in conn.execute(
            sa.select(calc_draft_lines).where(calc_draft_lines.c.kit_id == kit_id).order_by(calc_draft_lines.c.pos))]

    def v_calc_draft(self, conn, o: dict, lines: Optional[list[dict]] = None) -> dict:
        """Черновик для клиента. id = id комплекта: в кэше клиента черновик — объект типа calc_draft."""
        lines = self._draft_lines(conn, o["kit_id"]) if lines is None else lines
        t = calc.totals(calc.lines_from_json(lines, o["currency"]))
        return {"id": o["kit_id"], "kit_id": o["kit_id"], "project_id": o["project_id"], "currency": o["currency"],
                "note": o["note"], "lines": lines, "content_hash": o["content_hash"], "version": o["version"],
                "updated_by": o["updated_by"], "updated_at": iso(o["updated_at"]), **t}

    def v_calc_version(self, conn, o: dict) -> dict:
        lines = [_line_json(dict(r._mapping)) for r in conn.execute(
            sa.select(calc_version_lines).where(calc_version_lines.c.version_id == o["id"]).order_by(calc_version_lines.c.pos))]
        return {"id": o["id"], "kit_id": o["kit_id"], "project_id": o["project_id"], "version_no": o["version_no"],
                "currency": o["currency"], "note": o["note"], "reason": o["reason"], "total": money.to_json(o["total"]),
                "line_count": o["line_count"], "unknown_price_count": o["unknown_price_count"],
                "complete": o["unknown_price_count"] == 0, "content_hash": o["content_hash"],
                "draft_version": o["draft_version"], "frozen_by": o["frozen_by"], "frozen_at": iso(o["frozen_at"]),
                "lines": lines}

    def v_calc_revision(self, conn, o: dict) -> dict:
        """Ревизия проектного итога: состав (комплект, версия, hash, сумма) и итоги ПО ВАЛЮТАМ (не смешиваются).

        Код и название проекта включены, потому что бухгалтерия видит итоги, не имея права читать проект.
        """
        pr = self._get(conn, projects, o["project_id"])
        kit_names = {r.id: (r.code, r.title) for r in conn.execute(
            sa.select(kits.c.id, kits.c.code, kits.c.title).where(kits.c.project_id == o["project_id"]))}
        items = []
        by_cur: dict[Optional[str], dict] = defaultdict(lambda: {"total": Decimal(0), "unknown_price_count": 0, "kits": 0})
        for r in conn.execute(sa.select(project_calc_revision_items).where(project_calc_revision_items.c.revision_id == o["id"])):
            r = dict(r._mapping)
            code, title = kit_names.get(r["kit_id"], ("?", ""))
            items.append({"kit_id": r["kit_id"], "kit_code": code, "kit_title": title, "version_no": r["version_no"],
                          "calc_version_id": r["calc_version_id"], "content_hash": r["content_hash"], "currency": r["currency"],
                          "total": money.to_json(r["total"]), "unknown_price_count": r["unknown_price_count"]})
            g = by_cur[r["currency"]]
            g["total"] += r["total"]
            g["unknown_price_count"] += r["unknown_price_count"]
            g["kits"] += 1
        items.sort(key=lambda i: i["kit_code"])
        totals = [{"currency": c, "total": money.to_json(g["total"]), "unknown_price_count": g["unknown_price_count"],
                   "kits": g["kits"], "complete": g["unknown_price_count"] == 0}
                  for c, g in sorted(by_cur.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))]
        trig = self._get(conn, calc_versions, o["trigger_version_id"])
        return {"id": o["id"], "project_id": o["project_id"], "project_code": pr["code"], "project_title": pr["title"],
                "revision_no": o["revision_no"], "reason": o["reason"], "trigger_kit_id": o["trigger_kit_id"],
                "trigger_version_no": trig["version_no"] if trig else None, "created_by": o["created_by"],
                "created_at": iso(o["created_at"]), "items": items, "totals": totals,
                "complete": all(t["complete"] for t in totals)}

    def _latest_version(self, conn, kit_id: str) -> Optional[dict]:
        r = conn.execute(sa.select(calc_versions).where(calc_versions.c.kit_id == kit_id)
                         .order_by(calc_versions.c.version_no.desc()).limit(1)).first()
        return dict(r._mapping) if r else None

    @staticmethod
    def _calc_problems(e: calc.CalcError) -> Rejected:
        # Все замечания с номерами позиций — клиент подсветит их в таблице (первые 100: ответ не раздувается).
        return Rejected("calc_invalid", e.message_ru, {"problems": e.problems[:100], "problem_count": len(e.problems)})

    # ------------------------------------------------------------ calc.save_draft
    def _calc_save_draft(self, ctx, p, ev):
        """Сохранить черновик расчёта комплекта (позиции заменяются целиком).

        expected_version: версия черновика, на которой основана правка; 0 — «черновика ещё не было».
        Если тем временем коллега создал/изменил черновик — конфликт с текущим содержимым в details.current;
        пользователь сам решает: отменить своё или повторить поверх (никакого слияния денег).
        """
        only_keys(p, {"id", "currency", "note", "lines"})
        kit_id = req_uuid(p, "id")
        kit, pr = self._open_project_of_kit(ctx, kit_id)
        auth = self.policy.check(ctx.conn, ctx.user_id, "calc.save_draft", ctx.now, project_id=pr["id"])
        self._project_must_be_active(pr)
        try:
            lines, cur = calc.parse_lines(p.get("lines"), p.get("currency"))
        except calc.CalcError as e:
            raise self._calc_problems(e)
        note = opt_str(p, "note", 2000)
        h = calc.content_hash(lines, cur, note)
        d = self._get(ctx.conn, calc_drafts, kit_id, "kit_id")
        view = lambda o: self.v_calc_draft(ctx.conn, o)  # noqa: E731
        if d is None:
            if ev != 0:
                raise Rejected("calc_draft_missing", "Черновика расчёта на сервере нет. Обновите данные и повторите.")
            old_lines: list[dict] = []
        else:
            if ev == 0:
                raise Conflict("version_conflict", "Пока вы работали, коллега уже сохранил расчёт этого комплекта. "
                               "Изменение не применено; сравните и решите, повторить ли его.",
                               {"expected_version": 0, "current": view(d)})
            self._check_version(d, ev, view)
            if d["content_hash"] == h:
                # Ничего не изменилось: версия не растёт, журнал не засоряется. Ответ «принято» — повтор из
                # офлайн-очереди не должен останавливать синхронизацию ошибкой «нет изменений».
                return {"calc_draft": view(d), "unchanged": True}
            old_lines = self._draft_lines(ctx.conn, kit_id)
            ctx.conn.execute(calc_draft_lines.delete().where(calc_draft_lines.c.kit_id == kit_id))
        values = {"currency": cur, "note": note, "line_count": len(lines), "content_hash": h,
                  "updated_by": ctx.user_id, "updated_at": ctx.now}
        if d is None:
            ctx.conn.execute(calc_drafts.insert().values(kit_id=kit_id, project_id=pr["id"], version=1,
                                                         created_by=ctx.user_id, created_at=ctx.now, **values))
        else:
            n = ctx.conn.execute(calc_drafts.update().where(calc_drafts.c.kit_id == kit_id, calc_drafts.c.version == d["version"])
                                 .values(version=d["version"] + 1, **values)).rowcount
            if n != 1:   # дополнительная защита поверх SERIALIZABLE
                raise Conflict("version_conflict", "Черновик изменён параллельно. Повторите после обновления.", {"current": None})
        if lines:
            ctx.conn.execute(calc_draft_lines.insert(), [{"kit_id": kit_id, **_line_row(ln, i)} for i, ln in enumerate(lines, 1)])
        nd = self._get(ctx.conn, calc_drafts, kit_id, "kit_id")
        new_lines = [ln.to_json() for ln in lines]
        df = calc.diff(old_lines, new_lines, d["currency"] if d else None, cur)
        t = calc.totals(lines)
        ctx.log("calc_draft", kit_id, auth, d["version"] if d else None, nd["version"],
                {"project_id": pr["id"], "kit_code": kit["code"], "currency": cur, "total": t["total"],
                 "line_count": t["line_count"], "unknown_price_count": t["unknown_price_count"], "content_hash": h,
                 "added": df["added"], "removed": df["removed"], "changed": df["changed"],
                 "changed_lines": [r["name"] for r in df["rows"] if r["kind"] != "unchanged"][:20]})
        return {"calc_draft": self.v_calc_draft(ctx.conn, nd, new_lines)}

    # ------------------------------------------------------------ calc.freeze
    def _calc_freeze(self, ctx, p, ev):
        """Зафиксировать черновик: неизменяемая версия K.n + новая ревизия проектного итога (одна транзакция)."""
        only_keys(p, {"id", "reason"})
        kit_id = req_uuid(p, "id")
        kit, pr = self._open_project_of_kit(ctx, kit_id)
        auth = self.policy.check(ctx.conn, ctx.user_id, "calc.freeze", ctx.now, project_id=pr["id"])
        self._project_must_be_active(pr)
        d = self._get(ctx.conn, calc_drafts, kit_id, "kit_id")
        if d is None:
            raise Rejected("calc_draft_missing", "Сначала сохраните расчёт комплекта.")
        view = lambda o: self.v_calc_draft(ctx.conn, o)  # noqa: E731
        self._check_version(d, ev, view)
        reason = req_str(p, "reason", 2000)
        line_json = self._draft_lines(ctx.conn, kit_id)
        try:
            # Повторная проверка сохранённых позиций по ТЕКУЩИМ правилам: если правила ужесточили после
            # сохранения черновика, зафиксировать недопустимое содержимое нельзя.
            lines = calc.lines_from_json(line_json, d["currency"])
        except calc.CalcError as e:
            raise self._calc_problems(e)
        if not lines:
            raise Rejected("calc_empty", "В расчёте нет позиций — фиксировать нечего.")
        last = self._latest_version(ctx.conn, kit_id)
        if last is not None and last["content_hash"] == d["content_hash"]:
            raise Rejected("calc_nothing_to_freeze",
                           f"Черновик не отличается от зафиксированной версии v{last['version_no']} — новая версия не нужна.")
        t = calc.totals(lines)
        no = (last["version_no"] if last else 0) + 1
        vid = ctx.conn.execute(calc_versions.insert().values(
            kit_id=kit_id, project_id=pr["id"], version_no=no, currency=d["currency"], note=d["note"], reason=reason,
            total=Decimal(t["total"]), line_count=t["line_count"], unknown_price_count=t["unknown_price_count"],
            content_hash=d["content_hash"], draft_version=d["version"], frozen_by=ctx.user_id, frozen_at=ctx.now)
        ).inserted_primary_key[0]
        ctx.conn.execute(calc_version_lines.insert(), [{"version_id": vid, **_line_row(ln, i)} for i, ln in enumerate(lines, 1)])

        # Ревизия проектного итога: последние зафиксированные версии ВСЕХ комплектов проекта (включая новую).
        latest: dict[str, dict] = {}
        for r in ctx.conn.execute(sa.select(calc_versions).where(calc_versions.c.project_id == pr["id"])
                                  .order_by(calc_versions.c.kit_id, calc_versions.c.version_no)):
            latest[r.kit_id] = dict(r._mapping)          # по возрастанию номера — последняя перезаписывает
        prev_rev = ctx.conn.execute(sa.select(sa.func.max(project_calc_revisions.c.revision_no))
                                    .where(project_calc_revisions.c.project_id == pr["id"])).scalar()
        rev_no = (prev_rev or 0) + 1
        rid = ctx.conn.execute(project_calc_revisions.insert().values(
            project_id=pr["id"], revision_no=rev_no, reason=reason, trigger_kit_id=kit_id, trigger_version_id=vid,
            created_by=ctx.user_id, created_at=ctx.now)).inserted_primary_key[0]
        ctx.conn.execute(project_calc_revision_items.insert(), [
            {"revision_id": rid, "kit_id": k, "calc_version_id": v["id"], "version_no": v["version_no"],
             "content_hash": v["content_hash"], "currency": v["currency"], "total": v["total"],
             "unknown_price_count": v["unknown_price_count"]} for k, v in sorted(latest.items())])

        ver = self.v_calc_version(ctx.conn, self._get(ctx.conn, calc_versions, vid))
        rev = self.v_calc_revision(ctx.conn, self._get(ctx.conn, project_calc_revisions, rid))
        ctx.log("calc_draft", kit_id, auth, d["version"], d["version"],
                {"frozen_version_no": no, "content_hash": d["content_hash"], "currency": d["currency"], "total": t["total"],
                 "line_count": t["line_count"], "unknown_price_count": t["unknown_price_count"], "reason": reason,
                 "calc_revision_no": rev_no})
        # Ревизия итога — отдельный тип объекта в журнале: её история видна только тем, кто вправе видеть итог.
        ctx.log("project_calc", pr["id"], auth, None, None,
                {"calc_revision_no": rev_no, "reason": reason, "trigger_kit_code": kit["code"], "trigger_version_no": no,
                 "totals": rev["totals"],
                 "items": [{"kit_code": i["kit_code"], "version_no": i["version_no"], "total": i["total"],
                            "currency": i["currency"]} for i in rev["items"]]})
        return {"calc_draft": view(d), "calc_version": ver, "calc_revision": rev}

    # ------------------------------------------------------------ данные для pull
    def _pull_calc(self, conn, uid: str, now, readable: list[str]) -> dict:
        """Черновики и версии — по читаемым проектам; ревизии итога — по праву calc.read_revisions."""
        out = {"calc_drafts": [], "calc_versions": [], "calc_revisions": []}
        read_ids = [pid for pid in readable if self.policy.can(conn, uid, "calc.read", now, pid)]
        if read_ids:
            for d in conn.execute(sa.select(calc_drafts).where(calc_drafts.c.project_id.in_(read_ids))
                                  .order_by(calc_drafts.c.kit_id)):
                out["calc_drafts"].append(self.v_calc_draft(conn, dict(d._mapping)))
            for v in conn.execute(sa.select(calc_versions).where(calc_versions.c.project_id.in_(read_ids))
                                  .order_by(calc_versions.c.kit_id, calc_versions.c.version_no)):
                out["calc_versions"].append(self.v_calc_version(conn, dict(v._mapping)))
        rev_ids = [r.id for r in conn.execute(sa.select(projects.c.id).order_by(projects.c.code))
                   if self.policy.can(conn, uid, "calc.read_revisions", now, r.id)]
        if rev_ids:
            for r in conn.execute(sa.select(project_calc_revisions).where(project_calc_revisions.c.project_id.in_(rev_ids))
                                  .order_by(project_calc_revisions.c.project_id, project_calc_revisions.c.revision_no)):
                out["calc_revisions"].append(self.v_calc_revision(conn, dict(r._mapping)))
        return out
