import QtQuick
import QtQuick.Layouts

// Строка «подпись — значение» в карточке объекта (k, v); muted — приглушённое значение (например, «не указано»).
// tip — подсказка при наведении на подпись (D-37): по умолчанию из словаря (пояснение поля, расшифровка ИНН, НДС…).
RowLayout {
    id: kv
    property string tip: backend.tip(k)
    property string k: ""
    property string v: ""
    property bool muted: false
    spacing: 12
    Layout.fillWidth: true
    Text { text: k; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; Layout.preferredWidth: 150; Layout.alignment: Qt.AlignTop
           VTipArea { text: kv.tip } }
    Text {
        text: v === "" ? "—" : v
        color: (muted || v === "") ? Theme.textFaint : Theme.text
        font.family: Theme.family; font.pixelSize: Theme.fs
        wrapMode: Text.Wrap; Layout.fillWidth: true
    }
}
