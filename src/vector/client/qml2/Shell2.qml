import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../qml"

// Рабочая оболочка интерфейса 2.0 (D-98, docs/blueprint/56-ui-ergonomics.md) — гибрид концептов:
//  * лента команд сверху (разделы-табы + группы действий, как в Office/EPLAN);
//  * узкая иконка-рейл слева (разделы, избранное), вместо широкой колонки-меню классики;
//  * вкладки открытых объектов (проекты, комплекты) — как в браузере/VS Code, без потери контекста;
//  * инспектор справа — предпросмотр и действия выбранного объекта;
//  * статус-бар снизу (связь, очередь, рабочий день, плотность);
//  * палитра команд Ctrl+K.
// Оболочка живёт рядом с классикой: переключатель «Вид интерфейса» (backend.uiMode), данные и команды —
// те же backend-API; права и аудит не меняются. Страницы, не переписанные под 2.0, встраиваются как есть.
Item {
    id: shell2
    objectName: "shell2"

    // ---------- навигация (совместимо с классикой: страницы ждут shell.go/navTarget)
    property string page: "tenders"             // раздел активной вкладки (для ленты, крошек, лицензий)
    property var navTarget: ({})
    function go(p, id) {
        navTarget = { page: p, id: id || "" }
        openPageTab(p)
    }

    // ---------- вкладки: разделы и объекты (key: page:<name> | project:<id> | kit:<id>)
    property var openTabs: []
    property string activeTabKey: ""
    readonly property var activeTab: openTabs.find(t => t.key === activeTabKey) || null

    function tabFor(key) { return openTabs.find(t => t.key === key) || null }
    // Вкладка → модуль экосистемы (D-99): комплект — таблицы Меркурия, остальные разделы и объекты — ХАБ.
    function moduleOf(t) { return t && (t.kind === "kit" || t.kind === "doc") ? "mercury" : "hub" }
    function activateTab(key) {
        if (tabFor(key) === null) return
        activeTabKey = key
        var t = tabFor(key)
        page = t.kind === "page" ? t.id : "projects"   // объектные вкладки относятся к разделу «Проекты»
    }
    // Шаг по вкладкам с зацикливанием (Ctrl+Tab, D-120).
    function stepTab(dir) {
        if (openTabs.length < 2) return
        var i = 0
        for (var k = 0; k < openTabs.length; k++) if (openTabs[k].key === activeTabKey) { i = k; break }
        activateTab(openTabs[(i + dir + openTabs.length) % openTabs.length].key)
    }
    function openPageTab(p) {
        var key = "page:" + p
                        var titles = ({ home: "Главная", customers: "Заказчики", tenders: "Тендеры и ТКП", leads: "Лиды", projects: "Проекты",
                        finance: "Итоги проектов", analytics: "Аналитика", worktime: "Рабочее время",
                        stock: "Склад", procurement: "Закупка", production: "Производство",
                        products: "Изделия и паспорта", quality: "Качество", sync: "Синхронизация",
                        notifications: "Уведомления", delegations: "Замещения", org: "Оргструктура",
                        help: "Справка", admin: "Администрирование" })
        var icons = ({ home: "house", customers: "building-2", tenders: "gavel", leads: "sparkles", projects: "folder-kanban",
                       finance: "wallet", analytics: "sliders-horizontal", worktime: "clock",
                       stock: "warehouse", procurement: "shopping-cart", production: "factory",
                       products: "package-check", quality: "shield-check", sync: "refresh-cw",
                       notifications: "bell", delegations: "user-check", org: "network",
                       help: "circle-help", admin: "shield-check" })
        if (tabFor(key) === null)
            openTabs = openTabs.concat([{ key: key, kind: "page", id: p, title: titles[p] || p,
                                          icon: icons[p] || "file-text", closable: openTabs.length > 0 }])
        activateTab(key)
    }
    function openObject(kind, id) {
        var key = kind + ":" + id
        if (tabFor(key) === null) {
            var obj = kind === "project" ? backend.projects.find(p => p.id === id)
                    : kind === "kit" ? backend.kits.find(k => k.id === id) : null
            if (obj === null || obj === undefined) return
            openTabs = openTabs.concat([{ key: key, kind: kind, id: id,
                title: (obj.code ? obj.code : obj.title || ""), icon: kind === "project" ? "folder-kanban" : "layers",
                closable: true }])
        }
        activateTab(key)
    }
    // Документ: выбрать в инспекторе и открыть в редакторе ВЕКТОР (дерево проекта, карточки проекта).
    function openDocument(doc) {
        if (!doc) return
        selectObject("document", doc)
        officeEd.openFor(doc, doc.current_version_no || 1)
    }
    function closeTab(key) {
        var t = tabFor(key)
        if (t === null || t.closable !== true) return
        var idx = openTabs.findIndex(x => x.key === key)
        openTabs = openTabs.filter(x => x.key !== key)
        if (activeTabKey === key)
            activateTab(openTabs.length > 0 ? openTabs[Math.max(0, idx - 1)].key : "")
    }

    // ---------- выбранный объект (инспектор справа)
    property var selectedObject: null
    property string selectedKind: ""
    function selectObject(kind, obj) { selectedKind = kind; selectedObject = obj
        if (kind === "project" && obj) treeExpandedProject = obj.id
        if (kind === "kit" && obj) treeExpandedProject = projectOfKit(obj.id) }
    readonly property bool inspectorVisible: selectedObject !== null && activeTab !== null && activeTab.kind === "page"
    // Дерево проектов слева (концепт A, D-101): развёрнутый проект — проект активной вкладки
    // (для комплекта — его проект).
    property string treeExpandedProject: ""
    function projectOfKit(kitId) {
        var k = (backend.kits || []).find(function(k) { return k.id === kitId })
        return k ? k.project_id || "" : ""
    }
    onActiveTabKeyChanged: {
        var t = activeTab
        if (t !== null && t.kind === "project") treeExpandedProject = t.id
        else if (t !== null && t.kind === "kit") treeExpandedProject = projectOfKit(t.id)
    }

    // ---------- видимость разделов (та же логика подсказок по правам, что в классике)
    readonly property bool seesStock: backend.hint("stock.read", backend.permRevision)
    readonly property bool seesNeeds: seesStock || (backend.needsOverview.needs || []).length > 0
        || (backend.me.roles || []).indexOf("accountant") >= 0
        || (backend.me.roles || []).indexOf("po_approver") >= 0
        || (backend.me.roles || []).indexOf("dept_head") >= 0
    readonly property bool seesProduction: (backend.me.roles || []).some(r => r === "technologist" || r === "production" || r === "dept_head" || r === "warehouse")
        || (backend.productionOverview.my_ops || []).length > 0 || (backend.productionOverview.tps || []).length > 0
    readonly property bool seesProducts: backend.lifecycleOverview.sees === true
    readonly property bool seesQuality: backend.qualityOverview.sees === true

    // Стартовый раздел по ролям — как в классике; домашняя вкладка не закрывается.
    property bool startPicked: false
    function pickStart() {
        if (startPicked || !backend.me.user_id) return
        startPicked = true
        var rs = backend.me.roles || []
        var start = !backend.hint("tender.read", backend.permRevision)
            ? (rs.some(r => r === "production" || r === "technologist") ? "production"
             : rs.some(r => r === "otk") ? "quality"
             : backend.hint("stock.read", backend.permRevision) ? "stock" : "home")
            : "home"   // в 2.0 домашняя страница — рабочий стол «Главная» (D-108)
        openPageTab(start)
    }
    Component.onCompleted: {
        pageComponents = ({ home: home2C, customers: customersC, tenders: tendersC, leads: leadsC,
            projects: projects2C, sync: syncC, admin: adminC, finance: financeC, stock: stockC,
            procurement: procurementC, delegations: delegationsC, notifications: notificationsC,
            production: productionC, products: productsC, quality: qualityC, help: helpC,
            worktime: worktimeC, org: orgC, analytics: analyticsC })
        commandsCache = buildCommands()
        pickStart()
        if (wantsWorkdayPrompt) workdayPrompt.open()
    }
    Connections { target: backend; function onChanged() { shell2.pickStart() } }

    // ---------- лента: таб следует за разделом активной вкладки, вручную — только меняет ленту
    property string ribbonTab: "home"
    onPageChanged: {
        ribbonTab = (["projects", "customers", "tenders", "leads", "finance", "analytics"].indexOf(page) >= 0) ? "project"
            : (["stock", "procurement"].indexOf(page) >= 0) ? "supply"
            : (["production", "products", "quality"].indexOf(page) >= 0) ? "production"
            : (["sync", "notifications", "delegations", "org", "help", "admin", "worktime"].indexOf(page) >= 0) ? "system"
            : ribbonTab
        commandsCache = buildCommands()   // и палитра следует за контекстом
    }

    // Группы действий ленты для таба tab. Действие: {text, icon, tip, visible, enabled, warning, run}.
    function ribbonGroups(tab) {
        var A = function(o) { o.visible = o.visible !== false; o.enabled = o.enabled !== false; o.warning = o.warning === true; return o }
        var sel = selectedObject
        if (tab === "home") return [
            { group: "Действия", actions: [
                A({ text: "Обновить", icon: "refresh-cw", run: function() { backend.syncNow() } }),
                A({ text: "Палитра", icon: "search", tip: "Поиск по разделам и действиям (Ctrl+K)",
                    run: function() { palette.open() } }) ] },
            { group: "Вид", actions: [
                A({ text: Theme.dark ? "Светлая тема" : "Тёмная тема", icon: Theme.dark ? "sun" : "moon",
                    run: function() { backend.theme = Theme.dark ? "light" : "dark" } }),
                A({ text: backend.uiDensity === "compact" ? "Уютно" : "Компактно", icon: "sliders-horizontal",
                    tip: "Плотность интерфейса: просторно или компактно (для небольших мониторов)",
                    run: function() { backend.uiDensity = backend.uiDensity === "compact" ? "cozy" : "compact" } }),
                A({ text: "Классика", icon: "undo-2", tip: "Вернуться к привычному интерфейсу (выбор сохранится)",
                    run: function() { backend.uiMode = "classic" } }) ] } ]
        if (tab === "project") return [
            { group: "Проект", actions: [
                A({ text: "Проекты", icon: "folder-kanban", run: function() { shell2.go("projects") } }),
                A({ text: "Тендеры", icon: "gavel", visible: backend.hint("tender.read", backend.permRevision) && backend.moduleVisible("pdm"),
                    run: function() { shell2.go("tenders") } }),
                A({ text: "Заказчики", icon: "building-2", visible: backend.hint("customer.read", backend.permRevision) && backend.moduleVisible("pdm"),
                    run: function() { shell2.go("customers") } }),
                A({ text: "Лиды", icon: "sparkles", visible: backend.hint("opportunity.read", backend.permRevision) && backend.moduleVisible("pdm"),
                    run: function() { shell2.go("leads") } }) ] },
            { group: "Выбранное", actions: [
                A({ text: "Открыть", icon: "folder-kanban", enabled: sel !== null && selectedKind === "project",
                    tip: "Открыть выбранный проект во вкладке (двойной щелчок по карточке — то же самое)",
                    run: function() { if (sel) shell2.openObject("project", sel.id) } }),
                A({ text: "Тендер", icon: "gavel", enabled: sel !== null && selectedKind === "project" && sel.tender_id,
                    run: function() { if (sel) shell2.go("tenders", sel.tender_id) } }),
                A({ text: "Итоги", icon: "wallet", visible: (backend.me.roles || []).indexOf("accountant") >= 0 || backend.financeOverview.length > 0,
                    run: function() { shell2.go("finance") } }) ] },
            { group: "Портфель", actions: [
                A({ text: "Аналитика", icon: "sliders-horizontal", visible: backend.hint("norm.read", backend.permRevision) && backend.moduleVisible("analytics"),
                    run: function() { shell2.go("analytics") } }),
                A({ text: "Обновить", icon: "refresh-cw", run: function() { backend.syncNow() } }) ] } ]
        if (tab === "supply") return [
            { group: "Снабжение", actions: [
                A({ text: "Склад", icon: "warehouse", visible: seesStock && backend.moduleVisible("supply"),
                    run: function() { shell2.go("stock") } }),
                A({ text: "Закупка", icon: "shopping-cart", visible: seesNeeds && backend.moduleVisible("supply"),
                    run: function() { shell2.go("procurement") } }) ] },
            { group: "Действия", actions: [
                A({ text: "Обновить", icon: "refresh-cw", run: function() { backend.syncNow() } }) ] } ]
        if (tab === "production") return [
            { group: "Производство", actions: [
                A({ text: "Производство", icon: "factory", visible: seesProduction && backend.moduleVisible("production"),
                    run: function() { shell2.go("production") } }),
                A({ text: "Изделия", icon: "package-check", visible: seesProducts && backend.moduleVisible("service"),
                    run: function() { shell2.go("products") } }),
                A({ text: "Качество", icon: "shield-check", visible: seesQuality && backend.moduleVisible("quality"),
                    run: function() { shell2.go("quality") } }) ] },
            { group: "Действия", actions: [
                A({ text: "Обновить", icon: "refresh-cw", run: function() { backend.syncNow() } }) ] } ]
        return [
            { group: "Система", actions: [
                A({ text: "Уведомления", icon: "bell", run: function() { shell2.go("notifications") } }),
                A({ text: "Замещения", icon: "user-check", run: function() { shell2.go("delegations") } }),
                A({ text: "Оргструктура", icon: "network", run: function() { shell2.go("org") } }),
                A({ text: "Рабочее время", icon: "clock", run: function() { shell2.go("worktime") } }) ] },
            { group: "Справка", actions: [
                A({ text: "Справка", icon: "circle-help", run: function() { shell2.go("help") } }),
                A({ text: "Синхронизация", icon: "refresh-cw", run: function() { shell2.go("sync") } }),
                A({ text: "Админ", icon: "shield-check", visible: backend.hint("admin.list_users", backend.permRevision),
                    run: function() { shell2.go("admin") } }) ] } ]
    }

    // Команды палитры Ctrl+K: все видимые разделы + действия ленты + служебные.
    property var commandsCache: []
    Connections { target: backend; function onChanged() { shell2.commandsCache = shell2.buildCommands() } }
    function buildCommands() {
        var out = []
        var add = function(label, hint, icon, keys, run) { out.push({ label: label, hint: hint, icon: icon, keys: keys, run: run }) }
        var pages = ribbonGroups("project")[0].actions.concat(ribbonGroups("supply")[0].actions,
            ribbonGroups("production")[0].actions, ribbonGroups("system")[0].actions, ribbonGroups("system")[1].actions)
        for (var i = 0; i < pages.length; i++)
            if (pages[i].visible) add(pages[i].text, "Раздел", pages[i].icon, "", pages[i].run)
        add("Обновить данные", "Синхронизация с сервером", "refresh-cw", "F5", function() { backend.syncNow() })
        add(Theme.dark ? "Светлая тема" : "Тёмная тема", "Оформление", Theme.dark ? "sun" : "moon", "",
            function() { backend.theme = Theme.dark ? "light" : "dark" })
        add(backend.uiDensity === "compact" ? "Плотность: Уютно" : "Плотность: Компактно", "Вид интерфейса", "sliders-horizontal", "",
            function() { backend.uiDensity = backend.uiDensity === "compact" ? "cozy" : "compact" })
        add("Классический вид интерфейса", "Вернуться к привычной оболочке", "undo-2", "", function() { backend.uiMode = "classic" })
        for (var t = 0; t < openTabs.length; t++)
            add("Перейти: " + openTabs[t].title, "Открытая вкладка", openTabs[t].icon, "",
                (function(key) { return function() { shell2.activateTab(key) } })(openTabs[t].key))
        // D-110: объекты — проекты, комплекты, документы (щелчок открывает во вкладке).
        for (var p = 0; p < backend.projects.length; p++) {
            var pr = backend.projects[p]
            add(pr.code + " · " + pr.title, "Проект", "folder-kanban", "",
                (function(o) { return function() { shell2.openObject("project", o.id) } })(pr))
        }
        for (var k = 0; k < backend.kits.length; k++) {
            var kt = backend.kits[k]
            add(kt.code + " · " + kt.title, "Комплект", "layers", "",
                (function(o) { return function() { shell2.openObject("kit", o.id) } })(kt))
        }
        for (var d = 0; d < backend.documents.length; d++) {
            var dc = backend.documents[d]
            add(dc.code + " · " + dc.title, "Документ", dc.kind === "sheet" ? "table" : "file-text", "",
                (function(o) { return function() { shell2.selectObject("document", o); shell2.openDocument(o) } })(dc))
        }
        return out
    }

    // ---------- горячие клавиши (те же, что в классике, + Ctrl+K, Ctrl+W, Ctrl+Tab)
    Shortcut { sequence: "Ctrl+K"; onActivated: palette.open() }
    Shortcut { sequence: "Ctrl+W"; onActivated: shell2.closeTab(shell2.activeTabKey) }
    // D-120: Ctrl+Tab / Ctrl+Shift+Tab — следующая/предыдущая вкладка (браузерная привычка).
    Shortcut { sequence: "Ctrl+Tab"; onActivated: shell2.stepTab(1) }
    Shortcut { sequence: "Ctrl+Shift+Tab"; onActivated: shell2.stepTab(-1) }
    Shortcut { sequence: "Ctrl+3"; onActivated: shell2.go("projects") }
    Shortcut { sequence: "Ctrl+4"; onActivated: shell2.go("sync") }
    Shortcut { sequence: "Ctrl+5"; onActivated: if (shell2.seesStock) shell2.go("stock") }
    Shortcut { sequence: "Ctrl+6"; onActivated: if (shell2.seesNeeds) shell2.go("procurement") }
    Shortcut { sequence: "Ctrl+7"; onActivated: if (shell2.seesProduction) shell2.go("production") }
    Shortcut { sequence: "Ctrl+0"; onActivated: shell2.go("worktime") }
    Shortcut { sequence: "F5"; onActivated: backend.syncNow() }
    Shortcut { sequence: "F1"; onActivated: shell2.go("help") }

    // Страница → модуль лицензии (D-88) — полоса состояния, как в классике.
    readonly property var pageModule: ({ customers: "pdm", tenders: "pdm", projects: "pdm", leads: "pdm",
        finance: "analytics", analytics: "analytics", stock: "supply", procurement: "supply",
        worktime: "hr", production: "production", products: "service", quality: "quality" })

    // Фирменный фон рабочей области (D-99): «космос/чертёж» под вуалью цвета холста — виден в полях
    // между карточками и за панелями; карточки и панели непрозрачны, данные не страдают.
    Image {
        anchors.fill: parent
        source: Theme2.backdrop
        fillMode: Image.PreserveAspectCrop
        asynchronous: true; smooth: true; mipmap: true
    }
    Rectangle { anchors.fill: parent; color: Qt.alpha(Theme2.bg, Theme2.dark ? 0.80 : 0.85) }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ================= верхняя панель: логотип, крошки, палитра, уведомления, пользователь
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: Theme2.topBarH
            color: Theme2.sidebar
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme2.border }
            RowLayout {
                anchors { fill: parent; leftMargin: Theme2.pad; rightMargin: Theme2.pad }
                spacing: Theme2.pad
                RowLayout { spacing: 10
                    // Эмблема экосистемы из графического пака (D-99) на мягкой подложке (полный логотип — на входе).
                    // Подпись-лейбл не нужна: имена модулей — в переключателе рядом (место в верхней панели дорогое).
                    Rectangle { Layout.preferredWidth: Theme2.compact ? 28 : 34; Layout.preferredHeight: width
                                radius: Theme2.radiusS; color: Theme2.primarySoft
                        Image { objectName: "shell2Emblem"; source: Theme2.emblem
                                anchors.centerIn: parent; width: parent.width - 8; height: width
                                sourceSize: Qt.size(96, 96); fillMode: Image.PreserveAspectFit; smooth: true; mipmap: true
                                Accessible.name: "ВЕКТОР ПЛАТФОРМА" }
                        VTipArea { text: "ВЕКТОР ПЛАТФОРМА — единая платформа для инженеров и бизнеса" } } }
                // Переключатель модулей экосистемы (D-99): акцентная иконка + название. Сегодня работают ХАБ и
                // Меркурий (таблицы расчётов в комплектах); МАРС, Юпитер, Венера — в разработке, пункты отключены.
                RowLayout { spacing: 4
                    Repeater {
                        model: [
                            { id: "hub",     label: "ХАБ",      accent: Theme2.accHub,     enabled: true,
                              page: "projects", icon: "vmod/vektor_hub",
                              tip: "ВЕКТОР ХАБ — ядро платформы: проекты, комплекты, PDM" },
                            { id: "mercury", label: "Меркурий", accent: Theme2.accMercury, enabled: true,
                              page: "projects", icon: "vmod/vektor_mercury",
                              tip: "ВЕКТОР Меркурий — таблицы и спецификации (расчёты комплектов)" },
                            { id: "mars",    label: "МАРС",     accent: Theme2.accMars,    enabled: false,
                              page: "", icon: "vmod/vektor_mars",
                              tip: "ВЕКТОР МАРС — электротехнический проект (в разработке)" },
                            { id: "jupiter", label: "Юпитер",   accent: Theme2.accJupiter, enabled: false,
                              page: "", icon: "vmod/vektor_jupiter",
                              tip: "ВЕКТОР Юпитер — черчение и CAD (в разработке)" },
                            { id: "venus",   label: "Венера",   accent: Theme2.accVenus,   enabled: false,
                              page: "", icon: "vmod/vektor_venus",
                              tip: "ВЕКТОР Венера — конструкторские документы (в разработке)" }
                        ]
                        delegate: AbstractButton {
                            required property var modelData
                            // Активный модуль = модуль активной вкладки (комплект → Меркурий, остальное → ХАБ).
                            property bool current: modelData.enabled
                                && shell2.moduleOf(shell2.activeTab) === modelData.id
                            implicitHeight: Theme2.compact ? 26 : 30
                            implicitWidth: mi.implicitWidth + 14
                            enabled: modelData.enabled
                            opacity: enabled ? 1.0 : 0.45
                            Accessible.name: "Модуль " + modelData.label
                            VTipArea { text: modelData.tip }
                            onClicked: {
                                // ХАБ — раздел «Проекты»; Меркурий — первый доступный комплект (таблицы расчётов).
                                if (modelData.id === "mercury") {
                                    var ks = backend.kits || []
                                    if (ks.length > 0) shell2.openObject("kit", ks[0].id)
                                    else shell2.go("projects")
                                } else shell2.go(modelData.page)
                            }
                            background: Rectangle {
                                radius: Theme2.radiusS
                                color: parent.current ? Theme2.neutralSoft
                                     : parent.hovered ? Theme2.hover : "transparent" }
                            contentItem: RowLayout { id: mi; spacing: 6; anchors.centerIn: parent
                                Image { source: "image://icon/" + modelData.icon
                                        sourceSize: Qt.size(32, 32)
                                        Layout.preferredWidth: 13; Layout.preferredHeight: 13
                                        fillMode: Image.PreserveAspectFit; smooth: true }
                                Text { text: modelData.label.toUpperCase()
                                       color: parent.parent.parent.current ? modelData.accent : Theme2.textMuted
                                       font.family: Theme2.family; font.pixelSize: 9; font.weight: Font.DemiBold
                                       font.letterSpacing: 0.6 } }
                        }
                    }
                }
                // Хлебные крошки: раздел (кликабелен, D-117) → открытый объект
                RowLayout { spacing: 6; Layout.fillWidth: true
                    Text { objectName: "crumbPage"; text: ({ home: "Главная", customers: "Заказчики", tenders: "Тендеры и ТКП", leads: "Лиды",
                           projects: "Проекты", finance: "Итоги проектов", analytics: "Аналитика", worktime: "Рабочее время",
                           stock: "Склад", procurement: "Закупка", production: "Производство", products: "Изделия и паспорта",
                           quality: "Качество", sync: "Синхронизация", notifications: "Уведомления", delegations: "Замещения",
                           org: "Оргструктура", help: "Справка", admin: "Администрирование" })[shell2.page] || ""
                           color: crumbPageMa.containsMouse ? Theme2.primary : Theme2.textMuted
                           font.family: Theme2.family; font.pixelSize: Theme2.fsS
                           MouseArea { id: crumbPageMa; anchors.fill: parent; hoverEnabled: true
                                       cursorShape: Qt.PointingHandCursor
                                       onClicked: shell2.go(shell2.page) }
                           VToolTip { visible: crumbPageMa.containsMouse; text: "Открыть раздел «" + parent.text + "»" } }
                    Text { visible: shell2.activeTab !== null && shell2.activeTab.kind !== "page"; text: "/"
                           color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsS }
                    Text { visible: shell2.activeTab !== null && shell2.activeTab.kind !== "page"
                           text: shell2.activeTab ? shell2.activeTab.title : ""
                           color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold } }
                // Поле палитры команд (Ctrl+K)
                AbstractButton {
                    objectName: "paletteButton"
                    Layout.preferredWidth: Theme2.compact ? 240 : 320
                    implicitHeight: Theme2.compact ? 32 : 38
                    onClicked: palette.open()
                    background: Rectangle { radius: Theme2.radiusS; color: Theme2.surfaceAlt; border.color: Theme2.border }
                    contentItem: RowLayout { spacing: 8
                        Icon { Layout.leftMargin: 10; name: "search"; size: 15; color: Theme2.textFaint }
                        Text { text: "Поиск и команды…"; color: Theme2.textFaint; font.family: Theme2.family
                               font.pixelSize: Theme2.fsS; Layout.fillWidth: true }
                        Rectangle { Layout.rightMargin: 8; radius: 4; color: Theme2.surface; border.color: Theme2.border
                                    implicitWidth: ktxt.implicitWidth + 10; implicitHeight: 20
                            Text { id: ktxt; anchors.centerIn: parent; text: "Ctrl+K"; color: Theme2.textMuted
                                   font.family: Theme2.family; font.pixelSize: 10; font.weight: Font.DemiBold } } }
                }
                // Уведомления
                AbstractButton {
                    objectName: "bellButton"
                    implicitWidth: Theme2.compact ? 34 : 40; implicitHeight: width
                    onClicked: shell2.go("notifications")
                    Accessible.name: "Уведомления"
                    background: Rectangle { radius: Theme2.radiusS; color: parent.hovered ? Theme2.hover : "transparent" }
                    contentItem: Item {
                        Icon { anchors.centerIn: parent; name: "bell"; size: 18; color: Theme2.textMuted }
                        Rectangle { visible: (backend.notificationsOverview.unread || 0) > 0
                                    anchors.right: parent.right; anchors.top: parent.top
                                    anchors.rightMargin: 4; anchors.topMargin: 4
                                    implicitWidth: Math.max(15, bellN.implicitWidth + 7); height: 15; radius: 8
                                    color: Theme2.primary
                            Text { id: bellN; anchors.centerIn: parent; text: backend.notificationsOverview.unread || 0
                                   color: Theme2.textOnPrimary; font.family: Theme2.family; font.pixelSize: 9; font.weight: Font.Bold } }
                    }
                }
                // Пользователь
                RowLayout { spacing: 8
                    Rectangle { width: Theme2.compact ? 30 : 36; height: width; radius: width / 2; color: Theme2.primarySoft
                        Text { anchors.centerIn: parent; text: backend.me.initials || "?"; color: Theme2.primary
                               font.family: Theme2.family; font.pixelSize: Theme2.fsS; font.weight: Font.Bold } }
                    ColumnLayout { visible: !Theme2.compact; spacing: 0
                        Text { text: backend.me.display_name || ""; color: Theme2.text; font.family: Theme2.family
                               font.pixelSize: Theme2.fsXs; font.weight: Font.DemiBold; Layout.maximumWidth: 160; elide: Text.ElideRight }
                        Text { text: (backend.me.role_labels || []).join(", ") || "Нет ролей"; color: Theme2.textMuted
                               font.family: Theme2.family; font.pixelSize: 10; Layout.maximumWidth: 160; elide: Text.ElideRight
                               VTipArea { text: "Ваши роли: " + parent.text + (backend.tip(parent.text) !== "" ? "\n" + backend.tip(parent.text) : "") } } }
                    VButton { kind: "ghost"; compact: true; iconName: Theme.dark ? "sun" : "moon"
                              Accessible.name: "Сменить тему"; onClicked: backend.theme = Theme.dark ? "light" : "dark" }
                    VButton { kind: "ghost"; compact: true; iconName: "log-out"; Accessible.name: "Выйти"; onClicked: backend.logout() }
                }
            }
        }

        // ================= лента команд
        Rectangle {
            objectName: "ribbon"
            Layout.fillWidth: true; Layout.preferredHeight: Theme2.ribbonH
            color: Theme2.surface
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme2.border }
            ColumnLayout {
                anchors.fill: parent; spacing: 0
                RowLayout {
                    Layout.fillWidth: true; Layout.leftMargin: Theme2.gap; spacing: 2
                    RibbonTab2 { text: "Главная"; active: shell2.ribbonTab === "home"; onClicked: shell2.ribbonTab = "home" }
                    RibbonTab2 { text: "Проект"; active: shell2.ribbonTab === "project"; onClicked: shell2.ribbonTab = "project" }
                    RibbonTab2 { text: "Снабжение"; active: shell2.ribbonTab === "supply"; onClicked: shell2.ribbonTab = "supply" }
                    RibbonTab2 { text: "Производство"; active: shell2.ribbonTab === "production"; onClicked: shell2.ribbonTab = "production" }
                    RibbonTab2 { text: "Система"; active: shell2.ribbonTab === "system"; onClicked: shell2.ribbonTab = "system" }
                    Item { Layout.fillWidth: true }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme2.border }
                // Группы действий активного таба ленты (кнопки сверху, подпись группы снизу — как в Office)
                Flickable {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    contentWidth: ribbonRow.implicitWidth + Theme2.pad; contentHeight: height
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    Row {
                        id: ribbonRow
                        x: Theme2.pad - 6; height: parent.height
                        spacing: 0
                        Repeater {
                            model: shell2.ribbonGroups(shell2.ribbonTab)
                            delegate: Row {
                                id: grpRow
                                spacing: 0
                                height: ribbonRow.height
                                property var grp: modelData
                                readonly property var visibleActions: grp.actions.filter(ac => ac.visible !== false)
                                visible: visibleActions.length > 0
                                Item {
                                    width: actsRow.implicitWidth; height: ribbonRow.height
                                    Row {
                                        id: actsRow
                                        spacing: 2
                                        Repeater {
                                            model: grpRow.visibleActions
                                            delegate: RibbonAction2 {
                                                enabled: modelData.enabled
                                                warning: modelData.warning
                                                text: modelData.text; iconName: modelData.icon
                                                tip: modelData.tip || backend.tip(modelData.text)
                                                onClicked: modelData.run()
                                            }
                                        }
                                    }
                                    // Подпись группы — строго внизу группы, как в Office-ленте.
                                    Text { text: grpRow.grp.group; anchors { bottom: parent.bottom; bottomMargin: 3; horizontalCenter: parent.horizontalCenter }
                                           color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: 10 }
                                }
                                Rectangle { width: 1; height: ribbonRow.height - 18; anchors.verticalCenter: parent.verticalCenter
                                            color: Theme2.border; visible: true }
                            }
                        }
                    }
                }
            }
        }

        // ================= середина: рейл | (вкладки + контент + инспектор)
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            spacing: 0

            // ---------- иконка-рейл
            Rectangle {
                Layout.fillHeight: true; Layout.preferredWidth: Theme2.railW
                color: Theme2.sidebar
                Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme2.border }
                ColumnLayout {
                    anchors { fill: parent; margins: 5 } spacing: 4
                    Flickable {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        contentWidth: width; contentHeight: railCol.implicitHeight
                        clip: true; boundsBehavior: Flickable.StopAtBounds
                        ColumnLayout {
                            id: railCol; width: parent.width - 2; spacing: 4
                            RailItem2 { objectName: "railHome"; text: "Главная"; iconName: "house"; active: shell2.page === "home"
                                Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("home") }
                            RailItem2 { text: "Заказчики"; iconName: "building-2"; active: shell2.page === "customers"
                                visible: backend.hint("customer.read", backend.permRevision) && backend.moduleVisible("pdm")
                                Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("customers") }
                            RailItem2 { text: "Тендеры"; iconName: "gavel"; active: shell2.page === "tenders"
                                visible: backend.hint("tender.read", backend.permRevision) && backend.moduleVisible("pdm")
                                Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("tenders") }
                            RailItem2 { text: "Лиды"; iconName: "sparkles"; active: shell2.page === "leads"
                                visible: backend.hint("opportunity.read", backend.permRevision) && backend.moduleVisible("pdm")
                                Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("leads") }
                            RailItem2 { objectName: "railProjects"; text: "Проекты"; iconName: "folder-kanban"; active: shell2.page === "projects"
                                visible: backend.moduleVisible("pdm"); Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("projects") }
                            RailItem2 { text: "Итоги"; iconName: "wallet"; active: shell2.page === "finance"
                                visible: ((backend.me.roles || []).indexOf("accountant") >= 0 || backend.financeOverview.length > 0) && backend.moduleVisible("analytics")
                                Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("finance") }
                            RailItem2 { text: "Аналитика"; iconName: "sliders-horizontal"; active: shell2.page === "analytics"
                                visible: backend.hint("norm.read", backend.permRevision) && backend.moduleVisible("analytics")
                                Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("analytics") }
                            RailItem2 { text: "Время"; iconName: "clock"; active: shell2.page === "worktime"
                                visible: backend.moduleVisible("hr")
                                badge: ((backend.worktimeOverview.counts || {}).to_review || 0) + ((backend.worktimeOverview.counts || {}).returned || 0)
                                badgeTone: ((backend.worktimeOverview.counts || {}).returned || 0) > 0 ? "danger" : "warning"
                                Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("worktime") }
                            Rectangle { visible: shell2.seesStock || shell2.seesNeeds; Layout.fillWidth: true; height: 1
                                        color: Theme2.border; Layout.topMargin: 4; Layout.bottomMargin: 4 }
                            RailItem2 { text: "Склад"; iconName: "warehouse"; active: shell2.page === "stock"
                                visible: shell2.seesStock && backend.moduleVisible("supply")
                                Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("stock") }
                            RailItem2 { text: "Закупка"; iconName: "shopping-cart"; active: shell2.page === "procurement"
                                visible: shell2.seesNeeds && backend.moduleVisible("supply")
                                badge: (backend.needsOverview.candidates || []).length; badgeTone: "warning"
                                Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("procurement") }
                            Rectangle { visible: shell2.seesProduction || shell2.seesProducts || shell2.seesQuality
                                        Layout.fillWidth: true; height: 1; color: Theme2.border; Layout.topMargin: 4; Layout.bottomMargin: 4 }
                            RailItem2 { text: "Цех"; iconName: "factory"; active: shell2.page === "production"
                                visible: shell2.seesProduction && backend.moduleVisible("production")
                                badge: (backend.productionOverview.my_ops || []).length > 0 ? backend.productionOverview.my_ops.length : 0; badgeTone: "primary"
                                Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("production") }
                            RailItem2 { text: "Изделия"; iconName: "package-check"; active: shell2.page === "products"
                                visible: shell2.seesProducts && backend.moduleVisible("service")
                                Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("products") }
                            RailItem2 { text: "Качество"; iconName: "shield-check"; active: shell2.page === "quality"
                                visible: shell2.seesQuality && backend.moduleVisible("quality")
                                badge: (backend.qualityOverview.counts || {}).to_me || 0; badgeTone: "primary"
                                Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("quality") }
                        }
                    }
                    // Низ рейла: синхронизация, справка, админ — всегда под рукой
                    RailItem2 { text: "Синхр."; iconName: "refresh-cw"; active: shell2.page === "sync"
                        badge: backend.problemCount > 0 ? backend.problemCount : backend.pendingCount
                        badgeTone: backend.problemCount > 0 ? "danger" : "warning"
                        Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("sync") }
                    RailItem2 { text: "Справка"; iconName: "circle-help"; active: shell2.page === "help"
                        Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("help") }
                    RailItem2 { text: "Админ"; iconName: "settings"; active: shell2.page === "admin"
                        visible: backend.hint("admin.list_users", backend.permRevision)
                        Layout.alignment: Qt.AlignHCenter; onClicked: shell2.go("admin") }
                }
            }

            // ---------- вкладки + контент + инспектор
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true
                spacing: 0

                ObjectTabs2 {
                    Layout.fillWidth: true
                    model: shell2.openTabs
                    activeKey: shell2.activeTabKey
                    onActivated: function(key) { shell2.activateTab(key) }
                    onCloseRequested: function(key) { shell2.closeTab(key) }
                }

                RowLayout {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    spacing: 0

                    // ---------- дерево проектов (концепт A, D-101): проект → комплекты и документы.
                    // Навигация по иерархии работы — не поиском по спискам, а прямо по дереву.
                    Rectangle {
                        id: projectTree
                        objectName: "projectTree"
                        color: Theme2.surfaceAlt
                        visible: !backend.lowPerformanceMode
                        Layout.preferredWidth: visible ? (Theme2.compact ? 196 : 232) : 0
                        Layout.fillHeight: true
                        Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme2.border }
                        ColumnLayout {
                            anchors.fill: parent; spacing: 0
                            RowLayout {
                                Layout.fillWidth: true; Layout.margins: 10; spacing: 8
                                Icon { name: "folder-kanban"; size: 15; color: Theme2.primary }
                                Text { text: "Проекты"; color: Theme2.textMuted; font.family: Theme2.family
                                       font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0
                                       font.weight: Font.DemiBold; font.pixelSize: Theme2.fsS; Layout.fillWidth: true }
                                Text { text: backend.projects.length; color: Theme2.textFaint
                                       font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                                // Свернуть все узлы дерева
                                AbstractButton {
                                    id: collapseAll
                                    implicitWidth: 22; implicitHeight: 22; hoverEnabled: true
                                    visible: shell2.treeExpandedProject !== ""
                                    Accessible.name: "Свернуть все проекты"
                                    onClicked: shell2.treeExpandedProject = ""
                                    background: Rectangle { radius: 4; color: collapseAll.hovered ? Theme2.hover : "transparent" }
                                    contentItem: Icon { name: "chevron-down"; size: 13; color: Theme2.textMuted; rotation: -90 }
                                    VToolTip { visible: collapseAll.hovered; text: "Свернуть все проекты" }
                                }
                            }
                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme2.border }
                            Flickable {
                                Layout.fillWidth: true; Layout.fillHeight: true
                                contentWidth: width; contentHeight: treeCol.implicitHeight
                                clip: true; boundsBehavior: Flickable.StopAtBounds
                                Column {
                                    id: treeCol
                                    width: parent.width
                                    Repeater {
                                        model: backend.projects
                                        delegate: Column {
                                            id: treeProj
                                            property var project: modelData
                                            property bool expanded: shell2.treeExpandedProject === project.id
                                            width: treeCol.width
                                            // --- узел проекта
                                            Rectangle {
                                                width: parent.width; height: Theme2.compact ? 32 : 36
                                                color: projNodeMa.containsMouse ? Theme2.hover : "transparent"
                                                RowLayout {
                                                    anchors { fill: parent; leftMargin: 8; rightMargin: 8 } spacing: 6
                                                    Icon { name: "folder-kanban"; size: 14
                                                           color: treeProj.expanded ? Theme2.primary : Theme2.textMuted }
                                                    Text { text: (treeProj.project.code ? treeProj.project.code + " · " : "") + treeProj.project.title
                                                           color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                                                           font.weight: treeProj.expanded ? Font.DemiBold : Font.Normal
                                                           Layout.fillWidth: true; elide: Text.ElideRight }
                                                    Icon { name: "chevron-down"; size: 12; color: Theme2.textFaint
                                                           rotation: treeProj.expanded ? 0 : -90 }
                                                }
                                                MouseArea { id: projNodeMa; anchors.fill: parent; hoverEnabled: true
                                                    cursorShape: Qt.PointingHandCursor
                                                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                                                    onClicked: function(mouse) {
                                                        if (mouse.button === Qt.RightButton) { treeMenu.openFor("project", treeProj.project.id, treeProj.project); return }
                                                        if (shell2.treeExpandedProject === treeProj.project.id) shell2.treeExpandedProject = ""
                                                        else shell2.treeExpandedProject = treeProj.project.id
                                                    }
                                                    onDoubleClicked: shell2.openObject("project", treeProj.project.id) }
                                                VToolTip { visible: projNodeMa.containsMouse; text: treeProj.project.title
                                                           + " — щелчок раскрывает, двойной щелчок открывает во вкладке, правый щелчок — меню" }
                                            }
                                            // --- дети: комплекты и документы (без учётных записей в корзине)
                                            Column {
                                                visible: treeProj.expanded
                                                width: parent.width
                                                Repeater {
                                                    model: backend.kits.filter(function(k) { return k.project_id === treeProj.project.id })
                                                    delegate: Rectangle {
                                                        id: kitNode
                                                        property var kit: modelData
                                                        width: parent.width; height: Theme2.compact ? 28 : 32
                                                        color: kitNodeMa.containsMouse ? Theme2.hover : "transparent"
                                                        RowLayout {
                                                            anchors { fill: parent; leftMargin: 22; rightMargin: 8 } spacing: 6
                                                            Icon { name: "layers"; size: 13; color: Theme2.primary }
                                                            Text { text: kitNode.kit.code + " · " + kitNode.kit.title
                                                                   color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                                                                   Layout.fillWidth: true; elide: Text.ElideRight }
                                                            Rectangle { visible: kitNode.kit.pending === true; width: 7; height: 7; radius: 4
                                                                        color: Theme2.warning }
                                                        }
                                                        MouseArea { id: kitNodeMa; anchors.fill: parent; hoverEnabled: true
                                                            cursorShape: Qt.PointingHandCursor
                                                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                                                            onClicked: function(mouse) {
                                                                if (mouse.button === Qt.RightButton) { treeMenu.openFor("kit", kitNode.kit.id, kitNode.kit); return }
                                                                shell2.openObject("kit", kitNode.kit.id)
                                                            } }
                                                        VToolTip { visible: kitNodeMa.containsMouse
                                                                   text: kitNode.kit.title + " — открыть комплект во вкладке" }
                                                    }
                                                }
                                                // --- задачи проекта (открытые и в работе) — узел концепта A
                                                Repeater {
                                                    model: {
                                                        var kitIds = backend.kits.filter(function(k) { return k.project_id === treeProj.project.id })
                                                                                 .map(function(k) { return k.id })
                                                        return backend.tasks.filter(function(t) {
                                                            return kitIds.indexOf(t.kit_id) >= 0
                                                                && (t.status === "open" || t.status === "in_progress")
                                                        }).slice(0, 12)
                                                    }
                                                    delegate: Rectangle {
                                                        id: taskNode
                                                        property var task: modelData
                                                        width: parent.width; height: Theme2.compact ? 26 : 30
                                                        color: taskNodeMa.containsMouse ? Theme2.hover : "transparent"
                                                        RowLayout {
                                                            anchors { fill: parent; leftMargin: 36; rightMargin: 8 } spacing: 6
                                                            Icon { name: "list-checks"; size: 12
                                                                   color: taskNode.task.status === "in_progress" ? Theme2.info : Theme2.textFaint }
                                                            Text { text: taskNode.task.title
                                                                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                                                                   Layout.fillWidth: true; elide: Text.ElideRight }
                                                            Text { visible: (taskNode.task.due_date || "") !== ""
                                                                   text: "до " + (taskNode.task.due_date || "")
                                                                   color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                                                        }
                                                        MouseArea { id: taskNodeMa; anchors.fill: parent; hoverEnabled: true
                                                            cursorShape: Qt.PointingHandCursor
                                                            onClicked: shell2.openObject("kit", taskNode.task.kit_id) }
                                                        VToolTip { visible: taskNodeMa.containsMouse
                                                                   text: taskNode.task.title + " — открыть комплект с этой задачей" }
                                                    }
                                                }
                                                Repeater {
                                                    model: backend.documents.filter(function(d) {
                                                        var k = backend.kits.find(function(kk) { return kk.id === d.kit_id })
                                                        return k && k.project_id === treeProj.project.id
                                                    })
                                                    delegate: Rectangle {
                                                        id: docNode
                                                        property var doc: modelData
                                                        width: parent.width; height: Theme2.compact ? 26 : 30
                                                        color: docNodeMa.containsMouse ? Theme2.hover : "transparent"
                                                        RowLayout {
                                                            anchors { fill: parent; leftMargin: 36; rightMargin: 8 } spacing: 6
                                                            Icon { name: docNode.doc.kind === "sheet" ? "table" : "file-text"; size: 12
                                                                   color: Theme2.warning }
                                                            Text { text: docNode.doc.code + " · " + docNode.doc.title
                                                                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                                                                   Layout.fillWidth: true; elide: Text.ElideRight }
                                                            Rectangle { visible: docNode.doc.pending === true; width: 7; height: 7; radius: 4
                                                                        color: Theme2.warning }
                                                        }
                                                        MouseArea { id: docNodeMa; anchors.fill: parent; hoverEnabled: true
                                                            cursorShape: Qt.PointingHandCursor
                                                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                                                            onClicked: function(mouse) {
                                                                if (mouse.button === Qt.RightButton) { treeMenu.openFor("document", docNode.doc.id, docNode.doc); return }
                                                                shell2.selectObject("document", docNode.doc)
                                                            }
                                                            onDoubleClicked: shell2.openDocument(docNode.doc) }
                                                        VToolTip { visible: docNodeMa.containsMouse
                                                                   text: docNode.doc.title + " — щелчок: предпросмотр, двойной щелчок: редактор" }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // Контент активной вкладки. Все вкладки живут в StackLayout — состояние не теряется
                    // при переключении (как в браузере): черновик в поле останется на месте.
                    Item {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        // Без глухой подложки: сквозь поля страниц виден фирменный фон оболочки (D-101).
                        // Лицензионная полоса (D-88) — как в классике, поверх контента раздела.
                        Rectangle {
                            id: licenseBanner
                            objectName: "licenseBanner"
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }
                            z: 20
                            readonly property string mstate: backend.moduleState(shell2.pageModule[shell2.page] || "")
                            visible: mstate === "readonly" || mstate === "grace"
                            height: visible ? bannerText.implicitHeight + 20 : 0
                            radius: Theme2.radiusS
                            color: mstate === "readonly" ? Theme2.dangerSoft : Theme2.warningSoft
                            border.color: mstate === "readonly" ? Theme2.danger : Theme2.warning
                            border.width: 1
                            Text {
                                id: bannerText
                                objectName: "licenseBannerText"
                                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter
                                          leftMargin: 12; rightMargin: 12 }
                                wrapMode: Text.WordWrap
                                font.family: Theme2.family; font.pixelSize: Theme2.fsS
                                color: Theme2.text
                                text: licenseBanner.mstate === "readonly"
                                      ? "Подписка на этот раздел закончилась. Данные доступны для чтения и выгрузки; изменения сервер не примет. Продлите подписку — поставщик вышлет новый файл лицензии."
                                      : "Льготный период подписки: скоро раздел перейдёт в режим «только чтение». Продлите подписку, чтобы работать без ограничений."
                            }
                        }
                        Repeater {
                            model: shell2.openTabs
                            delegate: Loader {
                                anchors.fill: parent
                                anchors.topMargin: licenseBanner.visible ? licenseBanner.height + 24 : 0
                                visible: modelData.key === shell2.activeTabKey
                                sourceComponent: {
                                    if (modelData.kind === "project") return projectViewC
                                    if (modelData.kind === "kit") return kitViewC
                                    return pageComponents[modelData.id] || placeholderC
                                }
                                onLoaded: {
                                    if (modelData.kind === "project") item.projectId = modelData.id
                                    else if (modelData.kind === "kit") item.kitId = modelData.id
                                    else if (item && item.hasOwnProperty("shell")) item.shell = shell2
                                }
                            }
                        }
                    }

                    // Инспектор (предпросмотр) — виден на страницах-списках при выборе объекта.
                    InspectorPane2 {
                        Layout.fillHeight: true
                        visible: shell2.inspectorVisible
                        object: shell2.selectedObject
                        kind: shell2.selectedKind
                        team: shell2.selectedKind === "project" && shell2.selectedObject
                              ? backend.assignments.filter(a => a.project_id === shell2.selectedObject.id) : []
                        onOpenRequested: function(kind, id) { shell2.openObject(kind, id) }
                        onGoPage: function(p, id) { shell2.go(p, id) }
                        onEditDocument: function(doc) { officeEd.openFor(doc, doc.current_version_no || 1) }
                    }
                }
            }
        }

        // ================= статус-бар
        StatusBar2 { Layout.fillWidth: true; shell: shell2 }
    }

    // ---------- компоненты вкладок
    Component { id: projectViewC; ProjectView2 { shell: shell2 } }
    Component { id: kitViewC; KitView2 { shell: shell2 } }
    Component { id: placeholderC
        Item { Text { anchors.centerIn: parent; text: "Раздел недоступен"; color: Theme2.textMuted
                      font.family: Theme2.family; font.pixelSize: Theme2.fsM } } }

    // Разделы, не переписанные под 2.0, встраиваются без изменений (классические страницы).
    property var pageComponents: ({})
    Component { id: customersC; CustomersPage { shell: shell2 } }
    Component { id: tendersC; TendersPage { shell: shell2 } }
    Component { id: leadsC; LeadsPage { shell: shell2 } }
    Component { id: home2C; HomePage2 { shell: shell2 } }
    Component { id: projects2C; ProjectsPage2 { shell: shell2 } }
    Component { id: syncC; SyncPage {} }
    Component { id: financeC; FinancePage { shell: shell2 } }
    Component { id: adminC; AdminPage {} }
    Component { id: stockC; StockPage { shell: shell2 } }
    Component { id: procurementC; ProcurementPage { shell: shell2 } }
    Component { id: delegationsC; DelegationsPage { shell: shell2 } }
    Component { id: notificationsC; NotificationsPage { shell: shell2 } }
    Component { id: productionC; ProductionPage { shell: shell2 } }
    Component { id: productsC; ProductsPage { shell: shell2 } }
    Component { id: qualityC; QualityPage { shell: shell2 } }
    Component { id: helpC; HelpPage { shell: shell2 } }
    Component { id: worktimeC; WorktimePage { shell: shell2 } }
    Component { id: orgC; OrgPage { shell: shell2 } }
    Component { id: analyticsC; AnalyticsPage2 { shell: shell2 } }   // D-127: аналитика в стиле 2.0

    // ---------- палитра команд и офисный редактор (документы из инспектора)
    CommandPalette2 { id: palette; commands: shell2.commandsCache }
    OfficeEditor { id: officeEd }

    // ---------- контекстное меню дерева проектов (D-124): правый щелчок по узлу
    Menu {
        id: treeMenu
        objectName: "treeMenu"
        property string kind: ""
        property string objId: ""
        property var obj: null
        function openFor(kind, id, obj) {
            treeMenu.kind = kind; treeMenu.objId = id; treeMenu.obj = obj
            treeMenu.popup()
        }
        MenuItem { text: treeMenu.kind === "document" ? "Открыть в редакторе" : "Открыть во вкладке"
            onTriggered: treeMenu.kind === "document" ? shell2.openDocument(treeMenu.obj)
                         : shell2.openObject(treeMenu.kind, treeMenu.objId) }
        MenuItem { visible: treeMenu.kind === "document"; text: "Показать в инспекторе"
            onTriggered: shell2.selectObject("document", treeMenu.obj) }
        MenuItem { visible: treeMenu.kind === "project"; height: visible ? implicitHeight : 0
                   text: shell2.treeExpandedProject === treeMenu.objId ? "Свернуть" : "Развернуть"
            onTriggered: shell2.treeExpandedProject = shell2.treeExpandedProject === treeMenu.objId ? "" : treeMenu.objId }
    }

    // ---------- диалоги рабочего дня и переподключения (те же, что в классике, — правила не меняются)
    FormDialog {
        id: workdayPrompt
        objectName: "workdayPrompt"
        insist: true
        title: "Начните рабочий день"
        subtitle: "Таймер рабочего дня сегодня не запущен. Рабочий день обязательно начинать — нажмите «Начать рабочий день», и время будет считаться по часам этого компьютера."
                  + (backend.workdayOverview.schedule_start_label ? " Начало по графику — " + backend.workdayOverview.schedule_start_label + "." : "")
        note: "Начали работу раньше? После «Начать» укажите фактическое начало — «Я начал раньше» в разделе «Рабочее время» (не раньше начала по графику): руководитель согласует, и таймер посчитает день с него."
        confirmText: "Начать рабочий день"
        cancelText: "Выйти из учётной записи"
        onConfirmed: { working = true; backend.workdayMark("workday-prompt", "start") }
        onDismissed: { shell2.loggingOut = true; backend.logout() }
        onClosed: if (shell2.wantsWorkdayPrompt && !shell2.loggingOut) Qt.callLater(function() { if (shell2.wantsWorkdayPrompt && !workdayPrompt.visible) workdayPrompt.open() })
        Connections { target: backend
            function onCommandDone(t, r) { if (t !== "workday-prompt") return; workdayPrompt.working = false
                if (r.status !== "accepted" && r.status !== "queued") workdayPrompt.error = r.message_ru || "День не начат." } }
    }
    property bool loggingOut: false
    readonly property bool wantsWorkdayPrompt: backend.workdayOverview.prompt === true
    onWantsWorkdayPromptChanged: {
        if (wantsWorkdayPrompt && !workdayPrompt.visible) workdayPrompt.open()
        else if (!wantsWorkdayPrompt && workdayPrompt.visible) workdayPrompt.close()
    }

    FormDialog {
        id: workdayEndPrompt
        objectName: "workdayEndPrompt"
        insist: true
        title: "Рабочий день по графику закончился"
        subtitle: backend.workdayOverview.reminder_text || ""
        readonly property var check: wdFinishAt.text.trim() === "" ? ({ ok: true, at: "", message: "" }) : backend.workdayFinishCheck(wdFinishAt.text)
        note: check.ok === true && check.message ? check.message : ""
        canConfirm: check.ok === true
        confirmText: "Завершить день"
        confirmTip: "Завершить рабочий день сейчас (или во время из поля «Закончил в»). Можно без связи — уйдёт при подключении."
        cancelText: "Работаю дальше"
        onOpened: wdFinishAt.text = ""
        onConfirmed: { working = true; backend.workdayFinishAt("workday-end-prompt", check.at || "") }
        onDismissed: backend.workdaySnooze()
        VField { id: wdFinishAt; objectName: "wdFinishAt"; label: "Закончил в (если раньше, чем сейчас)"; placeholder: "17:40"
                 hint: "Необязательно. ЧЧ:ММ по часам компьютера — не раньше последнего нажатия таймера."
                 error: workdayEndPrompt.check.ok === false ? workdayEndPrompt.check.message : "" }
        Connections { target: backend
            function onCommandDone(t, r) { if (t !== "workday-end-prompt") return; workdayEndPrompt.working = false
                if (r.status === "accepted" || r.status === "queued") workdayEndPrompt.close(); else workdayEndPrompt.error = r.message_ru || "День не завершён." } }
    }
    readonly property bool wantsWorkdayEnd: backend.workdayOverview.reminder === true
    onWantsWorkdayEndChanged: {
        if (wantsWorkdayEnd && !workdayEndPrompt.visible) workdayEndPrompt.open()
        else if (!wantsWorkdayEnd && workdayEndPrompt.visible) workdayEndPrompt.close()
    }

    FormDialog {
        id: reconnect
        title: "Подключение к серверу"
        subtitle: "Введите пароль, чтобы получить сеанс и отправить локальную очередь изменений (" + backend.pendingCount + ")."
        confirmText: "Подключиться"
        onConfirmed: { backend.reconnect(rpwd2.text); rpwd2.text = ""; close() }
        VField { id: rpwd2; label: "Пароль"; password: true; onAccepted: reconnect.confirmed() }
    }
}
