"""Самопроверка тестового стенда ВЕКТОР: готов ли стенд к ручной проверке (docs/ops/test-pc.md, чек-лист П-1…П-13).

Запускается ПОСЛЕ установки, при запущенном сервере стенда, Python-ом из окружения стенда:

    Windows:  check-stand.cmd                         (или check-stand.cmd D:\\VECTOR-test)
    Linux:    bash deploy/test-stand/linux/check-stand.sh [--dir ~/vector-test]

Только ЧИТАЕТ: входит демо-учётками по HTTPS (тем же транспортом и с тем же сертификатом CA, что и клиент), получает
обновление (pull) и сверяет его с ожидаемым. Ничего не создаёт и не меняет — ни заказы, ни замещения, ни
«прочитано»; локальный кэш клиента — во временном каталоге, который удаляется в конце. На сервере остаются только
записи о входах в журнале аудита.

Пароль демо-учёток спрашивается без эха (или берётся из VECTOR_DEMO_PASSWORD) и никуда не записывается.
Код выхода: 0 — готов (предупреждения допустимы), 1 — есть ошибки, 2 — неверные параметры/нет стенда.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Демо-учётки (vector.server.demo) и роль, которую должен вернуть сервер. n.sokolov не входит: у него при первом
# входе обязательная смена пароля — самопроверка не должна её «съесть» до тестировщика.
DEMO = [("a.smirnova", "rp"), ("p.volkov", "gip"), ("e.orlova", "dept_head"), ("d.kuznetsov", "engineer"),
        ("m.lebedev", "lead_engineer"), ("o.nikitina", "accountant"), ("i.belova", "warehouse"),
        ("s.morozov", "procurement"), ("v.zaitseva", "po_approver")]
# Учётки технологии и производства (D-36). Появились в демо этапа 5: на стенде, обновлённом со старой версии, их нет —
# это предупреждение, а не ошибка (новые демо-данные при обновлении не добавляются).
DEMO_D36 = [("t.fedorova", "technologist"), ("a.grishin", "technologist"), ("k.pavlov", "production"),
            ("r.sergeev", "production")]
PROJECT = "П-2026-014"   # проект демо-данных, где p.volkov — ГИП, а m.lebedev его замещает (D-35)
FRESH_HINT = ("так бывает на стенде, обновлённом со старой версии (новые демо-данные при обновлении не добавляются), "
              "или если демо уже проверяли. Для этой проверки поставьте свежий стенд в новый каталог: "
              "setup.cmd -Dir %USERPROFILE%\\VECTOR-test2 (Linux: --dir ~/vector-test2) — test-pc.md § 7а")


class Report:
    """Накопитель результатов: OK / ПРЕДУПРЕЖДЕНИЕ / ОШИБКА — по строке на проверку."""

    def __init__(self):
        """Пустой отчёт."""
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def ok(self, text: str):
        """Проверка пройдена."""
        print(f"  [OK]    {text}")

    def warn(self, text: str):
        """Стенд работает, но часть сценариев проверить не получится (или скоро не получится)."""
        print(f"  [ВНИМ.] {text}")
        self.warnings.append(text)

    def fail(self, text: str):
        """Стенд не готов: ручная проверка упрётся в эту ошибку."""
        print(f"  [ОШИБКА] {text}")
        self.errors.append(text)


def default_dir() -> Path:
    """Каталог стенда по умолчанию — тот же, что у установщиков."""
    if os.name == "nt":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "VECTOR-test"
    return Path.home() / "vector-test"


def cert_days_left(pem: Path) -> int | None:
    """Сколько полных дней осталось у сертификата сервера (None — не удалось прочитать)."""
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(pem.read_bytes())
        end = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=timezone.utc)
        return (end - datetime.now(timezone.utc)).days
    except Exception:  # noqa: BLE001 — самопроверка не должна падать из-за формата файла
        return None


def main(argv: list[str] | None = None) -> int:
    """Разобрать параметры, выполнить проверки и напечатать итог."""
    ap = argparse.ArgumentParser(description="Самопроверка тестового стенда ВЕКТОР (только чтение).")
    ap.add_argument("--dir", type=Path, default=default_dir(), help="каталог стенда")
    args = ap.parse_args(argv)
    stand = args.dir.expanduser()
    try:
        from vector.client.sync import SyncClient
        from vector.client.transport import HttpTransport
        from vector.client.vault import MemoryStore
    except ImportError:
        print("Запустите Python из окружения стенда (venv): check-stand.cmd / check-stand.sh делают это сами.")
        return 2

    url_file, ca, srv_cert = stand / "server-url.txt", stand / "certs" / "ca.pem", stand / "certs" / "server.pem"
    if not url_file.exists() or not ca.exists():
        print(f"Стенд не найден в {stand} (нет server-url.txt или certs\\ca.pem). Укажите каталог: --dir <путь>.")
        return 2
    url = url_file.read_text(encoding="utf-8-sig").strip()
    rep = Report()
    print(f"Самопроверка стенда: {stand}\nСервер: {url}\n")

    print("1. Сертификат и связь")
    days = cert_days_left(srv_cert) if srv_cert.exists() else None
    if days is None:
        rep.ok("сертификат сервера — на другом ПК (стенд «только клиент»)" if not srv_cert.exists()
               else "срок сертификата прочитать не удалось — пропущено")
    elif days < 0:
        rep.fail("тестовый сертификат истёк — поставьте стенд заново (§ 8, § 4)")
    elif days < 5:
        rep.warn(f"тестовый сертификат истекает через {days} дн. — после этого стенд нужно поставить заново")
    else:
        rep.ok(f"тестовый сертификат действует ещё {days} дн.")

    tmp = tempfile.TemporaryDirectory(prefix="vector-check-")

    def client(ca_file):
        """Клиент с временным кэшем (удаляется в конце) и секретом устройства в памяти."""
        return SyncClient(HttpTransport(url, ca_file), url, Path(tempfile.mkdtemp(dir=tmp.name)), MemoryStore(),
                          str(uuid.uuid4()))

    pwd = os.environ.get("VECTOR_DEMO_PASSWORD") or getpass.getpass("Пароль демо-учёток (ввод не отображается): ")
    # Разбор первого входа: ошибка TLS (status error) — сертификат не тот; нет связи (refused_offline /
    # offline_unlocked — клиент пытался войти без сервера) — сервер не запущен; ответ с отказом — пароль. Дальше не идём: каждая неверная попытка считается сервером
    # (5 подряд — учётка блокируется на 15 минут).
    first = client(str(ca)).login(DEMO[0][0], pwd)
    if first.status == "error":
        rep.fail(f"сертификат сервера не принят: {first.message_ru} — сервер выпущен другим стендом или "
                 "сертификат пересоздан; возьмите certs\\ca.pem с ПК сервера (§ 7)")
        return finish(rep, tmp)
    if first.status in ("refused_offline", "offline_unlocked"):
        rep.fail("сервер не отвечает — запустите ярлык «ВЕКТОР — сервер (тест)» и дождитесь строки "
                 "«Uvicorn running on …», затем повторите самопроверку")
        return finish(rep, tmp)
    if first.status != "accepted" and (first.response or {}).get("code") == "rate_limited":
        # Самопроверка входит ~15 раз, сервер пускает 20 входов в минуту с адреса — повтор сразу после прошлого прогона.
        rep.fail("сервер временно ограничил входы с этого компьютера (недавно уже входили много раз) — подождите "
                 "минуту и повторите самопроверку; пароль тут ни при чём")
        return finish(rep, tmp)
    if first.status != "accepted":
        rep.fail(f"вход {DEMO[0][0]} не прошёл: {first.message_ru} — проверьте пароль демо-учёток (тот, что задали "
                 "при установке). Внимание: после 5 неверных паролей подряд учётка блокируется на 15 минут")
        return finish(rep, tmp)
    rep.ok("сервер отвечает по HTTPS, сертификат стенда принят")
    bad = client(None).login(DEMO[0][0], pwd)
    if bad.status == "accepted":
        rep.fail("соединение БЕЗ сертификата стенда прошло — проверка сертификата не работает")
    else:
        rep.ok("без сертификата стенда соединение отклоняется (так и должно быть)")

    print("\n2. Демо-учётки")
    sessions = {}
    for login, role in DEMO:
        c = client(str(ca))
        r = c.login(login, pwd)
        if r.status != "accepted":
            rep.fail(f"{login}: вход не прошёл ({r.message_ru}); стенд без демо-данных (-NoDemo)?")
            continue
        c.pull()
        roles = (c.get_meta("me") or {}).get("own_roles") or (c.get_meta("me") or {}).get("roles") or []
        if role in roles:
            rep.ok(f"{login}: вход и обновление, роль «{role}»")
        else:
            rep.fail(f"{login}: нет роли «{role}» (роли: {', '.join(roles) or 'нет'})")
        sessions[login] = c
    if len(sessions) < len(DEMO):
        return finish(rep, tmp)

    print("\n3. Демо-данные для сценариев")
    s = sessions["s.morozov"]
    orders = {o.get("order_no"): o for o in s.list_local("purchase_order")}
    rep.ok(f"заказов поставщикам: {len(orders)}") if orders else rep.fail("заказов поставщикам нет (П-8…П-10)")
    o5 = orders.get(5)
    on_approval = bool(o5 and o5.get("status") == "draft" and (o5.get("approval") or {}).get("state") == "submit")
    if on_approval:
        rep.ok("заказ № 5 на согласовании (П-9, П-10, П-11)")
    else:
        rep.warn("заказ № 5 уже не на согласовании — «Согласовать / Вернуть» проверьте на своём заказе "
                 "(s.morozov → «На согласование»)")

    print("\n4. Замещение ролей в проекте и уведомления (D-35, П-11)")
    notes = {k: sessions[k].get_meta("notifications") for k in ("e.orlova", "v.zaitseva", "m.lebedev")}
    if any(v is None for v in notes.values()):
        rep.fail("сервер не присылает уведомления — версия сервера старше клиента; обновите стенд (§ 7а)")
        return finish(rep, tmp)
    sub = [n for n in notes["e.orlova"] if n["kind"] == "po_submitted" and "№ 5" in n["title"] and not n["read_at"]]
    if sub:
        rep.ok(f"e.orlova: непрочитанное «{sub[0]['title']}»; всего уведомлений {len(notes['e.orlova'])}")
    else:
        rep.warn("e.orlova: нет непрочитанного уведомления о заказе № 5 — " + FRESH_HINT)
    if any(n["kind"] == "po_submitted" for n in notes["v.zaitseva"]):
        rep.ok("v.zaitseva: уведомления о заказах на согласовании есть")
    else:
        rep.warn("v.zaitseva: уведомлений нет — " + FRESH_HINT)
    me = sessions["m.lebedev"].get_meta("me") or {}
    dpr = [d for d in me.get("delegated_project_roles") or [] if d.get("role") == "gip"
           and d.get("project_code") == PROJECT]
    if dpr:
        rep.ok(f"m.lebedev замещает ГИП в {PROJECT} до {dpr[0].get('valid_until')}")
    else:
        rep.warn(f"m.lebedev не замещает ГИП в {PROJECT} (демо-замещение — на 7 дней со дня установки) — " + FRESH_HINT)
    smirnova = sessions["a.smirnova"]
    rps = {a["project_id"] for a in smirnova.get_meta("assignments") or []
           if a.get("user_id") == smirnova.user_id and a.get("project_role") == "rp"}
    if len(rps) >= 2:
        rep.ok(f"a.smirnova — РП в {len(rps)} проектах: в форме замещения будут пункты «РП в проекте …»")
    else:
        rep.warn(f"a.smirnova — РП в {len(rps)} проектах (в демо — 2)")

    print("\n5. Технология и производство (D-36, П-12)")
    prod, missing = {}, []
    for login, role in DEMO_D36:
        c = client(str(ca))
        r = c.login(login, pwd)
        if r.status != "accepted":
            missing.append(login)
            continue
        c.pull()
        roles = (c.get_meta("me") or {}).get("roles") or []
        rep.ok(f"{login}: вход, роль «{role}»") if role in roles else rep.fail(f"{login}: нет роли «{role}»")
        prod[login] = c
    if missing:   # одно предупреждение на все отсутствующие учётки — не четыре одинаковых
        rep.warn(f"нет учёток {', '.join(missing)} — " + FRESH_HINT)
    if "k.pavlov" in prod:
        mine = [o for o in prod["k.pavlov"].list_local("wo_operation")
                if o.get("assignee_id") == prod["k.pavlov"].user_id and o.get("state") in ("pending", "running", "paused")]
        if any(o.get("state") == "running" for o in mine):
            rep.ok(f"k.pavlov: операций в «Мои операции» — {len(mine)}, одна в работе (таймер идёт)")
        else:
            rep.warn("k.pavlov: нет операции в работе — демо-наряд уже проверяли; начните любую назначенную операцию")
    tps = smirnova.list_local("tech_process")
    if tps:
        rep.ok(f"техпроцессов: {len(tps)} (утверждённых {sum(t.get('status') == 'approved' for t in tps)}), "
               f"нарядов: {len(smirnova.list_local('work_order'))}")
    elif prod:
        rep.warn("техпроцессов нет — " + FRESH_HINT)
    return finish(rep, tmp)


def finish(rep: Report, tmp=None) -> int:
    """Удалить временный кэш и напечатать итог; код выхода — 1 при ошибках."""
    if tmp is not None:
        tmp.cleanup()
    print()
    if rep.errors:
        print(f"ИТОГ: стенд НЕ готов — ошибок {len(rep.errors)}. Что прислать разработчику — test-pc.md § 9.")
        return 1
    if rep.warnings:
        print(f"ИТОГ: стенд работает; предупреждений {len(rep.warnings)} — часть сценариев на этих данных не проверить "
              "(см. выше).")
    else:
        print("ИТОГ: стенд готов к проверке (чек-лист владельца, П-1…П-13; сценарии — test-pc.md § 6).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
