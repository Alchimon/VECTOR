"""Представление раздела «Производство» (этап 5, сценарий С5 шаги 1–6; D-36) — чистые функции без Qt.

Три вкладки:

* «Мои операции» — исполнителю (роль «Производство»): операции, назначенные ему в открытых нарядах, с кнопками
  «Начать / Пауза / Продолжить / Завершить / Проблема». Работает полностью без связи: событие ставится в очередь, а
  локальная копия операции сразу показывает новое состояние и таймер (те же правила ядра, что на сервере —
  vector.core.production.apply_event). Таймер — по часам этого компьютера (A-44).
* «Наряды» — РП проекта (мастер, A-42), руководителю отдела, технологу, складу: снимок (с проверкой SHA-256),
  операции и исполнители, действующий состав с заменами, материалы со склада, выпуск / назначение / замена /
  закрытие / отмена (только при связи).
* «Техпроцессы» — технологу: черновики ТП (можно без связи) и утверждение (не автор, при связи).

Флаги can_* — подсказки интерфейса по ролям и состоянию; решает всегда сервер.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from vector.core import production as prod

from .calc_view import fmt_decimal
from .stock_view import _date_ru, as_of_label, qty_label

# Статусы проекта, в которых возможны ТП и наряды (как на сервере: PRODUCTION_PROJECT_STATUSES).
PRODUCTION_PROJECT_STATUSES = ("rkd_in_progress", "rkd_issued", "production")
TP_TONES = {"draft": "warning", "approved": "success"}
WO_TONES = {"draft": "neutral", "released": "info", "in_progress": "primary", "completed": "success", "closed": "neutral",
            "cancelled": "danger"}
OP_TONES = {"pending": "neutral", "running": "primary", "paused": "warning", "done": "success"}
MATERIAL_TONES = {"ok": "success", "short": "danger", "unit_mismatch": "danger", "not_stock": "neutral"}
# Кнопки исполнителя по состоянию операции: (событие, подпись, значок, вид кнопки).
OP_ACTIONS = {"pending": [("start", "Начать", "play", "primary")],
              "running": [("pause", "Пауза", "pause", "soft"), ("finish", "Завершить", "circle-check", "primary")],
              "paused": [("resume", "Продолжить", "play", "primary"), ("finish", "Завершить", "circle-check", "soft")],
              "done": []}


def _dt(s: Optional[str]) -> Optional[datetime]:
    """ISO-строка → datetime UTC (None/ошибка → None)."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def when(s: Optional[str]) -> str:
    """«2026-11-16T09:05:00+00:00» → «16.11.2026 09:05» (местное время компьютера); пусто — «—»."""
    d = _dt(s)
    return d.astimezone().strftime("%d.%m.%Y %H:%M") if d else "—"


def duration_label(seconds: int) -> str:
    """Секунды → «1 ч 05 мин» / «12 мин» / «40 с» (для факта времени операции)."""
    s = max(0, int(seconds or 0))
    if s < 60:
        return f"{s} с"
    h, m = divmod(s // 60, 60)
    return f"{h} ч {m:02d} мин" if h else f"{m} мин"


def minutes_label(value: Any) -> str:
    """Норма в минутах («90.0000») → «90 мин»; больше часа — «1 ч 30 мин» (дробные минуты — как есть)."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "—"
    if d >= 60 and d == d.to_integral_value():
        h, m = divmod(int(d), 60)
        return f"{h} ч {m:02d} мин"
    return f"{fmt_decimal(str(d))} мин"


def live_seconds(op: dict, now: datetime) -> int:
    """Факт операции на «сейчас»: накопленное + текущий интервал «в работе» (для показа таймера)."""
    total = int(op.get("fact_seconds") or 0)
    since = _dt(op.get("running_since"))
    if op.get("state") == "running" and since is not None:
        total += max(0, int((now - since).total_seconds()))
    return total


def _project_roles(me_id: str, assignments: list[dict], deputies: list[dict]) -> dict[str, set]:
    """Роли текущего пользователя по проектам: свои назначения и замещения в проекте (D-35)."""
    out: dict[str, set] = {}
    for a in assignments or []:
        if a.get("user_id") == me_id:
            out.setdefault(a.get("project_id"), set()).add(a.get("project_role"))
    for d in deputies or []:
        if d.get("user_id") == me_id:
            out.setdefault(d.get("project_id"), set()).add(d.get("project_role"))
    return out


def tp_problems(tp: dict) -> list[str]:
    """Чего не хватает ТП для утверждения (подсказка; проверяет сервер)."""
    out = []
    if not tp.get("operations"):
        out.append("Нет операций.")
    if not tp.get("spec_version_id"):
        out.append("Не выбрана спецификация (зафиксированная версия расчёта комплекта).")
    return out


def _tp_row(tp: dict, me_id: str, roles: set, online: bool, people: dict[str, str], specs: dict) -> dict:
    """ТП для списка и карточки: подписи, операции, состав, утверждение, что можно сделать."""
    draft = tp.get("status") == "draft"
    pending = bool(tp.get("_pending"))
    appr = tp.get("approval") or None
    spec = tp.get("spec") or specs.get(str(tp.get("spec_version_id") or "")) or None
    problems = tp_problems(tp)
    ops = [{**o, "norm_label": minutes_label(o.get("norm_min")), "work_center": o.get("work_center") or "",
            "note": o.get("note") or ""} for o in tp.get("operations") or []]
    norm_total = sum((Decimal(str(o.get("norm_min") or 0)) for o in tp.get("operations") or []), Decimal(0))
    own = tp.get("created_by") == me_id
    may_approve = ("technologist" in roles and not own) or "dept_head" in roles
    return {
        "id": tp["id"], "title": tp.get("title") or "", "project_id": tp.get("project_id"), "kit_id": tp.get("kit_id"),
        "project_code": tp.get("project_code") or "", "kit_code": tp.get("kit_code") or "",
        "kit_label": " · ".join(x for x in (tp.get("project_code"), tp.get("kit_code"), tp.get("kit_title")) if x),
        "revision_no": tp.get("revision_no"), "revision_label": f"ред. {tp['revision_no']}" if tp.get("revision_no") else "новый",
        "status": tp.get("status") or "draft", "status_label": prod.TP_STATUS_LABELS_RU.get(tp.get("status") or "draft", ""),
        "status_tone": TP_TONES.get(tp.get("status") or "draft", "neutral"), "pending": pending,
        "note": tp.get("note") or "", "operations": ops, "op_count": len(ops),
        "norm_label": minutes_label(norm_total) + " на изделие",
        "spec_version_id": str(tp.get("spec_version_id") or ""),
        "spec_label": f"Версия расчёта № {spec['version_no']}" if spec and spec.get("version_no") else "не выбрана",
        "spec_lines": [{**ln, "qty_label": qty_label(ln.get("qty"), ln.get("unit") or "")} for ln in (spec or {}).get("lines") or []],
        "content_hash": tp.get("content_hash") or "",
        "author_name": people.get(tp.get("created_by") or "", ""),
        "approved": appr is not None,
        "approved_label": (f"Утвердил(а) {people.get(appr.get('approved_by') or '', '')}, {when(appr.get('approved_at'))}"
                           + (f" — за {people.get(appr['on_behalf_of'], '')}" if appr.get("on_behalf_of") else "")) if appr else "",
        "approval_valid": tp.get("approval_valid") is not False,
        "problems": problems,
        # Правка — технологу, только черновик; без связи тоже (очередь).
        "can_edit": draft and "technologist" in roles,
        # Утверждение — не автор (технолог) или руководитель отдела, при связи, без неотправленных правок.
        "may_approve": draft and may_approve, "is_own": own and "technologist" in roles and "dept_head" not in roles,
        "can_approve": draft and may_approve and online and not pending and not problems,
        "version": tp.get("_server_version") or tp.get("version") or 0,
    }


def _op_row(op: dict, me_id: str, now: datetime, people: dict[str, str]) -> dict:
    """Операция наряда: подписи, таймер, кнопки исполнителя (если назначена мне и наряд открыт)."""
    state = op.get("state") or "pending"
    mine = op.get("assignee_id") == me_id
    open_wo = op.get("wo_status") in ("released", "in_progress")
    secs = live_seconds(op, now)
    acts = [{"kind": k, "label": lb, "icon": ic, "btn": b} for k, lb, ic, b in OP_ACTIONS.get(state, [])] if mine and open_wo else []
    events = [{**e, "when": when(e.get("device_time")), "who": people.get(e.get("user_id") or "", "")
               if e.get("user_id") else ("вы (не отправлено)" if e.get("local") else ""),
               "local": bool(e.get("local"))} for e in op.get("events") or []]
    return {
        "id": op["id"], "work_order_id": op.get("work_order_id"), "op_no": op.get("op_no"), "name": op.get("name") or "",
        "title": f"{op.get('op_no')}. {op.get('name') or ''}", "work_center": op.get("work_center") or "",
        "wo_doc_no": op.get("wo_doc_no"), "wo_label": f"Наряд № {op.get('wo_doc_no')}", "wo_status": op.get("wo_status"),
        "wo_qty": op.get("wo_qty"),
        "state": state, "state_label": prod.OP_STATE_LABELS_RU.get(state, state), "state_tone": OP_TONES.get(state, "neutral"),
        "assignee_id": op.get("assignee_id") or "", "assignee_name": people.get(op.get("assignee_id") or "", "") or "не назначен",
        "mine": mine, "norm_label": minutes_label(op.get("norm_total_min")),
        "fact_seconds": int(op.get("fact_seconds") or 0), "running_since": op.get("running_since") or "",
        "fact_label": duration_label(secs), "clock_label": clock_label(secs), "running": state == "running",
        "qty_done": op.get("qty_done"), "problem_count": int(op.get("problem_count") or 0),
        "pending": bool(op.get("_pending")), "actions": acts, "can_problem": mine and open_wo and state != "done",
        "events": events, "version": op.get("_server_version") or op.get("version") or 0,
    }


def _wo_row(wo: dict, ops: list[dict], manage: bool, substitute: bool, online: bool, people: dict[str, str]) -> dict:
    """Наряд для списка и карточки: снимок, операции, состав с заменами, материалы, что можно сделать."""
    st = wo.get("status") or "released"
    snap = wo.get("snapshot") or {}
    pending_ops = any(o["pending"] for o in ops)
    mats = [{**m, "required_label": qty_label(m.get("required"), m.get("unit") or ""),
             "issued_label": qty_label(m.get("issued"), m.get("unit") or "") if m.get("issued") is not None else "—",
             "state_label": m.get("state_label") or prod.MATERIAL_STATE_LABELS_RU.get(m.get("state"), ""),
             "tone": MATERIAL_TONES.get(m.get("state"), "neutral"), "code": m.get("code") or ""} for m in wo.get("materials") or []]
    subs = [{**s, "old_label": f"{s['old'].get('name')} — {qty_label(s['old'].get('qty'), s['old'].get('unit') or '')}",
             "new_label": f"{s['new'].get('name')} — {qty_label(s['new'].get('qty'), s['new'].get('unit') or '')}",
             "who": people.get(s.get("created_by") or "", ""), "when": when(s.get("created_at"))}
            for s in wo.get("substitutions") or []]
    open_ = st in prod.WO_OPEN
    done_ops = sum(1 for o in ops if o["state"] == "done")
    # Снимок готовности сборки (D-61): вердикт, риск-разрешения и нехватка материалов на момент выпуска.
    # Снимка нет (наряд выпущен до v33) — честное «нет данных», а не «готово».
    rd = wo.get("readiness")
    rd_risks = [f"{r['label_ru']} — до {_date_ru(r['due_on'])}: {r['reason']}" for r in (rd or {}).get("risks") or []]
    rd_short = [m for m in (rd or {}).get("materials") or [] if m["state"] == "short"]
    rd_deviations = rd_risks + ([f"Материалы обеспечены не полностью ({len(rd_short)} поз.)"] if rd_short else [])
    close_problems = []
    if st != "completed":
        close_problems.append("Не все операции выполнены.")
    if not wo.get("materials_ready", True):
        close_problems.append("Материалы выданы со склада не полностью (или единица не совпадает).")
    return {
        "id": wo["id"], "doc_no": wo.get("doc_no"), "title": wo.get("title") or "", "label": f"Наряд № {wo.get('doc_no')}",
        "project_id": wo.get("project_id"), "kit_label": " · ".join(x for x in (wo.get("project_code"), wo.get("kit_code"), wo.get("kit_title")) if x),
        "qty": wo.get("qty"), "qty_label": f"{wo.get('qty')} шт.", "tp_label": f"ТП ред. {wo.get('tp_revision_no')}",
        "status": st, "status_label": prod.WO_STATUS_LABELS_RU.get(st, st), "status_tone": WO_TONES.get(st, "neutral"),
        "snapshot_ok": wo.get("snapshot_ok") is not False, "snapshot_hash": wo.get("snapshot_hash") or "",
        "readiness": rd, "readiness_label": (rd or {}).get("verdict_label") or "нет данных",
        "readiness_tone": {"ready": "success", "ready_with_deviations": "warning"}.get((rd or {}).get("verdict"), "neutral"),
        "readiness_ok": (rd or {}).get("readiness_ok") is not False,
        "readiness_rkd_label": ((rd or {}).get("rkd") or {}).get("label_ru") or "",
        "readiness_deviations": rd_deviations,
        "spec_label": f"Версия расчёта № {(snap.get('spec') or {}).get('version_no')}" if snap.get("spec") else "",
        "spec": [{**ln, "qty_label": qty_label(ln.get("qty"), ln.get("unit") or ""), "code": ln.get("code") or ""} for ln in wo.get("spec") or []],
        "spec_hash": wo.get("spec_hash") or "", "substitutions": subs, "materials": mats,
        "materials_ready": wo.get("materials_ready") is not False,
        "operations": ops, "progress_label": f"{done_ops} из {len(ops)} операций",
        "fact_label": duration_label(sum(o["fact_seconds"] for o in ops)),
        "released_label": f"{people.get(wo.get('released_by') or '', '')}, {when(wo.get('released_at'))}",
        "closed_label": f"{people.get(wo.get('closed_by') or '', '')}, {when(wo.get('closed_at'))}" if wo.get("closed_at") else "",
        "cancel_reason": wo.get("cancel_reason") or "", "note": wo.get("note") or "", "pending": pending_ops,
        "manage": manage, "may_substitute": substitute and open_,
        "can_assign": manage and online and open_, "can_substitute": substitute and online and open_,
        "can_close": manage and online and st == "completed" and wo.get("materials_ready") is not False and not pending_ops,
        "close_problems": close_problems,
        "can_cancel": manage and online and st in ("released", "in_progress"),
        "version": wo.get("_server_version") or wo.get("version") or 0,
    }


def production_view(tps: list[dict], specs: list[dict], wos: list[dict], ops: list[dict], staff: list[dict],
                    projects: list[dict], kits: list[dict], assignments: list[dict], deputies: list[dict],
                    people: dict[str, str], me: dict, online: bool, last_pull: str,
                    now: Optional[datetime] = None, prod_kits: Optional[list[dict]] = None) -> dict[str, Any]:
    """Всё для раздела «Производство»: вкладки, списки ТП / нарядов / моих операций, варианты для форм и подсказки.

    prod_kits — комплекты из pull «production_kits» (проекты, где можно читать ТП, с кодом и статусом проекта): у
    технолога нет права читать проект целиком, поэтому комплекты для «Нового ТП» берутся отсюда; если сервер их не
    прислал (старый сервер), — из проектов и комплектов кэша. now подставляется в тестах (таймер операции и подписи
    времени); по умолчанию — текущее время.
    """
    now = now or datetime.now(timezone.utc)
    me_id = me.get("user_id") or ""
    roles = set(me.get("roles") or [])
    proles = _project_roles(me_id, assignments, deputies)
    proj = {p["id"]: p for p in projects or []}
    spec_by_id = {str(s.get("id")): s for s in specs or []}

    tp_rows = [_tp_row(t, me_id, roles, online, people, spec_by_id) for t in tps or []]
    tp_rows.sort(key=lambda r: (r["kit_label"], -(r["revision_no"] or 10 ** 6)))

    op_rows = [_op_row(o, me_id, now, people) for o in ops or []]
    by_wo: dict[str, list[dict]] = {}
    for o in op_rows:
        by_wo.setdefault(o["work_order_id"], []).append(o)
    for v in by_wo.values():
        v.sort(key=lambda o: o["op_no"] or 0)
    wo_rows = []
    for w in wos or []:
        pr = proles.get(w.get("project_id"), set())
        manage = "dept_head" in roles or "rp" in pr
        substitute = "technologist" in roles or "gip" in pr
        wo_rows.append(_wo_row(w, by_wo.get(w["id"], []), manage, substitute, online, people))
    wo_rows.sort(key=lambda r: -(r["doc_no"] or 0))

    # Выполненные операции из «моих» уходят (история — в карточке наряда): на экране только то, что ещё делать.
    my_ops = [o for o in op_rows if o["mine"] and o["wo_status"] in ("released", "in_progress") and o["state"] != "done"]
    my_ops.sort(key=lambda o: ({"running": 0, "paused": 1, "pending": 2, "done": 3}.get(o["state"], 9), o["wo_doc_no"] or 0, o["op_no"] or 0))

    # Комплекты для нового ТП: проект в работе, у комплекта нет черновика ТП (один черновик на комплект).
    with_draft = {t["kit_id"] for t in tp_rows if t["status"] == "draft"}
    if prod_kits:
        cand = [(k["id"], k.get("project_code") or "", k.get("project_status"), k.get("code") or "", k.get("title") or "")
                for k in prod_kits]
    else:
        cand = [(k["id"], (proj.get(k.get("project_id")) or {}).get("code") or "",
                 (proj.get(k.get("project_id")) or {}).get("status"), k.get("code") or "", k.get("title") or "")
                for k in kits or [] if k.get("project_id") in proj]
    kit_opts = [{"id": kid, "label": f"{pcode} · {code} · {title}".strip(" ·")}
                for kid, pcode, status, code, title in sorted(cand, key=lambda c: (c[1], c[3]))
                if status in PRODUCTION_PROJECT_STATUSES and kid not in with_draft]
    spec_opts = [{"id": str(s["id"]), "kit_id": s.get("kit_id"),
                  "label": f"Версия расчёта № {s.get('version_no')} ({len(s.get('lines') or [])} поз.)"} for s in specs or []]
    # Выпуск наряда — по утверждённому ТП с действительным утверждением; мастер проекта или руководитель отдела.
    release_tps = [{"id": t["id"], "label": f"{t['kit_label']} — {t['title']} ({t['revision_label']})", "project_id": t["project_id"],
                    "operations": t["operations"]}
                   for t in tp_rows if t["status"] == "approved" and t["approval_valid"]
                   and ("dept_head" in roles or "rp" in proles.get(t["project_id"], set()))]
    staff_opts = [{"id": "", "label": "не назначен"}] + [{"id": s["id"], "label": s.get("display_name") or ""} for s in staff or []]

    tabs = []
    if "production" in roles or my_ops:
        tabs.append({"id": "my", "label": "Мои операции" + (f" ({len(my_ops)})" if my_ops else ""), "icon": "play"})
    tabs.append({"id": "orders", "label": "Наряды", "icon": "clipboard-list"})
    if tp_rows or "technologist" in roles or "dept_head" in roles:
        tabs.append({"id": "tps", "label": "Техпроцессы", "icon": "list-checks"})
    return {
        "online": online, "as_of": as_of_label(last_pull), "tabs": tabs, "default_tab": tabs[0]["id"],
        "tps": tp_rows, "work_orders": wo_rows, "my_ops": my_ops,
        "kit_options": kit_opts, "spec_options": spec_opts, "release_tps": release_tps, "staff_options": staff_opts,
        "can_tp_create": "technologist" in roles and bool(kit_opts),
        "is_technologist": "technologist" in roles,
        "can_release": online and bool(release_tps),
        "may_release": bool(release_tps),
        "counts": {"tp_draft": sum(1 for t in tp_rows if t["status"] == "draft"),
                   "wo_open": sum(1 for w in wo_rows if w["status"] in prod.WO_OPEN),
                   "my_active": sum(1 for o in my_ops if o["state"] in ("running", "paused"))},
        "empty_tps": "ТП ещё нет. Технолог создаёт черновик по комплекту проекта в работе."
                     if "technologist" in roles else "Технологических процессов пока нет.",
        "empty_orders": "Нарядов пока нет. РП проекта выпускает наряд по утверждённому ТП.",
        "empty_my": "Вам пока не назначено операций. Назначение приходит уведомлением.",
    }


NORM_STATUS_LABELS_RU = {"proposed": "предложена", "approved": "утверждена", "rejected": "отклонена"}
NORM_TONES = {"proposed": "warning", "approved": "success", "rejected": "danger"}


def _fmt_dec(v: Any) -> str:
    """Десятичное число из JSON («60.5000») → «60,5» (точка → запятая, хвостовые нули убраны)."""
    s = str(v or "").strip()
    if not s:
        return s
    s = s.replace(".", ",")
    return s.rstrip("0").rstrip(",") if "," in s else s


def _norm_row(p: dict, me_id: str, online: bool, can_decide: bool) -> dict:
    """Предложение нормы для списка: подписи, диапазон, статус, кто предложил/решил, флаги действий."""
    st = p.get("status") or "proposed"
    rng = (f"факт: {_fmt_dec(p.get('min_min'))}–{_fmt_dec(p.get('max_min'))} мин, "
           f"медиана {_fmt_dec(p.get('median_min'))} · выборка {p.get('sample_size') or 0}")
    decide_wait = can_decide and st == "proposed" and p.get("created_by") != me_id
    return {
        "id": p["id"], "op_name": p.get("op_name") or "", "work_center": p.get("work_center") or "",
        "title": (p.get("op_name") or "") + (f" ({p['work_center']})" if p.get("work_center") else ""),
        "range_label": rng, "proposed_label": f"{_fmt_dec(p.get('proposed_min'))} мин",
        "comment": p.get("comment") or "", "status": st,
        "status_label": NORM_STATUS_LABELS_RU.get(st, st), "status_tone": NORM_TONES.get(st, "neutral"),
        "author_name": p.get("created_by_name") or "", "created_label": when(p.get("created_at")),
        "decision_label": ((f"{NORM_STATUS_LABELS_RU.get(st, st)} · {p.get('decided_by_name') or ''}, "
                            f"{when(p.get('decided_at'))}") if p.get("decided_at") else ""),
        "decision_reason": p.get("decision_reason") or "",
        # Решает не автор, при связи; предложить может технолог (не обязательно тот, кто решает).
        "can_decide": online and decide_wait,
    }


def norm_proposals_view(proposals: list[dict], me: dict, online: bool) -> dict[str, Any]:
    """Список предложений норм для блока в карточке наряда: сначала ждущие решения, внутри — новые сверху."""
    roles = set(me.get("roles") or [])
    me_id = me.get("user_id") or ""
    rows = [_norm_row(p, me_id, online, bool(roles & {"lead_engineer", "dept_head"})) for p in proposals or []]
    # Сначала ждущие решения, внутри группы — новые сверху (стабильная сортировка от обратной дате).
    raw = {str(p.get("id")): str(p.get("created_at") or "") for p in proposals or []}
    rows.sort(key=lambda r: raw.get(r["id"], ""), reverse=True)
    rows.sort(key=lambda r: {"proposed": 0, "approved": 1, "rejected": 2}.get(r["status"], 9))
    return {"rows": rows, "can_propose": online and bool(roles & {"lead_engineer", "rp", "dept_head"})}


def _int_text(v: Any) -> Any:
    """№ операции из формы: «10» / 10 / 10.0 → 10; прочее — как есть (сервер и ядро объяснят, что не так)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    t = str(v if v is not None else "").strip()
    return int(t) if t.isdigit() and len(t) <= 9 else (t or None)


def clean_operations(rows: list[dict]) -> list[dict]:
    """Строки операций из формы ТП → операции для команды: пустые заготовки пропускаются, пустые поля → None,
    норма — без пробелов, запятая → точка (без float)."""
    out = []
    for o in rows or []:
        if not any(str(o.get(k) if o.get(k) is not None else "").strip() for k in ("op_no", "name", "work_center", "norm_min", "note")):
            continue
        norm = str(o.get("norm_min") if o.get("norm_min") is not None else "").strip().replace(",", ".").replace(" ", "").replace("\u00a0", "")
        out.append({"op_no": _int_text(o.get("op_no")), "name": str(o.get("name") or "").strip(),
                    "work_center": str(o.get("work_center") or "").strip() or None, "norm_min": norm or None,
                    "note": str(o.get("note") or "").strip() or None})
    return out


def operations_problems(rows: list[dict]) -> dict[str, Any]:
    """Проверка операций в форме ТП теми же правилами ядра, что на сервере: {problems: [текст], by_line: {№ строки
    формы (с 0): текст}, norm_label: норма на изделие}. Пустой problems — замечаний нет."""
    ops = clean_operations(rows)
    # Номер строки ядра (с 1, только непустые) → номер строки формы (с 0, включая пустые заготовки).
    form_idx = [i for i, o in enumerate(rows or []) if any(str(o.get(k) if o.get(k) is not None else "").strip()
                                                           for k in ("op_no", "name", "work_center", "norm_min", "note"))]
    try:
        good = prod.parse_operations(ops)
    except prod.ProductionError as e:
        probs = (e.details or {}).get("problems") or [{"message_ru": e.message_ru}]
        # В форме запятая разрешена (clean_operations меняет её на точку) — текст ядра «без запятой» здесь сбивал бы.
        probs = [dict(p, message_ru=f"Операция {p['line']}: «Норма» — число минут, например 90 или 1,5.")
                 if p.get("field") == "norm_min" and p.get("code") == "invalid_format" and isinstance(p.get("line"), int)
                 else p for p in probs]
        by_line = {}
        for p in probs:
            n = p.get("line")
            if isinstance(n, int) and 1 <= n <= len(form_idx):
                by_line.setdefault(str(form_idx[n - 1]), p["message_ru"])
        return {"problems": [p["message_ru"] for p in probs], "by_line": by_line, "norm_label": "—"}
    total = sum((Decimal(o["norm_min"]) for o in good), Decimal(0))
    return {"problems": [], "by_line": {}, "norm_label": minutes_label(total) + " на изделие"}


def clock_label(seconds: int) -> str:
    """Секунды → «1:05:09» (часы:минуты:секунды) — табло таймера операции."""
    s = max(0, int(seconds or 0))
    h, rest = divmod(s, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}"


def op_timer_label(fact_seconds: int, running_since: str, state: str, now: Optional[datetime] = None) -> str:
    """Табло таймера операции «на сейчас» (обновляется раз в секунду, пока операция в работе)."""
    return clock_label(live_seconds({"fact_seconds": fact_seconds, "running_since": running_since or None, "state": state},
                                    now or datetime.now(timezone.utc)))
