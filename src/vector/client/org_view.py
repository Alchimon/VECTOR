"""Представление раздела «Оргструктура» для интерфейса (D-44, ответ владельца на A-48 п. 1) — чистые функции без Qt.

Данные приходят снимком pull (org_units, org_members, org_events, org_me, people) и офлайн только читаются: править
структуру можно лишь при связи (команды org.* — online_required, право org.manage — администратор). Здесь — дерево в
виде списка с отступами, карточка подразделения (путь, руководитель, сотрудники, подчинённые), варианты для форм и
строка «кто проверяет мои отчёты». D-47 (ответ на A-51 п. 1): графики работы — справочник, какой график действует для
подразделения и каждого сотрудника (свой → подразделения по цепочке вверх → «по умолчанию»), варианты для форм.
"""
from __future__ import annotations

from typing import Any, Optional

from vector.core import org as og
from vector.core import schedule as sch
from vector.client.notification_view import when_label


def _name(people: dict[str, str], uid: Optional[str]) -> str:
    """Имя сотрудника из справочника (нет — «—»)."""
    return people.get(uid, "—") if uid else "—"


def _schedules_part(units: list[dict], members: list[dict], schedules: list[dict], links: list[dict],
                    people: dict[str, str]) -> dict[str, Any]:
    """Графики работы для раздела: список (подпись правила, «по умолчанию», в архиве), действующий график каждого
    подразделения и сотрудника (vector.core.schedule.resolve), варианты для форм назначения."""
    parent = {u["id"]: u.get("parent_id") for u in units}
    names = {u["id"]: u["name"] for u in units}
    unit_of = {m["user_id"]: m["unit_id"] for m in members}
    lk = {(x["target_kind"], x["target_id"]): x for x in links}
    by_id = {x["id"]: x for x in schedules}
    rows = [{**x, "label": sch.label(x), "full_label": sch.full_label(x),
             "assigned": sum(1 for v in links if v.get("schedule_id") == x["id"])}
            for x in sorted(schedules, key=lambda x: (not x.get("active", True), x["name"].lower()))]

    def own_label(kind: str, tid: str) -> str:
        """Что назначено именно этому подразделению / сотруднику («» — ничего, действует график выше)."""
        x = lk.get((kind, tid))
        if not x:
            return ""
        if x.get("exempt"):
            return "учёт рабочего времени не ведётся"
        return by_id.get(x.get("schedule_id"), {}).get("name", "")

    unit_sched = {}
    for u in units:
        r = sch.resolve("", u["id"], parent, links, schedules, names)
        unit_sched[u["id"]] = {"label": sch.full_label(r["schedule"]), "source": r["source_label"],
                               "own": own_label("unit", u["id"]),
                               "own_id": (lk.get(("unit", u["id"])) or {}).get("schedule_id") or ""}
    user_sched = {}
    for uid in people:
        r = sch.resolve(uid, unit_of.get(uid), parent, links, schedules, names)
        x = lk.get(("user", uid)) or {}
        user_sched[uid] = {"label": sch.full_label(r["schedule"]), "short": sch.label(r["schedule"]),
                           "source": r["source_label"], "exempt": r["exempt"],
                           "own_id": "__exempt__" if x.get("exempt") else (x.get("schedule_id") or "")}
    active = [x for x in rows if x.get("active", True)]
    opts = [{"id": x["id"], "label": x["full_label"]} for x in active]
    return {"schedules": rows, "unit_schedules": unit_sched, "user_schedules": user_sched,
            "schedule_unit_options": [{"id": "", "label": "— как у вышестоящего подразделения / по умолчанию —"}] + opts,
            "schedule_user_options": [{"id": "", "label": "— как у подразделения / по умолчанию —"}] + opts
                                     + [{"id": "__exempt__", "label": "— учёт рабочего времени не ведётся —"}],
            "default_schedule": next((x["full_label"] for x in active if x.get("is_default")),
                                     sch.full_label(dict(sch.BUILTIN)) + " (встроенный)")}


def org_view(units: list[dict], members: list[dict], events: list[dict], me: dict, people: dict[str, str],
             online: bool, now=None, query: str = "", schedules: Optional[list] = None,
             links: Optional[list] = None, schedule_manage: bool = False) -> dict[str, Any]:
    """Всё для экрана «Оргструктура».

    * rows — дерево по порядку обхода: id, глубина, вид (подпись и значок), название, код, руководитель, сколько
      сотрудников, путь, действует ли, есть ли подчинённые; поиск query — по названию, коду, руководителю и сотрудникам
      (с предками найденного, чтобы было видно, где оно в дереве);
    * cards — карточка каждого подразделения: путь, вышестоящее, руководитель, сотрудники (имя, должность),
      подчинённые подразделения;
    * unit_options / parent_options / people_options — варианты для форм;
    * mine — моё подразделение и кто проверяет мои отчёты; counts — сколько подразделений каждого вида;
    * unplaced — сотрудники вне оргструктуры (администратору: кого ещё включить);
    * events — последние изменения (подпись, кто, когда, что);
    * schedules / unit_schedules / user_schedules / schedule_*_options — графики работы (D-47).
    """
    sp = _schedules_part(units, members, schedules or [], links or [], people)
    byid = {u["id"]: u for u in units}
    by_unit: dict[str, list[dict]] = {}
    for m in members:
        by_unit.setdefault(m["unit_id"], []).append(m)
    kids = og.children_map(byid)
    q = (query or "").strip().lower()

    def hay(u: dict) -> str:
        """Текст подразделения для поиска."""
        names = " ".join(_name(people, m["user_id"]) for m in by_unit.get(u["id"], []))
        return " ".join([u["name"], u.get("code") or "", _name(people, u.get("head_user_id")), names]).lower()
    keep: Optional[set[str]] = None
    if q:
        keep = set()
        for u in units:
            if q in hay(u):
                keep.update(og.ancestors(byid, u["id"]))
    rows, cards = [], {}
    for x in og.flatten(byid):
        u = byid[x["id"]]
        if keep is not None and u["id"] not in keep:
            continue
        rows.append({"id": u["id"], "depth": x["depth"], "has_children": x["has_children"], "kind": u["kind"],
                     "kind_label": og.KIND_LABELS_RU[u["kind"]], "icon": og.KIND_ICONS[u["kind"]], "name": u["name"],
                     "code": u.get("code") or "", "head_name": _name(people, u.get("head_user_id")),
                     "members": len(by_unit.get(u["id"], [])), "active": bool(u.get("active", True)),
                     "subtitle": " · ".join(s for s in (og.KIND_LABELS_RU[u["kind"]], u.get("code") or "",
                                                       "руководитель: " + _name(people, u.get("head_user_id"))) if s)})
    for u in units:
        ms = sorted(by_unit.get(u["id"], []), key=lambda m: _name(people, m["user_id"]).lower())
        cards[u["id"]] = {
            **u, "kind_label": og.KIND_LABELS_RU[u["kind"]], "icon": og.KIND_ICONS[u["kind"]],
            "path": og.path_label(byid, u["id"]), "parent_name": byid[u["parent_id"]]["name"] if u.get("parent_id") in byid else "—",
            "head_name": _name(people, u.get("head_user_id")), "code": u.get("code") or "",
            "members_list": [{"user_id": m["user_id"], "name": _name(people, m["user_id"]),
                              "position": m.get("position") or "", "is_head": m["user_id"] == u.get("head_user_id"),
                              "schedule": sp["user_schedules"].get(m["user_id"], {}).get("short", ""),
                              "schedule_own_id": sp["user_schedules"].get(m["user_id"], {}).get("own_id", "")}
                             for m in ms],
            "schedule_label": sp["unit_schedules"][u["id"]]["label"],
            "schedule_source": sp["unit_schedules"][u["id"]]["source"],
            "schedule_own_id": sp["unit_schedules"][u["id"]]["own_id"],
            "children": [{"id": k, "name": byid[k]["name"], "kind_label": og.KIND_LABELS_RU[byid[k]["kind"]],
                          "active": bool(byid[k].get("active", True))} for k in kids.get(u["id"], [])],
            "can_archive": bool(u.get("active", True)) and not ms
                           and not any(byid[k].get("active", True) for k in kids.get(u["id"], [])),
        }
    active_rows = [x for x in og.flatten(byid, include_archived=False)]
    unit_options = [{"id": x["id"], "label": "    " * x["depth"] + byid[x["id"]]["name"]} for x in active_rows]
    placed = {m["user_id"] for m in members}
    people_options = sorted(({"id": k, "label": v} for k, v in people.items()), key=lambda p: p["label"].lower())
    mine_unit = me.get("unit_id")
    mine = {"unit_id": mine_unit, "unit_path": og.path_label(byid, mine_unit) if mine_unit else "",
            "reviewer_name": _name(people, me.get("reviewer_id")) if me.get("reviewer_id") else "",
            "text": (f"Вы: {og.path_label(byid, mine_unit)}. Дневные отчёты проверяет "
                     f"{_name(people, me.get('reviewer_id'))}." if mine_unit and me.get("reviewer_id") else
                     f"Вы: {og.path_label(byid, mine_unit)}. Руководителя по цепочке нет — отчёты видят все "
                     "руководители отделов." if mine_unit else
                     "Вы пока не включены в оргструктуру — дневные отчёты видят все руководители отделов.")}
    ev = []
    for e in events[:50]:
        d = e.get("details") or {}
        obj = byid.get(e["object_id"], {}).get("name") if e["object_type"] == "unit" else _name(people, e["object_id"])
        unit = byid.get(d.get("unit_id"), {}).get("name") if isinstance(d, dict) and d.get("unit_id") else ""
        ev.append({"id": e["id"], "label": e.get("event_label") or e["event"], "object": obj or "—",
                   "object_id": e["object_id"], "unit_id": (d.get("unit_id") if isinstance(d, dict) else "") or "",
                   "unit": unit or "", "who": _name(people, e.get("actor_id")),
                   "when": when_label(e.get("at"), now) if now else (e.get("at") or "")[:16].replace("T", " ")})
    return {"rows": rows, "cards": cards, "unit_options": unit_options,
            "parent_options": [{"id": "", "label": "— верхний уровень —"}] + unit_options,
            "head_options": [{"id": "", "label": "— не назначен —"}] + people_options,
            "people_options": people_options,
            "unplaced": [p for p in people_options if p["id"] not in placed],
            "kind_options": [{"id": k, "label": v} for k, v in og.KIND_LABELS_RU.items()],
            "counts": og.kind_of(byid, [u["id"] for u in units if u.get("active", True)]),
            "counts_label": ", ".join(f"{og.KIND_PLURAL_RU[k]}: {n}"
                                      for k, n in og.kind_of(byid, [u["id"] for u in units if u.get("active", True)]).items()),
            "mine": mine, "events": ev, "manage": bool(me.get("manage")), "online": online,
            "can_manage": bool(me.get("manage")) and online, "empty": not units,
            **sp, "schedule_manage": bool(schedule_manage), "can_schedule": bool(schedule_manage) and online}
