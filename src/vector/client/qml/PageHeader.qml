import QtQuick
import QtQuick.Layouts

// Заголовок страницы: title и необязательный subtitle (пояснение под заголовком).
// Подсказка (D-37): при наведении на заголовок — пояснение раздела из словаря; на пояснение — его полный текст
// (если сокращён многоточием) и расшифровка сокращений.
RowLayout {
    id: h
    property string title: ""
    property string subtitle: ""
    property string tip: backend.tip(title)
    default property alias actions: act.data
    Layout.fillWidth: true
    spacing: 12
    ColumnLayout {
        spacing: 2; Layout.fillWidth: true
        Text { text: h.title; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold
               VTipArea { text: h.tip } }
        // Длинное пояснение сокращается многоточием, а не раздвигает страницу шире окна (кнопки справа важнее).
        Text { visible: h.subtitle !== ""; text: h.subtitle; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs
               elide: Text.ElideRight; Layout.fillWidth: true; Layout.preferredWidth: 0
               VTipArea { readonly property string ab: backend.tip(h.subtitle)
                          text: parent.truncated ? h.subtitle + (ab !== "" ? "\n" + ab : "") : ab } }
    }
    RowLayout { id: act; spacing: 8 }
}
