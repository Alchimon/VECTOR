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
    """Ошибка конфигурации сервера. Текст — для администратора: что не так и как исправить. Сервер с такой ошибкой не
    запускается.
    """
    pass


@dataclass
class ServerConfig:
    """Проверенная конфигурация сервера (результат load_config).

    * db_url — строка подключения к БД (секрет: в журналы только через masked_db_url);
    * host/port — где слушать; tls_cert/tls_key — сертификат и закрытый ключ HTTPS;
    * dev_insecure_localhost — режим разработки без TLS (только 127.0.0.1);
    * backup_dir/backup_keep — каталог резервных копий и сколько хранить;
    * security — политика паролей, блокировки, сеансов, размера файлов;
    * warnings — предупреждения, которые сервер печатает при старте (например, «SQLite только для пилота»).
    """
    db_url: str
    host: str = "0.0.0.0"  # noqa: S104 — on-prem сервер принимает клиентов из ЛВС; защита — TLS и аутентификация
    port: int = 8443
    tls_cert: Path | None = None
    tls_key: Path | None = None
    dev_insecure_localhost: bool = False
    backup_dir: Path | None = None
    backup_keep: int | None = None
    # Лицензия установки (D-81): файл лицензии (подписан вендором) и публичный ключ проверки. Нет файла —
    # режим демо/стенда (все выпущенные модули доступны). Оба поля задаются вместе.
    license_file: Path | None = None
    license_public_key: Path | None = None
    # Целостность поставки (D-85, ФСТЭК): манифест SHA-256 файлов поставки (tools/make_manifest.py);
    # корень поставки — по умолчанию папка манифеста. Проверяется `vector-server check`.
    package_manifest: Path | None = None
    package_root: Path | None = None
    security: SecurityPolicy = field(default_factory=SecurityPolicy)
    warnings: list[str] = field(default_factory=list)

    def masked_db_url(self) -> str:
        """Строка подключения со скрытым паролем — для журналов и команды check."""
        return mask_url(self.db_url)


def mask_url(url: str) -> str:
    """Скрыть пароль в строке подключения. Если строку не удалось разобрать, не показываем её вовсе (в ней может быть
    пароль).
    """
    try:
        return sa.engine.make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<неразборчивая строка подключения>"


def _check_private_file(p: Path, what: str, warnings: list[str]) -> None:
    """Проверить, что файл с секретом (ключ TLS, строка подключения) не читается другими пользователями.

    В Linux права должны быть 600, иначе сервер не запускается. В Windows права задаются ACL, их проверить таким
    способом нельзя — выводится предупреждение для администратора.
    """
    if sys.platform.startswith("win"):
        warnings.append(f"{what}: проверьте ACL файла {p} (доступ только учётной записи службы и администраторов).")
        return
    mode = p.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"{what}: файл {p} доступен группе/всем (права {oct(mode & 0o777)}). Установите 600.")


def read_db_url(path: Path) -> str:
    """Только строка подключения из конфигурации — для служебных команд, не обслуживающих сеть
    (отчёт по журналу и т. п.): TLS и лицензию не требуем. Секрет по-прежнему только из url_env/url_file."""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"Не удалось прочитать конфигурацию {path}: {e}") from None
    dbc = data.get("database", {})
    if "url" in dbc:
        raise ConfigError("Строку подключения нельзя хранить в конфигурации (секрет). Используйте database.url_env или database.url_file.")
    base = Path(path).resolve().parent
    if "url_env" in dbc:
        url = os.environ.get(dbc["url_env"])
        if not url:
            raise ConfigError(f"Переменная окружения {dbc['url_env']} не задана.")
        return url
    if "url_file" in dbc:
        p = Path(dbc["url_file"])
        p = p if p.is_absolute() else base / p
        _check_private_file(p, "Строка подключения", [])
        return p.read_text(encoding="utf-8").strip()
    raise ConfigError("Не задан источник строки подключения: database.url_env или database.url_file.")


def load_config(path: Path) -> ServerConfig:
    """Прочитать и проверить server.toml. Любое нарушение — ConfigError с понятным текстом.

    Порядок: строка подключения (только из переменной окружения или отдельного файла — в самом server.toml секретов
    нет); адрес и TLS (без сертификата сервер в сеть не выходит); политика безопасности; резервные копии.
    Относительные пути считаются от папки server.toml.
    """
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"Не удалось прочитать конфигурацию {path}: {e}")
    warnings: list[str] = []
    base = Path(path).resolve().parent

    def rel(value) -> Path:
        """Относительные пути конфигурации — от папки самого файла конфигурации, а не от текущего каталога:
        `vector-server check --config /etc/vector/server.toml` работает одинаково, откуда бы его ни запустили."""
        q = Path(value)
        return q if q.is_absolute() else base / q
    dbc = data.get("database", {})
    if "url" in dbc:
        raise ConfigError("Строку подключения нельзя хранить в конфигурации (секрет). Используйте database.url_env или database.url_file.")
    if "url_env" in dbc:
        url = os.environ.get(dbc["url_env"])
        if not url:
            raise ConfigError(f"Переменная окружения {dbc['url_env']} не задана.")
    elif "url_file" in dbc:
        p = rel(dbc["url_file"])
        _check_private_file(p, "Строка подключения", warnings)
        url = p.read_text(encoding="utf-8").strip()
    else:
        raise ConfigError("Не задан источник строки подключения: database.url_env или database.url_file.")
    backend = sa.engine.make_url(url).get_backend_name()
    if backend == "sqlite":
        warnings.append("SQLite допустим только для тестов и пилота на одном рабочем месте; для работы — PostgreSQL или MS SQL.")
    srv = data.get("server", {})
    lic = data.get("license", {})
    license_file = license_public_key = None
    if lic:
        # Лицензия (D-81): [license] file = "license.json", public_key = "vendor-public.pem". Оба файла публичны
        # (подделку исключает подпись Ed25519 — секретный ключ только у вендора). Битая или подделанная лицензия —
        # сервер не запускается: администратор должен увидеть проблему сразу, а не в разгар рабочего дня.
        if not lic.get("file") or not lic.get("public_key"):
            raise ConfigError("Разделу [license] нужны оба поля: file (файл лицензии) и public_key (публичный ключ вендора).")
        license_file, license_public_key = rel(lic["file"]), rel(lic["public_key"])
        if not license_file.is_file():
            raise ConfigError(f"Файл лицензии не найден: {license_file}")
        if not license_public_key.is_file():
            raise ConfigError(f"Публичный ключ вендора не найден: {license_public_key}")

    pkg = data.get("package", {})
    package_manifest = package_root = None
    if pkg:
        if not pkg.get("manifest"):
            raise ConfigError("Разделу [package] нужен manifest — путь к MANIFEST.sha256 (tools/make_manifest.py).")
        package_manifest = rel(pkg["manifest"])
        package_root = rel(pkg["root"]) if pkg.get("root") else package_manifest.resolve().parent
        if not package_manifest.is_file():
            raise ConfigError(f"Манифест поставки не найден: {package_manifest}")
        if not package_root.is_dir():
            raise ConfigError(f"Корень поставки не найден: {package_root}")

    cfg = ServerConfig(db_url=url, host=srv.get("host", "0.0.0.0"),  # noqa: S104 — см. выше
                       port=int(srv.get("port", 8443)),
                       tls_cert=rel(srv["tls_cert"]) if srv.get("tls_cert") else None,
                       tls_key=rel(srv["tls_key"]) if srv.get("tls_key") else None,
                       dev_insecure_localhost=bool(srv.get("dev_insecure_localhost", False)), warnings=warnings,
                       license_file=license_file, license_public_key=license_public_key,
                       package_manifest=package_manifest, package_root=package_root)
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
        cfg.backup_dir = rel(b["dir"]) if b.get("dir") else None
        cfg.backup_keep = int(b["keep"]) if b.get("keep") else None
    return cfg
