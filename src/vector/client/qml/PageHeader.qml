import QtQuick
import QtQuick.Layouts

// Заголовок страницы: title и необязательный subtitle (пояснение под заголовком).
RowLayout {
    id: h
    property string title: ""
    property string subtitle: ""
    default property alias actions: act.data
    Layout.fillWidth: true
    spacing: 12
    ColumnLayout {
        spacing: 2; Layout.fillWidth: true
        Text { text: h.title; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold }
        // Длинное пояснение сокращается многоточием, а не раздвигает страницу шире окна (кнопки справа важнее).
        Text { visible: h.subtitle !== ""; text: h.subtitle; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs
               elide: Text.ElideRight; Layout.fillWidth: true; Layout.preferredWidth: 0 }
    }
    RowLayout { id: act; spacing: 8 }
}
