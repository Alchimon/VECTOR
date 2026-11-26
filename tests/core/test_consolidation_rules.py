"""Правила консолидации закупок (vector.core.consolidation, D-58, D-59, D-60): покрытие, кратность, распределение
прихода, разбор распределений. Чистые функции — без БД и сети."""
import uuid
from decimal import Decimal


from vector.core import consolidation as cs


def test_remaining_counts_batches_orders_and_reserves():
    # D-60: резерв склада — такое же покрытие, как пакет и заказ; без него строку заказали бы дважды.
    assert cs.remaining(Decimal("10"), Decimal("0"), Decimal("0")) == Decimal("10")
    assert cs.remaining(Decimal("10"), Decimal("4"), Decimal("3"), Decimal("2")) == Decimal("1")
    assert cs.remaining(Decimal("10"), Decimal("10"), Decimal("0")) == Decimal("0")
    assert cs.remaining(Decimal("10"), Decimal("6"), Decimal("6")) == Decimal("-2")   # перекрытие видно, не прячется


def test_round_up_to_pack():
    assert cs.round_up_to_pack(Decimal("95"), Decimal("100")) == Decimal("100")
    assert cs.round_up_to_pack(Decimal("100"), Decimal("100")) == Decimal("100")
    assert cs.round_up_to_pack(Decimal("101"), Decimal("100")) == Decimal("200")
    assert cs.round_up_to_pack(Decimal("95"), None) == Decimal("95")                 # кратности нет — как есть
    assert cs.round_up_to_pack(Decimal("0.5"), Decimal("0.25")) == Decimal("0.5")


def test_order_limit_is_allocated_rounded_up():
    # Излишек заказа — только округлением до кратности: «100 при 95» — да; «200 при 95» — нет.
    assert cs.order_limit(Decimal("95"), Decimal("100")) == Decimal("100")
    assert cs.order_limit(Decimal("95"), None) == Decimal("95")
    assert cs.order_limit(Decimal("95.0001"), Decimal("100")) == Decimal("100")   # один барабан закрывает 95.0001
    assert cs.order_limit(Decimal("100.0001"), Decimal("100")) == Decimal("200")  # а это уже два


def test_is_pack_multiple():
    assert cs.is_pack_multiple(Decimal("100"), Decimal("100"))
    assert cs.is_pack_multiple(Decimal("95"), None)
    assert not cs.is_pack_multiple(Decimal("95"), Decimal("100"))
    assert not cs.is_pack_multiple(Decimal("150"), Decimal("100"))


def test_distribute_receipt_in_order_with_surplus():
    a, b, c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    # Приход 100 на остатки 40 + 40 + 40: первые два закрыты полностью, третьему — 20, излишка нет.
    plan, surplus = cs.distribute(Decimal("100"), [(a, Decimal("40")), (b, Decimal("40")), (c, Decimal("40"))])
    assert plan == [(a, Decimal("40")), (b, Decimal("40")), (c, Decimal("20"))] and surplus == Decimal("0")
    # Приход 150: всё распределено, 30 — излишек (свободный остаток).
    plan, surplus = cs.distribute(Decimal("150"), [(a, Decimal("40")), (b, Decimal("40")), (c, Decimal("40"))])
    assert surplus == Decimal("30") and sum(q for _, q in plan) == Decimal("120")
    # Нулевые остатки пропускаются, пустой список — весь приход излишек.
    plan, surplus = cs.distribute(Decimal("10"), [(a, Decimal("0")), (b, Decimal("5"))])
    assert plan == [(b, Decimal("5"))] and surplus == Decimal("5")
    plan, surplus = cs.distribute(Decimal("7"), [])
    assert plan == [] and surplus == Decimal("7")


def test_parse_allocations_shape():
    nid, lid = str(uuid.uuid4()), str(uuid.uuid4())
    assert cs.parse_allocations([{"need_id": nid, "need_line_id": lid, "qty": "5"}]) == [(nid, lid, Decimal("5"))]
    assert cs.parse_allocations([]) is None                       # пусто
    assert cs.parse_allocations([{"need_id": nid, "need_line_id": lid, "qty": "0"}]) is None      # ноль
    assert cs.parse_allocations([{"need_id": nid, "need_line_id": lid, "qty": "x"}]) is None      # не число
    assert cs.parse_allocations([{"need_id": nid, "need_line_id": lid, "qty": "1"},
                                 {"need_id": nid, "need_line_id": lid, "qty": "2"}]) is None      # дубликат строки
    assert cs.parse_allocations([{"need_id": "no", "need_line_id": lid, "qty": "1"}]) is None     # не uuid
    assert cs.parse_allocations("строка") is None
