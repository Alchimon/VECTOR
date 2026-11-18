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
#   v8 — перемещения между складами: связка двух движений журнала (A-28);
#   v9 — перемещение со статусом «в пути»: отгрузка → приёмка получателем или возврат отправителю (D-29, A-29);
#   v10–v13 — акты, подписи, поставщики и заказы, согласование и НДС (D-30…D-33, см. migrate.py);
#   v14 — замещение (передача роли на время), основания решений, сверка заказа со счётом поставщика (D-34);
#   v15 — замещение роли в проекте и уведомления (D-35);
#   v16 — технология и производство: ТП, наряды, неизменяемый снимок, исполнение операций, замены, выдача
#         материалов на наряд (этап 5, D-36);
#   v17 — экземпляры изделий, их состав и история эксплуатации, паспорт и журнал черновых экспортов (этап 6, D-38);
#   v18 — качество: рекламации, действия CAPA, проверки эффективности, запросы (ECR) и извещения (ECO) об изменении,
#         журнал решений по качеству (этап 6, D-39);
#   v19 — рекламация без заводского номера (на партию или проект: claims.instance_id допускает NULL), охват и
#         классификация дефекта, срок ответа заказчику, неизменяемые ответы заказчику (D-40, ответ на A-46).
#   v20 — рабочее время (D-42): дневные отчёты сотрудников (смена, строки «на что ушло время»), журнал шагов отчёта
#         (неизменяемый) — таблицы daily_reports, daily_report_lines, daily_report_events.
SCHEMA_VERSION = 20

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

# Перемещение со статусом «в пути» (D-29, A-29, схема v9). Решение владельца: между отгрузкой и приёмкой товар
# числится «в пути» — его уже нет на складе-отправителе и ещё нет на складе-получателе.
#
#   stock.transfer          ─▶ in_transit  (расход на отправителе: out_movement_id)
#   stock.transfer_accept   ─▶ received    (приход на получателе: close_movement_id)
#   stock.transfer_cancel   ─▶ returned    (приход обратно на отправителе: close_movement_id)
#
# В отличие от stock_transfers (v8, одношаговые перемещения — остаются только как история), запись меняет статус,
# поэтому она НЕ неизменяема: смена статуса идёт по version (две приёмки/приёмка и возврат одновременно — пройдёт
# одна), а сами движения журнала по-прежнему неизменяемы, и каждая смена статуса записана в журнал аудита.
# Количество «в пути» = сумма qty перемещений в статусе in_transit; на остатки складов оно не влияет.
stock_shipments = sa.Table("stock_shipments", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("item_id", ID, sa.ForeignKey("stock_items.id"), nullable=False),
    sa.Column("from_warehouse_id", ID, sa.ForeignKey("warehouses.id"), nullable=False),
    sa.Column("to_warehouse_id", ID, sa.ForeignKey("warehouses.id"), nullable=False),
    sa.Column("qty", Qty, nullable=False),
    sa.Column("status", U(16), nullable=False),
    sa.Column("out_movement_id", ID, sa.ForeignKey("stock_movements.id"), nullable=False, unique=True),
    sa.Column("close_movement_id", ID, sa.ForeignKey("stock_movements.id")),   # приход при приёмке или возврате
    sa.Column("source_doc", U(200)),                 # накладная на перемещение — если оформляется
    sa.Column("reason", U(2000), nullable=False),    # зачем перемещаем
    sa.Column("close_reason", U(2000)),              # комментарий приёмки / причина возврата
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("closed_by", ID), sa.Column("closed_at", UtcDateTime),
    sa.CheckConstraint("status IN ('in_transit','received','returned')", name="status"),
    sa.CheckConstraint("from_warehouse_id <> to_warehouse_id", name="warehouses_differ"),
    sa.CheckConstraint("qty > 0", name="qty_pos"),
    # «В пути» — без закрывающего движения; принято/возвращено — с ним, временем и автором закрытия.
    sa.CheckConstraint("(status = 'in_transit' AND close_movement_id IS NULL AND closed_at IS NULL) OR "
                       "(status <> 'in_transit' AND close_movement_id IS NOT NULL AND closed_at IS NOT NULL "
                       "AND closed_by IS NOT NULL)", name="close_consistent"))
sa.Index("ix_stock_shipments_status", stock_shipments.c.status)
# Закрывающее движение — у одного перемещения. Частичный уникальный индекс: MS SQL в обычном UNIQUE допускает только
# один NULL, а «в пути» таких строк много.
sa.Index("ux_stock_shipments_close", stock_shipments.c.close_movement_id, unique=True,
         postgresql_where=stock_shipments.c.close_movement_id.isnot(None),
         mssql_where=stock_shipments.c.close_movement_id.isnot(None),
         sqlite_where=stock_shipments.c.close_movement_id.isnot(None))

# Акт недостачи при приёмке перемещения (D-30, схема v10). Решение владельца: «такой акт нужен, подписывает
# приёмщик склада». Если на склад-получатель пришло меньше отгруженного, приёмщик принимает фактическое количество
# (приход = accepted_qty), а разницу оформляет актом: номер, отгружено / принято / недостача, причина, кто и когда
# подписал (имя — снимок на момент подписи: акт не должен «переименовываться» вместе с учётной записью).
# Недостача не возвращается ни на один склад — она списана из учёта этим актом; кто за неё отвечает и как она
# отражается в деньгах, программа не решает (A-30). Акт неизменяем: исправление — только новым документом.
# Одно перемещение — не больше одного акта; shipped = accepted + shortage проверяет сама БД.
stock_transfer_acts = sa.Table("stock_transfer_acts", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("shipment_id", ID, sa.ForeignKey("stock_shipments.id"), nullable=False, unique=True),
    sa.Column("act_no", sa.Integer, nullable=False, unique=True),     # сквозной номер акта (1, 2, 3 …)
    sa.Column("shipped_qty", Qty, nullable=False),
    sa.Column("accepted_qty", Qty, nullable=False),
    sa.Column("shortage_qty", Qty, nullable=False),
    sa.Column("reason", U(2000), nullable=False),                     # что случилось — обязательно
    sa.Column("signed_by", ID, nullable=False),                       # приёмщик склада, подписавший акт
    sa.Column("signer_name", U(200), nullable=False),
    sa.Column("signed_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("accepted_qty > 0", name="accepted_pos"),
    sa.CheckConstraint("shortage_qty > 0", name="shortage_pos"),
    sa.CheckConstraint("shipped_qty = accepted_qty + shortage_qty", name="qty_balance"))

# Ожидаемая дата прибытия перемещения (D-30, схема v10) — «для справки» по решению владельца: указывается при
# отгрузке (по желанию), показывается в списке «В пути» с пометкой, если дата прошла, и НИ НА ЧТО не влияет
# (никаких автоматических действий, напоминаний и запретов). Отдельная таблица, потому что миграции только
# добавляют таблицы и не меняют существующие (принцип 3 migrate.py). Неизменяема — дата отгрузки не переписывается.
stock_shipment_terms = sa.Table("stock_shipment_terms", metadata,
    sa.Column("shipment_id", ID, sa.ForeignKey("stock_shipments.id"), primary_key=True),
    sa.Column("expected_on", sa.Date, nullable=False))

# Акт об утрате перемещения в пути (D-31, схема v11): не пришло НИЧЕГО. Перемещение закрывается без прихода —
# количество, уже списанное у отправителя, списывается из учёта этим актом. Отдельная таблица, потому что
# stock_shipments (v9) не допускает закрытия без движения (CHECK close_consistent), а миграции только добавляют
# таблицы: у утраченного перемещения в stock_shipments остаётся status = 'in_transit' (без закрывающего движения),
# а действующий статус «Утрачено в пути» определяется наличием строки здесь (сервер учитывает это везде: приёмка,
# возврат, сторно, список «в пути»). Нашёлся груз — обычный приход с основанием «найдено по акту об утрате № N».
# Номера актов об утрате — своя сквозная нумерация. Неизменяема.
stock_shipment_losses = sa.Table("stock_shipment_losses", metadata,
    sa.Column("shipment_id", ID, sa.ForeignKey("stock_shipments.id"), primary_key=True),
    sa.Column("act_no", sa.Integer, nullable=False, unique=True),
    sa.Column("qty", Qty, nullable=False),
    sa.Column("reason", U(2000), nullable=False),
    sa.Column("signed_by", ID, nullable=False),
    sa.Column("signer_name", U(200), nullable=False),
    sa.Column("signed_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("qty > 0", name="qty_pos"))

# Подписи документов (D-31, схема v11) — общая таблица для актов сейчас и согласований позже. Как подписано, решает
# настройка компании stock.act_signature: confirm — подтверждение вошедшим пользователем; password — с повторным
# вводом пароля; qes — квалифицированная электронная подпись (ЭЦП/КЭП; пока не подключена — A-32, публикация этого
# значения отклоняется). content_sha256 — SHA-256 канонического содержимого документа в момент подписи: по нему видно,
# что подписанное не изменилось. Для КЭП позже добавится отдельная таблица с сертификатом и самой подписью (CAdES),
# ссылающаяся на эту строку. Один документ — одна подпись данного назначения. Неизменяема.
signatures = sa.Table("signatures", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("object_type", U(40), nullable=False),      # stock_transfer_act, stock_loss_act …
    sa.Column("object_id", ID, nullable=False),
    sa.Column("purpose", U(40), nullable=False),          # act — подпись акта (приёмщик)
    sa.Column("method", U(16), nullable=False),
    sa.Column("signer_id", ID, nullable=False),
    sa.Column("signer_name", U(200), nullable=False),
    sa.Column("signed_at", UtcDateTime, nullable=False),
    sa.Column("content_sha256", sa.String(64), nullable=False),
    sa.CheckConstraint("method IN ('confirm','password','qes')", name="method"),
    sa.UniqueConstraint("object_type", "object_id", "purpose", name="one_signature"))

# ---------------------------------------------------------------- v12: заказы поставщикам (С4 шаг 5, D-32)
# Поставщики — справочник закупки (как номенклатура: правится офлайн через очередь, код уникален, отключение вместо
# удаления). ИНН необязателен; если указан — 10 цифр (организация) или 12 (ИП), контрольные цифры проверяются.
suppliers = sa.Table("suppliers", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("code", U(64), nullable=False, unique=True),
    sa.Column("name", U(300), nullable=False),
    sa.Column("inn", sa.String(12)),
    sa.Column("contact", U(500)),
    sa.Column("note", U(1000)),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False))

# Заказ поставщику. Статусы: draft (черновик — правится, можно офлайн) → placed (размещён у поставщика: строки
# заморожены, их SHA-256 — в placed_hash) → closed (закрыт: больше не ждём) или cancelled (отменён: только пока по
# заказу ничего не принято). «Поставлено частично / полностью» — не статус, а расчёт по фактам поставки
# (purchase_receipts), поэтому сторно прихода честно возвращает заказ к «ждём». Номер — сквозной (order_no).
# Валюта — одна на заказ (ISO 4217), обязательна, если указана хоть одна цена; курсов и НДС программа не считает.
purchase_orders = sa.Table("purchase_orders", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("order_no", sa.Integer, nullable=False, unique=True),
    sa.Column("supplier_id", ID, sa.ForeignKey("suppliers.id"), nullable=False, index=True),
    sa.Column("status", U(16), nullable=False),
    sa.Column("currency", sa.String(3)),
    sa.Column("supplier_ref", U(200)),          # номер счёта / подтверждения поставщика
    sa.Column("expected_on", sa.Date),          # ожидаемая дата поставки — для справки
    sa.Column("note", U(2000)),
    sa.Column("placed_hash", sa.String(64)),
    sa.Column("placed_by", ID), sa.Column("placed_at", UtcDateTime),
    sa.Column("closed_by", ID), sa.Column("closed_at", UtcDateTime),
    sa.Column("close_reason", U(2000)),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("status IN ('draft','placed','closed','cancelled')", name="status"),
    sa.CheckConstraint("(status = 'draft' AND placed_hash IS NULL) OR (status = 'cancelled') OR "
                       "(status IN ('placed','closed') AND placed_hash IS NOT NULL AND placed_at IS NOT NULL)",
                       name="placed_consistent"))

# Строки заказа: позиция номенклатуры, количество в её единице, цена за единицу (необязательна: неизвестна ≠ 0),
# ссылка на строку потребности (необязательна). Пока заказ — черновик, строки заменяются целиком при сохранении;
# у размещённого сервер их не меняет (проверка по placed_hash).
purchase_order_lines = sa.Table("purchase_order_lines", metadata,
    sa.Column("order_id", ID, sa.ForeignKey("purchase_orders.id"), primary_key=True),
    sa.Column("pos", sa.Integer, primary_key=True),
    sa.Column("line_id", ID, nullable=False),
    sa.Column("item_id", ID, sa.ForeignKey("stock_items.id"), nullable=False),
    sa.Column("qty", Qty, nullable=False),
    sa.Column("unit_price", Money),
    sa.Column("need_id", ID, sa.ForeignKey("procurement_needs.id")),
    sa.Column("need_line_id", ID),
    sa.Column("note", U(1000)),
    sa.UniqueConstraint("order_id", "line_id", name="uq_purchase_order_lines_line"),
    sa.CheckConstraint("qty > 0", name="qty_pos"),
    sa.CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="price_nonneg"),
    sa.CheckConstraint("(need_id IS NULL AND need_line_id IS NULL) OR (need_id IS NOT NULL AND need_line_id IS NOT NULL)",
                       name="need_pair"))

# Факт поставки: приход на склад (stock_movements, kind = receipt) по строке заказа. Сколько поставлено по строке —
# сумма таких приходов БЕЗ сторнированных (сторно прихода штатное, stock.reverse). Неизменяема; одно движение — не
# больше одной строки заказа.
purchase_receipts = sa.Table("purchase_receipts", metadata,
    sa.Column("movement_id", ID, sa.ForeignKey("stock_movements.id"), primary_key=True),
    sa.Column("order_id", ID, sa.ForeignKey("purchase_orders.id"), nullable=False, index=True),
    sa.Column("line_id", ID, nullable=False),
    sa.Column("qty", Qty, nullable=False),
    sa.CheckConstraint("qty > 0", name="qty_pos"))

# ---------------------------------------------------------------- v13: реквизиты, договоры, НДС, согласование (D-33)
# Миграции добавляют только новые таблицы (migrate.py, принцип 3), поэтому дополнения к поставщику и заказу —
# таблицы-расширения «один к одному», а не новые столбцы: старые строки остаются как были, у новых полей нет
# значения по умолчанию, которое могло бы что-то «придумать».

# Реквизиты поставщика (A-39): все необязательны; формат и контрольные цифры проверяет vector.core.purchasing.
# vat_rate — ставка НДС, которую редактор подставляет в новые строки заказов этому поставщику (только подсказка).
supplier_requisites = sa.Table("supplier_requisites", metadata,
    sa.Column("supplier_id", ID, sa.ForeignKey("suppliers.id"), primary_key=True),
    sa.Column("kpp", sa.String(9)),
    sa.Column("ogrn", sa.String(15)),
    sa.Column("address", U(500)),
    sa.Column("bank_name", U(300)),
    sa.Column("bik", sa.String(9)),
    sa.Column("bank_account", sa.String(20)),
    sa.Column("corr_account", sa.String(20)),
    sa.Column("vat_rate", sa.String(8)))

# Договоры с поставщиком: номер (уникален у поставщика), дата заключения, срок действия (необязателен — бессрочный),
# предмет. Правятся как справочник (офлайн через очередь); не удаляются, а отключаются. Заказ может ссылаться на
# договор своего поставщика — при согласовании и размещении договор должен действовать на эту дату.
supplier_contracts = sa.Table("supplier_contracts", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("supplier_id", ID, sa.ForeignKey("suppliers.id"), nullable=False, index=True),
    sa.Column("number", U(100), nullable=False),
    sa.Column("signed_on", sa.Date, nullable=False),
    sa.Column("valid_until", sa.Date),
    sa.Column("subject", U(500)),
    sa.Column("note", U(1000)),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("supplier_id", "number", name="uq_supplier_contracts_number"),
    sa.CheckConstraint("valid_until IS NULL OR valid_until >= signed_on", name="valid_range"))

# Условия заказа сверх v12: договор и режим цен (с НДС / без НДС). Строки нет — договор не указан, режим не указан.
purchase_order_terms = sa.Table("purchase_order_terms", metadata,
    sa.Column("order_id", ID, sa.ForeignKey("purchase_orders.id"), primary_key=True),
    sa.Column("contract_id", ID, sa.ForeignKey("supplier_contracts.id")),
    sa.Column("vat_mode", sa.String(8)),
    sa.CheckConstraint("vat_mode IS NULL OR vat_mode IN ('included','excluded')", name="vat_mode"))

# Ставка НДС строки заказа (код из vector.core.purchasing.VAT_RATES). Строки черновика заменяются целиком — вместе
# с ними и эти. Нет строки — ставка не указана (НДС «не указан», а не 0).
purchase_order_line_taxes = sa.Table("purchase_order_line_taxes", metadata,
    sa.Column("order_id", ID, sa.ForeignKey("purchase_orders.id"), primary_key=True),
    sa.Column("line_id", ID, primary_key=True),
    sa.Column("vat_rate", sa.String(8), nullable=False))

# Согласование заказа (D-33, A-37): неизменяемый журнал событий по черновику — submit (отправлен на согласование,
# с SHA-256 содержания), approve (согласован — то же содержание), return (возвращён на доработку, причина),
# withdraw (отозван закупкой). Текущее состояние — последнее событие; разместить можно только согласованное
# содержание (хэш последнего approve = хэш заказа сейчас). seq — порядковый номер события в заказе.
purchase_order_approvals = sa.Table("purchase_order_approvals", metadata,
    sa.Column("order_id", ID, sa.ForeignKey("purchase_orders.id"), primary_key=True),
    sa.Column("seq", sa.Integer, primary_key=True),
    sa.Column("kind", sa.String(8), nullable=False),
    sa.Column("content_hash", sa.String(64)),
    sa.Column("comment", U(2000)),
    sa.Column("actor_id", ID, nullable=False),
    sa.Column("at", UtcDateTime, nullable=False),
    sa.CheckConstraint("kind IN ('submit','approve','return','withdraw')", name="kind"),
    sa.CheckConstraint("seq > 0", name="seq_pos"),
    sa.CheckConstraint("kind NOT IN ('submit','approve') OR content_hash IS NOT NULL", name="hash_when_needed"))

# ------------------------------------------------------------ D-34 (схема v14): замещение и контроль расчёта
# Замещение (ответ владельца на A-37, правила — vector.core.delegation): носитель глобальной роли (или руководитель
# отдела / администратор от его имени) передаёт роль другому сотруднику на срок с причиной. Даты календарные,
# включительно. Отмена — revoked_* (строка остаётся в истории). Действует, только пока у носителя есть своя роль.
role_delegations = sa.Table("role_delegations", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("from_user_id", ID, sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("to_user_id", ID, sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("role", U(32), nullable=False),
    sa.Column("valid_from", sa.Date, nullable=False),
    sa.Column("valid_until", sa.Date, nullable=False),
    sa.Column("reason", U(500), nullable=False),
    sa.Column("created_by", ID, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("revoked_at", UtcDateTime),
    sa.Column("revoked_by", ID),
    sa.Column("revoke_reason", U(500)),
    sa.CheckConstraint("valid_until >= valid_from", name="period"),
    sa.CheckConstraint("from_user_id <> to_user_id", name="not_self"),
    sa.CheckConstraint("role <> 'admin'", name="not_admin"))

# Основание решений по согласованию (неизменяемо): чьим правом принято решение — своя роль, роль руководителя
# отдела или замещение (тогда on_behalf_of — носитель, delegation_id — замещение, grant_id — выдача роли носителя).
# object_type: purchase_order (object_id = id заказа, seq = номер события согласования) или tkp_approval
# (object_id = id согласования ТКП, seq = 1). authority — та же строка, что в журнале аудита.
approval_bases = sa.Table("approval_bases", metadata,
    sa.Column("object_type", U(32), primary_key=True),
    sa.Column("object_id", U(64), primary_key=True),
    sa.Column("seq", sa.Integer, primary_key=True),
    sa.Column("actor_id", ID, nullable=False),
    sa.Column("role", U(32), nullable=False),
    sa.Column("authority", U(200), nullable=False),
    sa.Column("grant_id", sa.BigInteger),
    sa.Column("delegation_id", sa.BigInteger),
    sa.Column("on_behalf_of", ID),
    sa.Column("at", UtcDateTime, nullable=False))

# Суммы по счёту поставщика для сверки с расчётом (A-38 → D-34). Черновик: правится вместе с заказом (входит в
# согласуемое содержание). NULL — не введена. Только целые копейки, ≥ 0.
purchase_order_invoices = sa.Table("purchase_order_invoices", metadata,
    sa.Column("order_id", ID, sa.ForeignKey("purchase_orders.id"), primary_key=True),
    sa.Column("invoice_gross", Money),
    sa.Column("invoice_vat", Money),
    sa.CheckConstraint("invoice_gross IS NULL OR invoice_gross >= 0", name="gross_nonneg"),
    sa.CheckConstraint("invoice_vat IS NULL OR invoice_vat >= 0", name="vat_nonneg"))

# Результат сверки со счётом в момент отправки на согласование (неизменяемо): state — none / match / mismatch /
# unknown, разницы «счёт − расчёт» и причина, без которой расхождение отправить нельзя.
purchase_order_invoice_checks = sa.Table("purchase_order_invoice_checks", metadata,
    sa.Column("order_id", ID, sa.ForeignKey("purchase_orders.id"), primary_key=True),
    sa.Column("seq", sa.Integer, primary_key=True),
    sa.Column("state", sa.String(8), nullable=False),
    sa.Column("diff_gross", Money),
    sa.Column("diff_vat", Money),
    sa.Column("reason", U(2000)),
    sa.CheckConstraint("state IN ('none','match','mismatch','unknown')", name="state"),
    sa.CheckConstraint("state NOT IN ('mismatch','unknown') OR reason IS NOT NULL", name="reason_when_needed"))

# Замещение роли В ПРОЕКТЕ (A-40 → D-35): расширение role_delegations. Есть строка — role_delegations.role это роль в
# проекте (gip / rp / participant) и действует только в project_id; нет строки — глобальная роль (D-34). Неизменяемо:
# проект замещения не меняется (нужен другой проект — новое замещение).
role_delegation_projects = sa.Table("role_delegation_projects", metadata,
    sa.Column("delegation_id", BIGID, sa.ForeignKey("role_delegations.id"), primary_key=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True))

# Уведомления в программе (A-40 → D-35): кому, о чём (kind, заголовок, текст), по какому объекту, кто вызвал.
# Создаются в той же транзакции, что и событие (отказ команды — уведомления нет). Меняется только read_at
# (прочитано); удаления нет — уведомления старше срока просто не приходят в pull.
notifications = sa.Table("notifications", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("user_id", ID, sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("kind", U(32), nullable=False),
    sa.Column("title", U(200), nullable=False),
    sa.Column("body", U(1000)),
    sa.Column("object_type", U(32)),
    sa.Column("object_id", U(64)),
    sa.Column("actor_id", ID),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("read_at", UtcDateTime))

# ------------------------------------------------------------ D-36 (схема v16): технология и производство
# ТП — технологический процесс изделия (комплекта): ревизии 1, 2, … по комплекту; черновик правится технологом
# (в т. ч. офлайн через очередь), утверждённая ревизия не меняется (новая ревизия — новый черновик). Спецификация
# ТП — ссылка на ЗАФИКСИРОВАННУЮ версию расчёта комплекта (calc_versions): она неизменяема, значит состав
# изделия, по которому утверждён ТП, не «поплывёт».
tech_processes = sa.Table("tech_processes", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("kit_id", ID, sa.ForeignKey("kits.id"), nullable=False, index=True),
    sa.Column("revision_no", sa.Integer, nullable=False),
    sa.Column("title", U(300), nullable=False),
    sa.Column("spec_version_id", BIGID, sa.ForeignKey("calc_versions.id")),
    sa.Column("note", U(2000)),
    sa.Column("status", U(16), nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("kit_id", "revision_no", name="uq_tech_processes_rev"),
    sa.CheckConstraint("status IN ('draft','approved')", name="status"),
    sa.CheckConstraint("revision_no > 0", name="rev_pos"))

# Операции ТП: заменяются целиком при сохранении черновика; у утверждённого ТП не меняются (проверка сервиса).
tech_process_ops = sa.Table("tech_process_ops", metadata,
    sa.Column("tp_id", ID, sa.ForeignKey("tech_processes.id"), primary_key=True),
    sa.Column("op_no", sa.Integer, primary_key=True),
    sa.Column("name", U(200), nullable=False),
    sa.Column("work_center", U(200)),
    sa.Column("norm_min", Qty, nullable=False),
    sa.Column("note", U(1000)),
    sa.CheckConstraint("norm_min >= 0", name="norm_nonneg"))

# Утверждение ТП (неизменяемо): кто, на каком основании, какое именно содержимое (SHA-256). Автор не утверждает.
tech_process_approvals = sa.Table("tech_process_approvals", metadata,
    sa.Column("tp_id", ID, sa.ForeignKey("tech_processes.id"), primary_key=True),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("approved_by", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("authority", U(120), nullable=False),
    sa.Column("approved_at", UtcDateTime, nullable=False))

# Наряд (производственное задание) на qty изделий по утверждённому ТП. Жизненный цикл (03 §4):
# released → in_progress → completed → closed; cancelled с причиной. Выпуск = создание + снимок.
work_orders = sa.Table("work_orders", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("doc_no", sa.Integer, nullable=False, unique=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("kit_id", ID, sa.ForeignKey("kits.id"), nullable=False),
    sa.Column("tp_id", ID, sa.ForeignKey("tech_processes.id"), nullable=False),
    sa.Column("qty", sa.Integer, nullable=False),
    sa.Column("title", U(300), nullable=False),
    sa.Column("note", U(2000)),
    sa.Column("status", U(16), nullable=False),
    sa.Column("snapshot_hash", sa.String(64), nullable=False),
    sa.Column("cancel_reason", U(2000)),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("released_by", ID, nullable=False), sa.Column("released_at", UtcDateTime, nullable=False),
    sa.Column("closed_by", ID), sa.Column("closed_at", UtcDateTime),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("status IN ('draft','released','in_progress','completed','closed','cancelled')", name="status"),
    sa.CheckConstraint("qty > 0", name="qty_pos"),
    sa.CheckConstraint("status <> 'cancelled' OR cancel_reason IS NOT NULL", name="cancel_reason"))

# Снимок наряда (неизменяемо, триггеры ниже): состав без цен + операции ТП на момент выпуска, с SHA-256.
work_order_snapshots = sa.Table("work_order_snapshots", metadata,
    sa.Column("work_order_id", ID, sa.ForeignKey("work_orders.id"), primary_key=True),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("snapshot_json", T, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False))

# Операция наряда — исполнение одной операции ТП: исполнитель, состояние, таймер (по времени устройства), факт.
wo_operations = sa.Table("wo_operations", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("work_order_id", ID, sa.ForeignKey("work_orders.id"), nullable=False, index=True),
    sa.Column("op_no", sa.Integer, nullable=False),
    sa.Column("name", U(200), nullable=False),
    sa.Column("work_center", U(200)),
    sa.Column("norm_min", Qty, nullable=False),
    sa.Column("assignee_id", ID, sa.ForeignKey("users.id"), index=True),
    sa.Column("state", U(16), nullable=False),
    sa.Column("qty_done", sa.Integer),
    sa.Column("fact_seconds", sa.Integer, nullable=False),
    sa.Column("started_at", UtcDateTime),
    sa.Column("finished_at", UtcDateTime),
    sa.Column("running_since", UtcDateTime),
    sa.Column("last_device_time", UtcDateTime),
    sa.Column("problem_count", sa.Integer, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("work_order_id", "op_no", name="uq_wo_operations_no"),
    sa.CheckConstraint("state IN ('pending','running','paused','done')", name="state"),
    sa.CheckConstraint("fact_seconds >= 0", name="fact_nonneg"))

# События исполнителя (неизменяемо): что, когда по часам устройства и по часам сервера, с какого устройства и под
# каким operation_id очереди — по ним восстанавливается таймер и разбирается любой спор.
wo_timer_events = sa.Table("wo_timer_events", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("wo_operation_id", ID, sa.ForeignKey("wo_operations.id"), nullable=False, index=True),
    sa.Column("kind", U(16), nullable=False),
    sa.Column("device_time", UtcDateTime, nullable=False),
    sa.Column("server_time", UtcDateTime, nullable=False),
    sa.Column("user_id", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("device_id", U(100)),
    sa.Column("operation_id", ID, nullable=False, unique=True),
    sa.Column("qty_done", sa.Integer),
    sa.Column("note", U(2000)),
    sa.CheckConstraint("kind IN ('start','pause','resume','finish','problem')", name="kind"))

# Замена в снимке (неизменяемо): снимок не переписывается — действующий состав = снимок + замены по порядку.
# Хранится: какая строка, было/стало, причина, хэш предпросмотра (состав, который видел мастер) и результата.
wo_substitutions = sa.Table("wo_substitutions", metadata,
    sa.Column("work_order_id", ID, sa.ForeignKey("work_orders.id"), primary_key=True),
    sa.Column("seq", sa.Integer, primary_key=True),
    sa.Column("line_id", U(64), nullable=False),
    sa.Column("old_json", T, nullable=False),
    sa.Column("new_json", T, nullable=False),
    sa.Column("reason", U(2000), nullable=False),
    sa.Column("preview_hash", sa.String(64), nullable=False),
    sa.Column("result_hash", sa.String(64), nullable=False),
    sa.Column("authority", U(120), nullable=False),
    sa.Column("created_by", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("seq > 0", name="seq_pos"))

# Выдача материалов на наряд: связь движения журнала склада (выдача) с нарядом. stock_movements не меняется
# (миграции только добавляют) — поэтому отдельная неизменяемая таблица, как у перемещений.
work_order_issues = sa.Table("work_order_issues", metadata,
    sa.Column("movement_id", ID, sa.ForeignKey("stock_movements.id"), primary_key=True),
    sa.Column("work_order_id", ID, sa.ForeignKey("work_orders.id"), nullable=False, index=True))

# ------------------------------------------------------------ D-38 (схема v17): экземпляры изделий и паспорт
# Экземпляр изделия — результат ЗАКРЫТОГО наряда: изделие (комплект проекта) + серия + заводской номер. Номер
# уникален в области «изделие + серия» (02 С5 шаг 7), не глобально. Статус: registered (изготовлен) →
# handed_over (передан заказчику). Состав экземпляра фиксируется при регистрации (instance_compositions).
product_instances = sa.Table("product_instances", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("kit_id", ID, sa.ForeignKey("kits.id"), nullable=False),
    sa.Column("work_order_id", ID, sa.ForeignKey("work_orders.id"), nullable=False, index=True),
    sa.Column("series", U(40), nullable=False),
    sa.Column("serial_no", U(60), nullable=False),
    sa.Column("serial_key", U(60), nullable=False),      # номер без учёта регистра — для уникальности
    sa.Column("status", U(16), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("kit_id", "series", "serial_key", name="uq_product_instances_serial"),
    sa.CheckConstraint("status IN ('registered','handed_over')", name="status"))

# Состав экземпляра на момент регистрации (неизменяемо): ссылки на проект, наряд и его снимок (SHA-256), ТП,
# версию расчёта, состав одного изделия с заменами по наряду. Отсюда паспорт берёт «точные связи».
instance_compositions = sa.Table("instance_compositions", metadata,
    sa.Column("instance_id", ID, sa.ForeignKey("product_instances.id"), primary_key=True),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("composition_json", T, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False))

# История эксплуатации (неизменяемо): передача заказчику, сервисные события, замены. Замена не переписывает
# состав — действующий состав = состав при регистрации + замены по порядку (как замены в снимке наряда).
instance_events = sa.Table("instance_events", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("instance_id", ID, sa.ForeignKey("product_instances.id"), nullable=False, index=True),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("kind", U(16), nullable=False),
    sa.Column("event_date", sa.Date, nullable=False),
    sa.Column("details_json", T, nullable=False),
    sa.Column("authority", U(120), nullable=False),
    sa.Column("created_by", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("instance_id", "seq", name="uq_instance_events_seq"),
    sa.CheckConstraint("kind IN ('handover','service','replacement')", name="kind"))

# Паспорт экземпляра (1─1): черновик по шаблону-мастеру (код, версия и SHA-256 шаблона хранятся — новая версия
# шаблона не меняет составленные паспорта). Правится и офлайн через очередь; оформления «чистовика» в D-38 нет.
passports = sa.Table("passports", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("instance_id", ID, sa.ForeignKey("product_instances.id"), nullable=False, unique=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("template_code", U(32), nullable=False),
    sa.Column("template_version", sa.Integer, nullable=False),
    sa.Column("template_hash", sa.String(64), nullable=False),
    sa.Column("status", U(16), nullable=False),
    sa.Column("fields_json", T, nullable=False),
    sa.Column("tests_json", T, nullable=False),
    sa.Column("documents_json", T, nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("status IN ('draft')", name="status"))

# Черновой экспорт паспорта (неизменяемо, журналируется): номер, какая версия паспорта и какое содержимое
# (SHA-256) выгружено, кто и на каком основании. Сам файл не хранится — его всегда можно получить снова.
passport_exports = sa.Table("passport_exports", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("passport_id", ID, sa.ForeignKey("passports.id"), nullable=False, index=True),
    sa.Column("export_no", sa.Integer, nullable=False),
    sa.Column("passport_version", sa.Integer, nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("authority", U(120), nullable=False),
    sa.Column("exported_by", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("exported_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("passport_id", "export_no", name="uq_passport_exports_no"))

# ------------------------------------------------------------ D-39 (схема v18): качество — рекламации, CAPA, ECR, ECO
# Правила — vector.core.quality (общие с клиентом). Рекламация относится к экземпляру изделия (точная связь с
# проектом, нарядом, составом и версиями — через экземпляр). Статусы: registered → analysis → action →
# effectiveness_check → closed (проверка «не помогло» возвращает в action).
# D-40 (v19): instance_id допускает NULL — рекламация на партию (наряд) или на проект без заводского номера; охват,
# наряд и классификация — в claim_terms. Миграция v18 → v19 снимает NOT NULL (данные не меняются, migrate.py).
claims = sa.Table("claims", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("claim_no", sa.Integer, nullable=False, unique=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("instance_id", ID, sa.ForeignKey("product_instances.id"), index=True),   # NULL — без номера (D-40)
    sa.Column("received_date", sa.Date, nullable=False),
    sa.Column("source", U(16), nullable=False),
    sa.Column("reference", U(300)),                       # номер письма / акта заказчика
    sa.Column("description", T, nullable=False),
    sa.Column("status", U(24), nullable=False),
    sa.Column("responsible_id", ID, sa.ForeignKey("users.id")),   # кто ведёт анализ причины
    sa.Column("cause", T),                                # найденная причина (при составлении плана)
    sa.Column("submitted_by", ID, sa.ForeignKey("users.id")),     # кто отправил на проверку эффективности
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("status IN ('registered','analysis','action','effectiveness_check','closed')", name="status"),
    sa.CheckConstraint("source IN ('customer','internal')", name="source"))

# Действия CAPA (корректирующие и предупреждающие) по рекламации: кто, что, к какому сроку; отметка «выполнено» с
# результатом. Кто составил действие (planned_by) и кто выполнил (done_by) — для независимости проверяющего.
claim_actions = sa.Table("claim_actions", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("claim_id", ID, sa.ForeignKey("claims.id"), nullable=False, index=True),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("kind", U(16), nullable=False),
    sa.Column("description", U(2000), nullable=False),
    sa.Column("owner_id", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("due_date", sa.Date, nullable=False),
    sa.Column("status", U(8), nullable=False),
    sa.Column("result", U(2000)),
    sa.Column("done_by", ID, sa.ForeignKey("users.id")), sa.Column("done_at", UtcDateTime),
    sa.Column("planned_by", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("planned_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("claim_id", "seq", name="uq_claim_actions_seq"),
    sa.CheckConstraint("kind IN ('corrective','preventive')", name="kind"),
    sa.CheckConstraint("status IN ('open','done')", name="status"))

# Проверка эффективности (неизменяемо): отдельная запись независимого проверяющего (03 § 4). Только по ней
# рекламация закрывается; «не помогло» — тоже запись, рекламация возвращается к действиям.
effectiveness_checks = sa.Table("effectiveness_checks", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("claim_id", ID, sa.ForeignKey("claims.id"), nullable=False, index=True),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("effective", sa.Boolean, nullable=False),
    sa.Column("evidence", T, nullable=False),
    sa.Column("authority", U(120), nullable=False),
    sa.Column("checked_by", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("checked_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("claim_id", "seq", name="uq_effectiveness_checks_seq"))

# Запрос на изменение (ECR): submitted → assessed → accepted | rejected. Может ссылаться на рекламацию и изделие.
change_requests = sa.Table("change_requests", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("ecr_no", sa.Integer, nullable=False, unique=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("kit_id", ID, sa.ForeignKey("kits.id")),
    sa.Column("claim_id", ID, sa.ForeignKey("claims.id")),
    sa.Column("title", U(300), nullable=False),
    sa.Column("reason", T, nullable=False),
    sa.Column("proposal", T, nullable=False),
    sa.Column("status", U(16), nullable=False),
    sa.Column("impact", T),                               # оценка влияния (что затронет, сроки, стоимость словами)
    sa.Column("assessed_by", ID, sa.ForeignKey("users.id")), sa.Column("assessed_at", UtcDateTime),
    sa.Column("decision_reason", T),
    sa.Column("decided_by", ID, sa.ForeignKey("users.id")), sa.Column("decided_at", UtcDateTime),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("status IN ('submitted','assessed','accepted','rejected')", name="status"))

# Извещение об изменении (ECO) по принятому ECR (одно на запрос): draft → approved → implemented → verified.
# items_json — затронутые ТОЧНЫЕ версии (документ + номер версии, техпроцесс + редакция) на момент составления;
# results_json — на какие версии заменены при внедрении. После утверждения строки не меняются (проверка сервиса).
change_orders = sa.Table("change_orders", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("eco_no", sa.Integer, nullable=False, unique=True),
    sa.Column("ecr_id", ID, sa.ForeignKey("change_requests.id"), nullable=False, unique=True),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), nullable=False, index=True),
    sa.Column("title", U(300), nullable=False),
    sa.Column("description", T, nullable=False),
    sa.Column("items_json", T, nullable=False),
    sa.Column("results_json", T),
    sa.Column("status", U(16), nullable=False),
    sa.Column("approved_by", ID, sa.ForeignKey("users.id")), sa.Column("approved_at", UtcDateTime),
    sa.Column("implemented_by", ID, sa.ForeignKey("users.id")), sa.Column("implemented_at", UtcDateTime),
    sa.Column("verified_by", ID, sa.ForeignKey("users.id")), sa.Column("verified_at", UtcDateTime),
    sa.Column("verify_note", T),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("status IN ('draft','approved','implemented','verified')", name="status"))

# Журнал решений по качеству (неизменяемо): каждый шаг рекламации, ECR и ECO — кто, когда, на каком основании и с
# какими подробностями. По нему карточка показывает историю, а проверка независимости — кто участвовал.
quality_decisions = sa.Table("quality_decisions", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("object_type", U(16), nullable=False),
    sa.Column("object_id", ID, nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("decision", U(24), nullable=False),
    sa.Column("details_json", T, nullable=False),
    sa.Column("authority", U(120), nullable=False),
    sa.Column("decided_by", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("decided_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("object_type", "object_id", "seq", name="uq_quality_decisions_seq"),
    sa.CheckConstraint("object_type IN ('claim','ecr','eco')", name="object_type"))

# ------------------------------------------------------------ D-40 (схема v19): охват, классификация, сроки ответа
# Условия рекламации (одна строка на рекламацию, таблица-расширение): охват — экземпляр, партия (наряд) или проект;
# тяжесть и категория дефекта; срок ответа заказчику и когда он установлен (due_set_at: «ответ дан» = есть ответ,
# записанный после этого момента). У рекламаций до v19 строки нет — это «экземпляр, не классифицирована, без срока».
claim_terms = sa.Table("claim_terms", metadata,
    sa.Column("claim_id", ID, sa.ForeignKey("claims.id"), primary_key=True),
    sa.Column("scope", U(16), nullable=False),
    sa.Column("work_order_id", ID, sa.ForeignKey("work_orders.id"), index=True),   # партия — наряд
    sa.Column("severity", U(16)),
    sa.Column("category", U(24)),
    sa.Column("response_due", sa.Date),
    sa.Column("due_set_at", UtcDateTime),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.CheckConstraint("scope IN ('instance','batch','project')", name="scope"),
    sa.CheckConstraint("scope <> 'batch' OR work_order_id IS NOT NULL", name="batch_wo"),
    sa.CheckConstraint("severity IS NULL OR severity IN ('critical','major','minor')", name="severity"),
    sa.CheckConstraint("category IS NULL OR category IN ('design','manufacturing','purchased','software','logistics',"
                       "'operation','other')", name="category"),
    sa.CheckConstraint("(response_due IS NULL AND due_set_at IS NULL) OR "
                       "(response_due IS NOT NULL AND due_set_at IS NOT NULL)", name="due_pair"))

# Ответы заказчику по рекламации (неизменяемо): вид (промежуточный / окончательный), дата отправки, суть, номер
# письма, кто записал и на каком основании. Ответ, записанный после установки срока, «закрывает» этот срок.
claim_responses = sa.Table("claim_responses", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("claim_id", ID, sa.ForeignKey("claims.id"), nullable=False, index=True),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("kind", U(16), nullable=False),
    sa.Column("response_date", sa.Date, nullable=False),
    sa.Column("summary", T, nullable=False),
    sa.Column("reference", U(300)),
    sa.Column("authority", U(120), nullable=False),
    sa.Column("responded_by", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("responded_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("claim_id", "seq", name="uq_claim_responses_seq"),
    sa.CheckConstraint("kind IN ('interim','final')", name="kind"))

# ------------------------------------------------------------ D-42 (схема v20): рабочее время и дневной отчёт
# Дневной отчёт: один на сотрудника за рабочий день. Смена — минуты от полуночи (начало, конец; обе или ни одной),
# перерыв — минуты; total_minutes — сумма строк (для списков без чтения строк). Статусы: draft → submitted →
# accepted | returned; submitted / accepted → draft — исправление с основанием (report.reopen).
daily_reports = sa.Table("daily_reports", metadata,
    sa.Column("id", ID, primary_key=True),
    sa.Column("user_id", ID, sa.ForeignKey("users.id"), nullable=False, index=True),
    sa.Column("work_date", sa.Date, nullable=False, index=True),
    sa.Column("status", U(16), nullable=False),
    sa.Column("shift_start", sa.Integer), sa.Column("shift_end", sa.Integer),
    sa.Column("break_minutes", sa.Integer, nullable=False),
    sa.Column("total_minutes", sa.Integer, nullable=False),
    sa.Column("comment", T),
    sa.Column("submitted_at", UtcDateTime),
    sa.Column("reviewed_by", ID, sa.ForeignKey("users.id")), sa.Column("reviewed_at", UtcDateTime),
    sa.Column("review_comment", T),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_by", ID, nullable=False), sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("updated_by", ID, nullable=False), sa.Column("updated_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("user_id", "work_date", name="uq_daily_reports_day"),
    sa.CheckConstraint("status IN ('draft','submitted','accepted','returned')", name="status"),
    sa.CheckConstraint("(shift_start IS NULL AND shift_end IS NULL) OR (shift_start IS NOT NULL AND shift_end IS NOT NULL "
                       "AND shift_start >= 0 AND shift_end <= 1440 AND shift_end > shift_start)", name="shift"),
    sa.CheckConstraint("break_minutes >= 0 AND total_minutes >= 0", name="minutes"))

# Строки отчёта «на что ушло время»: проект и задача (если работа проектная), минуты, что сделано. Меняются только
# в черновике / возвращённом отчёте (сохранение заменяет строки целиком); история шагов — в daily_report_events.
daily_report_lines = sa.Table("daily_report_lines", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("report_id", ID, sa.ForeignKey("daily_reports.id"), nullable=False, index=True),
    sa.Column("line_no", sa.Integer, nullable=False),
    sa.Column("project_id", ID, sa.ForeignKey("projects.id"), index=True),
    sa.Column("task_id", ID, sa.ForeignKey("tasks.id")),
    sa.Column("minutes", sa.Integer, nullable=False),
    sa.Column("note", T, nullable=False),
    sa.UniqueConstraint("report_id", "line_no", name="uq_daily_report_lines_no"),
    sa.CheckConstraint("minutes > 0 AND minutes <= 1440", name="minutes"),
    sa.CheckConstraint("task_id IS NULL OR project_id IS NOT NULL", name="task_project"))

# Журнал шагов дневного отчёта (неизменяемо): создан, сохранён, отправлен, принят, возвращён, открыт для
# исправления — кто, когда, на каком основании (замещение руководителя отдела — D-34) и подробности (итоги, причина).
daily_report_events = sa.Table("daily_report_events", metadata,
    sa.Column("id", BIGID, primary_key=True, autoincrement=True),
    sa.Column("report_id", ID, sa.ForeignKey("daily_reports.id"), nullable=False, index=True),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("decision", U(24), nullable=False),
    sa.Column("details_json", T, nullable=False),
    sa.Column("authority", U(120), nullable=False),
    sa.Column("decided_by", ID, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("decided_at", UtcDateTime, nullable=False),
    sa.UniqueConstraint("report_id", "seq", name="uq_daily_report_events_seq"))

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
                 (procurement_need_line_match_units, 50011), (stock_transfers, 50012),
                 (stock_transfer_acts, 50013), (stock_shipment_terms, 50014), (stock_shipment_losses, 50015),
                 (signatures, 50016), (purchase_receipts, 50017), (purchase_order_approvals, 50018),
                 (approval_bases, 50019), (purchase_order_invoice_checks, 50020),
                 (role_delegation_projects, 50021), (tech_process_approvals, 50022),
                 (work_order_snapshots, 50023), (wo_timer_events, 50024), (wo_substitutions, 50025),
                 (work_order_issues, 50026), (instance_compositions, 50027), (instance_events, 50028),
                 (passport_exports, 50029), (effectiveness_checks, 50030), (quality_decisions, 50031),
                 (claim_responses, 50032), (daily_report_events, 50033)):
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
