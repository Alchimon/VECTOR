"""Консолидация закупок и аналоги (этап 4 blueprint 52): чистые правила без БД и сети.

Источник переходов пакета — контракт workflows (procurement_batch); этот модуль добавляет то, что в JSON
не выразить: нормализацию распределений (группировку по позиции и единице) и арифметику покрытия строки
потребности.

Инварианты (blueprint 52, 2.3):

* консолидация только по ТОЧНОМУ коду позиции и единице — похожее название эквивалентностью не является;
* одна строка потребности не входит в пакет дважды; покрытие сверх потребности запрещено (проверяет сервер
  в транзакции с учётом других пакетов и черновиков заказов поставщикам);
* отменённый пакет освобождает покрытие; аналог — только предложение, решение принимает ГИП/ведущий инженер,
  молчаливой замены нет (замена — новой ревизией потребности из новой редакции расчёта).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

# Статусы пакета, в которых его распределения ЗАНИМАЮТ покрытие строки потребности
# (отменённый и закрытый — не занимают: отменённый освобождает, закрытый уже исполнен заказами).
COVERING_BATCH_STATUSES: tuple[str, ...] = ("open", "consolidated")

# Статусы заказа поставщику, чьи строки занимают покрытие (черновик — намерение закупки, размещённый — факт).
COVERING_ORDER_STATUSES: tuple[str, ...] = ("draft", "placed")


def batch_workflow(workflows: dict) -> dict:
    """Контракт переходов закупочного пакета (workflows.procurement_batch)."""
    return workflows["procurement_batch"]


def can_transition(workflows: dict, from_status: str, to: str) -> bool:
    return to in batch_workflow(workflows)["transitions"].get(from_status, ())


def needs_reason(workflows: dict, from_status: str, to: str) -> bool:
    """Причина обязательна при отмене и при возврате сформированного пакета в работу."""
    return to in batch_workflow(workflows).get("reason_required_for", ()) \
        or (from_status, to) == ("consolidated", "open")


def parse_allocations(items: Any) -> Optional[list[tuple[str, str, Decimal]]]:
    """Список распределений из payload → [(need_id, need_line_id, qty)]; None, если формат неверен.

    Отказ при: не список/пусто; не-uuid; количество не число или не положительное; дубликат строки потребности.
    """
    if not isinstance(items, list) or not items or len(items) > 500:
        return None
    out: list[tuple[str, str, Decimal]] = []
    seen: set[tuple[str, str]] = set()
    for it in items:
        if not isinstance(it, dict):
            return None
        nid, lid, qty = it.get("need_id"), it.get("need_line_id"), it.get("qty")
        if not isinstance(nid, str) or not isinstance(lid, str) or len(nid) != 36 or len(lid) != 36:
            return None
        try:
            q = Decimal(str(qty))
        except Exception:
            return None
        if q <= 0 or (nid, lid) in seen:
            return None
        seen.add((nid, lid))
        out.append((nid, lid, q))
    return out


def remaining(need_qty: Decimal, allocated: Decimal, ordered: Decimal, reserved: Decimal = Decimal(0)) -> Decimal:
    """Сколько ещё можно распределить по строке потребности: потребность минус занятое пакетами, заказами
    и резервами склада (D-60: резерв склада — тоже покрытие, иначе строку заказали бы дважды)."""
    return need_qty - allocated - ordered - reserved


# ---------------------------------------------------------------- закупка из пакета (D-59)

def round_up_to_pack(qty: Decimal, pack_qty: Optional[Decimal]) -> Decimal:
    """Количество, округлённое вверх до кратности упаковки. Кратности нет (None) — возвращается само qty."""
    if pack_qty is None:
        return qty
    packs, rem = divmod(qty, pack_qty)
    return packs * pack_qty if rem == 0 else (packs + 1) * pack_qty


def order_limit(allocated: Decimal, pack_qty: Optional[Decimal]) -> Decimal:
    """Сколько всего можно заказать по строке пакета: распределённое количество, при кратности — округлённое
    вверх до неё. Излишек заказа возможен ТОЛЬКО как такое округление: «заказано 100 при потребности 95» —
    объяснимо барабаном; «про запас» сверх кратности — нет (излишек без дублей, blueprint 52, этап 4).
    """
    return round_up_to_pack(allocated, pack_qty)


def is_pack_multiple(qty: Decimal, pack_qty: Optional[Decimal]) -> bool:
    """Количество кратно упаковке (или кратности нет)."""
    return pack_qty is None or qty % pack_qty == 0


def distribute(received: Decimal, allocations: list[tuple[str, Decimal]]) -> tuple[list[tuple[str, Decimal]], Decimal]:
    """Распределить приход по распределениям пакета в заданном порядке → ([(allocation_id, qty)], излишек).

    Каждому распределению — не больше его остатка (запрошено − уже поставлено); что не поместилось —
    излишек, он остаётся свободным складским остатком (blueprint 52, 2.3). Чистая функция: БД не нужна.
    """
    left = received
    plan: list[tuple[str, Decimal]] = []
    for aid, rem in allocations:
        if left <= 0:
            break
        take = min(rem, left)
        if take > 0:
            plan.append((aid, take))
            left -= take
    return plan, left
