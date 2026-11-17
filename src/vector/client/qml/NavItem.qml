import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Пункт левого меню: text, iconName, active — выбранный раздел; badge > 0 — счётчик (например, ожидающие
// операции), badgeTone — его цвет. Доступен с клавиатуры (Tab, Пробел/Enter).
AbstractButton {
    id: n
    property string iconName: ""
    property bool active: false
    property int badge: 0
    property string badgeTone: "primary"
    implicitHeight: 38
    Layout.fillWidth: true
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: text
    background: Rectangle {
        radius: Theme.radius - 2
        color: n.active ? Theme.primarySoft : n.hovered ? Theme.hover : "transparent"
        border.width: n.visualFocus ? 2 : 0; border.color: Qt.alpha(Theme.focus, 0.6)
    }
    contentItem: RowLayout {
        spacing: 10
        Item { width: 2 }
        Icon { name: n.iconName; size: 18; color: n.active ? Theme.primary : Theme.textMuted }
        Text {
            text: n.text; Layout.fillWidth: true
            color: n.active ? Theme.primary : Theme.text
            font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: n.active ? Font.DemiBold : Font.Medium
        }
        Rectangle {
            visible: n.badge > 0
            implicitWidth: Math.max(20, bt.implicitWidth + 10); implicitHeight: 20; radius: 10
            color: Theme.tone(n.badgeTone).bg
            Text { id: bt; anchors.centerIn: parent; text: n.badge; color: Theme.tone(n.badgeTone).fg; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.Bold }
        }
        Item { width: 4 }
    }
}
