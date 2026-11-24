"""Самопроверка тестового стенда ВЕКТОР: готов ли стенд к ручной проверке (docs/ops/test-pc.md, чек-лист П-1…П-28).

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
# Учётка отдела технического контроля (D-40) — тоже только в демо новой версии.
DEMO_D40 = [("l.egorova", "otk")]
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

    print("\n6. Изделия и паспорта (D-38, П-14)")
    insts = smirnova.list_local("product_instance")
    if insts:
        bad = [i for i in insts if i.get("composition_ok") is False]
        pps = smirnova.list_local("passport")
        rep.ok(f"экземпляров изделий: {len(insts)}, паспортов-черновиков: {len(pps)}, "
               f"передано заказчику: {sum(i.get('status') == 'handed_over' for i in insts)}")
        if bad:
            rep.fail(f"состав экземпляра не совпадает с контрольной суммой (SHA-256): {len(bad)} — сообщите разработчику")
    else:
        rep.warn("экземпляров изделий нет — " + FRESH_HINT)

    print("\n7. Качество: рекламации, CAPA, запросы и извещения об изменении (D-39, П-15)")
    cls = smirnova.list_local("claim")
    if cls:
        ecrs, ecos = smirnova.list_local("change_request"), smirnova.list_local("change_order")
        rep.ok(f"рекламаций: {len(cls)} (закрыто {sum(c.get('status') == 'closed' for c in cls)}), "
               f"запросов на изменение: {len(ecrs)}, извещений: {len(ecos)}")
    else:
        rep.warn("рекламаций нет — " + FRESH_HINT)

    print("\n8. Рекламации без номера изделия, сроки ответа, ОТК — отдел технического контроля, по проектам (D-40, D-41, П-16)")
    otk = None
    for login, role in DEMO_D40:
        c = client(str(ca))
        r = c.login(login, pwd)
        if r.status != "accepted":
            rep.warn(f"нет учётки {login} — " + FRESH_HINT)
            continue
        c.pull()
        roles = (c.get_meta("me") or {}).get("roles") or []
        rep.ok(f"{login}: вход, роль «{role}»") if role in roles else rep.fail(f"{login}: нет роли «{role}»")
        otk = c
    if otk is not None:
        # ОТК работает по проектам (D-41, ответ на A-47): l.egorova видит только проекты, куда её назначили участником.
        qp = [x.get("code") for x in otk.get_meta("quality_projects") or []]
        if not qp:
            # Без проекта рекламаций ОТК не видит — остальные проверки раздела пропускаются.
            rep.warn("l.egorova не назначена ни в один проект — ОТК работает только в проектах, куда сотрудника назначил "
                     "участником ГИП или РП (D-41); на обновлённом стенде назначьте её в проект П-2026-014 (a.smirnova → "
                     "«Проекты» → «Команда»)")
        else:
            rep.ok(f"l.egorova (ОТК — по проектам, D-41) работает в проектах: {', '.join(qp)}")
            mine = otk.list_local("claim")
            batch = [c for c in mine if c.get("scope") == "batch"]
            due = [c for c in mine if c.get("response_due")]
            if batch and due:
                rep.ok(f"l.egorova видит рекламаций: {len(mine)}; на партию (без номера изделия): {len(batch)}; "
                       f"со сроком ответа заказчику: {len(due)}")
            else:
                rep.warn("нет рекламации на партию или со сроком ответа — " + FRESH_HINT)

    print("\n9. Рабочее время: дневные отчёты, собранный программой отчёт и проверка руководителем отдела (D-42, D-43, П-17)")
    check_worktime(rep, sessions)
    print("\n10. Оргструктура: подразделения, руководители и маршрут дневных отчётов (D-44, П-19)")
    check_org(rep, sessions)
    print("\n11. Таймер рабочего дня и фактическое начало дня (D-46, П-20)")
    check_workday(rep, sessions)
    print("\n12. Графики работы и забытое «Завершить» (D-47, П-21)")
    check_schedule(rep, sessions)
    print("\n13. Производственный календарь: праздники, сокращённые дни, переносы (D-48, П-22)")
    check_calendar(rep, sessions)
    print("\n14. Сверхурочно, работа в выходной и праздник; настройки напоминания (D-49, П-23)")
    check_day_marks(rep, sessions)
    print("\n15. Недельные агрегаты по подразделениям без персональных действий (D-50, П-24)")
    check_weekly_metrics(rep, sessions)
    print("\n16. Пусконаладка и приёмочные испытания: шаблон чек-листа, запрос ПНР, идущий FAT с замечаниями (D-67, D-68, П-25)")
    check_pnr_fat(rep, sessions)
    print("\n17. Документы доставки: формы с плейсхолдерами и выписанный документ (D-69, П-26)")
    check_delivery_docs(rep, sessions)
    print("\n18. Нормы трудоёмкости: агрегированный диапазон и ручное утверждение (D-72, П-27)")
    check_norms(rep, sessions)
    print("\n19. Сверхурочные по ТК РФ: категории, основание, решение; норма учётного периода (D-74, D-75, П-28)")
    check_overtime_contour(rep, sessions, prod)
    return finish(rep, tmp)


def check_worktime(rep: Report, sessions: dict) -> None:
    """Раздел 9: руководитель отдела e.orlova может проверять отчёты и видит отправленные; у d.kuznetsov есть свои
    отчёты; среди них — собранный программой «На самопроверке» (D-43), руководителю он не виден. Только чтение (pull
    уже сделан в разделе 2; сборку отчётов запускает только синхронизация клиента) — самопроверка ничего не меняет."""
    head, eng = sessions.get("e.orlova"), sessions.get("d.kuznetsov")
    if head is None or eng is None:
        rep.warn("нет входа e.orlova или d.kuznetsov — раздел «Рабочее время» не проверить")
        return
    if head.get_meta("worktime_reviewer") is not True:
        rep.fail("e.orlova (руководитель отдела) не может проверять дневные отчёты — сервер старой версии? "
                 "Обновите стенд (update.cmd) и повторите")
        return
    me = head.user_id
    waiting = [r for r in head.list_local("daily_report") if r.get("status") == "submitted" and r.get("user_id") != me]
    own = eng.list_local("daily_report")
    if waiting and own:
        rep.ok(f"e.orlova: отчётов на проверке — {len(waiting)}; у d.kuznetsov своих отчётов — {len(own)}")
    else:
        rep.warn("нет дневных отчётов на проверке — " + FRESH_HINT)
    auto = [r for r in own if r.get("status") == "self_check" and r.get("origin") == "auto"]
    if auto and any(r.get("status") == "self_check" for r in head.list_local("daily_report") if r.get("user_id") != me):
        rep.fail("руководитель отдела видит чужой отчёт «На самопроверке» — он должен быть виден только автору")
    elif auto:
        rep.ok(f"d.kuznetsov: собранных программой отчётов на самопроверке — {len(auto)}; руководителю не видны")
    else:
        rep.warn("нет отчёта, собранного программой (D-43) — " + FRESH_HINT)


def check_org(rep: Report, sessions: dict) -> None:
    """Раздел 10 (D-44): оргструктура пришла в снимок; d.kuznetsov включён в подразделение и его отчёты проверяет
    e.orlova (руководитель вышестоящей дирекции — в его отделе руководителя нет). Только чтение."""
    eng, head = sessions.get("d.kuznetsov"), sessions.get("e.orlova")
    if eng is None or head is None:
        rep.warn("нет входа d.kuznetsov или e.orlova — раздел «Оргструктура» не проверить")
        return
    units = eng.get_meta("org_units")
    if units is None:
        rep.fail("в снимке нет оргструктуры — сервер старой версии? Обновите стенд (update.cmd) и повторите")
        return
    if not units:
        rep.warn("оргструктура не заведена (её заводит admin: «Оргструктура» → «Новое подразделение») — " + FRESH_HINT)
        return
    kinds = {k: sum(1 for u in units if u.get("kind") == k and u.get("active")) for k in ("branch", "structure", "department")}
    rep.ok(f"подразделений: {len(units)} (филиалов {kinds['branch']}, структур {kinds['structure']}, "
           f"отделов {kinds['department']})")
    me = eng.get_meta("org_me") or {}
    if not me.get("unit_id"):
        rep.warn("d.kuznetsov не включён в подразделение — отчёты видят все руководители отделов (правило D-42)")
    elif me.get("reviewer_id") == head.user_id:
        rep.ok("d.kuznetsov в оргструктуре; его дневные отчёты проверяет e.orlova (руководитель вышестоящей дирекции)")
    else:
        rep.warn("отчёты d.kuznetsov проверяет не e.orlova — оргструктуру меняли; маршрут: «Оргструктура» → строка "
                 "под заголовком")
    # D-45 (ответ владельца на A-50 п. 2): руководитель подразделения проверяет отчёты своих сотрудников без роли.
    head_wo_role = sessions.get("v.zaitseva")
    if head_wo_role is not None and any(u.get("head_user_id") == head_wo_role.user_id and u.get("active") for u in units):
        if head_wo_role.get_meta("worktime_reviewer") is True:
            rep.ok("v.zaitseva (руководитель коммерческой службы, без роли «Руководитель отдела») проверяет отчёты "
                   "своих сотрудников")
        else:
            rep.fail("v.zaitseva руководит подразделением, но не может проверять отчёты сотрудников — сервер старой "
                     "версии? Обновите стенд (update.cmd) и повторите")


def check_workday(rep: Report, sessions: dict) -> None:
    """Раздел 11 (D-46): у d.kuznetsov есть рабочие дни по таймеру; запрос «фактическое начало дня» d.kuznetsov
    видит e.orlova (тот, кто проверяет его отчёты). Только чтение: таймер на стенде никто не нажимает."""
    eng, head = sessions.get("d.kuznetsov"), sessions.get("e.orlova")
    if eng is None or head is None:
        rep.warn("нет входа d.kuznetsov или e.orlova — таймер рабочего дня не проверить")
        return
    days = eng.list_local("workday")
    if days:
        rep.ok(f"d.kuznetsov: рабочих дней по таймеру — {len(days)} (последний — {max(d['work_date'] for d in days)})")
    else:
        rep.warn("у d.kuznetsov нет рабочих дней по таймеру (демо-день создаётся при установке и виден 31 день) — "
                 "нажмите «Начать рабочий день» в боковой панели; если кнопки нет — обновите стенд (update.cmd)")
    reqs = [r for r in head.list_local("workday_start_request") if r.get("user_id") == eng.user_id]
    if reqs:
        pending = sum(1 for r in reqs if r.get("status") == "pending")
        rep.ok(f"e.orlova видит запросы d.kuznetsov на фактическое начало дня: {len(reqs)} (на согласовании — {pending})")
    else:
        rep.warn("e.orlova не видит запросов на фактическое начало дня — " + FRESH_HINT)


def check_schedule(rep: Report, sessions: dict) -> None:
    """Раздел 12 (D-47): графики работы заведены; у d.kuznetsov действует график своего отдела; запрос «фактическое
    окончание дня» i.belova (день завершён автоматически) видит v.zaitseva. Только чтение (pull)."""
    eng, boss = sessions.get("d.kuznetsov"), sessions.get("v.zaitseva")
    if eng is None or boss is None:
        rep.warn("нет входа d.kuznetsov или v.zaitseva — графики работы не проверить")
        return
    sch = eng.get_meta("workday_schedule")
    if not isinstance(sch, dict):
        rep.warn("сервер не прислал график работы — обновите стенд (update.cmd): графики появились в D-47")
        return
    names = [x.get("name") for x in eng.get_meta("work_schedules") or []]
    if names:
        rep.ok(f"графиков работы в компании: {len(names)} ({', '.join(names)})")
    else:
        rep.warn("графиков работы нет — действует встроенный 09:00–18:00; " + FRESH_HINT)
    rep.ok(f"d.kuznetsov: {sch.get('label')} — {sch.get('source_label')}")
    reqs = [r for r in boss.list_local("workday_end_request") if r.get("user_id") != boss.user_id]
    if reqs:
        pending = sum(1 for r in reqs if r.get("status") == "pending")
        rep.ok(f"v.zaitseva видит запросы на фактическое окончание дня: {len(reqs)} (на согласовании — {pending})")
    else:
        rep.warn("v.zaitseva не видит запросов на фактическое окончание дня (демо-запрос i.belova виден сутки после "
                 "установки) — " + FRESH_HINT)


def check_calendar(rep: Report, sessions: dict) -> None:
    """Раздел 13 (D-48): производственный календарь виден сотрудникам (откуда он на текущий год, сколько особых дней);
    у «Смены наладчиков» календарь не учитывается. Только чтение (pull)."""
    eng = sessions.get("d.kuznetsov")
    if eng is None:
        rep.warn("нет входа d.kuznetsov — производственный календарь не проверить")
        return
    cal = eng.get_meta("work_calendar")
    if not isinstance(cal, dict):
        rep.warn("сервер не прислал производственный календарь — обновите стенд (update.cmd): календарь появился в D-48")
        return
    # Годы — прошлый, текущий и следующий по часам сервера: текущий — средний.
    ys = cal.get("years") or []
    y = ys[1] if len(ys) == 3 else None
    year = (y or {}).get("year") or "текущий"
    days = [k for k in (cal.get("days") or {}) if k.startswith(f"{year}-")]
    if y is None or not days:
        rep.fail(f"производственного календаря на {year} год нет — ни загруженного, ни встроенного")
        return
    rep.ok(f"календарь на {year} год: {y.get('label')}; особых дней: {len(days)}")
    if y.get("state") != "loaded":
        rep.warn(f"календарь на {year} год не загружен в базу (действует встроенный) — " + FRESH_HINT)
    shift = next((x for x in eng.get_meta("work_schedules") or [] if x.get("name") == "Смена наладчиков"), None)
    if shift is not None and shift.get("use_calendar") is False:
        rep.ok("«Смена наладчиков» производственный календарь не учитывает (наладка — и в праздники)")
    elif shift is not None:
        rep.warn("у «Смены наладчиков» включён производственный календарь — " + FRESH_HINT)


def check_weekly_metrics(rep: Report, sessions: dict) -> None:
    """Раздел 15: руководитель видит только агрегированные итоги отправленных отчётов за прошлую неделю."""
    head = sessions.get("v.zaitseva")
    if head is None:
        rep.warn("нет входа v.zaitseva — недельные итоги проверить нельзя")
        return
    metrics = head.get_meta("worktime_weekly_metrics") or {}
    units = metrics.get("units") or []
    if not metrics.get("week_start") or not metrics.get("week_end"):
        rep.fail("нет границ отчётной недели в worktime_weekly_metrics — сервер старой версии? Обновите стенд")
        return
    if not units:
        rep.warn("нет отправленных дневных отчётов за прошлую неделю — агрегаты пусты")
        return
    if any(any(k in item for k in ("user_id", "employee", "user_name")) for item in units):
        rep.fail("в недельную сводку попали персональные строки; ожидались только итоги подразделений")
        return
    if all(int(item.get("reports") or 0) > 0 and int(item.get("minutes") or 0) >= 0 for item in units):
        rep.ok(f"{metrics['week_start']}—{metrics['week_end']}: агрегаты подразделений — {len(units)}, "
               "без строк сотрудников")
    else:
        rep.fail("в недельных агрегатах некорректны количества отчётов или минуты")


def check_day_marks(rep: Report, sessions: dict) -> None:
    """Раздел 14 (D-49, ответы владельца на A-53): v.zaitseva видит в отправленных отчётах сотрудников отметки дня
    (сверхурочно, работа в выходной / праздничный день); интервалы напоминания о конце дня — из настроек компании.
    Только чтение (pull)."""
    boss = sessions.get("v.zaitseva")
    if boss is None:
        rep.warn("нет входа v.zaitseva — отметки дня не проверить")
        return
    reports = [r for r in boss.list_local("daily_report") if r.get("user_id") != boss.user_id]
    if reports and all("day_marks" not in r for r in reports):
        rep.warn("в отчётах нет отметок дня — обновите стенд (update.cmd): отметки появились в D-49")
        return
    marks = [r["day_marks"] for r in reports if (r.get("day_marks") or {}).get("mark")]
    over = sum(1 for m in marks if m["mark"] == "overtime")
    off = sum(1 for m in marks if m["mark"] in ("weekend", "holiday"))
    if over and off:
        rep.ok(f"v.zaitseva видит отметки в отчётах сотрудников: сверхурочно — {over}, работа в выходной или праздник "
               f"— {off} (например: «{next(m['label'] for m in marks if m['mark'] != 'overtime')}»)")
    else:
        rep.warn("v.zaitseva не видит отчётов со сверхурочными и работой в выходной (демо-отчёты s.morozov и "
                 "o.nikitina) — " + FRESH_HINT)
    cfg = {c.get("key"): c for c in boss.get_meta("config") or []}
    rem, wait = cfg.get("workday.reminder_repeat"), cfg.get("workday.auto_finish_wait")
    if rem is None or wait is None:
        rep.warn("в настройках компании нет интервалов напоминания — обновите стенд (update.cmd): они появились в D-49")
    else:
        rep.ok(f"настройки компании: повтор напоминания — {rem.get('value_label_ru') or rem.get('value')}; "
               f"автоматическое завершение — {wait.get('value_label_ru') or wait.get('value')}"
               + ("" if rem.get("is_set") or wait.get("is_set") else " (не заданы — действуют решения владельца)"))


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
        print("ИТОГ: стенд готов к проверке (чек-лист владельца, П-1…П-28; сценарии — test-pc.md § 6).")
    return 0


def check_pnr_fat(rep: Report, sessions: dict) -> None:
    """Раздел 16 (D-67, D-68): активный шаблон чек-листа FAT, запрос ПНР на площадке со сроком, идущее приёмочное
    испытание с открытыми замечаниями (одно блокирующее — шлюз потока «Отгрузка» закрыт, пока они не закрыты).
    Только чтение (pull)."""
    rp = sessions.get("a.smirnova")
    if rp is None:
        return
    tpls = [t for t in rp.get_meta("checklist_templates") or [] if t.get("kind") == "fat" and t.get("active")]
    if not tpls:
        rep.warn("нет активного шаблона чек-листа FAT — " + FRESH_HINT)
    else:
        sections = tpls[0].get("sections") or []
        rep.ok(f"активных шаблонов FAT: {len(tpls)} (например, «{tpls[0].get('name')}» — разделов {len(sections)})")
    pnrs = rp.get_meta("pnr_requests") or []
    if not pnrs:
        rep.warn("запросов ПНР нет — " + FRESH_HINT)
    else:
        labels = {"requested": "Запрошена", "scheduled": "Назначена дата", "in_progress": "Выполняется",
                  "done": "Выполнена", "cancelled": "Отменена"}
        statuses = sorted({labels.get(x.get("status"), x.get("status") or "?") for x in pnrs})
        rep.ok(f"запросов ПНР: {len(pnrs)} (на площадке: {sum(x.get('kind') == 'site' for x in pnrs)}); статусы: "
               + ", ".join(statuses))
    fats = rp.get_meta("fat_sessions") or []
    findings = rp.get_meta("fat_findings") or []
    going = [f for f in fats if f.get("status") == "in_progress"]
    if not going:
        rep.warn("нет идущего приёмочного испытания — " + FRESH_HINT)
        return
    open_f = [f for f in findings if f.get("status") == "open"]
    blocking = sum(1 for f in open_f if f.get("blocking"))
    rep.ok(f"идущих приёмочных испытаний (FAT): {len(going)}; открытых замечаний: {len(open_f)} "
           f"(блокирующих {blocking}) — поток «Отгрузка» их ждёт")


def check_delivery_docs(rep: Report, sessions: dict) -> None:
    """Раздел 17 (D-69): активная форма документа доставки и выписанный по ней документ (текст — снимок с данными
    проекта и комплекта). Только чтение (pull)."""
    rp = sessions.get("a.smirnova")
    if rp is None:
        return
    tpls = [t for t in rp.get_meta("delivery_doc_templates") or [] if t.get("active")]
    docs = rp.get_meta("delivery_docs") or []
    if not tpls:
        rep.warn("нет активных форм документов доставки — " + FRESH_HINT)
        return
    if not docs:
        rep.warn("формы есть, а выписанных документов нет — " + FRESH_HINT)
        return
    d0 = docs[0]
    rep.ok(f"активных форм: {len(tpls)}; выписано документов: {len(docs)} "
           f"(«{d0.get('template_title')}» — текст отрендерен снимком, правка формы его не перепишет)")


def check_norms(rep: Report, sessions: dict) -> None:
    """Раздел 18 (D-72): предложение нормы с агрегированным диапазоном (выборка ≥ 2 завершённых операций) и ручное
    решение не-автора. Читает ведущий инженер (norm.read: РП/ГИП/руководитель/ведущий); только чтение (pull)."""
    tech = sessions.get("m.lebedev")
    if tech is None:
        rep.warn("нет входа m.lebedev — предложения норм не проверить")
        return
    props = tech.get_meta("norm_proposals") or []
    if not props:
        rep.warn("предложений норм нет — " + FRESH_HINT)
        return
    if any(int(x.get("sample_size") or 0) < 2 for x in props):
        rep.fail("есть предложение нормы с выборкой меньше двух операций — сообщите разработчику")
        return
    decided = [x for x in props if x.get("status") in ("approved", "rejected")]
    p0 = props[0]
    rep.ok(f"предложений норм: {len(props)} (решено: {len(decided)}); «{p0.get('op_name')}» "
           f"({p0.get('work_center')}): выборка {p0.get('sample_size')}, факт {p0.get('min_min')}—"
           f"{p0.get('max_min')} мин, предложено {p0.get('proposed_min')} мин — «{p0.get('status')}»")


def check_overtime_contour(rep: Report, sessions: dict, prod: dict) -> None:
    """Раздел 19 (D-74, D-75): учётный период задан и несёт норму по производственному календарю; у k.pavlov —
    кадровая категория «ребёнок до трёх лет» и согласованная заявка с основанием из справочника и отметками
    согласия. Только чтение (pull)."""
    kp = prod.get("k.pavlov")
    if kp is None:
        rep.warn("нет входа k.pavlov — ТК-контур не проверить (учётки D-36 появляются на свежем стенде)")
        return
    cfg = kp.get_meta("overtime_period_config")
    if not cfg:
        rep.fail("учётный период компании не задан — регистрация сверхурочных заблокирована; обновите стенд")
        return
    norm = cfg.get("norm") or {}
    modes = {"month": "календарный месяц", "quarter": "квартал", "year": "год"}
    if norm.get("label"):
        rep.ok(f"учётный период: {modes.get(cfg.get('mode'), cfg.get('mode'))}; {cfg.get('period_start')}—"
               f"{cfg.get('period_end')}; норма по производственному календарю: {norm.get('label')}"
               + ("" if norm.get("complete") else " (календарь покрывает период не полностью)"))
    else:
        rep.warn("в конфигурации периода нет нормы по календарю — обновите стенд (update.cmd): норма появилась в D-75")
    hr = kp.get_meta("hr_categories") or {}
    own = hr.get("own") or []
    if "parent_child_under3" in own:
        rep.ok("k.pavlov: кадровая категория «Работник с ребёнком до трёх лет» — привлечение только с согласия "
               "(ст. 99 ч. 5, ст. 259 ТК РФ)")
    else:
        rep.warn("у k.pavlov нет демо-категории «ребёнок до трёх лет» — " + FRESH_HINT)
    reqs = [r for r in kp.list_local("overtime_request") if r.get("user_id") == kp.user_id]
    appr = [r for r in reqs if r.get("status") == "approved" and r.get("ground")]
    if appr:
        r0 = appr[0]
        rep.ok(f"заявка k.pavlov согласована: {r0.get('work_date')}, {r0.get('minutes')} мин, основание "
               f"«{r0.get('ground_label')}» ({r0.get('ground_article')}); согласие и разъяснение права отказа "
               "зафиксированы")
    else:
        rep.warn("согласованной заявки k.pavlov с основанием ТК нет — " + FRESH_HINT)


if __name__ == "__main__":
    sys.exit(main())
