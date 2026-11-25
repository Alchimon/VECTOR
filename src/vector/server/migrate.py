"""Миграция схемы БД между версиями программы: `vector-server migrate`.

Принципы (коммерческая эксплуатация, данные компании):

1. **Сначала копия.** Перед любым изменением схемы создаётся зашифрованная резервная копия ТЕКУЩЕЙ БД и
   полностью проверяется (vector-premigrate-v<N>-<время>.vbak). Не удалось сделать или проверить копию —
   миграция не начинается.
2. **Всё или ничего.** Все шаги выполняются в ОДНОЙ транзакции вместе с записью новой версии схемы
   (DDL транзакционен на PostgreSQL, MS SQL и SQLite). Сбой посередине — БД остаётся в прежней версии.
3. **Только добавление.** Шаги не удаляют и не переписывают существующие данные; переходы v3 → v4
   (таблицы расчётов), v4 → v5 (склад и закупка) v5 → v6 (ручное сопоставление потребности), v6 → v7 (настройки, справочник пересчёта) и v7 → v8 (перемещения
   между складами) v8 → v9 (перемещение «в пути») v9 → v10 (акт недостачи, срок прибытия) v10 → v11 (акт об утрате, подписи), v11 → v12 (поставщики, заказы, факты поставки) и v12 → v13 (реквизиты и
   договоры поставщиков, НДС и согласование заказов — таблицы-расширения) v13 → v14 (замещение, основания
   решений, сверка со счётом поставщика) и v14 → v15 (замещение в проекте, уведомления) … v28 → v29 (лиды, потоки,
   риск-разрешения, архив — D-56), v29 → v30 (ревизии РКД, проверки, замечания, трансмитталы) и v30 → v31, v31 → v32
   (консолидация закупок; закупка из пакета — D-58, D-59) только создают новые таблицы. Шаги, меняющие данные, потребуют отдельного решения (Decision Log).
   Единственное исключение — v18 → v19 (D-40, ответ владельца на A-46 «рекламации без серийного номера бывают»):
   у claims.instance_id снимается ограничение NOT NULL. Данные при этом не меняются (существующие строки уже
   удовлетворяют более слабому ограничению), ограничение только ослабляется; см. _relax_claim_instance.
   Так же v20 → v21 (D-43, ответы владельца на A-48): у daily_reports ослабляются два CHECK-ограничения — статус
   «на самопроверке» и ночная смена (_replace_checks); существующие строки им уже удовлетворяют. Столбцы
   существующих таблиц не добавляются (копия и восстановление берут список столбцов из описания схемы) — новые
   данные лежат в новых таблицах.
4. **Fail-closed.** Неизвестная/более новая версия, незавершённое восстановление, уже существующая таблица,
   которую шаг должен создать, — отказ без изменений.

Откат: остановить сервер, восстановить копию «до миграции» в ПУСТУЮ БД той версией программы, с которой она
сделана (в заголовке копии записаны версии программы и схемы; `vector-server verify-backup` их покажет).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import sqlalchemy as sa

from . import backup as bk
from . import db


class MigrationError(RuntimeError):
    """Миграция невозможна или отменена. Текст — для администратора; к моменту исключения БД не изменена (всё или
    ничего).
    """
    pass


# Таблицы, появившиеся в каждой версии схемы. Таблицы версии N = все, кроме добавленных в версиях > N.
ADDED_IN: dict[int, list[sa.Table]] = {
    4: [db.calc_drafts, db.calc_draft_lines, db.calc_versions, db.calc_version_lines,
        db.project_calc_revisions, db.project_calc_revision_items],
    # С4: склад и закупка. Порядок учитывает внешние ключи (потребность → резерв → движение).
    5: [db.stock_items, db.warehouses, db.stock_balances, db.procurement_needs, db.procurement_need_lines,
        db.stock_reservations, db.stock_movements],
    # С4, часть 2: ручное сопоставление строк потребности (A-25).
    6: [db.procurement_need_line_matches],
    # Настройки компании (D-26) и справочник аналогов/пересчёта единиц (D-25). Порядок — по внешним ключам.
    7: [db.config_versions, db.item_aliases, db.unit_conversions, db.procurement_need_line_match_units],
    # Перемещение между складами (A-28): связка двух движений журнала.
    8: [db.stock_transfers],
    # Перемещение со статусом «в пути» (D-29).
    9: [db.stock_shipments],
    # Акт недостачи при приёмке и ожидаемая дата прибытия перемещения (D-30).
    10: [db.stock_transfer_acts, db.stock_shipment_terms],
    # Акт об утрате перемещения и подписи документов (D-31).
    11: [db.stock_shipment_losses, db.signatures],
    # Заказы поставщикам (С4 шаг 5, D-32).
    12: [db.suppliers, db.purchase_orders, db.purchase_order_lines, db.purchase_receipts],
    # Реквизиты и договоры поставщиков, НДС и согласование заказов (D-33). Порядок — по внешним ключам.
    13: [db.supplier_requisites, db.supplier_contracts, db.purchase_order_terms, db.purchase_order_line_taxes,
         db.purchase_order_approvals],
    # Замещение (A-37 → D-34), основания решений, сверка заказа со счётом поставщика (A-38 → D-34).
    14: [db.role_delegations, db.approval_bases, db.purchase_order_invoices, db.purchase_order_invoice_checks],
    # Замещение роли в проекте и уведомления в программе (A-40 → D-35).
    15: [db.role_delegation_projects, db.notifications],
    # Технология и производство (этап 5, D-36). Порядок — по внешним ключам.
    16: [db.tech_processes, db.tech_process_ops, db.tech_process_approvals, db.work_orders, db.work_order_snapshots,
         db.wo_operations, db.wo_timer_events, db.wo_substitutions, db.work_order_issues],
    # Экземпляры изделий, паспорт, история эксплуатации (этап 6, D-38). Порядок — по внешним ключам.
    17: [db.product_instances, db.instance_compositions, db.instance_events, db.passports, db.passport_exports],
    # Качество: рекламации, CAPA, проверки эффективности, ECR, ECO, журнал решений (этап 6, D-39).
    18: [db.claims, db.claim_actions, db.effectiveness_checks, db.change_requests, db.change_orders, db.quality_decisions],
    # Охват и классификация рекламаций, срок и ответы заказчику (D-40). Плюс снятие NOT NULL — см. STEPS[18].
    19: [db.claim_terms, db.claim_responses],
    # Рабочее время: дневные отчёты, их строки и журнал шагов (D-42).
    20: [db.daily_reports, db.daily_report_lines, db.daily_report_events],
    # Отчёт, собранный программой, и источник времени строк (D-43). Плюс ограничения и индекс — см. STEPS[20].
    21: [db.daily_report_auto, db.daily_report_line_sources],
    # Оргструктура: подразделения, сотрудники в них, журнал изменений (R-21, D-44). Порядок — по внешним ключам.
    22: [db.org_units, db.org_members, db.org_events],
    23: [db.workdays, db.workday_events, db.workday_start_requests],
    24: [db.work_schedules, db.work_schedule_links, db.workday_auto_finishes, db.workday_end_requests],
    25: [db.work_calendar_years, db.work_calendar_days, db.work_calendar_overrides, db.work_schedule_calendar],
    26: [db.overtime_requests, db.overtime_events],
    27: [db.overtime_period_config, db.overtime_request_periods],
    28: [db.overtime_period_closures],
    # Сквозные процессы проекта: лиды, потоки, риск-разрешения, события, снимок архива (D-56). Порядок — по внешним ключам.
    29: [db.sales_opportunities, db.project_workstreams, db.project_risk_acceptances, db.project_events,
         db.project_closeouts],
    # Ревизии РКД: бизнес-ревизии, решения проверки, замечания/ответы, трансмитталы (этап 3 blueprint 52).
    # Порядок — по внешним ключам.
    30: [db.document_revisions, db.document_reviews, db.document_comments, db.document_comment_responses,
         db.document_transmittals, db.transmittal_items],
    # Консолидация закупок: группы, пакеты, строки пакетов, распределения, предложения аналогов (этап 4, 52).
    31: [db.procurement_groups, db.procurement_batches, db.procurement_batch_lines, db.procurement_allocations,
         db.item_analogs],
    # Закупка из пакета: кратность заказа позиции, связь строки заказа со строкой пакета, распределение прихода
    # по распределениям пакета (складская часть этапа 4, D-59). Порядок — по внешним ключам.
    32: [db.stock_item_packs, db.purchase_order_batch_lines, db.procurement_allocation_receipts],
    # Снимок готовности сборки при выпуске наряда (этап 5 blueprint 52, D-61).
    33: [db.assembly_readiness_snapshots],
    # Профили шифров ревизий и статусы выдачи (этап 3б blueprint 52, D-62). Порядок — по внешним ключам.
    34: [db.document_profiles, db.document_revision_issues],
    # ZIP-пакет трансмиттала из неизменяемых версий (этап 3б blueprint 52, D-63).
    35: [db.transmittal_packages],
    36: [db.checklist_templates],
    37: [db.pnr_requests, db.fat_sessions, db.fat_findings],
    38: [db.delivery_doc_templates, db.delivery_docs],
    39: [db.norm_proposals],
    # ТК-контур сверхурочных: кадровые категории и основания/согласия заявок (blueprint 51, D-74).
    40: [db.hr_categories, db.overtime_request_grounds],
    # Корректировка остатка с подписью руководителя отдела (С4, D-77).
    41: [db.stock_adjustments],
    # Предложения перемещения между проектами и совместное решение «склад + закупка» (С4 шаги 3–4, D-78).
    42: [db.stock_transfer_proposals],
    # Пополнение расходников по порогу: правила и черновые позиции (С4 шаг 7, D-79).
    43: [db.stock_replenishment_rules, db.stock_replenishment_drafts],
    # Офисные документы (Ф1.3, D-94): вид и признак шаблона поверх карточки documents.
    44: [db.office_documents],
}


def tables_of_version(v: int) -> list[str]:
    """Имена таблиц, которые существуют в схеме версии v, в порядке резервного копирования. Нужны, чтобы копия перед
    миграцией содержала ровно таблицы старой версии.
    """
    later = {t.name for ver, ts in ADDED_IN.items() if ver > v for t in ts}
    return [n for n in db.BACKUP_TABLE_ORDER if n not in later]


def _create_tables(tables: list[sa.Table]) -> Callable:
    """Шаг миграции «создать новые таблицы». Возвращает функцию step(conn), выполняемую внутри общей транзакции."""
    def step(conn) -> list[str]:
        """Создать таблицы шага. Если хоть одна уже есть — БД в неожиданном состоянии: отказ без изменений (не
        «досоздаём» частично).
        """
        existing = set(sa.inspect(conn).get_table_names())
        clash = [t.name for t in tables if t.name in existing]
        if clash:
            raise MigrationError("Таблицы, которые должна создать миграция, уже существуют: " + ", ".join(clash) +
                                 ". БД в непредусмотренном состоянии — миграция отменена, изменений нет.")
        for t in tables:          # порядок списка учитывает внешние ключи
            t.create(conn)        # триггеры неизменяемости создаются событиями after_create (db._immutable)
        return [t.name for t in tables]
    return step


# Столбец, ограничение NOT NULL которого снимает шаг v18 → v19, и его внешний ключ / индекс (имена — по
# naming_convention db.metadata, одинаковые на всех СУБД).
_RELAX_TABLE, _RELAX_COLUMN = "claims", "instance_id"
_RELAX_FK = "fk_claims_instance_id_product_instances"
_RELAX_IX = "ix_claims_instance_id"


def _column_nullable(conn, table: str, column: str) -> bool:
    """Допускает ли столбец NULL в РЕАЛЬНОЙ БД (по описанию схемы СУБД, а не по db.metadata)."""
    for c in sa.inspect(conn).get_columns(table):
        if c["name"] == column:
            return bool(c["nullable"])
    raise MigrationError(f"В таблице {table} нет столбца {column} — БД в непредусмотренном состоянии, миграция отменена.")


def _relax_claim_instance(conn) -> None:
    """Снять NOT NULL с claims.instance_id (рекламация на партию или проект без заводского номера, D-40).

    Данные не меняются. Способ — свой для каждой СУБД, всё внутри общей транзакции миграции:

    * PostgreSQL — ALTER TABLE … ALTER COLUMN … DROP NOT NULL;
    * MS SQL — ALTER COLUMN с тем же типом и NULL; внешний ключ и индекс по столбцу на время пересоздаются (MS SQL
      не меняет столбец, на который они ссылаются); имена — те же, что у новой БД;
    * SQLite (тесты и пилот) — у SQLite нет ALTER COLUMN; применяется документированный SQLite способ снятия NOT NULL
      без перестройки таблицы: правка текста CREATE TABLE в sqlite_master (writable_schema) с увеличением
      schema_version — формат хранения строк от этого не меняется. Текст должен содержать ровно одно ожидаемое
      определение столбца, иначе — отказ без изменений (fail-closed).

    Если столбец уже допускает NULL (например, БД создана этой версией и версия схемы записана вручную) — ничего не
    делается. После шага проверяется, что столбец действительно допускает NULL.
    """
    if _column_nullable(conn, _RELAX_TABLE, _RELAX_COLUMN):
        return
    name = conn.dialect.name
    if name == "postgresql":
        conn.exec_driver_sql(f"ALTER TABLE {_RELAX_TABLE} ALTER COLUMN {_RELAX_COLUMN} DROP NOT NULL")
    elif name == "mssql":
        typ = db.claims.c.instance_id.type.compile(dialect=conn.dialect)
        conn.exec_driver_sql(f"ALTER TABLE {_RELAX_TABLE} DROP CONSTRAINT {_RELAX_FK}")
        conn.exec_driver_sql(f"DROP INDEX {_RELAX_IX} ON {_RELAX_TABLE}")
        conn.exec_driver_sql(f"ALTER TABLE {_RELAX_TABLE} ALTER COLUMN {_RELAX_COLUMN} {typ} NULL")
        conn.exec_driver_sql(f"CREATE INDEX {_RELAX_IX} ON {_RELAX_TABLE} ({_RELAX_COLUMN})")
        conn.exec_driver_sql(f"ALTER TABLE {_RELAX_TABLE} ADD CONSTRAINT {_RELAX_FK} FOREIGN KEY ({_RELAX_COLUMN}) "
                             "REFERENCES product_instances (id)")
    elif name == "sqlite":
        sql = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (_RELAX_TABLE,)).scalar()
        pattern = re.compile(r"(\n\s*" + _RELAX_COLUMN + r" VARCHAR\(36\)) NOT NULL(,)")
        if not sql or len(pattern.findall(sql)) != 1:
            raise MigrationError("Определение claims.instance_id не такое, как ожидалось (SQLite) — миграция отменена, "
                                 "изменений нет.")
        ver = conn.exec_driver_sql("PRAGMA schema_version").scalar()
        conn.exec_driver_sql("PRAGMA writable_schema=ON")
        try:
            conn.exec_driver_sql("UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?",
                                 (pattern.sub(r"\1\2", sql), _RELAX_TABLE))
            conn.exec_driver_sql(f"PRAGMA schema_version={int(ver) + 1}")
        finally:
            conn.exec_driver_sql("PRAGMA writable_schema=OFF")
        if conn.exec_driver_sql("PRAGMA integrity_check").scalar() != "ok":
            raise MigrationError("Проверка целостности SQLite после правки схемы не прошла — миграция отменена.")
    else:  # pragma: no cover — другие СУБД не поддерживаются (db.SUPPORTED)
        raise MigrationError(f"СУБД {name} не поддерживается.")
    if not _column_nullable(conn, _RELAX_TABLE, _RELAX_COLUMN):
        raise MigrationError("Не удалось снять ограничение NOT NULL с claims.instance_id — миграция отменена, изменений нет.")


def _v18_to_v19(conn) -> list[str]:
    """Шаг v18 → v19: снять NOT NULL с claims.instance_id и создать таблицы claim_terms и claim_responses."""
    _relax_claim_instance(conn)
    return _create_tables(ADDED_IN[19])(conn)


# Ограничения daily_reports, которые ослабляет шаг v20 → v21: имя (по naming_convention) → (было, стало).
_V21_CHECKS = {"ck_daily_reports_status": (db.REPORT_STATUS_CHECK_V20, db.REPORT_STATUS_CHECK),
               "ck_daily_reports_shift": (db.REPORT_SHIFT_CHECK_V20, db.REPORT_SHIFT_CHECK)}


def _check_names(conn, table: str) -> set[str]:
    """Имена CHECK-ограничений таблицы в РЕАЛЬНОЙ БД."""
    return {c["name"] for c in sa.inspect(conn).get_check_constraints(table) if c.get("name")}


def _replace_checks(conn, table: str, changes: dict[str, tuple[str, str]]) -> None:
    """Заменить CHECK-ограничения таблицы более слабыми (существующие строки им уже удовлетворяют). Данные не меняются.

    * PostgreSQL, MS SQL — ALTER TABLE … DROP CONSTRAINT имя; ADD CONSTRAINT имя CHECK (новое) — внутри общей
      транзакции миграции; ограничение с таким именем должно существовать;
    * SQLite — правка текста CREATE TABLE в sqlite_master (writable_schema, как в _relax_claim_instance): каждое
      прежнее определение «CONSTRAINT имя CHECK (было)» должно встретиться ровно один раз (или уже стоит новое —
      таблицу создал более ранний шаг той же миграции), иначе — отказ без изменений (fail-closed); затем проверка
      целостности.
    """
    name = conn.dialect.name
    have = _check_names(conn, table)
    missing = [n for n in changes if n not in have]
    if missing:
        raise MigrationError(f"В таблице {table} нет ограничений {', '.join(missing)} — БД в непредусмотренном "
                             "состоянии, миграция отменена, изменений нет.")
    if name in ("postgresql", "mssql"):
        for cname, (_old, new) in changes.items():
            conn.exec_driver_sql(f"ALTER TABLE {table} DROP CONSTRAINT {cname}")
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD CONSTRAINT {cname} CHECK ({new})")
    elif name == "sqlite":
        sql = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).scalar() or ""
        todo = 0
        for cname, (old, new) in changes.items():
            frag, done = f"CONSTRAINT {cname} CHECK ({old})", f"CONSTRAINT {cname} CHECK ({new})"
            if sql.count(done) == 1:
                continue                  # уже новое (таблицу создал более ранний шаг этой же миграции)
            if sql.count(frag) != 1:
                raise MigrationError(f"Определение ограничения {cname} не такое, как ожидалось (SQLite) — миграция "
                                     "отменена, изменений нет.")
            sql, todo = sql.replace(frag, done), todo + 1
        if not todo:
            return
        ver = conn.exec_driver_sql("PRAGMA schema_version").scalar()
        conn.exec_driver_sql("PRAGMA writable_schema=ON")
        try:
            conn.exec_driver_sql("UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?", (sql, table))
            conn.exec_driver_sql(f"PRAGMA schema_version={int(ver) + 1}")
        finally:
            conn.exec_driver_sql("PRAGMA writable_schema=OFF")
        if conn.exec_driver_sql("PRAGMA integrity_check").scalar() != "ok":
            raise MigrationError("Проверка целостности SQLite после правки схемы не прошла — миграция отменена.")
    else:  # pragma: no cover — другие СУБД не поддерживаются (db.SUPPORTED)
        raise MigrationError(f"СУБД {name} не поддерживается.")
    if not set(changes) <= _check_names(conn, table):
        raise MigrationError(f"Не удалось заменить ограничения таблицы {table} — миграция отменена, изменений нет.")


def _v20_to_v21(conn) -> list[str]:
    """Шаг v20 → v21 (D-43, ответы на A-48): статус «на самопроверке» и ночная смена (ослабить ограничения
    daily_reports), индекс журнала аудита «сотрудник + время», таблицы daily_report_auto и daily_report_line_sources."""
    _replace_checks(conn, "daily_reports", _V21_CHECKS)
    if db.AUDIT_ACTOR_INDEX.name not in {i["name"] for i in sa.inspect(conn).get_indexes("audit_log")}:
        db.AUDIT_ACTOR_INDEX.create(conn)
    return _create_tables(ADDED_IN[21])(conn)


# Шаг «из версии N в N+1».
STEPS: dict[int, Callable] = {3: _create_tables(ADDED_IN[4]), 4: _create_tables(ADDED_IN[5]),
                              5: _create_tables(ADDED_IN[6]), 6: _create_tables(ADDED_IN[7]),
                              7: _create_tables(ADDED_IN[8]), 8: _create_tables(ADDED_IN[9]),
                              9: _create_tables(ADDED_IN[10]), 10: _create_tables(ADDED_IN[11]), 11: _create_tables(ADDED_IN[12]),
                              12: _create_tables(ADDED_IN[13]), 13: _create_tables(ADDED_IN[14]),
                              14: _create_tables(ADDED_IN[15]), 15: _create_tables(ADDED_IN[16]),
                              16: _create_tables(ADDED_IN[17]), 17: _create_tables(ADDED_IN[18]),
                              18: _v18_to_v19, 19: _create_tables(ADDED_IN[20]), 20: _v20_to_v21,
                              21: _create_tables(ADDED_IN[22]), 22: _create_tables(ADDED_IN[23]),
                              23: _create_tables(ADDED_IN[24]),
                              24: _create_tables(ADDED_IN[25]),
                              25: _create_tables(ADDED_IN[26]),
                              26: _create_tables(ADDED_IN[27]),
                              27: _create_tables(ADDED_IN[28]),
                              28: _create_tables(ADDED_IN[29]),
                              29: _create_tables(ADDED_IN[30]),
                              30: _create_tables(ADDED_IN[31]),
                              31: _create_tables(ADDED_IN[32]),
                              32: _create_tables(ADDED_IN[33]),
                              33: _create_tables(ADDED_IN[34]),
                              34: _create_tables(ADDED_IN[35]),
                              35: _create_tables(ADDED_IN[36]),
                              36: _create_tables(ADDED_IN[37]),
                              37: _create_tables(ADDED_IN[38]),
                              38: _create_tables(ADDED_IN[39]),
                              39: _create_tables(ADDED_IN[40]),
                              40: _create_tables(ADDED_IN[41]),
                              41: _create_tables(ADDED_IN[42]),
                              42: _create_tables(ADDED_IN[43]),
                              43: _create_tables(ADDED_IN[44])}


@dataclass
class MigrationReport:
    """Итог миграции: с какой на какую версию, где копия до миграции, какие таблицы созданы. Печатается командой
    migrate.
    """
    from_version: int
    to_version: int
    backup_path: Path | None = None
    created_tables: list[str] = field(default_factory=list)


def current_version(engine: sa.Engine) -> int:
    """Текущая версия схемы БД. Отказ, если схемы нет, версия не записана или не завершено восстановление из копии.
    """
    with engine.connect() as conn:
        if "schema_meta" not in sa.inspect(conn).get_table_names():
            raise MigrationError("Схема ВЕКТОР не найдена. Для новой БД выполните «vector-server init».")
        meta = {r.key: r.value for r in conn.execute(sa.select(db.schema_meta))}
    if "restore_in_progress" in meta:
        raise MigrationError("Восстановление из резервной копии не завершено — миграция запрещена.")
    try:
        return int(meta["schema_version"])
    except (KeyError, ValueError):
        raise MigrationError("В БД не записана версия схемы — миграция невозможна.") from None


def migrate(engine: sa.Engine, backup_dir: Path, keys: bk.KeySource, now: datetime | None = None) -> MigrationReport:
    """Перевести БД на db.SCHEMA_VERSION: проверенная копия → все шаги в одной транзакции."""
    now = now or datetime.now(timezone.utc)
    v = current_version(engine)
    target = db.SCHEMA_VERSION
    if v == target:
        return MigrationReport(v, v)
    if v > target:
        raise MigrationError(f"Схема БД v{v} новее, чем знает эта программа (v{target}). Установите более новую версию.")
    missing = [n for n in range(v, target) if n not in STEPS]
    if missing:
        raise MigrationError(f"Нет шага миграции из v{missing[0]} — переход v{v} → v{target} не поддерживается.")

    # 1. Копия текущей БД (только таблицы её версии) и её полная проверка.
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    out = backup_dir / f"vector-premigrate-v{v}-{now:%Y%m%dT%H%M%SZ}.vbak"
    rep = bk.create_backup(engine, out, keys, tables=tables_of_version(v))
    vr = bk.verify_backup(rep.path, keys)
    if not vr.ok:
        raise MigrationError("Копия перед миграцией не прошла проверку: " + "; ".join(vr.problems) + " Миграция не начата.")

    # 2. Все шаги и новая версия схемы — одна транзакция.
    report = MigrationReport(v, target, rep.path)
    with engine.begin() as conn:
        # Повторная проверка версии внутри транзакции: параллельный запуск миграции не пройдёт дважды.
        cur = conn.execute(sa.select(db.schema_meta.c.value).where(db.schema_meta.c.key == "schema_version")).scalar()
        if int(cur) != v:
            raise MigrationError("Версия схемы изменилась во время подготовки (параллельный запуск?) — миграция отменена.")
        for n in range(v, target):
            report.created_tables += STEPS[n](conn)
        conn.execute(db.schema_meta.update().where(db.schema_meta.c.key == "schema_version").values(value=str(target)))
        conn.execute(db.schema_meta.insert().values(key=f"migrated_v{v}_to_v{target}_at", value=now.isoformat()))
    return report
