"""Конфигурация сервера (TOML) с проверкой. Секреты в файле конфигурации не хранятся.

Строка подключения к БД берётся из переменной окружения (database.url_env) или из файла
(database.url_file) с ограниченными правами. В журналах строка подключения маскируется.
"""
from __future__ import annotations

import os
import stat
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa

from .security import SecurityPolicy


class ConfigError(Exception):
    pass


@dataclass
class ServerConfig:
    db_url: str
    host: str = "0.0.0.0"
    port: int = 8443
    tls_cert: Path | None = None
    tls_key: Path | None = None
    dev_insecure_localhost: bool = False
    backup_dir: Path | None = None
    backup_keep: int | None = None
    security: SecurityPolicy = field(default_factory=SecurityPolicy)
    warnings: list[str] = field(default_factory=list)

    def masked_db_url(self) -> str:
        return mask_url(self.db_url)


def mask_url(url: str) -> str:
    try:
        return sa.engine.make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<неразборчивая строка подключения>"


def _check_private_file(p: Path, what: str, warnings: list[str]) -> None:
    if sys.platform.startswith("win"):
        warnings.append(f"{what}: проверьте ACL файла {p} (доступ только учётной записи службы и администраторов).")
        return
    mode = p.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"{what}: файл {p} доступен группе/всем (права {oct(mode & 0o777)}). Установите 600.")


def load_config(path: Path) -> ServerConfig:
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"Не удалось прочитать конфигурацию {path}: {e}")
    warnings: list[str] = []
    dbc = data.get("database", {})
    if "url" in dbc:
        raise ConfigError("Строку подключения нельзя хранить в конфигурации (секрет). Используйте database.url_env или database.url_file.")
    if "url_env" in dbc:
        url = os.environ.get(dbc["url_env"])
        if not url:
            raise ConfigError(f"Переменная окружения {dbc['url_env']} не задана.")
    elif "url_file" in dbc:
        p = Path(dbc["url_file"])
        _check_private_file(p, "Строка подключения", warnings)
        url = p.read_text(encoding="utf-8").strip()
    else:
        raise ConfigError("Не задан источник строки подключения: database.url_env или database.url_file.")
    backend = sa.engine.make_url(url).get_backend_name()
    if backend == "sqlite":
        warnings.append("SQLite допустим только для тестов и пилота на одном рабочем месте; для работы — PostgreSQL или MS SQL.")
    srv = data.get("server", {})
    cfg = ServerConfig(db_url=url, host=srv.get("host", "0.0.0.0"), port=int(srv.get("port", 8443)),
                       tls_cert=Path(srv["tls_cert"]) if srv.get("tls_cert") else None,
                       tls_key=Path(srv["tls_key"]) if srv.get("tls_key") else None,
                       dev_insecure_localhost=bool(srv.get("dev_insecure_localhost", False)), warnings=warnings)
    if cfg.dev_insecure_localhost:
        if cfg.host not in ("127.0.0.1", "::1", "localhost"):
            raise ConfigError("Режим без TLS разрешён только при host = 127.0.0.1 (разработка).")
        warnings.append("ВНИМАНИЕ: TLS выключен (режим разработки, только localhost).")
    else:
        if not (cfg.tls_cert and cfg.tls_key):
            raise ConfigError("Для работы в сети нужны server.tls_cert и server.tls_key (сертификат, выданный CA компании).")
        for p in (cfg.tls_cert, cfg.tls_key):
            if not p.is_file():
                raise ConfigError(f"Файл {p} не найден.")
        _check_private_file(cfg.tls_key, "Закрытый ключ TLS", warnings)
    sec = data.get("security", {})
    cfg.security = SecurityPolicy(**{k: int(v) for k, v in sec.items()})
    cfg.security.validate()
    b = data.get("backup", {})
    if b:
        cfg.backup_dir = Path(b["dir"]) if b.get("dir") else None
        cfg.backup_keep = int(b["keep"]) if b.get("keep") else None
    return cfg
