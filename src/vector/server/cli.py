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

from . import backup as bk
from . import db
from .config import ConfigError, load_config
from .demo import DemoError


def _keys(args) -> bk.KeySource:
    if getattr(args, "key_file", None):
        return bk.KeySource(key_file=Path(args.key_file))
    pp = os.environ.get("VECTOR_BACKUP_PASSPHRASE")
    if not pp and sys.stdin.isatty():
        pp = getpass.getpass("Парольная фраза резервной копии: ")
    return bk.KeySource(passphrase=pp or None)


def cmd_init(args):
    cfg = load_config(Path(args.config))
    eng = db.make_engine(cfg.db_url)
    db.create_schema(eng, str(uuid.uuid4()), datetime.now(timezone.utc))
    from .service import Service
    svc = Service(eng, sec=cfg.security)
    pwd = os.environ.get("VECTOR_ADMIN_PASSWORD")
    if not pwd:
        pwd = getpass.getpass("Пароль первого администратора: ")
        if pwd != getpass.getpass("Повторите пароль: "):
            raise SystemExit("Пароли не совпадают.")
    svc.bootstrap_admin(args.admin_login, pwd)
    print(f"Схема v{db.SCHEMA_VERSION} создана в {cfg.masked_db_url()}; администратор «{args.admin_login}» создан.")


def cmd_serve(args):
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
    vr = bk.verify_backup(Path(args.file), _keys(args))
    h = vr.header
    print(f"Создана: {h['created_at']} · версия {h['app_version']} · схема v{h['schema_version']} · источник {h['source_dialect']}")
    for t, n in vr.counts.items():
        print(f"  {t}: {n}")
    print("Результат:", "OK" if vr.ok else "ОШИБКА: " + "; ".join(vr.problems))
    raise SystemExit(0 if vr.ok else 2)


def cmd_restore(args):
    url = os.environ.get(args.target_url_env)
    if not url:
        raise SystemExit(f"Переменная {args.target_url_env} со строкой подключения к ПУСТОЙ целевой БД не задана.")
    rep = bk.restore_backup(Path(args.file), _keys(args), db.make_engine(url))
    print("Восстановлено строк:", sum(rep.counts.values()))
    print("Сверка:", "OK — БД готова к запуску" if rep.verified else "ОШИБКА — сервер не запустится: " + "; ".join(rep.problems))
    raise SystemExit(0 if rep.verified else 2)


def cmd_genkey(args):
    fp = bk.generate_key_file(Path(args.path))
    print(f"Ключ создан: {args.path} (отпечаток {fp}). Храните копию ключа отдельно от резервных копий — без него восстановление невозможно.")


def cmd_check(args):
    cfg = load_config(Path(args.config))
    print("Конфигурация: OK; БД:", cfg.masked_db_url())
    for w in cfg.warnings:
        print("Предупреждение:", w)
    v = db.check_schema(db.make_engine(cfg.db_url))
    print(f"Схема БД: v{v} — совместима")


def cmd_devcert(args):
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
    from . import demo, security
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
        problems = security.password_problems(demo_pwd, lg, cfg.security)
        if problems:
            raise demo.DemoError("Демо-пароль не подходит: " + " ".join(problems))
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
    s = sub.add_parser("gen-backup-key", help="создать файл ключа резервных копий"); s.add_argument("path"); s.set_defaults(fn=cmd_genkey)
    s = sub.add_parser("dev-cert", help="тестовый CA и TLS-сертификат для пилота")
    s.add_argument("--dir", required=True)
    s.add_argument("--host", action="append", help="имя или IP сервера; можно несколько раз (localhost добавляется всегда)")
    s.set_defaults(fn=cmd_devcert)
    s = sub.add_parser("demo-seed", help="ТОЛЬКО ДЛЯ ТЕСТА: вымышленные демо-данные в новую пустую БД")
    s.add_argument("--config", required=True); s.add_argument("--admin-login", default="admin"); s.set_defaults(fn=cmd_demo)
    args = p.parse_args(argv)
    try:
        args.fn(args)
    except (ConfigError, bk.BackupError, RuntimeError, ValueError, DemoError, FileExistsError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
