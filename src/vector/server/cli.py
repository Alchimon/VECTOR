"""vector-server — администрирование сервера ВЕКТОР.

Секреты (пароли, парольные фразы, строки подключения) не принимаются аргументами командной строки:
они видны в списке процессов и истории оболочки. Источник — переменные окружения, файлы с
ограниченными правами или интерактивный ввод без эха.
"""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from vector import __version__
from vector.core.errors import Rejected

from . import backup as bk
from . import db
from .config import ConfigError, load_config
from .demo import DemoError


def _keys(args) -> bk.KeySource:
    """Источник ключа резервных копий: файл ключа (--key-file), иначе парольная фраза из VECTOR_BACKUP_PASSPHRASE или
    ввод без эха. В аргументах командной строки секреты не принимаются.
    """
    if getattr(args, "key_file", None):
        return bk.KeySource(key_file=Path(args.key_file))
    pp = os.environ.get("VECTOR_BACKUP_PASSPHRASE")
    if not pp and sys.stdin.isatty():
        pp = getpass.getpass("Парольная фраза резервной копии: ")
    return bk.KeySource(passphrase=pp or None)


# Код возврата «пароль не соответствует политике; ничего не создано» — скрипты стенда по нему спрашивают пароль снова.
EXIT_WEAK_PASSWORD = 3


def _require_good_password(pwd: str, login: str, policy, what: str) -> None:
    """Проверить пароль по политике ДО любых изменений в БД; при отказе — понятное сообщение и код 3."""
    from . import security
    from .validation import _LOGIN_RE
    if not _LOGIN_RE.match(login):
        print(f"Ошибка: недопустимый логин «{login}».", file=sys.stderr)
        raise SystemExit(1)
    problems = security.password_problems(pwd, login, policy)
    if problems:
        print(f"Ошибка: {what} не подходит: {' '.join(problems)} Ничего не создано.", file=sys.stderr)
        raise SystemExit(EXIT_WEAK_PASSWORD)


def cmd_init(args):
    """vector-server init — создать схему в ПУСТОЙ БД и первого администратора.

    Пароль администратора — из VECTOR_ADMIN_PASSWORD или ввод без эха (дважды). Проверка пароля по политике идёт до
    создания схемы, чтобы не оставить базу наполовину инициализированной.
    """
    cfg = load_config(Path(args.config))
    pwd = os.environ.get("VECTOR_ADMIN_PASSWORD")
    if not pwd:
        pwd = getpass.getpass("Пароль первого администратора: ")
        if pwd != getpass.getpass("Повторите пароль: "):
            raise SystemExit("Пароли не совпадают.")
    # Сначала пароль, потом схема: раньше слабый пароль обнаруживался после создания схемы — база оставалась
    # наполовину инициализированной, а повторный init был невозможен.
    _require_good_password(pwd, args.admin_login, cfg.security, "Пароль администратора")
    eng = db.make_engine(cfg.db_url)
    db.create_schema(eng, str(uuid.uuid4()), datetime.now(timezone.utc))
    from .service import Service
    svc = Service(eng, sec=cfg.security)
    svc.bootstrap_admin(args.admin_login, pwd)
    print(f"Схема v{db.SCHEMA_VERSION} создана в {cfg.masked_db_url()}; администратор «{args.admin_login}» создан.")


def cmd_serve(args):
    """vector-server serve — запустить сервер: проверить конфигурацию и версию схемы, затем HTTPS (uvicorn).

    Журнал доступа uvicorn выключен (в нём могли бы оказаться адреса запросов); заголовок Server не отдаётся.
    """
    cfg = load_config(Path(args.config))
    for w in cfg.warnings:
        logging.warning(w)
    eng = db.make_engine(cfg.db_url)
    db.check_schema(eng)
    import uvicorn

    from .api import create_app
    from .service import Service
    app = create_app(Service(eng, sec=cfg.security))
    kw = {} if cfg.dev_insecure_localhost else {"ssl_certfile": str(cfg.tls_cert), "ssl_keyfile": str(cfg.tls_key)}
    uvicorn.run(app, host=cfg.host, port=cfg.port, access_log=False, server_header=False, **kw)


def cmd_backup(args):
    """vector-server backup — зашифрованная резервная копия, её немедленная проверка и удаление старых копий (keep).

    Код выхода 2, если проверка копии не прошла — планировщик заданий должен считать это ошибкой.
    """
    cfg = load_config(Path(args.config))
    eng = db.make_engine(cfg.db_url)
    db.check_schema(eng)
    out = Path(args.out) if args.out else (cfg.backup_dir or Path(".")) / f"vector-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.vbak"
    rep = bk.create_backup(eng, out, _keys(args))
    print(f"Резервная копия: {rep.path} ({rep.size} байт); строк: {sum(rep.counts.values())}")
    vr = bk.verify_backup(rep.path, _keys(args))
    print("Проверка копии:", "OK" if vr.ok else "ОШИБКА: " + "; ".join(vr.problems))
    keep = args.keep or cfg.backup_keep
    if keep:
        for p in bk.prune_backups(rep.path.parent, keep):
            print("Удалена старая копия:", p)
    if not vr.ok:
        raise SystemExit(2)


def cmd_verify(args):
    """vector-server verify-backup — проверить копию: расшифровка, подпись, контрольные суммы, число строк по таблицам.
    """
    vr = bk.verify_backup(Path(args.file), _keys(args))
    h = vr.header
    print(f"Создана: {h['created_at']} · версия {h['app_version']} · схема v{h['schema_version']} · источник {h['source_dialect']}")
    for t, n in vr.counts.items():
        print(f"  {t}: {n}")
    print("Результат:", "OK" if vr.ok else "ОШИБКА: " + "; ".join(vr.problems))
    raise SystemExit(0 if vr.ok else 2)


def cmd_restore(args):
    """vector-server restore — восстановить копию в ПУСТУЮ БД (строка подключения — из переменной окружения).

    Восстановление в непустую БД запрещено (иначе смешались бы данные). После загрузки — сверка; сервер не
    запустится на БД с незавершённым восстановлением.
    """
    url = os.environ.get(args.target_url_env)
    if not url:
        raise SystemExit(f"Переменная {args.target_url_env} со строкой подключения к ПУСТОЙ целевой БД не задана.")
    rep = bk.restore_backup(Path(args.file), _keys(args), db.make_engine(url))
    print("Восстановлено строк:", sum(rep.counts.values()))
    print("Сверка:", "OK — БД готова к запуску" if rep.verified else "ОШИБКА — сервер не запустится: " + "; ".join(rep.problems))
    raise SystemExit(0 if rep.verified else 2)


def cmd_genkey(args):
    """vector-server gen-backup-key — создать файл ключа резервных копий (права 600) и напечатать его отпечаток."""
    fp = bk.generate_key_file(Path(args.path))
    print(f"Ключ создан: {args.path} (отпечаток {fp}). Храните копию ключа отдельно от резервных копий — без него восстановление невозможно.")


def cmd_check(args):
    """vector-server check — проверить конфигурацию, подключение к БД и совместимость версии схемы (ничего не меняет).
    """
    cfg = load_config(Path(args.config))
    print("Конфигурация: OK; БД:", cfg.masked_db_url())
    for w in cfg.warnings:
        print("Предупреждение:", w)
    v = db.check_schema(db.make_engine(cfg.db_url))
    print(f"Схема БД: v{v} — совместима")


def cmd_migrate(args):
    """Обновить схему БД до версии программы: сначала проверенная копия, затем все шаги одной транзакцией."""
    from . import migrate as mg
    cfg = load_config(Path(args.config))
    eng = db.make_engine(cfg.db_url)
    v = mg.current_version(eng)
    if v == db.SCHEMA_VERSION:
        print(f"Схема БД уже v{v} — миграция не нужна.")
        return
    bdir = Path(args.backup_dir) if args.backup_dir else (cfg.backup_dir or Path(args.config).resolve().parent / "backups")
    print(f"Миграция схемы v{v} → v{db.SCHEMA_VERSION}. Сервер должен быть остановлен.")
    print(f"Сначала — резервная копия текущей БД в {bdir} (с проверкой).")
    rep = mg.migrate(eng, bdir, _keys(args))
    print(f"Копия до миграции: {rep.backup_path} — проверена.")
    print(f"Создано таблиц: {len(rep.created_tables)} ({', '.join(rep.created_tables)}).")
    print(f"Готово: схема v{rep.to_version}. Откат — восстановление копии до миграции в пустую БД той версией "
          "программы, с которой она сделана (см. docs/ops/backup-restore.md).")


def cmd_redact_secrets(args):
    """vector-server redact-secrets — зачистить хэши паролей в старых записях журнала операций (см. vector.core.redact).

    Без --apply только считает такие записи и ничего не меняет. Сервер лучше остановить; перед --apply — резервная
    копия (она сама по себе зашифрована, но после зачистки старые копии всё ещё содержат прежние хэши — их срок
    хранения определяет ротация копий).
    """
    from .service import Service
    cfg = load_config(Path(args.config))
    eng = db.make_engine(cfg.db_url)
    db.check_schema(eng)
    n = Service(eng).redact_legacy_secret_hashes(apply=args.apply)
    if args.apply:
        print(f"Зачищено записей журнала операций: {n}.")
    else:
        print(f"Найдено записей с хэшем, включавшим пароль: {n}. Ничего не изменено; для зачистки — --apply.")


def cmd_devcert(args):
    """vector-server dev-cert — тестовый CA и сертификат сервера на 30 дней (только пилот/разработка; для работы —
    сертификат CA компании).
    """
    from .devcert import make_dev_ca_and_cert
    paths = make_dev_ca_and_cert(Path(args.dir), args.host or ["localhost"])
    print("Создан ТЕСТОВЫЙ CA и сертификат (только для пилота/разработки):")
    for k, v in paths.items():
        print(f"  {k}: {v}")


def cmd_demo(args):
    """Тестовый стенд: наполнить НОВУЮ базу вымышленными данными (заказчики, тендеры, проект, документы, задачи).

    Пароли не принимаются аргументами: VECTOR_ADMIN_PASSWORD / VECTOR_DEMO_PASSWORD или ввод без эха.
    До начала проверяется всё, что можно проверить заранее (база пустая, пароль администратора верный,
    демо-пароль проходит политику), — чтобы не оставить базу заполненной наполовину.
    """
    from . import demo
    from .service import Service
    cfg = load_config(Path(args.config))
    svc = Service(db.make_engine(cfg.db_url), sec=cfg.security)
    demo.check_empty(svc)
    admin_pwd = os.environ.get("VECTOR_ADMIN_PASSWORD") or getpass.getpass(f"Пароль администратора «{args.admin_login}»: ")
    demo_pwd = os.environ.get("VECTOR_DEMO_PASSWORD")
    if not demo_pwd:
        demo_pwd = getpass.getpass("Пароль для всех демо-учёток (не короче 12 символов): ")
        if demo_pwd != getpass.getpass("Повторите пароль: "):
            raise SystemExit("Пароли не совпадают.")
    for lg, *_ in demo.ACCOUNTS:
        _require_good_password(demo_pwd, lg, cfg.security, "Демо-пароль")
    r = svc.handle({"type": "login", "login": args.admin_login, "password": admin_pwd, "device_id": demo.DEVICE}, demo.DEVICE)
    if r.get("status") != "accepted":
        raise demo.DemoError(f"Вход администратора не выполнен: {r.get('message_ru') or r.get('status')}")
    svc.handle({"type": "logout", "token": r["token"]})
    try:
        demo.seed_demo(svc, args.admin_login, admin_pwd, demo_pwd)
    except demo.DemoError as e:
        raise demo.DemoError(f"{e}. База заполнена частично: удалите тестовую базу и повторите init и demo-seed.")
    print("Демо-данные созданы (ВЫМЫШЛЕННЫЕ, только для теста). Учётные записи — пароль тот, что вы задали:")
    for lg, name, _role, label in demo.ACCOUNTS:
        print(f"  {lg:<12} {name:<18} {label}")


def main(argv=None):
    """Точка входа vector-server: разбор подкоманд и единая обработка ожидаемых ошибок.

    Ожидаемые ошибки (конфигурация, копия, миграция, отказ) печатаются одной строкой «Ошибка: …» с кодом выхода 1 —
    без трассировки стека. Неожиданные ошибки программы печатаются полностью.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="vector-server", description=f"Сервер ВЕКТОР {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init", help="создать схему в пустой БД и первого администратора")
    s.add_argument("--config", required=True); s.add_argument("--admin-login", default="admin"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("serve", help="запустить сервер"); s.add_argument("--config", required=True); s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("check", help="проверить конфигурацию и схему"); s.add_argument("--config", required=True); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("backup", help="зашифрованная резервная копия + проверка + ротация")
    s.add_argument("--config", required=True); s.add_argument("--out"); s.add_argument("--key-file"); s.add_argument("--keep", type=int); s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("verify-backup", help="проверить целостность и подлинность копии")
    s.add_argument("file"); s.add_argument("--key-file"); s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("restore", help="восстановить копию в ПУСТУЮ БД (любой поддерживаемой СУБД)")
    s.add_argument("file"); s.add_argument("--key-file"); s.add_argument("--target-url-env", default="VECTOR_RESTORE_DB_URL"); s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("migrate", help="обновить схему БД до версии программы (сначала проверенная копия)")
    s.add_argument("--config", required=True); s.add_argument("--key-file"); s.add_argument("--backup-dir")
    s.set_defaults(fn=cmd_migrate)
    s = sub.add_parser("gen-backup-key", help="создать файл ключа резервных копий"); s.add_argument("path"); s.set_defaults(fn=cmd_genkey)
    s = sub.add_parser("dev-cert", help="тестовый CA и TLS-сертификат для пилота")
    s.add_argument("--dir", required=True)
    s.add_argument("--host", action="append", help="имя или IP сервера; можно несколько раз (localhost добавляется всегда)")
    s.set_defaults(fn=cmd_devcert)
    s = sub.add_parser("redact-secrets", help="зачистить хэши паролей в старых записях журнала операций")
    s.add_argument("--config", required=True); s.add_argument("--apply", action="store_true"); s.set_defaults(fn=cmd_redact_secrets)
    s = sub.add_parser("demo-seed", help="ТОЛЬКО ДЛЯ ТЕСТА: вымышленные демо-данные в новую пустую БД")
    s.add_argument("--config", required=True); s.add_argument("--admin-login", default="admin"); s.set_defaults(fn=cmd_demo)
    args = p.parse_args(argv)
    try:
        args.fn(args)
    except Rejected as e:
        print(f"Ошибка: {e.message_ru}", file=sys.stderr)
        raise SystemExit(1)
    except (ConfigError, bk.BackupError, RuntimeError, ValueError, DemoError, FileExistsError) as e:   # MigrationError ⊂ RuntimeError
        print(f"Ошибка: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
