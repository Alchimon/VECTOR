"""Представление РКД комплекта для интерфейса (D-64): ревизии документов, замечания, трансмитталы, профили шифров.

Чистые функции над снимком pull (meta-списки) — без сети и Qt, чтобы тестировать правила отображения отдельно.
Кнопки действий — ПОДСКАЗКА по роли из кэша назначений: окончательное решение принимает сервер (отказ придёт
понятным сообщением). Что можно без связи: только чтение; все команды РКД — online_required (сервер перепроверяет
состояние в момент эффекта).
"""
from __future__ import annotations

from typing import Any, Optional

from vector.core import rkd as rk

# Тон чипа статуса ревизии (цвета — Theme в QML).
_STATUS_TONE = {"in_work": "neutral", "ready_for_rp": "info", "returned_to_engineer": "danger",
                "ready_to_transmit": "primary", "sent": "primary", "customer_comments": "warning",
                "revision_in_work": "warning", "approved": "success"}


def _project_roles(me_id: str, assignments: list[dict], project_id: str) -> set[str]:
    """Мои действующие назначения в проекте (по снимку назначений из pull)."""
    return {a["project_role"] for a in assignments
            if a.get("project_id") == project_id and a.get("user_id") == me_id}


def _fmt(s: Optional[str]) -> str:
    """Метка времени «ГГГГ-ММ-ДД ЧЧ:ММ» из ISO."""
    return (s or "")[:16].replace("T", " ")


def _comment_row(c: dict, responses: list[dict], people: dict[str, str]) -> dict:
    """Строка замечания: источник, страница, текст, статус, ответы (каждый закрывает замечание — сервер)."""
    resp = [{"text": r["text"], "author_name": people.get(r.get("created_by") or "", ""),
             "created_label": _fmt(r.get("created_at")), "has_file": bool(r.get("sha256"))}
            for r in responses if r.get("comment_id") == c["id"]]
    return {"id": c["id"], "seq": c["seq"], "source": c["source"], "source_label": c["source_label"],
            "page": c.get("page") or "", "text": c["text"], "external_ref": c.get("external_ref") or "",
            "status": c["status"], "status_label": "Закрыто" if c["status"] == "answered" else "Открыто",
            "version": c["version"], "author_name": people.get(c.get("created_by") or "", ""),
            "created_label": _fmt(c.get("created_at")), "responses": resp}


def _rev_actions(rev: dict, roles: set[str], open_comments: int, online: bool) -> list[dict]:
    """Доступные действия с ревизией — подсказка по роли; сервер перепроверяет.

    Авторская сторона (docrev.transition — участник проекта): подача на проверку, возврат в работу, доработка по
    замечаниям. Решения проверки (docrev.review — РП проекта): возврат на доработку, разрешение/отзыв отправки,
    «Согласована заказчиком» (с основанием). Всё — только при связи.
    """
    st = rev["status"]
    out: list[dict] = []
    if not online:
        return out
    author = bool(roles & {"gip", "rp", "participant"})
    reviewer = "rp" in roles
    if author and st == "in_work":
        out.append({"kind": "transition", "to": "ready_for_rp", "label": "На проверку РП", "reason": False})
    if reviewer and st == "ready_for_rp":
        out.append({"kind": "transition", "to": "ready_to_transmit", "label": "Разрешить отправку", "reason": False})
        out.append({"kind": "transition", "to": "returned_to_engineer", "label": "Вернуть на доработку",
                    "reason": True, "danger": True})
    if author and st == "returned_to_engineer":
        out.append({"kind": "transition", "to": "in_work", "label": "Взять в работу", "reason": False})
    if reviewer and st == "ready_to_transmit":
        out.append({"kind": "transition", "to": "in_work", "label": "Отозвать разрешение", "reason": True,
                    "danger": True})
    if reviewer and st in ("sent", "customer_comments"):
        out.append({"kind": "transition", "to": "approved", "label": "Согласована заказчиком", "reason": True,
                    "reason_label": "Основание (номер письма/протокола заказчика)",
                    "blocked": st == "customer_comments" and open_comments > 0,
                    "blocked_hint": "Сначала закройте все замечания ответом."})
    if reviewer and st == "customer_comments" and open_comments == 0:
        out.append({"kind": "transition", "to": "revision_in_work", "label": "В доработку по замечаниям",
                    "reason": False})
    # Внутреннее замечание — в ходе проверки (ревизия на проверке или возвращена); замечание заказчика — на
    # отправленную (регистрирует РП, с основанием).
    if author and st in ("ready_for_rp", "returned_to_engineer"):
        out.append({"kind": "comment", "source": "internal", "label": "Внутреннее замечание"})
    if reviewer and st in ("sent", "customer_comments"):
        out.append({"kind": "comment", "source": "customer", "label": "Замечание заказчика"})
    return out


def revision_rows(kit_id: str, documents: list[dict], revisions: list[dict], comments: list[dict],
                  responses: list[dict], issues: list[dict], people: dict[str, str], me_id: str,
                  assignments: list[dict], online: bool) -> list[dict]:
    """Строки ревизий комплекта по документам: шифр, статус, штамп выдачи, замечания, действия."""
    doc_by_id = {d["id"]: d for d in documents if d.get("kit_id") == kit_id}
    issue_by_rev = {i["revision_id"]: i for i in issues}
    out = []
    for rev in revisions:
        doc = doc_by_id.get(rev.get("document_id"))
        if doc is None:
            continue
        roles = _project_roles(me_id, assignments, doc.get("project_id"))
        c_rows = [c for c in comments if c.get("revision_id") == rev["id"]]
        open_comments = sum(1 for c in c_rows if c.get("status") == "open")
        issue = issue_by_rev.get(rev["id"])
        out.append({**{k: rev[k] for k in ("id", "document_id", "seq", "label", "status", "status_label",
                                           "version", "note")},
                    "doc_code": doc["code"], "doc_title": doc["title"], "doc_version_id": rev["doc_version_id"],
                    "tone": _STATUS_TONE.get(rev["status"], "neutral"),
                    "issue_code": issue["status_code"] if issue else "",
                    "issue_label": issue["status_label"] if issue else "",
                    "issue_version": issue["version"] if issue else 0,
                    "comments_open": open_comments, "comments_total": len(c_rows),
                    "comments": [_comment_row(c, responses, people) for c in c_rows],
                    "author_name": people.get(rev.get("created_by") or "", ""),
                    "created_label": _fmt(rev.get("created_at")),
                    "terminal": rev["status"] == "approved",
                    "actions": _rev_actions(rev, roles, open_comments, online)})
    # Порядок: документ по коду, внутри — новые ревизии сверху.
    return sorted(out, key=lambda r: (r["doc_code"].lower(), -r["seq"]))


def doc_options(kit_id: str, documents: list[dict], revisions: list[dict]) -> list[dict]:
    """Документы комплекта для диалога «Новая ревизия» с подсказкой занятых шифров."""
    out = []
    for d in sorted((x for x in documents if x.get("kit_id") == kit_id), key=lambda x: x["code"].lower()):
        labels = [r["label"] for r in revisions if r.get("document_id") == d["id"]]
        out.append({"id": d["id"], "label": f"{d['code']} · {d['title']}",
                    "hint": ("шифры: " + ", ".join(labels)) if labels else "ревизий ещё нет"})
    return out


def profile_rows(project_id: str, kit_id: Optional[str], profiles: list[dict],
                 people: dict[str, str]) -> list[dict]:
    """Профили шифров проекта (и их область: проект/комплект) для шапки вкладки и диалога управления."""
    out = []
    for p in profiles:
        if p.get("project_id") != project_id:
            continue
        statuses = p.get("issue_statuses") or []
        out.append({"id": p["id"], "name": p["name"], "kit_id": p.get("kit_id") or "",
                    "scope_label": "Комплект" if p.get("kit_id") else "Проект",
                    "on_this_kit": bool(p.get("kit_id")) and p.get("kit_id") == kit_id,
                    "label_pattern": p.get("label_pattern") or "", "label_example": p.get("label_example") or "",
                    "statuses_label": ", ".join(s["code"] for s in statuses) or "без словаря",
                    "active": bool(p.get("active")), "version": p["version"],
                    "revisions": p.get("revisions", 0),
                    "updated_label": _fmt(p.get("updated_at"))})
    return sorted(out, key=lambda x: (x["kit_id"] != "", x["name"].lower()))


def effective_profile_hint(project_id: str, kit_id: Optional[str], profiles: list[dict]) -> dict:
    """Действующий профиль области (комплектный важнее проектного) — для подсказки шифра в диалоге ревизии."""
    rows = [p for p in profiles if p.get("project_id") == project_id and p.get("active")]
    kit_level = next((p for p in rows if p.get("kit_id") and p.get("kit_id") == kit_id), None)
    p = kit_level or next((x for x in rows if not x.get("kit_id")), None)
    if p is None:
        return {"name": "", "label_pattern": "", "label_example": ""}
    return {"name": p["name"], "label_pattern": p.get("label_pattern") or "",
            "label_example": p.get("label_example") or ""}


def transmittal_rows(project_id: str, transmittals: list[dict], t_items: list[dict], packages: list[dict],
                     revisions: list[dict], documents: list[dict], people: dict[str, str],
                     online: bool, roles: set[str]) -> list[dict]:
    """Трансмитталы проекта: состав (документ + шифр ревизии), пакет (имя/размер/скачивание), доставка."""
    rev_by_id = {r["id"]: r for r in revisions}
    doc_by_id = {d["id"]: d for d in documents}
    pkg_by_t = {p["transmittal_id"]: p for p in packages}
    out = []
    for t in transmittals:
        if t.get("project_id") != project_id:
            continue
        items = []
        for it in sorted((x for x in t_items if x.get("transmittal_id") == t["id"]), key=lambda x: x["position"]):
            rev = rev_by_id.get(it["revision_id"])
            doc = doc_by_id.get(rev["document_id"]) if rev else None
            items.append({"position": it["position"], "label": f"{doc['code']} рев.{rev['label']}" if doc and rev else "—"})
        pkg = pkg_by_t.get(t["id"])
        out.append({"id": t["id"], "seq": t["seq"], "recipient": t["recipient"], "channel": t["channel"],
                    "channel_label": t["channel_label"], "status": t["status"],
                    "status_label": "Доставлен" if t["status"] == "delivered" else "Отправлен",
                    "tone": "success" if t["status"] == "delivered" else "info",
                    "external_ref": t.get("external_ref") or "", "note": t.get("note") or "",
                    "version": t["version"], "items": items,
                    "author_name": people.get(t.get("created_by") or "", ""),
                    "created_label": _fmt(t.get("created_at")), "delivered_label": _fmt(t.get("delivered_at")),
                    "package_name": pkg["package_name"] if pkg else "",
                    "package_size": pkg["size_bytes"] if pkg else 0,
                    "has_package": pkg is not None,
                    "can_download": pkg is not None and online,
                    "can_register_receipt": t["status"] == "sent" and online and "rp" in roles})
    return sorted(out, key=lambda x: -x["seq"])


def kit_rkd(kit_id: str, project_id: str, documents: list[dict], revisions: list[dict], comments: list[dict],
            responses: list[dict], issues: list[dict], profiles: list[dict], transmittals: list[dict],
            t_items: list[dict], packages: list[dict], people: dict[str, str], me_id: str,
            assignments: list[dict], online: bool) -> dict:
    """Весь срез вкладки «РКД» комплекта: ревизии, профили, трансмитталы проекта."""
    roles = _project_roles(me_id, assignments, project_id)
    revs = [r for r in revisions if (d := next((x for x in documents if x["id"] == r.get("document_id")), None))
            and d.get("kit_id") == kit_id]
    open_comments = sum(1 for r in revs for c in comments
                        if c.get("revision_id") == r["id"] and c.get("status") == "open")
    return {"revisions": revision_rows(kit_id, documents, revisions, comments, responses, issues, people,
                                       me_id, assignments, online),
            "doc_options": doc_options(kit_id, documents, revisions),
            "profiles": profile_rows(project_id, kit_id, profiles, people),
            "profile_hint": effective_profile_hint(project_id, kit_id, profiles),
            "transmittals": transmittal_rows(project_id, transmittals, t_items, packages, revisions, documents,
                                             people, online, roles),
            "counts": {"revisions": len(revs), "open_comments": open_comments,
                       "profiles": sum(1 for p in profiles if p.get("project_id") == project_id),
                       "transmittals": sum(1 for t in transmittals if t.get("project_id") == project_id)},
            "can_manage_profiles": online and bool(roles & {"gip", "rp"})}
