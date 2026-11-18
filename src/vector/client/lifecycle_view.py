"""Представление раздела «Изделия и паспорта» (этап 6, сценарий С5 шаги 7–9; D-38) — чистые функции без Qt.

Карточка экземпляра изделия:

* реквизиты и точные связи — проект, изделие (комплект), наряд и SHA-256 его снимка, техпроцесс (редакция и хэш),
  версия расчёта (спецификация), SHA-256 состава экземпляра (проверяется при показе);
* состав одного изделия (количество на единицу — из снимка наряда; строка, заменённая по наряду, — «на наряд»);
* паспорт (черновик) по шаблону-мастеру: разделы, испытания, документы с точными версиями; чего ещё не хватает;
  журнал черновых экспортов. Черновик можно править без связи (очередь); экспорт — только при связи и только РП;
* история эксплуатации: передача заказчику, сервисные события, замены (только при связи).

Флаги can_* — подсказки интерфейса по ролям и состоянию; решает всегда сервер.
"""
from __future__ import annotations

from typing import Any, Optional

from vector.core import lifecycle as lc
from vector.core import production as prod

from .production_view import _project_roles, duration_label, when
from .stock_view import as_of_label

STATUS_TONES = {"registered": "info", "handed_over": "success"}
EVENT_ICONS = {"handover": "truck", "service": "wrench", "replacement": "arrow-left-right"}
TEST_TONES = {"pass": "success", "fail": "danger"}
TEST_ICONS = {"pass": "circle-check", "fail": "circle-x"}


def _day(iso_d: Optional[str]) -> str:
    """«2026-11-16» → «16.11.2026» (пусто — «—»)."""
    if not iso_d:
        return "—"
    y, m, d = (str(iso_d)[:10].split("-") + ["", "", ""])[:3]
    return f"{d}.{m}.{y}" if d else str(iso_d)


def _qty(v: Any) -> str:
    """«10.5000» → «10,5»; пусто — «—»."""
    if v is None or v == "":
        return "—"
    s = str(v)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _short(h: Optional[str]) -> str:
    """Короткая запись SHA-256 для подписи: первые 12 символов и многоточие."""
    return (h[:12] + "…") if h else "—"


def _passport_row(p: dict, inst: dict, people: dict[str, str], manage_export: bool, edit: bool, online: bool,
                  doc_names: Optional[dict] = None) -> dict:
    """Паспорт для карточки: разделы шаблона с содержимым, испытания, документы, замечания, журнал экспортов.

    Паспорт, созданный без связи и ещё не подтверждённый сервером, не знает своего шаблона и подписей документов —
    берётся текущий шаблон-мастер, а названия документов — из справочника документов проекта (doc_names).
    """
    key = (p.get("template_code"), p.get("template_version")) if p.get("template_code") else lc.CURRENT_TEMPLATE
    t = lc.PASSPORT_TEMPLATES.get(key)
    doc_names = doc_names or {}
    fields = p.get("fields") or {}
    tests = [{**x, "result_label": lc.TEST_RESULTS_RU.get(x.get("result"), x.get("result")),
              "tone": TEST_TONES.get(x.get("result"), "neutral"), "date_label": _day(x.get("date"))}
             for x in p.get("tests") or []]
    docs = [{**d, "label": f"{d.get('code')} — {d.get('title')}" if d.get("code") else doc_names.get(d.get("document_id"), "Документ"),
             "version_label": f"версия {d.get('version_no')}",
             "sha_short": _short(d.get("sha256"))} for d in p.get("documents") or []]
    sections = []
    for s in (t or {}).get("sections", []):
        if s["kind"] == "text":
            sections.append({"key": s["key"], "title": s["title"], "hint": s["hint"], "text": fields.get(s["key"]) or "",
                             "empty": not (fields.get(s["key"]) or "").strip()})
    exports = [{**e, "label": f"Экспорт № {e.get('export_no')} · {when(e.get('exported_at'))} · {people.get(e.get('exported_by'), '—')}",
                "sha_short": _short(e.get("content_hash"))} for e in p.get("exports") or []]
    problems = lc.passport_problems(t, fields, p.get("tests") or []) if t else ["Шаблон паспорта неизвестен этой версии программы."]
    pending = bool(p.get("_pending"))
    return {"id": p["id"], "version": p.get("version") or 0, "status_label": lc.PASSPORT_STATUS_LABELS_RU.get(p.get("status"), "Черновик"),
            "template_label": f"{t['title']}, версия шаблона {t['version']}" if t else f"{p.get('template_code')} {p.get('template_version')}",
            "template_ok": p.get("template_ok", True) is not False, "fields": fields, "sections": sections,
            "tests": tests, "documents": docs, "problems": problems, "exports": exports,
            "content_hash": p.get("content_hash") or "", "pending": pending,
            "updated_label": f"{people.get(p.get('updated_by'), '—')}, {when(p.get('updated_at'))}" if p.get("updated_at") else "",
            "can_edit": edit, "can_export": manage_export and online and not pending and bool(p.get("content_hash")),
            "may_export": manage_export}


def _instance_row(i: dict, passport: Optional[dict], proles: set, roles: set, online: bool, people: dict[str, str]) -> dict:
    """Экземпляр для списка и карточки: подписи, связи, состав, операции, история, флаги действий."""
    comp = i.get("composition") or {}
    wo = comp.get("work_order") or {}
    manage = "rp" in proles or "dept_head" in roles
    edit = "rp" in proles or ("lead_engineer" in roles and bool(proles))
    lines = [{"code": ln.get("code") or "—", "name": ln.get("name") or "",
              "qty_label": (f"{_qty(ln.get('qty_per_unit'))} {ln.get('unit')}" if ln.get("qty_per_unit")
                            else f"{_qty(ln.get('qty'))} {ln.get('unit')} на наряд"),
              "substituted": bool(ln.get("substituted")), "line_id": ln.get("line_id")} for ln in comp.get("lines") or []]
    events = [{**e, "date_label": _day(e.get("date")), "icon": EVENT_ICONS.get(e.get("kind"), "history"),
               "who": people.get(e.get("created_by"), "—")
               + (f" (замещает: {people.get(e.get('on_behalf_of'), '—')})" if e.get("on_behalf_of") else "")}
              for e in i.get("events") or []]
    ops = [{**o, "title": f"{o.get('op_no')}. {o.get('name')}", "who": people.get(o.get("assignee_id"), "—"),
            "fact_label": duration_label(o.get("fact_seconds") or 0), "finished_label": when(o.get("finished_at")),
            "state_label": prod.OP_STATE_LABELS_RU.get(o.get("state"), o.get("state"))} for o in i.get("operations") or []]
    links = [("Проект", f"{i.get('project_code')} — {(comp.get('project') or {}).get('title') or ''}".strip(" —")),
             ("Изделие (комплект)", f"{i.get('kit_code')} — {i.get('kit_title') or ''}".strip(" —")),
             ("Наряд", f"№ {i.get('wo_doc_no')} на {wo.get('qty')} шт., закрыт {when(wo.get('closed_at'))}"),
             ("Снимок наряда, SHA-256", wo.get("snapshot_hash") or "—"),
             ("Техпроцесс", f"редакция {(comp.get('tp') or {}).get('revision_no')} — {(comp.get('tp') or {}).get('title') or ''}"),
             ("Спецификация", f"версия расчёта № {(comp.get('spec') or {}).get('version_no')}"),
             ("Состав экземпляра, SHA-256", i.get("composition_hash") or "—")]
    status = i.get("status") or "registered"
    return {"id": i["id"], "project_id": i.get("project_id"), "kit_code": i.get("kit_code") or "",
            "label": f"{i.get('kit_code')} {lc.serial_label(i.get('series') or '', i.get('serial_no') or '')}",
            "title": i.get("kit_title") or "", "serial_no": i.get("serial_no") or "", "series": i.get("series") or "",
            "project_code": i.get("project_code") or "", "wo_label": f"наряд № {i.get('wo_doc_no')}",
            "status": status, "status_label": lc.INSTANCE_STATUS_LABELS_RU.get(status, status),
            "status_tone": STATUS_TONES.get(status, "neutral"), "version": i.get("version") or 0,
            "composition_ok": i.get("composition_ok", True) is not False,
            "links": [{"k": k, "v": v} for k, v in links], "lines": lines, "substitutions": comp.get("substitutions") or [],
            "operations": ops, "events": events, "passport": passport, "has_passport": passport is not None,
            "pending": bool(i.get("_pending")) or bool(passport and passport.get("pending")),
            "manage": manage, "can_handover": manage and online and status == "registered",
            "can_service": manage and online and status == "handed_over", "can_replace": manage and online,
            "can_passport": edit and passport is None, "may_passport": edit}


def lifecycle_view(instances: list[dict], passports: list[dict], reg_wos: list[dict], docs: list[dict],
                   assignments: list[dict], deputies: list[dict], people: dict[str, str], me: dict, online: bool,
                   last_pull: str) -> dict[str, Any]:
    """Всё для раздела «Изделия и паспорта»: список экземпляров с карточками, варианты для форм, подсказки."""
    me_id = me.get("user_id") or ""
    roles = set(me.get("roles") or [])
    proles = _project_roles(me_id, assignments, deputies)
    by_inst = {p.get("instance_id"): p for p in passports or []}
    doc_names = {d["id"]: f"{d.get('code')} — {d.get('title')}" for d in docs or []}
    rows = []
    for i in instances or []:
        pr = proles.get(i.get("project_id"), set())
        p = by_inst.get(i["id"])
        edit = "rp" in pr or ("lead_engineer" in roles and bool(pr))
        prow = _passport_row(p, i, people, "rp" in pr, edit, online, doc_names) if p else None
        rows.append(_instance_row(i, prow, pr, roles, online, people))
    rows.sort(key=lambda r: (r["project_code"], r["kit_code"], r["series"], r["serial_no"]))
    reg = [{"id": w["id"], "left": w.get("left") or 0, "kit_code": w.get("kit_code"),
            "label": f"{w.get('project_code')} · наряд № {w.get('doc_no')} · {w.get('kit_code')} — осталось {w.get('left')} из {w.get('qty')}"}
           for w in reg_wos or [] if (w.get("left") or 0) > 0]
    t = lc.template(*lc.CURRENT_TEMPLATE)
    return {
        "online": online, "as_of": as_of_label(last_pull), "items": rows,
        # Показывать ли раздел в меню (подсказка): технологам и руководителю отдела, команде любого проекта и всем,
        # кому сервер отдал экземпляры или наряды для регистрации.
        "sees": bool(rows or reg or proles or roles & {"technologist", "dept_head"}),
        "counts": {"total": len(rows), "handed_over": sum(1 for r in rows if r["status"] == "handed_over"),
                   "no_passport": sum(1 for r in rows if not r["has_passport"])},
        "register_options": reg, "may_register": bool(reg), "can_register": online and bool(reg),
        "doc_options": [{"id": d["id"], "project_id": d.get("project_id"), "version_no": d.get("current_version_no"),
                         "label": f"{d.get('code')} — {d.get('title')} (версия {d.get('current_version_no')})"} for d in docs or []],
        "service_kinds": [{"id": k, "label": v} for k, v in lc.SERVICE_KINDS_RU.items()],
        "test_results": [{"id": k, "label": v, "icon": TEST_ICONS.get(k, "circle-check")} for k, v in lc.TEST_RESULTS_RU.items()],
        "text_sections": [{"key": s["key"], "title": s["title"], "hint": s["hint"]} for s in t["sections"] if s["kind"] == "text"],
        "draft_label": lc.DRAFT_LABEL,
        "empty": ("Экземпляров пока нет. После закрытия наряда РП проекта регистрирует экземпляры с заводскими номерами."
                  if reg or "dept_head" in roles else "Экземпляров изделий пока нет."),
    }


def passport_payload(fields: dict, tests: list[dict], docs: list[dict]) -> dict:
    """Данные формы паспорта → payload команды: тексты без пробелов по краям; испытания без пустых строк
    (строка, где ничего не заполнено, отбрасывается); документы — {document_id, version_no} целыми.
    """
    f = {k: (v or "").strip() for k, v in (fields or {}).items()}
    t = []
    for x in tests or []:
        row = {k: (x.get(k) or "").strip() if isinstance(x.get(k), str) or x.get(k) is None else x.get(k)
               for k in ("name", "result", "date", "protocol", "note")}
        if not any(row[k] for k in ("name", "date", "protocol", "note")):
            continue
        t.append(row)
    d = []
    for x in docs or []:
        try:
            d.append({"document_id": str(x["document_id"]), "version_no": int(x["version_no"])})
        except (KeyError, TypeError, ValueError):
            continue
    return {"fields": f, "tests": t, "documents": d}


def passport_check(fields: dict, tests: list[dict]) -> dict[str, Any]:
    """Проверка формы паспорта без сервера (правила ядра): ошибки испытаний по строкам."""
    p = passport_payload(fields, tests, [])
    try:
        lc.parse_tests(p["tests"])
        return {"ok": True, "message": "", "by_line": {}}
    except lc.LifecycleError as e:
        by = {}
        kept = [x for x in tests or [] if any((x.get(k) or "").strip() for k in ("name", "date", "protocol", "note")
                                              if isinstance(x.get(k), str))]
        for pr in e.details.get("problems", []):
            if pr["line"] < len(kept):
                by[str((tests or []).index(kept[pr["line"]]))] = pr["message_ru"]
        return {"ok": False, "message": e.message_ru, "by_line": by}
