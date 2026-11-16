"""Миграция схемы v3 → v4 (vector-server migrate): копия до миграции, всё-или-ничего, только добавление.

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


def make_v3(backend, tmp_path, clock):
    eng = db.make_engine(make_url(backend, tmp_path))
    v3 = [db.metadata.tables[n] for n in mg.tables_of_version(3)]
    with eng.begin() as c:
        db.metadata.create_all(c, tables=v3)
        c.execute(db.schema_meta.insert(), [{"key": "schema_version", "value": "3"},
                                            {"key": "installation_id", "value": str(uuid.uuid4())},
                                            {"key": "created_at", "value": clock.now().isoformat()}])
    return eng


@pytest.fixture
def keys(tmp_path):
    kf = tmp_path / "backup.key"
    bk.generate_key_file(kf)
    return bk.KeySource(key_file=kf)


def counts(eng, names):
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
    assert (rep.from_version, rep.to_version) == (3, 4) and len(rep.created_tables) == 6
    assert db.check_schema(eng) == 4
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
