"""Эксплуатация через vector-server: init → check → backup → verify → restore; TLS-сервер; секреты не в argv."""
import os
import socket
import ssl
import subprocess
import sys
import time

import httpx
import pytest

from conftest import PWD

PY = [sys.executable, "-m", "vector.server.cli"]


def run(args, env=None, ok=True):
    """Запустить vector-server в отдельном процессе (как администратор из консоли); ok=True — код возврата обязан быть
    0.
    """
    r = subprocess.run(PY + args, capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=180, stdin=subprocess.DEVNULL)
    if ok:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


@pytest.fixture
def setup(tmp_path):
    """Подготовить установку через CLI: dev-сертификат, URL БД, init, конфиг — как в инструкции по установке."""
    certs = tmp_path / "certs"
    run(["dev-cert", "--dir", str(certs), "--host", "127.0.0.1"])
    url_file = tmp_path / "db.url"
    url_file.write_text(f"sqlite:///{tmp_path / 'vector.sqlite'}")
    url_file.chmod(0o600)
    port = _free_port()
    cfg = tmp_path / "server.toml"
    cfg.write_text(f'''
[server]
host = "127.0.0.1"
port = {port}
tls_cert = "{certs / 'server.pem'}"
tls_key = "{certs / 'server.key'}"

[database]
url_file = "{url_file}"

[backup]
dir = "{tmp_path / 'backups'}"
keep = 2
''', encoding="utf-8")
    (tmp_path / "backups").mkdir()
    return tmp_path, cfg, certs, port


def _free_port():
    """Свободный TCP-порт на 127.0.0.1."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_config_rejects_secrets_and_insecure(tmp_path):
    from vector.server.config import ConfigError, load_config
    bad = tmp_path / "a.toml"
    bad.write_text('[database]\nurl = "postgresql://u:p@h/db"\n')
    with pytest.raises(ConfigError, match="секрет"):
        load_config(bad)
    f = tmp_path / "u"
    f.write_text("sqlite:///x")
    f.chmod(0o644)
    bad.write_text(f'[database]\nurl_file = "{f}"\n[server]\ndev_insecure_localhost = true\nhost="127.0.0.1"\n')
    with pytest.raises(ConfigError, match="600"):
        load_config(bad)
    f.chmod(0o600)
    bad.write_text(f'[database]\nurl_file = "{f}"\n[server]\nhost = "0.0.0.0"\n')
    with pytest.raises(ConfigError, match="tls_cert"):
        load_config(bad)
    bad.write_text(f'[database]\nurl_file = "{f}"\n[server]\ndev_insecure_localhost = true\nhost = "0.0.0.0"\n')
    with pytest.raises(ConfigError, match="127.0.0.1"):
        load_config(bad)


def test_full_ops_cycle(setup):
    tmp, cfg, certs, port = setup
    env = {"VECTOR_ADMIN_PASSWORD": PWD}
    r = run(["init", "--config", str(cfg)], env)
    assert "администратор «admin» создан" in r.stdout
    r = run(["init", "--config", str(cfg)], env, ok=False)
    assert r.returncode != 0 and "не пуста" in r.stderr
    assert "совместима" in run(["check", "--config", str(cfg)]).stdout

    key = tmp / "backup.key"
    run(["gen-backup-key", str(key)])
    assert run(["gen-backup-key", str(key)], ok=False).returncode != 0  # ключ не перезаписывается
    for _ in range(3):
        r = run(["backup", "--config", str(cfg), "--key-file", str(key)])
        assert "Проверка копии: OK" in r.stdout
        time.sleep(1.1)  # имена файлов — с точностью до секунды
    files = sorted((tmp / "backups").glob("*.vbak"))
    assert len(files) == 2  # ротация keep = 2
    r = run(["verify-backup", str(files[-1]), "--key-file", str(key)])
    assert "Результат: OK" in r.stdout and "users: 1" in r.stdout

    # Без ключа — отказ; без фразы — отказ (секрет не из argv).
    assert run(["verify-backup", str(files[-1])], ok=False).returncode != 0

    target = f"sqlite:///{tmp / 'restored.sqlite'}"
    r = run(["restore", str(files[-1]), "--key-file", str(key)], {"VECTOR_RESTORE_DB_URL": target})
    assert "OK — БД готова к запуску" in r.stdout
    r = run(["restore", str(files[-1]), "--key-file", str(key)], {"VECTOR_RESTORE_DB_URL": target}, ok=False)
    assert "не пуста" in r.stderr


def test_tls_server_serves_and_requires_tls(setup):
    tmp, cfg, certs, port = setup
    run(["init", "--config", str(cfg)], {"VECTOR_ADMIN_PASSWORD": PWD})
    proc = subprocess.Popen(PY + ["serve", "--config", str(cfg)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        base = f"https://127.0.0.1:{port}"
        for _ in range(100):
            try:
                with httpx.Client(verify=ssl.create_default_context(cafile=str(certs / "ca.pem"))) as c:
                    if c.get(base + "/api/v1/health").status_code == 200:
                        break
            except httpx.HTTPError:
                time.sleep(0.1)
        with httpx.Client(verify=ssl.create_default_context(cafile=str(certs / "ca.pem"))) as c:
            r = c.post(base + "/api/v1/auth/login", json={"login": "admin", "password": PWD, "device_id": "t"})
            assert r.status_code == 200 and r.json()["status"] == "accepted"
            assert "server" not in {k.lower() for k in r.headers}
        with pytest.raises(httpx.HTTPError):  # без доверия к CA — отказ
            httpx.get(base + "/api/v1/health", verify=True, timeout=3)
        with pytest.raises(httpx.HTTPError):  # открытый HTTP не обслуживается
            httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=3)

        # Клиентский транспорт: доверенный CA → вход; системное доверие (CA не установлен) → TlsError, а не «офлайн».
        from vector.client.transport import HttpTransport, TlsError
        t = HttpTransport(base, str(certs / "ca.pem"))
        try:
            r = t.send({"type": "login", "login": "admin", "password": PWD, "device_id": "t2"})
            assert r["status"] == "accepted" and r["token"]
            assert t.ping() is True
            # Файловые маршруты через настоящий TLS: администратор без бизнес-прав получает отказ в виде JSON,
            # транспорт его разбирает (не принимает за «нет связи» и не падает).
            import hashlib, uuid as _uuid
            up = t.upload(r["token"], str(_uuid.uuid4()), b"data", hashlib.sha256(b"data").hexdigest())
            assert up["status"] == "denied"
            import io as _io
            sink = _io.BytesIO()
            resp = t.download(r["token"], str(_uuid.uuid4()), 1, sink)
            assert resp["status"] == "denied" and sink.getvalue() == b""
        finally:
            t.close()
        t = HttpTransport(base)
        try:
            with pytest.raises(TlsError):
                t.send({"type": "login", "login": "admin", "password": PWD, "device_id": "t3"})
        finally:
            t.close()
        with pytest.raises(ValueError):  # открытый HTTP к удалённому адресу клиент не допускает
            HttpTransport("http://vector.example.local:8080")
    finally:
        proc.terminate()
        out = proc.communicate(timeout=20)[0]
    assert PWD not in out


# ====================================================================== тестовый стенд: demo-seed и сертификат
DEMO_PWD = "Демо-стенд-пароль-1"


def test_demo_seed_only_into_fresh_db(setup):
    """demo-seed наполняет новую базу через обычные команды; повторно и в рабочую базу — отказ, без изменений."""
    tmp, cfg, _, _ = setup
    run(["init", "--config", str(cfg)], env={"VECTOR_ADMIN_PASSWORD": PWD})
    # Неверный пароль администратора и слабый демо-пароль отсекаются ДО создания данных.
    r = run(["demo-seed", "--config", str(cfg)], env={"VECTOR_ADMIN_PASSWORD": "wrong-wrong-wrong", "VECTOR_DEMO_PASSWORD": DEMO_PWD}, ok=False)
    assert r.returncode == 1 and "Вход администратора не выполнен" in r.stderr
    r = run(["demo-seed", "--config", str(cfg)], env={"VECTOR_ADMIN_PASSWORD": PWD, "VECTOR_DEMO_PASSWORD": "short"}, ok=False)
    assert r.returncode == 3 and "Демо-пароль не подходит" in r.stderr and "Traceback" not in r.stderr
    r = run(["demo-seed", "--config", str(cfg)], env={"VECTOR_ADMIN_PASSWORD": PWD, "VECTOR_DEMO_PASSWORD": DEMO_PWD})
    assert "a.smirnova" in r.stdout and "ВЫМЫШЛЕННЫЕ" in r.stdout
    assert DEMO_PWD not in r.stdout + r.stderr and PWD not in r.stdout + r.stderr
    # Данные настоящие: РП входит демо-паролем и видит проект; действия записаны в аудит с устройством demo-seed.
    import sqlalchemy as sa
    from vector.server import db
    from vector.server.service import Service
    eng = db.make_engine(f"sqlite:///{tmp / 'vector.sqlite'}")
    svc = Service(eng)
    lg = svc.handle({"type": "login", "login": "a.smirnova", "password": DEMO_PWD, "device_id": "t"}, "t")
    assert lg["status"] == "accepted" and lg["must_change_password"] is False
    pull = svc.handle({"type": "query", "token": lg["token"], "query": "pull"}, "t")["result"]
    assert any(p["code"] == "П-2026-014" for p in pull["projects"])
    n = svc.handle({"type": "login", "login": "n.sokolov", "password": DEMO_PWD, "device_id": "t"}, "t")
    assert n["must_change_password"] is True
    # Склад и закупка (С4): у кладовщика — остатки и журнал со сторно, у закупщика — потребность АТХ v1.
    wh = svc.handle({"type": "login", "login": "i.belova", "password": DEMO_PWD, "device_id": "t"}, "t")
    sp = svc.handle({"type": "query", "token": wh["token"], "query": "pull"}, "t")["result"]
    assert len(sp["warehouses"]) == 2 and any(m["reverses_id"] for m in sp["stock_movements"])
    # Перемещения: два принятых — по две связанные записи журнала (одно — с актом недостачи № 1), третье ещё
    # «в пути» — одна (отгрузка), со сроком прибытия для справки; четвёртое утрачено (акт об утрате № 1, D-31) —
    # тоже только отгрузка.
    assert sum(1 for m in sp["stock_movements"] if m["transfer_id"]) == 6
    assert sorted(t["status"] for t in sp["stock_transfers"]) == ["in_transit", "lost", "received", "received"]
    assert [(t["loss"]["act_no"], t["loss"]["signature"]["method"]) for t in sp["stock_transfers"] if t["loss"]] == [(1, "password")]
    assert [t["act"]["act_no"] for t in sp["stock_transfers"] if t["act"]] == [1]
    assert [bool(t["expected_on"]) for t in sp["stock_transfers"] if t["status"] == "in_transit"] == [True]
    assert svc.ledger_mismatches() == []
    buyer = svc.handle({"type": "login", "login": "s.morozov", "password": DEMO_PWD, "device_id": "t"}, "t")
    needs = svc.handle({"type": "query", "token": buyer["token"], "query": "pull"}, "t")["result"]["needs"]
    assert [(x["kit_code"], x["calc_version_no"], x["matched_count"]) for x in needs] == [("АТХ", 1, 2)]
    # Заказы поставщикам (D-32, D-33): № 1 ждём (под строку ШУ-1 — видно в потребности), № 2 поставлен частично,
    # № 3 черновик без валюты, № 4 отменён, № 5 на согласовании у v.zaitseva. Размещённые прошли согласование.
    # Склад видит заказы без цен.
    bp = svc.handle({"type": "query", "token": buyer["token"], "query": "pull"}, "t")["result"]
    pos = sorted(bp["purchase_orders"], key=lambda o: o["order_no"])
    assert [(o["order_no"], o["status"], o["delivery"], o["approval"]["state"]) for o in pos] == \
        [(1, "placed", "none", "approve"), (2, "placed", "partial", "approve"), (3, "draft", None, None),
         (4, "cancelled", None, None), (5, "draft", None, "submit")]
    assert [(o["vat_mode"], o["contract_number"]) for o in pos] == \
        [("included", "ШП-3"), ("included", "П-14"), (None, None), (None, None), ("excluded", "П-14")]
    assert pos[4]["vat_totals"]["gross"] == "2348.5000"      # 500 × 3,85 = 1925 без НДС + 22 % = 423,50
    assert len(bp["suppliers"]) == 3 and [s["requisites_complete"] for s in bp["suppliers"]].count(True) == 2
    assert sorted(c["number"] for c in bp["supplier_contracts"]) == ["П-14", "ШП-3"]
    ap = svc.handle({"type": "login", "login": "v.zaitseva", "password": DEMO_PWD, "device_id": "t"}, "t")
    assert ap["status"] == "accepted"
    assert next(ln_["on_order"] for ln_ in needs[0]["lines"] if ln_["code"] == "ШУ-1") == "1.0000"
    assert all(o["total"] is None for o in sp["purchase_orders"]) and len(sp["purchase_orders"]) == 5
    with eng.connect() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(db.audit_log).where(db.audit_log.c.device_id == "demo-seed")).scalar() > 20
        before = c.execute(sa.select(sa.func.count()).select_from(db.customers)).scalar()
    eng.dispose()
    # Повторный запуск: база уже не новая — отказ, ничего не добавлено.
    r = run(["demo-seed", "--config", str(cfg)], env={"VECTOR_ADMIN_PASSWORD": PWD, "VECTOR_DEMO_PASSWORD": DEMO_PWD}, ok=False)
    assert r.returncode == 1 and "только в новую пустую базу" in r.stderr
    eng = db.make_engine(f"sqlite:///{tmp / 'vector.sqlite'}")
    with eng.connect() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(db.customers)).scalar() == before
    eng.dispose()


def test_dev_cert_covers_several_names(tmp_path):
    from cryptography import x509
    run(["dev-cert", "--dir", str(tmp_path / "c"), "--host", "VECTOR-PC", "--host", "192.168.1.20"])
    cert = x509.load_pem_x509_certificate((tmp_path / "c" / "server.pem").read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert set(san.get_values_for_type(x509.DNSName)) == {"VECTOR-PC", "localhost"}
    assert {str(i) for i in san.get_values_for_type(x509.IPAddress)} == {"192.168.1.20", "127.0.0.1"}
    # Повторный запуск в тот же каталог: понятный отказ, и НИ ОДИН файл не перезаписан (ключ и сертификат совпадают).
    before = {p.name: p.read_bytes() for p in (tmp_path / "c").iterdir()}
    r = run(["dev-cert", "--dir", str(tmp_path / "c"), "--host", "x"], ok=False)
    assert r.returncode == 1 and "уже существуют" in r.stderr and "Traceback" not in r.stderr
    assert {p.name: p.read_bytes() for p in (tmp_path / "c").iterdir()} == before


def test_init_checks_password_before_creating_anything(setup):
    """Слабый пароль администратора: код 3, понятное сообщение, база не создана — init можно повторить."""
    tmp, cfg, _, _ = setup
    for weak in ("short", "123456789012"):
        r = run(["init", "--config", str(cfg)], env={"VECTOR_ADMIN_PASSWORD": weak}, ok=False)
        assert r.returncode == 3 and "Ничего не создано" in r.stderr and "Traceback" not in r.stderr
        assert not (tmp / "vector.sqlite").exists() or (tmp / "vector.sqlite").stat().st_size == 0
    r = run(["init", "--config", str(cfg)], env={"VECTOR_ADMIN_PASSWORD": PWD})
    assert "администратор «admin» создан" in r.stdout
    # Повторный init в уже созданную базу — понятный отказ без трассировки.
    r = run(["init", "--config", str(cfg)], env={"VECTOR_ADMIN_PASSWORD": PWD}, ok=False)
    assert r.returncode == 1 and "Traceback" not in r.stderr


def test_migrate_cli_v3_to_v4(setup):
    """vector-server migrate: check отказывает на старой схеме, migrate делает проверенную копию и обновляет схему."""
    import uuid
    from datetime import datetime, timezone

    from vector.server import db
    from vector.server import migrate as mg
    tmp, cfg, _, _ = setup
    eng = db.make_engine(f"sqlite:///{tmp / 'vector.sqlite'}")
    with eng.begin() as c:
        db.metadata.create_all(c, tables=[db.metadata.tables[n] for n in mg.tables_of_version(3)])
        c.execute(db.schema_meta.insert(), [{"key": "schema_version", "value": "3"},
                                            {"key": "installation_id", "value": str(uuid.uuid4())},
                                            {"key": "created_at", "value": datetime.now(timezone.utc).isoformat()}])
    eng.dispose()
    r = run(["check", "--config", str(cfg)], ok=False)
    assert r.returncode == 1 and "vector-server migrate" in r.stderr
    run(["gen-backup-key", str(tmp / "backup.key")])
    r = run(["migrate", "--config", str(cfg), "--key-file", str(tmp / "backup.key")])
    assert f"схема v{db.SCHEMA_VERSION}" in r.stdout and "проверена" in r.stdout
    assert len(list((tmp / "backups").glob("vector-premigrate-v3-*.vbak"))) == 1
    assert "совместима" in run(["check", "--config", str(cfg)]).stdout
    assert "миграция не нужна" in run(["migrate", "--config", str(cfg), "--key-file", str(tmp / "backup.key")]).stdout


def test_relative_paths_resolve_from_config_folder(tmp_path, monkeypatch):
    """url_file / tls / backup.dir относительно папки конфигурации, а не текущего каталога."""
    from vector.server.config import load_config
    d = tmp_path / "stand"
    d.mkdir()
    (d / "db.url").write_text("sqlite:///x.sqlite")
    (d / "db.url").chmod(0o600)
    (d / "server.toml").write_text('[database]\nurl_file = "db.url"\n[server]\ndev_insecure_localhost = true\nhost = "127.0.0.1"\n'
                                   '[backup]\ndir = "backups"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)          # запускаем «не из той папки»
    cfg = load_config(d / "server.toml")
    assert cfg.db_url == "sqlite:///x.sqlite" and cfg.backup_dir == d / "backups"
