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
    property var model: []               // [{key, title, icon, closable}]
    property string activeKey: ""
    signal activated(string key)
    signal closeRequested(string key)

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
}
