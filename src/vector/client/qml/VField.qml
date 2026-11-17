import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Поле ввода с подписью: label, placeholder, hint (подсказка), error (текст ошибки — поле подсвечивается),
// required — звёздочка у подписи, password — скрытый ввод с кнопкой «показать», readOnly.
// text и input — доступ к введённому тексту и самому TextField; accepted() — нажат Enter.
ColumnLayout {
    id: f
    property alias text: input.text
    property alias input: input
    property string label: ""
    property string placeholder: ""
    property string hint: ""
    property string error: ""
    property bool required: false
    property bool password: false
    property bool readOnly: false
    property bool showPassword: false
    signal accepted()
    spacing: 6
    Layout.fillWidth: true

    Text {
        visible: f.label !== ""
        text: f.label + (f.required ? "  *" : "")
        color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.Medium
    }
    TextField {
        id: input
        Layout.fillWidth: true
        implicitHeight: 38
        placeholderText: f.placeholder
        placeholderTextColor: Theme.textFaint
        color: Theme.text
        readOnly: f.readOnly
        selectByMouse: true
        font.family: Theme.family; font.pixelSize: Theme.fs
        echoMode: f.password && !f.showPassword ? TextInput.Password : TextInput.Normal
        rightPadding: f.password ? 38 : 12
        leftPadding: 12
        Accessible.name: f.label
        onAccepted: f.accepted()
        background: Rectangle {
            radius: Theme.radius - 2
            color: f.readOnly ? Theme.surfaceAlt : Theme.surface
            border.width: input.activeFocus ? 2 : 1
            border.color: f.error !== "" ? Theme.danger : input.activeFocus ? Theme.primary : Theme.border
        }
        Icon {
            visible: f.password
            name: f.showPassword ? "eye-off" : "eye"
            size: 16
            anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
            MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: f.showPassword = !f.showPassword }
        }
    }
    Text {
        visible: f.error !== "" || f.hint !== ""
        text: f.error !== "" ? f.error : f.hint
        color: f.error !== "" ? Theme.danger : Theme.textFaint
        font.family: Theme.family; font.pixelSize: Theme.fsXs
        wrapMode: Text.Wrap; Layout.fillWidth: true
    }
}
