"""Авторизация «действие × объект × контекст» в момент эффекта (та же транзакция, что и запись).

Роль из клиентского запроса не используется никогда. Нет правила — нет доступа (fail-closed).
Generation = id выдачи роли / назначения; повторная выдача создаёт новый id.

Замещение (D-34): глобальная роль действует и у замещающего, пока действует замещение (срок, не отменено) и пока
у носителя есть своя действующая выдача этой роли (без цепочек: роль, полученная замещением, дальше не передаётся).
Основание такого действия — Authority(kind="delegated_role", ref_id = выдача носителя, delegation_id,
on_behalf_of = носитель): в журнале видно, кто действовал и за кого.

Замещение роли в проекте (D-35, A-40): роль gip / rp / participant, переданная в конкретном проекте
(role_delegation_projects), действует у замещающего только в этом проекте, пока у носителя есть действующее
назначение с этой ролью в нём. Основание — Authority(kind="delegated_project_role", ref_id = назначение носителя).
Такое замещение НИКОГДА не даёт глобальной роли (active_delegated его исключает, даже если код роли совпадает с
глобальной — gip, rp).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from vector.core.errors import Denied

from .db import project_assignments as pa
from .db import role_delegation_projects as rdp
from .db import role_delegations as rd
from .db import role_grants as rg
from .db import users as us


@dataclass(frozen=True)
class Authority:
    """Найденное основание доступа — что именно разрешило действие. Записывается в журнал аудита и показывается в
    истории объекта («Основание доступа: роль „Закупка“ (выдача №9)»).

    * kind — global_role (глобальная роль пользователя), delegated_role (роль, полученная замещением, D-34),
      project_role (назначение в проект), delegated_project_role (роль в проекте по замещению, D-35) или self
      (действие над своей учётной записью);
    * role — код роли (gip, rp, warehouse…);
    * ref_id — id выдачи роли / назначения (для замещения — выдача роли или назначение НОСИТЕЛЯ). Отзыв и повторная выдача дают
      новый id, поэтому старые согласования не «оживают» (решение владельца);
    * delegation_id, on_behalf_of — замещение и носитель роли (только для delegated_role / delegated_project_role).
    """
    kind: str      # global_role | delegated_role | project_role | delegated_project_role | self
    role: str
    ref_id: int
    delegation_id: Optional[int] = None
    on_behalf_of: Optional[str] = None

    def describe(self) -> str:
        """Короткая машинная запись основания для журнала: «global_role:warehouse#grant:9»,
        «delegated_role:po_approver#grant:9#delegation:3», «delegated_project_role:gip#assignment:5#delegation:4».
        """
        if self.kind == "self":
            return "self"
        if self.kind == "delegated_role":
            return f"{self.kind}:{self.role}#grant:{self.ref_id}#delegation:{self.delegation_id}"
        if self.kind == "delegated_project_role":
            return f"{self.kind}:{self.role}#assignment:{self.ref_id}#delegation:{self.delegation_id}"
        src = "grant" if self.kind == "global_role" else "assignment"
        return f"{self.kind}:{self.role}#{src}:{self.ref_id}"


def _active(table, now):
    """Условие SQL «запись действует сейчас»: не отозвана, срок начался и ещё не истёк. Общее для выдач ролей и
    назначений в проект.
    """
    return sa.and_(table.c.revoked_at.is_(None), table.c.valid_from <= now,
                   sa.or_(table.c.valid_until.is_(None), table.c.valid_until > now))


def active_grants(conn: Connection, user_id: str, now: datetime) -> list[tuple[int, str]]:
    """Действующие глобальные роли пользователя на момент now: список (id выдачи, роль)."""
    rows = conn.execute(sa.select(rg.c.id, rg.c.role).where(rg.c.user_id == user_id, _active(rg, now)).order_by(rg.c.id))
    return [(r.id, r.role) for r in rows]


def active_delegated(conn: Connection, user_id: str, now: datetime) -> list[tuple[int, str, int, str]]:
    """Роли, полученные пользователем замещением и действующие на момент now (D-34).

    Список (id выдачи носителя, роль, id замещения, id носителя). Замещение действует, если: срок включает
    сегодняшнюю дату сервера, оно не отменено, учётная запись носителя активна и у носителя есть СВОЯ действующая
    выдача этой роли (замещение замещения не считается — цепочек нет). Замещения роли в проекте (D-35) сюда не
    попадают никогда — они действуют только в своём проекте (active_delegated_project).
    """
    today = now.date()
    scoped = sa.exists().where(rdp.c.delegation_id == rd.c.id)
    q = (sa.select(rg.c.id.label("gid"), rd.c.role, rd.c.id.label("did"), rd.c.from_user_id)
         .select_from(rd.join(rg, sa.and_(rg.c.user_id == rd.c.from_user_id, rg.c.role == rd.c.role))
                      .join(us, us.c.id == rd.c.from_user_id))
         .where(rd.c.to_user_id == user_id, rd.c.revoked_at.is_(None), rd.c.valid_from <= today,
                rd.c.valid_until >= today, us.c.active == sa.true(), _active(rg, now), ~scoped)
         .order_by(rd.c.id, rg.c.id))
    out, seen = [], set()
    for r in conn.execute(q):
        if r.did not in seen:            # у носителя может быть несколько выдач одной роли — берём первую
            seen.add(r.did)
            out.append((r.gid, r.role, r.did, r.from_user_id))
    return out


def effective_roles(conn: Connection, user_id: str, now: datetime) -> list[str]:
    """Все действующие глобальные роли пользователя: свои и полученные замещением (без повторов, по порядку)."""
    out: list[str] = []
    for role in [r for _, r in active_grants(conn, user_id, now)] + [r for _, r, _, _ in active_delegated(conn, user_id, now)]:
        if role not in out:
            out.append(role)
    return out


def active_project_roles(conn: Connection, user_id: str, project_id: str, now: datetime) -> list[tuple[int, str]]:
    """Действующие роли пользователя в конкретном проекте (ГИП, РП, участник): список (id назначения, роль)."""
    rows = conn.execute(sa.select(pa.c.id, pa.c.project_role)
                        .where(pa.c.user_id == user_id, pa.c.project_id == project_id, _active(pa, now)).order_by(pa.c.id))
    return [(r.id, r.project_role) for r in rows]


def active_delegated_project(conn: Connection, user_id: str, project_id: str,
                             now: datetime) -> list[tuple[int, str, int, str]]:
    """Роли в проекте project_id, полученные пользователем замещением и действующие сейчас (D-35).

    Список (id назначения носителя, роль в проекте, id замещения, id носителя). Условия те же, что у глобального
    замещения: срок включает сегодня, не отменено, учётная запись носителя активна, у носителя есть СВОЁ действующее
    назначение с этой ролью именно в этом проекте (назначение не создаётся замещением — цепочек нет).
    """
    today = now.date()
    q = (sa.select(pa.c.id.label("aid"), rd.c.role, rd.c.id.label("did"), rd.c.from_user_id)
         .select_from(rd.join(rdp, rdp.c.delegation_id == rd.c.id)
                      .join(pa, sa.and_(pa.c.user_id == rd.c.from_user_id, pa.c.project_id == rdp.c.project_id,
                                        pa.c.project_role == rd.c.role))
                      .join(us, us.c.id == rd.c.from_user_id))
         .where(rd.c.to_user_id == user_id, rdp.c.project_id == project_id, rd.c.revoked_at.is_(None),
                rd.c.valid_from <= today, rd.c.valid_until >= today, us.c.active == sa.true(), _active(pa, now))
         .order_by(rd.c.id, pa.c.id))
    out, seen = [], set()
    for r in conn.execute(q):
        if r.did not in seen:
            seen.add(r.did)
            out.append((r.aid, r.role, r.did, r.from_user_id))
    return out


def project_member(conn: Connection, user_id: str, project_id: str, now: datetime) -> bool:
    """Работает ли пользователь в проекте сейчас: своё назначение или роль в проекте по замещению (D-35)."""
    return bool(active_project_roles(conn, user_id, project_id, now)
                or active_delegated_project(conn, user_id, project_id, now))


def grant_is_active(conn: Connection, grant_id: int, user_id: str, roles: list[str], now: datetime) -> bool:
    """Действует ли ещё конкретная выдача роли. Нужна, чтобы проверить, что основание, на котором сделано согласование,
    не отозвано.
    """
    row = conn.execute(sa.select(rg.c.role).where(rg.c.id == grant_id, rg.c.user_id == user_id, _active(rg, now))).first()
    return bool(row) and row.role in roles


class Policy:
    """Исполнитель матрицы прав (contracts/permissions.slice1.json).

    Правило действия — список условий any_of: достаточно выполнить одно. Условие — глобальная роль, роль в проекте
    (только если команда относится к проекту — передан project_id) или «любой вошедший» (authenticated). Глобальная
    роль с признаком in_project (D-38) действует только у участника проекта, к которому относится команда. Нет правила
    — отказ (fail-closed). Проверка идёт в той же транзакции, что и запись, — отзыв роли действует немедленно.
    """
    def __init__(self, permissions: dict):
        """Принимает загруженный контракт прав; сами роли пользователей читаются из БД при каждой проверке."""
        self.rules: dict = permissions["rules"]

    def rule(self, action: str) -> dict:
        """Правило действия; same_as — ссылка на правило другого действия (например, stock.issue = как stock.receive).
        Нет правила — Denied.
        """
        r = self.rules.get(action)
        if r is None:
            raise Denied("no_rule", f"Действие «{action}» не описано в матрице прав — доступ запрещён.")
        return self.rule(r["same_as"]) if "same_as" in r else r

    def roles_for(self, action: str, kind: str) -> list[str]:
        """Какие роли (global_role или project_role) упомянуты в правиле — для подсказок клиенту, не для решения."""
        return [x for c in self.rule(action)["any_of"] for x in c.get(kind, [])]

    def check(self, conn: Connection, user_id: str, action: str, now: datetime, project_id: str | None = None) -> Authority:
        """Проверить право user_id на action сейчас и вернуть основание (Authority) или поднять Denied.

        Важно: для проектных действий обязательно передавать project_id — без него роли в проекте не учитываются
        (это защищает от доступа «к любому проекту» по роли в одном). Глобальные роли и назначения читаются лениво —
        только если до них дошла очередь.
        """
        rule = self.rule(action)
        grants = proles = deleg = pdeleg = None
        for cond in rule["any_of"]:
            if cond.get("authenticated"):
                return Authority("self", "self", 0)
            if "global_role" in cond and cond.get("in_project") and (
                    project_id is None or not project_member(conn, user_id, project_id, now)):
                # Условие «глобальная роль, но только в своём проекте» (D-38): пользователь не участник проекта —
                # его глобальная роль здесь не действует (переходим к следующему условию).
                pass
            elif "global_role" in cond:
                grants = grants if grants is not None else active_grants(conn, user_id, now)
                for gid, role in grants:
                    if role in cond["global_role"]:
                        return Authority("global_role", role, gid)
                # Своей роли нет — роль, полученная замещением (D-34); своя всегда важнее (основание точнее).
                deleg = deleg if deleg is not None else active_delegated(conn, user_id, now)
                for gid, role, did, holder in deleg:
                    if role in cond["global_role"]:
                        return Authority("delegated_role", role, gid, did, holder)
            if "project_role" in cond and project_id is not None:
                proles = proles if proles is not None else active_project_roles(conn, user_id, project_id, now)
                for aid, role in proles:
                    if role in cond["project_role"]:
                        return Authority("project_role", role, aid)
                # Своего назначения нет — роль в этом проекте, полученная замещением (D-35).
                pdeleg = pdeleg if pdeleg is not None else active_delegated_project(conn, user_id, project_id, now)
                for aid, role, did, holder in pdeleg:
                    if role in cond["project_role"]:
                        return Authority("delegated_project_role", role, aid, did, holder)
        raise Denied("no_authority",
                     f"Нет действующего полномочия на «{action}». Роль или назначение отсутствуют, истекли или отозваны.",
                     {"action": action, "project_id": project_id})

    def can(self, conn, user_id, action, now, project_id=None) -> bool:
        """Тот же check, но ответ «да/нет» без исключения — для фильтрации списков в pull и подсказок интерфейсу."""
        try:
            self.check(conn, user_id, action, now, project_id)
            return True
        except Denied:
            return False
