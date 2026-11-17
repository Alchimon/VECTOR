import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Обязательная смена пароля (первый вход или после сброса администратором): текущий пароль, новый (не короче 12
// символов) и повтор. При успехе другие сеансы пользователя завершаются, ключ офлайн-профиля перешифровывается.
Item {
    id: page
    readonly property string mismatch: rep.text !== "" && rep.text !== nw.text ? "Пароли не совпадают." : ""
    readonly property string weak: nw.text !== "" && nw.text.length < 12 ? "Не короче 12 символов." : ""
    Rectangle { anchors.fill: parent; color: Theme.bg }
    VCard {
        anchors.centerIn: parent
        width: 460; height: col.implicitHeight + 64
        ColumnLayout {
            id: col
            anchors.fill: parent; anchors.margins: 32
            spacing: 16
            Rectangle { width: 48; height: 48; radius: 24; color: Theme.warningSoft
                Icon { anchors.centerIn: parent; name: "key-round"; size: 22; color: Theme.warning } }
            Text { text: "Смените временный пароль"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold }
            Text { Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs
                   text: "Пароль выдан администратором. До смены пароля работа в системе недоступна. Другие сеансы будут завершены." }
            VField { id: cur; label: "Текущий (временный) пароль"; password: true }
            VField { id: nw; label: "Новый пароль"; password: true; error: page.weak
                     hint: "Не короче 12 символов, без логина, не из списка распространённых. Хорошо — фраза из нескольких слов." }
            VField { id: rep; label: "Повторите новый пароль"; password: true; error: page.mismatch; onAccepted: go.clicked() }
            RowLayout {
                Layout.fillWidth: true
                VButton { text: "Выйти"; kind: "ghost"; iconName: "log-out"; onClicked: backend.logout() }
                Item { Layout.fillWidth: true }
                VButton { id: go; text: "Сменить пароль"; kind: "primary"
                          enabled: cur.text !== "" && nw.text.length >= 12 && rep.text === nw.text && !backend.busy
                          onClicked: backend.changePassword(cur.text, nw.text) }
            }
        }
    }
}
