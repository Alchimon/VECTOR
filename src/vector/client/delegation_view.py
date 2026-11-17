"""Представление раздела «Замещения» для интерфейса (D-34, ответ владельца на A-37; роли в проекте — D-35, A-40) —
чистые функции без Qt.

Данные приходят снимком pull (delegations, role_coverage, me) и офлайн только читаются: оформить и отменить
замещение можно лишь при связи с сервером (команды online_required). Здесь — подписи, сроки, кто что может
(подсказка; решает всегда сервер) и предупреждения покрытия ролей («роль у одного человека — заменить некем»).

Роль в проекте (ГИП, РП, участник) в форме — пункт «ГИП в проекте П-2026-014» с ключом «gip@<id проекта>»: форма
раскладывает ключ на role и project_id (role_key / split_role_key).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from vector.core import delegation as dl

# Состояния, в которых замещение ещё можно отменить (завершённое и отменённое — только история).
REVOCABLE = ("planned", "active", "suspended")
COVERAGE_TONES = {"none": "danger", "single": "warning", "ok": "success"}
COVERAGE_TEXTS = {"none": "Нет ни одного человека с этой ролью",
                  "single": "Роль у одного человека — на время его отсутствия оформите замещение",
                  "ok": "Есть кем заменить"}


def _day(s: Optional[str]) -> str:
    """«2026-11-30» → «30.11.2026» (пусто — «—»)."""
    if not s:
        return "—"
    try:
        return date.fromisoformat(s[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return s


def period_label(valid_from: Optional[str], valid_until: Optional[str]) -> str:
    """Срок замещения для списка: «16.11.2026 — 30.11.2026» (обе даты включительно)."""
    return f"{_day(valid_from)} — {_day(valid_until)}"


def role_key(role: str, project_id: Optional[str] = None) -> str:
    """Ключ пункта «Роль» в форме: «po_approver» (глобальная роль) или «gip@<id проекта>» (роль в проекте)."""
    return f"{role}@{project_id}" if project_id else role


def split_role_key(key: str) -> tuple[str, str]:
    """Ключ формы → (роль, id проекта или ""): обратное к role_key."""
    role, _, pid = (key or "").partition("@")
    return role, pid


def scoped_label(role: str, project_code: Optional[str], labels: dict[str, str]) -> str:
    """«Закупка» для глобальной роли или «ГИП в проекте П-2026-014» для роли в проекте (D-35)."""
    if project_code:
        return dl.scope_label(dl.PROJECT_ROLE_LABELS_RU.get(role, role), project_code)
    return labels.get(role, role)


def role_labels(me: dict, labels: dict[str, str]) -> list[str]:
    """Подписи ролей текущего пользователя: свои — как есть, полученные замещением — с пометкой срока
    («Согласование закупок (замещение до 30.11.2026)»), роли в проектах по замещению — «ГИП в проекте П-2026-014
    (замещение до 10.12.2026)» (D-35). Старый кэш без own_roles — все роли как свои.
    """
    roles = me.get("roles") or []
    delegated = {d["role"]: d for d in me.get("delegated_roles") or []}
    own = set(me.get("own_roles") if me.get("own_roles") is not None else roles)
    out = []
    for r in roles:
        lab = labels.get(r, r)
        if r not in own and r in delegated:
            lab += f" (замещение до {_day(delegated[r].get('valid_until'))})"
        out.append(lab)
    for d in me.get("delegated_project_roles") or []:
        out.append(f"{scoped_label(d['role'], d.get('project_code') or '?', labels)} "
                   f"(замещение до {_day(d.get('valid_until'))})")
    return out


def delegations_view(delegations: list[dict], coverage: list[dict], people: dict[str, str], me: dict,
                     labels: dict[str, str], online: bool, can_manage: bool,
                     assignments: Optional[list[dict]] = None,
                     projects: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Всё для раздела «Замещения».

    * received / given — замещения, где я замещающий / носитель роли; others — остальные (видит руководитель отдела
      и администратор); у замещения роли в проекте подпись «ГИП в проекте П-…»;
    * role_options — мои СВОИ роли, которые можно передать (кроме «Администратор»): глобальные и мои назначения в
      проектах (assignments; projects — коды проектов по id); all_role_options — для оформления за сотрудника:
      все глобальные роли и роли каждого известного проекта; people_options — кого назначить;
    * coverage — роли, у которых нет никого или ровно один человек (сначала «никого»), с подписями людей;
      warnings — сколько таких ролей (бейдж в меню у руководителя отдела);
    * can_create / can_manage — подсказка для кнопок (без связи — нельзя: команды только онлайн).
    """
    uid = me.get("user_id")
    own = me.get("own_roles") if me.get("own_roles") is not None else (me.get("roles") or [])
    projects = projects or {}

    def name(u: Optional[str]) -> str:
        return people.get(u, "—") if u else "—"

    def row(d: dict) -> dict:
        mine = uid in (d.get("from_user_id"), d.get("to_user_id"), d.get("created_by"))
        st = d.get("state")
        return {**d, "from_name": name(d.get("from_user_id")), "to_name": name(d.get("to_user_id")),
                "created_name": name(d.get("created_by")),
                "role_label": scoped_label(d["role"], d.get("project_code"), labels),
                "period": period_label(d.get("valid_from"), d.get("valid_until")),
                "tone": dl.STATE_TONES.get(st, "neutral"),
                "on_behalf": d.get("created_by") not in (None, d.get("from_user_id")),
                "can_revoke": online and st in REVOCABLE and (mine or can_manage)}
    rows = [row(d) for d in delegations or []]
    received = [r for r in rows if r["to_user_id"] == uid]
    given = [r for r in rows if r["from_user_id"] == uid and r["to_user_id"] != uid]
    others = [r for r in rows if uid not in (r["to_user_id"], r["from_user_id"])]
    cov = []
    for c in coverage or []:
        if c.get("state") == "ok":
            continue
        cov.append({**c, "label": scoped_label(c["role"], c.get("project_code"), labels),
                    "role_key": role_key(c["role"], c.get("project_id")),
                    "tone": COVERAGE_TONES.get(c["state"], "neutral"),
                    "text": COVERAGE_TEXTS.get(c["state"], "") if c.get("delegable", True)
                    else "Роль у одного человека; замещением не передаётся — выдайте её второму сотруднику",
                    "holder_names": [name(u) for u in c.get("holders") or []],
                    "holder_id": (c.get("holders") or [None])[0]})
    cov.sort(key=lambda c: (c["state"] != "none", c["label"]))
    role_opts = [{"id": r, "label": labels.get(r, r)} for r in own if r not in dl.NON_DELEGABLE]
    # Свои роли в проектах (D-35): по моим действующим назначениям из кэша.
    for a in sorted((a for a in assignments or [] if a.get("user_id") == uid),
                    key=lambda a: (projects.get(a["project_id"], ""), a["project_role"])):
        role_opts.append({"id": role_key(a["project_role"], a["project_id"]),
                          "label": scoped_label(a["project_role"], projects.get(a["project_id"], "?"), labels)})
    all_opts = [{"id": r, "label": labels.get(r, r)} for r in labels if r not in dl.NON_DELEGABLE]
    for pid, code in sorted(projects.items(), key=lambda kv: kv[1]):
        all_opts += [{"id": role_key(r, pid), "label": scoped_label(r, code, labels)} for r in dl.PROJECT_ROLE_LABELS_RU]
    people_opts = [{"id": k, "label": v} for k, v in sorted(people.items(), key=lambda kv: kv[1]) if k != uid]
    active_received = [r for r in received if r["state"] == "active"]
    return {"received": received, "given": given, "others": others, "coverage": cov, "warnings": len(cov),
            "role_options": role_opts, "people_options": people_opts,
            "all_role_options": all_opts,
            "can_create": online and bool(role_opts), "can_manage": online and can_manage, "manage": can_manage,
            "online": online, "active_received": len(active_received),
            "default_until": dl.default_until(date.today()).isoformat()}
