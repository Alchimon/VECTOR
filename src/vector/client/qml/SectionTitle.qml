import QtQuick
import QtQuick.Layouts

RowLayout {
    property string text: ""
    property string iconName: ""
    spacing: 8
    Layout.fillWidth: true
    Icon { visible: iconName !== ""; name: iconName; size: 16; color: Theme.textMuted }
    Text { text: parent.text; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM; font.weight: Font.DemiBold; Layout.fillWidth: true }
}
