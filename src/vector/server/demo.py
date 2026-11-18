"""Демонстрационные данные для тестового стенда и стенда интерфейса.

ТОЛЬКО ДЛЯ ТЕСТА. Все организации, люди, суммы и даты вымышлены. Данные создаются через обычные
команды сервиса — права, аудит, версии и проверки работают так же, как в настоящей работе, поэтому
демо-данные пригодны для проверки ролей и сценариев.

Защита от случайного запуска на рабочей базе (fail-closed): seed_demo() работает только в новой
базе, где есть единственная учётная запись (первый администратор) и нет ни одного заказчика.

Учётные записи (пароль у всех один — задаёт тестировщик):
  a.smirnova — РП; p.volkov — ГИП; e.orlova — руководитель отдела; d.kuznetsov — инженер;
  m.lebedev — ведущий инженер; o.nikitina — бухгалтерия; i.belova — склад; s.morozov — закупка;
  v.zaitseva — согласование закупок; t.fedorova, a.grishin — технологи; k.pavlov, r.sergeev — производство (D-36);
  n.sokolov — без роли, должен сменить пароль.

Рабочее время (D-42): дневные отчёты d.kuznetsov и m.lebedev — на проверке, черновик, принят, возвращён (см.
_seed_worktime).

Оргструктура (D-44): головной офис и филиал, дирекция, отделы, служба; руководители и сотрудники (см. _seed_org).
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
import uuid

import sqlalchemy as sa

from .db import calc_versions, customers, tasks, users, work_orders

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
    ("v.zaitseva", "Зайцева Вера", "po_approver", "Согласование закупок (коммерческий директор)"),
    # Технология и производство (D-36): два технолога — ТП утверждает не автор («четыре глаза»), и есть кем заменить.
    ("t.fedorova", "Федорова Татьяна", "technologist", "Технолог"),
    ("a.grishin", "Гришин Алексей", "technologist", "Технолог"),
    ("k.pavlov", "Павлов Кирилл", "production", "Производство (сборщик-монтажник)"),
    ("r.sergeev", "Сергеев Роман", "production", "Производство (наладчик)"),
    # Отдел технического контроля (D-40, ответ владельца на A-46): ведёт рекламации и проверяет эффективность действий —
    # в проектах, куда назначен (D-41).
    ("l.egorova", "Егорова Людмила", "otk", "Технический контроль (ОТК)"),
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
    tokens: dict[str, str] = {}

    def login(lg, pwd=None):
        """Войти демо-пользователем (по умолчанию с общим демо-паролем) и вернуть токен сеанса.

        Сеанс демо-пользователя запоминается и переиспользуется: вход ограничен по частоте с одного адреса
        (login_rate_per_minute_per_addr), а пользователей в демо больше этого предела.
        """
        if pwd is None and lg in tokens:
            return tokens[lg]
        r = svc.handle({"type": "login", "login": lg, "password": pwd or demo_password, "device_id": DEVICE}, DEVICE)
        if r["status"] != "accepted":
            raise DemoError(f"Вход «{lg}» не выполнен: {r.get('message_ru') or r.get('status')}")
        if pwd is None:
            tokens[lg] = r["token"]
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
    # ОТК работает по проектам (D-41, ответ владельца на A-47): сотрудник ОТК — участник проекта П-2026-014; во втором
    # проекте a.smirnova его нет — рекламаций того проекта он не видит.
    cmd(rp, "project.assign", {"project_id": pid, "user_id": people["l.egorova"], "project_role": "participant",
                               "reason": "Технический контроль по проекту"})
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
    # Перемещения (A-28, D-29): наконечники отгружены на склад монтажного участка и приняты там (две связанные записи
    # журнала); часть хомутов отгружена обратно на центральный склад и ещё «в пути» — её можно принять или вернуть.
    t1 = cmd(wh, "stock.transfer", {"id": str(uuid.uuid4()), "item_id": items["НШВИ-1.5"], "from_warehouse_id": whs["СК-1"],
                                    "to_warehouse_id": whs["СК-2"], "qty": "200", "reason": "На монтажный участок",
                                    "source_doc": "Накладная на перемещение № 3"})["transfer"]
    cmd(wh, "stock.transfer_accept", {"id": t1["id"], "comment": "Принято без замечаний"}, ev=t1["version"])
    # Ещё одна партия хомутов пришла на СК-1 не полностью: принято 48 из 50, недостача 2 шт оформлена актом
    # недостачи № 1, который подписал приёмщик (D-30). Отгружена раньше — в списке ниже «в пути».
    t2 = cmd(wh, "stock.transfer", {"id": str(uuid.uuid4()), "item_id": items["ХМТ-200"], "from_warehouse_id": whs["СК-2"],
                                    "to_warehouse_id": whs["СК-1"], "qty": "50", "reason": "Пополнение центрального склада",
                                    "source_doc": "Накладная на перемещение № 5"})["transfer"]
    cmd(wh, "stock.transfer_accept", {"id": t2["id"], "accepted_qty": "48", "shortage_reason": "Повреждена упаковка, 2 шт "
                                      "не пригодны", "sign_act": True, "sign_password": demo_password}, ev=t2["version"])
    # Утрата в пути (D-31): коробка наконечников не доехала до участка — акт об утрате № 1 (подпись приёмщика с
    # вводом пароля — способ по умолчанию, настройка stock.act_signature не задана).
    t3 = cmd(wh, "stock.transfer", {"id": str(uuid.uuid4()), "item_id": items["НШВИ-1.5"], "from_warehouse_id": whs["СК-1"],
                                    "to_warehouse_id": whs["СК-2"], "qty": "20", "reason": "Добор на монтажный участок",
                                    "source_doc": "Накладная на перемещение № 6"})["transfer"]
    cmd(wh, "stock.transfer_lost", {"id": t3["id"], "reason": "Коробка не прибыла с попутным транспортом; служебная "
                                    "записка водителя, розыск у перевозчика", "sign_act": True,
                                    "sign_password": demo_password}, ev=t3["version"])
    # Срок прибытия — «для справки» (D-30): через 3 дня после даты демо-данных.
    cmd(wh, "stock.transfer", {"id": str(uuid.uuid4()), "item_id": items["ХМТ-200"], "from_warehouse_id": whs["СК-2"],
                               "to_warehouse_id": whs["СК-1"], "qty": "100", "reason": "Излишек после монтажа трасс",
                               "source_doc": "Накладная на перемещение № 4",
                               "expected_on": (svc.now().date() + timedelta(days=3)).isoformat()})

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

    # Заказы поставщикам и факт поставки (D-32, D-33). Поставщики, цены, номера счетов, договоров и накладных
    # ВЫМЫШЛЕНЫ. Реквизиты — с кодом региона «00», которого нет ни в ИНН, ни в ОГРН, ни в КПП, ни в БИК настоящих
    # организаций и банков: контрольные цифры верные (программа их проверяет), но совпасть с реальными они не могут.
    approver = login("v.zaitseva")
    sups = {}
    fake_bank = {"bank_name": "АО «Демо-Банк» (вымышленный)", "bik": "040000001", "corr_account": "30101810100000000001"}
    for code, name, contact, req in [
            ("ШКАФЫ-ПРО", "ООО «Шкафы-Про» (вымышленный)", "Отдел продаж, доб. 101",
             {"inn": "0000002022", "kpp": "000001002", "ogrn": "1020000002021", "bank_account": "40702810900000000202",
              "address": "г. Демоград, ул. Вымышленная, д. 2", "vat_rate": "22", **fake_bank}),
            ("ЭЛЕКТРОСНАБ", "ООО «Электроснаб» (вымышленный)", "Менеджер Ковалёв",
             {"inn": "0000001011", "kpp": "000001001", "ogrn": "1020000001010", "bank_account": "40702810700000000101",
              "address": "г. Демоград, ул. Вымышленная, д. 1", "vat_rate": "22", **fake_bank}),
            ("КИП-КОМПЛЕКТ", "ООО «КИП-Комплект» (вымышленный)", None, {})]:
        sups[code] = str(uuid.uuid4())
        cmd(buyer, "supplier.create", {"id": sups[code], "code": code, "name": name,
                                       **({"contact": contact} if contact else {}), **req})
    today = svc.now().date()
    contracts = {}
    for sup, number, signed, until, subject in [
            # Даты — относительно сегодня, чтобы договоры действовали при любой дате наполнения демо-базы.
            ("ЭЛЕКТРОСНАБ", "П-14", today - timedelta(days=300), today + timedelta(days=200), "Поставка кабельной продукции"),
            ("ШКАФЫ-ПРО", "ШП-3", today - timedelta(days=600), None, "Изготовление и поставка шкафов управления")]:
        contracts[sup] = str(uuid.uuid4())
        cmd(buyer, "contract.create", {"id": contracts[sup], "supplier_id": sups[sup], "number": number,
                                       "signed_on": signed.isoformat(),
                                       **({"valid_until": until.isoformat()} if until else {}), "subject": subject})

    def po_line(item, qty, price=None, need_line=None, note=None, vat="22"):
        """Строка заказа поставщику (цена None — неизвестна; need_line — код строки потребности АТХ v1)."""
        return {"line_id": str(uuid.uuid4()), "item_id": items[item], "qty": qty, "unit_price": price,
                "vat_rate": vat, **({"need_id": need["id"], "need_line_id": line_of[need_line]} if need_line else {}),
                **({"note": note} if note else {})}

    def po(sup, lines, currency="RUB", **kw):
        """Черновик заказа от закупки; возвращает представление заказа."""
        return cmd(buyer, "po.create", {"id": str(uuid.uuid4()), "supplier_id": sups[sup], "currency": currency,
                                        "lines": lines, **kw})["purchase_order"]

    def submit(o, comment=None):
        """Отправить черновик на согласование (закупка)."""
        return cmd(buyer, "po.submit", {"id": o["id"], **({"comment": comment} if comment else {})},
                   ev=o["version"])["purchase_order"]

    def place(o):
        """Согласовать (коммерческий директор) и разместить черновик (условия замораживаются)."""
        o = submit(o)
        o = cmd(approver, "po.approve", {"id": o["id"]}, ev=o["version"])["purchase_order"]
        return cmd(buyer, "po.place", {"id": o["id"]}, ev=o["version"])["purchase_order"]
    # № 1 — размещён, ждём: третий шкаф ШУ-1 под непокрытую строку потребности (2 из 3 уже в резерве).
    o1 = place(po("ШКАФЫ-ПРО", [po_line("ШУ-1", "1", "485000", need_line="ШУ-1")], supplier_ref="Счёт № 218",
                  vat_mode="included", contract_id=contracts["ШКАФЫ-ПРО"],
                  expected_on=(today + timedelta(days=21)).isoformat()))
    # № 2 — размещён, поставлен частично: кабель пришёл 1200 м из 2000 (накладная), цена хомутов неизвестна.
    o2 = place(po("ЭЛЕКТРОСНАБ", [po_line("КВВГнг-7x1.5", "2000", "142.6"),
                                  po_line("ХМТ-200", "1000", None, note="Цену уточняют, счёт будет с поставкой")],
                  supplier_ref="Счёт № 5531", vat_mode="included", contract_id=contracts["ЭЛЕКТРОСНАБ"]))
    cmd(wh, "po.receive", {"id": str(uuid.uuid4()), "order_id": o2["id"], "line_id": o2["lines"][0]["line_id"],
                           "warehouse_id": whs["СК-2"], "qty": "1200", "source_doc": "ТН № 8812 от ООО «Электроснаб»"})
    # № 3 — черновик: датчики давления по изменению схемы (цены, валюты и НДС пока нет — не подставляются).
    o3 = po("КИП-КОМПЛЕКТ", [po_line("ДД", "8", None, vat=None)], currency=None, note="Ждём ТКП поставщика")
    # № 4 — отменён: случайный дубль № 3.
    o4 = po("КИП-КОМПЛЕКТ", [po_line("ДД", "8", None, vat=None)], currency=None)
    cmd(buyer, "po.cancel", {"id": o4["id"], "reason": "Дубль заказа № 3"}, ev=o4["version"])
    # № 5 — на согласовании у коммерческого директора: цены без НДС (так в счёте), НДС 22 % начислит программа.
    # Суммы из счёта введены — сверка «совпадает» (D-34): 500 × 3,85 = 1 925,00 + НДС 423,50 = 2 348,50.
    o5 = submit(po("ЭЛЕКТРОСНАБ", [po_line("НШВИ-1.5", "500", "3.85")], supplier_ref="Счёт № 5602",
                   vat_mode="excluded", contract_id=contracts["ЭЛЕКТРОСНАБ"], invoice_gross="2348.50",
                   invoice_vat="423.50"), comment="Под монтаж ШУ-1, нужно до конца месяца")
    # Замещение (D-34): руководитель отдела заранее передала свою роль ведущему инженеру на время отпуска —
    # запланировано (начнётся через 10 дней), сейчас на права не влияет.
    deleg = cmd(head, "delegation.create", {"to_user_id": people["m.lebedev"], "role": "dept_head",
                                            "valid_from": (today + timedelta(days=10)).isoformat(),
                                            "valid_until": (today + timedelta(days=23)).isoformat(),
                                            "reason": "Отпуск (приказ № 112-о)"})["delegation"]
    # Замещение роли в проекте (D-35): ГИП в командировке — его роль в проекте П-2026-014 на неделю у ведущего
    # инженера (он уже участник проекта). Действует сразу: Лебедев видит «ГИП в проекте … (замещение до …)», а
    # руководитель отдела и Лебедев получают уведомления.
    pdeleg = cmd(gip, "delegation.create", {"to_user_id": people["m.lebedev"], "role": "gip", "project_id": pid,
                                            "valid_until": (today + timedelta(days=6)).isoformat(),
                                            "reason": "Командировка на объект (приказ № 118-к)"})["delegation"]
    prod = _seed_production(cmd, login, people, svc, kits, atx_v1)
    prod.update(_seed_lifecycle(cmd, login, people, svc, prod["tech_processes"][0], items, whs, docs))
    prod.update(_seed_quality(cmd, login, people, svc, prod["product_instances"][0], kits, prod["lifecycle_work_order"]))
    prod.update(_seed_org(cmd, adm, people))
    prod.update(_seed_worktime(cmd, login, people, svc, pid))
    return {"customers": custs, "project": pid, "tender_won": t_won, "tender_sub": t_sub, "kits": kits, "docs": docs,
            "warehouses": whs, "items": items, "need": need["id"], "reservations": [res_shu["id"], res_arm["id"]],
            "suppliers": sups, "contracts": contracts, "delegations": [deleg["id"], pdeleg["id"]],
            "purchase_orders": [o1["id"], o2["id"], o3["id"], o4["id"], o5["id"]], **prod}


def _seed_production(cmd, login, people, svc, kits, atx_v1) -> dict:
    """Технология и производство (С5 шаги 1–6, D-36). Нормы, времена и номера ВЫМЫШЛЕНЫ.

    * ТП «Сборка и наладка шкафов управления» (АТХ, ред. 1): составила t.fedorova, утвердил a.grishin (не автор);
      спецификация — зафиксированная версия расчёта АТХ v1 (состав без цен).
    * ТП электрооборудования (ЭМ) — черновик t.fedorova (ещё не утверждён).
    * Наряд № 1 по ТП АТХ на 1 комплект: выпустила РП a.smirnova (мастер, A-42). Операция 10 выполнена (k.pavlov),
      операция 20 в работе с сообщением о проблеме (k.pavlov), 30 назначена r.sergeev, 40 — без исполнителя.
      Время событий — «по часам устройства» в прошлом относительно момента наполнения. Материалы на наряд со склада
      ещё не выдавались (ШУ-1 и АРМ — «Не выдано полностью»): закрыть наряд нельзя, пока склад не выдаст их на наряд
      (раздел «Склад» → «Выдать» с выбором наряда). Остальные демо-данные склада при этом не меняются.
    """
    tech, tech2, rp = login("t.fedorova"), login("a.grishin"), login("a.smirnova")
    k1 = login("k.pavlov")
    tp1 = cmd(tech, "tp.create", {"id": str(uuid.uuid4()), "kit_id": kits["АТХ"], "title": "Сборка и наладка шкафов управления",
                                  "spec_version_id": atx_v1, "note": "По схеме ФСА изм. 1", "operations": [
        {"op_no": 10, "name": "Сборка шкафа управления", "work_center": "Участок сборки", "norm_min": "240"},
        {"op_no": 20, "name": "Монтаж кабельных соединений", "work_center": "Участок сборки", "norm_min": "90"},
        {"op_no": 30, "name": "Проверка изоляции и прозвонка", "work_center": "Испытательный стенд", "norm_min": "45"},
        {"op_no": 40, "name": "Загрузка и наладка ПЛК", "work_center": "Испытательный стенд", "norm_min": "120",
         "note": "Версия ПО — по паспорту проекта"}]})["tech_process"]
    tp1 = cmd(tech2, "tp.approve", {"id": tp1["id"]}, tp1["version"])["tech_process"]
    with svc.engine.connect() as c:
        em_v1 = c.execute(sa.select(calc_versions.c.id).where(calc_versions.c.kit_id == kits["ЭМ"])).scalar_one()
    tp2 = cmd(tech, "tp.create", {"id": str(uuid.uuid4()), "kit_id": kits["ЭМ"], "title": "Сборка щитов 0,4 кВ",
                                  "spec_version_id": em_v1, "operations": [
        {"op_no": 10, "name": "Сборка щита", "work_center": "Участок сборки", "norm_min": "300"},
        {"op_no": 20, "name": "Испытание повышенным напряжением", "work_center": "Испытательный стенд", "norm_min": "60"}]})["tech_process"]
    r = cmd(rp, "wo.release", {"id": str(uuid.uuid4()), "tp_id": tp1["id"], "qty": 1, "title": "Шкафы управления котельной № 3",
                               "assignees": {"10": people["k.pavlov"], "20": people["k.pavlov"], "30": people["r.sergeev"]}})
    wo = r["work_order"]
    ops = {o["op_no"]: o for o in r["wo_operations"]}
    now = svc.now()

    def ev(tok, op_no, kind, minutes_ago, **kw):
        """Событие исполнителя с временем устройства «minutes_ago минут назад»."""
        dt = (now - timedelta(minutes=minutes_ago)).isoformat()
        return cmd(tok, "wo.op_event", {"id": ops[op_no]["id"], "kind": kind, "device_time": dt, **kw})
    ev(k1, 10, "start", 300)
    ev(k1, 10, "pause", 240)                 # обед
    ev(k1, 10, "resume", 200)
    ev(k1, 10, "finish", 70)
    ev(k1, 20, "start", 60)
    ev(k1, 20, "problem", 25, note="Не хватает наконечников НШВИ 1,5 — взял со склада участка, нужна выдача на наряд")
    # r.sergeev пока не начинал: операция 30 ждёт выполнения операции 20.
    return {"tech_processes": [tp1["id"], tp2["id"]], "work_orders": [wo["id"]]}


def _seed_lifecycle(cmd, login, people, svc, tp_id, items, whs, docs) -> dict:
    """Экземпляры изделий и паспорт (С5 шаги 7–9, D-38). Номера, даты, получатели и тексты ВЫМЫШЛЕНЫ.

    * Наряд № 2 по ТП АТХ на 1 комплект («Пилотный комплект — котельная № 1»): выпустила a.smirnova, все операции
      выполнил k.pavlov (вчера), материалы со склада выданы на наряд (перед выдачей кладовщик принял на СК-1 ровно
      столько ШУ-1 и АРМ, сколько нужно, — остатки, резервы и потребность прочих демо-данных не меняются), наряд закрыт.
    * Экземпляр «АТХ № 0001 (серия 2026)» зарегистрирован по наряду № 2; паспорт-черновик (составил ведущий инженер
      m.lebedev): технические данные и свидетельство о приёмке заполнены, раздел о гарантиях — пусто (текст утверждает
      компания), одно испытание, ссылка на схему АТХ-001 (версия 3). Экземпляр передан заказчику по акту.
    * Наряд № 1 (в работе) — без изменений: на нём по-прежнему показываются операции, проблема и невыданные материалы.
    """
    rp, k1, wh, lead = login("a.smirnova"), login("k.pavlov"), login("i.belova"), login("m.lebedev")
    r = cmd(rp, "wo.release", {"id": str(uuid.uuid4()), "tp_id": tp_id, "qty": 1, "title": "Пилотный комплект — котельная № 1",
                               "assignees": {str(n): people["k.pavlov"] for n in (10, 20, 30, 40)}})
    wo, ops = r["work_order"], sorted(r["wo_operations"], key=lambda o: o["op_no"])
    start = svc.now() - timedelta(days=1, hours=8)
    for i, op in enumerate(ops):
        t0 = start + timedelta(minutes=150 * i)
        cmd(k1, "wo.op_event", {"id": op["id"], "kind": "start", "device_time": t0.isoformat()})
        cmd(k1, "wo.op_event", {"id": op["id"], "kind": "finish", "device_time": (t0 + timedelta(minutes=120)).isoformat(), "qty_done": 1})
    # Материалы на наряд: складские позиции снимка (ШУ-1 ×3, АРМ ×2 на комплект) — приход «под наряд» и выдача.
    for code, qty, src in (("ШУ-1", "3", "ТН № 431 от ООО «Шкафы-Про» (пилотный комплект)"),
                           ("АРМ", "2", "ТН № 81 от ООО «ИТ-Снаб» (пилотный комплект)")):
        cmd(wh, "stock.receive", {"id": str(uuid.uuid4()), "warehouse_id": whs["СК-1"], "item_id": items[code], "qty": qty, "source_doc": src})
        cmd(wh, "stock.issue", {"id": str(uuid.uuid4()), "warehouse_id": whs["СК-1"], "item_id": items[code], "qty": qty,
                                "project_id": wo["project_id"], "work_order_id": wo["id"], "reason": "Выдача на наряд № 2"})
    with svc.engine.connect() as c:
        ver = c.execute(sa.select(work_orders.c.version).where(work_orders.c.id == wo["id"])).scalar_one()
    wo = cmd(rp, "wo.close", {"id": wo["id"]}, ver)["work_order"]
    inst = cmd(rp, "instance.register", {"work_order_id": wo["id"], "series": "2026",
                                         "items": [{"id": str(uuid.uuid4()), "serial_no": "0001"}]})["product_instances"][0]
    day = (svc.now() - timedelta(days=1)).date().isoformat()
    pp = cmd(lead, "passport.create", {"id": str(uuid.uuid4()), "instance_id": inst["id"], "fields": {
        "tech_data": "Напряжение питания 380 В, 50 Гц; потребляемая мощность до 2,5 кВт; степень защиты оболочки IP54.",
        "packing": "Упакован на участке сборки, тара — деревянный ящик.",
        "acceptance": "Комплект изготовлен и принят в соответствии с технологическим процессом и признан годным к эксплуатации."},
        "tests": [{"name": "Проверка сопротивления изоляции", "result": "pass", "date": day, "protocol": "031-2026"}],
        "documents": [{"document_id": docs["АТХ-001"], "version_no": 3}]})["passport"]
    inst = cmd(rp, "instance.handover", {"id": inst["id"], "date": svc.now().date().isoformat(), "recipient": "Иванов П. С., главный инженер",
                                         "document": "Акт приёма-передачи № 27 (пилотный комплект)"}, inst["version"])["product_instance"]
    return {"lifecycle_work_order": wo["id"], "product_instances": [inst["id"]], "passports": [pp["id"]]}


def _seed_quality(cmd, login, people, svc, instance_id, kits, batch_wo) -> dict:
    """Рекламация, CAPA и запрос на изменение (С5 шаг 10, D-39). Номера писем и тексты ВЫМЫШЛЕНЫ.

    * Рекламация № 1 по «АТХ № 0001 (серия 2026)» — от заказчика (письмо): зарегистрировала РП a.smirnova, анализ
      причины поручен m.lebedev, план CAPA утвердила a.smirnova: корректирующее действие (исполнитель d.kuznetsov —
      выполнено) и предупреждающее (исполнитель m.lebedev — ещё не выполнено). Состояние «Действия»: дальше —
      отметка второго действия, затем проверка эффективности ДРУГИМ человеком (например, ГИП p.volkov).
    * Запрос на изменение № 1 по этой рекламации подал d.kuznetsov — ждёт оценки влияния (РП или ГИП проекта).
    * D-40: у рекламации № 1 тяжесть «значительный», категория «документация» (указана при плане), срок ответа
      заказчику — через 10 дней, промежуточный ответ уже отправлен (срок закрыт).
    * D-40: рекламация № 2 — на партию (наряд № 2) без заводского номера; зарегистрировала ОТК l.egorova, тяжесть
      «незначительный», срок ответа — завтра (видно «срок подходит»), ответа ещё нет.
    """
    rp, eng, otk = login("a.smirnova"), login("d.kuznetsov"), login("l.egorova")
    today = svc.now().date()
    cid = str(uuid.uuid4())
    c = cmd(rp, "claim.register", {"id": cid, "instance_id": instance_id, "received_date": today.isoformat(), "source": "customer",
                                   "reference": "Письмо заказчика № 45 (пилотный комплект)", "severity": "major",
                                   "response_due": (today + timedelta(days=10)).isoformat(),
                                   "description": "При пуске насоса шкаф управления не переходит в автоматический режим; "
                                                  "на панели — сообщение об отсутствии сигнала датчика давления."})["claim"]
    c = cmd(rp, "claim.start_analysis", {"id": cid, "responsible_id": people["m.lebedev"]}, c["version"])["claim"]
    a1, a2 = str(uuid.uuid4()), str(uuid.uuid4())
    due = (today + timedelta(days=14)).isoformat()
    c = cmd(rp, "claim.plan", {"id": cid, "category": "design", "cause": "Перепутаны жилы кабеля датчика давления на клеммнике: в схеме подключения "
                                                   "нумерация клемм не совпадает с маркировкой шкафа.",
                               "actions": [{"id": a1, "kind": "corrective", "owner_id": people["d.kuznetsov"], "due_date": due,
                                            "description": "Переподключить датчик на объекте, проверить автоматический режим"},
                                           {"id": a2, "kind": "preventive", "owner_id": people["m.lebedev"], "due_date": due,
                                            "description": "Привести нумерацию клемм в схеме подключения к маркировке шкафа"}]},
            c["version"])["claim"]
    c = cmd(eng, "claim.action_done", {"id": cid, "action_id": a1, "result": "Датчик переподключён, автоматический режим работает"})["claim"]
    cmd(rp, "claim.respond", {"id": cid, "kind": "interim", "response_date": today.isoformat(), "reference": "Исх. № 12 (пилотный комплект)",
                              "summary": "Причина найдена, датчик на объекте переподключён; схема будет исправлена новой версией."}, c["version"])
    cid2 = str(uuid.uuid4())
    cmd(otk, "claim.register", {"id": cid2, "scope": "batch", "work_order_id": batch_wo, "received_date": today.isoformat(),
                                "source": "customer", "severity": "minor", "response_due": (today + timedelta(days=1)).isoformat(),
                                "reference": "Письмо заказчика № 47 (пилотный комплект)",
                                "description": "На дверцах шкафов партии не закреплены таблички с обозначением шкафа; номера изделий "
                                               "заказчик не указал."})
    rid = str(uuid.uuid4())
    cmd(eng, "ecr.submit", {"id": rid, "project_id": c["project_id"], "kit_id": kits["АТХ"], "claim_id": cid,
                            "title": "Нумерация клемм в схеме подключения",
                            "reason": "Рекламация № 1: нумерация клемм в схеме не совпадает с маркировкой шкафа.",
                            "proposal": "Выпустить новую версию схемы АТХ-001 с нумерацией по маркировке шкафа."})
    # Напоминания о сроке ответа создаются при обновлении (pull) тех, кто ведёт рекламацию. Создаём их сразу — как
    # если бы РП, ГИП, его замещающий и ОТК уже входили: самопроверка стенда (только чтение) ничего не добавит.
    with svc.engine.begin() as c:
        for lg in ("a.smirnova", "p.volkov", "m.lebedev", "l.egorova"):
            svc._quality_reminders(c, people[lg], svc.now())
    return {"claims": [cid, cid2], "change_requests": [rid]}


def _seed_org(cmd, adm, people) -> dict:
    """Оргструктура (D-44, ответ на A-48 п. 1) — администратор заводит дерево обычными командами. Названия ВЫМЫШЛЕНЫ.

    Головной офис (филиал, Волгоград)
      ├─ Инженерная дирекция (структура) — руководитель e.orlova: она сама, p.volkov (ГИП), a.smirnova (РП)
      │    └─ Отдел АСУ ТП (отдел, руководитель не назначен) — d.kuznetsov, m.lebedev: их отчёты уходят e.orlova
      ├─ Производственный участок (отдел) — технологи, производство, ОТК (отдел технического контроля)
      └─ Коммерческая служба (структура) — руководитель v.zaitseva (роли «Руководитель отдела» у неё нет — она
         всё равно проверяет отчёты своих сотрудников, D-45): закупка, склад, бухгалтерия
    Филиал Астрахань (филиал) └─ Сервисный отдел — пока пустой (второй корень дерева).
    n.sokolov в оргструктуру не включён (виден администратору как «вне оргструктуры»).
    """
    def unit(kind, name, parent=None, head=None, code=None):
        """Завести подразделение (head — логин руководителя) и вернуть его id."""
        uid = str(uuid.uuid4())
        cmd(adm, "org.unit_create", {"id": uid, "kind": kind, "name": name, "parent_id": parent,
                                     "head_user_id": people.get(head) if head else None, "code": code})
        return uid
    ho = unit("branch", "Головной офис", code="01")
    eng_dir = unit("structure", "Инженерная дирекция", ho, "e.orlova", "01.1")
    asu = unit("department", "Отдел АСУ ТП", eng_dir, code="01.1.1")
    shop = unit("department", "Производственный участок", ho, code="01.2")
    com = unit("structure", "Коммерческая служба", ho, "v.zaitseva", "01.3")
    astr = unit("branch", "Филиал Астрахань", code="02")
    service = unit("department", "Сервисный отдел", astr, code="02.1")
    for lg, u, pos in (("e.orlova", eng_dir, "Директор по инжинирингу"), ("p.volkov", eng_dir, "Главный инженер проекта"),
                       ("a.smirnova", eng_dir, "Руководитель проектов"), ("d.kuznetsov", asu, "Инженер-проектировщик"),
                       ("m.lebedev", asu, "Ведущий инженер"), ("t.fedorova", shop, "Технолог"), ("a.grishin", shop, "Технолог"),
                       ("k.pavlov", shop, "Сборщик-монтажник"), ("r.sergeev", shop, "Наладчик"),
                       ("l.egorova", shop, "Контролёр ОТК"), ("v.zaitseva", com, "Коммерческий директор"),
                       ("s.morozov", com, "Специалист по закупкам"), ("i.belova", com, "Кладовщик"),
                       ("o.nikitina", com, "Бухгалтер")):
        cmd(adm, "org.member_set", {"user_id": people[lg], "unit_id": u, "position": pos})
    return {"org_units": [ho, eng_dir, asu, shop, com, astr, service]}


def _seed_worktime(cmd, login, people, svc, pid) -> dict:
    """Дневные отчёты (этап 7, D-42) — через обычные команды, как в работе. Тексты ВЫМЫШЛЕНЫ.

    * d.kuznetsov: отчёт за вчера отправлен на проверку (проект П-2026-014 с задачей и общие дела отдела) — ждёт
      руководителя отдела e.orlova; за сегодня — черновик (смена и одна строка, можно дополнить и отправить).
    * m.lebedev: отчёт за позавчера принят e.orlova; отчёт за вчера возвращён с причиной — его нужно исправить и
      отправить снова.
    * D-43: у d.kuznetsov за позавчера — отчёт, собранный программой по действиям за день («На самопроверке»: строка
      «по таймеру» и строки «оценка программы»); руководителю он уйдёт после самопроверки. Отправка всех отчётов —
      с отметками самопроверки (A-48 п. 4).
    """
    eng, lead, head = login("d.kuznetsov"), login("m.lebedev"), login("e.orlova")
    today = svc.now().date()
    with svc.engine.connect() as c:
        task_id = c.execute(sa.select(tasks.c.id).where(tasks.c.project_id == pid).order_by(tasks.c.title)).scalar()

    sc = {"shift": True, "lines": True, "hours": True}      # самопроверка перед отправкой — все отметки

    def report(tok, days_ago, lines, start="09:00", end="18:00", brk=60, comment=None, **extra):
        """Создать отчёт за день (days_ago дней назад) и вернуть его (с версией)."""
        return cmd(tok, "report.create", {"id": str(uuid.uuid4()), "work_date": (today - timedelta(days=days_ago)).isoformat(),
                                          "shift_start": start, "shift_end": end, "break_minutes": brk, "lines": lines,
                                          **({"comment": comment} if comment else {}), **extra})["daily_report"]

    r1 = report(eng, 1, [{"project_id": pid, "task_id": task_id, "minutes": 330, "note": "Схема автоматизации: раздел насосной"},
                         {"project_id": pid, "minutes": 90, "note": "Совещание с заказчиком по замечаниям"},
                         {"minutes": 60, "note": "Обучение по охране труда"}])
    cmd(eng, "report.submit", {"id": r1["id"], "self_check": sc}, r1["version"])
    r2 = report(eng, 0, [{"project_id": pid, "task_id": task_id, "minutes": 180, "note": "Схема автоматизации: щит управления"}],
                end="18:00", comment="Вторую половину дня допишу вечером")
    r3 = report(lead, 2, [{"project_id": pid, "minutes": 420, "note": "Проверка схем подключения"},
                          {"minutes": 60, "note": "Планёрка отдела"}])
    r3 = cmd(lead, "report.submit", {"id": r3["id"], "self_check": sc}, r3["version"])["daily_report"]
    cmd(head, "report.review", {"id": r3["id"], "decision": "accept"}, r3["version"])
    r4 = report(lead, 1, [{"project_id": pid, "minutes": 480, "note": "Работа по проекту"}], start="08:00", end="17:00")
    r4 = cmd(lead, "report.submit", {"id": r4["id"], "self_check": sc}, r4["version"])["daily_report"]
    cmd(head, "report.review", {"id": r4["id"], "decision": "return",
                                "comment": "Распишите по задачам: «работа по проекту» — слишком общо."}, r4["version"])
    # Собран программой (как это делает клиент сотрудника назавтра): смена по первому и последнему действию.
    r5 = report(eng, 2, [{"project_id": pid, "minutes": 95, "note": "Наряд · Сборка шкафа управления", "source": "measured"},
                         {"project_id": pid, "task_id": task_id, "minutes": 240, "source": "estimated",
                          "note": "Изменить задачу: «Схема автоматизации»; добавить версию документа"},
                         {"minutes": 125, "source": "estimated", "note": "Прочие действия в программе: уведомления, поиск"}],
                start="08:45", end="17:15", brk=50, origin="auto",
                comment="Собран программой по 14 действиям за день — проверьте.",
                summary={"actions": 14, "measured_minutes": 95, "lines": 3})
    return {"daily_reports": [r1["id"], r2["id"], r3["id"], r4["id"], r5["id"]]}
