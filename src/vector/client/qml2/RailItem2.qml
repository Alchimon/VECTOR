import QtQuick
import QtQuick.Controls.Basic
import "../qml"

// Кнопка узкой иконка-рейла слева (интерфейс 2.0, D-98): иконка + короткая подпись (в «Уютно»),
// активный раздел — мягкая подложка и полоска-индикатор слева. badge — счётчик (как в классике).
AbstractButton {
    id: r
    property string iconName: ""
    property bool active: false
    property int badge: 0
    property string badgeTone: "primary"
    property string tip: backend.tip(text)
    implicitWidth: Theme2.railW - 10
    implicitHeight: Theme2.compact ? 48 : 58
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
    contentItem: Column {
        spacing: Theme2.compact ? 1 : 3
        topPadding: Theme2.compact ? 7 : 9
        Item { width: 22; height: 22; anchors.horizontalCenter: parent.horizontalCenter
            Icon { anchors.centerIn: parent; name: r.iconName; size: Theme2.compact ? 18 : 20
                   color: r.active ? Theme2.primary : Theme2.textMuted }
            Rectangle {  // бейдж-счётчик
                visible: r.badge > 0
                anchors.right: parent.right; anchors.top: parent.top; anchors.rightMargin: -8; anchors.topMargin: -5
                implicitWidth: Math.max(16, badgeText.implicitWidth + 8); height: 16; radius: 8
                color: Theme2.tone(r.badgeTone).fg
                Text { id: badgeText; anchors.centerIn: parent; text: r.badge > 99 ? "99+" : r.badge
                       color: Theme2.tone(r.badgeTone).bg; font.family: Theme2.family
                       font.pixelSize: 10; font.weight: Font.Bold }
            }
        }
        Text { visible: !Theme2.compact; text: r.text; anchors.horizontalCenter: parent.horizontalCenter
               color: r.active ? Theme2.primary : Theme2.textMuted; font.family: Theme2.family
               font.pixelSize: 10; font.weight: r.active ? Font.DemiBold : Font.Normal
               width: r.width - 8; elide: Text.ElideRight; horizontalAlignment: Text.AlignHCenter }
    }
}
