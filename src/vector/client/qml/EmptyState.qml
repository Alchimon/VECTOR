import QtQuick
import QtQuick.Layouts

// Пустое состояние списка или карточки: иконка, title, пояснение text и необязательная кнопка
// (actionText → сигнал action()). Отличает «ещё ничего нет» от ошибки и от «нет доступа».
// Подсказка при наведении на текст (D-37) — расшифровка сокращений из заголовка и пояснения.
// В тёмной теме фон экосистемы (D-99) легально живёт и в Empty State — за вуалью цвета окна (≈0,82).
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
        clip: true
        Image { anchors.fill: parent; source: Theme.dark ? Theme.backdrop : ""
                fillMode: Image.PreserveAspectCrop; opacity: 0.9; asynchronous: true; smooth: true }
        Rectangle { anchors.fill: parent; color: Qt.alpha(Theme.bg, 0.82); visible: Theme.dark }
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
