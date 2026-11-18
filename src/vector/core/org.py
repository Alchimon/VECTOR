"""Оргструктура (этап 7, R-21; decision log D-44 — ответ владельца на A-48 п. 1): подразделения — филиалы, структуры
(дирекции, управления, службы) и отделы, связи «входит в» между ними, дерево подчинения, руководители и сотрудники.

Чистые функции без БД — общие для сервера и клиента (одни и те же правила и подписи).

Модель:

* подразделение (unit) — {id, kind, name, code, parent_id, head_user_id, active}; kind — branch (филиал), structure
  (структура: дирекция, управление, служба) или department (отдел). Приложение универсальное (A-48): вид подразделения
  не ограничивает, во что оно входит, — нужно только, чтобы связи не образовали цикл;
* parent_id — «входит в» (одна связь вверх): из таких связей и складывается дерево; корни — подразделения без
  родителя (компания может иметь несколько филиалов-корней);
* руководитель (head_user_id) — один на подразделение (может быть не задан); сотрудник состоит в одном подразделении
  (основное место работы) с должностью — должность ≠ роль доступа ≠ подразделение (R-21): должность и подразделение
  прав не дают, права — только роли и назначения;
* маршрут проверки (review_route): дневной отчёт сотрудника проверяет руководитель его подразделения; если его нет,
  он неактивен или это сам сотрудник — руководитель ближайшего вышестоящего подразделения. Так отчёт руководителя
  отдела уходит руководителю структуры («четыре глаза»), и отсутствие руководителя работу не останавливает (A-37).
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from .lifecycle import LifecycleError

KIND_LABELS_RU = {"branch": "Филиал", "structure": "Структура", "department": "Отдел"}
# Во множественном числе — для подписей групп и счётчиков («Филиалы: 2»).
KIND_PLURAL_RU = {"branch": "Филиалы", "structure": "Структуры", "department": "Отделы"}
KIND_ICONS = {"branch": "building-2", "structure": "network", "department": "users"}
MAX_NAME = 200
MAX_CODE = 32
MAX_POSITION = 120
MAX_DEPTH = 12              # глубина дерева — защита от ошибочной цепочки (и от циклов в повреждённых данных)
_CODE_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё._/-]{1,32}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class OrgError(LifecycleError):
    """Нарушение правил оргструктуры (код, сообщение по-русски, подробности)."""


def _text(v: Any, field: str, what: str, max_len: int, required: bool = True) -> Optional[str]:
    """Строка без пробелов по краям; обязательная — не пустая; не длиннее max_len."""
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise OrgError("required", f"Укажите {what}.", {"field": field})
        return None
    if not isinstance(v, str):
        raise OrgError("invalid_type", f"{what[:1].upper() + what[1:]} — строка.", {"field": field})
    v = v.strip()
    if len(v) > max_len:
        raise OrgError("too_long", f"{what[:1].upper() + what[1:]}: не длиннее {max_len} знаков.", {"field": field})
    return v


def parse_kind(v: Any) -> str:
    """Вид подразделения: branch / structure / department."""
    if v not in KIND_LABELS_RU:
        raise OrgError("invalid_kind", "Вид подразделения — филиал, структура или отдел.", {"field": "kind"})
    return v


def parse_name(v: Any) -> str:
    """Название подразделения (обязательно)."""
    return _text(v, "name", "название подразделения", MAX_NAME)


def parse_code(v: Any) -> Optional[str]:
    """Код подразделения (необязательно): буквы, цифры, точка, дефис, косая черта, подчёркивание — до 32 знаков."""
    c = _text(v, "code", "код подразделения", MAX_CODE, required=False)
    if c is not None and not _CODE_RE.match(c):
        raise OrgError("invalid_code", "Код: буквы, цифры, точка, дефис, косая черта, без пробелов, до 32 знаков.",
                       {"field": "code"})
    return c


def parse_position(v: Any) -> Optional[str]:
    """Должность сотрудника (необязательно; прав не даёт)."""
    return _text(v, "position", "должность", MAX_POSITION, required=False)


def opt_id(v: Any, field: str) -> Optional[str]:
    """Необязательный id (UUID в нижнем регистре) или None."""
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not _UUID_RE.match(v):
        raise OrgError("invalid_id", "Неверный идентификатор.", {"field": field})
    return v


def ancestors(units: dict[str, dict], unit_id: Optional[str]) -> list[str]:
    """Цепочка вверх от подразделения (само подразделение первым) до корня. Повреждённые данные с циклом обрываются
    на MAX_DEPTH, без зацикливания."""
    out: list[str] = []
    cur = unit_id
    while cur and cur in units and cur not in out and len(out) < MAX_DEPTH + 1:
        out.append(cur)
        cur = units[cur].get("parent_id")
    return out


def check_parent(units: dict[str, dict], unit_id: str, parent_id: Optional[str]) -> None:
    """Можно ли подразделению unit_id войти в parent_id: родитель существует и действует, связь не замыкает цикл
    (родитель — не само подразделение и не его подчинённое), глубина дерева не больше MAX_DEPTH."""
    if parent_id is None:
        return
    p = units.get(parent_id)
    if p is None:
        raise OrgError("parent_not_found", "Вышестоящее подразделение не найдено.", {"field": "parent_id"})
    if not p.get("active", True):
        raise OrgError("parent_archived", f"«{p['name']}» в архиве — выберите действующее подразделение.",
                       {"field": "parent_id"})
    chain = ancestors(units, parent_id)
    if unit_id in chain:
        raise OrgError("cycle", "Так не получится: подразделение оказалось бы внутри самого себя (связи образуют круг). "
                                "Выберите подразделение, которое не входит в это.", {"field": "parent_id"})
    depth_below = _depth_below(units, unit_id)
    if len(chain) + 1 + depth_below > MAX_DEPTH:
        raise OrgError("too_deep", f"Дерево глубже {MAX_DEPTH} уровней — проверьте связи.", {"field": "parent_id"})


def _depth_below(units: dict[str, dict], unit_id: str) -> int:
    """Сколько уровней подчинённых под подразделением (0 — нет подчинённых)."""
    kids = children_map(units)
    best, stack = 0, [(unit_id, 0)]
    seen = set()
    while stack:
        u, d = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        best = max(best, d)
        stack += [(k, d + 1) for k in kids.get(u, [])]
    return best


def children_map(units: dict[str, dict]) -> dict[Optional[str], list[str]]:
    """parent_id → дочерние подразделения (по названию). Ключ None — корни."""
    out: dict[Optional[str], list[str]] = {}
    for u in sorted(units.values(), key=lambda x: (x.get("name") or "").lower()):
        out.setdefault(u.get("parent_id"), []).append(u["id"])
    return out


def subtree(units: dict[str, dict], unit_id: str) -> list[str]:
    """Подразделение и все подчинённые ему (на любую глубину) — для сводок «по отделу и ниже» (D-46)."""
    kids = children_map(units)
    out, stack = [], [unit_id]
    while stack:
        u = stack.pop()
        if u in out:
            continue
        out.append(u)
        stack += list(reversed(kids.get(u, [])))
    return out


def flatten(units: dict[str, dict], include_archived: bool = True) -> list[dict]:
    """Дерево в виде списка для отображения: {id, depth, has_children} по порядку обхода (корни и дети — по
    названию). Архивные — по флагу."""
    shown = {k: v for k, v in units.items() if include_archived or v.get("active", True)}
    kids = children_map(shown)
    out: list[dict] = []

    def walk(pid: Optional[str], depth: int) -> None:
        for uid in kids.get(pid, []):
            if any(x["id"] == uid for x in out) or depth > MAX_DEPTH:
                continue
            out.append({"id": uid, "depth": depth, "has_children": bool(kids.get(uid))})
            walk(uid, depth + 1)
    walk(None, 0)
    # Подразделения, чей родитель скрыт (архив) или потерян, — тоже показать, корнями.
    for uid in kids:
        if uid is not None and uid not in shown:
            for orphan in kids[uid]:
                if not any(x["id"] == orphan for x in out):
                    out.append({"id": orphan, "depth": 0, "has_children": bool(kids.get(orphan))})
                    walk(orphan, 1)
    return out


def path_label(units: dict[str, dict], unit_id: Optional[str]) -> str:
    """«Филиал Юг › Инженерная дирекция › Отдел АСУ ТП» — путь от корня; нет подразделения — пусто."""
    return " › ".join(units[u]["name"] for u in reversed(ancestors(units, unit_id)))


def review_route(units: dict[str, dict], member_unit: Optional[str], author_id: str,
                 is_active_user=lambda uid: True) -> Optional[dict]:
    """Кто проверяет отчёт автора: руководитель его подразделения, а если его нет, он неактивен или это сам автор —
    руководитель ближайшего вышестоящего подразделения (A-48 → D-44).

    Возвращает {unit_id, head_user_id} или None — автор вне оргструктуры или выше никого (тогда действует общее
    правило: руководители отделов по роли, D-42).
    """
    for uid in ancestors(units, member_unit):
        u = units[uid]
        head = u.get("head_user_id")
        if u.get("active", True) and head and head != author_id and is_active_user(head):
            return {"unit_id": uid, "head_user_id": head}
    return None


def chain_heads(units: dict[str, dict], member_unit: Optional[str], author_id: str,
                is_active_user=lambda uid: True) -> list[str]:
    """Руководители по цепочке вверх от подразделения автора: действующие подразделения, активные учётные записи, без
    самого автора и без повторов. Первый — тот же, что в review_route."""
    out: list[str] = []
    for uid in ancestors(units, member_unit):
        u = units[uid]
        head = u.get("head_user_id")
        if u.get("active", True) and head and head != author_id and head not in out and is_active_user(head):
            out.append(head)
    return out


def headed_units(units: dict[str, dict], user_id: str) -> list[str]:
    """Подразделения, которыми руководит пользователь (действующие)."""
    return [u["id"] for u in units.values() if u.get("head_user_id") == user_id and u.get("active", True)]


def check_archive(units: dict[str, dict], unit_id: str, member_count: int) -> None:
    """В архив — только пустое подразделение: без действующих подчинённых и без сотрудников (их сначала переводят)."""
    kids = [k for k in children_map(units).get(unit_id, []) if units[k].get("active", True)]
    if kids:
        raise OrgError("has_children", "Сначала переведите или отправьте в архив подчинённые подразделения: " +
                       ", ".join(f"«{units[k]['name']}»" for k in kids[:5]) + ".", {"field": "id"})
    if member_count:
        raise OrgError("has_members", f"В подразделении сотрудников: {member_count}. Сначала переведите их в другое "
                                      "подразделение.", {"field": "id"})


def unit_label(u: Optional[dict]) -> str:
    """«Отдел АСУ ТП (ОАСУ)» — название и код."""
    if not u:
        return "—"
    return f"{u['name']} ({u['code']})" if u.get("code") else u["name"]


def kind_of(units: dict[str, dict], ids: Iterable[str]) -> dict[str, int]:
    """Сколько подразделений каждого вида среди ids (для подписей «филиалов 1, отделов 3»)."""
    out = {k: 0 for k in KIND_LABELS_RU}
    for i in ids:
        if i in units:
            out[units[i]["kind"]] += 1
    return out
