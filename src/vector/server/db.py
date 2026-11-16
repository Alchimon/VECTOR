"""Схема и подключение к серверной БД. Одна схема для PostgreSQL, MS SQL и SQLite.

Решения (см. docs/adr/ADR-0002-database.md):
* строки — Unicode/UnicodeText (на MS SQL -> NVARCHAR, иначе кириллица теряется);
* деньги — Money: NUMERIC(18,4) на PostgreSQL/MS SQL, BIGINT в десятитысячных на SQLite;
* время — UtcDateTime: timestamptz / DATETIMEOFFSET / ISO-строка; в коде всегда aware UTC;
* изоляция — SERIALIZABLE (PG, MS SQL) и BEGIN IMMEDIATE (SQLite) + повтор при конфликте сериализации;
* журнал audit_log — append-only триггерами на всех СУБД (не защита от DBA — см. 05-architecture §7).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.types import TypeDecorator

from vector.core import money

SCHEMA_VERSION = 2

ID = sa.String(36)
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


class Money(TypeDecorator):
    impl = sa.Numeric(18, 4)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(sa.BigInteger())
        return dialect.type_descriptor(sa.Numeric(18, 4, asdecimal=True))

    def process_bind_param(self, value: Any, dialect):
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError("Money принимает только Decimal")
        money.parse_decimal(format(value, "f"))  # повторная проверка масштаба/диапазона на границе БД
        return money.to_minor(value) if dialect.name == "sqlite" else value

    def process_result_value(self, value: Any, dialect):
        if value is None:
            return None
        if dialect.name == "sqlite":
            return money.from_minor(int(value))
        return money.from_minor(money.to_minor(Decimal(value)))


class UtcDateTime(TypeDecorator):
    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "mssql":
            from sqlalchemy.dialects.mssql import DATETIMEOFFSET
            return dialect.type_descriptor(DATETIMEOFFSET(precision=6))
        if dialect.name == "sqlite":
            return dialect.type_descriptor(sa.String(32))
        return dialect.type_descriptor(sa.DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Ожидается время с часовым поясом (UTC)")
        value = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


metadata = sa.MetaData(naming_convention={
    "ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"})

U = sa.Unicode
T = sa.UnicodeText

schema_meta = sa.Table("schema_meta", metadata,
    sa.Column("key", U(64), primary_key=True), sa.Column("value", U(400), nullable=False))

users = sa.Table("users", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("login", U(64), nullable=False, unique=True),
    sa.Column("display_name", U(200), nullable=False),
    sa.Column("pwd_hash", U(200), nullable=False),           # Argon2id (encoded), без открытого пароля
    sa.Column("must_change_password", sa.Boolean, nullable=False),
    sa.Column("password_changed_at", UtcDateTime, nullable=False),
    sa.Column("failed_attempts", sa.Integer, nullable=False),
    sa.Column("locked_until", UtcDateTime),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False))

role_grants = sa.Table("role_grants", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),   # id = generation
    sa.Column("user_id", ID, sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("role", U(32), nullable=False),
    sa.Column("scope_type", U(16), nullable=False),
    sa.Column("valid_from", UtcDateTime, nullable=False),
    sa.Column("valid_until", UtcDateTime),
    sa.Column("revoked_at", UtcDateTime),
    sa.Column("granted_by", ID),
    sa.Column("reason", U(500), nullable=False),
    sa.CheckConstraint("scope_type IN ('company')", name="scope"))

sessions = sa.Table("sessions", metadata,
    sa.Column("token_hash", sa.String(64), primary_key=True),       # SHA-256 токена; сам токен не хранится
    sa.Column("user_id", ID, sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("device_id", U(100), nullable=False),
    sa.Column("client_addr", U(64)),
    sa.Column("issued_at", UtcDateTime, nullable=False),
    sa.Column("last_seen_at", UtcDateTime, nullable=False),
    sa.Column("expires_at", UtcDateTime, nullable=False),
    sa.Column("revoked_at", UtcDateTime),
    sa.Column("revoked_reason", U(200)))

customers = sa.Table("customers", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("name", U(200), nullable=False),
    sa.Column("inn", U(12)),
    sa.Column("note", T),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False))

tenders = sa.Table("tenders", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("customer_id", ID, sa.ForeignKey("customers.id"), nullable=False, index=True),
    sa.Column("title", U(200), nullable=False),
    sa.Column("submission_deadline", sa.Date),
    sa.Column("owner_user_id", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("status", U(32), nullable=False),
    sa.Column("status_reason", T),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False))

tkp = sa.Table("tkp", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("tender_id", ID, sa.ForeignKey("tenders.id"), nullable=False, index=True),
    sa.Column("label", U(64), nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),               # внутренняя монотонная
    sa.Column("amount", Money),                                      # NULL = неизвестно
    sa.Column("currency", sa.String(3)),                             # без умолчания
    sa.Column("note", T),
    sa.Column("status", U(32), nullable=False),
    sa.Column("sent_on", sa.Date),
    sa.Column("sent_channel", U(100)),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("last_edited_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("amount IS NULL OR currency IS NOT NULL", name="currency_with_amount"))

tkp_approvals = sa.Table("tkp_approvals", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("tkp_id", ID, sa.ForeignKey("tkp.id"), nullable=False, index=True),
    sa.Column("tkp_revision", sa.Integer, nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("approver_id", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("grant_id", sa.BigInteger, nullable=False),
    sa.Column("approved_at", UtcDateTime, nullable=False),
    sa.Column("superseded_at", UtcDateTime),
    sa.Column("superseded_reason", U(600)))

projects = sa.Table("projects", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("code", U(64), nullable=False, unique=True),           # A-10
    sa.Column("title", U(200), nullable=False),
    sa.Column("tender_id", ID, sa.ForeignKey("tenders.id")),
    sa.Column("tender_version_at_start", sa.Integer),
    sa.Column("status", U(32), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False))
sa.Index("ux_projects_tender", projects.c.tender_id, unique=True,
         postgresql_where=projects.c.tender_id.isnot(None),
         mssql_where=projects.c.tender_id.isnot(None),
         sqlite_where=projects.c.tender_id.isnot(None))

project_assignments = sa.Table("project_assignments", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),    # id = generation
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("user_id", ID, sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("project_role", U(16), nullable=False),
    sa.Column("valid_from", UtcDateTime, nullable=False),
    sa.Column("valid_until", UtcDateTime),
    sa.Column("revoked_at", UtcDateTime),
    sa.Column("revoked_reason", U(500)),
    sa.Column("assigned_by", ID, nullable=False),
    sa.CheckConstraint("project_role IN ('gip','rp','participant')", name="role"))

kits = sa.Table("kits", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False),
    sa.Column("code", U(64), nullable=False),
    sa.Column("title", U(200), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("project_id", "code", name="uq_kits_project_code"))

operations = sa.Table("operations", metadata,
    sa.Column("operation_id", ID, primary_key=True),
    sa.Column("user_id", ID),
    sa.Column("device_id", U(100)),
    sa.Column("command", U(64), nullable=False),
    sa.Column("request_hash", sa.String(64), nullable=False),
    sa.Column("status", U(32), nullable=False),
    sa.Column("response_json", T, nullable=False),
    sa.Column("recorded_at", UtcDateTime, nullable=False))

audit_log = sa.Table("audit_log", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("at", UtcDateTime, nullable=False),
    sa.Column("actor_user_id", ID),
    sa.Column("device_id", U(100)),
    sa.Column("client_addr", U(64)),
    sa.Column("operation_id", ID),
    sa.Column("command", U(64), nullable=False),
    sa.Column("object_type", U(32)),
    sa.Column("object_id", U(64)),
    sa.Column("authority", U(120)),
    sa.Column("version_before", sa.Integer),
    sa.Column("version_after", sa.Integer),
    sa.Column("result", U(32), nullable=False),
    sa.Column("details_json", T, nullable=False))
sa.Index("ix_audit_object", audit_log.c.object_type, audit_log.c.object_id)

# Порядок таблиц для резервного копирования/восстановления (родители раньше детей).
BACKUP_TABLE_ORDER = [t.name for t in metadata.sorted_tables]

# ---------------------------------------------------------------- append-only журнал
_PG_FN = sa.DDL("""
CREATE OR REPLACE FUNCTION vector_audit_append_only() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END; $$ LANGUAGE plpgsql""")
_PG_TRG = sa.DDL("""CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION vector_audit_append_only()""")
_PG_TRUNC = sa.DDL("""CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_log
FOR EACH STATEMENT EXECUTE FUNCTION vector_audit_append_only()""")
_MS_TRG = sa.DDL("""CREATE TRIGGER audit_append_only ON audit_log INSTEAD OF UPDATE, DELETE AS
BEGIN THROW 50001, 'audit_log is append-only', 1; END""")
_LITE_U = sa.DDL("CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END")
_LITE_D = sa.DDL("CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END")

for ddl in (_PG_FN, _PG_TRG, _PG_TRUNC):
    event.listen(audit_log, "after_create", ddl.execute_if(dialect="postgresql"))
event.listen(audit_log, "after_create", _MS_TRG.execute_if(dialect="mssql"))
for ddl in (_LITE_U, _LITE_D):
    event.listen(audit_log, "after_create", ddl.execute_if(dialect="sqlite"))


# ---------------------------------------------------------------- engine
SUPPORTED = ("postgresql", "mssql", "sqlite")


def make_engine(url: str, **kw) -> Engine:
    u = sa.engine.make_url(url)
    backend = u.get_backend_name()
    if backend not in SUPPORTED:
        raise ValueError(f"СУБД «{backend}» не поддерживается. Поддерживаются: PostgreSQL, MS SQL, SQLite (только тесты/пилот).")
    if backend == "sqlite":
        eng = sa.create_engine(url, connect_args={"timeout": 30, "check_same_thread": False}, **kw)

        @event.listens_for(eng, "connect")
        def _on_connect(dbapi_conn, _rec):
            dbapi_conn.isolation_level = None  # управляем транзакциями сами
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        @event.listens_for(eng, "begin")
        def _on_begin(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        return eng
    return sa.create_engine(url, isolation_level="SERIALIZABLE", pool_pre_ping=True, **kw)


def is_retryable(exc: BaseException) -> bool:
    """Конфликт сериализации/взаимоблокировка — безопасно повторить всю транзакцию."""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "pgcode", None)
    if code in ("40001", "40P01"):
        return True
    text = str(orig or exc)
    return any(s in text for s in ("(1205)", "(3960)", "deadlock", "database is locked", "could not serialize"))


def create_schema(engine: Engine, installation_id: str, now: datetime) -> None:
    with engine.begin() as conn:
        existing = sa.inspect(conn).get_table_names()
        if existing:
            raise RuntimeError("База данных не пуста — инициализация отменена (защита от перезаписи данных).")
        metadata.create_all(conn)
        conn.execute(schema_meta.insert(), [
            {"key": "schema_version", "value": str(SCHEMA_VERSION)},
            {"key": "installation_id", "value": installation_id},
            {"key": "created_at", "value": now.isoformat()},
        ])


def check_schema(engine: Engine) -> int:
    with engine.connect() as conn:
        if "schema_meta" not in sa.inspect(conn).get_table_names():
            raise RuntimeError("Схема ВЕКТОР не найдена. Выполните «vector-server init».")
        meta = {r.key: r.value for r in conn.execute(sa.select(schema_meta))}
    if "restore_in_progress" in meta:
        raise RuntimeError("Восстановление из резервной копии не завершено или не прошло сверку. "
                           "Запуск остановлен (fail-closed). Повторите восстановление в пустую БД.")
    v = meta.get("schema_version")
    if v is None or int(v) != SCHEMA_VERSION:
        raise RuntimeError(f"Версия схемы БД {v} не совпадает с версией приложения {SCHEMA_VERSION}. "
                           "Запуск остановлен (fail-closed). Нужна процедура миграции с резервной копией.")
    return int(v)
