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

# История версий схемы. Начиная с v3 базы обновляются командой `vector-server migrate` (migrate.py, D-22):
# каждый шаг только ДОБАВЛЯЕТ таблицы, существующие данные не меняются.
#   v2 — срез 1: заказчики, тендеры, ТКП, проекты, назначения, комплекты, учётные записи, аудит;
#   v3 — задачи, документы, неизменяемые версии документов, хранилище файлов в БД (блоками);
#   v4 — расчёт комплекта: черновик, неизменяемые версии K.n, неизменяемые ревизии проектного итога (С3);
#   v5 — склад и закупка: номенклатура, склады, остатки, резервы, журнал движений, потребность (С4, D-23);
#   v6 — ручное сопоставление строк потребности с номенклатурой (неизменяемая история, A-25);
#   v7 — настройки компании (версии, D-26), справочник аналогов и пересчёта единиц, коэффициент в решении о
#        сопоставлении (D-25, A-27);
#   v8 — перемещения между складами: связка двух движений журнала (A-28).
SCHEMA_VERSION = 8

# Идентификатор объекта — UUID строкой (36 символов). Его создаёт клиент, поэтому объект, заведённый офлайн,
# сохраняет свой id после синхронизации, а повторная отправка той же команды распознаётся.
ID = sa.String(36)
# Автоинкрементный номер (журналы). В SQLite автоинкремент работает только у INTEGER PRIMARY KEY.
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


class Money(TypeDecorator):
    """Тип столбца для денег и количеств (Decimal, 4 знака после запятой).

    В PostgreSQL/MS SQL — NUMERIC(18,4); в SQLite, где нет точного десятичного типа, — целое число десятитысячных
    (BIGINT), чтобы не было ошибок округления float. Принимает только Decimal; масштаб и диапазон проверяются ещё
    раз на границе с БД.
    """
    impl = sa.Numeric(18, 4)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        """Фактический тип столбца для конкретной СУБД (BIGINT для SQLite, NUMERIC(18,4) для остальных)."""
        if dialect.name == "sqlite":
            return dialect.type_descriptor(sa.BigInteger())
        return dialect.type_descriptor(sa.Numeric(18, 4, asdecimal=True))

    def process_bind_param(self, value: Any, dialect):
        """Python → БД: только Decimal (float и int — ошибка программы), проверка 4 знаков и диапазона; для SQLite —
        перевод в десятитысячные.
        """
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError("Money принимает только Decimal")
        money.parse_decimal(format(value, "f"))  # повторная проверка масштаба/диапазона на границе БД
        return money.to_minor(value) if dialect.name == "sqlite" else value

    def process_result_value(self, value: Any, dialect):
        """БД → Python: всегда Decimal ровно с 4 знаками после запятой, независимо от СУБД."""
        if value is None:
            return None
        if dialect.name == "sqlite":
            return money.from_minor(int(value))
        return money.from_minor(money.to_minor(Decimal(value)))


class UtcDateTime(TypeDecorator):
    """Тип столбца «момент времени в UTC». Время без часового пояса не принимается (ошибка программы). PostgreSQL —
    timestamptz, MS SQL — DATETIMEOFFSET(6), SQLite — строка ISO с суффиксом Z (сортируется как время).
    """
    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        """Фактический тип столбца для конкретной СУБД."""
        if dialect.name == "mssql":
            from sqlalchemy.dialects.mssql import DATETIMEOFFSET
            return dialect.type_descriptor(DATETIMEOFFSET(precision=6))
        if dialect.name == "sqlite":
            return dialect.type_descriptor(sa.String(32))
        return dialect.type_descriptor(sa.DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        """Python → БД: перевод в UTC; для SQLite — строка фиксированного формата."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Ожидается время с часовым поясом (UTC)")
        value = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return value

    def process_result_value(self, value, dialect):
        """БД → Python: datetime с часовым поясом UTC во всех СУБД."""
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

# ---------------------------------------------------------------- задачи (A-13)
# Задача живёт внутри комплекта проекта. project_id хранится явно (денормализация), чтобы права
# «роль в проекте» проверялись без лишних соединений и чтобы pull отбирал задачи по проекту одним запросом.
tasks = sa.Table("tasks", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("kit_id", ID, sa.ForeignKey("kits.id"), nullable=False, index=True),
    sa.Column("title", U(200), nullable=False),
    sa.Column("description", T),
    # Исполнитель — пользователь с действующим назначением в этом проекте (проверяется в обработчике).
    # NULL = «исполнитель не назначен» (это не то же самое, что «назначен на всех»).
    sa.Column("assignee_user_id", ID, sa.ForeignKey("users.id")),
    sa.Column("due_date", sa.Date),          # срок не подставляется по умолчанию — только если его ввели
    # Необязательная ссылка на документ, к которому относится задача/замечание.
    sa.Column("document_id", ID, sa.ForeignKey("documents.id")),
    sa.Column("status", U(16), nullable=False),
    sa.Column("status_reason", U(2000)),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("status IN ('open','in_progress','done','cancelled')", name="status"))

# ---------------------------------------------------------------- документы (A-14, A-15)
# Карточка документа: обозначение (code) уникально в пределах проекта, название, принадлежность комплекту.
# Файлы хранятся в неизменяемых версиях (document_versions); current_version_no — номер последней версии
# (0 — у документа ещё нет файла: «пусто» ≠ «ошибка»).
documents = sa.Table("documents", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("kit_id", ID, sa.ForeignKey("kits.id"), nullable=False, index=True),
    sa.Column("code", U(100), nullable=False),
    sa.Column("title", U(300), nullable=False),
    sa.Column("current_version_no", sa.Integer, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),          # версия КАРТОЧКИ (для обнаружения конфликтов)
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("project_id", "code", name="uq_documents_project_code"))

# Хранилище файлов (D-20): содержимое адресуется SHA-256 и хранится в БД блоками по FILE_CHUNK байт.
# Плюсы: одна транзакционная система, файлы входят в ту же резервную копию и восстанавливаются согласованно
# с метаданными на PostgreSQL / MS SQL. Одинаковый файл хранится один раз (дедупликация по хэшу).
FILE_CHUNK = 1024 * 1024
file_blobs = sa.Table("file_blobs", metadata,
    sa.Column("sha256", sa.String(64), primary_key=True),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("chunk_count", sa.Integer, nullable=False),
    sa.Column("uploaded_by", ID, nullable=False),
    sa.Column("uploaded_at", UtcDateTime, nullable=False))

file_chunks = sa.Table("file_chunks", metadata,
    sa.Column("sha256", sa.String(64), sa.ForeignKey("file_blobs.sha256"), primary_key=True),
    sa.Column("idx", sa.Integer, primary_key=True),
    # LargeBinary → bytea (PostgreSQL), VARBINARY(MAX) (MS SQL), BLOB (SQLite).
    sa.Column("data", sa.LargeBinary, nullable=False))

# Квитанция загрузки: «пользователь U прислал байты файла с хэшем H в рамках проекта P».
# Зачем: файлы дедуплицируются по SHA-256, и без квитанции любой, кто УЗНАЛ хэш (например, видел его
# в метаданных чужого проекта), мог бы сослаться на этот файл в своём документе и скачать его.
# Добавить версию документа можно только с файлом, который ты сам загрузил (доказал владение байтами).
file_uploads = sa.Table("file_uploads", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("sha256", sa.String(64), sa.ForeignKey("file_blobs.sha256"), nullable=False, index=True),
    sa.Column("user_id", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False),
    sa.Column("uploaded_at", UtcDateTime, nullable=False))

# Версия документа НЕИЗМЕНЯЕМА: после вставки строка не обновляется и не удаляется (как и аудит —
# триггеры ниже). Исправление = новая версия с номером n+1; история сохраняется полностью.
document_versions = sa.Table("document_versions", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("document_id", ID, sa.ForeignKey("documents.id"), nullable=False, index=True),
    sa.Column("version_no", sa.Integer, nullable=False),
    sa.Column("file_name", U(255), nullable=False),
    sa.Column("sha256", sa.String(64), sa.ForeignKey("file_blobs.sha256"), nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("comment", U(2000)),
    sa.Column("created_by", ID, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("document_id", "version_no", name="uq_document_versions_no"))

# ---------------------------------------------------------------- расчёт комплекта (С3, R-04/R-11/R-12)
# Правила позиций и итогов — vector.core.calc (общие с клиентом). Количество хранится тем же типом Money:
# NUMERIC(18,4) / BIGINT в десятитысячных — те же точность и диапазон, без float.
Qty = Money

# Черновик расчёта: один на комплект, изменяемый («Сохранить расчёт», в том числе офлайн через очередь).
# Официальный итог проекта от черновика НЕ зависит. version — для обнаружения конфликтов правок.
calc_drafts = sa.Table("calc_drafts", metadata,
    sa.Column("kit_id", ID, sa.ForeignKey("kits.id"), primary_key=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("currency", sa.String(3)),          # NULL — валюта не указана (допустимо, пока нет ни одной цены)
    sa.Column("note", U(2000)),
    sa.Column("line_count", sa.Integer, nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False))


def _calc_line_columns() -> list:
    """Колонки позиции расчёта (одинаковые у черновика и у версии). amount = qty × unit_price, вычисляется сервером."""
    return [sa.Column("pos", sa.Integer, primary_key=True),
            sa.Column("line_id", ID, nullable=False),
            sa.Column("code", U(100)),
            sa.Column("name", U(300), nullable=False),
            sa.Column("unit", U(20), nullable=False),
            sa.Column("qty", Qty, nullable=False),
            sa.Column("unit_price", Money),       # NULL = цена неизвестна (≠ 0)
            sa.Column("amount", Money),           # NULL ⇔ unit_price NULL
            sa.Column("note", U(1000))]


calc_draft_lines = sa.Table("calc_draft_lines", metadata,
    sa.Column("kit_id", ID, sa.ForeignKey("calc_drafts.kit_id"), primary_key=True),
    *_calc_line_columns(),
    sa.UniqueConstraint("kit_id", "line_id", name="uq_calc_draft_lines_line"))

# Зафиксированная версия K.n — НЕИЗМЕНЯЕМА (триггеры ниже), как и её позиции. Итоги хранятся вместе
# с версией: ревизия проектного итога ссылается на точную версию и её hash.
calc_versions = sa.Table("calc_versions", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("kit_id", ID, sa.ForeignKey("kits.id"), nullable=False, index=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("version_no", sa.Integer, nullable=False),
    sa.Column("currency", sa.String(3)),
    sa.Column("note", U(2000)),
    sa.Column("reason", U(2000), nullable=False),
    sa.Column("total", Money, nullable=False),          # сумма ИЗВЕСТНЫХ позиций
    sa.Column("line_count", sa.Integer, nullable=False),
    sa.Column("unknown_price_count", sa.Integer, nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("draft_version", sa.Integer, nullable=False),   # какая версия черновика была зафиксирована
    sa.Column("frozen_by", ID, nullable=False),
    sa.Column("frozen_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("kit_id", "version_no", name="uq_calc_versions_no"))

calc_version_lines = sa.Table("calc_version_lines", metadata,
    sa.Column("version_id", BIGID, sa.ForeignKey("calc_versions.id"), primary_key=True),
    *_calc_line_columns())

# Ревизия проектного итога P.r — создаётся ТОЛЬКО при фиксации комплекта, неизменяема. Состав = точный
# список (комплект, версия, hash) последних зафиксированных версий всех комплектов проекта на тот момент.
project_calc_revisions = sa.Table("project_calc_revisions", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("revision_no", sa.Integer, nullable=False),
    sa.Column("reason", U(2000), nullable=False),
    sa.Column("trigger_kit_id", ID, sa.ForeignKey("kits.id"), nullable=False),
    sa.Column("trigger_version_id", BIGID, sa.ForeignKey("calc_versions.id"), nullable=False),
    sa.Column("created_by", ID, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("project_id", "revision_no", name="uq_project_calc_revisions_no"))

project_calc_revision_items = sa.Table("project_calc_revision_items", metadata,
    sa.Column("revision_id", BIGID, sa.ForeignKey("project_calc_revisions.id"), primary_key=True),
    sa.Column("kit_id", ID, sa.ForeignKey("kits.id"), primary_key=True),
    sa.Column("calc_version_id", BIGID, sa.ForeignKey("calc_versions.id"), nullable=False),
    sa.Column("version_no", sa.Integer, nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("currency", sa.String(3)),
    sa.Column("total", Money, nullable=False),
    sa.Column("unknown_price_count", sa.Integer, nullable=False))

# ---------------------------------------------------------------- склад и закупка (С4, R-13/R-14, схема v5)
# Правила остатков/резервов — vector.core.stock (общие с клиентом). Количества — тот же тип Money (Qty):
# NUMERIC(18,4) / BIGINT в десятитысячных, без float.

# Номенклатура: код уникален и сравнивается точно — по нему строки потребности сопоставляются с позицией.
# Единица измерения принадлежит номенклатуре; пересчёта единиц нет (коэффициенты не утверждены).
stock_items = sa.Table("stock_items", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("code", U(64), nullable=False, unique=True),
    sa.Column("name", U(300), nullable=False),
    sa.Column("unit", U(20), nullable=False),
    sa.Column("note", U(1000)),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False))

warehouses = sa.Table("warehouses", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("code", U(64), nullable=False, unique=True),
    sa.Column("name", U(200), nullable=False),
    sa.Column("note", U(1000)),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False))

# Остаток позиции на складе — ПРОИЗВОДНЫЕ данные: всегда равны свёртке журнала движений и действующих
# резервов (это проверяет тест и `stock_ledger_check`). Хранятся отдельно ради двух вещей:
# 1) конкурентная проверка — каждая проводка/резерв меняет ЭТУ строку с условием по version, поэтому две
#    параллельные выдачи последней единицы не пройдут обе даже при ошибке в уровне изоляции;
# 2) ограничения CHECK — БД сама не даст записать отрицательный остаток или резерв больше остатка.
stock_balances = sa.Table("stock_balances", metadata,
    sa.Column("warehouse_id", ID, sa.ForeignKey("warehouses.id"), primary_key=True),
    sa.Column("item_id", ID, sa.ForeignKey("stock_items.id"), primary_key=True),
    sa.Column("on_hand", Qty, nullable=False),
    sa.Column("reserved", Qty, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("on_hand >= 0", name="on_hand_nonneg"),
    sa.CheckConstraint("reserved >= 0", name="reserved_nonneg"),
    sa.CheckConstraint("reserved <= on_hand", name="reserved_le_on_hand"))

# Потребность закупки — снимок зафиксированной версии расчёта комплекта (без цен: цены — данные расчёта,
# закупке они в этом срезе не раскрываются, A-22). Одна потребность на версию. Потребность — НЕ заказ.
procurement_needs = sa.Table("procurement_needs", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("kit_id", ID, sa.ForeignKey("kits.id"), nullable=False, index=True),
    sa.Column("calc_version_id", BIGID, sa.ForeignKey("calc_versions.id"), nullable=False, unique=True),
    sa.Column("calc_version_no", sa.Integer, nullable=False),
    sa.Column("calc_content_hash", sa.String(64), nullable=False),
    sa.Column("line_count", sa.Integer, nullable=False),
    sa.Column("matched_count", sa.Integer, nullable=False),
    sa.Column("status", U(32), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("status IN ('open')", name="status"))

# Строки потребности неизменяемы (снимок). item_id — номенклатура, найденная при формировании по ТОЧНОМУ
# совпадению кода и единицы; NULL — не сопоставлена (причина в match_note), сопоставление вручную — позже.
procurement_need_lines = sa.Table("procurement_need_lines", metadata,
    sa.Column("need_id", ID, sa.ForeignKey("procurement_needs.id"), primary_key=True),
    sa.Column("pos", sa.Integer, primary_key=True),
    sa.Column("line_id", ID, nullable=False),
    sa.Column("code", U(100)),
    sa.Column("name", U(300), nullable=False),
    sa.Column("unit", U(20), nullable=False),
    sa.Column("qty", Qty, nullable=False),
    sa.Column("item_id", ID, sa.ForeignKey("stock_items.id")),
    sa.Column("match_note", U(200)),
    sa.UniqueConstraint("need_id", "line_id", name="uq_procurement_need_lines_line"))

# Ручное сопоставление строки потребности с номенклатурой (A-25). Сама строка — неизменяемый снимок расчёта,
# поэтому решение закупки хранится отдельно и ТОЖЕ неизменяемо: каждое пересопоставление — новая запись со
# следующим seq, действует запись с наибольшим seq (история «кто, когда, на что и почему поменял» сохраняется).
# previous_item_id — что действовало до этой записи (автоматическое или прошлое ручное; NULL — не сопоставлена).
procurement_need_line_matches = sa.Table("procurement_need_line_matches", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("need_id", ID, sa.ForeignKey("procurement_needs.id"), nullable=False),
    sa.Column("line_id", ID, nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("item_id", ID, sa.ForeignKey("stock_items.id"), nullable=False),
    sa.Column("previous_item_id", ID),
    sa.Column("reason", U(2000), nullable=False),
    sa.Column("matched_by", ID, nullable=False),
    sa.Column("matched_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("need_id", "line_id", "seq", name="uq_need_line_matches_seq"),
    sa.CheckConstraint("seq > 0", name="seq_pos"))

# ---------------------------------------------------------------- v7: настройки и справочники закупки
# Настройки по модели 07: каждая публикация — НОВАЯ неизменяемая версия (key, scope, version); действует версия
# с наибольшим номером. Откат = публикация прежнего значения новой версией, история не переписывается.
# scope_id для компании — пустая строка (а не NULL): уникальность с NULL на разных СУБД ведёт себя по-разному.
config_versions = sa.Table("config_versions", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("key", U(100), nullable=False),
    sa.Column("scope_type", U(16), nullable=False),
    sa.Column("scope_id", U(36), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("value_json", T, nullable=False),
    sa.Column("reason", U(2000), nullable=False),
    sa.Column("author", ID, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("key", "scope_type", "scope_id", "version", name="uq_config_versions_version"),
    sa.CheckConstraint("version > 0", name="version_pos"),
    sa.CheckConstraint("scope_type IN ('company')", name="scope_type"))

# Другие обозначения позиции номенклатуры (аналоги, D-25): «КВВГнг(А)-LS 7×1,5» → позиция КВВГнг-7x1.5. Обозначение
# уникально во всём справочнике; потребность сопоставляется по нему автоматически, если единица совпадает.
item_aliases = sa.Table("item_aliases", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("item_id", ID, sa.ForeignKey("stock_items.id"), nullable=False, index=True),
    sa.Column("alias", U(100), nullable=False, unique=True),
    sa.Column("note", U(500)),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False))

# Пересчёт единиц (D-25, A-27): 1 from_unit = factor to_unit. item_id = NULL — общий пересчёт для любой позиции
# («км → м» = 1000), иначе — только для этой позиции («компл → шт» = 4). item_key — item_id или «*»: по нему
# уникальность (NULL в уникальном ключе на PG и MS SQL ведёт себя по-разному). Коэффициент вводит человек.
unit_conversions = sa.Table("unit_conversions", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("item_id", ID, sa.ForeignKey("stock_items.id")),
    sa.Column("item_key", U(36), nullable=False),
    sa.Column("from_unit", U(20), nullable=False),
    sa.Column("to_unit", U(20), nullable=False),
    sa.Column("factor", Qty, nullable=False),
    sa.Column("note", U(500)),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("item_key", "from_unit", "to_unit", name="uq_unit_conversions_pair"),
    sa.CheckConstraint("factor > 0", name="factor_pos"),
    sa.CheckConstraint("from_unit <> to_unit", name="units_differ"))

# Коэффициент, зафиксированный в решении о сопоставлении (1:1 с записью procurement_need_line_matches). Нет
# строки — единицы совпадали (коэффициент 1). Неизменяемо: правка справочника не меняет прошлых решений.
procurement_need_line_match_units = sa.Table("procurement_need_line_match_units", metadata,
    sa.Column("match_id", ID, sa.ForeignKey("procurement_need_line_matches.id"), primary_key=True),
    sa.Column("line_unit", U(20), nullable=False),
    sa.Column("item_unit", U(20), nullable=False),
    sa.Column("factor", Qty, nullable=False),
    sa.Column("conversion_id", ID, sa.ForeignKey("unit_conversions.id")),
    sa.CheckConstraint("factor > 0", name="factor_pos"))

# Резерв: active → consumed (израсходован выдачами полностью) | released (снят; неизрасходованное вернулось
# в доступное). Срок резерва и автоснятие — не в этом срезе: срок по умолчанию не утверждён (A-23).
stock_reservations = sa.Table("stock_reservations", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("warehouse_id", ID, sa.ForeignKey("warehouses.id"), nullable=False),
    sa.Column("item_id", ID, sa.ForeignKey("stock_items.id"), nullable=False),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("need_id", ID, sa.ForeignKey("procurement_needs.id"), index=True),
    sa.Column("need_line_id", ID),
    sa.Column("qty", Qty, nullable=False),
    sa.Column("consumed", Qty, nullable=False),
    sa.Column("status", U(32), nullable=False),
    sa.Column("reason", U(2000), nullable=False),
    sa.Column("close_reason", U(2000)),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("status IN ('active','consumed','released')", name="status"),
    sa.CheckConstraint("qty > 0", name="qty_pos"),
    sa.CheckConstraint("consumed >= 0 AND consumed <= qty", name="consumed_range"),
    sa.CheckConstraint("(need_id IS NULL AND need_line_id IS NULL) OR (need_id IS NOT NULL AND need_line_id IS NOT NULL)",
                       name="need_pair"))
sa.Index("ix_stock_reservations_balance", stock_reservations.c.warehouse_id, stock_reservations.c.item_id)

# Журнал движений — НЕИЗМЕНЯЕМ (триггеры ниже). Количество всегда > 0, направление — вид движения.
# Сторно — встречное движение со ссылкой reverses_id; у движения не больше одного сторно (уникальный индекс).
stock_movements = sa.Table("stock_movements", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("doc_no", sa.Integer, nullable=False, unique=True),
    sa.Column("kind", U(16), nullable=False),
    sa.Column("warehouse_id", ID, sa.ForeignKey("warehouses.id"), nullable=False),
    sa.Column("item_id", ID, sa.ForeignKey("stock_items.id"), nullable=False),
    sa.Column("qty", Qty, nullable=False),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), index=True),
    sa.Column("reservation_id", ID, sa.ForeignKey("stock_reservations.id")),
    sa.Column("reverses_id", ID, sa.ForeignKey("stock_movements.id")),
    sa.Column("source_doc", U(200)),
    sa.Column("reason", U(2000)),
    sa.Column("posted_by", ID, nullable=False),
    sa.Column("posted_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("kind IN ('receipt','issue')", name="kind"),
    sa.CheckConstraint("qty > 0", name="qty_pos"))
sa.Index("ix_stock_movements_balance", stock_movements.c.warehouse_id, stock_movements.c.item_id)
sa.Index("ux_stock_movements_reverses", stock_movements.c.reverses_id, unique=True,
         postgresql_where=stock_movements.c.reverses_id.isnot(None),
         mssql_where=stock_movements.c.reverses_id.isnot(None),
         sqlite_where=stock_movements.c.reverses_id.isnot(None))

# Перемещение между складами (A-28, схема v8). Одна операция — ДВА движения журнала, проведённые в одной транзакции:
# расход (issue) на складе-отправителе и приход (receipt) на складе-получателе; эта запись связывает их и хранит
# общие реквизиты. Таблица stock_movements не меняется (миграции только добавляют), поэтому свёртка журнала и сверка
# остатков (`ledger_mismatches`) работают без изменений. Запись неизменяема (триггеры ниже); каждое движение входит
# не больше чем в одно перемещение (уникальные ключи); ошибочное перемещение исправляют ОБРАТНЫМ перемещением,
# сторно отдельной половины запрещено (иначе товар «исчезнет» на одном складе и останется на другом).
stock_transfers = sa.Table("stock_transfers", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("item_id", ID, sa.ForeignKey("stock_items.id"), nullable=False),
    sa.Column("from_warehouse_id", ID, sa.ForeignKey("warehouses.id"), nullable=False),
    sa.Column("to_warehouse_id", ID, sa.ForeignKey("warehouses.id"), nullable=False),
    sa.Column("qty", Qty, nullable=False),
    sa.Column("out_movement_id", ID, sa.ForeignKey("stock_movements.id"), nullable=False, unique=True),
    sa.Column("in_movement_id", ID, sa.ForeignKey("stock_movements.id"), nullable=False, unique=True),
    sa.Column("source_doc", U(200)),                 # накладная на перемещение — если есть (необязательно)
    sa.Column("reason", U(2000), nullable=False),    # зачем перемещаем — обязательно
    sa.Column("posted_by", ID, nullable=False),
    sa.Column("posted_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("from_warehouse_id <> to_warehouse_id", name="warehouses_differ"),
    sa.CheckConstraint("out_movement_id <> in_movement_id", name="legs_differ"),
    sa.CheckConstraint("qty > 0", name="qty_pos"))

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

# Неизменяемость версий документов — тем же способом, что и журнал аудита.
_PG_DV_FN = sa.DDL("""
CREATE OR REPLACE FUNCTION vector_document_versions_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'document_versions are immutable'; END; $$ LANGUAGE plpgsql""")
_PG_DV_TRG = sa.DDL("""CREATE TRIGGER document_versions_immutable BEFORE UPDATE OR DELETE ON document_versions
FOR EACH ROW EXECUTE FUNCTION vector_document_versions_immutable()""")
_MS_DV_TRG = sa.DDL("""CREATE TRIGGER document_versions_immutable ON document_versions INSTEAD OF UPDATE, DELETE AS
BEGIN THROW 50002, 'document_versions are immutable', 1; END""")
_LITE_DV_U = sa.DDL("CREATE TRIGGER document_versions_no_update BEFORE UPDATE ON document_versions BEGIN SELECT RAISE(ABORT, 'document_versions are immutable'); END")
_LITE_DV_D = sa.DDL("CREATE TRIGGER document_versions_no_delete BEFORE DELETE ON document_versions BEGIN SELECT RAISE(ABORT, 'document_versions are immutable'); END")
for ddl in (_PG_DV_FN, _PG_DV_TRG):
    event.listen(document_versions, "after_create", ddl.execute_if(dialect="postgresql"))
event.listen(document_versions, "after_create", _MS_DV_TRG.execute_if(dialect="mssql"))
for ddl in (_LITE_DV_U, _LITE_DV_D):
    event.listen(document_versions, "after_create", ddl.execute_if(dialect="sqlite"))



def _immutable(table: sa.Table, ms_error: int) -> None:
    """Запретить UPDATE/DELETE строк таблицы на всех СУБД (как у журнала аудита и версий документов).

    Это защита от ошибок программы и случайных правок, а не от администратора СУБД (05-architecture §7).
    Вставка разрешена — её делают обработчики команд и восстановление из резервной копии.
    """
    n = table.name
    event.listen(table, "after_create", sa.DDL(f"""
CREATE OR REPLACE FUNCTION vector_{n}_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION '{n} are immutable'; END; $$ LANGUAGE plpgsql""").execute_if(dialect="postgresql"))
    event.listen(table, "after_create", sa.DDL(f"""CREATE TRIGGER {n}_immutable BEFORE UPDATE OR DELETE ON {n}
FOR EACH ROW EXECUTE FUNCTION vector_{n}_immutable()""").execute_if(dialect="postgresql"))
    event.listen(table, "after_create", sa.DDL(f"""CREATE TRIGGER {n}_immutable ON {n} INSTEAD OF UPDATE, DELETE AS
BEGIN THROW {ms_error}, '{n} are immutable', 1; END""").execute_if(dialect="mssql"))
    for op in ("UPDATE", "DELETE"):
        event.listen(table, "after_create", sa.DDL(
            f"CREATE TRIGGER {n}_no_{op.lower()} BEFORE {op} ON {n} BEGIN SELECT RAISE(ABORT, '{n} are immutable'); END"
        ).execute_if(dialect="sqlite"))


for _t, _err in ((calc_versions, 50003), (calc_version_lines, 50004), (project_calc_revisions, 50005),
                 (project_calc_revision_items, 50006), (stock_movements, 50007), (procurement_need_lines, 50008),
                 (procurement_need_line_matches, 50009), (config_versions, 50010),
                 (procurement_need_line_match_units, 50011), (stock_transfers, 50012)):
    _immutable(_t, _err)

for ddl in (_PG_FN, _PG_TRG, _PG_TRUNC):
    event.listen(audit_log, "after_create", ddl.execute_if(dialect="postgresql"))
event.listen(audit_log, "after_create", _MS_TRG.execute_if(dialect="mssql"))
for ddl in (_LITE_U, _LITE_D):
    event.listen(audit_log, "after_create", ddl.execute_if(dialect="sqlite"))


# ---------------------------------------------------------------- engine
# Поддерживаемые СУБД. Рабочие: PostgreSQL и MS SQL (решение владельца); SQLite — только тесты и пилот на одном ПК.
SUPPORTED = ("postgresql", "mssql", "sqlite")


def make_engine(url: str, **kw) -> Engine:
    """Создать подключение SQLAlchemy к поддерживаемой СУБД.

    PostgreSQL и MS SQL — уровень изоляции SERIALIZABLE (конфликты повторяются в Service._tx) и проверка соединения
    перед использованием. SQLite (тесты и пилот) — внешние ключи, WAL, ожидание блокировки 30 с и BEGIN IMMEDIATE,
    чтобы записи не перемежались.
    """
    u = sa.engine.make_url(url)
    backend = u.get_backend_name()
    if backend not in SUPPORTED:
        raise ValueError(f"СУБД «{backend}» не поддерживается. Поддерживаются: PostgreSQL, MS SQL, SQLite (только тесты/пилот).")
    if backend == "sqlite":
        eng = sa.create_engine(url, connect_args={"timeout": 30, "check_same_thread": False}, **kw)

        @event.listens_for(eng, "connect")
        def _on_connect(dbapi_conn, _rec):
            """Настройки каждого нового соединения SQLite (PRAGMA)."""
            dbapi_conn.isolation_level = None  # управляем транзакциями сами
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        @event.listens_for(eng, "begin")
        def _on_begin(conn):
            """Каждая транзакция SQLite сразу берёт блокировку записи — так поведение ближе к SERIALIZABLE."""
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
    """Создать схему в ПУСТОЙ БД и записать версию схемы, идентификатор установки и время создания. Непустая БД — отказ
    (защита от перезаписи данных).
    """
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
    """Проверить БД перед запуском сервера: схема есть, восстановление не прервано, версия схемы совпадает с
    программой. Иначе — отказ запуска (fail-closed) с подсказкой, что делать.
    """
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
                           "Запуск остановлен (fail-closed). Если БД старее программы — выполните "
                           "«vector-server migrate --config …» (сначала будет сделана проверенная копия).")
    return int(v)
