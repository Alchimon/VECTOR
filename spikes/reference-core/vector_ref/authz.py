"""Авторизация «действие × объект × контекст» в момент эффекта.

Вызывается сервером внутри той же транзакции (BEGIN IMMEDIATE), что и запись эффекта,
поэтому отзыв роли не может «проскочить» между проверкой и фиксацией (fencing в пределах
одной БД). Роль, присланная клиентом, не используется никогда.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from .errors import Denied


@dataclass(frozen=True)
class Authority:
    kind: str          # 'global_role' | 'project_role'
    role: str
    ref_id: int        # id выдачи роли или назначения (= generation)

    def describe(self) -> str:
        src = "grant" if self.kind == "global_role" else "assignment"
        return f"{self.kind}:{self.role}#{src}:{self.ref_id}"


def active_grants(conn: sqlite3.Connection, user_id: str, now: str) -> list[tuple[int, str]]:
    rows = conn.execute(
        """SELECT id, role FROM role_grants
           WHERE user_id=? AND revoked_at IS NULL AND valid_from<=?
             AND (valid_until IS NULL OR valid_until>?)
           ORDER BY id""",
        (user_id, now, now),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def active_project_roles(conn: sqlite3.Connection, user_id: str, project_id: str, now: str) -> list[tuple[int, str]]:
    rows = conn.execute(
        """SELECT id, project_role FROM project_assignments
           WHERE user_id=? AND project_id=? AND revoked_at IS NULL AND valid_from<=?
             AND (valid_until IS NULL OR valid_until>?)
           ORDER BY id""",
        (user_id, project_id, now, now),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def grant_is_active(conn: sqlite3.Connection, grant_id: int, user_id: str, role_set: list[str], now: str) -> bool:
    row = conn.execute(
        """SELECT 1 FROM role_grants WHERE id=? AND user_id=? AND revoked_at IS NULL
           AND valid_from<=? AND (valid_until IS NULL OR valid_until>?)""",
        (grant_id, user_id, now, now),
    ).fetchone()
    if not row:
        return False
    role = conn.execute("SELECT role FROM role_grants WHERE id=?", (grant_id,)).fetchone()[0]
    return role in role_set


class Policy:
    def __init__(self, permissions: dict):
        self.rules: dict = permissions["rules"]

    def rule(self, action: str) -> dict:
        r = self.rules.get(action)
        if r is None:
            # Нет правила — нет доступа (fail-closed).
            raise Denied("no_rule", f"Действие «{action}» не описано в матрице прав — доступ запрещён.")
        if "same_as" in r:
            return self.rule(r["same_as"])
        return r

    def roles_for(self, action: str, kind: str) -> list[str]:
        out: list[str] = []
        for cond in self.rule(action)["any_of"]:
            out.extend(cond.get(kind, []))
        return out

    def check(self, conn: sqlite3.Connection, user_id: str, action: str, now: str,
              project_id: Optional[str] = None) -> Authority:
        rule = self.rule(action)
        grants = None
        proles = None
        for cond in rule["any_of"]:
            if "global_role" in cond:
                if grants is None:
                    grants = active_grants(conn, user_id, now)
                for gid, role in grants:
                    if role in cond["global_role"]:
                        return Authority("global_role", role, gid)
            if "project_role" in cond and project_id is not None:
                if proles is None:
                    proles = active_project_roles(conn, user_id, project_id, now)
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
