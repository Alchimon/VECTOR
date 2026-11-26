import QtQuick
import QtQuick.Controls.Basic
import "../qml"

// Таб раздела ленты (интерфейс 2.0, D-98): «Главная», «Проект», «Склад»… Активный — подчёркнут
// фирменным цветом (как в Office). Переключает набор групп действий ленты, не меняя текущую страницу.
AbstractButton {
    id: t
    property bool active: false
    implicitHeight: Theme2.ribbonTabsH
    implicitWidth: label.implicitWidth + 26
    hoverEnabled: true
    Accessible.name: text
    contentItem: Text {
        id: label
        text: t.text
        color: t.active ? Theme2.primary : t.hovered ? Theme2.text : Theme2.textMuted
        font.family: Theme2.family; font.pixelSize: Theme2.fsS
        font.weight: t.active ? Font.DemiBold : Font.Normal
        anchors.centerIn: parent
    }
    background: Rectangle {
        color: t.hovered && !t.active ? Theme2.hover : "transparent"
        radius: Theme2.radiusS
        Rectangle { visible: t.active; anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right
                    anchors.leftMargin: 8; anchors.rightMargin: 8; height: 2; radius: 1; color: Theme2.primary }
    }
}
