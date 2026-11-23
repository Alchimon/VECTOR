"""Представление потоков проекта, риск-разрешений, журнала событий и лидов для интерфейса (D-65, этап 2 blueprint 52).

Чистые функции над снимком pull (meta-списки) — без сети и Qt. Подписи статусов и допустимые переходы читаются из
контракта workflows, который приезжает клиенту вместе с правами, — при смене контракта интерфейс не рассинхронизируется
с сервером. Кнопки действий — ПОДСКАЗКА по роли из кэша назначений: окончательное решение принимает сервер (отказ
придёт понятным сообщением). Чтение — и без связи; все команды — только онлайн.
"""
from __future__ import annotations

from typing import Any, Optional

# Порядок и ярлыки шести потоков проекта (D-56): ключ потока → имя секции workflows.
STREAM_ORDER = ["contract", "rkd", "procurement", "production", "shipping", "site"]

_RISK_TONE = {"active": "warning", "expired": "danger", "resolved": "success", "withdrawn": "neutral"}
_OPP_TONE = {"new": "info", "qualified": "primary", "tendered": "success", "declined": "neutral"}


def _fmt(s: Optional[str]) -> str:
    """Метка времени «ГГГГ-ММ-ДД ЧЧ:ММ» из ISO (даты — «ГГГГ-ММ-ДД»)."""
    return (s or "")[:16].replace("T", " ")


def _roles(me_id: str, assignments: list[dict], project_id: str, global_roles: list[str]) -> set[str]:
    """Мои действующие назначения в проекте + глобальные роли (подсказка доступных кнопок)."""
    out = {a["project_role"] for a in assignments
           if a.get("project_id") == project_id and a.get("user_id") == me_id}
    out.update(global_roles or [])
    return out


def _can_risk(roles: set[str]) -> bool:
    """Риск-разрешение принимает РП проекта; резерв — руководитель отдела (контракт прав)."""
    return "rp" in roles or "dept_head" in roles


def _stream_row(w: dict, wf: dict, roles: set[str], online: bool, people: dict[str, str],
                kit_codes: dict[str, str]) -> dict:
    """Строка потока: статус, ответственный, плановые даты, допустимые переходы (из контракта workflows)."""
    contract = wf.get("workstream_" + w["kind"], {})
    labels = contract.get("labels_ru", {})
    reason_req = set(contract.get("reason_required_for", []))
    gate = contract.get("gate", {})
    actions = []
    if online and _can_risk(roles):   # workstream.set_status — same_as project.transition (РП проекта)
        for to in contract.get("transitions", {}).get(w["status"], []):
            g = gate.get(to)
            actions.append({"to": to, "label": labels.get(to, to), "reason": to in reason_req,
                            "gate": bool(g),
                            "gate_hint": ("Требует РКД «Согласована заказчиком» или действующего риск-разрешения."
                                          if g else "")})
    return {"id": w["id"], "kind": w["kind"], "kind_label": w["kind_label"], "status": w["status"],
            "status_label": labels.get(w["status"], w["status"]), "version": w["version"],
            "responsible_name": people.get(w.get("responsible_user_id") or "", "—"),
            "responsible_user_id": w.get("responsible_user_id") or "",
            "planned_start": w.get("planned_start") or "", "planned_due": w.get("planned_due") or "",
            "note": w.get("note") or "", "terminal": w["status"] in set(contract.get("terminal", [])),
            "updated_label": _fmt(w.get("updated_at")), "actions": actions,
            "can_plan": online and _can_risk(roles)}


def _risk_row(r: dict, wf: dict, roles: set[str], online: bool, people: dict[str, str],
              kit_codes: dict[str, str]) -> dict:
    """Строка риск-разрешения: вид, состояние (действует/истёк/снято), причина, срок, охват, снятие."""
    contract = wf.get("risk_acceptance", {})
    kind_label = contract.get("kinds", {}).get(r["kind"], r["kind"])
    labels = contract.get("labels_ru", {})
    scope_ids = r.get("scope") or []
    scope_label = ", ".join(kit_codes.get(k, k) for k in scope_ids) if scope_ids else "Весь проект"
    actions = []
    if online and _can_risk(roles) and r["status"] == "active":
        for to in ("resolved", "withdrawn"):
            actions.append({"resolution": to, "label": labels.get(to, to)})
    return {"id": r["id"], "kind": r["kind"], "kind_label": kind_label, "status": r["status"],
            "state": r["state"], "state_label": labels.get(r["state"], r["state"]),
            "tone": _RISK_TONE.get(r["state"], "neutral"), "reason": r["reason"],
            "mitigation": r.get("mitigation") or "", "due_on": r["due_on"], "scope_label": scope_label,
            "accepted_by_name": people.get(r.get("accepted_by") or "", ""),
            "created_label": _fmt(r.get("created_at")),
            "resolved_by_name": people.get(r.get("resolved_by") or "", ""),
            "resolved_label": _fmt(r.get("resolved_at")), "resolution_reason": r.get("resolution_reason") or "",
            "version": r["version"], "actions": actions}


def project_delivery(project_id: str, project: dict, workstreams: list[dict], risks: list[dict],
                     events: list[dict], closeouts: list[dict], kits: list[dict], people: dict[str, str],
                     me_id: str, assignments: list[dict], global_roles: list[str], online: bool,
                     wf: dict) -> dict:
    """Срез «Потоки и риски» проекта: шесть потоков в каноническом порядке, риск-разрешения, лента событий,
    статус самого проекта (рамка) с допустимыми переходами и снимок закрытия."""
    roles = _roles(me_id, assignments, project_id, global_roles)
    kit_codes = {k["id"]: k["code"] for k in kits if k.get("project_id") == project_id}
    by_kind = {w["kind"]: w for w in workstreams if w.get("project_id") == project_id}
    streams = [_stream_row(by_kind[k], wf, roles, online, people, kit_codes)
               for k in STREAM_ORDER if k in by_kind]
    pr_wf = wf.get("project", {})
    pr_labels = pr_wf.get("labels_ru", {})
    pr_reason = set(pr_wf.get("reason_required_for", []))
    pr_actions = []
    if project and online and _can_risk(roles):
        for to in pr_wf.get("transitions", {}).get(project["status"], []):
            pr_actions.append({"to": to, "label": pr_labels.get(to, to),
                               "reason": to in pr_reason or to == "rkd_in_progress",
                               "danger": to == "cancelled"})
    risk_rows = [_risk_row(r, wf, roles, online, people, kit_codes)
                 for r in risks if r.get("project_id") == project_id]
    active_risks = sum(1 for r in risk_rows if r["state"] == "active")
    closeout = next((c for c in closeouts if c.get("project_id") == project_id), None)
    return {"streams": streams, "risks": sorted(risk_rows, key=lambda r: r["created_label"], reverse=True),
            "events": [{"seq": e["seq"], "kind": e["kind"], "summary": e["summary"],
                        "actor_name": people.get(e.get("actor_id") or "", ""), "at_label": _fmt(e.get("at"))}
                       for e in sorted((x for x in events if x.get("project_id") == project_id),
                                       key=lambda x: -x["seq"])],
            "project": {"id": project["id"], "status": project["status"],
                        "status_label": pr_labels.get(project["status"], project["status"]),
                        "version": project["version"], "actions": pr_actions} if project else None,
            "risk_kinds": [{"id": k, "label": v} for k, v in wf.get("risk_acceptance", {}).get("kinds", {}).items()],
            "kit_options": [{"id": k["id"], "label": k["code"]}
                            for k in sorted((x for x in kits if x.get("project_id") == project_id),
                                            key=lambda x: x["code"])],
            "can_manage": online and _can_risk(roles),
            "counts": {"streams_done": sum(1 for s in streams if s["terminal"]),
                       "active_risks": active_risks,
                       "expired_risks": sum(1 for r in risk_rows if r["state"] == "expired"),
                       "events": sum(1 for e in events if e.get("project_id") == project_id)},
            "closeout": {"content_sha256": closeout["content_sha256"],
                         "closed_by_name": people.get(closeout.get("closed_by") or "", ""),
                         "created_label": _fmt(closeout.get("created_at"))} if closeout else None}


def opportunity_rows(opportunities: list[dict], people: dict[str, str], online: bool, wf: dict) -> list[dict]:
    """Строки воронки лидов: источник, заказчик, ответственный, статус, действия по контракту workflows."""
    contract = wf.get("sales_opportunity", {})
    labels = contract.get("labels_ru", {})
    reason_req = set(contract.get("reason_required_for", []))
    out = []
    for o in opportunities:
        actions = []
        if online and o["status"] not in set(contract.get("terminal", [])):
            for to in contract.get("transitions", {}).get(o["status"], []):
                if to == "tendered":
                    continue   # в тендер — только convert_to_tender (она создаёт тендер и связь)
                actions.append({"to": to, "label": labels.get(to, to), "reason": to in reason_req,
                                "danger": to == "declined"})
            if o["status"] == "qualified" and o.get("customer_id"):
                actions.append({"kind": "convert", "label": "Перевести в тендер"})
        out.append({"id": o["id"], "title": o["title"], "source": o["source"],
                    "source_label": o["source_label"], "source_detail": o.get("source_detail") or "",
                    "customer_id": o.get("customer_id") or "",
                    "customer_label": o.get("customer_name") or "Заказчик не указан",
                    "owner_name": people.get(o.get("owner_user_id") or "", ""),
                    "expected_on": o.get("expected_on") or "", "note": o.get("note") or "",
                    "status": o["status"], "status_label": labels.get(o["status"], o["status"]),
                    "tone": _OPP_TONE.get(o["status"], "neutral"), "version": o["version"],
                    "terminal": o["status"] in set(contract.get("terminal", [])),
                    "updated_label": _fmt(o.get("updated_at")), "actions": actions})
    return sorted(out, key=lambda r: (r["terminal"], r["updated_label"]), reverse=False)


def opportunities_view(opportunities: list[dict], people: dict[str, str], me_id: str, global_roles: list[str],
                       online: bool, wf: dict) -> dict:
    """Раздел «Лиды»: воронка возможностей продаж. Право — глобальные роли ГИП/РП/руководитель отдела."""
    can = online and bool(set(global_roles or []) & {"gip", "rp", "dept_head"})
    rows = opportunity_rows(opportunities, people, can, wf)
    return {"rows": rows,
            "sources": [{"id": k, "label": v} for k, v in
                        (("platform", "Площадка"), ("manager", "Менеджер"), ("inbound", "Входящее обращение"),
                         ("other", "Другое"))],
            "counts": {"total": len(rows),
                       "open": sum(1 for r in rows if not r["terminal"]),
                       "new": sum(1 for r in rows if r["status"] == "new"),
                       "qualified": sum(1 for r in rows if r["status"] == "qualified")},
            "can_manage": can}
