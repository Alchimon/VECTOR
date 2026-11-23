"""Технология и производство: правила, общие для сервера и клиента (этап 5, сценарий С5 шаги 1–6; D-36).

Сервер применяет эти правила при выполнении команды, клиент — чтобы показать результат офлайн ДО синхронизации
(таймер операции, состояние, факт). Одинаковый код на обеих сторонах — одна правда: если клиент показал
«операция завершена, факт 42 мин», сервер посчитает ровно так же (или вернёт видимый конфликт, но никогда не
«подправит» молча).

Что здесь:

* ТП (технологический процесс) — список операций с нормой времени на единицу изделия; разбор и проверка
  (`parse_operations`), SHA-256 содержимого (`tp_content_hash`);
* снимок наряда — неизменяемая копия состава (спецификации из зафиксированной версии расчёта комплекта, БЕЗ цен)
  и операций утверждённого ТП на момент выпуска (`build_snapshot`, `snapshot_hash`);
* замена в снимке — снимок не переписывается: действующий состав = снимок + записи замен по порядку
  (`effective_spec`, `spec_hash`);
* исполнение операции — конечный автомат «ожидает → в работе ⇄ пауза → выполнена», таймер по времени устройства
  (`apply_event`);
* проверка закрытия наряда по складу — выдано ли со склада всё, что требует действующий состав (`materials`).

Ошибки — ProductionError с машинным кодом и русским сообщением; conflict=True означает «состояние уже другое»
(операция завершена, наряд закрыт, событие пришло не по порядку) — сервер вернёт ВИДИМЫЙ конфликт, а не слияние.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

from . import money

# ---------------------------------------------------------------- статусы и подписи
TP_STATUS_LABELS_RU = {"draft": "Черновик", "approved": "Утверждён"}

# Наряд: draft зарезервирован жизненным циклом blueprint (03 §4); в D-36 наряд выпускается сразу из
# утверждённого ТП (выпуск = создание + снимок), поэтому черновика наряда на сервере нет.
WO_STATUS_LABELS_RU = {"draft": "Черновик", "released": "Выпущен", "in_progress": "В работе", "completed": "Выполнен",
                       "closed": "Закрыт", "cancelled": "Отменён"}
# Статусы, в которых по наряду ещё идёт работа (события операций, замены, выдача материалов).
WO_OPEN = ("released", "in_progress", "completed")

OP_STATE_LABELS_RU = {"pending": "Ожидает", "running": "В работе", "paused": "Пауза", "done": "Выполнена"}

EVENT_LABELS_RU = {"start": "Начать", "pause": "Пауза", "resume": "Продолжить", "finish": "Завершить",
                   "problem": "Проблема"}

# Разрешённые переходы операции: событие → {из состояния: в состояние}. «Проблема» состояние не меняет.
TRANSITIONS: dict[str, dict[str, str]] = {
    "start": {"pending": "running"},
    "pause": {"running": "paused"},
    "resume": {"paused": "running"},
    "finish": {"running": "done", "paused": "done"},
}
EVENT_KINDS = tuple(TRANSITIONS) + ("problem",)

MAX_OPS = 200            # операций в ТП
MAX_NORM_MIN = Decimal("100000")   # норма на единицу, минут (≈ 70 суток — заведомо больше любой реальной)
MAX_QTY = 100000         # изделий в наряде


class ProductionError(ValueError):
    """Нарушено правило технологии/производства.

    code — машинный код (как в ответе сервера); message_ru — для пользователя; details — подробности (например,
    список замечаний по операциям); conflict — True, если причина в ТЕКУЩЕМ СОСТОЯНИИ (сервер ответит конфликтом).
    """

    def __init__(self, code: str, message_ru: str, details: Optional[dict] = None, conflict: bool = False):
        """Сохраняет код, сообщение, подробности и признак конфликта состояния."""
        super().__init__(f"{code}: {message_ru}")
        self.code, self.message_ru, self.details, self.conflict = code, message_ru, details or {}, conflict


def canon_hash(obj: Any) -> str:
    """SHA-256 канонического JSON (ключи отсортированы, без пробелов) — тот же формат, что у хэша расчёта."""
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ---------------------------------------------------------------- ТП
def _text(raw: dict, key: str, n: int, max_len: int, required: bool, problems: list) -> Optional[str]:
    """Текстовое поле операции: обрезка пробелов, обязательность, длина. Ошибки — в problems (с номером строки)."""
    v = raw.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            problems.append({"line": n, "field": key, "code": "field_required", "message_ru": f"Операция {n}: заполните «{_FIELD_RU[key]}»."})
        return None
    if not isinstance(v, str):
        problems.append({"line": n, "field": key, "code": "invalid_type", "message_ru": f"Операция {n}: «{_FIELD_RU[key]}» — текст."})
        return None
    v = v.strip()
    if len(v) > max_len:
        problems.append({"line": n, "field": key, "code": "too_long",
                         "message_ru": f"Операция {n}: «{_FIELD_RU[key]}» длиннее {max_len} символов."})
        return None
    return v


_FIELD_RU = {"op_no": "№ операции", "name": "Наименование", "work_center": "Участок / оборудование",
             "norm_min": "Норма, мин на ед.", "note": "Примечание"}
OP_KEYS = set(_FIELD_RU)


def parse_operations(raw_ops: Any) -> list[dict]:
    """Проверить и нормализовать операции ТП. Возвращает список, отсортированный по № операции.

    Операция: op_no — целое 1…9999, уникальное; name — обязательно (≤ 200); work_center, note — необязательно;
    norm_min — норма времени на ЕДИНИЦУ изделия в минутах, десятичная строка ≥ 0 (до 4 знаков, без округления;
    «0» допустимо — например, контрольная операция без нормы). Все замечания собираются сразу (ProductionError
    с details.problems), чтобы технолог исправил всё за один раз.
    """
    if not isinstance(raw_ops, list):
        raise ProductionError("invalid_type", "Операции ТП — список.", {"field": "operations"})
    if len(raw_ops) > MAX_OPS:
        raise ProductionError("too_many_operations", f"Операций больше {MAX_OPS}.", {"field": "operations"})
    problems: list[dict] = []
    out: list[dict] = []
    seen: set[int] = set()
    for n, raw in enumerate(raw_ops, 1):
        if not isinstance(raw, dict):
            problems.append({"line": n, "field": None, "code": "invalid_type", "message_ru": f"Операция {n}: ожидается объект."})
            continue
        extra = sorted(set(raw) - OP_KEYS)
        if extra:
            problems.append({"line": n, "field": extra[0], "code": "unknown_fields",
                             "message_ru": f"Операция {n}: неизвестные поля {', '.join(extra)}."})
        no = raw.get("op_no")
        if isinstance(no, bool) or not isinstance(no, int) or not 1 <= no <= 9999:
            problems.append({"line": n, "field": "op_no", "code": "invalid_op_no",
                             "message_ru": f"Операция {n}: № операции — целое от 1 до 9999."})
            no = None
        elif no in seen:
            problems.append({"line": n, "field": "op_no", "code": "duplicate_op_no",
                             "message_ru": f"Операция {n}: № {no} уже есть в ТП."})
        else:
            seen.add(no)
        name = _text(raw, "name", n, 200, True, problems)
        wc = _text(raw, "work_center", n, 200, False, problems)
        note = _text(raw, "note", n, 1000, False, problems)
        norm: Optional[Decimal] = None
        if raw.get("norm_min") is None:
            # «Неизвестно» здесь недопустимо: без нормы нельзя посчитать норму наряда; «нет нормы» — это «0».
            problems.append({"line": n, "field": "norm_min", "code": "field_required",
                             "message_ru": f"Операция {n}: укажите норму (0 — если нормы нет)."})
        else:
            try:
                norm = money.parse_decimal(raw.get("norm_min"))
            except money.MoneyError as e:
                problems.append({"line": n, "field": "norm_min", "code": e.code,
                                 "message_ru": f"Операция {n}: «Норма»: {e.message_ru}"})
        if norm is not None and not Decimal(0) <= norm <= MAX_NORM_MIN:
            problems.append({"line": n, "field": "norm_min", "code": "norm_range",
                             "message_ru": f"Операция {n}: норма — от 0 до {MAX_NORM_MIN} мин."})
            norm = None
        if no is not None and name is not None and norm is not None:
            out.append({"op_no": no, "name": name, "work_center": wc, "norm_min": money.to_json(norm), "note": note})
    if problems:
        raise ProductionError("tp_invalid", "ТП не сохранён: исправьте операции.",
                              {"problems": problems[:100], "problem_count": len(problems)})
    return sorted(out, key=lambda o: o["op_no"])


def tp_content_hash(title: str, spec_version_id: Optional[int], operations: list[dict], note: Optional[str]) -> str:
    """SHA-256 содержимого ТП: название, спецификация (версия расчёта), операции, примечание.

    По нему согласование привязано к ТОМУ содержимому, которое видел утверждающий.
    """
    return canon_hash({"title": title, "spec_version_id": spec_version_id, "operations": operations, "note": note})


# ---------------------------------------------------------------- снимок наряда
SNAPSHOT_FORMAT = "vector.wo-snapshot/1"


def _qty_total(per_unit: Decimal, qty: int) -> str:
    """Количество на наряд = на единицу × число изделий (точно, без округления; 4 знака как в расчёте)."""
    return money.to_json(per_unit * qty)


def build_snapshot(*, qty: int, project: dict, kit: dict, tp: dict, spec: dict, operations: list[dict]) -> dict:
    """Снимок наряда на момент выпуска — то, по чему работают исполнители (и офлайн).

    * project/kit — {id, code, (title)}; tp — {id, revision_no, title, content_hash};
    * spec — {calc_version_id, version_no, content_hash, lines:[{line_id, code, name, unit, qty}]} — состав из
      ЗАФИКСИРОВАННОЙ версии расчёта комплекта; цены в снимок НЕ входят (исполнителям деньги не нужны);
    * operations — операции ТП; к норме на единицу добавляется норма на наряд (× qty).

    Снимок неизменяем: сервер хранит его с SHA-256 (snapshot_hash) в таблице с запретом UPDATE/DELETE.
    """
    if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= MAX_QTY:
        raise ProductionError("invalid_qty", f"Количество изделий в наряде — целое от 1 до {MAX_QTY}.", {"field": "qty"})
    lines = []
    for ln in spec["lines"]:
        per = money.parse_decimal(ln["qty"])
        lines.append({"line_id": ln["line_id"], "code": ln.get("code"), "name": ln["name"], "unit": ln["unit"],
                      "qty_per_unit": money.to_json(per), "qty": _qty_total(per, qty)})
    ops = [{"op_no": o["op_no"], "name": o["name"], "work_center": o.get("work_center"), "note": o.get("note"),
            "norm_min": o["norm_min"], "norm_total_min": _qty_total(money.parse_decimal(o["norm_min"]), qty)}
           for o in sorted(operations, key=lambda o: o["op_no"])]
    return {"format": SNAPSHOT_FORMAT, "qty": qty,
            "project": {"id": project["id"], "code": project["code"]},
            "kit": {"id": kit["id"], "code": kit["code"], "title": kit.get("title")},
            "tp": {"id": tp["id"], "revision_no": tp["revision_no"], "title": tp["title"], "content_hash": tp["content_hash"]},
            "spec": {"calc_version_id": spec["calc_version_id"], "version_no": spec["version_no"],
                     "content_hash": spec["content_hash"], "lines": lines},
            "operations": ops}


def snapshot_hash(snapshot: dict) -> str:
    """SHA-256 снимка наряда (канонический JSON)."""
    return canon_hash(snapshot)


# ---------------------------------------------------------------- замены и действующий состав
def effective_spec(snapshot_lines: list[dict], substitutions: Iterable[dict]) -> list[dict]:
    """Действующий состав наряда: строки снимка с применёнными по порядку заменами.

    Замена — {line_id, new:{code, name, unit, qty}} — подменяет строку с тем же line_id (у строки появляется
    substituted=True и номер замены). Снимок при этом не меняется: его можно показать рядом («было по снимку»).
    """
    out = [{"line_id": ln["line_id"], "code": ln.get("code"), "name": ln["name"], "unit": ln["unit"], "qty": ln["qty"],
            "substituted": False, "substitution_seq": None} for ln in snapshot_lines]
    idx = {ln["line_id"]: i for i, ln in enumerate(out)}
    for s in substitutions:
        i = idx.get(s["line_id"])
        if i is None:
            continue            # защитно: сервер не создаёт замен для чужих строк
        new = s["new"]
        out[i] = {"line_id": s["line_id"], "code": new.get("code"), "name": new["name"], "unit": new["unit"],
                  "qty": new["qty"], "substituted": True, "substitution_seq": s.get("seq")}
    return out


def spec_hash(lines: list[dict]) -> str:
    """SHA-256 действующего состава (без служебных признаков замены) — «хэш предпросмотра» для команды замены.

    Мастер видит предпросмотр с этим хэшем; если к моменту замены состав уже изменился (коллега сделал другую
    замену), хэш не совпадёт и сервер вернёт конфликт — замена «вслепую» невозможна.
    """
    return canon_hash([{k: ln.get(k) for k in ("line_id", "code", "name", "unit", "qty")} for ln in lines])


# ---------------------------------------------------------------- исполнение операции
def parse_device_time(v: Any) -> datetime:
    """Время устройства из события: ISO-8601 С часовым поясом → UTC. Без пояса — ошибка (чьё это «10:00»?)."""
    if not isinstance(v, str):
        raise ProductionError("invalid_datetime", "Время события: ожидается ISO-8601 с часовым поясом.", {"field": "device_time"})
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise ProductionError("invalid_datetime", "Время события: неверный формат.", {"field": "device_time"})
    if dt.tzinfo is None:
        raise ProductionError("invalid_datetime", "Время события: укажите часовой пояс.", {"field": "device_time"})
    return dt.astimezone(timezone.utc)


def _dt(v: Any) -> Optional[datetime]:
    """Поле времени операции (строка ISO из JSON или datetime из БД) → datetime UTC или None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return parse_device_time(v)


def apply_event(op: dict, kind: str, device_time: datetime, wo_qty: int, qty_done: Any = None) -> dict:
    """Применить событие исполнителя к операции и вернуть ИЗМЕНЕНИЯ полей операции.

    op — {state, running_since, last_device_time, fact_seconds, started_at, qty_done}; время — по часам устройства
    (офлайн другого нет): таймер = сумма интервалов «в работе». Правила:

    * переходы — TRANSITIONS; «проблема» состояние не меняет (только запись события);
    * выполненная операция не принимает событий — конфликт operation_done;
    * время события не раньше предыдущего события этой операции — конфликт device_time_order (часы устройства
      переведены назад или события двух устройств перемешались — решает человек, а не сервер);
    * finish: qty_done — целое 0…qty наряда (по умолчанию — весь наряд); фиксируется факт времени.
    """
    if kind not in EVENT_KINDS:
        raise ProductionError("invalid_event", f"Неизвестное событие «{kind}».", {"field": "kind"})
    state = op["state"]
    if state == "done":
        raise ProductionError("operation_done", "Операция уже завершена — событие не применено.",
                              {"state": state}, conflict=True)
    last = _dt(op.get("last_device_time"))
    if last is not None and device_time < last:
        raise ProductionError("device_time_order",
                              "Время события раньше предыдущего события этой операции (часы устройства или порядок "
                              "событий). Событие не применено — проверьте время и повторите.",
                              {"last_device_time": last.isoformat(), "device_time": device_time.isoformat()},
                              conflict=True)
    ch: dict[str, Any] = {"last_device_time": device_time}
    if kind == "problem":
        return ch
    to = TRANSITIONS[kind].get(state)
    if to is None:
        raise ProductionError("invalid_transition",
                              f"«{EVENT_LABELS_RU[kind]}» невозможно: операция в состоянии «{OP_STATE_LABELS_RU[state]}».",
                              {"state": state, "kind": kind}, conflict=True)
    fact = int(op.get("fact_seconds") or 0)
    since = _dt(op.get("running_since"))
    if state == "running" and since is not None:
        fact += max(0, int((device_time - since).total_seconds()))     # закрываем текущий интервал «в работе»
    ch.update(state=to, fact_seconds=fact, running_since=device_time if to == "running" else None)
    if kind == "start":
        ch["started_at"] = device_time
    if kind == "finish":
        if qty_done is None:
            q = wo_qty
        elif isinstance(qty_done, bool) or not isinstance(qty_done, int) or not 0 <= qty_done <= wo_qty:
            raise ProductionError("invalid_qty_done", f"Выполнено — целое от 0 до {wo_qty}.", {"field": "qty_done"})
        else:
            q = qty_done
        ch.update(qty_done=q, finished_at=device_time)
    return ch


def fact_minutes(fact_seconds: int) -> str:
    """Факт времени для показа: минуты с одним знаком («42.5»)."""
    return f"{Decimal(int(fact_seconds or 0)) / Decimal(60):.1f}"


# ---------------------------------------------------------------- склад: что выдано по наряду
def materials(lines: list[dict], items_by_code: dict[str, dict], issued_by_item: dict[str, Decimal]) -> list[dict]:
    """Состояние материалов наряда по строкам действующего состава.

    items_by_code — номенклатура склада по коду {code: {id, unit}}; issued_by_item — нетто выдано по наряду
    (выдачи минус их сторно) по id номенклатуры. Состояние строки:

    * ok — выдано не меньше требуемого;
    * short — выдано меньше (закрыть наряд нельзя);
    * unit_mismatch — единица в составе не совпадает с единицей склада (закрыть нельзя: сначала замена в снимке
      с правильной единицей — пересчёт «на глаз» недопустим);
    * not_stock — позиции нет в номенклатуре склада (работа, покупное «под заказ»): закрытию не мешает, но
      показывается, чтобы это было видно.
    """
    out = []
    for ln in lines:
        it = items_by_code.get(ln.get("code") or "")
        req = money.parse_decimal(ln["qty"])
        row = {"line_id": ln["line_id"], "code": ln.get("code"), "name": ln["name"], "unit": ln["unit"],
               "required": money.to_json(req), "issued": None, "stock_item_id": None, "state": "not_stock"}
        if it is not None:
            got = issued_by_item.get(it["id"], Decimal(0))
            row.update(stock_item_id=it["id"], issued=money.to_json(got))
            if (it["unit"] or "").strip().lower() != (ln["unit"] or "").strip().lower():
                row.update(state="unit_mismatch", stock_unit=it["unit"])
            else:
                row["state"] = "ok" if got >= req else "short"
        out.append(row)
    return out


MATERIAL_STATE_LABELS_RU = {"ok": "Выдано", "short": "Не выдано полностью", "unit_mismatch": "Единица не совпадает со складом",
                            "not_stock": "Не складская позиция"}
BLOCKING_MATERIAL_STATES = ("short", "unit_mismatch")


# ---------------------------------------------------------------- готовность сборки (D-61, этап 5 blueprint 52)
# Снимок готовности при выпуске наряда: основания (ревизии), обеспеченность материалами, риск-разрешения.
READINESS_VERDICT_LABELS_RU = {"ready": "Готово к сборке", "ready_with_deviations": "Готово с отклонениями"}

# Обеспеченность строки состава на момент выпуска: covered — «у проекта» не меньше требуемого; short — меньше;
# unit_mismatch — единица в составе не совпадает со складской; not_stock — позиции нет в номенклатуре (работа,
# покупное «под заказ»): обеспечивать со склада нечего, но строка показывается, чтобы это было видно.
COVERAGE_STATE_LABELS_RU = {"covered": "Обеспечено", "short": "Не обеспечено",
                            "unit_mismatch": "Единица не совпадает со складом", "not_stock": "Не складская позиция"}
# Состояния, при которых материал считается обеспеченным для вердикта «Готово к сборке».
COVERED_STATES = ("covered", "not_stock")


def coverage_materials(lines: list[dict], items_by_code: dict[str, dict], secured_by_item: dict[str, Decimal]) -> list[dict]:
    """Обеспеченность материалами на момент выпуска наряда (D-61) по строкам снимка состава (уже × кол-во наряда).

    items_by_code — номенклатура склада по коду {code: {id, unit}}; secured_by_item — нетто «у проекта» по id
    позиции: выдано проекту (минус сторно) плюс неизрасходованный остаток действующих резервов проекта. Свободный
    остаток склада НЕ считается обеспечением — он ещё никому не принадлежит. Нехватка выпуск не блокирует
    (материалы могут подойти позже; факт контролируют выдача со склада и закрытие наряда), но честно записывается
    в снимок готовности и видна в наряде.
    """
    out = []
    for ln in lines:
        it = items_by_code.get(ln.get("code") or "")
        req = money.parse_decimal(ln["qty"])
        row = {"line_id": ln["line_id"], "code": ln.get("code"), "name": ln["name"], "unit": ln["unit"],
               "required": money.to_json(req), "secured": None, "state": "not_stock"}
        if it is not None:
            got = secured_by_item.get(it["id"], Decimal(0))
            row["secured"] = money.to_json(got)
            if (it["unit"] or "").strip().lower() != (ln["unit"] or "").strip().lower():
                row.update(state="unit_mismatch", stock_unit=it["unit"])
            else:
                row["state"] = "covered" if got >= req else "short"
        out.append(row)
    return out


def readiness_content(*, work_order: dict, tp: dict, spec: dict, needs: list[dict], rkd: dict,
                      risks: list[dict], materials: list[dict]) -> dict:
    """Содержимое снимка готовности сборки (перед записью в assembly_readiness_snapshots, D-61).

    * work_order — {id, doc_no, qty}; tp — {id, revision_no, content_hash}; spec — {calc_version_id, version_no,
      content_hash}: точные основания выпуска;
    * needs — потребности по версии расчёта [{id, status, line_count, matched_count}];
    * rkd — {status, label_ru, ok}: статус потока РКД проекта на момент выпуска и прошёл ли шлюз без исключений;
    * risks — действующие риск-разрешения, под которыми выпущен наряд [{id, kind, label_ru, reason, due_on,
      kit_ids}]; пусто, если РКД согласована;
    * materials — обеспеченность (coverage_materials).

    Вердикт «Готово к сборке» — только когда РКД согласована заказчиком, риск-разрешений не потребовалось и все
    складские позиции обеспечены; иначе «Готово с отклонениями» (выпуск по риск-разрешению и/или нехватка).
    """
    rkd_ok = bool(rkd["ok"]) and not risks
    materials_covered = all(m["state"] in COVERED_STATES for m in materials)
    verdict = "ready" if (rkd_ok and materials_covered) else "ready_with_deviations"
    return {"format": 1, "verdict": verdict,
            "work_order": {"id": work_order["id"], "doc_no": work_order["doc_no"], "qty": work_order["qty"]},
            "tp": {"id": tp["id"], "revision_no": tp["revision_no"], "content_hash": tp["content_hash"]},
            "spec": {"calc_version_id": spec["calc_version_id"], "version_no": spec["version_no"],
                     "content_hash": spec["content_hash"]},
            "needs": sorted(needs, key=lambda n: n["id"]),
            "rkd": {"status": rkd["status"], "label_ru": rkd["label_ru"], "ok": bool(rkd["ok"])},
            "risks": sorted(risks, key=lambda r: r["id"]),
            "materials": materials, "materials_covered": materials_covered}


def readiness_hash(content: dict) -> str:
    """SHA-256 канонического содержимого снимка готовности — хранится рядом и проверяется при показе."""
    return canon_hash(content)


# ------------------------------------------------------------ нормы трудоёмкости (этап 7 blueprint 52, D-72)
def norm_stats(ops: list[dict]) -> list[dict]:
    """Агрегированный диапазон фактической трудоёмкости по операциям (наименование + рабочий центр).

    На вход — завершённые операции нарядов (name, work_center, fact_seconds, qty_done). На выход — по каждой
    группе: размер выборки, мин/медиана/макс фактических минут НА ОПЕРАЦИЮ (не на единицу — объёмы различаются,
    это диапазон-ориентир, а не расчётная норма). Норма предлагается человеком и утверждается вручную —
    программа не выставляет норму и не строит рейтинг исполнителей (персональных данных здесь нет: агрегат).
    """
    groups: dict[tuple[str, str], list[float]] = {}
    for o in ops:
        sec = o.get("fact_seconds") or 0
        if not sec or not o.get("finished_at"):
            continue
        key = (o.get("name") or "", o.get("work_center") or "")
        groups.setdefault(key, []).append(sec / 60.0)
    out = []
    for (name, wc), vals in sorted(groups.items()):
        vals.sort()
        n = len(vals)
        med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
        out.append({"op_name": name, "work_center": wc, "sample_size": n,
                    "min_min": round(vals[0], 1), "median_min": round(med, 1), "max_min": round(vals[-1], 1)})
    return out
