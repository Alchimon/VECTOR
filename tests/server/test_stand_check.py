"""Самопроверка тестового стенда (deploy/test-stand/stand_check.py): итог на свежих демо-данных, предупреждения на
«использованных», ошибки (пароль, нет связи, чужой сертификат) и главное — она ничего не меняет на сервере.

HTTPS подменяется in-process сервером (LoopbackTransport) с теми же демо-данными, что ставит установщик стенда;
настоящий прогон по HTTPS против установленного стенда — docs/ops/test-pc.md § 11.
"""
import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa

from vector.client import transport as tr
from vector.server import db, security
from vector.server.demo import _seed
from vector.server.service import Service

ROOT = Path(__file__).resolve().parents[2]
PWD = "Демо-пароль-2026"


def _load():
    """Модуль самопроверки (лежит в deploy/test-stand — каталог с дефисом, обычным import не взять)."""
    spec = importlib.util.spec_from_file_location("stand_check", ROOT / "deploy" / "test-stand" / "stand_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def svc(tmp_path_factory):
    """Сервер с демо-данными стенда (как после setup-test-stand)."""
    security.configure_hasher_for_tests()
    tmp = tmp_path_factory.mktemp("stand")
    eng = db.make_engine(f"sqlite:///{tmp / 'vector.sqlite'}")
    db.create_schema(eng, str(uuid.uuid4()), datetime.now(timezone.utc))
    s = Service(eng)
    s.bootstrap_admin("admin", PWD, "Администратор системы")
    _seed(s, "admin", PWD, PWD)
    yield s
    # Windows держит .sqlite открытым, пока живы соединения пула, и не даёт pytest убрать временный
    # каталог (WinError 32) — закрываем движок явно.
    eng.dispose()


@pytest.fixture
def stand(tmp_path, svc, monkeypatch):
    """Каталог стенда «только клиент» (без server.pem) и HTTPS → in-process сервер; без CA — ошибка TLS."""
    (tmp_path / "certs").mkdir()
    (tmp_path / "certs" / "ca.pem").write_text("test CA", encoding="utf-8")
    (tmp_path / "server-url.txt").write_text("https://localhost:8443\n", encoding="utf-8")
    state = {"online": True}

    class NoCa:
        """Транспорт без сертификата стенда: сервер не проходит проверку."""
        def send(self, msg):
            raise tr.TlsError("Сертификат сервера не прошёл проверку.")

    def http(url, ca_file=None, timeout=15.0):
        """Подмена HttpTransport: свой адрес на каждый транспорт (предел входов — на адрес)."""
        if ca_file is None:
            return NoCa()
        t = tr.LoopbackTransport(svc, f"10.9.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}")
        t.online = state["online"]
        return t
    monkeypatch.setattr(tr, "HttpTransport", http)
    monkeypatch.setenv("VECTOR_DEMO_PASSWORD", PWD)
    return tmp_path, state


def _counts(svc):
    """Отпечаток данных, которые самопроверка не должна трогать."""
    with svc.engine.connect() as c:
        return (c.execute(sa.text("select count(*), sum(read_at is null) from notifications")).one(),
                c.execute(sa.text("select count(*) from role_delegations")).scalar(),
                c.execute(sa.text("select group_concat(status || ':' || version) from purchase_orders")).scalar())


def test_fresh_stand_is_ready_and_check_is_read_only(stand, svc, capsys):
    path, _ = stand
    before = _counts(svc)
    assert _load().main(["--dir", str(path)]) == 0
    out = capsys.readouterr().out
    assert "ИТОГ: стенд готов к проверке" in out and "[ВНИМ.]" not in out and "[ОШИБКА]" not in out
    assert "заказ № 5 на согласовании" in out and "m.lebedev замещает ГИП в П-2026-014" in out
    assert "без сертификата стенда соединение отклоняется" in out
    assert "k.pavlov: операций в «Мои операции» — 1, одна в работе" in out and "техпроцессов: 2 (утверждённых 1)" in out
    assert "рекламаций: 2 (закрыто 0), запросов на изменение: 1, извещений: 0" in out   # раздел 7 (D-39)
    assert "l.egorova: вход, роль «otk»" in out and "на партию (без номера изделия): 1" in out   # раздел 8 (D-40)
    assert "l.egorova (ОТК — по проектам, D-41) работает в проектах: П-2026-014" in out           # только свой проект
    assert "подразделений: 7 (филиалов 2, структур 2, отделов 3)" in out                         # раздел 10 (D-44)
    assert "его дневные отчёты проверяет e.orlova" in out
    assert "v.zaitseva (руководитель коммерческой службы, без роли «Руководитель отдела») проверяет отчёты" in out
    assert "d.kuznetsov: рабочих дней по таймеру — 1" in out                                     # раздел 11 (D-46)
    assert "e.orlova видит запросы d.kuznetsov на фактическое начало дня: 1 (на согласовании — 1)" in out
    assert "графиков работы в компании: 3 (Производство, Пятидневка, Смена наладчиков)" in out     # раздел 12 (D-47)
    assert "календарь на " in out and "загружен (файл ru-" in out                                 # раздел 13 (D-48)
    assert "«Смена наладчиков» производственный календарь не учитывает" in out
    assert "d.kuznetsov: Смена наладчиков:" in out and "подразделения «Отдел АСУ ТП»" in out
    assert "v.zaitseva видит запросы на фактическое окончание дня: 1 (на согласовании — 1)" in out
    assert "v.zaitseva видит отметки в отчётах сотрудников: сверхурочно — 1, работа в выходной или праздник — 1" in out
    assert "настройки компании: повтор напоминания — Через час" in out and "(не заданы — действуют решения" in out
    assert "15. Недельные агрегаты по подразделениям" in out and "агрегаты подразделений" in out
    assert _counts(svc) == before          # ни «прочитано», ни заказы, ни замещения не изменились


def test_wrong_password_stops_after_first_attempt(stand, svc, capsys, monkeypatch):
    path, _ = stand
    monkeypatch.setenv("VECTOR_DEMO_PASSWORD", "не-тот-пароль-2026")
    assert _load().main(["--dir", str(path)]) == 1
    out = capsys.readouterr().out
    assert "проверьте пароль демо-учёток" in out and "блокируется на 15 минут" in out
    assert out.count("[ОШИБКА]") == 1     # одна попытка, а не по попытке на каждую учётку


def test_server_down_and_missing_stand(stand, capsys, tmp_path_factory):
    path, state = stand
    state["online"] = False
    assert _load().main(["--dir", str(path)]) == 1
    assert "сервер не отвечает" in capsys.readouterr().out
    assert _load().main(["--dir", str(tmp_path_factory.mktemp("empty"))]) == 2
    assert "Стенд не найден" in capsys.readouterr().out


def test_used_demo_gives_warnings_not_errors(stand, svc, capsys):
    """После проверки заказа № 5 и «прочитать все» стенд работает, но предупреждает и подсказывает свежий стенд."""
    path, _ = stand
    from vector.client.sync import SyncClient
    from vector.client.vault import MemoryStore

    def session(login):
        c = SyncClient(tr.HttpTransport("u", "ca"), "u", path / login, MemoryStore(), login)
        assert c.login(login, PWD).status == "accepted"
        c.pull()
        return c
    z = session("v.zaitseva")
    o5 = next(o for o in z.list_local("purchase_order") if o["order_no"] == 5)
    assert z.execute("po.return", {"id": o5["id"], "reason": "Проверено"}, o5["version"]).status == "accepted"
    assert session("e.orlova").execute("notification.read", {"all": True}).status == "accepted"
    assert _load().main(["--dir", str(path)]) == 0
    out = capsys.readouterr().out
    assert "заказ № 5 уже не на согласовании" in out
    assert "нет непрочитанного уведомления о заказе № 5" in out and "VECTOR-test2" in out
    assert "ИТОГ: стенд работает; предупреждений 2" in out


def test_cert_days_left_reads_real_certificate(tmp_path):
    """Срок сертификата читается из PEM; мусор — None (проверка пропускается, а не падает)."""
    from vector.server.cli import main as cli
    assert cli(["dev-cert", "--dir", str(tmp_path), "--host", "localhost"]) in (0, None)
    mod = _load()
    assert 28 <= mod.cert_days_left(tmp_path / "server.pem") <= 30
    (tmp_path / "bad.pem").write_text("не сертификат", encoding="utf-8")
    assert mod.cert_days_left(tmp_path / "bad.pem") is None


def test_rate_limited_is_not_reported_as_wrong_password(stand, svc, capsys, monkeypatch):
    """Повтор самопроверки сразу после прошлой: сервер ограничил входы — подсказка «подождите минуту», не «пароль»."""
    path, _ = stand
    monkeypatch.setattr(svc.rate, "allow", lambda *a, **k: False)
    assert _load().main(["--dir", str(path)]) == 1
    out = capsys.readouterr().out
    assert "подождите минуту и повторите самопроверку" in out and "проверьте пароль" not in out
