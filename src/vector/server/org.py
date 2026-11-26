"""Оргструктура (этап 7, R-21; decision log D-44 — ответ владельца на A-48 п. 1: «создать отделы, структуры,
филиалы, связи между ними, дерево зависимостей и так далее»).

Примесь (mixin) к `Service`, как worktime.py: транзакция, идемпотентность, аудит и проверка версий — в service.py;
правила дерева (виды, цикл, глубина, маршрут проверки) — в vector.core.org, общие с клиентом.

Команды (все — только при связи: структура меняет, кому уходят отчёты, — правка не должна «догонять» очередь):

    org.unit_create  {id, kind, name, code?, parent_id?, head_user_id?}  — новое подразделение
    org.unit_update  {id, kind, name, code?, parent_id?, head_user_id?}  — изменить (в т. ч. перенести в другое
                                                                          подразделение, сменить руководителя)
    org.unit_archive {id, reason}                                         — в архив (только пустое)
    org.unit_restore {id}                                                 — вернуть из архива
    org.member_set   {user_id, unit_id?, position?}                       — включить сотрудника в подразделение,
                                                                          перевести или (unit_id пусто) вывести

Право org.manage — у всех, у кого есть роль «Администратор» (ответ владельца на A-50 п. 1, D-45). Читают все вошедшие (pull: org_units, org_members, org_events — справочник
компании, без прав и контактов).

Главное следствие (D-44, D-45): дневной отчёт сотрудника, включённого в оргструктуру, уходит руководителю ЕГО
подразделения (или ближайшего вышестоящего — если своего нет, он отключён или это сам автор), а не всем
руководителям отделов. Принять или вернуть отчёт может руководитель любого подразделения выше по цепочке или
замещающий руководителя по роли (D-34). Отдельная роль для этого не нужна: руководитель подразделения проверяет
отчёты своих сотрудников потому, что он их руководитель (ответ владельца на A-50 п. 2). Сотрудники вне
оргструктуры и те, у кого по цепочке нет ни одного руководителя, — по прежнему правилу D-42 (все руководители
отделов по роли): отчёт никогда не «повисает» (A-37).
"""
from __future__ import annotations

import json
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import org as og
from vector.core.errors import Denied, Rejected

from .authz import Authority, active_delegated
from .db import org_events, org_members, org_units, role_delegations, users
from .validation import iso, only_keys, req_uuid

# Сколько последних изменений оргструктуры приходит в pull (история на экране «Оргструктура»).
PULL_EVENTS = 200
EVENT_LABELS_RU = {"created": "Создано подразделение", "updated": "Изменено подразделение",
                   "archived": "Подразделение в архиве", "restored": "Подразделение возвращено из архива",
                   "placed": "Сотрудник включён в подразделение", "moved": "Сотрудник переведён",
                   "position": "Изменена должность", "removed": "Сотрудник выведен из оргструктуры"}


def _o(fn, *a, **kw):
    """Вызвать правило vector.core.org; OrgError → Rejected (ошибка ввода с полем)."""
    try:
        return fn(*a, **kw)
    except og.OrgError as e:
        raise Rejected(e.code, e.message_ru, e.details)


class OrgMixin:
    """Команды org.*, представления подразделений и маршрут проверки дневных отчётов."""

    # ------------------------------------------------------------ чтение
    def _org_units(self, conn) -> dict[str, dict]:
        """Все подразделения (с архивными): {id: строка}. Структура небольшая — читается целиком."""
        return {r.id: dict(r._mapping) for r in conn.execute(sa.select(org_units))}

    def _org_unit_of(self, conn, user_id: str) -> Optional[str]:
        """Подразделение сотрудника (основное место работы) или None — вне оргструктуры."""
        return conn.execute(sa.select(org_members.c.unit_id).where(org_members.c.user_id == user_id)).scalar()

    def _active_user_ids(self, conn) -> set[str]:
        """Активные учётные записи. Проверяющим по маршруту может быть только тот, кто может войти: отключённый
        руководитель пропускается — отчёт идёт выше (A-37: отсутствие человека не останавливает работу)."""
        return {r.id for r in conn.execute(sa.select(users.c.id).where(users.c.active == sa.true()))}

    def _is_org_head(self, conn, uid: str) -> bool:
        """Руководит ли пользователь хотя бы одним действующим подразделением (D-45: этого достаточно, чтобы видеть и
        проверять отчёты своих сотрудников — отдельная роль не нужна)."""
        return conn.execute(sa.select(org_units.c.id).where(org_units.c.head_user_id == uid,
                                                            org_units.c.active == sa.true()).limit(1)).first() is not None

    def v_org_unit(self, u: dict, members: int = 0) -> dict:
        """Подразделение для клиента: вид, название, код, «входит в», руководитель, действует ли, версия."""
        return {"id": u["id"], "kind": u["kind"], "kind_label": og.KIND_LABELS_RU[u["kind"]], "name": u["name"],
                "code": u["code"], "parent_id": u["parent_id"], "head_user_id": u["head_user_id"],
                "active": bool(u["active"]), "members": members, "version": u["version"],
                "updated_at": iso(u["updated_at"])}

    # ------------------------------------------------------------ маршрут дневного отчёта (D-44)
    def _report_route(self, conn, author_id: str, now) -> Optional[dict]:
        """Кто проверяет отчёты автора: {unit_id, head_user_id} — руководитель его подразделения или ближайшего
        вышестоящего (без самого автора, только активная учётная запись); None — автор вне оргструктуры или выше
        никого. Роль «Руководитель отдела» для этого не нужна (ответ владельца на A-50 п. 2, D-45)."""
        unit = self._org_unit_of(conn, author_id)
        if unit is None:
            return None
        ok = self._active_user_ids(conn)
        return og.review_route(self._org_units(conn), unit, author_id, lambda u: u in ok)

    def _chain(self, conn, author_id: str) -> Optional[list[tuple[str, str]]]:
        """Руководители по цепочке вверх от подразделения автора: [(id подразделения, руководитель)] — действующие
        подразделения, активные учётные записи, без самого автора и без повторов; первый — тот, кому уходит отчёт.
        Все они вправе принять или вернуть отчёт (руководители выше — для статистики и подстраховки). None — автор
        вне оргструктуры или по цепочке нет ни одного руководителя: тогда действует общее правило D-42 (все
        руководители отделов по роли), чтобы отчёт никогда не «повисал» (A-37)."""
        unit = self._org_unit_of(conn, author_id)
        if unit is None:
            return None
        units, ok, out = self._org_units(conn), self._active_user_ids(conn), []
        for uid in og.ancestors(units, unit):
            head = units[uid].get("head_user_id")
            if units[uid]["active"] and head and head != author_id and head in ok and head not in {h for _, h in out}:
                out.append((uid, head))
        return out or None

    def _deputies_of(self, conn, holder_ids: list[str], now) -> list[str]:
        """Кто сейчас замещает роль «Руководитель отдела» у кого-то из holder_ids (D-34) — они получают отчёты наравне
        с руководителем (A-37: отсутствие человека не останавливает работу)."""
        if not holder_ids:
            return []
        rd = role_delegations
        today = now.date()
        cand = [r.to_user_id for r in conn.execute(sa.select(rd.c.to_user_id).where(
            rd.c.from_user_id.in_(holder_ids), rd.c.role == "dept_head", rd.c.revoked_at.is_(None),
            rd.c.valid_from <= today, rd.c.valid_until >= today))]
        out = []
        for c in cand:
            if c not in out and any(h in holder_ids and r == "dept_head" for _, r, _, h in active_delegated(conn, c, now)):
                out.append(c)
        return out

    def _report_reviewers(self, conn, author_id: str, now) -> Optional[list[str]]:
        """Кому уходит уведомление «отчёт на проверке»: руководитель по маршруту и его замещающие. None — автор вне
        оргструктуры (тогда — всем руководителям отделов, как в D-42)."""
        route = self._report_route(conn, author_id, now)
        if route is None:
            return None
        return [route["head_user_id"]] + [d for d in self._deputies_of(conn, [route["head_user_id"]], now)
                                          if d != author_id]

    def _review_authority(self, conn, uid: str, author_id: str, now) -> Optional[Authority]:
        """Основание принять / вернуть отчёт автора, если автор в оргструктуре (D-44, D-45):

        * руководитель подразделения по цепочке автора — основание «руководитель подразделения» (org_head), роль
          «Руководитель отдела» не нужна (ответ владельца на A-50 п. 2: «отчёт должен уходить руководителю отдела»);
        * замещающий такого руководителя по роли «Руководитель отдела» (D-34) — основание «замещение, за кого».

        None — автор вне оргструктуры (проверка — по матрице прав, как в D-42). Нет права — Denied."""
        chain = self._chain(conn, author_id)
        if chain is None:
            return None
        for unit_id, head in chain:
            if head == uid:
                return Authority("org_head", "org_head", 0, unit_id=unit_id)
        heads = {h for _, h in chain}
        for gid, role, did, holder in active_delegated(conn, uid, now):
            if role == "dept_head" and holder in heads:
                return Authority("delegated_role", role, gid, did, holder)
        raise Denied("not_your_unit", "Этот отчёт проверяет руководитель подразделения сотрудника (или вышестоящего), "
                                      "либо его замещающий. Сотрудник — не из ваших подразделений.",
                     {"author_id": author_id})

    def _review_scope(self, conn, uid: str, now, by_role: bool):
        """Функция «видит ли uid отправленные отчёты автора». Автор вне оргструктуры (или без руководителей по
        цепочке) — только руководители отделов по роли (by_role, правило D-42); иначе — если uid или тот, кого он
        замещает в роли «Руководитель отдела» (D-34), руководит подразделением по цепочке автора. Считается один раз
        на pull."""
        units = self._org_units(conn)
        placed = {r.user_id: r.unit_id for r in conn.execute(sa.select(org_members.c.user_id, org_members.c.unit_id))}
        ok = self._active_user_ids(conn)
        heads = {uid} | {holder for _, role, _, holder in active_delegated(conn, uid, now) if role == "dept_head"}

        def visible(author_id: str) -> bool:
            if author_id not in placed:
                return by_role
            chain = og.chain_heads(units, placed[author_id], author_id, lambda u: u in ok)
            return bool(heads & set(chain)) if chain else by_role
        return visible

    # ------------------------------------------------------------ журнал
    def _org_event(self, ctx, otype: str, oid: str, event: str, details: dict, auth: Authority) -> None:
        """Записать шаг в неизменяемый журнал оргструктуры."""
        ctx.conn.execute(org_events.insert().values(object_type=otype, object_id=oid, event=event,
                                                    details_json=json.dumps(details, ensure_ascii=False, default=str),
                                                    authority=auth.describe(), actor_id=ctx.user_id, at=ctx.now))

    # ------------------------------------------------------------ проверки ввода
    def _check_head(self, conn, head: Optional[str]) -> None:
        """Руководитель — существующая активная учётная запись (или не задан)."""
        if head is None:
            return
        u = self._get(conn, users, head)
        if not u or not u["active"]:
            raise Rejected("head_not_found", "Руководитель: учётная запись не найдена или отключена.",
                           {"field": "head_user_id"})

    def _check_code_unique(self, units: dict, code: Optional[str], unit_id: str) -> None:
        """Код подразделения не повторяется (без учёта регистра)."""
        if code and any((u["code"] or "").lower() == code.lower() and u["id"] != unit_id for u in units.values()):
            raise Rejected("code_exists", f"Код «{code}» уже занят другим подразделением.", {"field": "code"})

    def _unit_fields(self, ctx, p: dict, unit_id: str, units: dict) -> dict:
        """Разобрать и проверить поля подразделения (общие для создания и правки)."""
        vals = {"kind": _o(og.parse_kind, p.get("kind")), "name": _o(og.parse_name, p.get("name")),
                "code": _o(og.parse_code, p.get("code")), "parent_id": _o(og.opt_id, p.get("parent_id"), "parent_id"),
                "head_user_id": _o(og.opt_id, p.get("head_user_id"), "head_user_id")}
        _o(og.check_parent, units, unit_id, vals["parent_id"])
        self._check_head(ctx.conn, vals["head_user_id"])
        self._check_code_unique(units, vals["code"], unit_id)
        return vals

    def _member_count(self, conn, unit_id: str) -> int:
        """Сколько сотрудников в подразделении (без подчинённых)."""
        return conn.execute(sa.select(sa.func.count()).select_from(org_members)
                            .where(org_members.c.unit_id == unit_id)).scalar() or 0

    # ------------------------------------------------------------ команды
    def _org_unit_create(self, ctx, p, ev):
        """org.unit_create {id, kind, name, code?, parent_id?, head_user_id?} — новое подразделение."""
        only_keys(p, {"id", "kind", "name", "code", "parent_id", "head_user_id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "org.manage", ctx.now)
        uid = req_uuid(p, "id")
        self._ensure_new(ctx.conn, org_units, uid)
        units = self._org_units(ctx.conn)
        vals = self._unit_fields(ctx, p, uid, units)
        ctx.conn.execute(org_units.insert().values(id=uid, active=True, version=1, created_by=ctx.user_id,
                                                   created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now, **vals))
        self._org_event(ctx, "unit", uid, "created", vals, auth)
        n = self._get(ctx.conn, org_units, uid)
        ctx.log("org_unit", uid, auth, None, 1, {"event": "OrgUnitCreated", "kind": vals["kind"], "name": vals["name"]})
        if vals["head_user_id"]:
            self._notify(ctx, [vals["head_user_id"]], "org_head",
                         f"Вы — руководитель подразделения «{vals['name']}»",
                         body="Дневные отчёты сотрудников подразделения теперь приходят вам на проверку.",
                         object_type="org_unit", object_id=uid)
        return {"org_unit": self.v_org_unit(n)}

    def _org_unit_update(self, ctx, p, ev):
        """org.unit_update {id, kind, name, code?, parent_id?, head_user_id?} — изменить подразделение: название,
        код, вид, во что входит (перенос в дереве — без циклов), руководителя. Архивное не правится."""
        only_keys(p, {"id", "kind", "name", "code", "parent_id", "head_user_id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "org.manage", ctx.now)
        o = self._require(ctx.conn, org_units, req_uuid(p, "id"), "Подразделение")
        self._check_version(o, ev, lambda x: self.v_org_unit(x))
        if not o["active"]:
            raise Rejected("archived", "Подразделение в архиве — сначала верните его.", {"field": "id"})
        units = self._org_units(ctx.conn)
        vals = self._unit_fields(ctx, p, o["id"], units)
        changed = {k: {"было": o[k], "стало": v} for k, v in vals.items() if o[k] != v}
        if not changed:
            return {"org_unit": self.v_org_unit(o, self._member_count(ctx.conn, o["id"]))}
        n = self._update_versioned(ctx, org_units, o, {**vals, "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._org_event(ctx, "unit", o["id"], "updated", changed, auth)
        ctx.log("org_unit", o["id"], auth, o["version"], n["version"], {"event": "OrgUnitUpdated", "fields": sorted(changed)})
        if "head_user_id" in changed and vals["head_user_id"]:
            self._notify(ctx, [vals["head_user_id"]], "org_head", f"Вы — руководитель подразделения «{vals['name']}»",
                         body="Дневные отчёты сотрудников подразделения теперь приходят вам на проверку.",
                         object_type="org_unit", object_id=o["id"])
        return {"org_unit": self.v_org_unit(n, self._member_count(ctx.conn, o["id"]))}

    def _org_unit_archive(self, ctx, p, ev):
        """org.unit_archive {id, reason} — в архив: только без действующих подчинённых и без сотрудников."""
        only_keys(p, {"id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "org.manage", ctx.now)
        o = self._require(ctx.conn, org_units, req_uuid(p, "id"), "Подразделение")
        self._check_version(o, ev, lambda x: self.v_org_unit(x))
        if not o["active"]:
            raise Rejected("archived", "Подразделение уже в архиве.", {"field": "id"})
        reason = _o(og._text, p.get("reason"), "reason", "причину", 500)
        _o(og.check_archive, self._org_units(ctx.conn), o["id"], self._member_count(ctx.conn, o["id"]))
        n = self._update_versioned(ctx, org_units, o, {"active": False, "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._org_event(ctx, "unit", o["id"], "archived", {"reason": reason}, auth)
        ctx.log("org_unit", o["id"], auth, o["version"], n["version"], {"event": "OrgUnitArchived"})
        return {"org_unit": self.v_org_unit(n)}

    def _org_unit_restore(self, ctx, p, ev):
        """org.unit_restore {id} — вернуть из архива (вышестоящее, если есть, должно действовать)."""
        only_keys(p, {"id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "org.manage", ctx.now)
        o = self._require(ctx.conn, org_units, req_uuid(p, "id"), "Подразделение")
        self._check_version(o, ev, lambda x: self.v_org_unit(x))
        if o["active"]:
            raise Rejected("not_archived", "Подразделение и так действует.", {"field": "id"})
        _o(og.check_parent, self._org_units(ctx.conn), o["id"], o["parent_id"])
        n = self._update_versioned(ctx, org_units, o, {"active": True, "updated_by": ctx.user_id, "updated_at": ctx.now})
        self._org_event(ctx, "unit", o["id"], "restored", {}, auth)
        ctx.log("org_unit", o["id"], auth, o["version"], n["version"], {"event": "OrgUnitRestored"})
        return {"org_unit": self.v_org_unit(n)}

    def _org_member_set(self, ctx, p, ev):
        """org.member_set {user_id, unit_id?, position?} — включить сотрудника в подразделение, перевести, сменить
        должность или (unit_id пусто) вывести из оргструктуры. Сотрудник узнаёт уведомлением."""
        only_keys(p, {"user_id", "unit_id", "position"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "org.manage", ctx.now)
        user_id = req_uuid(p, "user_id")
        u = self._require(ctx.conn, users, user_id, "Сотрудник")
        unit_id = _o(og.opt_id, p.get("unit_id"), "unit_id")
        position = _o(og.parse_position, p.get("position"))
        cur = self._get(ctx.conn, org_members, user_id, "user_id")
        if unit_id is None:
            if cur is None:
                return {"org_member": None}
            ctx.conn.execute(org_members.delete().where(org_members.c.user_id == user_id))
            self._org_event(ctx, "member", user_id, "removed", {"unit_id": cur["unit_id"]}, auth)
            ctx.log("org_member", user_id, auth, None, None, {"event": "OrgMemberRemoved", "unit_id": cur["unit_id"]})
            return {"org_member": None}
        unit = self._require(ctx.conn, org_units, unit_id, "Подразделение")
        if not unit["active"]:
            raise Rejected("archived", f"«{unit['name']}» в архиве — выберите действующее подразделение.", {"field": "unit_id"})
        if not u["active"]:
            raise Rejected("user_inactive", "Учётная запись отключена — в оргструктуру не включается.", {"field": "user_id"})
        if cur and cur["unit_id"] == unit_id and (cur["position"] or None) == position:
            return {"org_member": self._v_member(cur)}
        event = "placed" if cur is None else ("moved" if cur["unit_id"] != unit_id else "position")
        if cur is None:
            ctx.conn.execute(org_members.insert().values(user_id=user_id, unit_id=unit_id, position=position,
                                                         since=ctx.now, updated_by=ctx.user_id))
        else:
            ctx.conn.execute(org_members.update().where(org_members.c.user_id == user_id).values(
                unit_id=unit_id, position=position, updated_by=ctx.user_id,
                since=ctx.now if cur["unit_id"] != unit_id else cur["since"]))
        self._org_event(ctx, "member", user_id, event, {"unit_id": unit_id, "position": position,
                                                        "from_unit_id": cur and cur["unit_id"]}, auth)
        ctx.log("org_member", user_id, auth, None, None, {"event": "OrgMemberSet", "unit_id": unit_id})
        if event != "position":
            units = self._org_units(ctx.conn)
            self._notify(ctx, [user_id], "org_member", f"Вы в подразделении «{unit['name']}»",
                         body=og.path_label(units, unit_id), object_type="org_unit", object_id=unit_id)
        return {"org_member": self._v_member(self._get(ctx.conn, org_members, user_id, "user_id"))}

    @staticmethod
    def _v_member(m: Optional[dict]) -> Optional[dict]:
        """Сотрудник в подразделении для клиента."""
        if not m:
            return None
        return {"user_id": m["user_id"], "unit_id": m["unit_id"], "position": m["position"], "since": iso(m["since"])}

    # ------------------------------------------------------------ pull
    def _pull_org(self, conn, uid: str, now) -> dict[str, Any]:
        """Часть pull: подразделения (с архивными), сотрудники в них, последние изменения, своё подразделение и кто
        проверяет мои отчёты, может ли пользователь править структуру."""
        units = self._org_units(conn)
        members = [self._v_member(dict(r._mapping)) for r in conn.execute(sa.select(org_members).order_by(org_members.c.user_id))]
        counts: dict[str, int] = {}
        for m in members:
            counts[m["unit_id"]] = counts.get(m["unit_id"], 0) + 1
        events = [{"id": r.id, "object_type": r.object_type, "object_id": r.object_id, "event": r.event,
                   "event_label": EVENT_LABELS_RU.get(r.event, r.event), "details": json.loads(r.details_json),
                   "actor_id": r.actor_id, "at": iso(r.at)}
                  for r in conn.execute(sa.select(org_events).order_by(org_events.c.id.desc()).limit(PULL_EVENTS))]
        route = self._report_route(conn, uid, now)
        return {"org_units": [self.v_org_unit(u, counts.get(u["id"], 0)) for u in units.values()],
                "org_members": members, "org_events": events,
                "org_me": {"unit_id": self._org_unit_of(conn, uid), "reviewer_id": route and route["head_user_id"],
                           "reviewer_unit_id": route and route["unit_id"],
                           "manage": self.policy.can(conn, uid, "org.manage", now)}}
