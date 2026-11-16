import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ColumnLayout {
    id: s
    property string label: ""
    property var model: []
    property string textRole: "label"
    property string valueRole: "id"
    property alias currentIndex: box.currentIndex
    property string hint: ""
    readonly property var currentValue: box.currentIndex >= 0 && model.length > box.currentIndex ? model[box.currentIndex][valueRole] : ""
    spacing: 6
    Layout.fillWidth: true
    Text { visible: s.label !== ""; text: s.label; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.Medium }
    ComboBox {
        id: box
        Layout.fillWidth: true
        implicitHeight: 38
        model: s.model
        textRole: s.textRole
        font.family: Theme.family; font.pixelSize: Theme.fs
        Accessible.name: s.label
        contentItem: Text { leftPadding: 12; text: box.displayText; color: Theme.text; font: box.font; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        indicator: Icon { name: "chevron-down"; size: 16; x: box.width - width - 12; anchors.verticalCenter: parent.verticalCenter }
        background: Rectangle { radius: Theme.radius - 2; color: Theme.surface; border.width: box.activeFocus ? 2 : 1; border.color: box.activeFocus ? Theme.primary : Theme.border }
        delegate: ItemDelegate {
            width: box.width
            highlighted: box.highlightedIndex === index
            contentItem: Text { text: modelData[s.textRole]; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs }
            background: Rectangle { color: highlighted ? Theme.hover : Theme.surface }
        }
        popup.background: Rectangle { color: Theme.surface; border.color: Theme.border; radius: Theme.radius - 2 }
    }
    Text { visible: s.hint !== ""; text: s.hint; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }
}
