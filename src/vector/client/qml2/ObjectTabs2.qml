import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../qml"

// Вкладки открытых объектов (интерфейс 2.0, D-98) — как в браузере/VS Code: каждый открытый проект,
// комплект или документ — вкладка; переключение мгновенное, без потери контекста. Модель — у оболочки
// (Shell2.openTabs): {key, title, icon, closable}. Первая вкладка — раздел (не закрывается).
Rectangle {
    id: tabs
    objectName: "objectTabs"
    property var model: []               // [{key, title, icon, closable, pinned}]
    property string activeKey: ""
    signal activated(string key)
    signal closeRequested(string key)
    // Контекстное меню вкладки (D-137): массовые закрытия, закрепление и шифр — логика у оболочки (Shell2).
    signal closeOthersRequested(string key)
    signal closeRightRequested(string key)
    signal closeAllRequested()
    signal pinToggled(string key)
    signal copyCodeRequested(string key)
    signal copyLinkRequested(string key)

    color: Theme2.sidebar
    implicitHeight: Theme2.tabsH
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme2.border }

    ListView {
        id: lv
        anchors { fill: parent; leftMargin: Theme2.gap; rightMargin: Theme2.gap }
        orientation: ListView.Horizontal
        spacing: 4
        clip: true
        model: tabs.model
        delegate: AbstractButton {
            id: tab
            readonly property bool active: modelData.key === tabs.activeKey
            implicitHeight: Theme2.tabsH - 8
            implicitWidth: Math.min(trow.implicitWidth + 22, Theme2.compact ? 200 : 260)
            anchors.verticalCenter: parent.verticalCenter
            hoverEnabled: true
            Accessible.name: "Вкладка " + modelData.title
            // D-120: средняя кнопка закрывает вкладку (как в браузере), левая — активирует;
            // правая — контекстное меню вкладки (D-137).
            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.MiddleButton
                onClicked: if (modelData.closable === true) tabs.closeRequested(modelData.key)
            }
            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.RightButton
                onClicked: tabMenu.openFor(modelData.key)
            }
            onClicked: tabs.activated(modelData.key)
            background: Rectangle {
                radius: Theme2.radiusS
                color: tab.active ? Theme2.surface : tab.hovered ? Theme2.hover : "transparent"
                border.color: tab.active ? Theme2.border : "transparent"
                Rectangle { visible: tab.active; anchors.top: parent.top; anchors.horizontalCenter: parent.horizontalCenter
                            width: parent.width - 16; height: 2; radius: 1; color: Theme2.primary }
            }
            contentItem: RowLayout {
                id: trow
                spacing: 8
                Icon { Layout.leftMargin: 10; name: modelData.icon || "file-text"; size: 15
                       color: tab.active ? Theme2.primary : Theme2.textMuted }
                Text { text: modelData.title; color: tab.active ? Theme2.text : Theme2.textMuted
                       font.family: Theme2.mono; font.pixelSize: Theme2.fsS
                       font.weight: tab.active ? Font.DemiBold : Font.Normal
                       Layout.fillWidth: true; elide: Text.ElideRight }
                // Метка закреплённой вкладки (D-137): массовые закрытия её не трогают.
                Icon { visible: modelData.pinned === true; name: "bookmark"; size: 12; color: Theme2.primary
                       Accessible.name: "Вкладка закреплена" }
                AbstractButton {
                    visible: modelData.closable === true
                    Layout.rightMargin: 8
                    implicitWidth: 20; implicitHeight: 20
                    hoverEnabled: true
                    Accessible.name: "Закрыть вкладку " + modelData.title
                    onClicked: tabs.closeRequested(modelData.key)
                    contentItem: Icon { anchors.centerIn: parent; name: "x"; size: 12
                                        color: parent.hovered ? Theme2.danger : Theme2.textFaint }
                    background: Rectangle { radius: 6; color: parent.hovered ? Theme2.dangerSoft : "transparent" }
                }
            }
        }
    }

    // Контекстное меню вкладки (D-137): правый щелчок по вкладке. Действия — сигналами оболочке.
    Menu {
        id: tabMenu
        objectName: "tabMenu"
        property string key: ""
        readonly property var tabObj: {
            var t = tabs.model.filter(x => x.key === tabMenu.key)
            return t.length > 0 ? t[0] : null
        }
        function openFor(key) { tabMenu.key = key; tabMenu.popup() }
        MenuItem { objectName: "tabMenuClose"; text: "Закрыть"; enabled: tabMenu.tabObj !== null && tabMenu.tabObj.closable === true && tabMenu.tabObj.pinned !== true
                   onTriggered: tabs.closeRequested(tabMenu.key) }
        MenuItem { objectName: "tabMenuCloseOthers"; text: "Закрыть другие"
                   enabled: tabMenu.tabObj !== null && tabs.model.some(x => x.key !== tabMenu.key && x.closable === true && x.pinned !== true)
                   onTriggered: tabs.closeOthersRequested(tabMenu.key) }
        MenuItem { objectName: "tabMenuCloseRight"; text: "Закрыть справа"
                   onTriggered: tabs.closeRightRequested(tabMenu.key) }
        MenuItem { objectName: "tabMenuCloseAll"; text: "Закрыть все"
                   enabled: tabs.model.some(x => x.closable === true && x.pinned !== true)
                   onTriggered: tabs.closeAllRequested() }
        MenuSeparator {}
        MenuItem { objectName: "tabMenuPin"; text: tabMenu.tabObj !== null && tabMenu.tabObj.pinned === true ? "Открепить вкладку" : "Закрепить вкладку"
                   enabled: tabMenu.tabObj !== null && tabMenu.tabObj.closable === true
                   onTriggered: tabs.pinToggled(tabMenu.key) }
        MenuItem { objectName: "tabMenuCopyCode"; text: "Копировать шифр"
                   onTriggered: tabs.copyCodeRequested(tabMenu.key) }
        // Внутренняя ссылка vector://… (P3-1): для объектных вкладок (проект/комплект/документ).
        MenuItem { objectName: "tabMenuCopyLink"; text: "Копировать ссылку"
                   enabled: tabMenu.tabObj !== null && ["project", "kit", "document"].indexOf(tabMenu.tabObj.kind) >= 0
                   onTriggered: tabs.copyLinkRequested(tabMenu.key) }
    }
}
