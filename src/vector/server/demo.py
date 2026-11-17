"""Демонстрационные данные для тестового стенда и стенда интерфейса.

ТОЛЬКО ДЛЯ ТЕСТА. Все организации, люди, суммы и даты вымышлены. Данные создаются через обычные
команды сервиса — права, аудит, версии и проверки работают так же, как в настоящей работе, поэтому
демо-данные пригодны для проверки ролей и сценариев.

Защита от случайного запуска на рабочей базе (fail-closed): seed_demo() работает только в новой
базе, где есть единственная учётная запись (первый администратор) и нет ни одного заказчика.

Учётные записи (пароль у всех один — задаёт тестировщик):
  a.smirnova — РП; p.volkov — ГИП; e.orlova — руководитель отдела; d.kuznetsov — инженер;
  m.lebedev — ведущий инженер; o.nikitina — бухгалтерия; i.belova — склад; s.morozov — закупка;
  n.sokolov — без роли, должен сменить пароль.
"""
from __future__ import annotations

import hashlib
import uuid

import sqlalchemy as sa

from .db import calc_versions, customers, users

DEVICE = "demo-seed"

# (логин, ФИО, роль) — для справки на экране и в инструкции.
ACCOUNTS = [
    ("a.smirnova", "Смирнова Анна", "rp", "Руководитель проекта"),
    ("p.volkov", "Волков Павел", "gip", "ГИП"),
    ("e.orlova", "Орлова Елена", "dept_head", "Руководитель отдела"),
    ("d.kuznetsov", "Кузнецов Дмитрий", "engineer", "Инженер"),
    ("m.lebedev", "Лебедев Максим", "lead_engineer", "Ведущий инженер"),
    ("o.nikitina", "Никитина Ольга", "accountant", "Бухгалтерия"),
    ("i.belova", "Белова Ирина", "warehouse", "Склад"),
    ("s.morozov", "Морозов Сергей", "procurement", "Закупка"),
    ("n.sokolov", "Соколов Никита", None, "Без роли; при первом входе должен сменить пароль"),
]


class DemoError(Exception):
    """Демо-данные не созданы (база не пустая, неверный пароль администратора, слабый пароль и т. п.)."""


def check_empty(svc) -> None:
    """Fail-closed: демо-данные — только в новую базу (один администратор, ни одного заказчика)."""
    with svc.engine.connect() as c:
        n_users = c.execute(sa.select(sa.func.count()).select_from(users)).scalar()
        n_cust = c.execute(sa.select(sa.func.count()).select_from(customers)).scalar()
    if n_users != 1 or n_cust != 0:
        raise DemoError("Демо-данные добавляются только в новую пустую базу (после init, до начала работы). "
                        f"Сейчас учётных записей: {n_users}, заказчиков: {n_cust}. Ничего не изменено.")


def seed_demo(svc, admin_login: str, admin_password: str, demo_password: str) -> dict:
    """Проверить, что база новая, и наполнить её демо-данными. Возвращает идентификаторы созданных объектов."""
    check_empty(svc)
    return _seed(svc, admin_login, admin_password, demo_password)


def _seed(svc, admin_login: str, admin_password: str, demo_password: str) -> dict:
    """Наполнить базу демо-сценарием (без проверки пустоты — её делает seed_demo).

    Порядок повторяет реальную работу: администратор заводит людей и роли → РП заводит заказчиков и тендеры → по
    выигранному тендеру начинается РКД (проект) → комплекты, документы с версиями файлов, задачи → расчёты и их
    фиксация (С3) → склады, номенклатура, приходы, сторно, потребность и резерв (С4). Любой отказ команды прерывает
    наполнение с понятным текстом.
    """
    def login(lg, pwd=None):
        """Войти демо-пользователем (по умолчанию с общим демо-паролем) и вернуть токен сеанса."""
        r = svc.handle({"type": "login", "login": lg, "password": pwd or demo_password, "device_id": DEVICE}, DEVICE)
        if r["status"] != "accepted":
            raise DemoError(f"Вход «{lg}» не выполнен: {r.get('message_ru') or r.get('status')}")
        return r["token"]

    def cmd(tok, c, p, ev=None):
        """Выполнить команду от имени пользователя с новым operation_id; отказ → DemoError. Возвращает result команды.
        """
        r = svc.handle({"type": "command", "token": tok, "operation_id": str(uuid.uuid4()), "command": c, "payload": p, "expected_version": ev}, DEVICE)
        if r["status"] != "accepted":
            raise DemoError(f"{c}: {r.get('message_ru') or r.get('code') or r.get('status')}")
        return r["result"]

    adm = login(admin_login, admin_password)
    people = {}
    for lg, name, role, _ in [a for a in ACCOUNTS if a[2]]:
        uid = cmd(adm, "admin.create_user", {"login": lg, "display_name": name, "initial_password": demo_password})["user_id"]
        cmd(adm, "admin.grant_role", {"user_id": uid, "role": role, "reason": "демо"})
        t = login(lg)
        # Демо-учётки сразу готовы к входу: временный пароль меняется и возвращается обратно (через обычную команду,
        # с аудитом). Требование смены пароля при первом входе показывает отдельная учётка n.sokolov (см. ниже).
        cmd(t, "account.change_password", {"current_password": demo_password, "new_password": demo_password + "!"})
        cmd(t, "account.change_password", {"current_password": demo_password + "!", "new_password": demo_password})
        people[lg] = uid
    cmd(adm, "admin.create_user", {"login": "n.sokolov", "display_name": "Соколов Никита", "initial_password": demo_password})
    rp, head = login("a.smirnova"), login("e.orlova")

    custs = {}
    for name, inn, note in [("АО «Энергомаш»", "7701234567", "Ключевой заказчик, энергетика"), ("ООО «ТеплоСеть-Юг»", "3443123456", ""),
                            ("ПАО «Волгохимпром»", "3444987654", "Тендеры через ЭТП"), ("МУП «Водоканал»", None, "ИНН уточняется")]:
        cid = str(uuid.uuid4())
        cmd(rp, "customer.create", {"id": cid, "name": name, **({"inn": inn} if inn else {}), **({"note": note} if note else {})})
        custs[name] = cid

    def tender(cust, title, deadline, path):
        """Создать тендер и провести его по цепочке статусов path (для отмены/проигрыша — с причиной)."""
        tid = str(uuid.uuid4())
        cmd(rp, "tender.create", {"id": tid, "customer_id": custs[cust], "title": title, "submission_deadline": deadline})
        v = 1
        for st in path:
            cmd(rp, "tender.transition", {"id": tid, "to": st, **({"reason": "Заказчик перенёс закупку"} if st in ("cancelled", "lost") else {})}, v)
            v += 1
        return tid

    t_won = tender("АО «Энергомаш»", "АСУ ТП котельной № 3: проектирование и поставка", "2026-10-20", ["in_work", "bid_submitted", "won"])
    t_won2 = tender("ПАО «Волгохимпром»", "Модернизация системы ПАЗ цеха № 5", "2026-09-30", ["in_work", "bid_submitted", "won"])
    t_sub = tender("ООО «ТеплоСеть-Юг»", "Диспетчеризация ЦТП (12 объектов)", "2026-12-05", ["in_work", "bid_submitted"])
    tender("ООО «ТеплоСеть-Юг»", "Узлы учёта тепловой энергии", "2026-12-18", ["in_work"])
    tender("МУП «Водоканал»", "КИПиА насосной станции 2-го подъёма", None, [])
    tender("ПАО «Волгохимпром»", "Пусконаладка САУ компрессорной", "2026-08-01", ["cancelled"])

    k1 = str(uuid.uuid4())
    cmd(rp, "tkp.create", {"id": k1, "tender_id": t_won, "label": "ТКП-27/2026", "amount": "18450000.00", "currency": "RUB", "note": "Вариант с резервированием контроллеров"})
    cmd(head, "tkp.approve", {"id": k1, "comment": "Согласовано"}, 1)
    cmd(rp, "tkp.record_sent", {"id": k1, "sent_on": "2026-10-18", "channel": "ЭТП"}, 2)
    k2 = str(uuid.uuid4())
    cmd(rp, "tkp.create", {"id": k2, "tender_id": t_sub, "label": "ТКП-31/2026", "amount": "6230500.50", "currency": "RUB"})
    cmd(head, "tkp.approve", {"id": k2}, 1)
    cmd(rp, "tkp.create", {"id": str(uuid.uuid4()), "tender_id": t_sub, "label": "ТКП-31/2026 (вариант Б)"})

    pid = str(uuid.uuid4())
    cmd(rp, "project.start_rkd", {"id": pid, "tender_id": t_won, "code": "П-2026-014", "title": "АСУ ТП котельной № 3"})
    cmd(adm, "project.assign", {"project_id": pid, "user_id": people["p.volkov"], "project_role": "gip", "reason": "Приказ № 112"})
    gip = login("p.volkov")
    cmd(gip, "project.assign", {"project_id": pid, "user_id": people["a.smirnova"], "project_role": "rp", "reason": "Приказ № 112"})
    cmd(rp, "project.assign", {"project_id": pid, "user_id": people["d.kuznetsov"], "project_role": "participant", "reason": "Распределение работ"})
    cmd(rp, "project.assign", {"project_id": pid, "user_id": people["m.lebedev"], "project_role": "participant", "reason": "Распределение работ"})
    kits = {}
    for code, title in [("АТХ", "Автоматизация технологических процессов"), ("ЭМ", "Силовое электрооборудование"),
                        ("КИП", "Контрольно-измерительные приборы"), ("СС", "Сети связи и диспетчеризация")]:
        kits[code] = str(uuid.uuid4())
        cmd(rp, "kit.create", {"id": kits[code], "project_id": pid, "code": code, "title": title})

    # Документы комплекта АТХ: файл загружается отдельно (put_file → квитанция), затем add_version.
    # Содержимое — синтетические байты (это демо-данные интерфейса, а не настоящие чертежи).
    eng = login("d.kuznetsov")
    docs = {}

    def doc(tok, code, title, versions):
        """Создать документ в комплекте АТХ и загрузить его версии (содержимое файлов — повторяющийся текст нужного
        размера, не настоящие PDF).
        """
        did = str(uuid.uuid4())
        cmd(tok, "document.create", {"id": did, "kit_id": kits["АТХ"], "code": code, "title": title})
        for n, (fname, size, comment) in enumerate(versions, start=1):
            data = (code + fname).encode() * (size // len((code + fname).encode()) + 1)
            data = data[:size]
            sha = hashlib.sha256(data).hexdigest()
            up = svc.put_file(tok, pid, data, sha, DEVICE)
            if up["status"] != "accepted":
                raise DemoError(f"Загрузка демо-файла отклонена: {up.get('message_ru')}")
            cmd(tok, "document.add_version", {"id": did, "sha256": sha, "file_name": fname, **({"comment": comment} if comment else {})}, n)
        docs[code] = did
        return did

    doc(eng, "АТХ-001", "Схема автоматизации функциональная",
        [("АТХ-001_схема_ФСА.pdf", 1_840_000, "Первая выдача"), ("АТХ-001_схема_ФСА_изм1.pdf", 1_910_500, "Замечания ГИП учтены"),
         ("АТХ-001_схема_ФСА_изм2.pdf", 1_925_120, "Изм. 2: добавлены датчики давления")])
    doc(eng, "АТХ-002", "Перечень входных/выходных сигналов", [("АТХ-002_перечень_сигналов.xlsx", 86_400, None)])
    doc(rp, "АТХ-003", "Пояснительная записка", [])

    def task(title, who, status_path, due=None, document=None, desc=None):
        """Создать задачу в комплекте АТХ и провести её по статусам status_path."""
        tid = str(uuid.uuid4())
        cmd(rp, "task.create", {"id": tid, "kit_id": kits["АТХ"], "title": title, **({"assignee_user_id": people[who]} if who else {}),
                                **({"due_date": due} if due else {}), **({"document_id": docs[document]} if document else {}),
                                **({"description": desc} if desc else {})})
        for v, st in enumerate(status_path, start=1):
            cmd(rp, "task.transition", {"id": tid, "to": st, **({"reason": "Объединено с задачей по ФСА"} if st == "cancelled" else {})}, v)

    task("Разработать функциональную схему автоматизации", "d.kuznetsov", ["in_progress"], "2026-11-28", "АТХ-001",
         "По опросным листам заказчика; учесть резервирование контроллеров.")
    task("Сверить перечень сигналов с поставщиком КИП", "m.lebedev", [], "2026-12-03", "АТХ-002")
    task("Подготовить пояснительную записку", "d.kuznetsov", [], "2026-12-15", "АТХ-003")
    task("Согласовать опросные листы с заказчиком", "m.lebedev", ["in_progress", "done"], "2026-11-10")
    task("Отдельная схема питания шкафов", None, ["cancelled"])

    # Расчёты комплектов (С3). Цены ВЫМЫШЛЕНЫ — это демо-данные интерфейса, а не справочник цен.
    # АТХ: черновик → фиксация ГИП (v1) → новые правки инженера (видно сравнение с v1, одна цена неизвестна).
    # ЭМ: черновик РП → фиксация (v1). Итог проекта = последняя ревизия из v1 обоих комплектов.
    def ln(code, name, unit, qty, price, note=None):
        """Позиция расчёта комплекта (числа строками, как в настоящем запросе; цена None — «неизвестна»)."""
        return {"line_id": str(uuid.uuid4()), "code": code, "name": name, "unit": unit, "qty": qty, "unit_price": price,
                **({"note": note} if note else {})}
    atx = [ln("ШУ-1", "Шкаф управления котлом (ПЛК, панель оператора)", "шт", "3", "485000"),
           ln("ШУ-0", "Шкаф общекотельного оборудования", "шт", "1", "612000"),
           ln("ПЛК-ВВ", "Модуль ввода-вывода резервный", "шт", "6", "38500"),
           ln("АРМ", "АРМ оператора (ПК, ИБП, ПО SCADA)", "компл", "2", "214000"),
           ln(None, "Кабель контрольный КВВГнг(А)-LS 7×1,5", "м", "1850", "142.6"),
           ln(None, "Пусконаладочные работы АСУ ТП", "усл", "1", "780000", "по этапам")]
    r = cmd(eng, "calc.save_draft", {"id": kits["АТХ"], "currency": "RUB", "note": "По опросным листам", "lines": atx}, 0)
    cmd(gip, "calc.freeze", {"id": kits["АТХ"], "reason": "Расчёт к ТКП-27/2026"}, r["calc_draft"]["version"])
    atx2 = [dict(x) for x in atx]
    atx2[4]["qty"] = "2140"                                         # уточнили длины трасс
    atx2.append(ln("ДД", "Датчик давления (изм. 2 схемы ФСА)", "шт", "8", None, "цена у поставщика КИП уточняется"))
    del atx2[2]                                                     # резервные модули — в поставке ПЛК
    cmd(eng, "calc.save_draft", {"id": kits["АТХ"], "currency": "RUB", "note": "По опросным листам", "lines": atx2},
        r["calc_draft"]["version"])
    r = cmd(rp, "calc.save_draft", {"id": kits["ЭМ"], "currency": "RUB", "lines": [
        ln("ЩР-1", "Щит распределительный 0,4 кВ", "шт", "2", "356000"),
        ln(None, "Кабель силовой ВВГнг(А)-LS 5×16", "м", "420", "1185.5"),
        ln(None, "Монтаж электрооборудования", "усл", "1", "295000")]}, 0)
    cmd(gip, "calc.freeze", {"id": kits["ЭМ"], "reason": "Расчёт к ТКП-27/2026"}, r["calc_draft"]["version"])

    pid2 = str(uuid.uuid4())
    cmd(rp, "project.start_rkd", {"id": pid2, "tender_id": t_won2, "code": "П-2026-011", "title": "ПАЗ цеха № 5"})
    cmd(adm, "project.assign", {"project_id": pid2, "user_id": people["a.smirnova"], "project_role": "rp", "reason": "Приказ № 98"})
    cmd(rp, "kit.create", {"id": str(uuid.uuid4()), "project_id": pid2, "code": "ПАЗ", "title": "Противоаварийная защита"})
    # Склад и закупка (С4). Количества и номера накладных ВЫМЫШЛЕНЫ. Номенклатура заведена с кодами, совпадающими
    # с обозначениями части позиций расчёта АТХ, — так видно и сопоставленные строки потребности, и несопоставленные.
    wh, buyer = login("i.belova"), login("s.morozov")
    whs = {}
    for code, name in [("СК-1", "Центральный склад"), ("СК-2", "Склад монтажного участка")]:
        whs[code] = str(uuid.uuid4())
        cmd(wh, "warehouse.create", {"id": whs[code], "code": code, "name": name})
    items = {}
    for code, name, unit, by in [("ШУ-1", "Шкаф управления котлом", "шт", wh), ("АРМ", "АРМ оператора", "компл", wh),
                                 ("ДД", "Датчик давления", "шт", buyer), ("ЩР-1", "Щит распределительный 0,4 кВ", "шт", wh),
                                 ("НШВИ-1.5", "Наконечник НШВИ 1,5 мм²", "шт", wh), ("ХМТ-200", "Хомут нейлоновый 200 мм", "шт", wh),
                                 # Кабель есть в номенклатуре, но в расчёте АТХ записан без обозначения — строка
                                 # потребности остаётся «Не сопоставлена»: её можно сопоставить вручную (A-25).
                                 ("КВВГнг-7x1.5", "Кабель контрольный КВВГнг(А)-LS 7×1,5", "м", buyer)]:
        items[code] = str(uuid.uuid4())
        cmd(by, "item.create", {"id": items[code], "code": code, "name": name, "unit": unit})

    # Справочник аналогов и пересчёта (D-25, A-27): общий пересчёт «км → м» и другое обозначение хомута из ТКП
    # поставщика. На демо-потребности они не влияют (в АТХ v1 таких обозначений и единиц нет) — это пример.
    cmd(buyer, "unit_conversion.create", {"id": str(uuid.uuid4()), "from_unit": "км", "to_unit": "м", "factor": "1000",
                                          "note": "Метрическая система"})
    cmd(wh, "item_alias.create", {"id": str(uuid.uuid4()), "item_id": items["ХМТ-200"], "alias": "ХС-200",
                                  "note": "Так пишет поставщик в ТКП"})

    def receipt(w, item, qty, src):
        """Приход на склад от имени кладовщика с основанием src; возвращает созданное движение."""
        return cmd(wh, "stock.receive", {"id": str(uuid.uuid4()), "warehouse_id": whs[w], "item_id": items[item],
                                         "qty": qty, "source_doc": src})["movement"]
    receipt("СК-1", "ШУ-1", "2", "ТН № 412 от ООО «Шкафы-Про»")
    receipt("СК-1", "АРМ", "2", "ТН № 77 от ООО «ИТ-Снаб»")
    wrong = receipt("СК-1", "НШВИ-1.5", "10000", "ТН № 1520")
    cmd(wh, "stock.reverse", {"id": str(uuid.uuid4()), "movement_id": wrong["id"], "reason": "Ошибка ввода: лишний ноль"})
    receipt("СК-1", "НШВИ-1.5", "1000", "ТН № 1520")
    receipt("СК-2", "ХМТ-200", "500", "ТН № 1521")
    receipt("СК-2", "ЩР-1", "1", "ТН № 90 от АО «Электрощит»")
    # Перемещение (A-28): часть наконечников — на склад монтажного участка. В журнале — две связанные записи.
    cmd(wh, "stock.transfer", {"id": str(uuid.uuid4()), "item_id": items["НШВИ-1.5"], "from_warehouse_id": whs["СК-1"],
                               "to_warehouse_id": whs["СК-2"], "qty": "200", "reason": "На монтажный участок",
                               "source_doc": "Накладная на перемещение № 3"})

    # Закупка формирует потребность из последней зафиксированной версии АТХ (v1; черновик v2 не зафиксирован).
    # id версии берётся из БД напрямую: закупка не видит расчёты (цены) через pull — и не должна.
    with svc.engine.connect() as c:
        atx_v1 = c.execute(sa.select(calc_versions.c.id).where(calc_versions.c.kit_id == kits["АТХ"])).scalar_one()
    need = cmd(buyer, "need.generate", {"id": str(uuid.uuid4()), "calc_version_id": atx_v1})["need"]
    line_of = {ln_["code"]: ln_["line_id"] for ln_ in need["lines"] if ln_["code"]}
    res_shu = cmd(wh, "stock.reserve", {"id": str(uuid.uuid4()), "warehouse_id": whs["СК-1"], "item_id": items["ШУ-1"],
                                        "project_id": pid, "qty": "2", "reason": "Под монтаж котлов № 1 и 2",
                                        "need_id": need["id"], "need_line_id": line_of["ШУ-1"]})["reservation"]
    res_arm = cmd(wh, "stock.reserve", {"id": str(uuid.uuid4()), "warehouse_id": whs["СК-1"], "item_id": items["АРМ"],
                                        "project_id": pid, "qty": "2", "reason": "Под ПНР",
                                        "need_id": need["id"], "need_line_id": line_of["АРМ"]})["reservation"]
    cmd(wh, "stock.issue", {"id": str(uuid.uuid4()), "warehouse_id": whs["СК-1"], "item_id": items["АРМ"], "qty": "1",
                            "reservation_id": res_arm["id"], "reason": "Выдано на ПНР, бригада Иванова"})
    cmd(wh, "stock.issue", {"id": str(uuid.uuid4()), "warehouse_id": whs["СК-2"], "item_id": items["ХМТ-200"], "qty": "120",
                            "project_id": pid, "reason": "Монтаж кабельных трасс"})
    return {"customers": custs, "project": pid, "tender_won": t_won, "tender_sub": t_sub, "kits": kits, "docs": docs,
            "warehouses": whs, "items": items, "need": need["id"], "reservations": [res_shu["id"], res_arm["id"]]}
