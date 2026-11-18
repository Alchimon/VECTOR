"""Представление раздела «Качество» (этап 6, сценарий С5 шаг 10; D-39) — чистые функции без Qt.

Три вкладки:

* «Рекламации» — рекламация по экземпляру изделия → анализ причины → план CAPA (корректирующие и предупреждающие
  действия) → выполнение → проверка эффективности ДРУГИМ человеком → закрыта (или снова действия);
* «Запросы на изменение» (ECR) — подан → оценён → принят / отклонён (решает ГИП проекта, не автор);
* «Извещения об изменении» (ECO) — черновик с ТОЧНЫМИ затронутыми версиями → утверждено (ГИП, не составитель) →
  внедрено (на какие версии заменены) → проверено (не тот, кто внедрял).

Флаги can_* — подсказки интерфейса по ролям и состоянию; решает всегда сервер. Если действие недоступно по правилу
«другой человек», рядом показывается почему (block_*), а не просто серая кнопка.
"""
from __future__ import annotations

from typing import Any, Optional

from vector.core import quality as qa

from .lifecycle_view import _day
from .production_view import _project_roles, when
from .stock_view import as_of_label

CLAIM_TONES = {"registered": "info", "analysis": "warning", "action": "warning", "effectiveness_check": "primary",
               "closed": "success"}
ECR_TONES = {"submitted": "info", "assessed": "primary", "accepted": "success", "rejected": "neutral"}
ECO_TONES = {"draft": "neutral", "approved": "primary", "implemented": "warning", "verified": "success"}
ACTION_ICONS = {"corrective": "wrench", "preventive": "shield-check"}
# Что дальше по рекламации (подсказка над кнопками карточки).
CLAIM_NEXT = {"registered": "Дальше: назначить ответственного за анализ причины.",
              "analysis": "Дальше: записать найденную причину и утвердить план действий (хотя бы одно корректирующее).",
              "action": "Дальше: исполнители отмечают действия; когда всё выполнено — отправить на проверку эффективности.",
              "effectiveness_check": "Дальше: проверка эффективности — её делает человек, не участвовавший в плане и действиях.",
              "closed": ""}
ECR_NEXT = {"submitted": "Дальше: оценка влияния (РП или ГИП проекта).",
            "assessed": "Дальше: решение ГИП проекта — принять или отклонить (не автор запроса).",
            "accepted": "Дальше: составить извещение об изменении с точными версиями.", "rejected": ""}
ECO_NEXT = {"draft": "Дальше: утверждение ГИП проекта (не тот, кто составлял или правил).",
            "approved": "Дальше: внедрить — выпустить новые версии документов и техпроцесса и отметить их.",
            "implemented": "Дальше: проверка внедрения (не тот, кто внедрял).", "verified": ""}


def _who(people: dict[str, str], uid: Optional[str], behalf: Optional[str] = None) -> str:
    """Имя сотрудника («—», если нет) и «(замещает: …)», если решение принято за другого."""
    name = people.get(uid or "", "—")
    return f"{name} (замещает: {people.get(behalf, '—')})" if behalf else name


def _history(rows: list[dict], people: dict[str, str]) -> list[dict]:
    """История из журнала решений: подпись шага, кто и когда, подробности одной строкой."""
    out = []
    for h in rows or []:
        d = h.get("details") or {}
        extra = d.get("reason") or d.get("evidence") or d.get("impact") or d.get("note") or d.get("cause") or d.get("result") or ""
        out.append({"label": h.get("label") or h.get("decision"), "who": _who(people, h.get("by"), h.get("on_behalf_of")),
                    "when": when(h.get("at")), "extra": extra})
    return out


def _team(project_id: str, assignments: list[dict], deputies: list[dict], people: dict[str, str]) -> list[dict]:
    """Участники проекта (свои назначения и замещающие в проекте) — для выбора ответственного и исполнителей."""
    ids = [a.get("user_id") for a in assignments or [] if a.get("project_id") == project_id]
    ids += [d.get("user_id") for d in deputies or [] if d.get("project_id") == project_id]
    return sorted(({"id": u, "label": people.get(u, "—")} for u in dict.fromkeys(i for i in ids if i)), key=lambda x: x["label"])


def _claim_row(c: dict, me_id: str, pr: set, roles: set, online: bool, people: dict[str, str], inst_label: str) -> dict:
    """Рекламация для списка и карточки: подписи, действия CAPA, проверки, история, флаги и причины недоступности."""
    st = c.get("status") or "registered"
    manage = bool(pr & {"rp", "gip"}) or "dept_head" in roles
    acts = c.get("actions") or []
    planners, doers = set(c.get("planners") or []), set(c.get("doers") or [])
    actions = []
    for a in acts:
        done = a.get("status") == "done"
        actions.append({**a, "icon": ACTION_ICONS.get(a.get("kind"), "wrench"), "owner": people.get(a.get("owner_id"), "—"),
                        "due_label": _day(a.get("due_date")), "done": done,
                        "done_label": f"{_who(people, a.get('done_by'))}, {when(a.get('done_at'))}" if done else "",
                        "can_done": online and st == "action" and not done and (a.get("owner_id") == me_id or manage)})
    checks = [{**x, "label": "Эффективно" if x.get("effective") else "Не эффективно",
               "tone": "success" if x.get("effective") else "danger", "who": _who(people, x.get("checked_by")),
               "when": when(x.get("checked_at"))} for x in c.get("checks") or []]
    why = qa.checker_conflicts(me_id, planners=planners, doers=doers, submitter=c.get("submitted_by"))
    may_check = bool(pr & {"rp", "gip"}) or "dept_head" in roles
    left = [a for a in acts if a.get("status") != "done"]
    pending = bool(c.get("_pending"))
    no = c.get("claim_no")
    return {"id": c["id"], "project_id": c.get("project_id"), "version": c.get("version") or 0, "pending": pending,
            "label": f"Рекламация № {no}" if no else "Рекламация (не отправлена)",
            "title": (c.get("description") or "")[:120], "description": c.get("description") or "",
            "instance_label": c.get("instance_label") or inst_label, "instance_id": c.get("instance_id"),
            "status": st, "status_label": qa.CLAIM_STATUS_LABELS_RU.get(st, st), "status_tone": CLAIM_TONES.get(st, "neutral"),
            "source_label": qa.CLAIM_SOURCES_RU.get(c.get("source"), ""), "reference": c.get("reference") or "",
            "received_label": _day(c.get("received_date")), "responsible": people.get(c.get("responsible_id") or "", ""),
            "cause": c.get("cause") or "", "actions": actions, "checks": checks, "next": CLAIM_NEXT.get(st, ""),
            "history": _history(c.get("history"), people), "closure_note": c.get("closure_note") or "",
            "change_requests": [{**r, "label": f"Запрос № {r.get('ecr_no')} — {r.get('title')}",
                                 "status_label": qa.ECR_STATUS_LABELS_RU.get(r.get("status"), "")} for r in c.get("change_requests") or []],
            "open_actions": len(left), "manage": manage,
            "can_start": manage and online and st == "registered" and not pending,
            "can_plan": manage and online and st in ("analysis", "action") and not pending, "first_plan": st == "analysis",
            "can_submit": manage and online and st == "action" and bool(acts) and not left,
            "submit_block": (f"Не выполнено действий: {len(left)}." if st == "action" and left else ""),
            "may_check": may_check and st == "effectiveness_check",
            "can_check": may_check and online and st == "effectiveness_check" and not why,
            "check_block": ("Проверять должен другой человек: " + "; ".join(why) + ".") if st == "effectiveness_check" and may_check and why else "",
            "can_ecr": bool(pr) and not pending}


def _ecr_row(r: dict, me_id: str, pr: set, online: bool, people: dict[str, str], kit_codes: dict) -> dict:
    """Запрос на изменение: подписи, оценка, решение, флаги (решает ГИП, не автор)."""
    st = r.get("status") or "submitted"
    no = r.get("ecr_no")
    pending = bool(r.get("_pending"))
    own = r.get("created_by") == me_id or (pending and not r.get("created_by"))
    eco = r.get("change_order")
    return {"id": r["id"], "project_id": r.get("project_id"), "version": r.get("version") or 0, "pending": pending,
            "label": f"Запрос № {no}" if no else "Запрос (не отправлен)", "title": r.get("title") or "",
            "reason": r.get("reason") or "", "proposal": r.get("proposal") or "",
            "status": st, "status_label": qa.ECR_STATUS_LABELS_RU.get(st, st), "status_tone": ECR_TONES.get(st, "neutral"),
            "kit_code": r.get("kit_code") or kit_codes.get(r.get("kit_id"), ""), "claim_label": f"Рекламация № {r['claim_no']}" if r.get("claim_no") else "",
            "claim_id": r.get("claim_id"), "impact": r.get("impact") or "",
            "assessed_label": f"{_who(people, r.get('assessed_by'))}, {when(r.get('assessed_at'))}" if r.get("assessed_by") else "",
            "decision_reason": r.get("decision_reason") or "",
            "decided_label": f"{_who(people, r.get('decided_by'))}, {when(r.get('decided_at'))}" if r.get("decided_by") else "",
            "author": people.get(r.get("created_by") or "", ""), "next": ECR_NEXT.get(st, ""),
            "history": _history(r.get("history"), people),
            "eco_label": f"Извещение № {eco.get('eco_no')} — {qa.ECO_STATUS_LABELS_RU.get(eco.get('status'), '')}" if eco else "",
            "eco_id": (eco or {}).get("id"),
            "can_assess": bool(pr & {"rp", "gip"}) and online and st == "submitted" and not pending,
            "may_decide": "gip" in pr and st == "assessed",
            "can_decide": "gip" in pr and online and st == "assessed" and not own,
            "decide_block": "Решение по своему запросу принимает другой человек («четыре глаза»)." if "gip" in pr and st == "assessed" and own else "",
            "can_eco": bool(pr & {"rp", "gip"}) and st == "accepted" and not eco}


def _eco_item_label(it: dict, res: Optional[dict]) -> str:
    """«Документ АТХ-СХ — Схема, версия 1 → версия 2» / «Техпроцесс АТХ, редакция 1 → редакция 2»."""
    if it.get("kind") == "document":
        s = f"Документ {it.get('code') or ''} — {it.get('title') or ''}, версия {it.get('from_version')}"
        return s + (f" → версия {res.get('to_version')}" if res else "")
    s = f"Техпроцесс {it.get('kit_code') or ''}, редакция {it.get('revision_no') or '?'} — {it.get('title') or ''}"
    return s + (f" → редакция {res.get('revision_no')}" if res else "")


def _eco_targets(it: dict, refs: list[dict]) -> dict:
    """Варианты внедрения строки: для документа — предлагаемая новая версия (последняя, если новее прежней);
    для техпроцесса — УТВЕРЖДЁННЫЕ редакции того же изделия новее прежней (иначе сервер откажет)."""
    if it.get("kind") == "document":
        ref = next((x for x in refs if x.get("kind") == "document" and x.get("id") == it.get("document_id")), None)
        cur = (ref or {}).get("current_version_no") or 0
        return {"suggest": cur if cur > (it.get("from_version") or 0) else 0, "tp_options": [],
                "hint": "" if cur > (it.get("from_version") or 0) else "Новой версии ещё нет — загрузите её в «Документах»."}
    opts = [{"id": x["id"], "label": f"редакция {x.get('revision_no')} — {x.get('title')}"} for x in refs
            if x.get("kind") == "tech_process" and x.get("kit_id") == it.get("kit_id") and x.get("status") == "approved"
            and (x.get("revision_no") or 0) > (it.get("revision_no") or 0)]
    return {"suggest": 0, "tp_options": opts,
            "hint": "" if opts else "Утверждённой новой редакции ещё нет — её выпускает технолог в «Технологии»."}


def _eco_row(o: dict, me_id: str, pr: set, roles: set, online: bool, people: dict[str, str], refs: list[dict]) -> dict:
    """Извещение: строки с точными версиями (и на какие заменены), экземпляры, варианты внедрения, флаги «четырёх
    глаз». У ещё не отправленного извещения обозначения строк подставляются из справочника quality_refs."""
    st = o.get("status") or "draft"
    no = o.get("eco_no")
    pending = bool(o.get("_pending"))
    results = o.get("results") or []
    items = []
    for n, it in enumerate(o.get("items") or []):
        res = results[n] if n < len(results) else None
        if not it.get("title"):
            ref = next((x for x in refs if x.get("id") == (it.get("document_id") or it.get("tp_id"))), {})
            it = {**{k: ref.get(k) for k in ("code", "title", "kit_code", "kit_id", "revision_no")}, **it}
        items.append({**it, "index": n, "label": _eco_item_label(it, res), **_eco_targets(it, refs),
                      "kind_label": qa.ECO_ITEM_KINDS_RU.get(it.get("kind"), "")})
    editors = set(o.get("editors") or ([me_id] if pending else []))
    edit = bool(pr & {"rp", "gip"})
    return {"id": o["id"], "project_id": o.get("project_id"), "version": o.get("version") or 0, "pending": pending,
            "label": f"Извещение № {no}" if no else "Извещение (не отправлено)", "title": o.get("title") or "",
            "description": o.get("description") or "", "ecr_label": f"Запрос № {o['ecr_no']}" if o.get("ecr_no") else "",
            "ecr_id": o.get("ecr_id"), "status": st, "status_label": qa.ECO_STATUS_LABELS_RU.get(st, st),
            "status_tone": ECO_TONES.get(st, "neutral"), "items": items, "raw_items": o.get("items") or [],
            "instances": [{**i, "why_label": "; ".join(i.get("why") or [])} for i in o.get("instances") or []],
            "approved_label": f"{_who(people, o.get('approved_by'))}, {when(o.get('approved_at'))}" if o.get("approved_by") else "",
            "implemented_label": f"{_who(people, o.get('implemented_by'))}, {when(o.get('implemented_at'))}" if o.get("implemented_by") else "",
            "verified_label": f"{_who(people, o.get('verified_by'))}, {when(o.get('verified_at'))}" if o.get("verified_by") else "",
            "verify_note": o.get("verify_note") or "", "next": ECO_NEXT.get(st, ""), "history": _history(o.get("history"), people),
            "can_edit": edit and st == "draft",
            "may_approve": "gip" in pr and st == "draft",
            "can_approve": "gip" in pr and online and st == "draft" and not pending and me_id not in editors,
            "approve_block": "Утверждает не тот, кто составлял или правил извещение («четыре глаза»)."
                             if "gip" in pr and st == "draft" and me_id in editors else "",
            "can_implement": edit and online and st == "approved",
            "may_verify": (bool(pr & {"rp", "gip"}) or "dept_head" in roles) and st == "implemented",
            "can_verify": (bool(pr & {"rp", "gip"}) or "dept_head" in roles) and online and st == "implemented"
                          and o.get("implemented_by") != me_id,
            "verify_block": "Проверяет не тот, кто отмечал внедрение." if st == "implemented" and o.get("implemented_by") == me_id else ""}


def quality_view(claims: list[dict], ecrs: list[dict], ecos: list[dict], instances: list[dict], refs: list[dict],
                 projects: list[dict], kits: list[dict], assignments: list[dict], deputies: list[dict],
                 people: dict[str, str], me: dict, online: bool, last_pull: str) -> dict[str, Any]:
    """Всё для раздела «Качество»: три списка с карточками, варианты для форм, счётчики, подсказки."""
    me_id = me.get("user_id") or ""
    roles = set(me.get("roles") or [])
    proles = _project_roles(me_id, assignments, deputies)
    pcode = {p["id"]: p.get("code") or "" for p in projects or []}
    kit_codes = {k["id"]: k.get("code") or "" for k in kits or []}
    inst_label = {i["id"]: f"{i.get('kit_code')} № {i.get('serial_no')} (серия {i.get('series')})" for i in instances or []}
    c_rows = [_claim_row(c, me_id, proles.get(c.get("project_id"), set()), roles, online, people,
                         inst_label.get(c.get("instance_id"), "")) for c in claims or []]
    r_rows = [_ecr_row(r, me_id, proles.get(r.get("project_id"), set()), online, people, kit_codes) for r in ecrs or []]
    o_rows = [_eco_row(o, me_id, proles.get(o.get("project_id"), set()), roles, online, people, refs or []) for o in ecos or []]
    for rows in (c_rows, r_rows, o_rows):
        for x in rows:
            x["project_code"] = pcode.get(x.get("project_id"), "")
        rows.sort(key=lambda x: (x["status"] in ("closed", "rejected", "verified"), x["label"]))
    may_claim = lambda pid: bool(proles.get(pid, set()) & {"rp", "gip"}) or "dept_head" in roles  # noqa: E731
    inst_opts = [{"id": i["id"], "project_id": i.get("project_id"), "label": f"{pcode.get(i.get('project_id'), '')} · {inst_label[i['id']]}"}
                 for i in instances or [] if may_claim(i.get("project_id"))]
    ecr_projects = [{"id": p["id"], "label": f"{p.get('code')} — {p.get('title') or ''}"} for p in projects or []
                    if proles.get(p["id"], set()) & {"gip", "rp", "participant"}]
    teams = {pid: _team(pid, assignments, deputies, people) for pid in {i.get("project_id") for i in instances or []}
             | {c.get("project_id") for c in claims or []}}
    ref_opts = {"document": [], "tech_process": []}
    for x in refs or []:
        if x.get("kind") == "document":
            ref_opts["document"].append({"id": x["id"], "project_id": x.get("project_id"), "current_version_no": x.get("current_version_no"),
                                         "label": f"{x.get('code')} — {x.get('title')} (последняя версия {x.get('current_version_no')})"})
        else:
            ref_opts["tech_process"].append({"id": x["id"], "project_id": x.get("project_id"), "kit_id": x.get("kit_id"),
                                             "revision_no": x.get("revision_no"), "status": x.get("status"),
                                             "label": f"{x.get('kit_code')}, редакция {x.get('revision_no')} — {x.get('title')}"
                                                      + ("" if x.get("status") == "approved" else " (черновик)")})
    open_claims = sum(1 for x in c_rows if x["status"] != "closed")
    to_me = (sum(1 for x in c_rows if x["can_check"]) + sum(1 for x in r_rows if x["can_decide"] or x["can_assess"])
             + sum(1 for x in o_rows if x["can_approve"] or x["can_verify"])
             + sum(1 for x in c_rows for a in x["actions"] if a["can_done"] and a.get("owner_id") == me_id))
    return {
        "online": online, "as_of": as_of_label(last_pull), "claims": c_rows, "ecrs": r_rows, "ecos": o_rows,
        # Показывать ли раздел в меню (подсказка): команде любого проекта, технологам, руководителю отдела и всем,
        # кому сервер отдал данные качества.
        "sees": bool(c_rows or r_rows or o_rows or proles or roles & {"technologist", "dept_head"}),
        "counts": {"claims_open": open_claims, "ecrs_open": sum(1 for x in r_rows if x["status"] in ("submitted", "assessed")),
                   "ecos_open": sum(1 for x in o_rows if x["status"] != "verified"), "to_me": to_me},
        "instance_options": inst_opts, "may_claim": bool(inst_opts), "teams": teams,
        "ecr_projects": ecr_projects, "may_ecr": bool(ecr_projects),
        "kit_options": [{"id": k["id"], "project_id": k.get("project_id"), "label": f"{k.get('code')} — {k.get('title') or ''}"} for k in kits or []],
        "claim_options": [{"id": c["id"], "project_id": c["project_id"], "label": f"{c['label']} — {c['instance_label']}"} for c in c_rows if c.get("project_id")],
        "ref_options": ref_opts,
        "sources": [{"id": k, "label": v, "icon": "building-2" if k == "customer" else "factory"} for k, v in qa.CLAIM_SOURCES_RU.items()],
        "action_kinds": [{"id": k, "label": v, "icon": ACTION_ICONS[k]} for k, v in qa.ACTION_KINDS_RU.items()],
        "closure_note": qa.CLOSURE_NOTE,
    }


def quality_check(kind: str, payload: dict) -> dict[str, Any]:
    """Проверка формы без сервера (правила ядра): {ok, message, field, rows: {номер строки: замечание}}."""
    try:
        if kind == "claim":
            qa.parse_claim(payload)
        elif kind == "actions":
            if payload.get("first"):
                qa._text(payload.get("cause"), "cause", "найденную причину", qa.MAX_TEXT)
            acts = qa.parse_actions(payload.get("actions"))
            if payload.get("first") and not qa.has_corrective(acts):
                return {"ok": False, "message": "Нужно хотя бы одно корректирующее действие — оно устраняет причину.", "field": "actions", "rows": {}}
        elif kind == "ecr":
            qa.parse_ecr(payload)
        elif kind == "check":
            qa.parse_check(payload)
        elif kind == "eco":
            qa._text(payload.get("description"), "description", "что и как изменяется", qa.MAX_TEXT)
            qa.parse_eco_items(payload.get("items"))
        return {"ok": True, "message": "", "field": "", "rows": {}}
    except qa.QualityError as e:
        rows = {str(p["row"] - 1): p["message_ru"] for p in e.details.get("problems", [])}
        return {"ok": False, "message": e.message_ru, "field": e.details.get("field") or "", "rows": rows}
