"""Миграция схемы БД между версиями программы: `vector-server migrate`.

Принципы (коммерческая эксплуатация, данные компании):

1. **Сначала копия.** Перед любым изменением схемы создаётся зашифрованная резервная копия ТЕКУЩЕЙ БД и
   полностью проверяется (vector-premigrate-v<N>-<время>.vbak). Не удалось сделать или проверить копию —
   миграция не начинается.
2. **Всё или ничего.** Все шаги выполняются в ОДНОЙ транзакции вместе с записью новой версии схемы
   (DDL транзакционен на PostgreSQL, MS SQL и SQLite). Сбой посередине — БД остаётся в прежней версии.
3. **Только добавление.** Шаги не удаляют и не переписывают существующие данные; переходы v3 → v4
   (таблицы расчётов), v4 → v5 (склад и закупка) и v5 → v6 (ручное сопоставление потребности) только создают новые таблицы. Шаги, меняющие данные, потребуют отдельного решения (Decision Log).
4. **Fail-closed.** Неизвестная/более новая версия, незавершённое восстановление, уже существующая таблица,
   которую шаг должен создать, — отказ без изменений.

Откат: остановить сервер, восстановить копию «до миграции» в ПУСТУЮ БД той версией программы, с которой она
сделана (в заголовке копии записаны версии программы и схемы; `vector-server verify-backup` их покажет).
"""
from __future__ import annotations

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


# Шаг «из версии N в N+1».
STEPS: dict[int, Callable] = {3: _create_tables(ADDED_IN[4]), 4: _create_tables(ADDED_IN[5]),
                              5: _create_tables(ADDED_IN[6])}


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
