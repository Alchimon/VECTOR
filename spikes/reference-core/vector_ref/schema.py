"""Схема серверной БД эталонного ядра (SQLite STRICT).

В целевом продукте сервер использует PostgreSQL или MS SQL (NUMERIC(18,4));
SQLite здесь — только Class A. Клиент к этой БД не подключается никогда.
"""

SERVER_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  login         TEXT NOT NULL UNIQUE,
  display_name  TEXT NOT NULL,
  pwd_salt      BLOB NOT NULL,
  pwd_hash      BLOB NOT NULL,
  active        INTEGER NOT NULL CHECK (active IN (0,1)),
  created_at    TEXT NOT NULL
) STRICT;

-- Выдача роли доступа. id = generation: повторная выдача -> новая строка.
CREATE TABLE IF NOT EXISTS role_grants (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       TEXT NOT NULL REFERENCES users(id),
  role          TEXT NOT NULL,
  scope_type    TEXT NOT NULL CHECK (scope_type IN ('company')),
  valid_from    TEXT NOT NULL,
  valid_until   TEXT,
  revoked_at    TEXT,
  granted_by    TEXT,
  reason        TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS sessions (
  token_hash    TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL REFERENCES users(id),
  device_id     TEXT NOT NULL,
  issued_at     TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  revoked_at    TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS customers (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  inn           TEXT,
  note          TEXT,
  version       INTEGER NOT NULL,
  created_by    TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  updated_by    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS tenders (
  id                   TEXT PRIMARY KEY,
  customer_id          TEXT NOT NULL REFERENCES customers(id),
  title                TEXT NOT NULL,
  submission_deadline  TEXT,
  owner_user_id        TEXT NOT NULL REFERENCES users(id),
  status               TEXT NOT NULL,
  status_reason        TEXT,
  version              INTEGER NOT NULL,
  created_by           TEXT NOT NULL,
  created_at           TEXT NOT NULL,
  updated_by           TEXT NOT NULL,
  updated_at           TEXT NOT NULL
) STRICT;

-- ТКП: пользовательская метка label отделена от внутренней монотонной revision.
CREATE TABLE IF NOT EXISTS tkp (
  id              TEXT PRIMARY KEY,
  tender_id       TEXT NOT NULL REFERENCES tenders(id),
  label           TEXT NOT NULL,
  revision        INTEGER NOT NULL,
  amount_minor    INTEGER,          -- NULL = неизвестно, 0 = известный ноль
  currency        TEXT,             -- без значения по умолчанию
  note            TEXT,
  status          TEXT NOT NULL,
  sent_at         TEXT,
  sent_channel    TEXT,
  version         INTEGER NOT NULL,
  created_by      TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  last_edited_by  TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  CHECK (amount_minor IS NULL OR currency IS NOT NULL),
  CHECK (amount_minor IS NULL OR (amount_minor BETWEEN -999999999999999999 AND 999999999999999999))
) STRICT;

-- Согласование привязано к снимку (hash содержимого + revision) и к конкретной выдаче роли.
CREATE TABLE IF NOT EXISTS tkp_approvals (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tkp_id          TEXT NOT NULL REFERENCES tkp(id),
  tkp_revision    INTEGER NOT NULL,
  content_hash    TEXT NOT NULL,
  approver_id     TEXT NOT NULL REFERENCES users(id),
  grant_id        INTEGER NOT NULL REFERENCES role_grants(id),
  approved_at     TEXT NOT NULL,
  superseded_at   TEXT,
  superseded_reason TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS projects (
  id              TEXT PRIMARY KEY,
  code            TEXT NOT NULL UNIQUE,     -- A-10: уникальность кода в пределах компании (предположение)
  title           TEXT NOT NULL,
  tender_id       TEXT REFERENCES tenders(id),
  tender_version_at_start INTEGER,
  status          TEXT NOT NULL,
  version         INTEGER NOT NULL,
  created_by      TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_by      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_tender ON projects(tender_id) WHERE tender_id IS NOT NULL;

-- Назначение в проекте. id = generation.
CREATE TABLE IF NOT EXISTS project_assignments (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id      TEXT NOT NULL REFERENCES projects(id),
  user_id         TEXT NOT NULL REFERENCES users(id),
  project_role    TEXT NOT NULL CHECK (project_role IN ('gip','rp','participant')),
  valid_from      TEXT NOT NULL,
  valid_until     TEXT,
  revoked_at      TEXT,
  revoked_reason  TEXT,
  assigned_by     TEXT NOT NULL
) STRICT;

-- Комплект: код уникален в пределах проекта, а не глобально.
CREATE TABLE IF NOT EXISTS kits (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES projects(id),
  code            TEXT NOT NULL,
  title           TEXT NOT NULL,
  version         INTEGER NOT NULL,
  created_by      TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_by      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  UNIQUE (project_id, code)
) STRICT;

-- Идемпотентность: результат операции хранится и возвращается при повторе того же operation_id.
CREATE TABLE IF NOT EXISTS operations (
  operation_id    TEXT PRIMARY KEY,
  user_id         TEXT,
  device_id       TEXT,
  command         TEXT NOT NULL,
  request_hash    TEXT NOT NULL,
  status          TEXT NOT NULL,
  response_json   TEXT NOT NULL,
  recorded_at     TEXT NOT NULL
) STRICT;

-- Журнал. Append-only на уровне приложения; НЕ является независимой защитой от админа БД.
CREATE TABLE IF NOT EXISTS audit_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  at              TEXT NOT NULL,
  actor_user_id   TEXT,
  device_id       TEXT,
  operation_id    TEXT,
  command         TEXT NOT NULL,
  object_type     TEXT,
  object_id       TEXT,
  authority       TEXT,
  version_before  INTEGER,
  version_after   INTEGER,
  result          TEXT NOT NULL,
  details_json    TEXT NOT NULL
) STRICT;

CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""
