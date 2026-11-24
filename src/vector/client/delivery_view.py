"""Представление потоков проекта, риск-разрешений, журнала событий и лидов для интерфейса (D-65, этап 2 blueprint 52).

Чистые функции над снимком pull (meta-списки) — без сети и Qt. Подписи статусов и допустимые переходы читаются из
контракта workflows, который приезжает клиенту вместе с правами, — при смене контракта интерфейс не рассинхронизируется
с сервером. Кнопки действий — ПОДСКАЗКА по роли из кэша назначений: окончательное решение принимает сервер (отказ
придёт понятным сообщением). Чтение — и без связи; все команды — только онлайн.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

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


# ------------------------------------------------------------ ПНР, FAT, документы доставки (D-70, этап 6)
_PNR_TONE = {"requested": "info", "scheduled": "primary", "in_progress": "warning", "done": "success",
             "cancelled": "neutral"}
_FAT_TONE = {"in_progress": "warning", "passed": "success", "failed": "danger"}
_FAT_KIND_LABEL = {"preliminary": "Предварительный FAT", "final": "Финальный (приёмочный) FAT"}
_FINDING_TONE = {"open": "danger", "closed": "success"}
_FINDING_STATUS = {"open": "Открыто", "closed": "Закрыто"}


def _pnr_row(r: dict, wf: dict, roles: set[str], online: bool, kit_codes: dict[str, str]) -> dict:
    """Строка запроса ПНР: вид, питание, плановая дата, статус; статус ведёт РП (кнопки — подсказка)."""
    contract = wf.get("pnr_request", {})
    labels = contract.get("labels_ru", {})
    reason_req = set(contract.get("reason_required_for", []))
    actions = []
    if online and _can_risk(roles):
        for to in contract.get("transitions", {}).get(r["status"], []):
            actions.append({"to": to, "label": labels.get(to, to), "reason": to in reason_req,
                            "danger": to == "cancelled"})
    return {"id": r["id"], "kind": r["kind"], "kind_label": r["kind_label"], "title": r["title"],
            "need_power": bool(r["need_power"]), "planned_on": r.get("planned_on") or "",
            "kit_label": kit_codes.get(r.get("kit_id") or "", "Весь проект"),
            "status": r["status"], "status_label": labels.get(r["status"], r["status"]),
            "status_reason": r.get("status_reason") or "", "tone": _PNR_TONE.get(r["status"], "neutral"),
            "terminal": r["status"] in set(contract.get("terminal", [])), "version": r["version"],
            "created_label": _fmt(r.get("created_at")), "actions": actions}


def _finding_row(f: dict, roles: set[str], online: bool, fat_status: str) -> dict:
    """Строка замечания FAT: раздел, градация (снимок), блокировка, статус; закрытие — РП, с причиной."""
    actions = []
    if online and _can_risk(roles) and f["status"] == "open":
        actions.append({"label": "Закрыть замечание"})
    return {"id": f["id"], "fat_id": f["fat_id"], "seq": f["seq"], "section_code": f["section_code"],
            "severity": f["severity"], "severity_label": f["severity_label"], "blocking": bool(f["blocking"]),
            "text": f["text"], "status": f["status"], "status_label": _FINDING_STATUS[f["status"]],
            "tone": _FINDING_TONE[f["status"]] if f["status"] == "closed" else ("danger" if f["blocking"] else "warning"),
            "close_reason": f.get("close_reason") or "", "version": f["version"],
            "created_label": _fmt(f.get("created_at")), "closed_label": _fmt(f.get("closed_at") or ""),
            "can_close": bool(actions)}


def _fat_row(fat: dict, findings: list[dict], templates: dict[str, dict], wf: dict, roles: set[str],
             online: bool, kit_codes: dict[str, str], people: dict[str, str]) -> dict:
    """Строка FAT-сессии: вид, итог, шаблон (снимок), замечания; действия — итог и добавление замечания."""
    contract = wf.get("fat_session", {})
    labels = contract.get("labels_ru", {})
    reason_req = set(contract.get("reason_required_for", []))
    my_findings = [f for f in findings if f.get("fat_id") == fat["id"]]
    open_blocking = sum(1 for f in my_findings if f["status"] == "open" and f["blocking"])
    actions = []
    if online and _can_risk(roles) and fat["status"] == "in_progress":
        for to in contract.get("transitions", {}).get(fat["status"], []):
            actions.append({"to": to, "label": labels.get(to, to), "reason": to in reason_req,
                            "danger": to == "failed"})
    can_add = online and fat["status"] == "in_progress" and bool(roles & {"gip", "rp", "participant"})
    sections = fat.get("sections") or []
    severities = fat.get("severities") or []
    return {"id": fat["id"], "seq": fat.get("seq"), "kind": fat["kind"],
            "kind_label": contract.get("kinds", {}).get(fat["kind"], _FAT_KIND_LABEL.get(fat["kind"], fat["kind"])),
            "template_name": fat["template_name"],
            "kit_label": kit_codes.get(fat.get("kit_id") or "", "Весь проект"),
            "status": fat["status"], "status_label": labels.get(fat["status"], fat["status"]),
            "tone": _FAT_TONE.get(fat["status"], "neutral"), "version": fat["version"],
            "note": fat.get("note") or "", "created_label": _fmt(fat.get("created_at")),
            "finished_label": _fmt(fat.get("finished_at") or ""),
            "finished_by_name": people.get(fat.get("finished_by") or "", ""),
            "findings": [_finding_row(f, roles, online, fat["status"])
                         for f in sorted(my_findings, key=lambda x: x["seq"])],
            "counts": {"findings": len(my_findings),
                       "open": sum(1 for f in my_findings if f["status"] == "open"),
                       "open_blocking": open_blocking},
            "section_options": [{"id": s["code"], "label": s["title"]} for s in sections],
            "severity_options": [{"id": s["code"], "label": s["label"] + (" — блокирует отгрузку" if s["blocking"] else "")}
                                 for s in severities],
            "actions": actions, "can_add_finding": can_add}


def _delivery_doc_row(d: dict, roles: set[str], online: bool, kit_codes: dict[str, str]) -> dict:
    """Строка выписанного документа доставки: форма (снимок), текст, правка — участник проекта."""
    return {"id": d["id"], "template_code": d["template_code"], "template_title": d["template_title"],
            "kit_label": kit_codes.get(d.get("kit_id") or "", "Весь проект"),
            "rendered": d["rendered"], "version": d["version"],
            "updated_label": _fmt(d.get("updated_at")),
            "can_edit": online and bool(roles & {"gip", "rp", "participant"})}


def pnr_fat_block(project_id: str, pnr: list[dict], fats: list[dict], findings: list[dict],
                  templates: list[dict], doc_templates: list[dict], docs: list[dict], kits: list[dict],
                  people: dict[str, str], me_id: str, assignments: list[dict], global_roles: list[str],
                  online: bool, wf: dict) -> dict:
    """Срез «ПНР, испытания и документы доставки» проекта (этап 6 blueprint 52).

    Запросы ПНР (внутренние/площадочные), FAT-сессии с замечаниями (снимок чек-листа), итоговый шлюз отгрузки
    (подсказка, почему «Отгружено» недоступно), документы доставки по настраиваемым формам.
    """
    roles = _roles(me_id, assignments, project_id, global_roles)
    kit_codes = {k["id"]: k["code"] for k in kits if k.get("project_id") == project_id}
    my_pnr = [r for r in pnr if r.get("project_id") == project_id]
    my_fats = sorted((f for f in fats if f.get("project_id") == project_id), key=lambda f: f.get("seq") or 0)
    my_docs = [d for d in docs if d.get("project_id") == project_id]
    # Шлюз отгрузки (подсказка к кнопке потока «Отгрузка»): повторяет серверное правило D-68 по снимку.
    final_fats = sorted((f for f in my_fats if f["kind"] == "final"), key=lambda f: f.get("seq") or 0,
                        reverse=True)
    gate_block = None
    if not final_fats:
        gate_block = "Нет финального (приёмочного) FAT — сначала проведите и завершите приёмочные испытания."
    else:
        last = final_fats[0]
        if last["status"] == "in_progress":
            gate_block = "Финальный FAT ещё идёт — завершите испытание с итогом."
        else:
            open_bl = [f for f in findings if f.get("fat_id") == last["id"] and f["status"] == "open"
                       and f["blocking"]]
            if open_bl:
                gate_block = (f"Открыты блокирующие замечания финального FAT: {len(open_bl)} шт. — закройте их "
                              f"(с причиной) или проведите новое испытание.")
            elif last["status"] != "passed":
                gate_block = "Последний финальный FAT неуспешен — устраните замечания и проведите новое приёмочное испытание."
    fat_templates = [{"id": t["id"], "label": t["name"]}
                     for t in templates if t.get("kind") == "fat" and t.get("active")]
    doc_tpl_options = [{"id": t["id"], "label": t["code"] + " — " + t["title"], "fields": t.get("fields") or []}
                       for t in doc_templates if t.get("active")]
    return {"pnr": [_pnr_row(r, wf, roles, online, kit_codes)
                    for r in sorted(my_pnr, key=lambda r: r.get("created_at") or "", reverse=True)],
            "fats": [_fat_row(f, findings, {t["id"]: t for t in templates}, wf, roles, online, kit_codes, people)
                     for f in my_fats],
            "docs": [_delivery_doc_row(d, roles, online, kit_codes) for d in my_docs],
            "shipping_gate": {"blocked": gate_block is not None, "reason": gate_block or ""},
            "fat_templates": fat_templates, "doc_templates": doc_tpl_options,
            "kit_options": [{"id": "", "label": "Весь проект"}] +
                           [{"id": k["id"], "label": k["code"]}
                            for k in sorted((x for x in kits if x.get("project_id") == project_id),
                                            key=lambda x: x["code"])],
            "pnr_kinds": [{"id": k, "label": v} for k, v in wf.get("pnr_request", {}).get("kinds", {}).items()],
            "fat_kinds": [{"id": k, "label": v} for k, v in wf.get("fat_session", {}).get("kinds", {}).items()],
            "can_manage": online and _can_risk(roles),
            "counts": {"pnr_open": sum(1 for r in my_pnr if r["status"] not in ("done", "cancelled")),
                       "fats_running": sum(1 for f in my_fats if f["status"] == "in_progress"),
                       "open_blocking": sum(1 for f in findings
                                            if any(f["fat_id"] == fat["id"] for fat in my_fats)
                                            and f["status"] == "open" and f["blocking"]),
                       "docs": len(my_docs)}}


# ------------------------------------------------------------ аналитика проекта (D-71, этап 7 blueprint 52)
def project_analytics_block(project_id: str, streams: list[dict], events: list[dict], fats: list[dict],
                            findings: list[dict], pnr: list[dict], docs: list[dict], closeouts: list[dict],
                            people: dict[str, str], wf: dict) -> dict:
    """«Аналитика проекта»: план/факт по потокам, сводка испытаний и замечаний, ПНР, документы доставки.

    Считается из снимка pull (чтение и офлайн): фактические даты потоков — из журнала событий (details
    workstream_status), плановые — из карточек потоков. Снимок закрытия (если проект в архиве) показывает
    зафиксированные факты с контрольной суммой.
    """
    from vector.core.delivery import stream_facts, delivery_facts, STREAM_LABELS_RU

    my_events = [e for e in events if e.get("project_id") == project_id]
    my_streams = [w for w in streams if w.get("project_id") == project_id]
    facts = stream_facts(my_events, wf)
    rows = []
    for w in sorted(my_streams, key=lambda x: STREAM_ORDER.index(x["kind"]) if x["kind"] in STREAM_ORDER else 99):
        contract = wf.get("workstream_" + w["kind"], {})
        labels = contract.get("labels_ru", {})
        f = facts.get(w["kind"], {})
        plan_days = None
        if w.get("planned_start") and w.get("planned_due"):
            from datetime import date
            try:
                y1, m1, d1 = (int(x) for x in w["planned_start"].split("-"))
                y2, m2, d2 = (int(x) for x in w["planned_due"].split("-"))
                plan_days = (date(y2, m2, d2) - date(y1, m1, d1)).days
            except ValueError:
                plan_days = None
        fact_days = None
        if f.get("start") and f.get("finish"):
            from datetime import date
            try:
                y1, m1, d1 = (int(x) for x in f["start"].split("-"))
                y2, m2, d2 = (int(x) for x in f["finish"].split("-"))
                fact_days = (date(y2, m2, d2) - date(y1, m1, d1)).days
            except ValueError:
                fact_days = None
        rows.append({"kind": w["kind"], "kind_label": STREAM_LABELS_RU.get(w["kind"], w["kind"]),
                     "status": w["status"], "status_label": labels.get(w["status"], w["status"]),
                     "terminal": w["status"] in set(contract.get("terminal", [])),
                     "planned_start": w.get("planned_start") or "", "planned_due": w.get("planned_due") or "",
                     "plan_days": plan_days, "actual_start": f.get("start", ""), "actual_finish": f.get("finish", ""),
                     "fact_days": fact_days})
    my_fats = [f for f in fats if f.get("project_id") == project_id]
    fat_ids = {f["id"] for f in my_fats}
    my_findings = [f for f in findings if f.get("fat_id") in fat_ids]
    my_pnr = [r for r in pnr if r.get("project_id") == project_id]
    my_docs = [d for d in docs if d.get("project_id") == project_id]
    agg = delivery_facts(my_pnr, my_fats, my_findings, my_docs)
    closeout = next((c for c in closeouts if c.get("project_id") == project_id), None)
    closeout_facts = None
    if closeout:
        import json as _json
        snap = _json.loads(closeout["snapshot"]) if isinstance(closeout.get("snapshot"), str) \
            else closeout.get("snapshot")
        closeout_facts = {"sha256": closeout["content_sha256"],
                          "created_label": _fmt(closeout.get("created_at")),
                          "closed_by_name": people.get(closeout.get("closed_by") or "", ""),
                          "facts": (snap or {}).get("facts") or {}}
    return {"streams": rows, "facts": agg,
            "done_streams": sum(1 for r in rows if r["terminal"]),
            "total_streams": len(rows), "closeout": closeout_facts}


# ====================================================================== сводная аналитика портфеля (D-73, этап 7 blueprint 52)
def _day(s: Optional[str]):
    """«2026-11-16» → date; пусто/ошибка → None."""
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _days(a: Optional[str], b: Optional[str]) -> Optional[int]:
    """Число дней между двумя датами (обе обязательны), иначе None."""
    da, db = _day(a), _day(b)
    return (db - da).days if da and db else None


def _median_label(vals: list[int]) -> str:
    """Медиана целых дней → «45» / «45,5»; пустая выборка — «—»."""
    if not vals:
        return "—"
    v = sorted(vals)
    n = len(v)
    med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    return str(int(med)) if med == int(med) else f"{med:.1f}".replace(".", ",")


def portfolio_analytics(projects: list[dict], streams: list[dict], events: list[dict], risks: list[dict],
                        purchase_orders: list[dict], work_orders: list[dict], claims: list[dict],
                        wf: dict) -> dict:
    """Сводная аналитика портфеля проектов (этап 7 blueprint 52, D-73) — только чтение из снимка pull.

    Показатели: потоки (сколько проектов дошли до конечных статусов каждого потока; медиана плана и факта в
    днях), закупки (lead-time размещённых/закрытых заказов: размещён → закрыт, медиана и опоздавшие к
    ожидаемой дате), замены в нарядах и рекламации (по источникам). Денег здесь нет: права на цены отдельные
    (ответ владельца этапа 1), в сводку идут только количества и сроки. Доступ — по праву norm.read
    (производственное руководство); пользователь видит только те проекты/заказы/наряды, что ему вернул pull.
    """
    from vector.core.delivery import STREAM_LABELS_RU, stream_facts

    # ---------- потоки проектов
    ev_by_project: dict[str, list[dict]] = {}
    for e in events or []:
        ev_by_project.setdefault(e.get("project_id"), []).append(e)
    stream_rows = []
    for kind in [k for k in STREAM_ORDER if k in STREAM_LABELS_RU]:
        terminal = 0
        plan_days: list[int] = []
        fact_days: list[int] = []
        for w in streams or []:
            if w.get("kind") != kind:
                continue
            contract = wf.get("workstream_" + kind, {})
            if w.get("status") in (contract.get("terminal") or []):
                terminal += 1
            pdays = _days(w.get("planned_start"), w.get("planned_due"))
            if pdays is not None:
                plan_days.append(pdays)
            f = stream_facts(ev_by_project.get(w.get("project_id")), wf).get(kind, {})
            fdays = _days(f.get("start"), f.get("finish"))
            if fdays is not None:
                fact_days.append(fdays)
        stream_rows.append({"kind": kind, "label": STREAM_LABELS_RU.get(kind, kind), "terminal": terminal,
                            "plan_median": _median_label(plan_days), "fact_median": _median_label(fact_days),
                            "plan_n": len(plan_days), "fact_n": len(fact_days)})
    # ---------- закупки: lead-time размещённых/закрытых заказов
    leads: list[int] = []
    lates: list[int] = []
    total_placed = 0
    for o in purchase_orders or []:
        if o.get("status") not in ("placed", "closed"):
            continue
        total_placed += 1
        lead = _days((o.get("placed_at") or "")[:10], (o.get("closed_at") or "")[:10])
        if lead is not None:
            leads.append(lead)
        late = _days(o.get("expected_on"), (o.get("closed_at") or "")[:10])
        if late is not None and late > 0:
            lates.append(late)
    # ---------- производство: замены в составе нарядов
    subs = sum(len(w.get("substitutions") or []) for w in work_orders or [])
    # ---------- качество: рекламации по источникам
    claim_rows = []
    for src, label in (("customer", "От заказчика"), ("internal", "Внутренние")):
        mine = [c for c in claims or [] if c.get("source") == src]
        claim_rows.append({"source": src, "label": label, "total": len(mine),
                           "closed": sum(1 for c in mine if c.get("status") == "closed")})
    # ---------- проекты по статусам (грубая рамка workflows project)
    pr_labels = (wf.get("project") or {}).get("labels_ru", {})
    statuses: dict[str, int] = {}
    for p in projects or []:
        statuses[p.get("status") or ""] = statuses.get(p.get("status") or "", 0) + 1
    project_rows = [{"status": s, "label": pr_labels.get(s, s or "—"), "count": n}
                    for s, n in sorted(statuses.items())]
    return {
        "projects_total": len(projects or []), "project_rows": project_rows,
        "active_risks": sum(1 for r in risks or [] if r.get("state") == "active"),
        "streams": stream_rows,
        "procurement": {"placed_total": total_placed, "lead_median": _median_label(leads), "lead_n": len(leads),
                        "late_count": len(lates), "late_median": _median_label(lates)},
        "production": {"work_orders": len(work_orders or []), "substitutions": subs},
        "claims": claim_rows, "claims_total": len(claims or []),
    }
