"""Склад: правила количеств, остатков и резервов (сценарий С4, шаги 2 и 6; R-13, R-14).

Модуль чистый (без БД и Qt) и общий для сервера и клиента:

* сервер — окончательная проверка в той же транзакции, где записывается движение или резерв;
* клиент — подсказка ДО отправки и расчёт «доступно» по кэшу (с пометкой «на момент …»). Решение клиента
  никогда не считается окончательным: склад меняется другими пользователями, а проводки — только онлайн.

Термины (03-modules-data-lifecycles §4, 02-scenarios С4):

* **остаток** (on_hand) — сколько физически числится на складе по журналу проведённых движений;
* **резерв** (reserved) — сумма НЕизрасходованных частей действующих резервов;
* **доступно** (available) = остаток − резерв. Только это количество можно выдать без резерва или
  зарезервировать заново. Отрицательных остатков и резерва больше остатка не бывает — это инвариант,
  который дополнительно защищён ограничениями CHECK в БД (db.stock_balances).

Правила (не менять без записи в Decision Log, A-20…A-24):

* количество — Decimal, не более 4 знаков после точки, диапазон NUMERIC(18,4), на входе только десятичная
  строка (float запрещён, vector.core.money); в движении и резерве количество строго > 0, направление задаёт
  вид движения, а не знак числа;
* проведённое движение неизменяемо; ошибку исправляют сторно — встречным движением со ссылкой на исходное;
* единица измерения принадлежит номенклатуре; пересчёта единиц нет (коэффициенты не утверждены) —
  количество в движении всегда в единице номенклатуры;
* никаких порогов, норм и количеств «по умолчанию» (Q2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Optional

from . import money

ZERO = Decimal("0")

# Вид движения → знак изменения остатка. Сторно имеет вид, противоположный исходному (receipt ↔ issue),
# и ссылку reverses_id — поэтому отдельного вида «storno» в балансе нет.
MOVEMENT_SIGN = {"receipt": 1, "issue": -1}
MOVEMENT_LABELS_RU = {"receipt": "Приход", "issue": "Выдача"}
# Подпись сторно по ВИДУ САМОГО СТОРНО: расход ("issue") отменяет приход, приход ("receipt") — выдачу.
STORNO_LABELS_RU = {"issue": "Сторно прихода", "receipt": "Сторно выдачи"}
# Подписи половин перемещения между складами (A-28): расход на отправителе и приход на получателе. В балансе это обычные
# issue/receipt — перемещение не меняет общий запас, только распределение по складам.
TRANSFER_LABELS_RU = {"issue": "Расход по перемещению", "receipt": "Приход по перемещению"}
RESERVATION_STATES = ("active", "consumed", "released")
RESERVATION_LABELS_RU = {"active": "Действует", "consumed": "Израсходован", "released": "Снят"}

_CODE_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё._/\-]{0,63}$")


class StockError(ValueError):
    """Нарушено складское правило. code — машинный код (как в ответе сервера), message_ru — для пользователя."""

    def __init__(self, code: str, message_ru: str, details: Optional[dict] = None):
        """Сохраняет машинный код, русское сообщение и подробности (например, текущий остаток для подсказки)."""
        super().__init__(f"{code}: {message_ru}")
        self.code, self.message_ru, self.details = code, message_ru, details or {}


def parse_qty(value: object, label_ru: str = "Количество") -> Decimal:
    """Строго положительное количество из десятичной строки. Ноль и отрицательные — ошибка (направление задаёт вид)."""
    try:
        d = money.parse_decimal(value)
    except money.MoneyError as e:
        raise StockError(e.code, f"«{label_ru}»: {e.message_ru}")
    if d <= 0:
        raise StockError("qty_not_positive", f"«{label_ru}» должно быть больше нуля.")
    return d


def check_code(value: object, label_ru: str = "Код") -> str:
    """Код номенклатуры/склада: 1–64 символа, буквы (в т. ч. кириллица), цифры и . _ / - ; без пробелов.

    Код сравнивается ТОЧНО (с учётом регистра): по нему потребность сопоставляется с номенклатурой, и
    «похожие» коды не должны склеиваться молча.
    """
    if not isinstance(value, str) or not value.strip():
        raise StockError("field_required", f"Заполните поле «{label_ru}».")
    v = value.strip()
    if not _CODE_RE.match(v):
        raise StockError("invalid_code", f"«{label_ru}»: 1–64 символа — буквы, цифры и . _ / - без пробелов.")
    return v


@dataclass(frozen=True)
class Balance:
    """Остаток позиции номенклатуры на одном складе."""
    on_hand: Decimal = ZERO
    reserved: Decimal = ZERO

    @property
    def available(self) -> Decimal:
        """Доступно = остаток − резерв: столько можно выдать без резерва или зарезервировать заново."""
        return self.on_hand - self.reserved

    def to_json(self) -> dict:
        """Остаток для ответа сервера: три десятичные строки с 4 знаками (on_hand, reserved, available)."""
        return {"on_hand": money.to_json(self.on_hand), "reserved": money.to_json(self.reserved),
                "available": money.to_json(self.available)}


def convert_qty(qty: Decimal, factor: Decimal) -> Decimal:
    """Количество строки потребности в единицах позиции: qty × factor (1 единица строки = factor единиц позиции).

    Результат должен быть ТОЧНЫМ: не больше 4 знаков после запятой и в допустимом диапазоне. Иначе — ошибка
    conversion_inexact: округлять количество за пользователя программа не будет (A-27, Q2). Пример: 7 шт при
    «1 шт = 0,0833 упак» дают 0,5831 упак — точно; 1 шт при «1 шт = 0,33333 упак» — коэффициент уже недопустим.
    """
    with localcontext() as ctx:
        ctx.prec = 60
        exact = qty * factor
        q = exact.quantize(Decimal("0.0001"))
    if q != exact:
        raise StockError("conversion_inexact", f"Пересчёт {_fmt(qty)} × {_fmt(factor)} = {exact.normalize():f} — больше 4 знаков "
                         "после запятой. Округление не выполняется: проверьте коэффициент или единицы.",
                         {"exact": f"{exact.normalize():f}"})
    if q > money.MAX_ABS:
        raise StockError("out_of_range", "Пересчитанное количество вне допустимого диапазона.")
    return q


def _fmt(d: Decimal) -> str:
    """Количество для текста сообщения («12.5000»); None не бывает, но на всякий случай показываем ноль, а не «None».
    """
    return money.to_json(d) or "0.0000"


def _checked(b: Balance) -> Balance:
    """Проверить инварианты остатка после операции: 0 ≤ резерв ≤ остаток и остаток в диапазоне NUMERIC(18,4).

    Правила операций уже не допускают нарушений; эта проверка ловит ошибку программы до записи в БД (там те же
    ограничения продублированы CHECK).
    """
    if abs(b.on_hand) > money.MAX_ABS:
        raise StockError("out_of_range", "Остаток вне допустимого диапазона NUMERIC(18,4).")
    if b.on_hand < 0 or b.reserved < 0 or b.reserved > b.on_hand:   # защита от ошибки программы
        raise StockError("stock_invariant", "Внутренняя ошибка: нарушен инвариант остатка (0 ≤ резерв ≤ остаток).")
    return b


def receive(b: Balance, qty: Decimal) -> Balance:
    """Приход (или сторно выдачи): остаток растёт, резерв не меняется."""
    return _checked(Balance(b.on_hand + qty, b.reserved))


def issue_free(b: Balance, qty: Decimal) -> Balance:
    """Выдача без резерва: можно выдать только ДОСТУПНОЕ (чужие резервы неприкосновенны)."""
    if qty > b.available:
        raise StockError("insufficient_available",
                         f"Недостаточно доступного количества: доступно {_fmt(b.available)}, требуется {_fmt(qty)} "
                         f"(остаток {_fmt(b.on_hand)}, в резерве {_fmt(b.reserved)}).",
                         {"balance": b.to_json(), "requested": _fmt(qty)})
    return _checked(Balance(b.on_hand - qty, b.reserved))


def issue_reserved(b: Balance, qty: Decimal, reservation_remaining: Decimal) -> Balance:
    """Выдача по резерву: расходует резерв (остаток и резерв уменьшаются вместе). Больше остатка резерва — нельзя.

    Выдать «резерв + немного сверху» одной операцией нельзя намеренно: сверхрезервная часть — это отдельная
    выдача из доступного, и она должна быть видна в журнале отдельно.
    """
    if qty > reservation_remaining:
        raise StockError("exceeds_reservation",
                         f"По резерву осталось {_fmt(reservation_remaining)}, а к выдаче {_fmt(qty)}. "
                         "Уменьшите количество или оформите остаток отдельной выдачей из доступного.",
                         {"reservation_remaining": _fmt(reservation_remaining), "requested": _fmt(qty)})
    return _checked(Balance(b.on_hand - qty, b.reserved - qty))


def reverse_receipt(b: Balance, qty: Decimal) -> Balance:
    """Сторно прихода: уменьшает остаток. Нельзя, если товар уже зарезервирован или выдан (доступного мало)."""
    if qty > b.available:
        raise StockError("insufficient_available",
                         f"Сторно прихода невозможно: из {_fmt(qty)} доступно только {_fmt(b.available)} — "
                         "часть уже зарезервирована или выдана. Сначала снимите резервы или сторнируйте выдачи.",
                         {"balance": b.to_json(), "requested": _fmt(qty)})
    return _checked(Balance(b.on_hand - qty, b.reserved))


def reserve(b: Balance, qty: Decimal) -> Balance:
    """Резерв: только из доступного. Резерв не двигает остаток (это не выдача)."""
    if qty > b.available:
        raise StockError("insufficient_available",
                         f"Нельзя зарезервировать {_fmt(qty)}: доступно {_fmt(b.available)} "
                         f"(остаток {_fmt(b.on_hand)}, в резерве {_fmt(b.reserved)}).",
                         {"balance": b.to_json(), "requested": _fmt(qty)})
    return _checked(Balance(b.on_hand, b.reserved + qty))


def release(b: Balance, remaining: Decimal) -> Balance:
    """Снятие резерва: неизрасходованная часть возвращается в доступное."""
    return _checked(Balance(b.on_hand, b.reserved - remaining))


def coverage(required: Decimal, reserved_for_need: Decimal, available_total: Optional[Decimal]) -> dict:
    """Покрытие строки потребности: сколько уже зарезервировано, сколько ещё нужно, хватает ли доступного.

    available_total = None — номенклатура не сопоставлена или склад не виден пользователю («неизвестно»
    ≠ «ноль»): тогда can_cover = None, а не False.
    """
    open_qty = max(required - reserved_for_need, ZERO)
    can_cover = None if available_total is None else available_total >= open_qty
    return {"required": _fmt(required), "reserved": _fmt(reserved_for_need), "open": _fmt(open_qty),
            "available": money.to_json(available_total), "can_cover": can_cover}
