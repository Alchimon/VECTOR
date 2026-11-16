import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Строка списка: заголовок, подзаголовок, справа — чип статуса и метка синхронизации.
AbstractButton {
    id: r
    property string title: ""
    property string subtitle: ""
    property string chipText: ""
    property string chipTone: "neutral"
    property bool pending: false
    property bool selected: false
    property string iconName: ""
    implicitHeight: 62
    width: ListView.view ? ListView.view.width : 300
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: title
    background: Rectangle {
        radius: Theme.radius
        color: r.selected ? Theme.primarySoft : r.hovered ? Theme.hover : "transparent"
        border.width: r.visualFocus ? 2 : 0; border.color: Qt.alpha(Theme.focus, 0.6)
    }
    contentItem: RowLayout {
        spacing: 12
        Rectangle {
            visible: r.iconName !== ""
            width: 34; height: 34; radius: 9
            color: r.selected ? Theme.surface : Theme.surfaceAlt
            Icon { anchors.centerIn: parent; name: r.iconName; size: 17; color: r.selected ? Theme.primary : Theme.textMuted }
        }
        ColumnLayout {
            spacing: 3; Layout.fillWidth: true
            Text { text: r.title; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { visible: r.subtitle !== ""; text: r.subtitle; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; elide: Text.ElideRight; Layout.fillWidth: true }
        }
        VChip { visible: r.pending; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
        VChip { visible: r.chipText !== ""; text: r.chipText; tone: r.chipTone; maxWidth: Math.round(r.width * 0.34)
            ToolTip.visible: hov.hovered && naturalWidth > width; ToolTip.text: r.chipText
            HoverHandler { id: hov } }
    }
}
