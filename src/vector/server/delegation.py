"""Замещение — передача роли на время (D-34 — глобальные роли; D-35 — роли в проекте) и основания решений.

Владелец: «все и вся должны быть заменяемыми, не должно возникать ситуации, что если нет человека, невозможно было
решить какой-то вопрос или кому-то делегировать» (A-37); «проектные роли — тоже надо иметь возможность замещать»
(A-40). Правила — vector.core.delegation; здесь — команды, представления и часть pull:

* delegation.create {from_user_id?, to_user_id, role, project_id?, valid_from?, valid_until, reason} — оформить
  замещение. Без project_id — глобальная роль (D-34); с project_id — роль в этом проекте: ГИП, РП или участник
  (D-35). Свою роль передаёт любой её носитель (право delegation.create — любой вошедший, роль или назначение
  проверяются здесь); за отсутствующего — руководитель отдела или администратор (право delegation.manage), но не
  себе самому;
* delegation.revoke {id, reason} — отменить: носитель, замещающий (отказаться), оформивший; остальные — с
  delegation.manage;
* pull: delegations — свои (передал, получил, оформил) или все (delegation.manage); role_coverage — покрытие ролей
  (глобальные роли и ГИП/РП каждого проекта: никого / один человек) — только delegation.manage; project_deputies
  — кто сейчас замещает в читаемых проектах (для списка команды проекта).

Обе команды — только при связи: передача полномочий не должна «догонять» офлайн-очередь. Об оформлении и отмене
узнают замещающий, носитель и руководители отдела — уведомлением (vector.server.notify, D-35).
Как замещение участвует в проверке прав — vector.server.authz (active_delegated, active_delegated_project).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

import sqlalchemy as sa

from vector.core import delegation as dl
from vector.core.errors import Rejected

from .authz import Authority, active_delegated, active_delegated_project, active_grants, active_project_roles
from .db import (approval_bases, project_assignments, projects, role_delegation_projects, role_delegations,
                 role_grants, users)
from .validation import iso, only_keys, opt_int, req_str, req_uuid

# Сколько дней после окончания замещения оно ещё приходит в pull (история на экране «Замещения»).
PULL_ENDED_DAYS = 180
# Роли в проекте, которые показываются в покрытии (у участников нет единственного ответственного за проект).
COVERAGE_PROJECT_ROLES = ("gip", "rp")


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

    # ------------------------------------------------------------ область замещения (D-35)
    def _delegation_projects(self, conn, ids: list[int]) -> dict[int, dict]:
        """Проект каждого замещения роли в проекте: {id замещения: {project_id, project_code, project_title}}.
        Замещения глобальных ролей в словарь не попадают.
        """
        if not ids:
            return {}
        rdp = role_delegation_projects
        rows = conn.execute(sa.select(rdp.c.delegation_id, projects.c.id, projects.c.code, projects.c.title)
                            .join(projects, projects.c.id == rdp.c.project_id).where(rdp.c.delegation_id.in_(ids)))
        return {r.delegation_id: {"project_id": r.id, "project_code": r.code, "project_title": r.title} for r in rows}

    # ------------------------------------------------------------ представление
    @staticmethod
    def v_delegation(d: dict, holder_has_role: bool, today: date, project: Optional[dict] = None) -> dict:
        """Замещение для клиента: кто кому какую роль (и в каком проекте — D-35), срок, причина, кто оформил,
        отмена и состояние на сегодня. project_id = None — глобальная роль.
        """
        st = dl.state(d["valid_from"], d["valid_until"], d["revoked_at"] is not None, holder_has_role, today)
        project = project or {}
        return {"id": d["id"], "from_user_id": d["from_user_id"], "to_user_id": d["to_user_id"], "role": d["role"],
                "project_id": project.get("project_id"), "project_code": project.get("project_code"),
                "project_title": project.get("project_title"),
                "valid_from": d["valid_from"].isoformat(), "valid_until": d["valid_until"].isoformat(),
                "reason": d["reason"], "created_by": d["created_by"], "created_at": iso(d["created_at"]),
                "revoked_at": iso(d["revoked_at"]), "revoked_by": d["revoked_by"], "revoke_reason": d["revoke_reason"],
                "state": st, "state_label": dl.STATE_LABELS_RU[st]}

    def _holder_has_role(self, conn, user_id: str, role: str, now, project_id: Optional[str] = None) -> bool:
        """Есть ли у носителя СВОЯ действующая роль (и активна ли его учётная запись): глобальная выдача или — для
        замещения в проекте — назначение с этой ролью в project_id.
        """
        u = self._get(conn, users, user_id)
        if not (u and u["active"]):
            return False
        if project_id is not None:
            return role in [r for _, r in active_project_roles(conn, user_id, project_id, now)]
        return role in [r for _, r in active_grants(conn, user_id, now)]

    def _delegation_view(self, conn, d: dict, now) -> dict:
        """Представление замещения по строке таблицы (состояние — на сегодня)."""
        proj = self._delegation_projects(conn, [d["id"]]).get(d["id"])
        has = self._holder_has_role(conn, d["from_user_id"], d["role"], now, proj and proj["project_id"])
        return self.v_delegation(d, has, now.date(), proj)

    def _scope_label(self, role: str, proj: Optional[dict]) -> str:
        """«Закупка» или «ГИП в проекте П-2026-014» — для сообщений и уведомлений."""
        if proj:
            return dl.scope_label(dl.PROJECT_ROLE_LABELS_RU.get(role, role), proj["project_code"])
        return self._role_label(role)

    # ------------------------------------------------------------ команды
    def _delegation_create(self, ctx, p, ev):
        """delegation.create {from_user_id?, to_user_id, role, project_id?, valid_from?, valid_until, reason}.

        Свою роль (from_user_id не указан или это вы) — право delegation.create и СВОЯ действующая роль: выдача
        глобальной роли или назначение в проект с этой ролью (полученную замещением роль дальше не передают). За
        другого — право delegation.manage (руководитель отдела, администратор), у носителя должна быть своя роль,
        назначить замещающим себя нельзя. Роль «Администратор» не передаётся. Замещающий — активный сотрудник, не
        носитель, без своей такой же роли (в этом проекте — для роли в проекте); пересекающееся по сроку такое же
        замещение — отказ. Уведомления — замещающему, носителю и руководителям отдела.
        """
        only_keys(p, {"from_user_id", "to_user_id", "role", "project_id", "valid_from", "valid_until", "reason"})
        holder = p.get("from_user_id") or ctx.user_id
        if not isinstance(holder, str):
            raise Rejected("invalid_type", "from_user_id — строка.", {"field": "from_user_id"})
        for_self = holder == ctx.user_id
        auth = self.policy.check(ctx.conn, ctx.user_id, "delegation.create" if for_self else "delegation.manage", ctx.now)
        proj = None
        if p.get("project_id") is not None:
            pr = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")
            proj = {"project_id": pr["id"], "project_code": pr["code"], "project_title": pr["title"]}
            role = dl.check_project_role(p.get("role"), self.contracts.permissions["project_roles"])
        else:
            role = dl.check_role(p.get("role"), self.contracts.permissions["roles"])
        pid = proj and proj["project_id"]
        label = self._scope_label(role, proj)
        to = req_uuid(p, "to_user_id")
        if to == holder:
            raise Rejected("self_delegation", "Замещающий — другой сотрудник, не сам носитель роли.", {"field": "to_user_id"})
        if not for_self and to == ctx.user_id:
            raise Rejected("self_assign", "Назначить замещающим себя нельзя: это делает сам носитель роли, другой "
                           "руководитель отдела или администратор (решение — «в четыре глаза»).", {"field": "to_user_id"})
        h = self._get(ctx.conn, users, holder)
        if not h:
            raise Rejected("user_not_found", "Носитель роли не найден.", {"field": "from_user_id"})
        if not self._holder_has_role(ctx.conn, holder, role, ctx.now, pid):
            raise Rejected("role_not_held",
                           (f"У вас нет своей роли «{label}». Передать можно только свою роль — полученную замещением "
                            "роль дальше не передают." if for_self else
                            f"У сотрудника «{h['display_name']}» нет действующей роли «{label}» — передавать нечего."),
                           {"field": "role"})
        t = self._get(ctx.conn, users, to)
        if not t or not t["active"]:
            raise Rejected("user_not_found", "Замещающий не найден или отключён.", {"field": "to_user_id"})
        if self._holder_has_role(ctx.conn, to, role, ctx.now, pid):
            raise Rejected("already_has_role", f"У сотрудника «{t['display_name']}» уже есть своя роль «{label}» — "
                           "замещение не нужно.", {"field": "to_user_id"})
        start, until = dl.check_period(dl.parse_day(p.get("valid_from"), "valid_from"),
                                       dl.parse_day(p.get("valid_until"), "valid_until"), ctx.now.date())
        reason = req_str(p, "reason", 500)
        rd, rdp = role_delegations, role_delegation_projects
        same_scope = (sa.exists().where(rdp.c.delegation_id == rd.c.id, rdp.c.project_id == pid) if pid else
                      ~sa.exists().where(rdp.c.delegation_id == rd.c.id))
        clash = ctx.conn.execute(sa.select(rd.c.id).where(
            rd.c.from_user_id == holder, rd.c.to_user_id == to, rd.c.role == role, rd.c.revoked_at.is_(None),
            rd.c.valid_from <= until, rd.c.valid_until >= start, same_scope)).first()
        if clash:
            raise Rejected("delegation_exists", "Такое замещение на эти даты уже оформлено.", {"delegation_id": clash.id})
        did = ctx.conn.execute(rd.insert().values(from_user_id=holder, to_user_id=to, role=role, valid_from=start,
                                                  valid_until=until, reason=reason, created_by=ctx.user_id,
                                                  created_at=ctx.now)).inserted_primary_key[0]
        if pid:
            ctx.conn.execute(rdp.insert().values(delegation_id=did, project_id=pid))
        ctx.log("delegation", did, auth, None, 1, {"from_user_id": holder, "to_user_id": to, "role": role,
                                                  "project_id": pid, "valid_from": start.isoformat(),
                                                  "valid_until": until.isoformat(), "reason": reason,
                                                  "on_behalf": not for_self})
        self._notify_delegation(ctx, "delegation_created", did, holder, to, label, start, until, reason)
        return {"delegation": self._delegation_view(ctx.conn, self._get(ctx.conn, rd, did), ctx.now)}

    def _delegation_revoke(self, ctx, p, ev):
        """delegation.revoke {id, reason} — отменить замещение (строка остаётся в истории с причиной).

        Носитель, замещающий и оформивший — право delegation.revoke (любой вошедший); остальные — delegation.manage.
        Отменённое или уже завершившееся замещение не отменяется повторно. Уведомления — как при оформлении.
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
        proj = self._delegation_projects(ctx.conn, [did]).get(did)
        ctx.log("delegation", did, auth, 1, 2, {"revoked": True, "reason": reason, "role": d["role"],
                                               "project_id": proj and proj["project_id"],
                                               "from_user_id": d["from_user_id"], "to_user_id": d["to_user_id"]})
        self._notify_delegation(ctx, "delegation_revoked", did, d["from_user_id"], d["to_user_id"],
                                self._scope_label(d["role"], proj), d["valid_from"], d["valid_until"], reason)
        return {"delegation": self._delegation_view(ctx.conn, self._get(ctx.conn, role_delegations, did), ctx.now)}

    def _notify_delegation(self, ctx, kind: str, did: int, holder: str, to: str, label: str, start: date,
                           until: date, reason: str) -> None:
        """Уведомить об оформлении / отмене замещения (D-35): замещающего, носителя и руководителей отдела.

        Автор действия уведомление о своём действии не получает (vector.server.notify._notify).
        """
        h, t = self._user_name(ctx.conn, holder), self._user_name(ctx.conn, to)
        actor = self._user_name(ctx.conn, ctx.user_id)
        period = dl.period_label(start, until)
        heads = self._users_with_role(ctx.conn, "dept_head", ctx.now)
        if kind == "delegation_created":
            self._notify(ctx, [to], kind, f"Вам передана роль «{label}» {period}",
                         f"Вы замещаете: {h}. Причина: {reason}. Оформил(а): {actor}.", "delegation", did)
            self._notify(ctx, [holder], kind, f"За вас оформлено замещение: «{label}» {period}",
                         f"Замещает: {t}. Причина: {reason}. Оформил(а): {actor}.", "delegation", did)
            self._notify(ctx, [u for u in heads if u not in (to, holder)], kind,
                         f"Замещение: {h} → {t}, «{label}»", f"{period[0].upper()}{period[1:]}. Причина: {reason}. "
                         f"Оформил(а): {actor}.", "delegation", did)
        else:
            self._notify(ctx, [to, holder] + [u for u in heads if u not in (to, holder)], kind,
                         f"Замещение отменено: {h} → {t}, «{label}»",
                         f"Причина отмены: {reason}. Отменил(а): {actor}.", "delegation", did)

    def _role_label(self, role: str) -> str:
        """Русское название роли для сообщений (из контракта прав; нет — код)."""
        return (self.contracts.permissions.get("role_labels_ru") or {}).get(role) or ROLE_LABELS_RU.get(role, role)

    # ------------------------------------------------------------ pull
    def _pull_delegations(self, conn, uid: str, now, readable_projects: Optional[list[str]] = None) -> dict[str, Any]:
        """Часть pull: замещения (свои или все — для delegation.manage), покрытие ролей (только delegation.manage)
        и кто сейчас замещает в читаемых проектах (project_deputies — для списка команды проекта).

        Замещения — действующие, запланированные и завершившиеся/отменённые не раньше PULL_ENDED_DAYS дней назад.
        """
        manage = self.policy.can(conn, uid, "delegation.manage", now)
        rd = role_delegations
        today = now.date()
        q = sa.select(rd).where(rd.c.valid_until >= date.fromordinal(today.toordinal() - PULL_ENDED_DAYS))
        if not manage:
            q = q.where(sa.or_(rd.c.from_user_id == uid, rd.c.to_user_id == uid, rd.c.created_by == uid))
        rows = [dict(r._mapping) for r in conn.execute(q.order_by(rd.c.valid_from.desc(), rd.c.id.desc()))]
        scopes = self._delegation_projects(conn, [d["id"] for d in rows])
        cache: dict[tuple, bool] = {}

        def has_role(d: dict) -> bool:
            """Есть ли у носителя своя роль замещения (кэш на один pull)."""
            proj = scopes.get(d["id"])
            key = (d["from_user_id"], d["role"], proj and proj["project_id"])
            if key not in cache:
                cache[key] = self._holder_has_role(conn, *key[:2], now, key[2])
            return cache[key]
        out: dict[str, Any] = {"delegations": [self.v_delegation(d, has_role(d), today, scopes.get(d["id"]))
                                               for d in rows], "role_coverage": [], "project_deputies": []}
        if manage:
            out["role_coverage"] = self._role_coverage(conn, now)
        out["project_deputies"] = self._project_deputies(conn, readable_projects or [], now)
        return out

    def _project_deputies(self, conn, project_ids: list[str], now) -> list[dict]:
        """Действующие сейчас замещения ролей в проектах project_ids: кто, за кого, какая роль, до какого числа."""
        if not project_ids:
            return []
        rd, rdp = role_delegations, role_delegation_projects
        today = now.date()
        out = []
        for r in conn.execute(sa.select(rd, rdp.c.project_id).join(rdp, rdp.c.delegation_id == rd.c.id)
                              .where(rdp.c.project_id.in_(project_ids), rd.c.revoked_at.is_(None),
                                     rd.c.valid_from <= today, rd.c.valid_until >= today).order_by(rd.c.id)):
            # Тем же правилом, что права: действует, только пока у носителя есть назначение (и учётка активна).
            if any(x[2] == r.id for x in active_delegated_project(conn, r.to_user_id, r.project_id, now)):
                out.append({"delegation_id": r.id, "project_id": r.project_id, "user_id": r.to_user_id,
                            "for_user_id": r.from_user_id, "project_role": r.role,
                            "valid_until": r.valid_until.isoformat()})
        return out

    def _role_coverage(self, conn, now) -> list[dict]:
        """Покрытие ролей: у каждой роли — кто её носит и кто замещает сегодня; состояние none (никого), single (один
        человек — заменить некем) или ok.

        Глобальные роли — те, что встречаются в правилах матрицы прав (остальные пока ни на что не влияют).
        Роли в проекте (D-35) — ГИП и РП каждого проекта (scope = "project", project_id / project_code): ГИП и РП
        решают за проект, и их отсутствие не должно его останавливать.
        """
        used = {r for rule in self.contracts.permissions["rules"].values() for c in rule.get("any_of", [])
                for r in c.get("global_role", [])}
        roles = [r for r in self.contracts.permissions["roles"] if r in used]
        rg, us, rd, rdp = role_grants, users, role_delegations, role_delegation_projects
        holders: dict[str, list[str]] = {r: [] for r in roles}
        for r in conn.execute(sa.select(rg.c.user_id, rg.c.role).join(us, us.c.id == rg.c.user_id)
                              .where(us.c.active == sa.true(), rg.c.revoked_at.is_(None), rg.c.valid_from <= now,
                                     sa.or_(rg.c.valid_until.is_(None), rg.c.valid_until > now))):
            if r.role in holders and r.user_id not in holders[r.role]:
                holders[r.role].append(r.user_id)
        deputies: dict[str, list[dict]] = {r: [] for r in roles}
        today = now.date()
        scoped = sa.exists().where(rdp.c.delegation_id == rd.c.id)
        for d in conn.execute(sa.select(rd).join(us, us.c.id == rd.c.to_user_id)
                              .where(us.c.active == sa.true(), rd.c.revoked_at.is_(None), rd.c.valid_from <= today,
                                     rd.c.valid_until >= today, ~scoped)):
            if d.role in deputies and d.from_user_id in holders.get(d.role, []):
                deputies[d.role].append({"user_id": d.to_user_id, "for_user_id": d.from_user_id,
                                         "valid_until": d.valid_until.isoformat(), "delegation_id": d.id})
        out = []
        for r in roles:
            out.append(self._coverage_row(r, holders[r], deputies[r], r not in dl.NON_DELEGABLE))
        out.extend(self._project_coverage(conn, now))
        return out

    @staticmethod
    def _coverage_row(role: str, holders: list[str], deputies: list[dict], delegable: bool,
                      project: Optional[dict] = None) -> dict:
        """Строка покрытия: носители, замещающие, сколько всего людей и состояние none / single / ok."""
        people = set(holders) | {d["user_id"] for d in deputies}
        state = "none" if not people else ("single" if len(people) == 1 else "ok")
        row = {"role": role, "holders": holders, "deputies": deputies, "people": len(people), "state": state,
               "delegable": delegable, "scope": "project" if project else "global", "project_id": None,
               "project_code": None}
        if project:
            row.update(project_id=project["id"], project_code=project["code"])
        return row

    def _project_coverage(self, conn, now) -> list[dict]:
        """Покрытие ГИП и РП по каждому проекту (D-35): назначенные и замещающие на сегодня."""
        pa, us = project_assignments, users
        out = []
        for pr in conn.execute(sa.select(projects.c.id, projects.c.code).order_by(projects.c.code)):
            holders: dict[str, list[str]] = {r: [] for r in COVERAGE_PROJECT_ROLES}
            for a in conn.execute(sa.select(pa.c.user_id, pa.c.project_role).join(us, us.c.id == pa.c.user_id)
                                  .where(pa.c.project_id == pr.id, us.c.active == sa.true(),
                                         pa.c.revoked_at.is_(None), pa.c.valid_from <= now,
                                         sa.or_(pa.c.valid_until.is_(None), pa.c.valid_until > now))):
                if a.project_role in holders and a.user_id not in holders[a.project_role]:
                    holders[a.project_role].append(a.user_id)
            deputies: dict[str, list[dict]] = {r: [] for r in COVERAGE_PROJECT_ROLES}
            for d in self._project_deputies(conn, [pr.id], now):
                if d["project_role"] in deputies:
                    deputies[d["project_role"]].append({"user_id": d["user_id"], "for_user_id": d["for_user_id"],
                                                        "valid_until": d["valid_until"],
                                                        "delegation_id": d["delegation_id"]})
            for r in COVERAGE_PROJECT_ROLES:
                out.append(self._coverage_row(r, holders[r], deputies[r], True, {"id": pr.id, "code": pr.code}))
        return out

    def _me_roles(self, conn, uid: str, now) -> dict[str, Any]:
        """Роли для «me» в pull: все действующие глобальные (свои + замещение), свои отдельно, полученные
        замещением (за кого, до какого числа) и роли в проектах по замещению (D-35) — чтобы интерфейс показывал
        «Согласование закупок (замещение до 30.11)» и «ГИП в проекте П-2026-014 (замещение до 10.12)».
        """
        own = [r for _, r in active_grants(conn, uid, now)]
        delegated = []
        for gid, role, did, holder in active_delegated(conn, uid, now):
            d = self._get(conn, role_delegations, did)
            delegated.append({"role": role, "delegation_id": did, "from_user_id": holder,
                              "valid_until": d["valid_until"].isoformat() if d else None})
        roles = list(dict.fromkeys(own + [d["role"] for d in delegated]))
        rd, rdp = role_delegations, role_delegation_projects
        today = now.date()
        pids = [r.project_id for r in conn.execute(
            sa.select(rdp.c.project_id).join(rd, rd.c.id == rdp.c.delegation_id)
            .where(rd.c.to_user_id == uid, rd.c.revoked_at.is_(None), rd.c.valid_from <= today,
                   rd.c.valid_until >= today).distinct())]
        proj_delegated = []
        for pid in sorted(pids):
            pr = self._get(conn, projects, pid)
            for aid, role, did, holder in active_delegated_project(conn, uid, pid, now):
                d = self._get(conn, role_delegations, did)
                proj_delegated.append({"role": role, "project_id": pid, "project_code": pr["code"] if pr else None,
                                       "delegation_id": did, "from_user_id": holder,
                                       "valid_until": d["valid_until"].isoformat() if d else None})
        return {"roles": roles, "own_roles": own, "delegated_roles": delegated,
                "delegated_project_roles": proj_delegated}


# Подписи ролей для сообщений сервера (клиент держит такой же словарь для интерфейса — ROLE_LABELS).
ROLE_LABELS_RU = {"admin": "Администратор", "gip": "ГИП", "rp": "РП", "dept_head": "Руководитель отдела",
                  "lead_engineer": "Ведущий инженер", "engineer": "Инженер", "designer": "Конструктор",
                  "technologist": "Технолог", "production": "Производство", "warehouse": "Склад",
                  "procurement": "Закупка", "accountant": "Бухгалтерия", "po_approver": "Согласование закупок",
                  "otk": "Технический контроль (ОТК)"}
