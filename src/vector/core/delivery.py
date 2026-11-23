"""Сквозные процессы проекта: потоки, риск-разрешения, лиды, архив (D-56, blueprint 52).

Чистые правила без БД и сети — ими пользуются и сервер (server/delivery.py), и клиент (подсказки, подписи).
Источник допустимых статусов и переходов — контракт contracts/workflows.slice1.json (workstream_<kind>,
sales_opportunity, risk_acceptance); этот модуль только читает его и добавляет то, что в JSON не выразить:
вычисление состояния «истёк» у риск-разрешения и канонический снимок закрытия проекта.

Решения владельца (2026-11-16), которые здесь закреплены:

* п. 1 — риск-разрешение в рамках проекта принимает РП (права — contracts/permissions, здесь не дублируются);
* п. 2 — «РКД согласована» означает ТОЛЬКО подтверждение заказчиком (статус approved_for_use потока РКД);
* п. 3 — запуск производства и заказ до согласования РКД блокируются шлюзом, исключение — только действующее
  (не истёкшее) риск-разрешение;
* п. 5 — лиды тендерного отдела и менеджеров — отдельная сущность (sales_opportunities), конвертируемая в тендер.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Optional

# Потоки проекта (порядок — порядок показа в карточке проекта). Допустимые статусы — в контракте workflows
# под ключом «workstream_<kind>».
STREAM_KINDS: tuple[str, ...] = ("contract", "rkd", "procurement", "production", "shipping", "site")

# Короткие названия потоков для интерфейса (родительный падеж — «поток закупки»).
STREAM_LABELS_RU: dict[str, str] = {
    "contract": "Договор",
    "rkd": "РКД",
    "procurement": "Закупка",
    "production": "Производство",
    "shipping": "Отгрузка",
    "site": "ПНР и ШМР на площадке",
}

# Откуда пришёл лид (source в sales_opportunities).
OPPORTUNITY_SOURCES: dict[str, str] = {
    "platform": "Тендерная площадка",
    "manager": "Личный канал менеджера",
    "inbound": "Входящее обращение",
    "other": "Другое",
}

# Виды риск-разрешений (подписи — в контракте workflows, раздел risk_acceptance.kinds).
RISK_KINDS: tuple[str, ...] = ("procurement_before_rkd_approval", "production_before_rkd_approval")


def stream_workflow(workflows: dict, kind: str) -> dict:
    """Контракт переходов потока kind; неизвестный вид — ошибка программы (KeyError), а не молчаливый пропуск."""
    return workflows["workstream_" + kind]


def stream_initial(workflows: dict, kind: str) -> str:
    """Начальный статус потока kind по контракту (потоки создаются при старте проекта)."""
    return stream_workflow(workflows, kind)["initial"]


def stream_gate(workflows: dict, kind: str, to: str) -> Optional[dict]:
    """Шлюз перехода потока kind в статус to: {"requires_rkd": ..., "risk_kind": ...} или None.

    Шлюз описан в контракте workflows (ключ gate): переход разрешён, если поток РКД в требуемом статусе,
    либо если есть действующее риск-разрешение указанного вида. Проверяет сервер в момент эффекта.
    """
    return stream_workflow(workflows, kind).get("gate", {}).get(to)


def risk_state(risk: dict, today: date) -> str:
    """Действующее состояние риск-разрешения на дату: active / expired / resolved / withdrawn.

    «Истёк» — вычисляемое состояние (статус в БД остаётся active): после даты due_on разрешение не открывает
    шлюзы и требует явного снятия (resolved/withdrawn) — история не переписывается автоматически.
    """
    st = risk.get("status")
    if st != "active":
        return st
    due = risk.get("due_on")
    if isinstance(due, str):
        due = date.fromisoformat(due)
    if due is not None and due < today:
        return "expired"
    return "active"


def canon(obj: Any) -> str:
    """Канонический JSON (ключи отсортированы, без пробелов) — для контрольных сумм снимков."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def closeout_content(project: dict, streams: list[dict], risks: list[dict], counts: dict[str, int],
                     labels: dict[str, str]) -> dict:
    """Содержимое снимка закрытия проекта (перед записью в project_closeouts).

    Снимок — только факты на момент архива: сам проект, итоговые статусы потоков, все риск-разрешения с их
    итогом и счётчики основных объектов. Никаких файлов и цен — снимок читается ролями проекта.
    """
    return {
        "project": {"id": project["id"], "code": project["code"], "title": project["title"]},
        "streams": [{"kind": s["kind"], "label_ru": labels.get(s["kind"], s["kind"]), "status": s["status"]}
                    for s in sorted(streams, key=lambda s: s["kind"])],
        "risks": [{"id": r["id"], "kind": r["kind"], "status": r["status"], "reason": r["reason"],
                   "due_on": str(r["due_on"]), "resolved_by": r["resolved_by"],
                   "resolution_reason": r["resolution_reason"]} for r in sorted(risks, key=lambda r: r["created_at"])],
        "counts": dict(sorted(counts.items())),
    }


def closeout_sha256(content: dict) -> str:
    """SHA-256 канонического содержимого снимка закрытия — контрольная сумма хранится рядом со снимком."""
    return hashlib.sha256(canon(content).encode("utf-8")).hexdigest()


# ------------------------------------------------------------ чек-листы ПНР/FAT (D-66/D-67, этап 6 blueprint 52)

CHECKLIST_KINDS = ("pnr", "fat")
CHECKLIST_KINDS_RU = {"pnr": "ПНР (пусконаладочные работы)", "fat": "FAT (приёмочные испытания)"}

# Градации замечаний по умолчанию, если шаблон не задаёт свои (blocking=true блокирует отгрузку до закрытия).
DEFAULT_SEVERITIES = [{"code": "minor", "label": "Несущественное", "blocking": False},
                      {"code": "major", "label": "Существенное", "blocking": False},
                      {"code": "critical", "label": "Блокирующее", "blocking": True}]


def parse_sections(raw: Any) -> list[dict]:
    """Разделы чек-листа из JSON/списка: [{code, title}] в заданном порядке."""
    rows = json.loads(raw) if isinstance(raw, str) else (raw or [])
    return [{"code": str(r["code"]), "title": str(r["title"])} for r in rows]


def parse_severities(raw: Any) -> list[dict]:
    """Градации замечаний из JSON/списка; пустой ввод — градации по умолчанию."""
    rows = json.loads(raw) if isinstance(raw, str) else (raw or [])
    if not rows:
        return [dict(x) for x in DEFAULT_SEVERITIES]
    return [{"code": str(r["code"]), "label": str(r["label"]), "blocking": bool(r.get("blocking"))}
            for r in rows]


def validate_sections(rows: Any) -> list[dict]:
    """Проверить разделы: непустой список {code, title}, коды уникальны (ошибка — ValueError с русским текстом)."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("Разделы чек-листа — непустой список {code, title}.")
    out, seen = [], set()
    for r in rows:
        code, title = str((r or {}).get("code") or "").strip(), str((r or {}).get("title") or "").strip()
        if not code or not title or len(code) > 40 or len(title) > 200:
            raise ValueError("У каждого раздела — код (до 40 символов) и название (до 200).")
        if code in seen:
            raise ValueError(f"Код раздела «{code}» повторяется.")
        seen.add(code)
        out.append({"code": code, "title": title})
    return out


def validate_severities(rows: Any) -> list[dict]:
    """Проверить градации замечаний: список {code, label, blocking}; пустой ввод — None (шаблон возьмёт умолчание)."""
    if rows is None:
        return None
    if not isinstance(rows, list) or not rows:
        raise ValueError("Градации замечаний — непустой список {code, label, blocking} или отсутствуют.")
    out, seen, has_blocking = [], set(), False
    for r in rows:
        code, label = str((r or {}).get("code") or "").strip(), str((r or {}).get("label") or "").strip()
        if not code or not label or len(code) > 40 or len(label) > 120:
            raise ValueError("У каждой градации — код (до 40 символов) и название (до 120).")
        if code in seen:
            raise ValueError(f"Код градации «{code}» повторяется.")
        seen.add(code)
        blocking = bool(r.get("blocking"))
        has_blocking = has_blocking or blocking
        out.append({"code": code, "label": label, "blocking": blocking})
    if not has_blocking:
        raise ValueError("Хотя бы одна градация должна быть блокирующей (blocking=true) — иначе финальный FAT "
                         "не сможет остановить отгрузку.")
    return out
