"""Замещение — передача глобальной роли на время (D-34, ответ владельца на A-37) и основания решений.

Владелец: «все и вся должны быть заменяемыми, не должно возникать ситуации, что если нет человека, невозможно было
решить какой-то вопрос или кому-то делегировать». Правила — vector.core.delegation; здесь — команды, представления
и часть pull:

* delegation.create {from_user_id?, to_user_id, role, valid_from?, valid_until, reason} — оформить замещение.
  Свою роль передаёт любой её носитель (право delegation.create — любой вошедший, роль проверяется здесь); за
  отсутствующего — руководитель отдела или администратор (право delegation.manage), но не себе самому;
* delegation.revoke {id, reason} — отменить: носитель, замещающий (отказаться), оформивший; остальные — с
  delegation.manage;
* pull: delegations — свои (передал, получил, оформил) или все (delegation.manage); role_coverage — покрытие ролей
  (у какой роли нет ни одного человека или нет замены) — только delegation.manage.

Обе команды — только при связи: передача полномочий не должна «догонять» офлайн-очередь.
Как замещение участвует в проверке прав — vector.server.authz (active_delegated, Policy.check).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import delegation as dl
from vector.core.errors import Rejected

from .authz import Authority, active_delegated, active_grants
from .db import approval_bases, role_delegations, role_grants, users
from .validation import iso, only_keys, opt_int, req_str, req_uuid

# Сколько дней после окончания замещения оно ещё приходит в pull (история на экране «Замещения»).
PULL_ENDED_DAYS = 180


class DelegationMixin:
    """Команды и представления замещения; основания решений по согласованию (approval_bases)."""

    # ------------------------------------------------------------ основания решений
    def _record_basis(self, ctx, object_type: str, object_id: str, seq: int, auth: Optional[Authority]) -> None:
        """Записать неизменяемо, чьим правом принято решение по согласованию: роль, выдача, замещение и носитель.

        Вызывается для каждого события согласования заказа и для согласования ТКП. По этой записи история
        показывает «Иванов (замещает Зайцеву)», а проверка действительности согласования ТКП — чью выдачу роли
        проверять (носителя, а не замещающего).
        """
        if auth is None:
            return
        ctx.conn.execute(approval_bases.insert().values(
            object_type=object_type, object_id=str(object_id), seq=seq, actor_id=ctx.user_id, role=auth.role,
            authority=auth.describe(), grant_id=auth.ref_id if auth.kind in ("global_role", "delegated_role") else None,
            delegation_id=auth.delegation_id, on_behalf_of=auth.on_behalf_of, at=ctx.now))

    def _basis(self, conn, object_type: str, object_id: str, seq: int = 1) -> Optional[dict]:
        """Основание решения (approval_bases) или None — для решений, принятых до D-34."""
        r = conn.execute(sa.select(approval_bases).where(approval_bases.c.object_type == object_type,
                                                         approval_bases.c.object_id == str(object_id),
                                                         approval_bases.c.seq == seq)).first()
        return dict(r._mapping) if r else None

    # ------------------------------------------------------------ представление
    @staticmethod
    def v_delegation(d: dict, holder_has_role: bool, today: date) -> dict:
        """Замещение для клиента: кто кому какую роль, срок, причина, кто оформил, отмена и состояние на сегодня."""
        st = dl.state(d["valid_from"], d["valid_until"], d["revoked_at"] is not None, holder_has_role, today)
        return {"id": d["id"], "from_user_id": d["from_user_id"], "to_user_id": d["to_user_id"], "role": d["role"],
                "valid_from": d["valid_from"].isoformat(), "valid_until": d["valid_until"].isoformat(),
                "reason": d["reason"], "created_by": d["created_by"], "created_at": iso(d["created_at"]),
                "revoked_at": iso(d["revoked_at"]), "revoked_by": d["revoked_by"], "revoke_reason": d["revoke_reason"],
                "state": st, "state_label": dl.STATE_LABELS_RU[st]}

    def _holder_has_role(self, conn, user_id: str, role: str, now) -> bool:
        """Есть ли у носителя СВОЯ действующая выдача роли (и активна ли его учётная запись)."""
        u = self._get(conn, users, user_id)
        return bool(u and u["active"]) and role in [r for _, r in active_grants(conn, user_id, now)]

    def _delegation_view(self, conn, d: dict, now) -> dict:
        """Представление замещения по строке таблицы (состояние — на сегодня)."""
        return self.v_delegation(d, self._holder_has_role(conn, d["from_user_id"], d["role"], now), now.date())

    # ------------------------------------------------------------ команды
    def _delegation_create(self, ctx, p, ev):
        """delegation.create {from_user_id?, to_user_id, role, valid_from?, valid_until, reason} — оформить замещение.

        Свою роль (from_user_id не указан или это вы) — право delegation.create и СВОЯ действующая выдача роли
        (полученную замещением роль дальше не передают). За другого — право delegation.manage (руководитель
        отдела, администратор), у носителя должна быть своя роль, назначить замещающим себя нельзя. Роль
        «Администратор» не передаётся. Замещающий — активный сотрудник, не носитель, без своей такой же роли;
        пересекающееся по сроку такое же замещение — отказ.
        """
        only_keys(p, {"from_user_id", "to_user_id", "role", "valid_from", "valid_until", "reason"})
        holder = p.get("from_user_id") or ctx.user_id
        if not isinstance(holder, str):
            raise Rejected("invalid_type", "from_user_id — строка.", {"field": "from_user_id"})
        for_self = holder == ctx.user_id
        auth = self.policy.check(ctx.conn, ctx.user_id, "delegation.create" if for_self else "delegation.manage", ctx.now)
        role = dl.check_role(p.get("role"), self.contracts.permissions["roles"])
        to = req_uuid(p, "to_user_id")
        if to == holder:
            raise Rejected("self_delegation", "Замещающий — другой сотрудник, не сам носитель роли.", {"field": "to_user_id"})
        if not for_self and to == ctx.user_id:
            raise Rejected("self_assign", "Назначить замещающим себя нельзя: это делает сам носитель роли, другой "
                           "руководитель отдела или администратор (решение — «в четыре глаза»).", {"field": "to_user_id"})
        h = self._get(ctx.conn, users, holder)
        if not h:
            raise Rejected("user_not_found", "Носитель роли не найден.", {"field": "from_user_id"})
        if not self._holder_has_role(ctx.conn, holder, role, ctx.now):
            label = self._role_label(role)
            raise Rejected("role_not_held",
                           (f"У вас нет своей роли «{label}». Передать можно только свою роль — полученную замещением "
                            "роль дальше не передают." if for_self else
                            f"У сотрудника «{h['display_name']}» нет действующей роли «{label}» — передавать нечего."),
                           {"field": "role"})
        t = self._get(ctx.conn, users, to)
        if not t or not t["active"]:
            raise Rejected("user_not_found", "Замещающий не найден или отключён.", {"field": "to_user_id"})
        if role in [r for _, r in active_grants(ctx.conn, to, ctx.now)]:
            raise Rejected("already_has_role", f"У сотрудника «{t['display_name']}» уже есть своя роль "
                           f"«{self._role_label(role)}» — замещение не нужно.", {"field": "to_user_id"})
        start, until = dl.check_period(dl.parse_day(p.get("valid_from"), "valid_from"),
                                       dl.parse_day(p.get("valid_until"), "valid_until"), ctx.now.date())
        reason = req_str(p, "reason", 500)
        rd = role_delegations
        clash = ctx.conn.execute(sa.select(rd.c.id).where(
            rd.c.from_user_id == holder, rd.c.to_user_id == to, rd.c.role == role, rd.c.revoked_at.is_(None),
            rd.c.valid_from <= until, rd.c.valid_until >= start)).first()
        if clash:
            raise Rejected("delegation_exists", "Такое замещение на эти даты уже оформлено.", {"delegation_id": clash.id})
        did = ctx.conn.execute(rd.insert().values(from_user_id=holder, to_user_id=to, role=role, valid_from=start,
                                                  valid_until=until, reason=reason, created_by=ctx.user_id,
                                                  created_at=ctx.now)).inserted_primary_key[0]
        ctx.log("delegation", did, auth, None, 1, {"from_user_id": holder, "to_user_id": to, "role": role,
                                                  "valid_from": start.isoformat(), "valid_until": until.isoformat(),
                                                  "reason": reason, "on_behalf": not for_self})
        return {"delegation": self._delegation_view(ctx.conn, self._get(ctx.conn, rd, did), ctx.now)}

    def _delegation_revoke(self, ctx, p, ev):
        """delegation.revoke {id, reason} — отменить замещение (строка остаётся в истории с причиной).

        Носитель, замещающий и оформивший — право delegation.revoke (любой вошедший); остальные — delegation.manage.
        Отменённое или уже завершившееся замещение не отменяется повторно.
        """
        only_keys(p, {"id", "reason"})
        did = opt_int(p, "id")
        d = self._get(ctx.conn, role_delegations, did) if did is not None else None
        if not d:
            raise Rejected("not_found", "Замещение не найдено.")
        own = ctx.user_id in (d["from_user_id"], d["to_user_id"], d["created_by"])
        auth = self.policy.check(ctx.conn, ctx.user_id, "delegation.revoke" if own else "delegation.manage", ctx.now)
        if d["revoked_at"] is not None:
            raise Rejected("already_revoked", "Замещение уже отменено.")
        if d["valid_until"] < ctx.now.date():
            raise Rejected("delegation_ended", "Замещение уже завершилось — отменять нечего.")
        reason = req_str(p, "reason", 500)
        ctx.conn.execute(role_delegations.update().where(role_delegations.c.id == did)
                         .values(revoked_at=ctx.now, revoked_by=ctx.user_id, revoke_reason=reason))
        ctx.log("delegation", did, auth, 1, 2, {"revoked": True, "reason": reason, "role": d["role"],
                                               "from_user_id": d["from_user_id"], "to_user_id": d["to_user_id"]})
        return {"delegation": self._delegation_view(ctx.conn, self._get(ctx.conn, role_delegations, did), ctx.now)}

    def _role_label(self, role: str) -> str:
        """Русское название роли для сообщений (из контракта прав; нет — код)."""
        return (self.contracts.permissions.get("role_labels_ru") or {}).get(role) or ROLE_LABELS_RU.get(role, role)

    # ------------------------------------------------------------ pull
    def _pull_delegations(self, conn, uid: str, now) -> dict[str, Any]:
        """Часть pull: замещения (свои или все — для delegation.manage) и покрытие ролей (только delegation.manage).

        Замещения — действующие, запланированные и завершившиеся/отменённые не раньше PULL_ENDED_DAYS дней назад.
        """
        manage = self.policy.can(conn, uid, "delegation.manage", now)
        rd = role_delegations
        today = now.date()
        q = sa.select(rd).where(rd.c.valid_until >= date.fromordinal(today.toordinal() - PULL_ENDED_DAYS))
        if not manage:
            q = q.where(sa.or_(rd.c.from_user_id == uid, rd.c.to_user_id == uid, rd.c.created_by == uid))
        rows = [dict(r._mapping) for r in conn.execute(q.order_by(rd.c.valid_from.desc(), rd.c.id.desc()))]
        own_roles: dict[str, set[str]] = {}

        def holder_roles(user_id: str) -> set[str]:
            """Свои действующие роли носителя (кэш на один pull)."""
            if user_id not in own_roles:
                u = self._get(conn, users, user_id)
                own_roles[user_id] = {r for _, r in active_grants(conn, user_id, now)} if u and u["active"] else set()
            return own_roles[user_id]
        out: dict[str, Any] = {"delegations": [self.v_delegation(d, d["role"] in holder_roles(d["from_user_id"]), today)
                                               for d in rows], "role_coverage": []}
        if manage:
            out["role_coverage"] = self._role_coverage(conn, now)
        return out

    def _role_coverage(self, conn, now) -> list[dict]:
        """Покрытие ролей: у каждой роли из матрицы прав — кто её носит и кто замещает сегодня; состояние none (никого),
        single (один человек — заменить некем) или ok. Роли, которые не встречаются ни в одном правиле, не
        показываются (они пока ни на что не влияют).
        """
        used = {r for rule in self.contracts.permissions["rules"].values() for c in rule.get("any_of", [])
                for r in c.get("global_role", [])}
        roles = [r for r in self.contracts.permissions["roles"] if r in used]
        rg, us, rd = role_grants, users, role_delegations
        holders: dict[str, list[str]] = {r: [] for r in roles}
        for r in conn.execute(sa.select(rg.c.user_id, rg.c.role).join(us, us.c.id == rg.c.user_id)
                              .where(us.c.active == sa.true(), rg.c.revoked_at.is_(None), rg.c.valid_from <= now,
                                     sa.or_(rg.c.valid_until.is_(None), rg.c.valid_until > now))):
            if r.role in holders and r.user_id not in holders[r.role]:
                holders[r.role].append(r.user_id)
        deputies: dict[str, list[dict]] = {r: [] for r in roles}
        today = now.date()
        for d in conn.execute(sa.select(rd).join(us, us.c.id == rd.c.to_user_id)
                              .where(us.c.active == sa.true(), rd.c.revoked_at.is_(None), rd.c.valid_from <= today,
                                     rd.c.valid_until >= today)):
            if d.role in deputies and d.from_user_id in holders.get(d.role, []):
                deputies[d.role].append({"user_id": d.to_user_id, "for_user_id": d.from_user_id,
                                         "valid_until": d.valid_until.isoformat(), "delegation_id": d.id})
        out = []
        for r in roles:
            people = set(holders[r]) | {d["user_id"] for d in deputies[r]}
            state = "none" if not people else ("single" if len(people) == 1 else "ok")
            out.append({"role": r, "holders": holders[r], "deputies": deputies[r], "people": len(people), "state": state,
                        "delegable": r not in dl.NON_DELEGABLE})
        return out

    def _me_roles(self, conn, uid: str, now) -> dict[str, Any]:
        """Роли для «me» в pull: все действующие (свои + замещение), свои отдельно и полученные замещением (за кого,
        до какого числа) — чтобы интерфейс показывал «Согласование закупок (замещение до 30.11)».
        """
        own = [r for _, r in active_grants(conn, uid, now)]
        delegated = []
        for gid, role, did, holder in active_delegated(conn, uid, now):
            d = self._get(conn, role_delegations, did)
            delegated.append({"role": role, "delegation_id": did, "from_user_id": holder,
                              "valid_until": d["valid_until"].isoformat() if d else None})
        roles = list(dict.fromkeys(own + [d["role"] for d in delegated]))
        return {"roles": roles, "own_roles": own, "delegated_roles": delegated}


# Подписи ролей для сообщений сервера (клиент держит такой же словарь для интерфейса — ROLE_LABELS).
ROLE_LABELS_RU = {"admin": "Администратор", "gip": "ГИП", "rp": "РП", "dept_head": "Руководитель отдела",
                  "lead_engineer": "Ведущий инженер", "engineer": "Инженер", "designer": "Конструктор",
                  "technologist": "Технолог", "production": "Производство", "warehouse": "Склад",
                  "procurement": "Закупка", "accountant": "Бухгалтерия", "po_approver": "Согласование закупок"}

