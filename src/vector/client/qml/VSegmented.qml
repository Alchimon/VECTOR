import QtQuick
import QtQuick.Layouts

// Переключатель вкладок (сегменты): model — [{id, label, icon}], current — id выбранной.
// Доступен с клавиатуры: Tab — к сегменту, Пробел/Enter — выбрать.
Rectangle {
    id: seg
    property var model: []
    property string current: ""
    signal picked(string id)
    implicitWidth: segRow.implicitWidth + 6; implicitHeight: 34; radius: 10
    color: Theme.surfaceAlt; border.color: Theme.border
    Row {
        id: segRow; anchors.centerIn: parent; spacing: 2
        Repeater {
            model: seg.model
            delegate: Rectangle {
                required property var modelData
                readonly property bool on: seg.current === modelData.id
                objectName: "seg-" + modelData.id
                width: segIn.implicitWidth + 22; height: 28; radius: 8
                color: on ? Theme.surface : "transparent"
                border.color: on ? Theme.border : (activeFocus ? Theme.focus : "transparent")
                Accessible.role: Accessible.PageTab; Accessible.name: modelData.label
                activeFocusOnTab: true
                Keys.onSpacePressed: seg.picked(modelData.id)
                Keys.onReturnPressed: seg.picked(modelData.id)
                RowLayout { id: segIn; anchors.centerIn: parent; spacing: 6
                    Icon { name: modelData.icon; size: 14; color: on ? Theme.primary : Theme.textMuted }
                    Text { text: modelData.label; font.family: Theme.family; font.pixelSize: Theme.fsS
                           font.weight: on ? Font.DemiBold : Font.Normal
                           color: on ? Theme.text : Theme.textMuted } }
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: seg.picked(modelData.id) }
            }
        }
    }
}
