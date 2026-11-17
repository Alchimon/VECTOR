import QtQuick

// Раздел «Закупка» (сценарий С4): три вкладки —
//  * «Потребности» (NeedsPane.qml) — что нужно по зафиксированным расчётам и чем это покрыто (шаги 1–2);
//  * «Заказы поставщикам» (OrdersPane.qml) — черновик → размещён → закрыт, приём поставки складом (шаг 5, D-32);
//  * «Поставщики» (SuppliersPane.qml) — справочник (A-34).
// Каждая вкладка — полноценный экран «список + карточка»; раздел хранит только выбранную вкладку.
// Вкладка по умолчанию — «Потребности»; кто потребностей не видит (бухгалтерия) — начинает с заказов.
Item {
    id: page
    property var shell
    readonly property bool seesNeeds: (backend.needsOverview.needs || []).length > 0 || (backend.me.roles || []).some(r => r === "procurement" || r === "warehouse")
    readonly property bool seesOrders: (backend.me.roles || []).some(r => r === "procurement" || r === "warehouse" || r === "accountant")
    property string tab: seesNeeds ? "needs" : "orders"
    readonly property var tabsModel: [].concat(
        seesNeeds ? [{ id: "needs", label: "Потребности", icon: "shopping-cart" }] : [],
        seesOrders ? [{ id: "orders", label: "Заказы поставщикам" + ((backend.ordersOverview.counts || {}).waiting ? " (" + backend.ordersOverview.counts.waiting + ")" : ""), icon: "clipboard-list" },
                      { id: "suppliers", label: "Поставщики", icon: "factory" }] : [])

    NeedsPane { anchors.fill: parent; visible: page.tab === "needs"; shell: page.shell
                tabs: page.tabsModel.length > 1 ? page.tabsModel : []; tab: page.tab; onTabPicked: id => page.tab = id }
    OrdersPane { anchors.fill: parent; visible: page.tab === "orders"; shell: page.shell
                 tabs: page.tabsModel; tab: page.tab; onTabPicked: id => page.tab = id }
    SuppliersPane { anchors.fill: parent; visible: page.tab === "suppliers"; shell: page.shell
                    tabs: page.tabsModel; tab: page.tab; onTabPicked: id => page.tab = id }
}
