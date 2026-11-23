import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

// Вкладка «РКД» комплекта (D-64): бизнес-ревизии документов с проверкой и замечаниями, профили шифров,
// трансмитталы заказчику с ZIP-пакетом.
//
// Как это устроено:
//  * Всё видно из снимка pull (meta): читается и без связи; устаревание подсвечено общим баннером снимка.
//  * Все действия — онлайн-команды (сервер перепроверяет состояние в момент эффекта и права по роли); кнопки
//    здесь — подсказка по роли из кэша назначений, при отказе сервер пришлёт понятное сообщение.
//  * Файл ревизии — из уже загруженных версий файла документа; пакет трансмиттала скачивается ZIP'ом и
//    сохраняется только после сверки контрольной суммы.
ColumnLayout {
    id: kr
    property var kit: null
    readonly property var v: kit ? backend.kitRkd(kit.id, backend.dataRevision) : ({})
    readonly property var revs: v.revisions || []
    readonly property var transmittals: v.transmittals || []
    readonly property var profiles: v.profiles || []
    readonly property var profileHint: v.profile_hint || ({})
    readonly property var counts: v.counts || ({})
    readonly property var docOptions: v.doc_options || []
    spacing: 10

    // ---------- сегменты: Ревизии / Трансмитталы / Профили
    VSegmented {
        model: [
            { id: "revisions", label: "Ревизии" + (counts.revisions ? " · " + counts.revisions : ""), icon: "file-check" },
            { id: "transmittals", label: "Трансмитталы" + (counts.transmittals ? " · " + counts.transmittals : ""), icon: "send" },
            { id: "profiles", label: "Профили шифров" + (counts.profiles ? " · " + counts.profiles : ""), icon: "tag" }
        ]
        current: kr.seg
        onPicked: function(sid) { kr.seg = sid }
    }
    property string seg: "revisions"

    // ================================ РЕВИЗИИ
    ColumnLayout {
        visible: kr.seg === "revisions"
        Layout.fillWidth: true
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: (counts.open_comments || 0) > 0
                      ? "Открытых замечаний: " + counts.open_comments
                      : "Ревизии документов комплекта — проверка, выдача, согласование заказчиком"
                color: Theme.textMuted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap
            }
            VButton { text: "Новая ревизия"; iconName: "plus"; enabled: kr.docOptions.length > 0 && backend.online
                      onClicked: revDialog.ask() }
        }

        EmptyState {
            visible: kr.revs.length === 0
            Layout.fillWidth: true
            iconName: "file-check"; title: "Ревизий пока нет"
            text: "Загрузите файл на вкладке «Документы», затем создайте ревизию с внешним шифром (например, «01»)."
        }

        Repeater {
            model: kr.revs
            delegate: VCard {
                id: revCard
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: revCol.implicitHeight + 28
                ColumnLayout {
                    id: revCol
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14
                    spacing: 8
                    RowLayout {
                        Layout.fillWidth: true; spacing: 10
                        ColumnLayout { spacing: 1; Layout.fillWidth: true
                            Text { text: revCard.modelData.doc_code + " · ревизия «" + revCard.modelData.label + "»"
                                   color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                            Text { text: revCard.modelData.doc_title
                                   color: Theme.textMuted; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true } }
                        VChip { visible: revCard.modelData.issue_code !== ""
                                text: revCard.modelData.issue_label + " (" + revCard.modelData.issue_code + ")"
                                tone: "warning" }
                        VChip { text: revCard.modelData.status_label; tone: revCard.modelData.tone }
                    }
                    Text { visible: revCard.modelData.note !== ""; text: "Примечание: " + revCard.modelData.note
                           color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    Text {
                        text: revCard.modelData.author_name + " · " + revCard.modelData.created_label
                              + (revCard.modelData.comments_total > 0
                                 ? " · замечания: " + revCard.modelData.comments_open + "/" + revCard.modelData.comments_total + " открыто"
                                 : "")
                        color: Theme.textMuted; font.pixelSize: 11
                    }

                    // --- действия с ревизией (подсказка по роли; право проверяет сервер)
                    Flow {
                        Layout.fillWidth: true; spacing: 8
                        Repeater {
                            model: revCard.modelData.actions
                            delegate: VButton {
                                required property var modelData
                                text: modelData.label; kind: modelData.danger ? "danger" : "ghost"
                                enabled: !modelData.blocked
                                tip: modelData.blocked ? (modelData.blocked_hint || "") : ""
                                onClicked: {
                                    if (modelData.kind === "transition") {
                                        transDialog.setup(revCard.modelData, modelData)
                                    } else if (modelData.kind === "comment") {
                                        commentDialog.setup(revCard.modelData, modelData)
                                    }
                                }
                            }
                        }
                        VButton { text: "Штамп выдачи"; kind: "ghost"; iconName: "tag"
                                  visible: revCard.modelData.issue_code !== "" || backend.online
                                  onClicked: issueDialog.setup(revCard.modelData) }
                        VButton { text: "Скачать файл"; kind: "ghost"; iconName: "download"
                                  onClicked: {
                                      fileSave.suggested = backend.suggestedFileName(revCard.modelData.doc_version_id)
                                      fileSave.tag = "rkd-rev"; fileSave.versionId = revCard.modelData.doc_version_id
                                      fileSave.open()
                                  } }
                    }

                    // --- замечания и ответы
                    ColumnLayout {
                        visible: revCard.modelData.comments.length > 0
                        Layout.fillWidth: true; spacing: 6
                        Repeater {
                            model: revCard.modelData.comments
                            delegate: Rectangle {
                                id: cRow
                                required property var modelData
                                Layout.fillWidth: true
                                radius: 8; color: Theme.surfaceAlt
                                implicitHeight: cCol.implicitHeight + 20
                                ColumnLayout {
                                    id: cCol
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                                    spacing: 4
                                    RowLayout {
                                        Layout.fillWidth: true; spacing: 8
                                        Text { text: "№ " + cRow.modelData.seq
                                               color: Theme.textMuted; font.pixelSize: 12; font.weight: Font.DemiBold }
                                        VChip { text: cRow.modelData.source_label
                                                tone: cRow.modelData.source === "customer" ? "warning" : "neutral" }
                                        Text { visible: cRow.modelData.page !== ""; text: "стр. " + cRow.modelData.page
                                               color: Theme.textMuted; font.pixelSize: 11 }
                                        Text { visible: cRow.modelData.external_ref !== ""; text: cRow.modelData.external_ref
                                               color: Theme.textMuted; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true }
                                        Item { Layout.fillWidth: true }
                                        VChip { text: cRow.modelData.status_label
                                                tone: cRow.modelData.status === "answered" ? "success" : "danger" }
                                    }
                                    Text { text: cRow.modelData.text; color: Theme.text; font.pixelSize: 12
                                           wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                    Repeater {
                                        model: cRow.modelData.responses
                                        delegate: Text {
                                            required property var modelData
                                            text: "↳ Ответ: " + modelData.text + " — " + modelData.author_name + ", " + modelData.created_label
                                                  + (modelData.has_file ? " (с файлом)" : "")
                                            color: Theme.textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap
                                            Layout.fillWidth: true; leftPadding: 12
                                        }
                                    }
                                    VButton {
                                        visible: cRow.modelData.status === "open" && backend.online
                                        text: "Ответить"; kind: "ghost"
                                        onClicked: responseDialog.setup(cRow.modelData)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ================================ ТРАНСМИТТАЛЫ
    ColumnLayout {
        visible: kr.seg === "transmittals"
        Layout.fillWidth: true
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Text { text: "Трансмитталы проекта — письма-передачи ревизий заказчику с ZIP-пакетом"
                   color: Theme.textMuted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            VButton { text: "Новый трансмиттал"; iconName: "plus"; enabled: backend.online
                      onClicked: trDialog.ask() }
        }

        EmptyState {
            visible: kr.transmittals.length === 0
            Layout.fillWidth: true
            iconName: "send"; title: "Трансмитталов пока нет"
            text: "Трансмиттал отправляет ревизии со статусом «Разрешена к отправке» заказчику и собирает ZIP-пакет."
        }

        Repeater {
            model: kr.transmittals
            delegate: VCard {
                id: trCard
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: trCol.implicitHeight + 28
                ColumnLayout {
                    id: trCol
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true; spacing: 10
                        Text { text: "Трансмиттал № " + trCard.modelData.seq + " → " + trCard.modelData.recipient
                               color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; Layout.fillWidth: true
                               elide: Text.ElideRight }
                        VChip { text: trCard.modelData.channel_label; tone: "neutral" }
                        VChip { text: trCard.modelData.status_label; tone: trCard.modelData.tone }
                    }
                    Text {
                        text: trCard.modelData.items.map(function(i) { return i.label }).join("; ")
                        color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                    Text {
                        text: trCard.modelData.author_name + " · " + trCard.modelData.created_label
                              + (trCard.modelData.delivered_label !== ""
                                 ? " · доставлен " + trCard.modelData.delivered_label + " (" + trCard.modelData.external_ref + ")"
                                 : "")
                              + (trCard.modelData.has_package ? " · " + trCard.modelData.package_name : "")
                        color: Theme.textMuted; font.pixelSize: 11
                    }
                    RowLayout {
                        spacing: 8
                        VButton { visible: trCard.modelData.has_package; text: "Скачать пакет (ZIP)"; kind: "ghost"
                                  iconName: "download"; enabled: trCard.modelData.can_download
                                  onClicked: {
                                      pkgSave.suggested = backend.transmittalPackageName(trCard.modelData.id)
                                      pkgSave.transmittalId = trCard.modelData.id
                                      pkgSave.open()
                                  } }
                        VButton { visible: trCard.modelData.can_register_receipt; text: "Зарегистрировать доставку"
                                  kind: "ghost"; iconName: "check-check"
                                  onClicked: receiptDialog.setup(trCard.modelData) }
                    }
                }
            }
        }
    }

    // ================================ ПРОФИЛИ ШИФРОВ
    ColumnLayout {
        visible: kr.seg === "profiles"
        Layout.fillWidth: true
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Text { text: "Профили шифров — шаблон номера ревизии и словарь статусов выдачи (И/З) для документов проекта"
                   color: Theme.textMuted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            VButton { text: "Новый профиль"; iconName: "plus"; enabled: kr.v.can_manage_profiles === true
                      onClicked: profileDialog.ask() }
        }

        EmptyState {
            visible: kr.profiles.length === 0
            Layout.fillWidth: true
            iconName: "tag"; title: "Профилей пока нет"
            text: "Профиль задаёт шаблон шифра ревизии (например, «%02d») и статусы выдачи И0/И1/З1."
        }

        Repeater {
            model: kr.profiles
            delegate: VCard {
                id: pCard
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: pCol.implicitHeight + 28
                ColumnLayout {
                    id: pCol
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14
                    spacing: 5
                    RowLayout {
                        Layout.fillWidth: true; spacing: 10
                        Text { text: pCard.modelData.name; color: Theme.text; font.pixelSize: 14
                               font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                        VChip { text: pCard.modelData.scope_label; tone: pCard.modelData.kit_id !== "" ? "info" : "neutral" }
                        VChip { visible: !pCard.modelData.active; text: "Неактивен"; tone: "danger" }
                    }
                    Text { text: "Шаблон: " + pCard.modelData.label_pattern + " → пример «" + pCard.modelData.label_example + "»"
                           color: Theme.textMuted; font.pixelSize: 12 }
                    Text { text: "Статусы выдачи: " + pCard.modelData.statuses_label
                                 + " · ревизий по профилю: " + pCard.modelData.revisions
                                 + " · обновлён " + pCard.modelData.updated_label
                           color: Theme.textMuted; font.pixelSize: 11 }
                    Flow {
                        Layout.fillWidth: true; spacing: 8
                        VButton { text: "Изменить"; kind: "ghost"; enabled: kr.v.can_manage_profiles === true
                                  onClicked: profileDialog.askEdit(pCard.modelData) }
                        VButton { text: pCard.modelData.active ? "Отключить" : "Включить"; kind: "ghost"
                                  enabled: kr.v.can_manage_profiles === true
                                  onClicked: profileCmd.run(pCard.modelData.active ? "docprofile.archive" : "docprofile.unarchive",
                                                     { id: pCard.modelData.id, expected_version: pCard.modelData.version }) }
                    }
                }
            }
        }
    }

    // ================================ диалоги
    Cmd { id: revCmd; dialog: revDialog }
    Cmd { id: transCmd; dialog: transDialog }
    Cmd { id: commentCmd; dialog: commentDialog }
    Cmd { id: respCmd; dialog: responseDialog }
    Cmd { id: issueCmd; dialog: issueDialog }
    Cmd { id: trCmd; dialog: trDialog }
    Cmd { id: receiptCmd; dialog: receiptDialog }
    Cmd { id: profileCmd; dialog: profileDialog }

    // --- новая ревизия
    FormDialog {
        id: revDialog
        title: "Новая ревизия документа"
        subtitle: "Внешний шифр ревизии задаёт инженер; он уникален в документе. Файл — из уже загруженных версий."
        confirmText: "Создать"
        canConfirm: docSel.currentIndex >= 0 && labelField.text.trim() !== "" && verSel.currentIndex >= 0
        function ask() { error = ""; docSel.currentIndex = -1; labelField.text = ""; noteField.text = ""; open() }
        function payload() {
            var d = kr.docOptions[docSel.currentIndex]
            var vers = d ? backend.documents.filter(function(x) { return x.id === d.id })[0].versions : []
            return { id: backend.newId(), document_id: d.id, label: labelField.text.trim(),
                     doc_version_id: vers[verSel.currentIndex].id,
                     note: noteField.text.trim() !== "" ? noteField.text.trim() : null }
        }
        onConfirmed: revCmd.run("docrev.create", payload())
        VSelect { id: docSel; label: "Документ"; model: kr.docOptions }
        VField { id: labelField; label: "Шифр ревизии"; required: true
                 hint: kr.profileHint.name !== ""
                       ? "Профиль «" + kr.profileHint.name + "»: " + kr.profileHint.label_pattern + ", пример «" + kr.profileHint.label_example + "»"
                       : "Например: «01», «REV A»"; placeholder: "01" }
        VSelect { id: verSel; label: "Файл ревизии"
                  model: {
                      if (docSel.currentIndex < 0) return []
                      var d = kr.docOptions[docSel.currentIndex]
                      var vers = backend.documents.filter(function(x) { return x.id === d.id })[0].versions
                      var out = []
                      for (var i = 0; i < vers.length; i++)
                          out.push({ id: vers[i].id, label: "версия " + vers[i].version_no + " · " + vers[i].file_name })
                      return out
                  } }
        VField { id: noteField; label: "Примечание"; hint: "Необязательно — видно в истории ревизии" }
    }

    // --- переход статуса ревизии (с причиной или без)
    FormDialog {
        id: transDialog
        property var rev: null
        property var act: null
        title: act ? act.label : ""
        subtitle: rev ? rev.doc_code + " · ревизия «" + rev.label + "» — сейчас «" + rev.status_label + "»" : ""
        confirmText: "Выполнить"
        confirmKind: act && act.danger ? "danger" : "primary"
        canConfirm: !act || !act.reason || reasonField.text.trim() !== ""
        function setup(rev, act) { this.rev = rev; this.act = act; error = ""; reasonField.text = ""; open() }
        onConfirmed: transCmd.run("docrev.transition", { id: transDialog.rev.id, expected_version: transDialog.rev.version,
                               to: transDialog.act.to,
                               reason: reasonField.text.trim() !== "" ? reasonField.text.trim() : null })
        VField { id: reasonField; label: transDialog.act && transDialog.act.reason_label ? transDialog.act.reason_label : "Причина"
                 required: transDialog.act && transDialog.act.reason
                 hint: transDialog.act && transDialog.act.reason ? "Обязательно — попадёт в историю ревизии" : "Необязательно" }
    }

    // --- замечание к ревизии
    FormDialog {
        id: commentDialog
        property var rev: null
        property var act: null
        title: act ? act.label : "Замечание"
        subtitle: rev ? rev.doc_code + " · ревизия «" + rev.label + "»" : ""
        confirmText: "Добавить"
        canConfirm: textField.text.trim() !== "" && (act && act.source !== "customer" || refField.text.trim() !== "")
        function setup(rev, act) { this.rev = rev; this.act = act; error = ""; textField.text = ""; pageField.text = ""; refField.text = ""; open() }
        onConfirmed: commentCmd.run("comment.add", { id: backend.newId(), revision_id: commentDialog.rev.id, source: commentDialog.act.source,
                             text: textField.text.trim(),
                             page: pageField.text.trim() !== "" ? pageField.text.trim() : null,
                             external_ref: act.source === "customer" && refField.text.trim() !== "" ? refField.text.trim() : null })
        VField { id: pageField; label: "Страница/лист"; hint: "Необязательно — где именно замечание"; placeholder: "Лист 3" }
        VField { id: textField; label: "Текст замечания"; required: true }
        VField { id: refField; label: "Основание (письмо заказчика)"; required: commentDialog.act && commentDialog.act.source === "customer"
                 visible: commentDialog.act && commentDialog.act.source === "customer"
                 hint: "Номер и дата письма заказчика — замечание регистрируется только по документу" }
    }

    // --- ответ на замечание
    FormDialog {
        id: responseDialog
        property var comment: null
        title: "Ответ на замечание № " + (comment ? comment.seq : "")
        subtitle: "Ответ закрывает замечание. Можно приложить файл с обоснованием."
        confirmText: "Отправить ответ"
        canConfirm: respText.text.trim() !== ""
        property string filePath: ""
        function setup(c) { responseDialog.comment = c; error = ""; respText.text = ""; filePath = ""; open() }
        onConfirmed: respCmd.run("comment.respond", { id: backend.newId(), comment_id: responseDialog.comment.id,
                             expected_version: responseDialog.comment.version, text: respText.text.trim(),
                             file_path: filePath !== "" ? filePath : null })
        Text { text: responseDialog.comment ? responseDialog.comment.text : ""; color: Theme.textMuted; font.pixelSize: 12
               wrapMode: Text.WordWrap; Layout.fillWidth: true }
        VField { id: respText; label: "Ответ"; required: true; hint: "Что сделано по замечанию" }
        RowLayout {
            Layout.fillWidth: true
            VField { readOnly: true; label: "Файл ответа"; text: responseDialog.filePath !== "" ? responseDialog.filePath.split("/").pop() : ""
                     placeholder: "не выбран"; Layout.fillWidth: true }
            VButton { text: "Выбрать…"; kind: "ghost"; onClicked: respFile.open() }
        }
    }
    FileDialog {
        id: respFile
        fileMode: FileDialog.OpenFile
        onAccepted: responseDialog.filePath = selectedFile.toString().replace("file://", "")
    }

    // --- штамп выдачи ревизии (И/З): статусы — из действующего профиля шифров снимка
    FormDialog {
        id: issueDialog
        property var rev: null
        title: "Штамп выдачи"
        subtitle: rev ? rev.doc_code + " · ревизия «" + rev.label + "»" + (rev.issue_code !== "" ? " — сейчас " + rev.issue_label : "") : ""
        confirmText: "Поставить"
        canConfirm: issueSel.currentIndex >= 0
        function setup(rev) {
            this.rev = rev; error = ""
            // Действующий профиль области (комплектный важнее проектного); статусы — его словарь issue_statuses.
            var profs = kr.profiles
            var pick = null
            for (var i = 0; i < profs.length; i++) if (profs[i].kit_id === kr.kit.id && profs[i].active) pick = profs[i]
            if (!pick) for (var j = 0; j < profs.length; j++) if (profs[j].kit_id === "" && profs[j].active) pick = profs[j]
            var opts = []
            if (pick) {
                var st = pick.issue_statuses || []
                for (var k = 0; k < st.length; k++) opts.push({ id: st[k].code, label: st[k].code + " — " + st[k].label })
            }
            issueSel.model = opts
            issueSel.currentIndex = opts.length > 0 ? 0 : -1
            if (!pick) error = "Нет действующего профиля шифров — сначала создайте профиль на вкладке «Профили шифров»."
            open()
        }
        onConfirmed: issueCmd.run("docrev.set_issue", { id: issueDialog.rev.id, expected_version: issueDialog.rev.issue_version,
                             status_code: issueSel.model[issueSel.currentIndex].id })
        VSelect { id: issueSel; label: "Статус выдачи"; hint: "Словарь статусов задаёт профиль шифров (И/З)" }
    }

    // --- трансмиттал: создание
    FormDialog {
        id: trDialog
        title: "Новый трансмиттал"
        subtitle: "Отправляются только ревизии со статусом «Разрешена к отправке»; сервер соберёт ZIP-пакет и переведёт их в «Отправлена»."
        confirmText: "Отправить"
        canConfirm: recipientField.text.trim() !== "" && revChecks.selected.length > 0
        readonly property var readyRevs: {
            var out = []
            var all = kr.v.revisions || []
            for (var i = 0; i < all.length; i++)
                if (all[i].status === "ready_to_transmit") out.push(all[i])
            return out
        }
        function ask() { error = ""; recipientField.text = ""; channelSel.currentIndex = 0; noteTr.text = ""; revChecks.selected = []; open() }
        onConfirmed: {
            var payload = { id: backend.newId(), project_id: kr.kit.project_id, recipient: recipientField.text.trim(),
                            channel: channelSel.model[channelSel.currentIndex].id,
                            revision_ids: revChecks.selected }
            if (noteTr.text.trim() !== "") payload.note = noteTr.text.trim()
            trCmd.run("transmittal.create", payload)
        }
        VField { id: recipientField; label: "Получатель"; required: true; placeholder: "ООО «Заказчик», отдел ПТО" }
        VSelect { id: channelSel; label: "Способ отправки"
                  model: [{ id: "email", label: "Электронная почта" }, { id: "edms", label: "СЭД (система электронного документооборота)" },
                          { id: "courier", label: "Курьер / почта" }] }
        Text { text: "Ревизии к отправке:"; color: Theme.textMuted; font.pixelSize: 12 }
        Repeater {
            model: trDialog.readyRevs
            delegate: CheckBox {
                required property var modelData
                text: modelData.doc_code + " · ревизия «" + modelData.label + "»"
                checked: revChecks.selected.indexOf(modelData.id) >= 0
                onToggled: {
                    var s = revChecks.selected.slice()
                    var i = s.indexOf(modelData.id)
                    if (checked && i < 0) s.push(modelData.id)
                    if (!checked && i >= 0) s.splice(i, 1)
                    revChecks.selected = s
                }
            }
        }
        QtObject { id: revChecks; property var selected: [] }
        Text { visible: trDialog.readyRevs.length === 0
               text: "Нет ревизий со статусом «Разрешена к отправке» — сначала разрешите отправку на вкладке «Ревизии»."
               color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        VField { id: noteTr; label: "Примечание"; hint: "Необязательно" }
    }

    // --- регистрация доставки трансмиттала
    FormDialog {
        id: receiptDialog
        property var tr: null
        title: "Доставка трансмиттала № " + (tr ? tr.seq : "")
        subtitle: "Отметьте доставку и укажите подтверждение заказчика (номер входящего письма/протокола)."
        confirmText: "Зарегистрировать"
        canConfirm: refReceipt.text.trim() !== ""
        function setup(t) { tr = t; error = ""; refReceipt.text = ""; open() }
        onConfirmed: receiptCmd.run("transmittal.register_receipt", { id: receiptDialog.tr.id, expected_version: receiptDialog.tr.version,
                             external_ref: refReceipt.text.trim() })
        VField { id: refReceipt; label: "Основание"; required: true
                 hint: "Номер входящего письма или протокола заказчика"; placeholder: "вх. № 123 от 10.11.2026" }
    }

    // --- профиль шифров: создание/правка
    FormDialog {
        id: profileDialog
        property var editing: null
        title: editing ? "Профиль шифров — правка" : "Новый профиль шифров"
        subtitle: "Шаблон шифра: «%02d» — порядковый номер с ведущим нулём; «REV %s» — буква. Статусы выдачи: «И0: На исполнение» — код и название."
        confirmText: editing ? "Сохранить" : "Создать"
        canConfirm: nameP.text.trim() !== "" && patternP.text.trim() !== ""
        function ask() { editing = null; error = ""; nameP.text = ""; patternP.text = "%02d"; exampleP.text = ""; statusesP.text = "И0: На исполнение\nИ1: На исполнение (повторно)\nЗ1: Замечание"; open() }
        function askEdit(p) {
            editing = p; error = ""; nameP.text = p.name; patternP.text = p.label_pattern; exampleP.text = p.label_example
            var st = p.issue_statuses || [], lines = []
            for (var i = 0; i < st.length; i++) lines.push(st[i].code + ": " + st[i].label)
            statusesP.text = lines.join("\n")
            open()
        }
        function statusesPayload() {
            var lines = statusesP.text.split("\n"), out = []
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim()
                if (line === "") continue
                var colon = line.indexOf(":")
                if (colon <= 0) return null
                out.push({ code: line.substring(0, colon).trim(), label: line.substring(colon + 1).trim() })
            }
            return out.length > 0 ? out : null
        }
        onConfirmed: {
            var stPayload = statusesPayload()
            if (statusesP.text.trim() !== "" && stPayload === null) {
                error = "Статусы выдачи: каждая строка — «код: название» (например, «И0: На исполнение»)."
                return
            }
            if (editing) {
                var upd = { id: editing.id, expected_version: editing.version,
                            name: nameP.text.trim(), label_pattern: patternP.text.trim(),
                            label_example: exampleP.text.trim() !== "" ? exampleP.text.trim() : null }
                if (stPayload !== null) upd.issue_statuses = stPayload
                profileCmd.run("docprofile.update", upd)
            } else {
                var crt = { id: backend.newId(), project_id: kr.kit.project_id,
                            kit_id: null, name: nameP.text.trim(), label_pattern: patternP.text.trim(),
                            label_example: exampleP.text.trim() !== "" ? exampleP.text.trim() : null }
                if (stPayload !== null) crt.issue_statuses = stPayload
                profileCmd.run("docprofile.create", crt)
            }
        }
        VField { id: nameP; label: "Название"; required: true; placeholder: "ГОСТ Р 21.1101" }
        VField { id: patternP; label: "Шаблон шифра"; required: true; placeholder: "%02d" }
        VField { id: exampleP; label: "Пример шифра"; hint: "Показывается инженеру при создании ревизии"; placeholder: "01" }
        VField { id: statusesP; label: "Статусы выдачи (по строке «код: название»)"
                 hint: "Например: «И0: На исполнение». Пусто при правке — словарь не меняется." }
    }

    // --- сохранение файла ревизии / пакета трансмиттала
    FileDialog {
        id: fileSave
        fileMode: FileDialog.SaveFile
        property string suggested: ""
        property string tag: ""
        property string versionId: ""
        currentFile: suggested
        onAccepted: backend.downloadDocumentVersion(tag, versionId, selectedFile)
    }
    FileDialog {
        id: pkgSave
        fileMode: FileDialog.SaveFile
        property string suggested: ""
        property string transmittalId: ""
        currentFile: suggested
        onAccepted: backend.downloadTransmittalPackage("rkd-pkg", transmittalId, selectedFile)
    }
}
