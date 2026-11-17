"""Представление раздела «Замещения» для интерфейса (D-34, ответ владельца на A-37) — чистые функции без Qt.

Данные приходят снимком pull (delegations, role_coverage, me) и офлайн только читаются: оформить и отменить
замещение можно лишь при связи с сервером (команды online_required). Здесь — подписи, сроки, кто что может
(подсказка; решает всегда сервер) и предупреждения покрытия ролей («роль у одного человека — заменить некем»).
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


def role_labels(me: dict, labels: dict[str, str]) -> list[str]:
    """Подписи ролей текущего пользователя: свои — как есть, полученные замещением — с пометкой срока
    («Согласование закупок (замещение до 30.11.2026)»). Старый кэш без own_roles — все роли как свои.
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
    return out


def delegations_view(delegations: list[dict], coverage: list[dict], people: dict[str, str], me: dict,
                     labels: dict[str, str], online: bool, can_manage: bool) -> dict[str, Any]:
    """Всё для раздела «Замещения».

    * received / given — замещения, где я замещающий / носитель роли; others — остальные (видит руководитель отдела
      и администратор);
    * role_options — мои СВОИ роли, которые можно передать (кроме «Администратор»); people_options — кого назначить;
    * coverage — роли, у которых нет никого или ровно один человек (сначала «никого»), с подписями людей;
      warnings — сколько таких ролей (бейдж в меню у руководителя отдела);
    * can_create / can_manage — подсказка для кнопок (без связи — нельзя: команды только онлайн).
    """
    uid = me.get("user_id")
    own = me.get("own_roles") if me.get("own_roles") is not None else (me.get("roles") or [])

    def name(u: Optional[str]) -> str:
        return people.get(u, "—") if u else "—"

    def row(d: dict) -> dict:
        mine = uid in (d.get("from_user_id"), d.get("to_user_id"), d.get("created_by"))
        st = d.get("state")
        return {**d, "from_name": name(d.get("from_user_id")), "to_name": name(d.get("to_user_id")),
                "created_name": name(d.get("created_by")), "role_label": labels.get(d["role"], d["role"]),
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
        cov.append({**c, "label": labels.get(c["role"], c["role"]), "tone": COVERAGE_TONES.get(c["state"], "neutral"),
                    "text": COVERAGE_TEXTS.get(c["state"], "") if c.get("delegable", True)
                    else "Роль у одного человека; замещением не передаётся — выдайте её второму сотруднику",
                    "holder_names": [name(u) for u in c.get("holders") or []],
                    "holder_id": (c.get("holders") or [None])[0]})
    cov.sort(key=lambda c: (c["state"] != "none", c["label"]))
    role_opts = [{"id": r, "label": labels.get(r, r)} for r in own if r not in dl.NON_DELEGABLE]
    people_opts = [{"id": k, "label": v} for k, v in sorted(people.items(), key=lambda kv: kv[1]) if k != uid]
    active_received = [r for r in received if r["state"] == "active"]
    return {"received": received, "given": given, "others": others, "coverage": cov, "warnings": len(cov),
            "role_options": role_opts, "people_options": people_opts,
            "all_role_options": [{"id": r, "label": labels.get(r, r)} for r in labels if r not in dl.NON_DELEGABLE],
            "can_create": online and bool(role_opts), "can_manage": online and can_manage, "manage": can_manage,
            "online": online, "active_received": len(active_received),
            "default_until": dl.default_until(date.today()).isoformat()}
