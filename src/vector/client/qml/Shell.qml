import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

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
        if (page === "tenders" && !backend.hint("tender.read", backend.permRevision))
            page = backend.hint("stock.read", backend.permRevision) ? "stock" : "projects"
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
    Shortcut { sequence: "F5"; onActivated: backend.syncNow() }

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
                    Rectangle { width: 34; height: 34; radius: 9; color: Theme.primary
                        Icon { anchors.centerIn: parent; name: "layers"; size: 19; color: Theme.textOnPrimary } }
                    ColumnLayout { spacing: -2
                        Text { text: "ВЕКТОР"; color: Theme.text; font.family: Theme.family; font.pixelSize: 16; font.weight: Font.Black; font.letterSpacing: 1.5 }
                        Text { text: "инженерная платформа"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs } }
                }
                Text { text: "РАБОТА"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1.2; Layout.leftMargin: 10; Layout.bottomMargin: 4 }
                NavItem { text: "Заказчики"; iconName: "building-2"; active: shellRoot.page === "customers"; onClicked: shellRoot.go("customers"); visible: backend.hint("customer.read", backend.permRevision) }
                NavItem { text: "Тендеры и ТКП"; iconName: "gavel"; active: shellRoot.page === "tenders"; onClicked: shellRoot.go("tenders"); visible: backend.hint("tender.read", backend.permRevision) }
                NavItem { text: "Проекты"; iconName: "folder-kanban"; active: shellRoot.page === "projects"; onClicked: shellRoot.go("projects") }
                NavItem { text: "Итоги проектов"; iconName: "wallet"; active: shellRoot.page === "finance"; onClicked: shellRoot.go("finance")
                          visible: (backend.me.roles || []).indexOf("accountant") >= 0 || backend.financeOverview.length > 0 }
                Text { visible: shellRoot.seesStock || shellRoot.seesNeeds; text: "СНАБЖЕНИЕ"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1.2; Layout.leftMargin: 10; Layout.topMargin: 14; Layout.bottomMargin: 4 }
                NavItem { objectName: "navStock"; text: "Склад"; iconName: "warehouse"; active: shellRoot.page === "stock"; onClicked: shellRoot.go("stock"); visible: shellRoot.seesStock }
                NavItem { objectName: "navProcurement"; text: "Закупка"; iconName: "shopping-cart"; active: shellRoot.page === "procurement"; onClicked: shellRoot.go("procurement"); visible: shellRoot.seesNeeds
                          badge: (backend.needsOverview.candidates || []).length; badgeTone: "warning" }
                Text { text: "СИСТЕМА"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1.2; Layout.leftMargin: 10; Layout.topMargin: 14; Layout.bottomMargin: 4 }
                NavItem { text: "Синхронизация"; iconName: "refresh-cw"; active: shellRoot.page === "sync"; onClicked: shellRoot.go("sync")
                          badge: backend.problemCount > 0 ? backend.problemCount : backend.pendingCount; badgeTone: backend.problemCount > 0 ? "danger" : "warning" }
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
                        Text { text: (backend.me.role_labels || []).join(", ") || "Нет ролей"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; elide: Text.ElideRight; Layout.fillWidth: true } }
                    VButton { kind: "ghost"; compact: true; iconName: Theme.dark ? "sun" : "moon"; Accessible.name: "Сменить тему"; onClicked: backend.theme = Theme.dark ? "light" : "dark" }
                    VButton { kind: "ghost"; compact: true; iconName: "log-out"; Accessible.name: "Выйти"; onClicked: backend.logout() }
                }
            }
        }

        Loader {
            Layout.fillWidth: true; Layout.fillHeight: true
            sourceComponent: ({ customers: customersC, tenders: tendersC, projects: projectsC, sync: syncC, admin: adminC, finance: financeC,
                               stock: stockC, procurement: procurementC })[shellRoot.page]
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

    FormDialog {
        id: reconnect
        title: "Подключение к серверу"
        subtitle: "Введите пароль, чтобы получить сеанс и отправить локальную очередь изменений (" + backend.pendingCount + ")."
        confirmText: "Подключиться"
        onConfirmed: { backend.reconnect(rpwd.text); rpwd.text = ""; close() }
        VField { id: rpwd; label: "Пароль"; password: true; onAccepted: reconnect.confirmed() }
    }
}
