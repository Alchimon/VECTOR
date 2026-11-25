"""Статическая совместимость схемы с MS SQL Server (D-89, issue #3): вся схема — таблицы, индексы, ограничения —
компилируется в DDL диалекта mssql БЕЗ живого сервера (sqlalchemy компилирует офлайн). Это Class B-проверка:
ловит типы/конструкции, которых MS SQL не поймёт, ещё до стенда. Зелёный прогон на настоящем сервере
(с триггерами INSTEAD OF, DATETIMEOFFSET и функциональными сценариями) — отдельно, за площадкой владельца (#3).
"""
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mssql

from vector.server import db

DIALECT = mssql.dialect()


def _compile(obj):
    """Компилировать DDL-выражение в строку T-SQL; любая несовместимость — исключение на этапе компиляции."""
    return str(obj.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True}))


@pytest.mark.parametrize("table", sorted(db.metadata.sorted_tables, key=lambda t: t.name),
                         ids=lambda t: t.name)
def test_table_ddl_compiles_for_mssql(table):
    sql = _compile(sa.schema.CreateTable(table))
    assert "CREATE TABLE" in sql and table.name in sql
    # Хранимых процедур/вычислений серверной стороны быть не должно — вся логика в Python (05-architecture).
    assert "COMPUTE" not in sql and "EXEC" not in sql


def test_all_indexes_compile_for_mssql():
    n = 0
    for table in db.metadata.sorted_tables:
        for idx in table.indexes:
            sql = _compile(sa.schema.CreateIndex(idx))
            assert "INDEX" in sql
            n += 1
    assert n > 30                                    # страховка от «тест ничего не проверил»


def test_check_constraints_and_fks_present():
    """Ключевые инварианты схемы дожили до DDL: FK и CHECK компилируются, не молча отброшены."""
    sql = "\\n".join(_compile(sa.schema.CreateTable(t)) for t in db.metadata.sorted_tables)
    assert "FOREIGN KEY" in sql and "CHECK" in sql


def test_money_and_instant_types_have_mssql_mapping():
    """Деньги — NUMERIC(18,4) (точность 4 знака, не float); моменты времени — DATETIMEOFFSET (UTC-осознанные)."""
    probe = sa.Table("type_probe", sa.MetaData(),
                     sa.Column("m", db.Money(), nullable=False),
                     sa.Column("t", db.UtcDateTime(), nullable=False))
    sql = _compile(sa.schema.CreateTable(probe))
    assert "NUMERIC(18, 4)" in sql and "DATETIMEOFFSET" in sql


def test_mssql_triggers_are_defined():
    """Append-only и неизменяемость версий имеют T-SQL вариант (INSTEAD OF … THROW) — проверяем наличие текста."""
    src = open(db.__file__, encoding="utf-8").read()
    assert src.count('INSTEAD OF UPDATE, DELETE') >= 3    # журнал, версии документов, фабрика _immutable
    assert 'execute_if(dialect="mssql")' in src
