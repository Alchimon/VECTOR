# -*- coding: utf-8 -*-
"""UI-проверки блока «ПНР, испытания (FAT) и доставка» на карточке проекта (D-70, этап 6 blueprint 52).

Что проверяем «глазами пользователя»:
* блок показывает запросы ПНР, FAT-сессии (со снимком чек-листа), документы доставки и подсказку шлюза отгрузки;
* создание запроса ПНР через форму доходит до сервера; статус ведёт РП;
* замечание добавляется к идущему испытанию, закрытие — с причиной; итог FAT неизменяем;
* шлюз отгрузки: без успешного финального FAT баннер «Отгрузка заблокирована», после успешного — нет;
* без связи кнопки действий недоступны, чтение работает.
"""
import uuid

import pytest

pytestmark = pytest.mark.ui


def _block(ui):
    """Срез «ПНР, испытания и доставка» демо-проекта из backend."""
    return ui.backend.projectPnrFat(ui.ids["project"], ui.backend.property("dataRevision"))


def _open_project(ui):
    """Войти РП и открыть демо-проект."""
    ui.login("a.smirnova")
    ui.go("projects", ui.ids["project"])
    ui.wait_idle()


def _cmd(ui, command, payload):
    """Выполнить команду через клиент (как из формы) и обновить снимок."""
    r = ui.backend.client.execute(command, payload)
    ui.backend.syncNow()
    ui.wait_idle()
    return r


def _template(ui, active=True):
    """Активный FAT-шаблон через клиент (в UI-тесте права head даём админской сессией сервера)."""
    svc = ui.svc
    r = svc.handle({"type": "login", "login": "e.orlova", "password": "Демо-пароль-2026", "device_id": "ui-test"}, "10.0.0.9")
    tok = r["token"]
    tid = str(uuid.uuid4())
    res = svc.handle({"type": "command", "token": tok, "command": "checklist.create",
                      "operation_id": str(uuid.uuid4()),
                      "payload": {"id": tid, "kind": "fat", "name": "FAT (типовой)",
                                  "sections": [{"code": "power", "title": "Питание"},
                                               {"code": "io", "title": "Входы/выходы"}],
                                  "severities": [{"code": "minor", "label": "Несущественное", "blocking": False},
                                                 {"code": "crit", "label": "Блокирующее", "blocking": True}]}},
                     "10.0.0.9")
    assert res["status"] == "accepted", res
    if not active:
        svc.handle({"type": "command", "token": tok, "command": "checklist.archive",
                    "operation_id": str(uuid.uuid4()), "payload": {"id": tid}}, "10.0.0.9")
    return tid


def _doc_template(ui):
    svc = ui.svc
    r = svc.handle({"type": "login", "login": "e.orlova", "password": "Демо-пароль-2026", "device_id": "ui-test"}, "10.0.0.9")
    tok = r["token"]
    tid = str(uuid.uuid4())
    res = svc.handle({"type": "command", "token": tok, "command": "doc_template.create",
                      "operation_id": str(uuid.uuid4()),
                      "payload": {"id": tid, "code": "TOR12", "title": "ТОРГ-12",
                                  "body": "Проект {{project.code}}, заказчик {{customer.name}}, "
                                          "комплект {{kit.code}}, транспорт {{truck}}"}},
                     "10.0.0.9")
    assert res["status"] == "accepted", res
    return tid


def test_block_seeded_demo_state(ui):
    """Демо-данные (D-76): запрос ПНР «Назначена дата», идущий FAT с открытым блокирующим замечанием и
    выписанный акт уже в блоке; шлюз отгрузки закрыт (испытание не завершено); шаблон FAT активен."""
    _open_project(ui)
    v = _block(ui)
    assert len(v["pnr"]) == 1 and v["pnr"][0]["status"] == "scheduled"
    assert v["pnr"][0]["kind_label"] == "ПНР на площадке" and v["pnr"][0]["need_power"] is True
    assert len(v["fats"]) == 1 and v["fats"][0]["status"] == "in_progress"
    assert v["fats"][0]["counts"]["open_blocking"] == 1
    assert len(v["docs"]) == 1 and "Акт выполнения пусконаладочных работ" in v["docs"][0]["template_title"]
    # Шлюз: идёт финальный FAT — баннер блокировки с причиной.
    assert v["shipping_gate"]["blocked"] is True and "ещё идёт" in v["shipping_gate"]["reason"]
    # Активный шаблон из демо — «Начать FAT» доступно.
    assert v["fat_templates"]


def test_pnr_create_and_status_flow(ui):
    _open_project(ui)
    r = _cmd(ui, "pnr.create", {"id": str(uuid.uuid4()), "project_id": ui.ids["project"],
                                "kind": "internal", "title": "Наладка шкафа АВР",
                                "need_power": True, "planned_on": "2026-12-01"})
    assert r.status == "accepted", r
    v = _block(ui)
    rows = [x for x in v["pnr"] if x["title"] == "Наладка шкафа АВР"]   # рядом — демо-запрос (D-76)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "requested" and row["need_power"] is True and row["kind_label"] == "Внутренняя наладка"
    # РП ведёт статус: назначить дату → выполнена.
    _cmd(ui, "pnr.set_status", {"id": row["id"], "to": "scheduled"})
    v = _block(ui)
    row = next(x for x in v["pnr"] if x["id"] == row["id"])
    assert row["status"] == "scheduled"
    labels = {a["to"]: a for a in row["actions"]}
    assert "in_progress" in labels and "cancelled" in labels


def test_fat_findings_gate_and_doc(ui):
    _open_project(ui)
    tpl = _template(ui)
    doc_tpl = _doc_template(ui)
    ui.backend.syncNow()
    ui.wait_idle()
    pid = ui.ids["project"]
    # Начинаем финальный FAT, добавляем блокирующее замечание.
    fat_id = str(uuid.uuid4())
    r = _cmd(ui, "fat.create", {"id": fat_id, "project_id": pid, "kind": "final", "template_id": tpl})
    assert r.status == "accepted", r
    v = _block(ui)
    fat = next(f for f in v["fats"] if f["id"] == fat_id)
    assert fat["status"] == "in_progress"
    assert fat["template_name"] == "FAT (типовой)"
    assert fat["section_options"] and fat["severity_options"]
    _cmd(ui, "fat.add_finding", {"id": str(uuid.uuid4()), "fat_id": fat["id"], "section_code": "power",
                                 "severity": "crit", "text": "Индикация аварии не срабатывает"})
    v = _block(ui)
    frow = next(f for f in v["fats"] if f["id"] == fat_id)
    assert frow["counts"]["open_blocking"] == 1
    fnd = frow["findings"][0]
    assert fnd["blocking"] is True and fnd["severity_label"] == "Блокирующее" and fnd["status_label"] == "Открыто"
    # Завершаем неуспешно (с причиной); шлюз: неуспешный финальный — блок.
    _cmd(ui, "fat.finish", {"id": fat["id"], "result": "failed", "reason": "есть блокирующие замечания"})
    v = _block(ui)
    assert next(f for f in v["fats"] if f["id"] == fat_id)["status"] == "failed"
    assert v["shipping_gate"]["blocked"] is True and "блокирующ" in v["shipping_gate"]["reason"]
    # Итог неизменяем: кнопок итога у завершённого нет.
    assert next(f for f in v["fats"] if f["id"] == fat_id)["actions"] == []
    # Закрытие замечания с причиной; итог не переписывается — шлюз всё ещё блок (теперь из-за «неуспешен»).
    _cmd(ui, "fat.close_finding", {"id": fnd["id"], "reason": "Заменён модуль индикации"})
    v = _block(ui)
    assert next(f for f in v["fats"] if f["id"] == fat_id)["findings"][0]["status"] == "closed"
    assert v["shipping_gate"]["blocked"] is True and "неуспешен" in v["shipping_gate"]["reason"]
    # Новое успешное финальное испытание — шлюз открыт.
    fat2_id = str(uuid.uuid4())
    r = _cmd(ui, "fat.create", {"id": fat2_id, "project_id": pid, "kind": "final", "template_id": tpl})
    assert r.status == "accepted", r
    fat2 = next(f for f in _block(ui)["fats"] if f["id"] == fat2_id)
    assert fat2["seq"] == 3                       # seq 1 — демо-FAT (D-76), seq 2 — первая сессия этого теста
    _cmd(ui, "fat.finish", {"id": fat2["id"], "result": "passed"})
    v = _block(ui)
    assert v["shipping_gate"]["blocked"] is False and v["shipping_gate"]["reason"] == ""
    # Документ доставки по форме: автозаполнение + ручное значение.
    kid = ui.ids["kits"]["АТХ"]
    r = _cmd(ui, "delivery_doc.create", {"id": str(uuid.uuid4()), "project_id": pid, "kit_id": kid,
                                         "template_id": doc_tpl, "values": {"truck": "МАЗ В001АА"}})
    assert r.status == "accepted", r
    v = _block(ui)
    docs = [d for d in v["docs"] if d["template_code"] == "TOR12"]   # рядом — демо-акт (D-76)
    assert len(docs) == 1
    doc = docs[0]
    assert "МАЗ В001АА" in doc["rendered"]
    assert "{{truck}}" not in doc["rendered"]


def test_offline_disables_actions(ui):
    _open_project(ui)
    tpl = _template(ui)
    _cmd(ui, "fat.create", {"id": str(uuid.uuid4()), "project_id": ui.ids["project"], "kind": "final",
                            "template_id": tpl})
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    v = _block(ui)
    assert not v["can_manage"]
    assert all(f["actions"] == [] and f["can_add_finding"] is False for f in v["fats"])
    # Чтение офлайн работает: сессии (демо-FAT и созданная выше) и шлюз видны из снимка.
    assert len(v["fats"]) == 2 and v["shipping_gate"]["blocked"] is True
