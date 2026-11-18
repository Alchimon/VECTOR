import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Выпадающий список с подписью: model — массив объектов, textRole/valueRole — их поля для текста и значения,
// currentValue — выбранное значение (пустая строка, если не выбрано), hint — подсказка под полем.
// tip — подсказка при наведении (D-37): по умолчанию из словаря по подписи, если пусто — hint; к ней добавляется
// расшифровка сокращений в выбранном значении (например, «ТП АТХ ред. 1»).
ColumnLayout {
    id: s
    property string label: ""
    property var model: []
    property string textRole: "label"
    property string valueRole: "id"
    property alias currentIndex: box.currentIndex
    property string hint: ""
    property string tip: backend.tip(label)
    readonly property string tipText: {
        var own = tip !== "" ? tip : hint
        var ab = backend.tip(box.displayText)
        return own !== "" && ab !== "" ? own + "\n" + ab : own + ab
    }
    readonly property var currentValue: box.currentIndex >= 0 && model.length > box.currentIndex ? model[box.currentIndex][valueRole] : ""
    spacing: 6
    Layout.fillWidth: true
    Text { visible: s.label !== ""; text: s.label; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.Medium
           VTipArea { text: s.tipText } }
    ComboBox {
        id: box
        Layout.fillWidth: true
        implicitHeight: 38
        model: s.model
        textRole: s.textRole
        font.family: Theme.family; font.pixelSize: Theme.fs
        Accessible.name: s.label
        Accessible.description: s.tipText
        hoverEnabled: true
        VToolTip { visible: box.hovered && !box.popup.visible && s.tipText !== ""; text: s.tipText }
        contentItem: Text { leftPadding: 12; rightPadding: 36; text: box.displayText; color: Theme.text; font: box.font; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
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
