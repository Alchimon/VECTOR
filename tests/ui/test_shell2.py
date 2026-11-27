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
    assert sh.property("page") == "home"          # D-108: стартовая — рабочий стол «Главная»
    assert ui.find("ribbon").isVisible()
    assert ui.find("objectTabs").isVisible()
    assert ui.find("statusBar2").isVisible()
    # Домашняя вкладка создана и не закрывается
    tabs = _tabs(sh)
    assert [t["key"] for t in tabs] == ["page:home"]
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
    assert tabs[0]["key"] == "page:home"
    keys = [t["key"] for t in tabs]
    assert keys == ["page:home", "project:" + ui.ids["project"], "kit:" + ui.ids["kits"]["АТХ"]]
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


def test_v2_inspector_versions_tab(ui):
    """Вкладка «Версии» инспектора (D-102, концепт B): у документа — таймлайн файловых версий
    (автор, дата, размер, отметка «Текущая»), у комплекта — зафиксированные версии расчёта."""
    from PySide6.QtCore import Q_ARG, QMetaObject
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    # Документ с файловыми версиями (АТХ-001 в демо-данных имеет 3 версии)
    doc = next(d for d in _plain(ui.backend.property("documents"))
               if d.get("code") == "АТХ-001" and (d.get("versions") or []))
    QMetaObject.invokeMethod(sh, "selectObject", Q_ARG("QVariant", "document"), Q_ARG("QVariant", doc))
    ui.wait_idle()
    insp = ui.find("inspector2")
    insp.setProperty("inspectorTab", "versions")
    ui.pump(0.3)
    assert len(doc["versions"]) >= 1
    # Комплект: версии расчёта подтягиваются из backend.kitCalc
    kit = next(k for k in _plain(ui.backend.property("kits")) if k["id"] == ui.ids["kits"]["АТХ"])
    QMetaObject.invokeMethod(sh, "selectObject", Q_ARG("QVariant", "kit"), Q_ARG("QVariant", kit))
    ui.wait_idle()
    insp.setProperty("inspectorTab", "versions")
    ui.pump(0.3)
    versions = _plain(insp.property("kitCalcVersions"))
    assert versions and versions[0]["author_name"], "версии расчёта комплекта не показаны"
    assert versions[0]["label"].startswith("v") and versions[0]["total_label"]


def test_v2_project_tab_lists_documents(ui):
    """Вкладка проекта (концепт A, D-102): под комплектами — раздел «Документы» с версиями файлов;
    документ из дерева/списка открывается в редакторе через shell2.openDocument."""
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    _open_object(sh, "project", ui.ids["project"])
    ui.wait_idle()
    # Вкладка проекта активна, документы комплектов собраны в раздел (ищем делегат с кодом документа)
    codes = [str(i.property("text") or "") for i in ui.items("text")]
    assert any("АТХ-001" == c for c in codes), "раздел документов проекта не показан"
    # Двойной щелчок по документу → openDocument: выбор в инспекторе + редактор (здесь просто не падает)
    from PySide6.QtCore import Q_ARG, QMetaObject
    doc = next(d for d in _plain(ui.backend.property("documents")) if d.get("code") == "АТХ-001")
    QMetaObject.invokeMethod(sh, "openDocument", Q_ARG("QVariant", doc))
    ui.wait_idle()
    assert sh.property("selectedKind") == "document"


def test_v2_inspector_new_version_button(ui):
    """Инспектор документа (концепт A): действие «Новая версия» на виду — тот же контур загрузки,
    что и в классике (SHA-256, только онлайн)."""
    from PySide6.QtCore import Q_ARG, QMetaObject
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    doc = next(d for d in _plain(ui.backend.property("documents")) if d.get("code") == "АТХ-001")
    QMetaObject.invokeMethod(sh, "selectObject", Q_ARG("QVariant", "document"), Q_ARG("QVariant", doc))
    ui.wait_idle()
    insp = ui.find("inspector2")
    btn = insp.findChild(type(insp), "inspNewVersion")
    assert btn is not None and btn.property("visible") is True
    assert btn.property("enabled") is True  # онлайн-стенд, карточка на сервере


def test_v2_rail_flyout(ui):
    """Рейл чисто иконочный (концепт B): при наведении всплывает flyout-панель с названием раздела."""
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    ui.login("a.smirnova")
    _to_v2(ui)
    # Фильтр isVisible: после переключения классика→2.0 старый («призрачный») экземпляр рейла
    # остаётся в QObject-дереве — как и в стенде сцен 77 (см. ui_harness).
    rail_items = [i for i in ui.win.findChildren(QQuickItem)
                  if i.property("iconName") is not None and i.property("badge") is not None and i.isVisible()]
    assert rail_items, "рейл не найден"
    ri = rail_items[1]
    c = ri.mapToScene(QPointF(ri.width() / 2, ri.height() / 2)).toPoint()
    QTest.mouseMove(ui.win, c)
    ui.pump(0.4)
    fly = ri.findChild(QObject, "railFlyout")
    assert fly is not None and fly.property("visible") is True


def test_v2_inspector_document_preview_and_download(ui):
    """Инспектор документа (концепт A, D-103): карточка предпросмотра содержимого и «Скачать версию N».
    Демо-документ — внешний файл (PDF): превью честно сообщает, что оно — для документов ВЕКТОР."""
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    doc = next(d for d in _plain(ui.backend.property("documents")) if d.get("code") == "АТХ-001")
    QMetaObject.invokeMethod(sh, "selectObject", Q_ARG("QVariant", "document"), Q_ARG("QVariant", doc))
    ui.wait_idle()
    ui.pump(0.5)
    insp = ui.find("inspector2")
    card = insp.findChild(QObject, "docPreview")
    assert card is not None and card.property("visible") is True
    assert "ВЕКТОР" in (insp.property("previewNote") or "")
    dl = insp.findChild(QObject, "inspDownload")
    assert dl is not None and dl.property("visible") is True
    # «Новая задача» по документу (D-109) видна и доступна онлайн
    nt = insp.findChild(QObject, "inspNewTask")
    assert nt is not None and nt.property("visible") is True and nt.property("enabled") is True
    assert insp.findChild(QObject, "inspTaskForm") is not None


def test_v2_projects_filter_chips(ui):
    """Страница «Проекты» 2.0 (D-106): чипы-фильтры Активные/Архив/Все и прогресс задач на карточках."""
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    # Страница — делегат Repeater вкладок: findChild её не видит, ищем визуальным обходом (ui.items).
    # Стартовая вкладка — «Главная» (D-108); раздел «Проекты» открываем явно.
    from PySide6.QtCore import QMetaObject, Q_ARG
    QMetaObject.invokeMethod(sh, "go", Q_ARG("QVariant", "projects"), Q_ARG("QVariant", ""))
    ui.wait_idle()
    page = next((i for i in ui.items("filter") if i.property("objectName") == "projectsPage2"), None)
    assert page is not None
    # По умолчанию — активные: оба демо-проекта в разработке РКД
    assert page.property("filter") == "active"
    assert len(_plain(page.property("items"))) == 2
    page.setProperty("filter", "archive")
    ui.pump(0.2)
    assert len(_plain(page.property("items"))) == 0      # архива в демо нет
    page.setProperty("filter", "all")
    ui.pump(0.2)
    assert len(_plain(page.property("items"))) == 2


def test_v2_home_page(ui):
    """«Главная» 2.0 (D-108): рабочий стол открывается стартовой вкладкой, счётчики честные."""
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    assert _plain(sh.property("activeTabKey")) == "page:home"
    home = next((i for i in ui.items("myOpenTasks") if i.property("objectName") == "homePage2"), None)
    assert home is not None
    # У a.smirnova (РП) в демо нет назначенных открытых задач — честный 0
    assert len(_plain(home.property("myOpenTasks"))) == 0
    assert len(_plain(home.property("activeProjects"))) == 2
    # Лента уведомлений (D-115): элементы приходят из notificationsOverview
    assert len(_plain(home.property("notifItems"))) > 0
    # Полоса рабочего дня (D-121): в демо-стенде день не начат — есть кнопка «Начать»
    wd = _plain(home.property("workday"))
    assert wd.get("status") is not None
    assert next((i for i in ui.items("iconName") if i.property("objectName") == "homeWorkdayStart"), None) is not None


def test_v2_palette_finds_objects(ui):
    """Палитра команд 2.0 (D-110): ищет не только разделы, но и объекты — проекты, комплекты, документы."""
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    from PySide6.QtCore import QMetaObject, Q_ARG
    QMetaObject.invokeMethod(sh, "buildCommands")
    cmds = _plain(sh.property("commandsCache"))
    labels = [c["label"] for c in cmds]
    assert any("ПАЗ цеха" in l for l in labels)                       # проект
    assert any(l.startswith("АТХ ·") for l in labels)                 # комплект
    assert any("АТХ-001" in l for l in labels)                        # документ


def test_v2_kit_inspector_new_document(ui):
    """Инспектор комплекта 2.0 (D-112): кнопка «Новый документ» и форма создания карточки."""
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    kit = next(k for k in _plain(ui.backend.property("kits")) if k["code"] == "АТХ")
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject
    QMetaObject.invokeMethod(sh, "selectObject", Q_ARG("QVariant", "kit"), Q_ARG("QVariant", kit))
    ui.wait_idle()
    insp = ui.find("inspector2")
    btn = insp.findChild(QObject, "inspNewDoc")
    assert btn is not None and btn.property("visible") is True
    assert insp.findChild(QObject, "inspDocForm") is not None
    # «Изменить карточку» (D-114) — у комплекта есть и кнопка, и форма
    ec = insp.findChild(QObject, "inspEditCard")
    assert ec is not None and ec.property("visible") is True
    assert insp.findChild(QObject, "inspEditForm") is not None


def test_v2_project_versions_timeline(ui):
    """Инспектор проекта 2.0 (D-116): вкладка «Версии» — хронология версий документов проекта."""
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    p = next(x for x in _plain(ui.backend.property("projects")) if x["id"] == ui.ids["project"])
    from PySide6.QtCore import Q_ARG, QMetaObject
    QMetaObject.invokeMethod(sh, "selectObject", Q_ARG("QVariant", "project"), Q_ARG("QVariant", p))
    ui.wait_idle()
    insp = ui.find("inspector2")
    rows = _plain(insp.property("projDocVersions"))
    assert len(rows) == 4                                    # 3 версии АТХ-001 + 1 версия АТХ-002
    assert rows[0]["version_no"] >= rows[-1]["version_no"]   # новые сверху
    assert rows[0]["doc_code"].startswith("АТХ")


def test_v2_document_discussion(ui):
    """Вкладка «Обсуждение» документа 2.0 (D-118): замечания ревизий РКД из клиентского кэша."""
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    doc = next(d for d in _plain(ui.backend.property("documents")) if d.get("code") == "АТХ-001")
    from PySide6.QtCore import Q_ARG, QMetaObject
    QMetaObject.invokeMethod(sh, "selectObject", Q_ARG("QVariant", "document"), Q_ARG("QVariant", doc))
    ui.wait_idle()
    insp = ui.find("inspector2")
    # В демо у АТХ-001 замечаний нет — честный пустой список; слот отвечает нужной формой
    r = ui.backend.documentDiscussion(doc["id"])
    assert "items" in r and "open_count" in r
    assert _plain(insp.property("discussItems")) == []
    # Для комплекта — заглушка: список пуст
    kit = next(k for k in _plain(ui.backend.property("kits")) if k["code"] == "АТХ")
    QMetaObject.invokeMethod(sh, "selectObject", Q_ARG("QVariant", "kit"), Q_ARG("QVariant", kit))
    ui.wait_idle()
    assert _plain(insp.property("discussItems")) == []


def test_v2_tab_stepping(ui):
    """Вкладки 2.0 (D-120): stepTab листает вкладки с зацикливанием (Ctrl+Tab)."""
    ui.login("a.smirnova")
    sh = _to_v2(ui)
    _open_object(sh, "project", ui.ids["project"])
    _open_object(sh, "kit", ui.ids["kits"]["АТХ"])
    ui.wait_idle()
    from PySide6.QtCore import Q_ARG, QMetaObject
    QMetaObject.invokeMethod(sh, "stepTab", Q_ARG("QVariant", 1))      # с последней — на первую (круг)
    ui.wait_idle()
    assert sh.property("activeTabKey") == "page:home"
    QMetaObject.invokeMethod(sh, "stepTab", Q_ARG("QVariant", -1))     # назад — на последнюю
    ui.wait_idle()
    assert sh.property("activeTabKey") == "kit:" + ui.ids["kits"]["АТХ"]
