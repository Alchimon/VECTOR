"""Миграция схемы v3 → v4 → v5 → v6 (vector-server migrate): копия до миграции, всё-или-ничего, только добавление.

БД версии 3 строится честно: создаются только таблицы v3 и запись schema_version=3, затем данные среза 1
вносятся обычными командами сервиса (они не касаются таблиц расчётов).
"""
import uuid

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
    # v3 → … → v6 одной транзакцией: 6 таблиц расчётов + 7 таблиц склада и закупки + 1 таблица сопоставлений.
    assert (rep.from_version, rep.to_version) == (3, db.SCHEMA_VERSION) and len(rep.created_tables) == 14
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
    assert rep.created_tables == [t.name for t in mg.ADDED_IN[5] + mg.ADDED_IN[6]]
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
        c.execute(db.schema_meta.update().where(db.schema_meta.c.key == "schema_version").values(value="9"))
    with pytest.raises(mg.MigrationError, match="новее"):
        mg.migrate(eng, tmp_path / "bk", keys)
    with eng.begin() as c:
        c.execute(db.schema_meta.update().where(db.schema_meta.c.key == "schema_version").values(value="1"))
    with pytest.raises(mg.MigrationError, match="Нет шага"):
        mg.migrate(eng, tmp_path / "bk", keys)
