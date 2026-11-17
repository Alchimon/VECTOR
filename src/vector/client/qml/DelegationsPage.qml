import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Раздел «Замещения» (D-34, ответ владельца на A-37: «все и вся должны быть заменяемыми»; отсутствие человека не
// должно делать невозможным решить вопрос или передать его).
// * Любой сотрудник передаёт СВОЮ роль на срок (отпуск, командировка) — «Передать мою роль».
// * Руководитель отдела и администратор оформляют замещение ЗА отсутствующего (заболел, не может сам) и видят
//   «покрытие ролей»: роли, которые сейчас у одного человека или ни у кого.
// * Замещающий действует ролью носителя; в журнале решений видно «замещает такого-то».
// * Роль в проекте (ГИП, РП, участник) тоже передаётся (D-35, A-40) — пункт «ГИП в проекте П-…» в списке ролей;
//   действует только в этом проекте. Ключ пункта «gip@<id проекта>» раскладывается на role и project_id.
// Данные — снимок pull (dv = backend.delegationsOverview); оформить и отменить — только при связи (решает сервер).
Item {
    id: page
    objectName: "delegationsPage"
    property var shell: null
    readonly property var dv: backend.delegationsOverview

    // Карточка одного замещения: роль, кто → кому, срок, состояние, причина, «оформил за …», «Отменить».
    component DelegationCard: VCard {
        id: card
        property var d: ({})
        Layout.fillWidth: true
        implicitHeight: dcol.implicitHeight + 32
        ColumnLayout {
            id: dcol
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 16; spacing: 6
            RowLayout { Layout.fillWidth: true; spacing: 10
                Icon { name: "user-check"; color: Theme.primary }
                Text { text: card.d.role_label || ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM; font.weight: Font.Bold }
                VChip { text: card.d.state_label || ""; tone: card.d.tone || "neutral" }
                Item { Layout.fillWidth: true }
                VButton { objectName: "btnRevokeDelegation"; visible: card.d.can_revoke === true; text: "Отменить"; compact: true; kind: "ghost"; iconName: "x"
                          onClicked: revoke.openFor(card.d) } }
            Text { text: (card.d.from_name || "") + "  →  " + (card.d.to_name || ""); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
            RowLayout { spacing: 6
                Icon { name: "calendar-clock"; size: 14 }
                Text { text: card.d.period || ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS } }
            Text { text: "Причина: " + (card.d.reason || ""); color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
            Text { visible: card.d.on_behalf === true; text: "Оформил(а) за сотрудника: " + (card.d.created_name || "")
                   color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs }
            Text { visible: !!card.d.revoked_at; text: "Отменено: " + (card.d.revoke_reason || ""); color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }
        }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 28
        spacing: 16
        PageHeader {
            title: "Замещения"
            subtitle: "Передайте роль на время отсутствия: замещающий решает вашей ролью, в журнале видно «замещает»."
            VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() }
            VButton { objectName: "btnDelegateForOther"; visible: page.dv.manage === true; text: "За сотрудника"; iconName: "users"
                      enabled: page.dv.can_manage === true; onClicked: create.openFor(true, "", "") }
            VButton { objectName: "btnDelegateOwn"; text: "Передать мою роль"; kind: "primary"; iconName: "user-check"
                      enabled: page.dv.can_create === true; onClicked: create.openFor(false, "", "") }
        }
        Banner { visible: page.dv.online !== true; tone: "warning"
                 text: "Оформить и отменить замещение можно только при связи с сервером. Ниже — состояние на момент последнего обновления." }
        Banner { visible: page.dv.online === true && (page.dv.role_options || []).length === 0 && page.dv.manage !== true; tone: "info"
                 text: "У вас нет своих ролей, которые можно передать. Если вы замещаете коллегу — это видно ниже." }

        Flickable {
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            contentHeight: body.implicitHeight; boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            ColumnLayout {
                id: body
                width: parent.width; spacing: 12

                SectionTitle { text: "Я замещаю"; iconName: "user-check" }
                Text { visible: (page.dv.received || []).length === 0; text: "Сейчас вы никого не замещаете."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
                Repeater { model: page.dv.received || []; delegate: DelegationCard { d: modelData } }

                SectionTitle { text: "Меня замещают"; iconName: "users"; Layout.topMargin: 8 }
                Text { visible: (page.dv.given || []).length === 0; text: "Замещений ваших ролей нет. Уходите в отпуск — передайте роль, чтобы работа не стояла."
                       color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Repeater { model: page.dv.given || []; delegate: DelegationCard { d: modelData } }

                // Покрытие ролей — руководителю отдела и администратору: где нельзя никого подменить (после своих замещений:
                // их немного, а список покрытия на небольшой компании длинный).
                SectionTitle { visible: (page.dv.coverage || []).length > 0; text: "Покрытие ролей — заменить некем"; iconName: "triangle-alert"; Layout.topMargin: 8 }
                Repeater {
                    model: page.dv.coverage || []
                    delegate: VCard {
                        objectName: "coverageRow"
                        Layout.fillWidth: true; implicitHeight: crow.implicitHeight + 28
                        RowLayout { id: crow; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.margins: 16; spacing: 12
                            VChip { text: modelData.state === "none" ? "Никого" : "Один"; tone: modelData.tone }
                            ColumnLayout { Layout.fillWidth: true; spacing: 2
                                Text { text: modelData.label + (modelData.holder_names.length ? " — " + modelData.holder_names.join(", ") : "")
                                       color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                Text { text: modelData.text; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
                            VButton { visible: modelData.state === "single" && modelData.delegable && page.dv.can_manage === true
                                      && modelData.holder_id !== backend.me.user_id
                                      text: "Оформить замещение"; compact: true; kind: "soft"
                                      onClicked: create.openFor(true, modelData.holder_id, modelData.role_key) } }
                    }
                }

                SectionTitle { visible: page.dv.manage === true; text: "Все замещения отдела"; iconName: "list-checks"; Layout.topMargin: 8 }
                Text { visible: page.dv.manage === true && (page.dv.others || []).length === 0; text: "Других замещений нет."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
                Repeater { model: page.dv.manage === true ? (page.dv.others || []) : []; delegate: DelegationCard { d: modelData } }
                Item { implicitHeight: 8 }
            }
        }
    }

    // Оформить замещение: своей роли (forOther=false) или за отсутствующего (forOther=true, руководитель/админ).
    FormDialog {
        id: create
        objectName: "delegationForm"
        property bool forOther: false
        title: forOther ? "Замещение за сотрудника" : "Передать мою роль на время"
        subtitle: forOther ? "Когда сотрудник отсутствует и не может оформить сам. Назначить замещающим себя нельзя."
                           : "Замещающий получит вашу роль на указанные дни; его решения записываются «за вас». Вы роль не теряете."
        confirmText: "Оформить"
        note: "Роль «Администратор» не передаётся. Роль в проекте действует только в этом проекте. Замещение заканчивается само в последний день срока; отменить можно раньше."
        canConfirm: dTo.currentValue !== "" && dRole.currentValue !== "" && (!forOther || dFrom.currentValue !== "")
                    && /^\d{4}-\d{2}-\d{2}$/.test(dUntil.text.trim()) && dFromDay.error === "" && dReason.text.trim() !== ""
        function idx(list, id) { for (var i = 0; i < list.length; i++) if (list[i].id === id) return i; return id ? -1 : 0 }
        function openFor(other, holder, role) {
            forOther = other
            dFrom.currentIndex = Math.max(0, idx(page.dv.people_options || [], holder))
            dRole.currentIndex = Math.max(0, idx(dRole.model, role))
            dTo.currentIndex = -1
            dFromDay.text = ""; dUntil.text = page.dv.default_until || ""; dReason.text = ""
            open()
        }
        // «gip@<id проекта>» → роль в проекте; без «@» — глобальная роль (project_id "" не передаётся).
        onConfirmed: {
            var key = String(dRole.currentValue || ""), at = key.indexOf("@")
            dCmd.run("delegation.create", { from_user_id: forOther ? dFrom.currentValue : "", to_user_id: dTo.currentValue,
                                            role: at < 0 ? key : key.substring(0, at), project_id: at < 0 ? "" : key.substring(at + 1),
                                            valid_from: dFromDay.text.trim(), valid_until: dUntil.text.trim(), reason: dReason.text.trim() })
        }
        VSelect { id: dFrom; objectName: "dFrom"; visible: create.forOther; label: "Чью роль (отсутствующий сотрудник)"; model: page.dv.people_options || [] }
        VSelect { id: dRole; objectName: "dRole"; label: "Роль"; model: create.forOther ? (page.dv.all_role_options || []) : (page.dv.role_options || []) }
        VSelect { id: dTo; objectName: "dTo"; label: "Замещающий"; model: page.dv.people_options || [] }
        RowLayout { Layout.fillWidth: true; spacing: 12
            VField { id: dFromDay; label: "С (необязательно)"; placeholder: "сегодня"
                     error: text !== "" && !/^\d{4}-\d{2}-\d{2}$/.test(text.trim()) ? "Формат: ГГГГ-ММ-ДД." : "" }
            VField { id: dUntil; objectName: "dUntil"; label: "По (включительно)"; required: true; placeholder: "ГГГГ-ММ-ДД" } }
        VField { id: dReason; objectName: "dReason"; label: "Причина"; required: true; placeholder: "Отпуск, приказ № …" }
    }
    Cmd { id: dCmd; dialog: create; tag: "deleg-create" }

    FormDialog {
        id: revoke
        property var d: null
        title: "Отменить замещение"
        subtitle: d ? d.role_label + ": " + d.from_name + " → " + d.to_name + " (" + d.period + ")" : ""
        confirmText: "Отменить замещение"; confirmKind: "danger"
        canConfirm: rvReason.text.trim() !== ""
        function openFor(x) { d = x; rvReason.text = ""; open() }
        onConfirmed: rvCmd.run("delegation.revoke", { id: d.id, reason: rvReason.text.trim() })
        VField { id: rvReason; label: "Причина"; required: true; placeholder: "Вышел из отпуска раньше" }
    }
    Cmd { id: rvCmd; dialog: revoke; tag: "deleg-revoke" }
}
