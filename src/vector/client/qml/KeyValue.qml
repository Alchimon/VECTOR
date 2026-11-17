import QtQuick
import QtQuick.Layouts

// Строка «подпись — значение» в карточке объекта (k, v); muted — приглушённое значение (например, «не указано»).
RowLayout {
    property string k: ""
    property string v: ""
    property bool muted: false
    spacing: 12
    Layout.fillWidth: true
    Text { text: k; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; Layout.preferredWidth: 150; Layout.alignment: Qt.AlignTop }
    Text {
        text: v === "" ? "—" : v
        color: (muted || v === "") ? Theme.textFaint : Theme.text
        font.family: Theme.family; font.pixelSize: Theme.fs
        wrapMode: Text.Wrap; Layout.fillWidth: true
    }
}
