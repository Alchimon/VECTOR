import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Раздел «Администрирование» (только admin; список пользователей приходит запросом users, право проверяет сервер).
// Слева — пользователи, справа — карточка: создать пользователя, выдать/отозвать глобальную роль, заблокировать/
// разблокировать, сбросить пароль (пользователь сменит его при входе), завершить все сеансы.
// reload() перечитывает список; команды формы помечаются тегом «adm-…», после успеха список обновляется.
Item {
    id: page
    property var users: []
    property string state: "loading"
    property string selectedId: ""
    readonly property var sel: users.find(u => u.id === selectedId) || null
    function reload() { state = "loading"; backend.loadUsers("admin-users") }
    Component.onCompleted: reload()
    Connections {
        target: backend
        function onQueryDone(t, r) {
            if (t !== "admin-users") return
            if (r.status === "accepted") { page.users = r.result; page.state = "ready" }
            else page.state = r.status === "denied" ? "denied" : "offline"
        }
        function onCommandDone(t, r) { if (t.indexOf("adm-") === 0 && r.status === "accepted") page.reload() }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 28
        spacing: 20
        PageHeader {
            title: "Администрирование"
            subtitle: "Учётные записи и роли доступа. Должность ≠ отдел ≠ роль доступа."
            VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: page.reload() }
            VButton { text: "Новый пользователь"; kind: "primary"; iconName: "user-plus"; enabled: backend.sessionMode === "online"; onClicked: createUser.open() }
        }
        Banner { visible: page.state === "offline"; tone: "warning"; text: "Администрирование требует связи с сервером." }
        Banner { visible: page.state === "denied"; tone: "danger"; text: "Раздел доступен только администратору." }

        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 20
            visible: page.state === "ready"
            VCard {
                Layout.fillWidth: true; Layout.fillHeight: true
                ColumnLayout { anchors.fill: parent; anchors.margins: 12; spacing: 0
                    Rectangle { Layout.fillWidth: true; implicitHeight: 34; color: Theme.surfaceAlt; radius: Theme.radiusS
                        RowLayout { anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 12
                            Text { text: "Пользователь"; Layout.preferredWidth: 240; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.Bold }
                            Text { text: "Роли доступа"; Layout.fillWidth: true; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.Bold }
                            Text { text: "Состояние"; Layout.preferredWidth: 170; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.Bold } } }
                    ListView {
                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                        model: page.users
                        ScrollBar.vertical: ScrollBar {}
                        delegate: AbstractButton {
                            width: ListView.view.width; implicitHeight: 58
                            hoverEnabled: true
                            onClicked: page.selectedId = modelData.id
                            background: Rectangle { color: modelData.id === page.selectedId ? Theme.primarySoft : parent.hovered ? Theme.hover : "transparent"; radius: Theme.radiusS
                                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border } }
                            contentItem: RowLayout { spacing: 12
                                Item { width: 2 }
                                Rectangle { width: 32; height: 32; radius: 16; color: modelData.active ? Theme.primarySoft : Theme.neutralSoft
                                    Text { anchors.centerIn: parent; text: modelData.display_name.substring(0, 1); color: modelData.active ? Theme.primary : Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.Bold } }
                                ColumnLayout { spacing: 0; Layout.preferredWidth: 196
                                    Text { text: modelData.display_name; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                                    Text { text: modelData.login; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs } }
                                Item { Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                    Row { anchors.verticalCenter: parent.verticalCenter; spacing: 4
                                        Repeater { model: modelData.role_labels; delegate: VChip { text: modelData; tone: modelData === "Администратор" ? "primary" : "neutral" } } }
                                    Text { visible: modelData.role_labels.length === 0; anchors.verticalCenter: parent.verticalCenter; text: "нет ролей"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs } }
                                RowLayout { Layout.preferredWidth: 170; spacing: 4
                                    VChip { text: modelData.active ? "Активен" : "Отключён"; tone: modelData.active ? "success" : "neutral" }
                                    VChip { visible: !!modelData.locked_until; text: "Блок."; tone: "danger"; iconName: "lock" }
                                    VChip { visible: modelData.must_change_password; text: "Врем. пароль"; tone: "warning" } }
                            }
                        }
                    }
                }
            }
            VCard {
                Layout.preferredWidth: 380; Layout.fillHeight: true
                EmptyState { anchors.centerIn: parent; visible: !page.sel; iconName: "user"; tone: "neutral"; title: "Выберите пользователя"; text: "" }
                ColumnLayout {
                    visible: !!page.sel
                    anchors.fill: parent; anchors.margins: 22; spacing: 14
                    Text { text: page.sel ? page.sel.display_name : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsL; font.weight: Font.Bold }
                    Text { text: page.sel ? "Логин: " + page.sel.login : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
                    SectionTitle { text: "Роли доступа"; iconName: "shield-check" }
                    Repeater {
                        model: page.sel ? page.sel.grants.filter(g => g.active) : []
                        delegate: RowLayout { Layout.fillWidth: true
                            VChip { text: modelData.label; tone: "primary" }
                            Text { text: modelData.valid_until ? "до " + modelData.valid_until.substring(0, 10) : "бессрочно"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.fillWidth: true }
                            VButton { text: "Отозвать"; compact: true; kind: "ghost"; onClicked: { act.cmdName = "admin.revoke_role"; act.extra = { grant_id: modelData.id }; act.title = "Отозвать роль «" + modelData.label + "»"; act.subtitle = "Согласования, выданные по этой роли, станут недействительными; повторная выдача их не восстановит."; act.confirmKind = "danger"; act.open() } } }
                    }
                    VButton { text: "Выдать роль"; kind: "soft"; iconName: "plus"; onClicked: grant.open() }
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                    SectionTitle { text: "Учётная запись"; iconName: "key-round" }
                    VButton { text: "Сбросить пароль"; iconName: "key-round"; Layout.fillWidth: true; onClicked: reset.open() }
                    VButton { text: "Завершить все сеансы"; iconName: "log-out"; Layout.fillWidth: true
                              onClicked: { act.cmdName = "admin.revoke_sessions"; act.extra = { user_id: page.sel.id }; act.title = "Завершить все сеансы"; act.subtitle = "Пользователю придётся войти заново."; act.confirmKind = "primary"; act.open() } }
                    VButton { text: page.sel && page.sel.active ? "Отключить учётную запись" : "Включить учётную запись"; kind: page.sel && page.sel.active ? "danger" : "soft"; Layout.fillWidth: true
                              iconName: page.sel && page.sel.active ? "lock" : "check"
                              onClicked: { act.cmdName = "admin.set_user_active"; act.extra = { user_id: page.sel.id, active: !page.sel.active }; act.title = page.sel.active ? "Отключить учётную запись" : "Включить учётную запись"; act.subtitle = page.sel.active ? "Все сеансы будут завершены. История и авторство сохраняются." : ""; act.confirmKind = page.sel.active ? "danger" : "primary"; act.open() } }
                    Item { Layout.fillHeight: true }
                }
            }
        }
    }

    FormDialog {
        id: createUser
        title: "Новый пользователь"
        subtitle: "Временный пароль передайте лично. При первом входе пользователь обязан его сменить."
        canConfirm: cuLogin.text.trim() !== "" && cuName.text.trim() !== "" && cuPwd.text.length >= 12
        onOpened: { cuLogin.text = ""; cuName.text = ""; cuPwd.text = "" }
        onConfirmed: cuCmd.run("admin.create_user", { login: cuLogin.text.trim(), display_name: cuName.text.trim(), initial_password: cuPwd.text })
        VField { id: cuName; label: "Имя для отображения"; required: true; placeholder: "Иванов Иван" }
        VField { id: cuLogin; label: "Логин"; required: true; placeholder: "i.ivanov"; hint: "Латиница, цифры, точка, дефис, подчёркивание." }
        VField { id: cuPwd; label: "Временный пароль"; required: true; password: true; hint: "Не короче 12 символов." }
    }
    Cmd { id: cuCmd; dialog: createUser; tag: "adm-create" }

    FormDialog {
        id: grant
        title: "Выдать роль доступа"
        subtitle: page.sel ? page.sel.display_name : ""
        confirmText: "Выдать"
        canConfirm: gReason.text.trim() !== "" && gUntil.error === ""
        onOpened: { gReason.text = ""; gUntil.text = "" }
        onConfirmed: gCmd.run("admin.grant_role", { user_id: page.sel.id, role: gRole.currentValue, reason: gReason.text.trim(), valid_until: gUntil.text.trim() ? gUntil.text.trim() + "T23:59:59.000000Z" : null })
        VSelect { id: gRole; label: "Роль"; model: backend.roleOptions }
        VField { id: gUntil; label: "Действует до (необязательно)"; placeholder: "ГГГГ-ММ-ДД"; error: text !== "" && !/^\d{4}-\d{2}-\d{2}$/.test(text.trim()) ? "Формат даты: ГГГГ-ММ-ДД." : ""; hint: "Для замещения на период отпуска и т. п." }
        VField { id: gReason; label: "Основание"; required: true; placeholder: "Приказ № …" }
    }
    Cmd { id: gCmd; dialog: grant; tag: "adm-grant" }

    FormDialog {
        id: reset
        title: "Сбросить пароль"
        subtitle: "Все сеансы пользователя будут завершены; при входе потребуется сменить временный пароль. Несинхронизированные изменения на его компьютере станут недоступны."
        confirmText: "Сбросить"; confirmKind: "danger"
        canConfirm: rPwd.text.length >= 12 && rReason.text.trim() !== ""
        onOpened: { rPwd.text = ""; rReason.text = "" }
        onConfirmed: rCmd.run("admin.reset_password", { user_id: page.sel.id, temporary_password: rPwd.text, reason: rReason.text.trim() })
        VField { id: rPwd; label: "Временный пароль"; password: true; required: true }
        VField { id: rReason; label: "Основание"; required: true; placeholder: "Обращение пользователя, заявка №…" }
    }
    Cmd { id: rCmd; dialog: reset; tag: "adm-reset" }

    FormDialog {
        id: act
        property string cmdName: ""
        property var extra: ({})
        confirmText: "Подтвердить"
        canConfirm: aReason.text.trim() !== ""
        onOpened: aReason.text = ""
        onConfirmed: { var p = Object.assign({}, extra); p.reason = aReason.text.trim(); aCmd.run(cmdName, p) }
        VField { id: aReason; label: "Основание"; required: true }
    }
    Cmd { id: aCmd; dialog: act; tag: "adm-act" }
}
