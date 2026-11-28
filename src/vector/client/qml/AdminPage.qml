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
    property string tab: "users"
    // Настройки компании (D-26, A-26) приходят снимком pull — видны и без загрузки списка пользователей.
    readonly property var sv: backend.settingsOverview
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
            subtitle: page.tab === "users" ? "Учётные записи и роли доступа. Должность ≠ отдел ≠ роль доступа."
                                           : "Правила работы компании. Каждое изменение — новая версия с причиной; откат — публикация прежнего значения."
            VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: { page.reload(); backend.syncNow() } }
            VButton { visible: page.tab === "users"; text: "Новый пользователь"; kind: "primary"; iconName: "user-plus"; enabled: backend.sessionMode === "online"; onClicked: createUser.open() }
        }
        VSegmented {
            objectName: "adminTabs"
            current: page.tab
            onPicked: id => page.tab = id
            model: [{ id: "users", label: "Пользователи", icon: "users" },
                    { id: "settings", label: "Настройки компании", icon: "sliders-horizontal" }]
        }
        Banner { visible: page.tab === "users" && page.state === "offline"; tone: "warning"; text: "Администрирование требует связи с сервером." }
        Banner { visible: page.tab === "users" && page.state === "denied"; tone: "danger"; text: "Раздел доступен только администратору." }

        // ================================================================ Настройки компании
        Banner { visible: page.tab === "settings" && page.sv.can_publish !== true; tone: "info"
                 text: "Публиковать настройки может администратор при связи с сервером. Здесь видно, какие правила действуют сейчас." }
        ListView {
            objectName: "settingsList"
            visible: page.tab === "settings"
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 12
            model: page.tab === "settings" ? (page.sv.items || []) : []
            ScrollBar.vertical: ScrollBar {}
            delegate: VCard {
                width: ListView.view.width; implicitHeight: scol.implicitHeight + 36
                ColumnLayout {
                    id: scol
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; spacing: 6
                    RowLayout { Layout.fillWidth: true; spacing: 10
                        Text { text: modelData.label_ru; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM; font.weight: Font.Bold; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        VChip { text: modelData.state_label; tone: modelData.state_tone }
                        VButton { objectName: "btnPublish"; visible: page.sv.can_publish === true; text: "Изменить"; compact: true; kind: "soft"; iconName: "pencil"
                                  onClicked: pub.openFor(modelData) } }
                    Text { text: "Действует: " + modelData.value_label; color: Theme.primary; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
                    Text { text: modelData.description_ru || ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Text { visible: modelData.is_set === true; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true
                           text: "Версия " + modelData.version + " · " + (modelData.set_by || "") + " · " + (modelData.set_at || "").substring(0, 16).replace("T", " ") + " UTC · причина: " + (modelData.reason || "") }
                    Text { visible: !!modelData.decision; text: "Решение: " + (modelData.decision || ""); color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 20
            visible: page.tab === "users" && page.state === "ready"
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
                    SectionTitle { text: "Кадровые категории"; iconName: "id-card" }
                    Text { text: "Допуск к сверхурочной работе и работе в выходные/праздники (ст. 99, 113 ТК РФ). Запрещающая категория блокирует подачу и согласование заявок."
                           color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    ColumnLayout {
                        id: hrCatList
                        Layout.fillWidth: true; spacing: 4
                        Repeater {
                            model: backend.hrCategoryCatalog
                            delegate: ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true; spacing: 0
                                CheckBox {
                                    property string code: modelData.code
                                    objectName: "hrCat_" + modelData.code
                                    text: modelData.label_ru
                                    checked: page.sel ? (page.sel.hr_categories || []).indexOf(modelData.code) >= 0 : false
                                }
                                Text { text: modelData.article; color: Theme.textFaint; font.family: Theme.family
                                       font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true
                                       leftPadding: 30 }
                            }
                        }
                    }
                    VButton { objectName: "hrCatSave"; text: "Сохранить категории"; iconName: "check"; kind: "soft"
                        tip: "Правка версионируется, требует основания; сотрудник получит уведомление. Действует на новые заявки и на решения по ожидающим."
                        onClicked: { hrForm.open() } }
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
        DateField { id: gUntil; label: "Действует до (необязательно)"; hint: "Для замещения на период отпуска и т. п." }
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

    // Кадровые категории ТК-контура (D-74): отмеченные коды + обязательное основание; сервер версионирует запись.
    FormDialog {
        id: hrForm
        title: "Кадровые категории"
        subtitle: page.sel ? page.sel.display_name + " — допуск к работе вне графика (ст. 99, 113 ТК РФ)" : ""
        confirmText: "Сохранить категории"
        canConfirm: hrReason.text.trim() !== ""
        onOpened: hrReason.text = ""
        onConfirmed: {
            var codes = []
            for (var i = 0; i < hrCatList.children.length; i++) {
                var cell = hrCatList.children[i]
                var box = cell.children && cell.children.length > 0 ? cell.children[0] : null
                if (box && box.code !== undefined && box.checked) codes.push(box.code)
            }
            hrCmd.run("hr.category_set", { user_id: page.sel.id, categories: codes, reason: hrReason.text.trim() },
                      (page.sel.hr_categories_version || 0))
        }
        VField { id: hrReason; label: "Основание"; required: true; placeholder: "Приказ № …, заявление работника от …" }
    }
    Cmd { id: hrCmd; dialog: hrForm; tag: "adm-hr-cat"
        onDone: function(r) { if (r.status === "accepted") page.reload() } }

    // Публикация настройки: новое значение + причина. Версия — та, что видел администратор (0 — не задавалась);
    // если её успели изменить, сервер вернёт конфликт и форма покажет причину.
    FormDialog {
        id: pub
        objectName: "publishForm"
        property var item: null
            title: item ? item.label_ru : ""
        subtitle: item ? (item.is_set ? "Сейчас: " + item.value_label + " (версия " + item.version + ")." : "Не задано — действует самое строгое: " + item.value_label + ".") : ""
        confirmText: "Опубликовать"
        canConfirm: pValue.currentValue !== "" && pReason.text.trim() !== "" && (!item || !item.is_set || pValue.currentValue !== item.value)
        function openFor(it) { item = it; pReason.text = ""; pValue.currentIndex = page.indexOfOpt(it.options, it.value); open() }
        onConfirmed: { working = true; error = ""; backend.publishSetting("adm-config", item.key, pValue.currentValue, pReason.text.trim(), item.version) }
        VSelect { id: pValue; objectName: "pValue"; label: "Значение"; model: pub.item ? pub.item.options : [] }
        // Варианты, которые описаны, но ещё не подключены (например, КЭП, D-31): видно, почему их нет в списке.
        Text { visible: pub.item !== null && (pub.item.unavailable || []).length > 0
               text: pub.item ? "Пока недоступно: " + (pub.item.unavailable || []).join("; ") : ""
               color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }
        VField { id: pReason; objectName: "pReason"; label: "Причина изменения"; required: true; placeholder: "Решение совещания склада и закупки от …" }
    }
    function indexOfOpt(options, id) { for (var i = 0; i < options.length; i++) if (options[i].id === id) return i; return 0 }
    Connections {
        target: backend
        function onCommandDone(t, r) {
            if (t !== "adm-config") return
            pub.working = false
            if (r.status === "accepted") pub.close()
            else pub.error = r.message_ru || "Не опубликовано."
        }
    }
}
