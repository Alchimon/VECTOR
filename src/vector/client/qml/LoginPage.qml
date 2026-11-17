import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Экран входа: адрес сервера, логин, пароль; в «Дополнительно» — файл корневого сертификата (свой УЦ компании).
// submit() вызывает backend.login. Без связи вход открывает офлайн-профиль (если пользователь уже входил на этом ПК).
// Ошибка входа показывается из backend.loginError. Пароль нигде не сохраняется.
Item {
    id: page
    property bool advanced: backend.savedCa !== ""
    function submit() { backend.login(server.text, login.text, pwd.text, ca.text) }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // Бренд-панель: фоновая «чертёжная» композиция владельца (D-28) + логотип. Поверх фона — мягкая вуаль цвета
        // окна, чтобы текст и чипы читались на любом участке изображения.
        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: parent.width * 0.44
            color: Theme.bg
            clip: true
            Image {
                objectName: "loginBackdrop"
                anchors.fill: parent
                source: Theme.backdrop
                fillMode: Image.PreserveAspectCrop
                horizontalAlignment: Image.AlignLeft
                asynchronous: true; smooth: true; mipmap: true
            }
            Rectangle {
                anchors.fill: parent
                gradient: Gradient {
                    orientation: Gradient.Vertical
                    GradientStop { position: 0.0;  color: Qt.alpha(Theme.bg, 0.30) }
                    GradientStop { position: 0.45; color: Qt.alpha(Theme.bg, 0.78) }
                    GradientStop { position: 1.0;  color: Qt.alpha(Theme.bg, 0.92) }
                }
            }
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.border }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 56
                spacing: 18
                Image {
                    objectName: "loginLogo"
                    source: Theme.logo
                    Layout.preferredWidth: Math.min(400, parent.width)
                    Layout.preferredHeight: Layout.preferredWidth * sourceSize.height / Math.max(1, sourceSize.width)
                    fillMode: Image.PreserveAspectFit
                    smooth: true; mipmap: true
                    Accessible.name: "ВЕКТОР — единая платформа для инженеров и бизнеса"
                }
                Item { Layout.fillHeight: true }
                Text {
                    text: "От заказчика и тендера —\nдо проекта, комплекта\nи документа."
                    color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXxl; font.weight: Font.Bold; lineHeight: 1.15
                }
                Text {
                    Layout.maximumWidth: 440
                    text: "Единая цепочка инженерной компании: права проверяются сервером в момент действия, каждое изменение — в истории, работа продолжается без сети."
                    color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsM; wrapMode: Text.Wrap; lineHeight: 1.3
                }
                RowLayout {
                    spacing: 8; Layout.topMargin: 8
                    VChip { text: "Шифрование TLS"; tone: "primary"; iconName: "lock" }
                    VChip { text: "Офлайн-режим"; tone: "info"; iconName: "cloud-off" }
                    VChip { text: "Аудит"; tone: "success"; iconName: "shield-check" }
                }
                Item { Layout.fillHeight: true }
                Text { text: "Версия " + backend.version; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs }
            }
        }

        // Форма входа
        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
            VButton {
                anchors.top: parent.top; anchors.right: parent.right; anchors.margins: 20
                kind: "ghost"; iconName: Theme.dark ? "sun" : "moon"
                Accessible.name: "Сменить тему"
                onClicked: backend.theme = Theme.dark ? "light" : "dark"
            }
            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(400, parent.width - 64)
                spacing: 16
                Text { text: "Вход в систему"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold }
                Text { text: "Используйте учётную запись, выданную администратором ВЕКТОР."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                VField { id: server; label: "Адрес сервера"; placeholder: "https://vector.company.local:8443"; text: backend.savedServer; onAccepted: login.input.forceActiveFocus() }
                VField { id: login; label: "Логин"; placeholder: "i.ivanov"; text: backend.savedLogin; onAccepted: pwd.input.forceActiveFocus() }
                VField { id: pwd; label: "Пароль"; password: true; onAccepted: page.submit() }
                AbstractButton {
                    id: adv
                    implicitHeight: 22
                    onClicked: page.advanced = !page.advanced
                    contentItem: RowLayout {
                        spacing: 6
                        Icon { name: page.advanced ? "chevron-down" : "chevron-right"; size: 14 }
                        Text { text: "Сертификат корпоративного центра (CA)"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                    }
                }
                VField { id: ca; visible: page.advanced; label: "Файл сертификата CA (PEM)"; placeholder: "C:\\VECTOR\\ca.pem"; text: backend.savedCa
                         hint: "Нужен, если сертификат сервера выпущен внутренним центром компании и не установлен в Windows." }
                Banner { tone: "danger"; text: backend.loginError }
                VButton { text: backend.busy ? "Подключение…" : "Войти"; kind: "primary"; Layout.fillWidth: true; implicitHeight: 42; enabled: !backend.busy; onClicked: page.submit() }
                Text {
                    Layout.fillWidth: true
                    text: "Без связи с сервером можно открыть локальный кэш, если вы уже входили на этом компьютере за последние 7 дней."
                    color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap
                }
            }
        }
    }
    Component.onCompleted: (backend.savedLogin !== "" ? pwd : server).input.forceActiveFocus()
}
