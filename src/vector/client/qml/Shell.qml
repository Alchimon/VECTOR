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
    Component.onCompleted: pickStart()
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
                            Accessible.name: "ВЕКТОР"
                            VTipArea { text: "ВЕКТОР — единая платформа для инженеров и бизнеса: тендеры, проекты, расчёты, склад, закупка, производство, качество, рабочее время" } }
                    // Подпись — полностью, как в логотипе владельца (D-28): «Единая платформа / для инженеров и бизнеса»,
                    // в две строки, чтобы поместиться в ширину боковой панели и не обрезаться.
                    ColumnLayout { spacing: 0
                        Text { text: "ВЕКТОР"; color: Theme.text; font.family: Theme.family; font.pixelSize: 17; font.weight: Font.DemiBold; font.letterSpacing: 2 }
                        Text { objectName: "shellTagline"; text: "Единая платформа\nдля инженеров и бизнеса"; color: Theme.textFaint; font.family: Theme.family
                               font.pixelSize: Theme.fsXs; lineHeight: 0.95 } }
                }
                Text { text: "РАБОТА"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1.2; Layout.leftMargin: 10; Layout.bottomMargin: 4 }
                NavItem { text: "Заказчики"; iconName: "building-2"; active: shellRoot.page === "customers"; onClicked: shellRoot.go("customers"); visible: backend.hint("customer.read", backend.permRevision) }
                NavItem { text: "Тендеры и ТКП"; iconName: "gavel"; active: shellRoot.page === "tenders"; onClicked: shellRoot.go("tenders"); visible: backend.hint("tender.read", backend.permRevision) }
                NavItem { text: "Проекты"; iconName: "folder-kanban"; active: shellRoot.page === "projects"; onClicked: shellRoot.go("projects") }
                NavItem { text: "Итоги проектов"; iconName: "wallet"; active: shellRoot.page === "finance"; onClicked: shellRoot.go("finance")
                          visible: (backend.me.roles || []).indexOf("accountant") >= 0 || backend.financeOverview.length > 0 }
                // Рабочее время (D-42): свой дневной отчёт — всем; бейдж — руководителю отдела отчёты на проверке,
                // остальным — возвращённые на исправление.
                NavItem { objectName: "navWorktime"; text: "Рабочее время"; iconName: "clock"; active: shellRoot.page === "worktime"; onClicked: shellRoot.go("worktime")
                          badge: ((backend.worktimeOverview.counts || {}).to_review || 0) + ((backend.worktimeOverview.counts || {}).returned || 0)
                          badgeTone: ((backend.worktimeOverview.counts || {}).returned || 0) > 0 ? "danger" : "warning" }
                Text { visible: shellRoot.seesStock || shellRoot.seesNeeds; text: "СНАБЖЕНИЕ"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1.2; Layout.leftMargin: 10; Layout.topMargin: 14; Layout.bottomMargin: 4 }
                NavItem { objectName: "navStock"; text: "Склад"; iconName: "warehouse"; active: shellRoot.page === "stock"; onClicked: shellRoot.go("stock"); visible: shellRoot.seesStock }
                NavItem { objectName: "navProcurement"; text: "Закупка"; iconName: "shopping-cart"; active: shellRoot.page === "procurement"; onClicked: shellRoot.go("procurement"); visible: shellRoot.seesNeeds
                          badge: (backend.needsOverview.candidates || []).length; badgeTone: "warning" }
                Text { visible: shellRoot.seesProduction || shellRoot.seesProducts || shellRoot.seesQuality; text: "ПРОИЗВОДСТВО"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1.2; Layout.leftMargin: 10; Layout.topMargin: 14; Layout.bottomMargin: 4 }
                // Бейдж: исполнителю — операции в работе/на паузе, остальным — открытые наряды.
                NavItem { objectName: "navProduction"; text: "Производство"; iconName: "factory"; active: shellRoot.page === "production"; onClicked: shellRoot.go("production"); visible: shellRoot.seesProduction
                          badge: (backend.productionOverview.my_ops || []).length > 0 ? backend.productionOverview.my_ops.length : 0; badgeTone: "primary" }
                // Изделия и паспорта (D-38): экземпляры с заводскими номерами, паспорт-черновик, эксплуатация.
                NavItem { objectName: "navProducts"; text: "Изделия и паспорта"; iconName: "package-check"; active: shellRoot.page === "products"; onClicked: shellRoot.go("products"); visible: shellRoot.seesProducts }
                // Качество (D-39): бейдж — что ждёт моего решения или отметки (проверки, решения, мои действия CAPA).
                NavItem { objectName: "navQuality"; text: "Качество"; iconName: "shield-check"; active: shellRoot.page === "quality"; onClicked: shellRoot.go("quality"); visible: shellRoot.seesQuality
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
                NavItem { objectName: "navHelp"; text: "Сокращения и подсказки"; iconName: "circle-help"; active: shellRoot.page === "help"; onClicked: shellRoot.go("help") }
                NavItem { text: "Администрирование"; iconName: "shield-check"; active: shellRoot.page === "admin"; onClicked: shellRoot.go("admin"); visible: backend.hint("admin.list_users", backend.permRevision) }
                Item { Layout.fillHeight: true }
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
                    VButton { kind: "ghost"; compact: true; iconName: "log-out"; Accessible.name: "Выйти"; onClicked: backend.logout() }
                }
            }
        }

        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
            // Фон рабочей области — та же «чертёжная» композиция, приглушённая: видна в полях между карточками,
            // сами карточки непрозрачны, поэтому данные читаются как прежде.
            Image {
                objectName: "shellBackdrop"
                anchors.fill: parent
                source: Theme.backdrop
                fillMode: Image.PreserveAspectCrop
                opacity: Theme.backdropOpacity
                asynchronous: true; smooth: true; mipmap: true
            }
        Loader {
            anchors.fill: parent
            sourceComponent: ({ customers: customersC, tenders: tendersC, projects: projectsC, sync: syncC, admin: adminC, finance: financeC,
                               stock: stockC, procurement: procurementC, delegations: delegationsC,
                               notifications: notificationsC, production: productionC, products: productsC, quality: qualityC, help: helpC,
                               worktime: worktimeC, org: orgC })[shellRoot.page]
        }
        }
    }
    Component { id: customersC; CustomersPage { shell: shellRoot } }
    Component { id: tendersC; TendersPage { shell: shellRoot } }
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

    FormDialog {
        id: reconnect
        title: "Подключение к серверу"
        subtitle: "Введите пароль, чтобы получить сеанс и отправить локальную очередь изменений (" + backend.pendingCount + ")."
        confirmText: "Подключиться"
        onConfirmed: { backend.reconnect(rpwd.text); rpwd.text = ""; close() }
        VField { id: rpwd; label: "Пароль"; password: true; onAccepted: reconnect.confirmed() }
    }
}
