import QtQuick
import QtQuick.Controls.Basic
import "../qml"

// Кнопка узкой иконка-рейла слева (интерфейс 2.0, D-98/D-101): только иконка, название — в тултипе
// (как в утверждённом концепте B). Активный раздел — мягкая подложка и полоска-индикатор слева.
// badge — счётчик (как в классике).
AbstractButton {
    id: r
    property string iconName: ""
    property bool active: false
    property int badge: 0
    property string badgeTone: "primary"
    property string tip: r.text + (backend.tip(r.text) !== "" ? "\n" + backend.tip(r.text) : "")
    implicitWidth: Theme2.railW - 12
    implicitHeight: Theme2.compact ? 38 : 42
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: text
    Accessible.description: tip
    VToolTip { visible: r.hovered && r.tip !== ""; text: r.tip }

    background: Rectangle {
        radius: Theme2.radiusS
        color: r.active ? Theme2.primarySoft : r.hovered ? Theme2.hover : "transparent"
        Rectangle {  // индикатор активного раздела — полоска слева
            visible: r.active
            anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
            width: 3; height: parent.height * 0.55; radius: 2; color: Theme2.primary
        }
        border.width: r.visualFocus ? 2 : 0; border.color: Qt.alpha(Theme2.focus, 0.6)
    }
    contentItem: Item {
        Icon { anchors.centerIn: parent; name: r.iconName; size: Theme2.compact ? 18 : 20
               color: r.active ? Theme2.primary : Theme2.textMuted }
        Rectangle {  // бейдж-счётчик
            visible: r.badge > 0
            anchors.right: parent.right; anchors.top: parent.top; anchors.rightMargin: 2; anchors.topMargin: 1
            implicitWidth: Math.max(15, badgeText.implicitWidth + 7); height: 15; radius: 8
            color: Theme2.tone(r.badgeTone).fg
            Text { id: badgeText; anchors.centerIn: parent; text: r.badge > 99 ? "99+" : r.badge
                   color: Theme2.tone(r.badgeTone).bg; font.family: Theme2.family
                   font.pixelSize: 10; font.weight: Font.Bold }
        }
    }
}
