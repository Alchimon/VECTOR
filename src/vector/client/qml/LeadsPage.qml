import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Раздел «Лиды» (D-65, этап 2 blueprint 52): воронка возможностей продаж до тендера.
// Лид регистрирует тендерный отдел (с площадки) или менеджер (личный канал); квалифицированный лид с указанным
// заказчиком переводится в тендер одной командой (создаётся тендер, связь с лидом сохраняется).
// Отклонение — всегда с причиной. Снимок pull: читается и без связи; действия — онлайн (право — ГИП/РП/руководитель
// отдела, проверяет сервер).
ColumnLayout {
    id: page
    property var shell
    spacing: 10
    readonly property var v: backend.opportunitiesView(backend.dataRevision)
    readonly property var rows: v.rows || []
    readonly property var counts: v.counts || ({})

    PageHeader {
        title: "Лиды"
        subtitle: "Возможности продаж до тендера  ·  " + (counts.open || 0) + " в работе · " + (counts.total || 0) + " всего"
        actions: [
            VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() },
            VButton { text: "Новый лид"; kind: "primary"; iconName: "plus"
                      visible: backend.hint("opportunity.create", backend.permRevision)
                      enabled: page.v.can_manage === true; onClicked: leadDialog.ask() }
        ]
    }

    EmptyState {
        visible: page.rows.length === 0
        Layout.fillWidth: true; Layout.fillHeight: true
        iconName: "sparkles"; title: "Лидов пока нет"
        text: "Зарегистрируйте возможность продажи — от площадки, менеджера или входящего обращения. Квалифицированный лид переводится в тендер одной кнопкой."
    }

    Repeater {
        model: page.rows
        delegate: VCard {
            id: leadCard
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: leadCol.implicitHeight + 24
            ColumnLayout {
                id: leadCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
                spacing: 5
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Text { text: leadCard.modelData.title; color: Theme.text; font.pixelSize: Theme.fsM
                           font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                    VChip { text: leadCard.modelData.source_label; tone: "neutral" }
                    VChip { text: leadCard.modelData.status_label; tone: leadCard.modelData.tone }
                }
                Text { text: leadCard.modelData.customer_label
                             + (leadCard.modelData.expected_on !== "" ? " · ожидается " + leadCard.modelData.expected_on : "")
                             + " · ответственный: " + leadCard.modelData.owner_name
                       color: Theme.textMuted; font.pixelSize: Theme.fsS }
                Text { visible: leadCard.modelData.note !== ""; text: leadCard.modelData.note
                       color: Theme.textMuted; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Flow {
                    Layout.fillWidth: true; spacing: 6
                    Repeater {
                        model: leadCard.modelData.actions
                        delegate: VButton {
                            required property var modelData
                            text: modelData.label; kind: modelData.danger ? "danger" : "ghost"; compact: true
                            onClicked: {
                                if (modelData.kind === "convert") convertDialog.setup(leadCard.modelData)
                                else leadTransDialog.setup(leadCard.modelData, modelData)
                            }
                        }
                    }
                    VButton { visible: !leadCard.modelData.terminal && page.v.can_manage === true
                              text: "Изменить"; kind: "ghost"; compact: true; iconName: "pencil"
                              onClicked: leadDialog.askEdit(leadCard.modelData) }
                }
            }
        }
    }

    Cmd { id: leadCmd; dialog: leadDialog }
    Cmd { id: leadTransCmd; dialog: leadTransDialog }
    Cmd { id: convertCmd; dialog: convertDialog }

    FormDialog {
        id: leadDialog
        property var editing: null
        title: editing ? "Лид — правка" : "Новый лид"
        subtitle: "Источник: площадка (тендерный отдел), личный канал менеджера, входящее обращение или другое."
        confirmText: editing ? "Сохранить" : "Создать"
        canConfirm: titleF.text.trim() !== ""
        function ask() { editing = null; error = ""; titleF.text = ""; srcSel.currentIndex = 0; srcDetailF.text = ""
                         custSel.currentIndex = 0; custNameF.text = ""; expF.text = ""; noteF.text = ""; open() }
        function askEdit(o) {
            editing = o; error = ""
            titleF.text = o.title; srcDetailF.text = o.source_detail
            var opts = [{ id: "", label: "— не указан —" }]
            var cs = backend.customers
            for (var i = 0; i < cs.length; i++) opts.push({ id: cs[i].id, label: cs[i].name })
            custSel.model = opts
            custSel.currentIndex = 0
            for (var j = 0; j < opts.length; j++) if (opts[j].id === o.customer_id) custSel.currentIndex = j
            custNameF.text = o.customer_id === "" ? o.customer_label : ""
            expF.text = o.expected_on; noteF.text = o.note
            open()
        }
        function ask2() { /* инициализация списка заказчиков при создании */
            var opts = [{ id: "", label: "— не указан —" }]
            var cs = backend.customers
            for (var i = 0; i < cs.length; i++) opts.push({ id: cs[i].id, label: cs[i].name })
            custSel.model = opts
        }
        onOpened: if (!editing) ask2()
        onConfirmed: {
            var cid = custSel.currentValue && custSel.currentValue.id !== "" ? custSel.currentValue.id : null
            if (editing) {
                leadCmd.run("opportunity.update", { id: editing.id, title: titleF.text.trim(),
                            source_detail: srcDetailF.text.trim() !== "" ? srcDetailF.text.trim() : null,
                            customer_id: cid,
                            customer_name: cid === null && custNameF.text.trim() !== "" ? custNameF.text.trim() : null,
                            expected_on: expF.text.trim() !== "" ? expF.text.trim() : null,
                            note: noteF.text.trim() !== "" ? noteF.text.trim() : null }, editing.version)
            } else {
                leadCmd.run("opportunity.create", { id: backend.newId(), title: titleF.text.trim(),
                            source: srcSel.model[srcSel.currentIndex].id,
                            source_detail: srcDetailF.text.trim() !== "" ? srcDetailF.text.trim() : null,
                            customer_id: cid,
                            customer_name: cid === null && custNameF.text.trim() !== "" ? custNameF.text.trim() : null,
                            expected_on: expF.text.trim() !== "" ? expF.text.trim() : null,
                            note: noteF.text.trim() !== "" ? noteF.text.trim() : null })
            }
        }
        VField { id: titleF; label: "Название"; required: true; placeholder: "Модернизация АСУ ТП для …" }
        VSelect { id: srcSel; label: "Источник"; model: page.v.sources || [] }
        VField { id: srcDetailF; label: "Детали источника"; hint: "Например, название площадки или контакт" }
        VSelect { id: custSel; label: "Заказчик (из справочника)" }
        VField { id: custNameF; label: "Заказчик (текстом)"; visible: custSel.currentIndex === 0
                 hint: "Если заказчика нет в справочнике — название текстом; перед переводом в тендер выберите из справочника" }
        DateField { id: expF; label: "Ожидаемая дата решения" }
        VField { id: noteF; label: "Примечание" }
    }

    FormDialog {
        id: leadTransDialog
        property var lead: null
        property var act: null
        title: lead ? lead.title : ""
        subtitle: act ? "Переход: «" + act.label + "»" : ""
        confirmText: "Выполнить"
        confirmKind: act && act.danger ? "danger" : "primary"
        canConfirm: !act || !act.reason || leadReason.text.trim() !== ""
        function setup(lead, act) { this.lead = lead; this.act = act; error = ""; leadReason.text = ""; open() }
        onConfirmed: leadTransCmd.run("opportunity.transition", { id: leadTransDialog.lead.id,
                                      to: leadTransDialog.act.to,
                                      reason: leadReason.text.trim() !== "" ? leadReason.text.trim() : null },
                                      leadTransDialog.lead.version)
        VField { id: leadReason; label: "Причина"; required: leadTransDialog.act && leadTransDialog.act.reason
                 hint: "Обязательна при отклонении — попадёт в историю лида" }
    }

    FormDialog {
        id: convertDialog
        property var lead: null
        title: "Перевести лид в тендер"
        subtitle: lead ? "«" + lead.title + "»: будет создан тендер, ответственный — ответственный лида; связь сохраняется." : ""
        confirmText: "Создать тендер"
        function setup(lead) { this.lead = lead; error = ""; titleC.text = lead.title; deadlineC.text = ""; open() }
        onConfirmed: convertCmd.run("opportunity.convert_to_tender", { id: convertDialog.lead.id,
                                    tender_id: backend.newId(),
                                    title: titleC.text.trim() !== "" ? titleC.text.trim() : null,
                                    submission_deadline: deadlineC.text.trim() !== "" ? deadlineC.text.trim() : null },
                                    convertDialog.lead.version)
        VField { id: titleC; label: "Название тендера"; hint: "По умолчанию — название лида" }
        DateField { id: deadlineC; label: "Срок подачи заявки" }
    }
}
