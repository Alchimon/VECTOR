import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    property bool advanced: backend.savedCa !== ""
    function submit() { backend.login(server.text, login.text, pwd.text, ca.text) }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // Бренд-панель
        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: parent.width * 0.44
            gradient: Gradient {
                orientation: Gradient.Vertical
                GradientStop { position: 0.0; color: Theme.dark ? "#1B2140" : "#E9ECFF" }
                GradientStop { position: 1.0; color: Theme.dark ? "#241B3D" : "#F3EEFF" }
            }
            // Декоративная сетка «чертёжной» бумаги
            Canvas {
                anchors.fill: parent
                opacity: Theme.dark ? 0.10 : 0.35
                onPaint: {
                    var ctx = getContext("2d"); ctx.clearRect(0, 0, width, height)
                    ctx.strokeStyle = Theme.dark ? "#8EA2FF" : "#C9D0F5"; ctx.lineWidth = 1
                    for (var x = 0; x < width; x += 28) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke() }
                    for (var y = 0; y < height; y += 28) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke() }
                }
            }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 56
                spacing: 18
                RowLayout {
                    spacing: 12
                    Rectangle {
                        width: 44; height: 44; radius: 12; color: Theme.primary
                        Icon { anchors.centerIn: parent; name: "layers"; size: 24; color: Theme.textOnPrimary }
                    }
                    ColumnLayout {
                        spacing: 0
                        Text { text: "ВЕКТОР"; color: Theme.text; font.family: Theme.family; font.pixelSize: 22; font.weight: Font.Black; font.letterSpacing: 2 }
                        Text { text: "VECTOR"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.letterSpacing: 3 }
                    }
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
