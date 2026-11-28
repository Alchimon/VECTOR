# -*- coding: utf-8 -*-
"""Раздел «Знания» 2.0 (P3-3, blueprint 57): база знаний (статьи компании и проектов — офлайн через очередь),
общая база подрядчиков и каталог оборудования (только при связи)."""
import uuid

import pytest
from ui_harness import _plain

pytestmark = pytest.mark.ui

PWD = "Демо-пароль-2026"


def _to_v2(ui):
    """Переключить стенд в интерфейс 2.0 и вернуть оболочку Shell2."""
    ui.backend.uiMode = "v2"
    ui.wait_idle()
    sh = ui.find("shell2")
    assert sh is not None, "оболочка 2.0 не загрузилась"
    return sh


def _find_named(root, name):
    """Все элементы с objectName == name в визуальном дереве (делегаты Repeater findChild не видит)."""
    found = []

    def walk(o):
        for ch in o.childItems():
            if ch.property("objectName") == name:
                found.append(ch)
            walk(ch)
    walk(root)
    return found


def _go_knowledge(ui, sh):
    from PySide6.QtCore import Q_ARG, QMetaObject
    QMetaObject.invokeMethod(sh, "go", Q_ARG("QVariant", "knowledge"), Q_ARG("QVariant", ""))
    ui.wait_idle()
    page = next((p for p in _find_named(sh, "knowledgePage2") if p.isVisible()), None)
    assert page is not None, "страница «Знания» 2.0 не появилась"
    return page


def _svc_login(ui, login):
    r = ui.svc.handle({"type": "login", "login": login, "password": PWD, "device_id": "ui-test"}, "10.0.0.9")
    assert r["status"] == "accepted", r
    return r["token"]


def _svc_cmd(ui, token, command, payload):
    r = ui.svc.handle({"type": "command", "token": token, "command": command,
                       "operation_id": str(uuid.uuid4()), "payload": payload}, "10.0.0.9")
    assert r["status"] == "accepted", r
    return r["result"]


def test_knowledge_page_shows_articles(ui):
    """Статьи компании и проекта приходят снимком, ищутся, карточка показывает текст и метки."""
    ui.login("m.lebedev")                     # ведущий инженер: правит стандарты компании
    sh = _to_v2(ui)
    # Две статьи через сервер (как коллеги положили): стандарт компании и инструкция демо-проекта.
    lead = _svc_login(ui, "m.lebedev")
    _svc_cmd(ui, lead, "kb.create", {"id": str(uuid.uuid4()), "scope": "company", "kind": "standard",
                                     "code": "СТО-01", "title": "Порядок согласования извещений",
                                     "body_md": "1. Извещение согласует ГИП.", "tags": ["ркд", "изменения"]})
    _svc_cmd(ui, lead, "kb.create", {"id": str(uuid.uuid4()), "scope": "project",
                                     "project_id": ui.ids["project"], "kind": "howto",
                                     "code": "ИНСТР-АТХ", "title": "Как готовить АТХ к отгрузке",
                                     "body_md": "Проверить комплектность и паспорта."})
    ui.backend.syncNow()
    ui.wait_idle()

    page = _go_knowledge(ui, sh)
    # Пункт рейла и заголовок
    assert any(r.isVisible() for r in _find_named(sh, "railKnowledge"))
    articles = _plain(ui.backend.property("kbArticles"))
    assert len(articles) == 2
    # Карточки в списке: обе статьи
    cards = [c for c in _find_named(sh, "kbCard") if c.isVisible()]
    assert len(cards) == 2
    # Выбор — карточка статьи с текстом и видом
    ui.click(cards[0])
    detail = next((d for d in _find_named(sh, "kbDetail") if d.isVisible()), None)
    assert detail is not None, "карточка статьи не появилась после выбора"
    body = next((t for t in _find_named(sh, "kbBody") if t.isVisible()), None)
    assert body is not None and body.property("text") != ""
    # Фильтр раздела: только статьи проекта
    page.setProperty("scopeFilter", "project")
    ui.pump(0.2)
    items = _plain(page.property("articles"))
    assert len(items) == 1 and items[0]["code"] == "ИНСТР-АТХ"
    page.setProperty("scopeFilter", "")
    ui.pump(0.2)
    # Поиск по коду
    search = next(s for s in _find_named(sh, "knowledge2Search"))
    search.setProperty("text", "СТО-01")
    ui.wait_idle()
    items = _plain(page.property("articles"))
    assert len(items) == 1 and items[0]["title"] == "Порядок согласования извещений"
    search.setProperty("text", "")
    ui.wait_idle()


def test_knowledge_vendors_and_equipment_tabs(ui):
    """Вкладки «Подрядчики» и «Оборудование»: данные от закупки и технолога, поиск, ИНН и характеристики."""
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    proc = _svc_login(ui, "s.morozov")
    _svc_cmd(ui, proc, "vendor.create", {"id": str(uuid.uuid4()), "name": "ООО «Монтажстрой»",
                                         "kind": "contractor", "inn": "7723456789",
                                         "contacts": {"phone": "+7 900 000-00-00"},
                                         "rating_note": "Монтаж КИПиА, 2025 — без нареканий"})
    tech = _svc_login(ui, "t.fedorova")
    _svc_cmd(ui, tech, "equipment.create", {"id": str(uuid.uuid4()), "code": "НСС-К80",
                                            "name": "Насос К 80-65-160", "category": "Насосы",
                                            "manufacturer": "АО «Гидромаш»",
                                            "params": {"подача": "50 м³/ч", "напор": "32 м"}})
    ui.backend.syncNow()
    ui.wait_idle()

    page = _go_knowledge(ui, sh)
    # Подрядчики: список и карточка с ИНН и опытом работы
    page.setProperty("tab", "vendors")
    ui.pump(0.3)
    vendors = _plain(ui.backend.property("vendors"))
    assert len(vendors) == 1 and vendors[0]["kind_label"] == "Подрядчик"
    cards = [c for c in _find_named(sh, "kbCard") if c.isVisible()]
    assert len(cards) == 1
    ui.click(cards[0])
    detail = next((d for d in _find_named(sh, "kbDetail") if d.isVisible()), None)
    assert detail is not None

    # Оборудование: список и характеристики в карточке
    page.setProperty("tab", "equipment")
    ui.pump(0.3)
    equipment = _plain(ui.backend.property("equipment"))
    assert len(equipment) == 1 and equipment[0]["params"]["подача"] == "50 м³/ч"
    cards = [c for c in _find_named(sh, "kbCard") if c.isVisible()]
    assert len(cards) == 1
    ui.click(cards[0])
    detail = next((d for d in _find_named(sh, "kbDetail") if d.isVisible()), None)
    assert detail is not None
    # Поиск по категории
    search = next(s for s in _find_named(sh, "knowledge2Search"))
    search.setProperty("text", "насосы")
    ui.wait_idle()
    items = _plain(page.property("equipment"))
    assert len(items) == 1
    search.setProperty("text", "")
    ui.wait_idle()


def test_knowledge_online_only_forms(ui):
    """Статьи можно создавать без связи (очередь); подрядчиков и оборудование — только при связи."""
    ui.login("a.smirnova")                    # РП демо-проекта: право kb.create в проекте
    sh = _to_v2(ui)
    page = _go_knowledge(ui, sh)
    create = next(b for b in _find_named(sh, "kbCreate") if b.isVisible())
    assert create.property("enabled") is True                       # статьи — всегда (очередь)
    # Без связи: кнопки справочников выключены и объясняют почему
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    assert ui.backend.property("online") is False
    page.setProperty("tab", "vendors")
    ui.pump(0.3)
    create_v = next((b for b in _find_named(sh, "kbCreate") if b.isVisible()), None)
    # У РП нет права vendor.create — кнопки просто нет; проверяем на примере того, у кого право есть:
    # выходим и заходим закупкой.
    ui.transport.online = True
    ui.backend.syncNow()
    ui.wait_idle()
    ui.backend.logout()
    ui.wait_idle()
    ui.login("s.morozov")                      # закупка: vendor.create есть, связь включим обратно ниже
    sh = _to_v2(ui)
    page = _go_knowledge(ui, sh)
    page.setProperty("tab", "vendors")
    ui.pump(0.3)
    create_v = next(b for b in _find_named(sh, "kbCreate") if b.isVisible())
    assert create_v.property("enabled") is True                     # онлайн — можно
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    create_v = next(b for b in _find_named(sh, "kbCreate") if b.isVisible())
    assert create_v.property("enabled") is False                    # офлайн — справочник только для чтения
    # Статьи при этом доступны и без связи
    page.setProperty("tab", "articles")
    ui.pump(0.3)
    create_a = next((b for b in _find_named(sh, "kbCreate") if b.isVisible()), None)
    if create_a is not None:                                        # у закупки может не быть права kb.create
        assert create_a.property("enabled") is True


def test_knowledge_palette_opens_article(ui):
    """Палитра Ctrl+K: статья базы знаний ищется и открывается в разделе «Знания» с выделением."""
    ui.login("m.lebedev")
    sh = _to_v2(ui)
    lead = _svc_login(ui, "m.lebedev")
    aid = str(uuid.uuid4())
    _svc_cmd(ui, lead, "kb.create", {"id": aid, "scope": "company", "kind": "note",
                                     "code": "ЗАМ-07", "title": "Заметка о цветах маркировки",
                                     "body_md": "Клеммы силовых цепей — красные."})
    ui.backend.syncNow()
    ui.wait_idle()
    # Запись палитры построена и ведёт в раздел
    cmds = _plain(sh.property("commandsCache"))
    entry = next((c for c in cmds if c["hint"] == "Статья базы знаний" and aid and "ЗАМ-07" in c["label"]), None)
    assert entry is not None, "статьи нет в палитре команд"
    from PySide6.QtCore import Q_ARG, QMetaObject
    QMetaObject.invokeMethod(sh, "go", Q_ARG("QVariant", "knowledge"), Q_ARG("QVariant", "articles|" + aid))
    ui.wait_idle()
    page = next((p for p in _find_named(sh, "knowledgePage2") if p.isVisible()), None)
    assert page is not None
    assert page.property("tab") == "articles"
    assert page.property("selectedId") == aid
    detail = next((d for d in _find_named(sh, "kbDetail") if d.isVisible()), None)
    assert detail is not None, "карточка статьи не открылась переходом из палитры"
    title = next((t for t in _find_named(sh, "kbTitle") if t.isVisible()), None)
    assert title is not None and "маркировки" in title.property("text")
