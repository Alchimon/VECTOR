import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Модальная форма: заголовок, пояснение, поля (default property), баннер ошибки, кнопки.
// Подсказки (D-37): при наведении на заголовок и пояснение — расшифровка сокращений в них; у кнопки подтверждения —
// пояснение действия из словаря подсказок (что произойдёт, нужна ли связь); confirmTip — своё пояснение.
Popup {
    id: d
    property string title: ""
    property string subtitle: ""
    property string confirmText: "Сохранить"
    property string confirmKind: "primary"
    property string confirmTip: ""
    property string error: ""
    property string note: ""
    property bool working: false
    property bool canConfirm: true
    // Текст второй кнопки: «Отмена» (по умолчанию) или своё — например «Позже» в окне «Начните рабочий день» (D-46);
    // нажатие сообщается сигналом dismissed (крестик и Esc просто закрывают форму — окно покажется снова позже).
    property string cancelText: "Отмена"
    signal dismissed()
    // Ширина формы: обычные формы — 520; формы с полями в две колонки (реквизиты поставщика) — шире.
    property int formWidth: 520
    default property alias content: body.data
    signal confirmed()
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(formWidth, (Overlay.overlay ? Overlay.overlay.width : 800) - 48)
    padding: 0
    closePolicy: Popup.CloseOnEscape
    onOpened: { error = ""; working = false }
    Overlay.modal: Rectangle { color: Theme.scrim }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 140 } NumberAnimation { property: "scale"; from: 0.97; to: 1; duration: 140; easing.type: Easing.OutCubic } }
    background: Rectangle { color: Theme.surface; radius: Theme.radiusL; border.color: Theme.border }

    contentItem: ColumnLayout {
        spacing: 0
        ColumnLayout {
            Layout.fillWidth: true; Layout.margins: Theme.padL; Layout.bottomMargin: 8
            spacing: 4
            RowLayout {
                Layout.fillWidth: true
                Text { text: d.title; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsL; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.Wrap
                       VTipArea { text: backend.tip(d.title) } }
                VButton { kind: "ghost"; iconName: "x"; compact: true; onClicked: d.close(); Accessible.name: "Закрыть" }
            }
            Text { visible: d.subtitle !== ""; text: d.subtitle; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true
                   VTipArea { text: backend.tip(d.subtitle) } }
        }
        ColumnLayout {
            id: body
            Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 8
            spacing: 14
        }
        Banner { tone: "info"; text: d.note; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 14; Layout.fillWidth: true }
        Banner { tone: "danger"; text: d.error; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 14; Layout.fillWidth: true }
        Rectangle { Layout.fillWidth: true; Layout.topMargin: Theme.padL; height: 1; color: Theme.border }
        RowLayout {
            Layout.fillWidth: true; Layout.margins: 16; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL
            spacing: 8
            Item { Layout.fillWidth: true }
            VButton { text: d.cancelText; kind: "ghost"; onClicked: { d.dismissed(); d.close() } }
            VButton { text: d.working ? "Выполняется…" : d.confirmText; kind: d.confirmKind; enabled: d.canConfirm && !d.working; onClicked: d.confirmed()
                      tip: d.confirmTip !== "" ? d.confirmTip : backend.tip(text) }
        }
    }
}
