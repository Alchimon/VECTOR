"""Интерфейс 2.0 (D-98): оболочка Shell2 — лента команд, вкладки открытых объектов, инспектор-предпросмотр,
палитра команд Ctrl+K, плотность «Уютно/Компактно», переключение с классикой и обратно без потери данных."""
import pytest
from ui_harness import _plain

pytestmark = pytest.mark.ui


def _to_v2(ui):
    """Переключить стенд в интерфейс 2.0 и вернуть оболочку Shell2."""
    ui.backend.uiMode = "v2"
    ui.wait_idle()
    sh = ui.find("shell2")
    assert sh is not None, "оболочка 2.0 не загрузилась"
    assert sh.isVisible()
    return sh


def _open_object(sh, kind, oid):
    """Открыть объект во вкладке — как двойной щелчок по карточке."""
    from PySide6.QtCore import Q_ARG, QMetaObject
    QMetaObject.invokeMethod(sh, "openObject", Q_ARG("QVariant", kind), Q_ARG("QVariant", oid))


def _tabs(sh):
    """Список вкладок оболочки как обычные dict."""
    return [_plain(t) for t in _plain(sh.property("openTabs"))]


def test_v2_loads_with_ribbon_tabs_statusbar(ui):
    """Каркас 2.0: лента, вкладки, статус-бар видны; домашний раздел — проекты (у РП)."""
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    assert sh.property("page") == "projects"
    assert ui.find("ribbon").isVisible()
    assert ui.find("objectTabs").isVisible()
    assert ui.find("statusBar2").isVisible()
    # Домашняя вкладка раздела создана и не закрывается
    tabs = _tabs(sh)
    assert [t["key"] for t in tabs] == ["page:projects"]
    assert tabs[0]["closable"] is False


def test_v2_object_tabs_open_activate_close(ui):
    """Проект и комплект открываются вкладками (как в браузере); закрытие возвращает предыдущую."""
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    _open_object(sh, "project", ui.ids["project"])
    ui.wait_idle()
    _open_object(sh, "kit", ui.ids["kits"]["АТХ"])
    ui.wait_idle()
    tabs = _tabs(sh)
    keys = [t["key"] for t in tabs]
    assert keys == ["page:projects", "project:" + ui.ids["project"], "kit:" + ui.ids["kits"]["АТХ"]]
    assert sh.property("activeTabKey") == keys[-1]
    # Вкладка комплекта показывает KitWork с задачами (Repeater-делегаты findChild не видит — ищем визуально)
    assert any(i.isVisible() for i in ui.items("kitTasks"))
    # Закрытие активной вкладки возвращает предыдущую
    from PySide6.QtCore import Q_ARG, QMetaObject
    QMetaObject.invokeMethod(sh, "closeTab", Q_ARG("QVariant", keys[-1]))
    ui.wait_idle()
    assert sh.property("activeTabKey") == keys[1]
    assert len(_tabs(sh)) == 2


def test_v2_inspector_previews_selected_project(ui):
    """Выбор карточки в списке показывает предпросмотр в инспекторе справа: свойства, команда, комплекты."""
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    from PySide6.QtCore import Q_ARG, QMetaObject
    p = next(x for x in _plain(ui.backend.property("projects")) if x["id"] == ui.ids["project"])
    QMetaObject.invokeMethod(sh, "selectObject", Q_ARG("QVariant", "project"), Q_ARG("QVariant", p))
    ui.wait_idle()
    insp = ui.find("inspector2")
    assert insp.isVisible()
    title = ui.find("inspTitle")
    assert title is not None and ui.ids["project"] and "П-2026-014" in title.property("text")


def test_v2_palette_opens_filters_and_runs(ui):
    """Палитра команд (Ctrl+K): открывается, фильтрует по слову, Enter выполняет команду."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    ui.login("a.smirnova")
    _to_v2(ui)
    ui.win.requestActivate()
    ui.pump(0.2)
    QTest.keySequence(ui.win, Qt.KeyboardModifier.ControlModifier | Qt.Key.Key_K)
    ui.pump(0.3)
    pal = ui.find("commandPalette")
    assert pal.property("visible") is True
    ui.find("paletteField").setProperty("text", "тендер")
    ui.pump(0.2)
    model = _plain(ui.find("paletteList").property("model"))
    labels = [m["label"] if isinstance(m, dict) else m for m in model]
    assert any("Тендеры" in str(l) for l in labels)
    # Выполнить первую команду — переход в раздел «Тендеры и ТКП»
    QTest.keyClick(ui.win, Qt.Key.Key_Return)
    ui.wait_idle()
    assert ui.find("shell2").property("page") == "tenders"


def test_v2_density_toggle_shrinks_chrome(ui):
    """Плотность «Компактно»: статус-бар и вкладки ниже, рейл уже; настройка сохраняется."""
    ui.login("a.smirnova")
    _to_v2(ui)
    h_cozy = ui.find("statusBar2").property("implicitHeight")
    ui.backend.uiDensity = "compact"
    ui.wait_idle()
    assert ui.backend._settings.value("ui/density") == "compact"
    h_compact = ui.find("statusBar2").property("implicitHeight")
    assert h_compact < h_cozy
    ui.backend.uiDensity = "cozy"
    ui.wait_idle()
    assert ui.find("statusBar2").property("implicitHeight") == h_cozy


def test_v2_is_default_mode(ui, tmp_path):
    """Свежая установка (нет сохранённых настроек) стартует в интерфейсе 2.0 (D-100)."""
    from PySide6.QtCore import QSettings
    from vector.client.backend import Backend
    b = Backend(settings=QSettings(str(tmp_path / "fresh.ini"), QSettings.Format.IniFormat))
    try:
        assert b.uiMode == "v2"
    finally:
        b.shutdown()


def test_v2_switch_roundtrip_with_classic(ui):
    """Классика ↔ 2.0: кнопка «Новый вид» в классике, «Классика» на ленте 2.0; выбор сохраняется.
    (Стенд-фикстура стартует классику ради совместимости тестов; фабричное умолчание — 2.0, проверено выше.)"""
    ui.login("a.smirnova")
    # В 2.0 — кнопкой ✨ в классике
    btn = ui.find("btnUiV2")
    assert btn is not None
    ui.click(btn)
    assert ui.backend.uiMode == "v2"
    ui.wait_idle()
    assert ui.find("shell2") is not None
    assert ui.backend._settings.value("ui/mode") == "v2"
    # Обратно — действием ленты «Главная → Классика»
    ui.backend.uiMode = "classic"
    ui.wait_idle()
    # Классика на месте и работает (объектная функция page() читает Shell)
    assert ui.shell() is not None
    assert ui.page() == "tenders"   # стартовый раздел классики для РП
    # Видимой оболочки 2.0 не осталось
    assert all(not i.isVisible() for i in ui.items("openTabs"))
    assert ui.backend._settings.value("ui/mode") == "classic"


def test_v2_invalid_ui_values_ignored(ui):
    """Некорректные значения вида/плотности игнорируются (защита настроек)."""
    ui.login("a.smirnova")
    before_mode = ui.backend.uiMode
    ui.backend.uiMode = "spaceship"
    ui.backend.uiDensity = "sparse"
    assert ui.backend.uiMode == before_mode
    assert ui.backend.uiDensity == "cozy"
