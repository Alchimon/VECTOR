import QtQuick
import QtQuick.Layouts

Item {
    id: host
    anchors.fill: parent
    ListModel { id: toasts }
    Connections {
        target: backend
        function onScreenChanged() { if (backend.screen !== "main") toasts.clear() }
        function onToast(kind, text) {
            for (let i = 0; i < toasts.count; ++i) if (toasts.get(i).text === text) toasts.remove(i--)
            toasts.append({ kind: kind, text: text })
            if (toasts.count > 4) toasts.remove(0)
        }
    }
    Column {
        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 20
        spacing: 8
        width: 380
        Repeater {
            model: toasts
            delegate: Rectangle {
                id: tt
                width: 380
                readonly property var t: Theme.tone(model.kind === "error" ? "danger" : model.kind)
                height: row.implicitHeight + 24
                radius: Theme.radius
                color: Theme.surface
                border.color: Theme.border
                Rectangle { width: 4; radius: 2; color: tt.t.fg; anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.margins: 8 }
                RowLayout {
                    id: row
                    anchors.fill: parent; anchors.margins: 12; anchors.leftMargin: 22
                    spacing: 10
                    Icon { name: model.kind === "success" ? "circle-check" : model.kind === "error" ? "circle-x" : model.kind === "warning" ? "triangle-alert" : "info"; color: tt.t.fg; size: 18; Layout.alignment: Qt.AlignTop }
                    Text { text: model.text; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Icon { name: "x"; size: 14; Layout.alignment: Qt.AlignTop
                        MouseArea { anchors.fill: parent; anchors.margins: -6; onClicked: toasts.remove(index); cursorShape: Qt.PointingHandCursor } }
                }
                Timer { interval: model.kind === "error" || model.kind === "warning" ? 9000 : 4500; running: true; onTriggered: if (index >= 0) toasts.remove(index) }
            }
        }
    }
}
