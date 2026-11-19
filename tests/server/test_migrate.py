"""Миграция схемы v3 → … → v9 (vector-server migrate): копия до миграции, всё-или-ничего, только добавление.

БД версии 3 строится честно: создаются только таблицы v3 и запись schema_version=3, затем данные среза 1
вносятся обычными командами сервиса (они не касаются таблиц расчётов).
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from conftest import World, make_url, new_id
from vector.server import backup as bk
from vector.server import db
from vector.server import migrate as mg
from vector.server.service import Service


def make_v3(backend, tmp_path, clock, version=3):
    """Создать БД старой версии схемы (по умолчанию v3) с данными — исходное состояние для проверки миграции."""
    eng = db.make_engine(make_url(backend, tmp_path))
    vt = [db.metadata.tables[n] for n in mg.tables_of_version(version)]
    with eng.begin() as c:
        db.metadata.create_all(c, tables=vt)
        c.execute(db.schema_meta.insert(), [{"key": "schema_version", "value": str(version)},
                                            {"key": "installation_id", "value": str(uuid.uuid4())},
                                            {"key": "created_at", "value": clock.now().isoformat()}])
    return eng


@pytest.fixture
def keys(tmp_path):
    """Ключ резервных копий для миграции (миграция всегда сначала делает копию)."""
    kf = tmp_path / "backup.key"
    bk.generate_key_file(kf)
    return bk.KeySource(key_file=kf)


def counts(eng, names):
    """Число строк по таблицам — сверка «до/после миграции»."""
    with eng.connect() as c:
        return {n: c.execute(sa.select(sa.func.count()).select_from(db.metadata.tables[n])).scalar() for n in names}


def test_v3_to_v4_keeps_data_backs_up_first_and_enables_calc(backend, tmp_path, clock, keys):
    eng = make_v3(backend, tmp_path, clock)
    w = World(Service(eng, clock=clock), clock)
    rp = w.login("rp1")
    _, tender, _ = w.won_tender(rp)
    pid, kit = new_id(), new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-1", "title": "Проект"})
    w.admin.ok("project.assign", {"project_id": pid, "user_id": w.users["gip1"], "project_role": "gip", "reason": "Приказ"})
    w.admin.ok("project.assign", {"project_id": pid, "user_id": w.users["rp1"], "project_role": "rp", "reason": "Приказ"})
    rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Комплект"})
    before = counts(eng, mg.tables_of_version(3))
    with pytest.raises(RuntimeError):
        db.check_schema(eng)                       # программа v4 на БД v3 не запускается (fail-closed)

    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    # v3 → … → v9 одной транзакцией: все таблицы, добавленные после v3 (расчёты, склад, сопоставления, настройки,
    # перемещения).
    assert (rep.from_version, rep.to_version) == (3, db.SCHEMA_VERSION)
    assert rep.created_tables == [t.name for v in sorted(mg.ADDED_IN) for t in mg.ADDED_IN[v]]
    assert db.check_schema(eng) == db.SCHEMA_VERSION
    # Данные не тронуты (schema_meta получила одну служебную запись о миграции).
    after = counts(eng, mg.tables_of_version(3))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}
    # Копия до миграции: проверена, схема v3, только таблицы v3.
    vr = bk.verify_backup(rep.backup_path, keys)
    assert vr.ok and vr.header["schema_version"] == 3 and "calc_drafts" not in vr.header["tables"]
    assert vr.counts["kits"] == 1
    # Расчёты работают, триггеры неизменяемости созданы миграцией.
    gip = w.login("gip1")
    line = {"line_id": str(uuid.uuid4()), "name": "Шкаф", "unit": "шт", "qty": "1", "unit_price": "100"}
    gip.ok("calc.save_draft", {"id": kit, "lines": [line], "currency": "RUB"}, expected_version=0)
    gip.ok("calc.freeze", {"id": kit, "reason": "v1"}, expected_version=1)
    with pytest.raises(sa.exc.DBAPIError):
        with eng.begin() as c:
            c.execute(db.calc_versions.update().values(reason="подмена"))
    # Повторный запуск — ничего не делает.
    again = mg.migrate(eng, tmp_path / "bk", keys)
    assert again.backup_path is None and again.created_tables == []


def test_v4_to_v5_adds_stock_without_touching_calc(backend, tmp_path, clock, keys):
    """Стенд, обновлённый до v4 (расчёты), получает склад и закупку; данные расчётов не меняются."""
    eng = make_v3(backend, tmp_path, clock, version=4)
    w = World(Service(eng, clock=clock), clock)
    rp = w.login("rp1")
    _, tender, _ = w.won_tender(rp)
    pid, kit = new_id(), new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-1", "title": "Проект"})
    w.admin.ok("project.assign", {"project_id": pid, "user_id": w.users["gip1"], "project_role": "gip", "reason": "Приказ"})
    w.admin.ok("project.assign", {"project_id": pid, "user_id": w.users["rp1"], "project_role": "rp", "reason": "Приказ"})
    rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Комплект"})
    gip = w.login("gip1")
    line = {"line_id": str(uuid.uuid4()), "name": "Шкаф", "code": "ШК-1", "unit": "шт", "qty": "2", "unit_price": "100"}
    gip.ok("calc.save_draft", {"id": kit, "lines": [line], "currency": "RUB"}, expected_version=0)
    ver = gip.ok("calc.freeze", {"id": kit, "reason": "v1"}, expected_version=1)["calc_version"]
    before = counts(eng, mg.tables_of_version(4))

    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (4, db.SCHEMA_VERSION)
    assert rep.created_tables == [t.name for v in sorted(mg.ADDED_IN) if v >= 5 for t in mg.ADDED_IN[v]]
    after = counts(eng, mg.tables_of_version(4))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}
    vr = bk.verify_backup(rep.backup_path, keys)
    assert vr.ok and vr.header["schema_version"] == 4 and "stock_movements" not in vr.header["tables"]
    assert vr.counts["calc_versions"] == 1
    # Склад работает поверх старых данных: потребность из версии, зафиксированной ДО миграции.
    w.make_user("wh1", "warehouse")
    w.make_user("pr1", "procurement")
    wh, pr = w.login("wh1"), w.login("pr1")
    wid, iid = new_id(), new_id()
    wh.ok("warehouse.create", {"id": wid, "code": "СК-1", "name": "Склад"})
    wh.ok("item.create", {"id": iid, "code": "ШК-1", "name": "Шкаф", "unit": "шт"})
    wh.ok("stock.receive", {"id": new_id(), "warehouse_id": wid, "item_id": iid, "qty": "1", "source_doc": "ТН"})
    need = pr.ok("need.generate", {"id": new_id(), "calc_version_id": ver["id"]})["need"]
    assert need["matched_count"] == 1 and need["lines"][0]["coverage"]["can_cover"] is False
    # Триггеры неизменяемости журнала созданы миграцией.
    with pytest.raises(sa.exc.DBAPIError):
        with eng.begin() as c:
            c.execute(db.stock_movements.delete())


def test_failed_step_leaves_db_unchanged(backend, tmp_path, clock, keys, monkeypatch):
    eng = make_v3(backend, tmp_path, clock)
    real = mg.STEPS[3]

    def broken(conn):
        real(conn)                                  # таблицы созданы…
        raise RuntimeError("сбой посередине миграции")   # …и тут сбой
    monkeypatch.setitem(mg.STEPS, 3, broken)
    with pytest.raises(RuntimeError, match="сбой"):
        mg.migrate(eng, tmp_path / "bk", keys)
    assert mg.current_version(eng) == 3
    with eng.connect() as c:
        assert "calc_drafts" not in sa.inspect(c).get_table_names()


def test_refuses_unexpected_states(backend, tmp_path, clock, keys):
    eng = make_v3(backend, tmp_path, clock)
    with eng.begin() as c:                           # «чужая» таблица с именем из миграции
        db.calc_drafts.create(c)
    with pytest.raises(mg.MigrationError, match="уже существуют"):
        mg.migrate(eng, tmp_path / "bk", keys)
    assert mg.current_version(eng) == 3
    with eng.begin() as c:
        c.execute(db.schema_meta.insert().values(key="restore_in_progress", value="1"))
    with pytest.raises(mg.MigrationError, match="Восстановление"):
        mg.migrate(eng, tmp_path / "bk", keys)


def test_newer_or_unknown_versions_refused(backend, tmp_path, clock, keys):
    eng = make_v3(backend, tmp_path, clock)
    with eng.begin() as c:
        c.execute(db.schema_meta.update().where(db.schema_meta.c.key == "schema_version").values(value="99"))
    with pytest.raises(mg.MigrationError, match="новее"):
        mg.migrate(eng, tmp_path / "bk", keys)
    with eng.begin() as c:
        c.execute(db.schema_meta.update().where(db.schema_meta.c.key == "schema_version").values(value="1"))
    with pytest.raises(mg.MigrationError, match="Нет шага"):
        mg.migrate(eng, tmp_path / "bk", keys)


def test_v6_to_v7_adds_settings_and_unit_tables_with_immutability(backend, tmp_path, clock, keys):
    """v6 → v7: только новые таблицы (настройки, аналоги, пересчёт единиц); история настроек неизменяема."""
    eng = make_v3(backend, tmp_path, clock, version=6)
    before = counts(eng, mg.tables_of_version(6))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (6, db.SCHEMA_VERSION)
    assert rep.created_tables == [t.name for v in range(7, db.SCHEMA_VERSION + 1) for t in mg.ADDED_IN[v]]
    after = counts(eng, mg.tables_of_version(6))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}
    with eng.begin() as c:
        c.execute(db.config_versions.insert().values(id=new_id(), key="k", scope_type="company", scope_id="", version=1,
                                                     value_json='"x"', reason="r", author=new_id(), created_at=clock.now()))
    with pytest.raises(sa.exc.DBAPIError):
        with eng.begin() as c:
            c.execute(db.config_versions.update().values(value_json='"y"'))


def test_v7_to_v8_adds_transfers_immutable_and_checked(backend, tmp_path, clock, keys):
    """v7 → v8: только новая таблица перемещений; запись неизменяема, склады должны различаться (CHECK в БД)."""
    eng = make_v3(backend, tmp_path, clock, version=7)
    before = counts(eng, mg.tables_of_version(7))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (7, db.SCHEMA_VERSION)
    assert rep.created_tables == [t.name for v in range(8, db.SCHEMA_VERSION + 1) for t in mg.ADDED_IN[v]]
    assert rep.created_tables[:4] == ["stock_transfers", "stock_shipments", "stock_transfer_acts", "stock_shipment_terms"]
    assert rep.backup_path.exists()                     # сначала — проверенная копия БД v7
    after = counts(eng, mg.tables_of_version(7))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}
    assert db.check_schema(eng) == db.SCHEMA_VERSION
    # Данные для проверки ограничений: склад, позиция, два движения.
    now, uid, w1, w2, it, m1, m2 = clock.now(), new_id(), new_id(), new_id(), new_id(), new_id(), new_id()
    with eng.begin() as c:
        for w, code in ((w1, "СК-1"), (w2, "СК-2")):
            c.execute(db.warehouses.insert().values(id=w, code=code, name=code, active=True, version=1, created_by=uid,
                                                    created_at=now, updated_by=uid, updated_at=now))
        c.execute(db.stock_items.insert().values(id=it, code="Х", name="Х", unit="шт", active=True, version=1,
                                                 created_by=uid, created_at=now, updated_by=uid, updated_at=now))
        for no, (m, kind, w) in enumerate(((m1, "issue", w1), (m2, "receipt", w2)), start=1):
            c.execute(db.stock_movements.insert().values(id=m, doc_no=no, kind=kind, warehouse_id=w, item_id=it,
                                                         qty=Decimal("1"), posted_by=uid, posted_at=now))
    row = dict(item_id=it, from_warehouse_id=w1, to_warehouse_id=w2, qty=Decimal("1"), out_movement_id=m1, in_movement_id=m2,
               reason="Перенос", posted_by=uid, posted_at=now)
    with pytest.raises(sa.exc.DBAPIError):                  # склад-отправитель = склад-получатель — БД не примет
        with eng.begin() as c:
            c.execute(db.stock_transfers.insert().values(id=new_id(), **{**row, "to_warehouse_id": w1}))
    with eng.begin() as c:
        c.execute(db.stock_transfers.insert().values(id=new_id(), **row))
    with pytest.raises(sa.exc.DBAPIError):                  # одно движение — не больше чем в одном перемещении
        with eng.begin() as c:
            c.execute(db.stock_transfers.insert().values(id=new_id(), **row))
    with pytest.raises(sa.exc.DBAPIError):                  # неизменяемость
        with eng.begin() as c:
            c.execute(db.stock_transfers.update().values(reason="Подмена"))


def test_v8_to_v9_adds_shipments_with_consistent_states(backend, tmp_path, clock, keys):
    """v8 → v9: только таблица перемещений «в пути»; БД не даст записать «принято» без закрывающего движения и
    одно закрывающее движение двум перемещениям."""
    eng = make_v3(backend, tmp_path, clock, version=8)
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (8, db.SCHEMA_VERSION)
    assert rep.created_tables == [t.name for v in range(9, db.SCHEMA_VERSION + 1) for t in mg.ADDED_IN[v]]
    now, uid, w1, w2, it = clock.now(), new_id(), new_id(), new_id(), new_id()
    mv = [new_id() for _ in range(6)]
    with eng.begin() as c:
        for w, code in ((w1, "СК-1"), (w2, "СК-2")):
            c.execute(db.warehouses.insert().values(id=w, code=code, name=code, active=True, version=1, created_by=uid,
                                                    created_at=now, updated_by=uid, updated_at=now))
        c.execute(db.stock_items.insert().values(id=it, code="Х", name="Х", unit="шт", active=True, version=1,
                                                 created_by=uid, created_at=now, updated_by=uid, updated_at=now))
        for no, m in enumerate(mv, start=1):
            c.execute(db.stock_movements.insert().values(id=m, doc_no=no, kind="issue" if no % 2 else "receipt",
                                                         warehouse_id=w1, item_id=it, qty=Decimal("1"),
                                                         posted_by=uid, posted_at=now))
    base = dict(item_id=it, from_warehouse_id=w1, to_warehouse_id=w2, qty=Decimal("1"), reason="Перенос", version=1,
                created_by=uid, created_at=now)

    def ins(**kw):
        with eng.begin() as c:
            c.execute(db.stock_shipments.insert().values(id=new_id(), **{**base, **kw}))
    ins(status="in_transit", out_movement_id=mv[0])
    ins(status="in_transit", out_movement_id=mv[2])                       # много «в пути» без закрытия — можно
    with pytest.raises(sa.exc.DBAPIError):                                # «принято» без закрывающего движения
        ins(status="received", out_movement_id=mv[1])
    ins(status="received", out_movement_id=mv[1], close_movement_id=mv[3], closed_by=uid, closed_at=now)
    with pytest.raises(sa.exc.DBAPIError):                                # то же закрывающее движение повторно
        ins(status="returned", out_movement_id=mv[4], close_movement_id=mv[3], closed_by=uid, closed_at=now)
    with pytest.raises(sa.exc.DBAPIError):                                # неизвестный статус
        ins(status="lost", out_movement_id=mv[5])


def test_v9_to_v10_adds_shortage_acts_and_terms_immutable_and_balanced(backend, tmp_path, clock, keys):
    """v9 → v10: только таблицы актов недостачи и сроков прибытия; БД сама требует «отгружено = принято + недостача»,
    один акт на перемещение, уникальный номер; обе таблицы неизменяемы."""
    eng = make_v3(backend, tmp_path, clock, version=9)
    before = counts(eng, mg.tables_of_version(9))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (9, db.SCHEMA_VERSION)
    assert rep.created_tables[:2] == ["stock_transfer_acts", "stock_shipment_terms"] and rep.backup_path.exists()
    after = counts(eng, mg.tables_of_version(9))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}
    now, uid, w1, w2, it = clock.now(), new_id(), new_id(), new_id(), new_id()
    mv, sh = [new_id() for _ in range(4)], [new_id(), new_id()]
    with eng.begin() as c:
        for w, code in ((w1, "СК-1"), (w2, "СК-2")):
            c.execute(db.warehouses.insert().values(id=w, code=code, name=code, active=True, version=1, created_by=uid,
                                                    created_at=now, updated_by=uid, updated_at=now))
        c.execute(db.stock_items.insert().values(id=it, code="Х", name="Х", unit="шт", active=True, version=1,
                                                 created_by=uid, created_at=now, updated_by=uid, updated_at=now))
        for no, m in enumerate(mv, start=1):
            c.execute(db.stock_movements.insert().values(id=m, doc_no=no, kind="issue" if no % 2 else "receipt",
                                                         warehouse_id=w1, item_id=it, qty=Decimal("1"),
                                                         posted_by=uid, posted_at=now))
        for s_id, out, close in ((sh[0], mv[0], mv[1]), (sh[1], mv[2], mv[3])):
            c.execute(db.stock_shipments.insert().values(
                id=s_id, item_id=it, from_warehouse_id=w1, to_warehouse_id=w2, qty=Decimal("10"), status="received",
                out_movement_id=out, close_movement_id=close, reason="r", version=2, created_by=uid, created_at=now,
                closed_by=uid, closed_at=now))
    act = dict(shipped_qty=Decimal("10"), accepted_qty=Decimal("7"), shortage_qty=Decimal("3"), reason="Бой",
               signed_by=uid, signer_name="Приёмщик", signed_at=now)

    def ins(**kw):
        with eng.begin() as c:
            c.execute(db.stock_transfer_acts.insert().values(id=new_id(), **{**act, **kw}))
    with pytest.raises(sa.exc.DBAPIError):                      # 7 + 2 ≠ 10 — БД не примет
        ins(shipment_id=sh[0], act_no=1, shortage_qty=Decimal("2"))
    with pytest.raises(sa.exc.DBAPIError):                      # «недостача» 0 — это не акт
        ins(shipment_id=sh[0], act_no=1, accepted_qty=Decimal("10"), shortage_qty=Decimal("0"))
    ins(shipment_id=sh[0], act_no=1)
    with pytest.raises(sa.exc.DBAPIError):                      # второй акт на то же перемещение
        ins(shipment_id=sh[0], act_no=2)
    with pytest.raises(sa.exc.DBAPIError):                      # номер акта не повторяется
        ins(shipment_id=sh[1], act_no=1)
    with eng.begin() as c:
        c.execute(db.stock_shipment_terms.insert().values(shipment_id=sh[0], expected_on=now.date()))
    for upd in (db.stock_transfer_acts.update().values(reason="Подмена"),
                db.stock_shipment_terms.update().values(expected_on=now.date())):
        with pytest.raises(sa.exc.DBAPIError):                  # неизменяемость
            with eng.begin() as c:
                c.execute(upd)


def test_v10_to_v11_adds_losses_and_signatures_immutable_and_checked(backend, tmp_path, clock, keys):
    """v10 → v11: только таблицы актов об утрате и подписей; способ подписи — из списка, одна подпись на документ и
    назначение; обе таблицы неизменяемы."""
    eng = make_v3(backend, tmp_path, clock, version=10)
    before = counts(eng, mg.tables_of_version(10))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version, rep.created_tables) == (10, db.SCHEMA_VERSION, [t.name for v in range(11, db.SCHEMA_VERSION + 1) for t in mg.ADDED_IN[v]])
    after = counts(eng, mg.tables_of_version(10))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}
    now, uid, oid = clock.now(), new_id(), new_id()
    sig = dict(object_type="stock_loss_act", object_id=oid, purpose="act", signer_id=uid, signer_name="П",
               signed_at=now, content_sha256="0" * 64)

    def ins(**kw):
        with eng.begin() as c:
            c.execute(db.signatures.insert().values(id=new_id(), **{**sig, **kw}))
    with pytest.raises(sa.exc.DBAPIError):                      # неизвестный способ подписи
        ins(method="pen")
    ins(method="password")
    with pytest.raises(sa.exc.DBAPIError):                      # вторая подпись того же назначения
        ins(method="confirm")
    with pytest.raises(sa.exc.DBAPIError):                      # неизменяемость
        with eng.begin() as c:
            c.execute(db.signatures.update().values(method="confirm"))


def test_v11_to_v12_adds_purchasing_tables_with_checks(backend, tmp_path, clock, keys):
    """v11 → v12: поставщики, заказы, строки, факты поставки. Данные прежних таблиц не меняются; статус заказа — из
    списка, размещённый заказ — с контрольной суммой; строка — количество > 0, цена ≥ 0 или неизвестна, ссылка на
    потребность — парой; факт поставки неизменяем."""
    eng = make_v3(backend, tmp_path, clock, version=11)
    before = counts(eng, mg.tables_of_version(11))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version, rep.created_tables) == (
        11, db.SCHEMA_VERSION, [t.name for v in range(12, db.SCHEMA_VERSION + 1) for t in mg.ADDED_IN[v]])
    assert rep.created_tables[:4] == ["suppliers", "purchase_orders", "purchase_order_lines", "purchase_receipts"]
    after = counts(eng, mg.tables_of_version(11))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}
    now, uid, sid, oid, it = clock.now(), new_id(), new_id(), new_id(), new_id()
    stamp = dict(version=1, created_by=uid, created_at=now, updated_by=uid, updated_at=now)
    with eng.begin() as c:
        c.execute(db.suppliers.insert().values(id=sid, code="П-1", name="ООО Поставщик", active=True, **stamp))
        c.execute(db.stock_items.insert().values(id=it, code="X", name="X", unit="шт", active=True, **stamp))

    def bad(stmt):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.execute(stmt)
    bad(db.purchase_orders.insert().values(id=new_id(), order_no=1, supplier_id=sid, status="sent", **stamp))
    bad(db.purchase_orders.insert().values(id=new_id(), order_no=1, supplier_id=sid, status="placed", **stamp))
    with eng.begin() as c:
        c.execute(db.purchase_orders.insert().values(id=oid, order_no=1, supplier_id=sid, status="draft", **stamp))
    line = dict(order_id=oid, line_id=new_id(), item_id=it, qty=Decimal("1"))
    bad(db.purchase_order_lines.insert().values(pos=1, **{**line, "qty": Decimal("0")}))
    bad(db.purchase_order_lines.insert().values(pos=1, **line, unit_price=Decimal("-1")))
    bad(db.purchase_order_lines.insert().values(pos=1, **line, need_line_id=new_id()))
    with eng.begin() as c:
        c.execute(db.purchase_order_lines.insert().values(pos=1, **line))


def test_v12_to_v13_adds_requisites_contracts_vat_and_approvals(backend, tmp_path, clock, keys):
    """v12 → v13 (D-33): только новые таблицы-расширения — реквизиты и договоры поставщика, условия заказа (договор,
    режим цен), ставки НДС строк, журнал согласования. Заказы v12 не меняются; журнал согласования неизменяем;
    режим цен — из списка; срок договора не раньше даты заключения."""
    eng = make_v3(backend, tmp_path, clock, version=12)
    now, uid, sid, oid = clock.now(), new_id(), new_id(), new_id()
    stamp = dict(version=1, created_by=uid, created_at=now, updated_by=uid, updated_at=now)
    with eng.begin() as c:
        c.execute(db.suppliers.insert().values(id=sid, code="П-1", name="ООО Поставщик", active=True, **stamp))
        c.execute(db.purchase_orders.insert().values(id=oid, order_no=1, supplier_id=sid, status="draft", **stamp))
    before = counts(eng, mg.tables_of_version(12))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version, rep.created_tables) == (
        12, db.SCHEMA_VERSION, [t.name for v in range(13, db.SCHEMA_VERSION + 1) for t in mg.ADDED_IN[v]])
    assert rep.created_tables[:5] == ["supplier_requisites", "supplier_contracts", "purchase_order_terms",
                                      "purchase_order_line_taxes", "purchase_order_approvals"]
    after = counts(eng, mg.tables_of_version(12))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}

    def bad(stmt):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.execute(stmt)
    from datetime import date
    cid = new_id()
    bad(db.supplier_contracts.insert().values(id=new_id(), supplier_id=sid, number="1", signed_on=date(2026, 5, 1),
                                              valid_until=date(2026, 4, 1), active=True, **stamp))
    with eng.begin() as c:
        c.execute(db.supplier_contracts.insert().values(id=cid, supplier_id=sid, number="1", signed_on=date(2026, 5, 1),
                                                        active=True, **stamp))
    bad(db.supplier_contracts.insert().values(id=new_id(), supplier_id=sid, number="1", signed_on=date(2026, 5, 1),
                                              active=True, **stamp))          # номер у поставщика уникален
    bad(db.purchase_order_terms.insert().values(order_id=oid, vat_mode="gross"))
    with eng.begin() as c:
        c.execute(db.purchase_order_terms.insert().values(order_id=oid, contract_id=cid, vat_mode="included"))
    ev = dict(order_id=oid, actor_id=uid, at=now)
    bad(db.purchase_order_approvals.insert().values(seq=1, kind="submit", **ev))           # без хэша
    bad(db.purchase_order_approvals.insert().values(seq=1, kind="ok", content_hash="0" * 64, **ev))
    with eng.begin() as c:
        c.execute(db.purchase_order_approvals.insert().values(seq=1, kind="submit", content_hash="0" * 64, **ev))
    bad(db.purchase_order_approvals.update().values(kind="approve"))                         # неизменяем
    bad(db.purchase_order_approvals.delete())
    assert db.check_schema(eng) == db.SCHEMA_VERSION


def test_v13_to_v14_adds_delegations_bases_and_invoice_checks(backend, tmp_path, clock, keys):
    """v13 → v14 (D-34): замещение, основания решений, суммы по счёту и сверка. Данные v13 не меняются; замещение —
    не себе, не роль администратора, срок не наоборот; основания и результаты сверки неизменяемы; расхождение без
    причины не записывается."""
    from datetime import date
    eng = make_v3(backend, tmp_path, clock, version=13)
    now, uid, uid2, sid, oid = clock.now(), new_id(), new_id(), new_id(), new_id()
    stamp = dict(version=1, created_by=uid, created_at=now, updated_by=uid, updated_at=now)
    with eng.begin() as c:
        for u, login in ((uid, "u1"), (uid2, "u2")):
            c.execute(db.users.insert().values(id=u, login=login, display_name=login, pwd_hash="x",
                                               must_change_password=False, password_changed_at=now, failed_attempts=0,
                                               active=True, created_at=now))
        c.execute(db.suppliers.insert().values(id=sid, code="П-1", name="ООО Поставщик", active=True, **stamp))
        c.execute(db.purchase_orders.insert().values(id=oid, order_no=1, supplier_id=sid, status="draft", **stamp))
    before = counts(eng, mg.tables_of_version(13))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version, rep.created_tables) == (
        13, db.SCHEMA_VERSION, [t.name for v in range(14, db.SCHEMA_VERSION + 1) for t in mg.ADDED_IN[v]])
    assert rep.created_tables[:4] == ["role_delegations", "approval_bases", "purchase_order_invoices",
                                      "purchase_order_invoice_checks"]
    after = counts(eng, mg.tables_of_version(13))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}

    def bad(stmt):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.execute(stmt)
    d = dict(from_user_id=uid, to_user_id=uid2, role="po_approver", valid_from=date(2026, 11, 16),
             valid_until=date(2026, 11, 30), reason="Отпуск", created_by=uid, created_at=now)
    bad(db.role_delegations.insert().values(**{**d, "to_user_id": uid}))                   # не себе
    bad(db.role_delegations.insert().values(**{**d, "role": "admin"}))                     # не администратор
    bad(db.role_delegations.insert().values(**{**d, "valid_until": date(2026, 11, 1)}))    # срок наоборот
    with eng.begin() as c:
        c.execute(db.role_delegations.insert().values(**d))
    base = dict(object_type="purchase_order", object_id=oid, seq=1, actor_id=uid2, role="po_approver",
                authority="delegated_role:po_approver#grant:1#delegation:1", at=now)
    with eng.begin() as c:
        c.execute(db.approval_bases.insert().values(**base))
    bad(db.approval_bases.update().values(role="dept_head"))                                 # неизменяемо
    bad(db.approval_bases.delete())
    bad(db.purchase_order_invoices.insert().values(order_id=oid, invoice_gross=Decimal("-1")))
    with eng.begin() as c:
        c.execute(db.purchase_order_invoices.insert().values(order_id=oid, invoice_gross=Decimal("100.10")))
    bad(db.purchase_order_invoice_checks.insert().values(order_id=oid, seq=1, state="mismatch"))   # без причины
    bad(db.purchase_order_invoice_checks.insert().values(order_id=oid, seq=1, state="maybe"))
    with eng.begin() as c:
        c.execute(db.purchase_order_invoice_checks.insert().values(order_id=oid, seq=1, state="mismatch",
                                                                   diff_gross=Decimal("0.01"), reason="Счёт округлён"))
    bad(db.purchase_order_invoice_checks.update().values(reason="другая"))
    assert db.check_schema(eng) == db.SCHEMA_VERSION


def test_v14_to_v15_adds_project_delegations_and_notifications(backend, tmp_path, clock, keys):
    """v14 → v15 (D-35): замещение роли в проекте (расширение role_delegations) и уведомления. Данные v14 не
    меняются; проект замещения неизменяем; уведомление можно отметить прочитанным, но не без адресата."""
    from datetime import date
    eng = make_v3(backend, tmp_path, clock, version=14)
    now, uid, uid2, pid = clock.now(), new_id(), new_id(), new_id()
    with eng.begin() as c:
        for u, login in ((uid, "u1"), (uid2, "u2")):
            c.execute(db.users.insert().values(id=u, login=login, display_name=login, pwd_hash="x",
                                               must_change_password=False, password_changed_at=now, failed_attempts=0,
                                               active=True, created_at=now))
        c.execute(db.projects.insert().values(id=pid, code="П-1", title="Проект", status="rkd_in_progress", version=1,
                                              created_by=uid, created_at=now, updated_by=uid, updated_at=now))
        did = c.execute(db.role_delegations.insert().values(
            from_user_id=uid, to_user_id=uid2, role="gip", valid_from=date(2026, 11, 16), valid_until=date(2026, 11, 30),
            reason="Отпуск", created_by=uid, created_at=now)).inserted_primary_key[0]
    before = counts(eng, mg.tables_of_version(14))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (14, db.SCHEMA_VERSION)
    assert rep.created_tables == [t.name for v in range(15, db.SCHEMA_VERSION + 1) for t in mg.ADDED_IN[v]]
    after = counts(eng, mg.tables_of_version(14))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}

    def bad(stmt):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.execute(stmt)
    with eng.begin() as c:
        c.execute(db.role_delegation_projects.insert().values(delegation_id=did, project_id=pid))
    bad(db.role_delegation_projects.insert().values(delegation_id=did, project_id=pid))       # одна область
    bad(db.role_delegation_projects.update().values(project_id=new_id()))                     # неизменяемо
    bad(db.role_delegation_projects.delete())
    bad(db.notifications.insert().values(user_id=None, kind="po_submitted", title="x", created_at=now))
    with eng.begin() as c:
        nid = c.execute(db.notifications.insert().values(user_id=uid, kind="po_submitted", title="Заказ № 1",
                                                         created_at=now)).inserted_primary_key[0]
        c.execute(db.notifications.update().where(db.notifications.c.id == nid).values(read_at=now))
    assert db.check_schema(eng) == db.SCHEMA_VERSION


def test_v15_to_v16_adds_production_tables_with_immutable_snapshot(backend, tmp_path, clock, keys):
    """v15 → v16 (D-36): технология и производство. Данные v15 не меняются; снимок наряда, утверждение ТП, события
    исполнителя, замены и выдачи на наряд неизменяемы; отменённый наряд — только с причиной."""
    eng = make_v3(backend, tmp_path, clock, version=15)
    now, uid, pid, kid = clock.now(), new_id(), new_id(), new_id()
    with eng.begin() as c:
        c.execute(db.users.insert().values(id=uid, login="u1", display_name="u1", pwd_hash="x", must_change_password=False,
                                           password_changed_at=now, failed_attempts=0, active=True, created_at=now))
        c.execute(db.projects.insert().values(id=pid, code="П-1", title="Проект", status="rkd_in_progress", version=1,
                                              created_by=uid, created_at=now, updated_by=uid, updated_at=now))
        c.execute(db.kits.insert().values(id=kid, project_id=pid, code="К", title="Комплект", version=1,
                                          created_by=uid, created_at=now, updated_by=uid, updated_at=now))
    before = counts(eng, mg.tables_of_version(15))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (15, db.SCHEMA_VERSION)
    assert rep.created_tables == [t.name for v in range(16, db.SCHEMA_VERSION + 1) for t in mg.ADDED_IN[v]]
    after = counts(eng, mg.tables_of_version(15))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}

    def bad(stmt):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.execute(stmt)
    tid, wid = new_id(), new_id()
    with eng.begin() as c:
        c.execute(db.tech_processes.insert().values(id=tid, project_id=pid, kit_id=kid, revision_no=1, title="ТП",
                                                    status="approved", content_hash="h" * 64, version=1, created_by=uid,
                                                    created_at=now, updated_by=uid, updated_at=now))
        c.execute(db.tech_process_approvals.insert().values(tp_id=tid, content_hash="h" * 64, approved_by=uid,
                                                            authority="global_role:technologist#grant:1", approved_at=now))
    wo = dict(id=wid, doc_no=1, project_id=pid, kit_id=kid, tp_id=tid, qty=1, title="Наряд", snapshot_hash="s" * 64,
              version=1, released_by=uid, released_at=now, updated_by=uid, updated_at=now)
    bad(db.work_orders.insert().values(**wo, status="cancelled"))                    # отмена — только с причиной
    bad(db.work_orders.insert().values(**{**wo, "qty": 0}, status="released"))
    with eng.begin() as c:
        c.execute(db.work_orders.insert().values(**wo, status="released"))
        c.execute(db.work_order_snapshots.insert().values(work_order_id=wid, content_hash="s" * 64, snapshot_json="{}",
                                                          created_at=now))
    bad(db.work_order_snapshots.update().values(snapshot_json='{"x":1}'))           # снимок неизменяем
    bad(db.work_order_snapshots.delete())
    bad(db.tech_process_approvals.update().values(content_hash="x" * 64))
    with eng.begin() as c:
        c.execute(db.work_orders.update().where(db.work_orders.c.id == wid).values(status="in_progress", version=2))
    assert db.check_schema(eng) == db.SCHEMA_VERSION


def test_v18_to_v19_relaxes_claim_instance_and_adds_terms_and_responses(backend, tmp_path, clock, keys, monkeypatch):
    """v18 → v19 (D-40, ответ на A-46): рекламация без заводского номера. У claims.instance_id снимается NOT NULL —
    данные v18 не меняются; появляются условия рекламации (охват, классификация, срок ответа) и неизменяемые ответы
    заказчику. Работает на SQLite (правка схемы) и PostgreSQL (ALTER COLUMN … DROP NOT NULL)."""
    # Схема v18 — как у установленной программы: instance_id NOT NULL (в db.py v19 столбец уже допускает NULL).
    monkeypatch.setattr(db.claims.c.instance_id, "nullable", False)
    eng = make_v3(backend, tmp_path, clock, version=18)
    monkeypatch.undo()
    with eng.connect() as c:
        assert not mg._column_nullable(c, "claims", "instance_id")
    now, uid, pid, kid, tid, wid, iid, cid = clock.now(), *(new_id() for _ in range(7))
    meta = dict(version=1, created_by=uid, created_at=now, updated_by=uid, updated_at=now)
    with eng.begin() as c:
        c.execute(db.users.insert().values(id=uid, login="u1", display_name="u1", pwd_hash="x", must_change_password=False,
                                           password_changed_at=now, failed_attempts=0, active=True, created_at=now))
        c.execute(db.projects.insert().values(id=pid, code="П-1", title="Проект", status="rkd_in_progress", **meta))
        c.execute(db.kits.insert().values(id=kid, project_id=pid, code="К", title="Комплект", **meta))
        c.execute(db.tech_processes.insert().values(id=tid, project_id=pid, kit_id=kid, revision_no=1, title="ТП",
                                                    status="approved", content_hash="h" * 64, **meta))
        c.execute(db.work_orders.insert().values(id=wid, doc_no=1, project_id=pid, kit_id=kid, tp_id=tid, qty=2, title="Наряд",
                                                 snapshot_hash="s" * 64, status="closed", version=1, released_by=uid,
                                                 released_at=now, updated_by=uid, updated_at=now))
        c.execute(db.product_instances.insert().values(id=iid, project_id=pid, kit_id=kid, work_order_id=wid, series="2026",
                                                       serial_no="001", serial_key="001", status="registered", **meta))
        c.execute(db.claims.insert().values(id=cid, claim_no=1, project_id=pid, instance_id=iid, received_date=now.date(),
                                            source="customer", description="Отказ", status="registered", **meta))
    before = counts(eng, mg.tables_of_version(18))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (18, db.SCHEMA_VERSION)
    assert rep.created_tables == [t.name for v in range(19, db.SCHEMA_VERSION + 1) for t in mg.ADDED_IN[v]]
    assert rep.backup_path.name.startswith("vector-premigrate-v18-")
    after = counts(eng, mg.tables_of_version(18))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}
    with eng.connect() as c:
        assert mg._column_nullable(c, "claims", "instance_id")
        assert c.execute(sa.select(db.claims.c.instance_id).where(db.claims.c.id == cid)).scalar() == iid  # данные те же

    def bad(stmt):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.execute(stmt)
    c2 = new_id()
    with eng.begin() as c:
        # Рекламация на партию: без экземпляра, наряд — в условиях.
        c.execute(db.claims.insert().values(id=c2, claim_no=2, project_id=pid, instance_id=None, received_date=now.date(),
                                            source="customer", description="Партия", status="registered", **meta))
        c.execute(db.claim_terms.insert().values(claim_id=c2, scope="batch", work_order_id=wid, severity="major",
                                                 response_due=now.date(), due_set_at=now, updated_by=uid, updated_at=now))
        c.execute(db.claim_responses.insert().values(claim_id=c2, seq=1, kind="interim", response_date=now.date(),
                                                     summary="Приняли в работу", authority="global_role:otk#grant:1",
                                                     responded_by=uid, responded_at=now))
    terms = dict(updated_by=uid, updated_at=now)
    bad(db.claim_terms.insert().values(claim_id=cid, scope="batch", **terms))                   # партия — с нарядом
    bad(db.claim_terms.insert().values(claim_id=cid, scope="instance", response_due=now.date(), **terms))  # срок — с моментом
    bad(db.claim_terms.insert().values(claim_id=cid, scope="instance", severity="huge", **terms))
    bad(db.claim_responses.update().values(summary="Другое"))                                   # ответ неизменяем
    bad(db.claim_responses.delete())
    assert db.check_schema(eng) == db.SCHEMA_VERSION
    # Повторный запуск — уже текущая версия: ничего не делает.
    assert mg.migrate(eng, tmp_path / "bk", keys, now=clock.now()).created_tables == []


def test_v18_to_v19_refuses_unexpected_claims_definition(tmp_path, clock, keys, monkeypatch):
    """SQLite: если текст определения claims не такой, как ожидалось, — отказ без изменений (fail-closed)."""
    monkeypatch.setattr(db.claims.c.instance_id, "nullable", False)
    monkeypatch.setattr(db.claims.c.instance_id, "type", sa.String(40))      # «чужое» определение столбца
    eng = make_v3("sqlite", tmp_path, clock, version=18)
    monkeypatch.undo()
    with pytest.raises(mg.MigrationError, match="не такое, как ожидалось"):
        mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    with eng.connect() as c:
        assert mg.current_version(eng) == 18 and "claim_terms" not in sa.inspect(c).get_table_names()
        assert not mg._column_nullable(c, "claims", "instance_id")


def test_v19_to_v20_adds_worktime_tables(backend, tmp_path, clock, keys):
    """v19 → v20 (D-42): только новые таблицы рабочего времени; данные v19 не меняются; ограничения и неизменяемость
    журнала шагов действуют на обеих СУБД."""
    eng = make_v3(backend, tmp_path, clock, version=19)
    now, uid, rid = clock.now(), new_id(), new_id()
    with eng.begin() as c:
        c.execute(db.users.insert().values(id=uid, login="u1", display_name="u1", pwd_hash="x", must_change_password=False,
                                           password_changed_at=now, failed_attempts=0, active=True, created_at=now))
    before = counts(eng, mg.tables_of_version(19))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (19, db.SCHEMA_VERSION)
    assert rep.created_tables[:3] == ["daily_reports", "daily_report_lines", "daily_report_events"]
    after = counts(eng, mg.tables_of_version(19))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}

    def bad(stmt):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.execute(stmt)
    r = dict(user_id=uid, work_date=now.date(), status="draft", break_minutes=0, total_minutes=0, version=1,
             created_by=uid, created_at=now, updated_by=uid, updated_at=now)
    bad(db.daily_reports.insert().values(id=new_id(), **{**r, "status": "in_review"}))
    bad(db.daily_reports.insert().values(id=new_id(), shift_start=600, **r))                  # смена — обе границы
    bad(db.daily_reports.insert().values(id=new_id(), shift_start=600, shift_end=540, **r))   # конец позже начала
    with eng.begin() as c:
        c.execute(db.daily_reports.insert().values(id=rid, shift_start=510, shift_end=1050, **r))
        c.execute(db.daily_report_events.insert().values(report_id=rid, seq=1, decision="created", details_json="{}",
                                                         authority="authenticated", decided_by=uid, decided_at=now))
    bad(db.daily_reports.insert().values(id=new_id(), **r))                                   # один отчёт за день
    bad(db.daily_report_lines.insert().values(report_id=rid, line_no=1, task_id=new_id(), minutes=30, note="x"))
    bad(db.daily_report_lines.insert().values(report_id=rid, line_no=1, minutes=0, note="x"))
    bad(db.daily_report_events.update().values(decision="x"))                                 # журнал неизменяем
    bad(db.daily_report_events.delete())
    assert db.check_schema(eng) == db.SCHEMA_VERSION


def _v20_checks(monkeypatch):
    """Ограничения daily_reports — как в схеме v20 (до ответов на A-48): без self_check, смена в пределах суток."""
    old = {"status": db.REPORT_STATUS_CHECK_V20, "shift": db.REPORT_SHIFT_CHECK_V20}
    for ck in db.daily_reports.constraints:
        if isinstance(ck, sa.CheckConstraint) and str(ck.name).split("_")[-1] in old:
            monkeypatch.setattr(ck, "sqltext", sa.text(old[str(ck.name).split("_")[-1]]))


def test_v20_to_v21_self_check_night_shift_and_auto_tables(backend, tmp_path, clock, keys, monkeypatch):
    """v20 → v21 (D-43, ответы на A-48): статус «на самопроверке» и ночная смена (ослабленные ограничения), отметка
    «собран программой» (неизменяема) и источник времени строк; данные v20 не меняются; индекс журнала по сотруднику."""
    _v20_checks(monkeypatch)
    eng = make_v3(backend, tmp_path, clock, version=20)
    monkeypatch.undo()
    now, uid, rid = clock.now(), new_id(), new_id()
    r = dict(user_id=uid, status="draft", break_minutes=0, total_minutes=0, version=1, created_by=uid, created_at=now,
             updated_by=uid, updated_at=now)

    def bad(stmt):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.execute(stmt)
    with eng.begin() as c:
        c.execute(db.users.insert().values(id=uid, login="u1", display_name="u1", pwd_hash="x", must_change_password=False,
                                           password_changed_at=now, failed_attempts=0, active=True, created_at=now))
        c.execute(db.daily_reports.insert().values(id=rid, work_date=now.date(), shift_start=510, shift_end=1050, **r))
    # До миграции: ночная смена и «на самопроверке» запрещены (схема v20).
    bad(db.daily_reports.insert().values(id=new_id(), work_date=date(2026, 11, 1), shift_start=1320, shift_end=1800, **r))
    bad(db.daily_reports.insert().values(id=new_id(), work_date=date(2026, 11, 2), **{**r, "status": "self_check"}))
    before = counts(eng, mg.tables_of_version(20))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (20, db.SCHEMA_VERSION)
    assert rep.created_tables[:2] == ["daily_report_auto", "daily_report_line_sources"]
    assert rep.backup_path.name.startswith("vector-premigrate-v20-")
    after = counts(eng, mg.tables_of_version(20))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}
    with eng.begin() as c:
        c.execute(db.daily_reports.insert().values(id=new_id(), work_date=date(2026, 11, 1), shift_start=1320, shift_end=1800, **r))
        c.execute(db.daily_reports.insert().values(id=new_id(), work_date=date(2026, 11, 2), **{**r, "status": "self_check"}))
        c.execute(db.daily_report_auto.insert().values(report_id=rid, assembled_at=now, summary_json="{}"))
        c.execute(db.daily_report_line_sources.insert().values(report_id=rid, line_no=1, source="measured"))
        assert "ix_audit_actor_at" in {i["name"] for i in sa.inspect(c).get_indexes("audit_log")}
    bad(db.daily_reports.insert().values(id=new_id(), work_date=date(2026, 11, 3), shift_start=600, shift_end=2100, **r))  # > суток
    bad(db.daily_reports.insert().values(id=new_id(), work_date=date(2026, 11, 4), shift_start=1440, shift_end=1500, **r))
    bad(db.daily_reports.insert().values(id=new_id(), work_date=date(2026, 11, 5), **{**r, "status": "in_review"}))
    bad(db.daily_report_line_sources.insert().values(report_id=rid, line_no=2, source="guess"))
    bad(db.daily_report_auto.update().values(summary_json="[]"))                              # отметка неизменяема
    assert db.check_schema(eng) == db.SCHEMA_VERSION
    assert mg.migrate(eng, tmp_path / "bk", keys, now=clock.now()).created_tables == []


def test_v20_to_v21_refuses_unexpected_check_definition(tmp_path, clock, keys, monkeypatch):
    """SQLite: определение ограничения не такое, как ожидалось (правили вручную), — отказ без изменений."""
    _v20_checks(monkeypatch)
    for ck in db.daily_reports.constraints:
        if isinstance(ck, sa.CheckConstraint) and str(ck.name).endswith("status"):
            monkeypatch.setattr(ck, "sqltext", sa.text("status IN ('draft','submitted','accepted','returned','x')"))
    eng = make_v3("sqlite", tmp_path, clock, version=20)
    monkeypatch.undo()
    with pytest.raises(mg.MigrationError, match="не такое, как ожидалось"):
        mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    with eng.connect() as c:
        assert mg.current_version(eng) == 20 and "daily_report_auto" not in sa.inspect(c).get_table_names()


def test_v21_to_v22_adds_org_structure(backend, tmp_path, clock, keys):
    """v21 → v22 (D-44, ответ на A-48 п. 1): таблицы оргструктуры; данные v21 не меняются; журнал оргструктуры
    неизменяем; подразделение не входит само в себя, вид — из трёх."""
    eng = make_v3(backend, tmp_path, clock, version=21)
    now, uid = clock.now(), new_id()
    with eng.begin() as c:
        c.execute(db.users.insert().values(id=uid, login="u1", display_name="u1", pwd_hash="x", must_change_password=False,
                                           password_changed_at=now, failed_attempts=0, active=True, created_at=now))
    before = counts(eng, mg.tables_of_version(21))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (21, db.SCHEMA_VERSION)
    assert rep.created_tables[:3] == ["org_units", "org_members", "org_events"]
    after = counts(eng, mg.tables_of_version(21))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}
    unit = dict(kind="department", name="Отдел", active=True, version=1, created_by=uid, created_at=now, updated_by=uid,
                updated_at=now)
    uid1 = new_id()
    with eng.begin() as c:
        c.execute(db.org_units.insert().values(id=uid1, **unit))
        c.execute(db.org_members.insert().values(user_id=uid, unit_id=uid1, position="Инженер", since=now, updated_by=uid))
        c.execute(db.org_events.insert().values(object_type="unit", object_id=uid1, event="created", details_json="{}",
                                                authority="global_role:admin#grant:1", actor_id=uid, at=now))

    def bad(stmt):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.execute(stmt)
    bad(db.org_units.insert().values(id=new_id(), **{**unit, "kind": "division"}))
    x = new_id()
    bad(db.org_units.insert().values(id=x, parent_id=x, **unit))
    bad(db.org_events.update().values(event="x"))                                       # журнал неизменяем
    bad(db.org_members.insert().values(user_id=uid, unit_id=uid1, since=now, updated_by=uid))   # один на сотрудника
    assert db.check_schema(eng) == db.SCHEMA_VERSION
    assert mg.migrate(eng, tmp_path / "bk", keys, now=clock.now()).created_tables == []


def test_v22_to_v23_adds_workday_timer(backend, tmp_path, clock, keys):
    """v22 → v23 (D-46, ответы на A-49 пп. 2–4): таблицы таймера рабочего дня и запросов на фактическое начало;
    данные v22 не меняются; журнал нажатий неизменяем; один рабочий день на сотрудника и дату; статусы — из списка."""
    eng = make_v3(backend, tmp_path, clock, version=22)
    now, uid = clock.now(), new_id()
    with eng.begin() as c:
        c.execute(db.users.insert().values(id=uid, login="u1", display_name="u1", pwd_hash="x", must_change_password=False,
                                           password_changed_at=now, failed_attempts=0, active=True, created_at=now))
    before = counts(eng, mg.tables_of_version(22))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (22, db.SCHEMA_VERSION)
    assert rep.created_tables[:3] == ["workdays", "workday_events", "workday_start_requests"]
    after = counts(eng, mg.tables_of_version(22))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}
    day = dict(user_id=uid, work_date=now.date(), status="working", started_at=now, version=1, created_at=now,
               updated_at=now)
    wid = new_id()
    with eng.begin() as c:
        c.execute(db.workdays.insert().values(id=wid, **day))
        c.execute(db.workday_events.insert().values(workday_id=wid, seq=1, event="start", device_time=now,
                                                    server_time=now, operation_id=new_id()))
        c.execute(db.workday_start_requests.insert().values(id=new_id(), workday_id=wid, user_id=uid,
                                                            requested_start_at=now, reason="Забыл", status="pending",
                                                            created_at=now, version=1))

    def bad(stmt):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.execute(stmt)
    bad(db.workdays.insert().values(id=new_id(), **day))                                  # один день на дату
    bad(db.workdays.insert().values(id=new_id(), **{**day, "work_date": now.date() - timedelta(days=1), "status": "x"}))
    bad(db.workday_events.update().values(event="pause"))                                 # журнал неизменяем
    bad(db.workday_events.insert().values(workday_id=wid, seq=2, event="lunch", device_time=now, server_time=now,
                                          operation_id=new_id()))
    assert db.check_schema(eng) == db.SCHEMA_VERSION
    assert mg.migrate(eng, tmp_path / "bk", keys, now=clock.now()).created_tables == []


def test_v23_to_v24_adds_schedules_and_forgotten_finish(backend, tmp_path, clock, keys):
    """v23 → v24 (D-47, ответы на A-51): графики работы и их назначения, пометка автоматического «Завершить»
    (неизменяема, код 50037) и запросы «фактическое окончание дня»; данные v23 не меняются; назначение — одно на
    получателя, вид получателя и причина автоматического завершения — из списка."""
    eng = make_v3(backend, tmp_path, clock, version=23)
    now, uid = clock.now(), new_id()
    with eng.begin() as c:
        c.execute(db.users.insert().values(id=uid, login="u1", display_name="u1", pwd_hash="x", must_change_password=False,
                                           password_changed_at=now, failed_attempts=0, active=True, created_at=now))
    before = counts(eng, mg.tables_of_version(23))
    rep = mg.migrate(eng, tmp_path / "bk", keys, now=clock.now())
    assert (rep.from_version, rep.to_version) == (23, db.SCHEMA_VERSION)
    assert rep.created_tables[:4] == ["work_schedules", "work_schedule_links", "workday_auto_finishes",
                                      "workday_end_requests"]
    after = counts(eng, mg.tables_of_version(23))
    assert {k: v for k, v in after.items() if k != "schema_meta"} == {k: v for k, v in before.items() if k != "schema_meta"}
    sid, wid = new_id(), new_id()
    with eng.begin() as c:
        c.execute(db.work_schedules.insert().values(id=sid, name="Пятидневка", start_time="08:00", end_time="17:00",
                                                    break_minutes=60, weekdays="1111100", is_default=True, active=True,
                                                    version=1, created_at=now, updated_at=now))
        c.execute(db.work_schedule_links.insert().values(id=new_id(), target_kind="user", target_id=uid, schedule_id=sid,
                                                         exempt=False, version=1, created_at=now, updated_at=now))
        c.execute(db.workdays.insert().values(id=wid, user_id=uid, work_date=now.date(), status="finished",
                                              started_at=now, version=1, created_at=now, updated_at=now))
        c.execute(db.workday_auto_finishes.insert().values(workday_id=wid, seq=2, reason="no_answer", created_at=now))
        c.execute(db.workday_end_requests.insert().values(id=new_id(), workday_id=wid, user_id=uid,
                                                          requested_end_at=now, reason="Работал дольше",
                                                          status="pending", created_at=now, version=1))

    def bad(stmt):
        with pytest.raises(sa.exc.DBAPIError):
            with eng.begin() as c:
                c.execute(stmt)
    link = dict(schedule_id=sid, exempt=False, version=1, created_at=now, updated_at=now)
    bad(db.work_schedule_links.insert().values(id=new_id(), target_kind="user", target_id=uid, **link))   # одно назначение
    bad(db.work_schedule_links.insert().values(id=new_id(), target_kind="team", target_id=new_id(), **link))
    bad(db.workday_auto_finishes.insert().values(workday_id=wid, seq=3, reason="guess", created_at=now))
    bad(db.workday_auto_finishes.update().values(reason="closed"))                         # пометка неизменяема
    bad(db.workday_end_requests.update().values(status="x"))
    assert db.check_schema(eng) == db.SCHEMA_VERSION
    assert mg.migrate(eng, tmp_path / "bk", keys, now=clock.now()).created_tables == []
