import QtQuick
import QtQuick.Layouts

// Переключатель вкладок (сегменты): model — [{id, label, icon, tip?}], current — id выбранной.
// Подсказка вкладки при наведении (D-37) — modelData.tip, иначе из словаря подсказок по названию вкладки.
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
                id: segDel
                required property var modelData
                readonly property bool on: seg.current === modelData.id
                objectName: "seg-" + modelData.id
                width: segIn.implicitWidth + 22; height: 28; radius: 8
                color: on ? Theme.surface : "transparent"
                border.color: on ? Theme.border : (activeFocus ? Theme.focus : "transparent")
                readonly property string tipText: modelData.tip || backend.tip(modelData.label)
                Accessible.role: Accessible.PageTab; Accessible.name: modelData.label; Accessible.description: tipText
                activeFocusOnTab: true
                Keys.onSpacePressed: seg.picked(modelData.id)
                Keys.onReturnPressed: seg.picked(modelData.id)
                RowLayout { id: segIn; anchors.centerIn: parent; spacing: 6
                    Icon { name: modelData.icon; size: 14; color: on ? Theme.primary : Theme.textMuted }
                    Text { text: modelData.label; font.family: Theme.family; font.pixelSize: Theme.fsS
                           font.weight: on ? Font.DemiBold : Font.Normal
                           color: on ? Theme.text : Theme.textMuted } }
                MouseArea { id: segMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: seg.picked(modelData.id) }
                VToolTip { visible: segMouse.containsMouse && segDel.tipText !== ""; text: segDel.tipText }
            }
        }
    }
}
