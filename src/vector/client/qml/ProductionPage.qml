import QtQuick

// Раздел «Производство» (этап 5, сценарий С5 шаги 1–6; D-36): три вкладки —
//  * «Мои операции» (MyOpsPane.qml) — исполнителю: старт / пауза / продолжить / завершить / проблема, таймер;
//    полностью без связи (события — в очередь, время — часы этого компьютера, A-44);
//  * «Наряды» (WorkOrdersPane.qml) — РП проекта (мастер, A-42) и руководителю отдела: выпуск по утверждённому ТП,
//    снимок, исполнители, замены, материалы, закрытие; технологу, ГИП и складу — просмотр (и замена — ГИП/технолог);
//  * «Техпроцессы» (TechProcessPane.qml) — технологу: черновик ТП (можно без связи), утверждение (не автор).
// Вкладка по умолчанию — первая доступная (исполнителю — «Мои операции»). Переход из уведомления
// (shell.go("production", id)) открывает наряд или ТП с этим id.
Item {
    id: page
    objectName: "productionPage"
    property var shell
    readonly property var pv: backend.productionOverview
    readonly property string target: shell && shell.navTarget.page === "production" ? (shell.navTarget.id || "") : ""
    readonly property bool targetIsTp: target !== "" && (pv.tps || []).some(t => t.id === target)
    property string tab: target !== "" ? (targetIsTp ? "tps" : "orders") : (pv.default_tab || "orders")

    MyOpsPane { anchors.fill: parent; visible: page.tab === "my"; shell: page.shell
                tabs: page.pv.tabs || []; tab: page.tab; onTabPicked: id => page.tab = id }
    WorkOrdersPane { anchors.fill: parent; visible: page.tab === "orders"; shell: page.shell
                     selectedId: page.targetIsTp ? "" : page.target
                     tabs: (page.pv.tabs || []).length > 1 ? page.pv.tabs : []; tab: page.tab; onTabPicked: id => page.tab = id }
    TechProcessPane { anchors.fill: parent; visible: page.tab === "tps"; shell: page.shell
                      selectedId: page.targetIsTp ? page.target : ""
                      tabs: page.pv.tabs || []; tab: page.tab; onTabPicked: id => page.tab = id }
}
