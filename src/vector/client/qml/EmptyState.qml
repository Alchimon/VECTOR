import QtQuick
import QtQuick.Layouts

// Пустое состояние списка или карточки: иконка, title, пояснение text и необязательная кнопка
// (actionText → сигнал action()). Отличает «ещё ничего нет» от ошибки и от «нет доступа».
// Подсказка при наведении на текст (D-37) — расшифровка сокращений из заголовка и пояснения.
// Фирменный фон здесь сознательно НЕ используем: в кружке 56px «космос» превращается в шум за иконкой;
// место фона по дизайн-системе (D-99) — бренд-панель входа и splash.
ColumnLayout {
    id: e
    property string iconName: "inbox"
    property string title: ""
    property string text: ""
    property string actionText: ""
    property string tone: "primary"
    signal action()
    spacing: 10
    Rectangle {
        Layout.alignment: Qt.AlignHCenter
        width: 56; height: 56; radius: 28
        color: Theme.tone(e.tone).bg
        Icon { anchors.centerIn: parent; name: e.iconName; size: 24; color: Theme.tone(e.tone).fg }
    }
    Text { Layout.alignment: Qt.AlignHCenter; text: e.title; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM; font.weight: Font.DemiBold }
    Text {
        Layout.alignment: Qt.AlignHCenter; Layout.maximumWidth: 360
        text: e.text; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
        VTipArea { text: backend.tip(e.title + " " + e.text) }
    }
    VButton { Layout.alignment: Qt.AlignHCenter; visible: e.actionText !== ""; text: e.actionText; kind: "soft"; iconName: "plus"; onClicked: e.action() }
}
