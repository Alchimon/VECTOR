import QtQuick
import QtQuick.Controls.Basic
import "../qml"

// Большая кнопка ленты команд (интерфейс 2.0, D-98): иконка сверху, подпись снизу — как в Office/EPLAN.
// Видна всегда, когда действие доступно по правам-подсказкам (окончательно право проверяет сервер);
// недоступное — бледное, с подсказкой-причиной.
AbstractButton {
    id: a
    property string iconName: ""
    property string tip: backend.tip(text)
    property bool warning: false
    // Ширина подписи фиксированная (не зависит от ширины кнопки) — иначе цикл раскладки Column ↔ Text.
    readonly property int labelW: Theme2.compact ? 66 : 92
    implicitWidth: labelW + 16
    implicitHeight: Theme2.ribbonH - Theme2.ribbonTabsH - 16
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: text
    Accessible.description: tip
    VToolTip { visible: a.hovered && a.tip !== ""; text: a.tip }

    background: Rectangle {
        radius: Theme2.radiusS
        color: !a.enabled ? "transparent" : a.down ? Theme2.primarySoft : a.hovered ? Theme2.hover : "transparent"
        border.width: a.visualFocus ? 2 : 0; border.color: Qt.alpha(Theme2.focus, 0.6)
    }
    contentItem: Column {
        spacing: Theme2.compact ? 2 : 3
        topPadding: Theme2.compact ? 3 : 5
        width: a.width
        Icon { anchors.horizontalCenter: parent.horizontalCenter; name: a.iconName
               size: Theme2.compact ? 16 : 18
               color: !a.enabled ? Theme2.textFaint : a.warning ? Theme2.warning : Theme2.primary }
        Text { text: a.text; anchors.horizontalCenter: parent.horizontalCenter
               color: a.enabled ? Theme2.text : Theme2.textFaint
               font.family: Theme2.family; font.pixelSize: Theme2.fsXs
               width: a.labelW; elide: Text.ElideRight; horizontalAlignment: Text.AlignHCenter
               maximumLineCount: 2; wrapMode: Text.WordWrap; lineHeight: 0.95 }
    }
}
