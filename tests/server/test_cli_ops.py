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
    r = subprocess.run(PY + args, capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=180, stdin=subprocess.DEVNULL)
    if ok:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


@pytest.fixture
def setup(tmp_path):
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
