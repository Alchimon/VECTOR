"""Файлы тестового стенда (deploy/test-stand): форматы, шаблон конфигурации, согласованность с зависимостями.

PowerShell и cmd.exe в песочнице недоступны, поэтому сами Windows-скрипты здесь НЕ исполняются (Class C,
см. docs/ops/test-pc.md). Эти проверки ловят то, что ломает их наверняка: кодировку и переводы строк,
ссылки на несуществующие шаблоны, конфигурацию, которую сервер не примет.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STAND = ROOT / "deploy" / "test-stand"
WIN = STAND / "windows"
LINUX = STAND / "linux"


def test_windows_cmd_files_are_ascii_crlf():
    """cmd.exe читает пакетные файлы в кодовой странице OEM: только ASCII; переводы строк — CRLF."""
    files = sorted(WIN.glob("*.cmd"))
    assert {f.name for f in files} == {"setup.cmd", "start-server.cmd", "start-client.cmd", "backup.cmd", "update.cmd",
                                      "check-stand.cmd"}
    for f in files:
        raw = f.read_bytes()
        raw.decode("ascii")
        assert b"\n" not in raw.replace(b"\r\n", b""), f"{f.name}: LF без CR"


def test_powershell_script_is_utf8_bom_crlf_and_balanced():
    """PowerShell 5.1 читает файл без BOM как ANSI — кириллица превратилась бы в мусор."""
    raw = (WIN / "setup-test-stand.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in raw.replace(b"\r\n", b"")
    text = raw.decode("utf-8-sig")
    code = re.sub(r'"(?:[^"`]|`.)*"|\'[^\']*\'', '""', re.sub(r"<#.*?#>", "", text, flags=re.S))
    code = "\n".join(line.split("#", 1)[0] for line in code.splitlines())
    for o, c in ("{}", "()", "[]"):
        assert code.count(o) == code.count(c), f"скобки {o}{c} не сбалансированы"
    # «$Port:» в строке PowerShell читает как переменную с указанием диска (как $env:) — ошибка разбора
    # InvalidVariableReferenceWithDrive (так упал первый запуск на Windows). Разрешены только области видимости.
    bad_ref = re.findall(r"\$(?!(?:env|script|global|local|private|using):)[A-Za-z_]\w*:", text)
    assert not bad_ref, f"используйте ${{имя}}: перед двоеточием: {bad_ref}"
    # «"$Repo[...]"» — неоднозначная граница имени в строке; пишем «${Repo}[...]».
    assert not re.findall(r'"[^"\r\n]*\$[A-Za-z_]\w*\[', text)
    # Конструкции PowerShell 7, которых нет в 5.1 (встроенной в Windows 10/11).
    for bad in ("??", "?.", " && ", " || "):
        assert bad not in code, f"конструкция PowerShell 7: {bad!r}"
    # Каждый вызов программы стенда — через Run/Probe (проверка кода возврата) или внутри Invoke-PasswordStep
    # (там ErrorActionPreference=Continue и проверка $LASTEXITCODE с повтором при коде 3).
    for m in re.finditer(r"& \$VS ([\w-]+)", text):
        line = text[text.rfind("\n", 0, m.start()) + 1:text.find("\n", m.start())]
        before = text[:m.start()]
        in_pwd_step = before.rfind("Invoke-PasswordStep \"") > before.rfind("\n    }")
        assert "Run " in line or in_pwd_step, line
    assert "$rc -ne 3" in text and '"Continue"' in text  # повтор только на «слабый пароль», остальное — отказ


def test_scripts_reference_existing_templates():
    ps = (WIN / "setup-test-stand.ps1").read_text(encoding="utf-8-sig")
    for name in re.findall(r"\$Templates\\([\w.-]+)", ps):
        assert (WIN / name).is_file(), name
    sh = (LINUX / "setup-test-stand.sh").read_text(encoding="utf-8")
    for name in ("start-server.sh", "start-client.sh", "backup.sh"):
        assert name in sh and (LINUX / name).is_file()
    import os
    for f in LINUX.glob("*.sh"):
        assert b"\r" not in f.read_bytes(), f.name
        if os.name == "posix":                               # бит исполнения — только POSIX (Windows его не хранит)
            assert f.stat().st_mode & 0o111, f"{f.name}: нет права на исполнение"
    assert "deploy/test-stand/constraints.txt" in sh and "deploy\\test-stand\\constraints.txt" in ps
    assert "server.test.toml" in sh and "server.test.toml" in ps


@pytest.mark.parametrize("bind", ["127.0.0.1", "0.0.0.0"])
def test_config_template_is_accepted_by_server(tmp_path, monkeypatch, bind):
    """Шаблон, подставленный так же, как это делают скрипты, проходит проверку конфигурации сервера."""
    from vector.server.config import load_config
    from vector.server.devcert import make_dev_ca_and_cert
    make_dev_ca_and_cert(tmp_path / "certs", ["localhost"])
    (tmp_path / "db.url").write_text(f"sqlite:///{tmp_path / 'data' / 'vector.sqlite'}\n", encoding="utf-8")
    (tmp_path / "db.url").chmod(0o600)
    text = (STAND / "server.test.toml").read_text(encoding="utf-8").replace("@BIND@", bind).replace("@PORT@", "8443")
    (tmp_path / "server.toml").write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)   # ярлыки стенда запускают сервер из каталога стенда: пути относительные
    cfg = load_config(Path("server.toml"))
    assert (cfg.host, cfg.port, cfg.backup_keep) == (bind, 8443, 14)
    assert cfg.db_url.startswith("sqlite:///") and cfg.security.max_file_mb == 200


def test_constraints_fit_project_ranges():
    """Зафиксированные версии стенда не выходят за диапазоны pyproject.toml (в т. ч. PySide6 < 6.12, D-21)."""
    import tomllib
    from packaging.requirements import Requirement
    from packaging.version import Version
    pins = {}
    for line in (STAND / "constraints.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, ver = line.split("==")
            pins[name.lower().replace("_", "-")] = Version(ver)
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    reqs = proj["optional-dependencies"]["server"] + proj["optional-dependencies"]["client"]
    for r in map(Requirement, reqs):
        name = r.name.lower().replace("_", "-")
        assert name in pins, f"{name} не зафиксирован в constraints.txt"
        assert pins[name] in r.specifier, f"{name}=={pins[name]} вне диапазона {r.specifier}"


def test_update_scripts_back_up_and_migrate_before_check():
    """Обновление стенда: новая версия в тот же venv → migrate (копия с проверкой) → check; данные не пересоздаются."""
    for text in ((WIN / "update.cmd").read_text(encoding="ascii"), (LINUX / "update-test-stand.sh").read_text(encoding="utf-8")):
        i_pip, i_mig, i_chk = text.index("pip install"), text.index("migrate --config"), text.index("check --config")
        assert i_pip < i_mig < i_chk
        assert "constraints.txt" in text and "--key-file backup.key" in text
        assert " init " not in text and "demo-seed" not in text     # обновление не создаёт базу заново
