"""Авторизация «действие × объект × контекст» в момент эффекта (та же транзакция, что и запись).

Роль из клиентского запроса не используется никогда. Нет правила — нет доступа (fail-closed).
Generation = id выдачи роли / назначения; повторная выдача создаёт новый id.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from vector.core.errors import Denied

from .db import project_assignments as pa
from .db import role_grants as rg


@dataclass(frozen=True)
class Authority:
    kind: str      # global_role | project_role | self
    role: str
    ref_id: int

    def describe(self) -> str:
        if self.kind == "self":
            return "self"
        src = "grant" if self.kind == "global_role" else "assignment"
        return f"{self.kind}:{self.role}#{src}:{self.ref_id}"


def _active(table, now):
    return sa.and_(table.c.revoked_at.is_(None), table.c.valid_from <= now,
                   sa.or_(table.c.valid_until.is_(None), table.c.valid_until > now))


def active_grants(conn: Connection, user_id: str, now: datetime) -> list[tuple[int, str]]:
    rows = conn.execute(sa.select(rg.c.id, rg.c.role).where(rg.c.user_id == user_id, _active(rg, now)).order_by(rg.c.id))
    return [(r.id, r.role) for r in rows]


def active_project_roles(conn: Connection, user_id: str, project_id: str, now: datetime) -> list[tuple[int, str]]:
    rows = conn.execute(sa.select(pa.c.id, pa.c.project_role)
                        .where(pa.c.user_id == user_id, pa.c.project_id == project_id, _active(pa, now)).order_by(pa.c.id))
    return [(r.id, r.project_role) for r in rows]


def grant_is_active(conn: Connection, grant_id: int, user_id: str, roles: list[str], now: datetime) -> bool:
    row = conn.execute(sa.select(rg.c.role).where(rg.c.id == grant_id, rg.c.user_id == user_id, _active(rg, now))).first()
    return bool(row) and row.role in roles


class Policy:
    def __init__(self, permissions: dict):
        self.rules: dict = permissions["rules"]

    def rule(self, action: str) -> dict:
        r = self.rules.get(action)
        if r is None:
            raise Denied("no_rule", f"Действие «{action}» не описано в матрице прав — доступ запрещён.")
        return self.rule(r["same_as"]) if "same_as" in r else r

    def roles_for(self, action: str, kind: str) -> list[str]:
        return [x for c in self.rule(action)["any_of"] for x in c.get(kind, [])]

    def check(self, conn: Connection, user_id: str, action: str, now: datetime, project_id: str | None = None) -> Authority:
        rule = self.rule(action)
        grants = proles = None
        for cond in rule["any_of"]:
            if cond.get("authenticated"):
                return Authority("self", "self", 0)
            if "global_role" in cond:
                grants = grants if grants is not None else active_grants(conn, user_id, now)
                for gid, role in grants:
                    if role in cond["global_role"]:
                        return Authority("global_role", role, gid)
            if "project_role" in cond and project_id is not None:
                proles = proles if proles is not None else active_project_roles(conn, user_id, project_id, now)
                for aid, role in proles:
                    if role in cond["project_role"]:
                        return Authority("project_role", role, aid)
        raise Denied("no_authority",
                     f"Нет действующего полномочия на «{action}». Роль или назначение отсутствуют, истекли или отозваны.",
                     {"action": action, "project_id": project_id})

    def can(self, conn, user_id, action, now, project_id=None) -> bool:
        try:
            self.check(conn, user_id, action, now, project_id)
            return True
        except Denied:
            return False
