"""UI-проверки подсказок и расшифровки сокращений (D-37) — настоящий QML, сервер in-process, демо-данные.

Владелец: «аббревиатуры обязательно расшифровывай и делай подсказки для всего». Проверяется:
* у каждой видимой кнопки, пункта меню и вкладки во всех разделах есть непустая подсказка (для разных ролей);
* подсказка действительно всплывает при наведении мыши и расшифровывает сокращение;
* раздел «Сокращения и подсказки» открывается по F1, ищет по сокращению и названию;
* в списках выбора ролей сокращения расшифрованы; QML без предупреждений.
"""
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from tests.ui.test_purchasing_ui import _v, qml_warnings  # noqa: F401

pytestmark = pytest.mark.ui

PAGES = ["customers", "tenders", "projects", "finance", "stock", "procurement", "production", "sync",
         "notifications", "delegations", "admin", "help"]


def _visible_without_tip(ui):
    """Видимые кнопки (VButton: свойство kind), пункты меню (NavItem: active) и вкладки (VSegmented) без подсказки."""
    bad = []
    for it in ui.items("tipText"):
        if it.isVisible() and it.property("kind") is not None and not it.property("tipText"):
            bad.append(("VButton", it.property("text")))
    for it in ui.items("badgeTone"):
        if it.isVisible() and not it.property("tip"):
            bad.append(("NavItem", it.property("text")))
    for it in ui.items("tipText"):
        if it.isVisible() and (it.objectName() or "").startswith("seg-") and not it.property("tipText"):
            bad.append(("VSegmented", it.objectName()))
    return bad


@pytest.mark.parametrize("login", ["a.smirnova", "s.morozov", "t.fedorova", "i.belova", "e.orlova", "admin"])
def test_every_visible_button_nav_item_and_tab_has_a_tip(ui, qml_warnings, login):  # noqa: F811
    ui.login(login)
    bad = set()
    for p in PAGES:
        ui.go(p)
        bad |= set((p, *b) for b in _visible_without_tip(ui))
    assert not bad, f"Без подсказки: {sorted(bad)}"
    assert qml_warnings == []


def test_tooltip_pops_up_on_hover_and_expands_abbreviation(ui, qml_warnings):  # noqa: F811
    ui.login("a.smirnova")
    ui.go("tenders", ui.ids["tender_sub"])
    btn = next(i for i in ui.items("tipText") if i.isVisible() and i.property("text") == "Новое ТКП")
    tip = btn.property("tipText")
    assert "технико-коммерческое предложение" in tip and "без связи" in tip
    c = btn.mapToScene(QPointF(btn.width() / 2, btn.height() / 2))
    QTest.mouseMove(ui.win, c.toPoint())
    ui.pump(0.9)   # задержка показа — 0,5 с
    shown = [i for i in ui.items("text") if i.isVisible() and i.property("text") == tip and i is not btn]
    assert shown, "подсказка не появилась при наведении"
    # Пункт меню с сокращением: пояснение раздела + расшифровка.
    nav = next(i for i in ui.items("badgeTone") if i.property("text") == "Тендеры и ТКП")
    assert "ТКП — технико-коммерческое предложение" in nav.property("tip")
    # Пояснение страницы расшифровывает ТКП прямо в тексте (видно без наведения).
    assert any((i.property("subtitle") or "").startswith("ТКП — технико-коммерческие предложения")
               for i in ui.items("subtitle") if i.isVisible())
    assert qml_warnings == []


def test_help_page_by_f1_lists_and_searches_glossary(ui, qml_warnings):  # noqa: F811
    ui.login("k.pavlov")   # исполнитель: у него нет ни тендеров, ни склада — справка доступна всем
    ui.win.requestActivate()
    ui.pump(0.2)
    QTest.keyClick(ui.win, Qt.Key.Key_F1)
    ui.wait_idle()
    assert ui.page() == "help"
    assert ui.nav()["Справка"] is True
    lst = ui.find("glossaryList")
    total = lst.property("count")
    assert total == len(_v(ui.backend, "glossary")) and total > 50
    search = ui.find("glossarySearch")
    search.setProperty("text", "технологический процесс")
    ui.pump(0.2)
    assert 0 < lst.property("count") < total
    search.setProperty("text", "ТКП")
    ui.pump(0.2)
    assert lst.property("count") >= 1
    search.setProperty("text", "нет-такого-слова")
    ui.pump(0.2)
    assert lst.property("count") == 0
    assert qml_warnings == []


def test_help_page_guide_tab(ui, qml_warnings):  # noqa: F811
    """Встроенное руководство (issue #2): вкладка «Руководство» — разделы слева, текст справа, всё без связи."""
    ui.login("k.pavlov")
    ui.win.requestActivate()
    ui.pump(0.2)
    QTest.keyClick(ui.win, Qt.Key.Key_F1)
    ui.wait_idle()
    assert ui.page() == "help"
    ui.click(ui.find("tabGuide"))
    ui.wait_idle()
    sections = ui.find("guideSections")
    total = sections.property("count")
    assert total >= 20, "руководство должно содержать 21+ раздел"
    body = ui.find("guideBody")
    assert "Сервер — главный" in body.property("text")          # раздел 1 открыт по умолчанию
    assert "![" not in body.property("text")                    # снимки заменены подписями
    # Выбор другого раздела меняет текст.
    page = ui.find("helpPage")
    page.setProperty("guideIdx", 3)                              # «4. Работа без связи»
    ui.pump(0.2)
    assert "очеред" in body.property("text").lower()
    assert qml_warnings == []

def test_role_selects_expand_abbreviations(ui):
    ui.login("admin")
    labels = {r["id"]: r["label"] for r in _v(ui.backend, "roleOptions")}
    assert labels["gip"] == "ГИП (главный инженер проекта)" and labels["rp"] == "РП (руководитель проекта)"
    assert labels["technologist"] == "Технолог"
    proj = {r["id"]: r["label"] for r in _v(ui.backend, "projectRoleOptions")}
    assert proj["gip"] == "ГИП проекта (главный инженер проекта)"
    assert ui.backend.tip("Сторно").startswith("Исправить ошибочную запись")


def test_help_page_about_tab_license_demo(ui):
    """«О программе» (D-81): версия, режим и модули из лицензии. Стенд без файла лицензии — демо: все выпущенные
    модули доступны (moduleOk), навигация не скрыта; ворота лицензии — на сервере (tests/server/test_license_gate.py)."""
    ui.login("k.pavlov")
    ui.go("help")
    ui.click(ui.find("tabAbout"))
    ui.wait_idle()
    lic = _v(ui.backend, "licenseInfo")
    assert lic["mode"] == "demo" and lic["app_version"]
    assert ui.backend.moduleOk("pdm") and ui.backend.moduleOk("supply")
    assert not ui.backend.moduleOk("schema")                      # «В разработке» — раздела ещё нет
    support = ui.find("aboutSupport")
    assert "демо" in support.property("v").lower() or "поддержк" in support.property("v").lower()


def test_about_tab_orbita_status(ui):
    """«О программе» (D-131/D-132): статус подключения к серверу экосистемы — ВЕКТОР Орбита — с адресом."""
    ui.login("k.pavlov")
    ui.go("help")
    ui.click(ui.find("tabAbout"))
    ui.wait_idle()
    st = _v(ui.backend, "orbitaStatus")
    assert st["state"] in ("online", "reconnect", "offline") and st["label"]
    assert st["host"]                                          # адрес Орбиты из настроек стенда


def test_license_banner_readonly_and_grace(ui):
    """Полоса состояния лицензии раздела (D-88): подписка модуля истекла (readonly) — видна на страницах модуля,
    скрыта на платформенных страницах и в разделах активных модулей; льготный период (grace) — мягкое
    предупреждение. Решения о доступе принимает сервер (tests/server/test_license_gate.py); это подсказка."""
    ui.login("k.pavlov")
    lic = {"mode": "subscription", "customer": "ООО «Тест»", "kind": "subscription",
           "modules": [{"id": "pdm", "title_ru": "Проекты", "state": "readonly", "label_ru": "", "detail_ru": ""},
                       {"id": "hr", "title_ru": "Люди", "state": "grace", "label_ru": "", "detail_ru": ""},
                       {"id": "supply", "title_ru": "Склад", "state": "active", "label_ru": "", "detail_ru": ""},
                       {"id": "schema", "title_ru": "Схемы", "state": "planned", "label_ru": "", "detail_ru": ""}]}
    ui.backend.client._set_meta("license", lic)
    ui.backend.dataChanged.emit()
    ui.pump(0.2)
    banner = ui.find("licenseBanner")
    assert banner is not None
    ui.go("projects"); ui.pump(0.2)                       # pdm — readonly
    assert banner.property("visible") is True
    assert "только чтения" in ui.find("licenseBannerText").property("text") or "чтения и выгрузки" in ui.find("licenseBannerText").property("text")
    ui.go("worktime"); ui.pump(0.2)                       # hr — grace
    assert banner.property("visible") is True
    assert "Льготный период" in ui.find("licenseBannerText").property("text")
    ui.go("sync"); ui.pump(0.2)                           # платформа — полосы нет
    assert banner.property("visible") is False
    assert ui.backend.moduleState("supply") == "active" and ui.backend.moduleState("nope") == "active"
    # readonly-раздел НЕ скрыт (чтение/выгрузка — политика D-81 «данные не заложники»), но полного доступа нет:
    assert ui.backend.moduleVisible("pdm") is True and ui.backend.moduleOk("pdm") is False
    assert ui.backend.moduleVisible("schema") is False    # «В разработке» — скрыт
