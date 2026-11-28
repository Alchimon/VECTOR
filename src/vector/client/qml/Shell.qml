import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Рабочая оболочка после входа: левое меню разделов, верхняя строка (связь, очередь), область страницы.
// page — текущий раздел; go(page, id) — перейти к разделу и (необязательно) открыть объект id (navTarget).
// Видимость пунктов меню — подсказка по правам (backend.hint); настоящий отказ всегда даёт сервер.
// Горячие клавиши: Ctrl+1…9 и Ctrl+0 (рабочее время) — разделы, F5 — синхронизация. В офлайн-режиме внизу — кнопка переподключения.
Item {
    id: shellRoot
    objectName: "shell"
    property string page: "tenders"
    property var navTarget: ({})     // {page, id} — переход к объекту из другого раздела
    function go(p, id) { navTarget = { page: p, id: id || "" }; page = p }
    // Страница → модуль лицензии (D-88): по нему полоса состояния раздела знает, что показывать.
    // Платформенные страницы (sync, admin, help, org, notifications, delegations) в лицензии не участвуют.
    readonly property var pageModule: ({ customers: "pdm", tenders: "pdm", projects: "pdm", leads: "pdm",
        finance: "analytics", analytics: "analytics", stock: "supply", procurement: "supply",
        worktime: "hr", production: "production", products: "service", quality: "quality" })
    // Стартовый раздел по ролям: у склада и закупки нет тендеров — сразу открываем их рабочий раздел.
    // Роли приходят из кэша чуть позже создания оболочки, поэтому выбор делается один раз — когда они известны.
    property bool startPicked: false
    function pickStart() {
        if (startPicked || !backend.me.user_id) return
        startPicked = true
        var rs = backend.me.roles || []
        if (page === "tenders" && !backend.hint("tender.read", backend.permRevision))
            // Технолог и производство (D-36) начинают с раздела «Производство».
            // ОТК (отдел технического контроля, D-40) — с раздела «Качество».
            page = rs.some(r => r === "production" || r === "technologist") ? "production"
                 : rs.some(r => r === "otk") ? "quality"
                 : backend.hint("stock.read", backend.permRevision) ? "stock" : "projects"
    }
    Component.onCompleted: { pickStart(); if (wantsWorkdayPrompt) workdayPrompt.open() }
    Connections { target: backend; function onChanged() { shellRoot.pickStart() } }

    Shortcut { sequence: "Ctrl+1"; onActivated: shellRoot.go("customers") }
    Shortcut { sequence: "Ctrl+2"; onActivated: shellRoot.go("tenders") }
    Shortcut { sequence: "Ctrl+3"; onActivated: shellRoot.go("projects") }
    Shortcut { sequence: "Ctrl+4"; onActivated: shellRoot.go("sync") }
    Shortcut { sequence: "Ctrl+5"; onActivated: if (shellRoot.seesStock) shellRoot.go("stock") }
    Shortcut { sequence: "Ctrl+6"; onActivated: if (shellRoot.seesNeeds) shellRoot.go("procurement") }
    // Подсказки видимости разделов (решает сервер: без права pull просто не вернёт данные).
    readonly property bool seesStock: backend.hint("stock.read", backend.permRevision)
    readonly property bool seesNeeds: seesStock || (backend.needsOverview.needs || []).length > 0
        || (backend.me.roles || []).indexOf("accountant") >= 0   // бухгалтерия видит заказы поставщикам (D-32)
        || (backend.me.roles || []).indexOf("po_approver") >= 0  // согласующий закупки — заказы на согласовании (D-33)
        || (backend.me.roles || []).indexOf("dept_head") >= 0    // руководитель отдела «в курсе» согласований (D-34)
    Shortcut { sequence: "Ctrl+7"; onActivated: if (shellRoot.seesProduction) shellRoot.go("production") }
    // Производство (D-36): технолог, исполнитель, руководитель отдела, склад — и все, кому сервер отдал наряды или ТП
    // (РП и ГИП проектов). Подсказка; данные решает сервер.
    readonly property bool seesProduction: (backend.me.roles || []).some(r => r === "technologist" || r === "production" || r === "dept_head" || r === "warehouse")
        || (backend.productionOverview.work_orders || []).length > 0 || (backend.productionOverview.tps || []).length > 0
    Shortcut { sequence: "Ctrl+8"; onActivated: if (shellRoot.seesProducts) shellRoot.go("products") }
    // Изделия и паспорта (D-38): команда проекта, технологи, руководитель отдела — и все, кому сервер отдал
    // экземпляры или закрытые наряды для регистрации. Подсказка; данные решает сервер.
    readonly property bool seesProducts: backend.lifecycleOverview.sees === true
    Shortcut { sequence: "Ctrl+9"; onActivated: if (shellRoot.seesQuality) shellRoot.go("quality") }
    // Качество (D-39): рекламации и CAPA, запросы и извещения об изменении — команда проекта, технологи, руководитель
    // отдела и все, кому сервер отдал данные качества. Подсказка; данные решает сервер.
    readonly property bool seesQuality: backend.qualityOverview.sees === true
    // Рабочее время (D-42): дневной отчёт ведёт каждый — раздел виден всем.
    Shortcut { sequence: "Ctrl+0"; onActivated: shellRoot.go("worktime") }
    Shortcut { sequence: "F5"; onActivated: backend.syncNow() }
    // Справочник сокращений и подсказок (D-37) — всем, без связи тоже.
    Shortcut { sequence: "F1"; onActivated: shellRoot.go("help") }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: 248
            color: Theme.sidebar
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.border }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 14
                spacing: 4
                RowLayout {
                    Layout.bottomMargin: 18; Layout.topMargin: 6; Layout.leftMargin: 6
                    spacing: 10
                    // Знак «V» из логотипа владельца (D-28) и название; полный логотип — на экране входа.
                    Image { objectName: "shellMark"; source: Theme.mark; Layout.preferredWidth: 46; Layout.preferredHeight: 46
                            sourceSize: Qt.size(128, 128); fillMode: Image.PreserveAspectFit; smooth: true; mipmap: true
                            Accessible.name: "ВЕКТОР Платформа"
                            VTipArea { text: "ВЕКТОР Платформа — единая платформа для инженеров и бизнеса: тендеры, проекты, расчёты, склад, закупка, производство, качество, рабочее время" } }
                    // Подпись — полностью, как в логотипе владельца (D-28): «Единая платформа / для инженеров и бизнеса»,
                    // в две строки, чтобы поместиться в ширину боковой панели и не обрезаться.
                    ColumnLayout { spacing: 0
                        Text { text: "ВЕКТОР Платформа"; color: Theme.text; font.family: Theme.family; font.pixelSize: 14; font.weight: Font.DemiBold; font.letterSpacing: 1.2 }
                        Text { objectName: "shellTagline"; text: "Единая платформа\nдля инженеров и бизнеса"; color: Theme.textFaint; font.family: Theme.family
                               font.pixelSize: Theme.fsXs; lineHeight: 0.95 } }
                }
                // Меню прокручивается, если пунктов больше, чем помещается по высоте (D-46: под меню появился таймер
                // рабочего дня) — таймер, состояние связи и пользователь с кнопкой «Выйти» всегда остаются видны внизу.
                Flickable {
                    id: navFlick
                    objectName: "navScroll"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    contentWidth: width; contentHeight: navCol.implicitHeight
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: navFlick.contentHeight > navFlick.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }
                    ColumnLayout {
                        id: navCol
                        width: navFlick.width
                        spacing: 4
                    Text { text: "РАБОТА"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1.2; Layout.leftMargin: 10; Layout.bottomMargin: 4 }
                    NavItem { text: "Заказчики"; iconName: "building-2"; active: shellRoot.page === "customers"; onClicked: shellRoot.go("customers"); visible: backend.hint("customer.read", backend.permRevision) && backend.moduleVisible("pdm") }
                    NavItem { text: "Тендеры и ТКП"; iconName: "gavel"; active: shellRoot.page === "tenders"; onClicked: shellRoot.go("tenders"); visible: backend.hint("tender.read", backend.permRevision) && backend.moduleVisible("pdm") }
                    NavItem { objectName: "navLeads"; text: "Лиды"; iconName: "sparkles"; active: shellRoot.page === "leads"; onClicked: shellRoot.go("leads")
                              visible: backend.hint("opportunity.read", backend.permRevision) && backend.moduleVisible("pdm") }
                    NavItem { text: "Проекты"; iconName: "folder-kanban"; active: shellRoot.page === "projects"; onClicked: shellRoot.go("projects"); visible: backend.moduleVisible("pdm") }
                    NavItem { text: "Итоги проектов"; iconName: "wallet"; active: shellRoot.page === "finance"; onClicked: shellRoot.go("finance")
                              visible: ((backend.me.roles || []).indexOf("accountant") >= 0 || backend.financeOverview.length > 0) && backend.moduleVisible("analytics") }
                    // Аналитика портфеля (D-73): сводные сроки/количества без денег — производственному руководству.
                    NavItem { objectName: "navAnalytics"; text: "Аналитика"; iconName: "sliders-horizontal"; active: shellRoot.page === "analytics"; onClicked: shellRoot.go("analytics")
                              visible: backend.hint("norm.read", backend.permRevision) && backend.moduleVisible("analytics") }
                    // Рабочее время (D-42): свой дневной отчёт — всем; бейдж — руководителю отдела отчёты на проверке,
                    // остальным — возвращённые на исправление.
                    NavItem { objectName: "navWorktime"; text: "Рабочее время"; iconName: "clock"; active: shellRoot.page === "worktime"; onClicked: shellRoot.go("worktime")
                              badge: ((backend.worktimeOverview.counts || {}).to_review || 0) + ((backend.worktimeOverview.counts || {}).returned || 0)
                              badgeTone: ((backend.worktimeOverview.counts || {}).returned || 0) > 0 ? "danger" : "warning"; visible: backend.moduleVisible("hr") }
                    Text { visible: shellRoot.seesStock || shellRoot.seesNeeds; text: "СНАБЖЕНИЕ"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1.2; Layout.leftMargin: 10; Layout.topMargin: 14; Layout.bottomMargin: 4 }
                    NavItem { objectName: "navStock"; text: "Склад"; iconName: "warehouse"; active: shellRoot.page === "stock"; onClicked: shellRoot.go("stock"); visible: shellRoot.seesStock && backend.moduleVisible("supply") }
                    NavItem { objectName: "navProcurement"; text: "Закупка"; iconName: "shopping-cart"; active: shellRoot.page === "procurement"; onClicked: shellRoot.go("procurement"); visible: shellRoot.seesNeeds && backend.moduleVisible("supply")
                              badge: (backend.needsOverview.candidates || []).length; badgeTone: "warning" }
                    Text { visible: shellRoot.seesProduction || shellRoot.seesProducts || shellRoot.seesQuality; text: "ПРОИЗВОДСТВО"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1.2; Layout.leftMargin: 10; Layout.topMargin: 14; Layout.bottomMargin: 4 }
                    // Бейдж: исполнителю — операции в работе/на паузе, остальным — открытые наряды.
                    NavItem { objectName: "navProduction"; text: "Производство"; iconName: "factory"; active: shellRoot.page === "production"; onClicked: shellRoot.go("production"); visible: shellRoot.seesProduction && backend.moduleVisible("production")
                              badge: (backend.productionOverview.my_ops || []).length > 0 ? backend.productionOverview.my_ops.length : 0; badgeTone: "primary" }
                    // Изделия и паспорта (D-38): экземпляры с заводскими номерами, паспорт-черновик, эксплуатация.
                    NavItem { objectName: "navProducts"; text: "Изделия и паспорта"; iconName: "package-check"; active: shellRoot.page === "products"; onClicked: shellRoot.go("products"); visible: shellRoot.seesProducts && backend.moduleVisible("service") }
                    // Качество (D-39): бейдж — что ждёт моего решения или отметки (проверки, решения, мои действия CAPA).
                    NavItem { objectName: "navQuality"; text: "Качество"; iconName: "shield-check"; active: shellRoot.page === "quality"; onClicked: shellRoot.go("quality"); visible: shellRoot.seesQuality && backend.moduleVisible("quality")
                              badge: (backend.qualityOverview.counts || {}).to_me || 0; badgeTone: "primary" }
                    Text { text: "СИСТЕМА"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1.2; Layout.leftMargin: 10; Layout.topMargin: 14; Layout.bottomMargin: 4 }
                    NavItem { text: "Синхронизация"; iconName: "refresh-cw"; active: shellRoot.page === "sync"; onClicked: shellRoot.go("sync")
                              badge: backend.problemCount > 0 ? backend.problemCount : backend.pendingCount; badgeTone: backend.problemCount > 0 ? "danger" : "warning" }
                    // Уведомления (D-35): свои — заказы на согласовании, решения, замещения; бейдж — непрочитанные.
                    NavItem { objectName: "navNotifications"; text: "Уведомления"; iconName: "bell"; active: shellRoot.page === "notifications"
                              onClicked: shellRoot.go("notifications"); badge: backend.notificationsOverview.unread || 0; badgeTone: "primary" }
                    // Замещения — всем: свою роль может передать каждый (D-34). Бейдж: руководителю — роли, которые заменить
                    // некем; остальным — сколько ролей коллег он сейчас замещает.
                    NavItem { objectName: "navDelegations"; text: "Замещения"; iconName: "user-check"; active: shellRoot.page === "delegations"; onClicked: shellRoot.go("delegations")
                              badge: backend.delegationsOverview.manage ? backend.delegationsOverview.warnings : backend.delegationsOverview.active_received
                              badgeTone: backend.delegationsOverview.manage ? "warning" : "primary" }
                    // Оргструктура (D-44) — всем: справочник компании и «кто проверяет мои отчёты»; правит администратор.
                    NavItem { objectName: "navOrg"; text: "Оргструктура"; iconName: "network"; active: shellRoot.page === "org"; onClicked: shellRoot.go("org") }
                    NavItem { objectName: "navHelp"; text: "Справка"; iconName: "circle-help"; active: shellRoot.page === "help"; onClicked: shellRoot.go("help") }
                    NavItem { text: "Администрирование"; iconName: "shield-check"; active: shellRoot.page === "admin"; onClicked: shellRoot.go("admin"); visible: backend.hint("admin.list_users", backend.permRevision) }
                    }
                }
                // Таймер рабочего дня (D-46) — всегда под рукой: состояние, «сколько натикало», кнопки.
                WorkdayCard { compact: true; Layout.fillWidth: true; Layout.bottomMargin: 8 }
                ConnectionPill {}
                VButton { visible: backend.sessionMode === "offline"; text: "Подключиться"; kind: "soft"; iconName: "cloud"; Layout.fillWidth: true; onClicked: reconnect.open() }
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border; Layout.topMargin: 10; Layout.bottomMargin: 10 }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Rectangle { width: 34; height: 34; radius: 17; color: Theme.primarySoft
                        Text { anchors.centerIn: parent; text: backend.me.initials || "?"; color: Theme.primary; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.Bold } }
                    ColumnLayout { spacing: 0; Layout.fillWidth: true
                        Text { text: backend.me.display_name || ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                        Text { text: (backend.me.role_labels || []).join(", ") || "Нет ролей"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; elide: Text.ElideRight; Layout.fillWidth: true
                               // Подсказка (D-37): все роли полностью и расшифровка сокращений (ГИП, РП).
                               VTipArea { text: "Ваши роли: " + parent.text + (backend.tip(parent.text) !== "" ? "\n" + backend.tip(parent.text) : "") } } }
                    VButton { kind: "ghost"; compact: true; iconName: Theme.dark ? "sun" : "moon"; Accessible.name: "Сменить тему"; onClicked: backend.theme = Theme.dark ? "light" : "dark" }
                    // Переход в интерфейс 2.0 (D-98): лента команд, вкладки объектов, предпросмотр, статус-бар.
                    VButton { objectName: "btnUiV2"; kind: "ghost"; compact: true; iconName: "sparkles"; Accessible.name: "Новый вид интерфейса"
                              tip: "Попробовать интерфейс 2.0: лента команд, вкладки открытых объектов, предпросмотр справа, палитра команд Ctrl+K. Вернуться можно в любой момент (лента «Главная» → «Классический вид»)."
                              onClicked: backend.uiMode = "v2" }
                    VButton { kind: "ghost"; compact: true; iconName: "log-out"; Accessible.name: "Выйти"; onClicked: backend.logout() }
                }
            }
        }

        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
            // Фон рабочей области — фирменная композиция экосистемы («космос/чертёж», D-99) под плотной вуалью
            // цвета холста (≈0,82): фирменный образ читается в полях между карточками, сами карточки непрозрачны,
            // данные не страдают.
            Image {
                objectName: "shellBackdrop"
                anchors.fill: parent
                source: Theme.backdrop
                fillMode: Image.PreserveAspectCrop
                asynchronous: true; smooth: true; mipmap: true
            }
            Rectangle {
                anchors.fill: parent
                // Фирменный фон присутствует ВСЕГДА (решение владельца, D-101): вуаль цвета холста 0,80
                // в тёмной теме и 0,85 в светлой — узор «космос/чертёж» читается в обеих, данные на
                // непрозрачных карточках не страдают.
                color: Qt.alpha(Theme.bg, Theme.dark ? 0.80 : 0.85)
            }
        Loader {
            anchors.fill: parent
            anchors.topMargin: licenseBanner.visible ? licenseBanner.height + 20 : 0
            sourceComponent: ({ customers: customersC, tenders: tendersC, projects: projectsC, sync: syncC, admin: adminC, finance: financeC,
                               stock: stockC, procurement: procurementC, delegations: delegationsC,
                               notifications: notificationsC, production: productionC, products: productsC, quality: qualityC, help: helpC,
                               worktime: worktimeC, org: orgC, leads: leadsC, analytics: analyticsC })[shellRoot.page]
        }
        // Полоса состояния лицензии раздела (D-88). readonly: подписка истекла — данные читаются и выгружаются,
        // изменения отклонит сервер (ворота — там, это только честная подсказка). grace: льготный период —
        // пора продлить. Появляется только для страниц, принадлежащих лицензируемым модулям.
        Rectangle {
            id: licenseBanner
            objectName: "licenseBanner"
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }
            z: 20
            readonly property string mstate: backend.moduleState(shellRoot.pageModule[shellRoot.page] || "")
            visible: mstate === "readonly" || mstate === "grace"
            height: visible ? bannerText.implicitHeight + 20 : 0
            radius: Theme.radius
            color: mstate === "readonly" ? Theme.dangerSoft : Theme.warningSoft
            border.color: mstate === "readonly" ? Theme.danger : Theme.warning
            border.width: 1
            Text {
                id: bannerText
                objectName: "licenseBannerText"
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter
                          leftMargin: 12; rightMargin: 12 }
                wrapMode: Text.WordWrap
                font.family: Theme.family; font.pixelSize: Theme.fsS
                color: Theme.text
                text: licenseBanner.mstate === "readonly"
                      ? "Подписка на этот раздел закончилась. Данные доступны для чтения и выгрузки; изменения сервер не примет. Продлите подписку — поставщик вышлет новый файл лицензии."
                      : "Льготный период подписки: скоро раздел перейдёт в режим «только чтение». Продлите подписку, чтобы работать без ограничений."
            }
        }
        }
    }
    Component { id: customersC; CustomersPage { shell: shellRoot } }
    Component { id: tendersC; TendersPage { shell: shellRoot } }
    Component { id: leadsC; LeadsPage { shell: shellRoot } }
    Component { id: analyticsC; AnalyticsPage { shell: shellRoot } }
    Component { id: projectsC; ProjectsPage { shell: shellRoot } }
    Component { id: syncC; SyncPage {} }
    Component { id: financeC; FinancePage { shell: shellRoot } }
    Component { id: adminC; AdminPage {} }
    Component { id: stockC; StockPage { shell: shellRoot } }
    Component { id: procurementC; ProcurementPage { shell: shellRoot } }
    Component { id: delegationsC; DelegationsPage { shell: shellRoot } }
    Component { id: notificationsC; NotificationsPage { shell: shellRoot } }
    Component { id: productionC; ProductionPage { shell: shellRoot } }
    Component { id: productsC; ProductsPage { shell: shellRoot } }
    Component { id: qualityC; QualityPage { shell: shellRoot } }
    Component { id: helpC; HelpPage { shell: shellRoot } }
    Component { id: worktimeC; WorktimePage { shell: shellRoot } }
    Component { id: orgC; OrgPage { shell: shellRoot } }

    // Окно «Начните рабочий день» (D-46, A-49 п. 4; D-47 — A-51 пп. 3–4: «рабочий день надо обязательно начинать»,
    // окно «настаивает»): сегодня рабочий день по графику, а таймер не запускали. Кнопки «Позже» нет, крестик и Esc не
    // закрывают; можно начать день или выйти из учётной записи (чужой компьютер). Если окно всё же закрылось, а день
    // не начат, — оно открывается снова. Начали раньше — после «Начать» «Я начал раньше» в «Рабочем времени».
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
        onDismissed: { shellRoot.loggingOut = true; backend.logout() }
        onClosed: if (shellRoot.wantsWorkdayPrompt && !shellRoot.loggingOut) Qt.callLater(function() { if (shellRoot.wantsWorkdayPrompt && !workdayPrompt.visible) workdayPrompt.open() })
        Connections { target: backend
            function onCommandDone(t, r) { if (t !== "workday-prompt") return; workdayPrompt.working = false
                if (r.status !== "accepted" && r.status !== "queued") workdayPrompt.error = r.message_ru || "День не начат." } }
    }
    property bool loggingOut: false
    // Окно открывается, когда снимок говорит «пора», и закрывается, когда день начат.
    readonly property bool wantsWorkdayPrompt: backend.workdayOverview.prompt === true
    onWantsWorkdayPromptChanged: {
        if (wantsWorkdayPrompt && !workdayPrompt.visible) workdayPrompt.open()
        else if (!wantsWorkdayPrompt && workdayPrompt.visible) workdayPrompt.close()
    }

    // Напоминание о конце рабочего дня (D-47, A-51 п. 5: «если человек забыл завершить день, а время всё так же
    // идёт»): по графику день закончился, таймер идёт. «Завершить день» (поле «Закончил в» — если закончили раньше),
    // «Работаю дальше» — напомнить повторно через интервал из настроек компании (D-49). Активность сотрудника не
    // отслеживается; без ответа после настроенного ожидания программа завершит день по времени напоминания; работали
    // дольше — «Я работал дольше» (согласует руководитель).
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
        onConfirmed: { backend.reconnect(rpwd.text); rpwd.text = ""; close() }
        VField { id: rpwd; label: "Пароль"; password: true; onAccepted: reconnect.confirmed() }
    }
}
