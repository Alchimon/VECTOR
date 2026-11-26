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
                     labels: dict[str, str], facts: Optional[dict] = None) -> dict:
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
        # D-71: агрегированные факты на момент архива (сроки потоков, ПНР/FAT, замечания, документы доставки).
        **({"facts": facts} if facts else {}),
    }


def stream_facts(events: list[dict], wf: dict) -> dict:
    """Фактические даты потоков из журнала событий проекта (этап 7 blueprint 52, D-71).

    Для каждого потока: actual_start — первый выход из начального статуса, actual_finish — вход в конечный
    статус (даты ISO из событий workstream_status; в details события — kind/from/to). События сортируются по
    seq (порядок в проекте), при равных метках времени важен именно seq.
    """
    out: dict[str, dict] = {}
    for e in sorted(events, key=lambda x: x.get("seq") or 0):
        if e.get("kind") != "workstream_status":
            continue
        det = e.get("details") or {}
        kind, to = det.get("kind"), det.get("to")
        if not kind or not to:
            continue
        contract = wf.get("workstream_" + kind, {})
        f = out.setdefault(kind, {})
        at = (e.get("at") or "")[:10]
        if not at:
            continue
        if "start" not in f and to != contract.get("initial"):
            f["start"] = at                       # первый уход из начального статуса
        if to in set(contract.get("terminal", [])):
            f["finish"] = at                      # вход в конечный статус (последний, если терминалов много)
    return out


def delivery_facts(pnr: list[dict], fats: list[dict], findings: list[dict], docs: list[dict]) -> dict:
    """Сводные факты этапа 6 для снимка закрытия и аналитики: ПНР, испытания, замечания, документы доставки."""
    fat_by_id = {f["id"]: f for f in fats}
    my_findings = [f for f in findings if f.get("fat_id") in fat_by_id]
    return {"pnr": {"total": len(pnr),
                    "done": sum(1 for r in pnr if r.get("status") == "done"),
                    "cancelled": sum(1 for r in pnr if r.get("status") == "cancelled"),
                    "site": sum(1 for r in pnr if r.get("kind") == "site")},
            "fat": {"total": len(fats),
                    "preliminary": sum(1 for f in fats if f.get("kind") == "preliminary"),
                    "final": sum(1 for f in fats if f.get("kind") == "final"),
                    "passed": sum(1 for f in fats if f.get("status") == "passed"),
                    "failed": sum(1 for f in fats if f.get("status") == "failed")},
            "findings": {"total": len(my_findings),
                         "blocking": sum(1 for f in my_findings if f.get("blocking")),
                         "closed": sum(1 for f in my_findings if f.get("status") == "closed")},
            "delivery_docs": len(docs)}


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


PNR_KINDS = ("internal", "site")
PNR_KINDS_RU = {"internal": "Внутренняя наладка", "site": "ПНР на площадке"}

FAT_KINDS = ("preliminary", "final")


def fat_shipping_gate(conn_rows: list[dict], findings_rows: list[dict]) -> Optional[str]:
    """Шлюз отгрузки (ответ владельца п. 3): None — отгрузка разрешена; иначе текст причины блокировки.

    conn_rows — финальные FAT-сессии области (новые первыми); findings_rows — их замечания. Правило: последняя
    финальная сессия должна быть успешной и без открытых блокирующих замечаний; предварительные сессии на шлюз
    не влияют (ответ п. 4). Сессия «идёт испытание» — отгрузка заблокирована (испытание не завершено).
    """
    if not conn_rows:
        return "Нет финального (приёмочного) FAT — сначала проведите и завершите приёмочные испытания."
    last = conn_rows[0]
    if last["status"] == "in_progress":
        return "Финальный FAT ещё идёт — завершите испытание с итогом."
    open_blocking = [f for f in findings_rows if f["fat_id"] == last["id"] and f["status"] == "open" and f["blocking"]]
    if open_blocking:
        return (f"Открыты блокирующие замечания финального FAT: {len(open_blocking)} шт. — закройте их "
                f"(с причиной) или проведите новое испытание.")
    if last["status"] != "passed":
        return "Последний финальный FAT неуспешен — устраните замечания и проведите новое приёмочное испытание."
    return None


# ------------------------------------------------------------ документы доставки (D-69)
import re as _re

# Поля автозаполнения из карточки проекта/комплекта (плейсхолдеры {{field}} в шаблоне). Список открытый:
# неизвестный плейсхолдер остаётся как есть и виден — так безопаснее, чем молча подставить пустоту.
PLACEHOLDER_RE = _re.compile(r"\{\{\s*([a-z0-9_.]+)\s*\}\}")


def render_template(body: str, values: dict) -> str:
    """Подставить значения в шаблон с плейсхолдерами {{field}}. Неизвестные поля остаются в тексте как есть."""
    def sub(m):
        return str(values.get(m.group(1), m.group(0)))
    return PLACEHOLDER_RE.sub(sub, body)


def template_fields(body: str) -> list[str]:
    """Какие плейсхолдеры использует шаблон (для подсказки в справочнике)."""
    return sorted(set(PLACEHOLDER_RE.findall(body or "")))
