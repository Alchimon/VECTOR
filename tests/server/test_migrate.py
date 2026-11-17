"""Миграция схемы v3 → … → v9 (vector-server migrate): копия до миграции, всё-или-ничего, только добавление.

БД версии 3 строится честно: создаются только таблицы v3 и запись schema_version=3, затем данные среза 1
вносятся обычными командами сервиса (они не касаются таблиц расчётов).
"""
import uuid
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
    assert rep.created_tables == [t.name for v in (7, 8, 9) for t in mg.ADDED_IN[v]]
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
    assert rep.created_tables == ["stock_transfers", "stock_shipments"]
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
    assert (rep.from_version, rep.to_version, rep.created_tables) == (8, db.SCHEMA_VERSION, ["stock_shipments"])
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
