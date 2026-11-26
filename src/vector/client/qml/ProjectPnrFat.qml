import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Блок «ПНР, испытания (FAT) и доставка» на карточке проекта (D-70, этап 6 blueprint 52):
//  * запросы ПНР — внутренняя наладка / выезд на площадку: питание, плановая дата; статус ведёт РП,
//    отмена — с причиной;
//  * FAT-сессии по шаблонам чек-листов (снимок разделов/градаций): замечания к проекту (не к человеку),
//    закрытие с причиной; итог неизменяем — повторное испытание новой сессией;
//  * подсказка шлюза отгрузки: почему «Отгружена» недоступна (нет финального FAT / идёт / неуспешен /
//    открытые блокирующие замечания);
//  * документы доставки по настраиваемым формам (автозаполнение данными проекта/комплекта).
//
// Чтение — из снимка pull (и офлайн); действия — онлайн-команды, кнопки — подсказка по роли (сервер решает).
ColumnLayout {
    id: pf
    property var project: null
    readonly property var v: project ? backend.projectPnrFat(project.id, backend.dataRevision) : ({})
    readonly property bool gateBlocked: v.shipping_gate !== undefined && v.shipping_gate !== null
                                        && v.shipping_gate.blocked === true
    readonly property var pnrRows: v.pnr || []
    readonly property var fatRows: v.fats || []
    readonly property var docRows: v.docs || []
    readonly property var counts: v.counts || ({})
    spacing: 8

    RowLayout {
        Layout.fillWidth: true
        SectionTitle { text: "ПНР, испытания и доставка"; iconName: "clipboard-check" }
        Item { Layout.fillWidth: true }
        Text { visible: (pf.counts.open_blocking || 0) > 0
               text: "блокирующих замечаний: " + pf.counts.open_blocking
               color: Theme.danger; font.pixelSize: Theme.fsS }
    }

    // ---------- шлюз отгрузки (подсказка; проверяет сервер в момент перехода потока «Отгрузка»)
    Banner {
        visible: pf.gateBlocked
        Layout.fillWidth: true; tone: "warning"
        text: pf.gateBlocked ? "Отгрузка заблокирована: " + pf.v.shipping_gate.reason : ""
    }

    // ---------- запросы ПНР
    RowLayout {
        Layout.fillWidth: true; Layout.topMargin: 4
        SectionTitle { text: "Запросы ПНР" + (pf.pnrRows.length ? " (" + pf.pnrRows.length + ")" : ""); iconName: "wrench" }
        Item { Layout.fillWidth: true }
        VButton { text: "Запросить ПНР"; kind: "ghost"; compact: true; iconName: "plus"
                  enabled: backend.online === true
                  tip: "Внутренняя наладка или выезд на площадку заказчика; дальше запрос ведёт РП проекта"
                  onClicked: pnrDialog.ask() }
    }
    Text { visible: pf.pnrRows.length === 0
           text: "Запросов ПНР нет — создайте, когда потребуется наладка (внутренняя или на площадке)."
           color: Theme.textFaint; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    Repeater {
        model: pf.pnrRows
        delegate: VCard {
            id: pCard
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: pCol.implicitHeight + 20
            ColumnLayout {
                id: pCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                spacing: 4
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Text { text: pCard.modelData.title; color: Theme.text; font.pixelSize: Theme.fs
                           font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                    VChip { text: pCard.modelData.kind_label; tone: "neutral" }
                    VChip { text: pCard.modelData.status_label; tone: pCard.modelData.tone }
                }
                Text { text: (pCard.modelData.need_power ? "Нужно питание · " : "")
                             + (pCard.modelData.planned_on !== "" ? "план: " + pCard.modelData.planned_on + " · " : "")
                             + pCard.modelData.kit_label
                       color: Theme.textMuted; font.pixelSize: Theme.fsXs }
                Text { visible: pCard.modelData.status_reason !== ""
                       text: "Причина: " + pCard.modelData.status_reason
                       color: Theme.textMuted; font.pixelSize: Theme.fsXs; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Flow {
                    Layout.fillWidth: true; spacing: 6
                    Repeater {
                        model: pCard.modelData.actions
                        delegate: VButton {
                            required property var modelData
                            text: modelData.label; kind: modelData.danger ? "danger" : "ghost"; compact: true
                            onClicked: pnrStatusDialog.setup(pCard.modelData, modelData)
                        }
                    }
                }
            }
        }
    }

    // ---------- испытания FAT
    RowLayout {
        Layout.fillWidth: true; Layout.topMargin: 4
        SectionTitle { text: "Испытания (FAT)" + (pf.fatRows.length ? " (" + pf.fatRows.length + ")" : ""); iconName: "flask" }
        Item { Layout.fillWidth: true }
        VButton { text: "Начать FAT"; kind: "ghost"; compact: true; iconName: "plus"
                  enabled: pf.v.can_manage === true && (pf.v.fat_templates || []).length > 0
                  tip: (pf.v.fat_templates || []).length === 0
                       ? "Сначала начальник сервисной службы создаёт активный шаблон чек-листа вида FAT"
                       : "Предварительный — для ранней проверки (не влияет на показатели); финальный решает отгрузку"
                  onClicked: fatDialog.ask() }
    }
    Text { visible: pf.fatRows.length === 0
           text: "Испытаний пока нет. Предварительный FAT — ранняя проверка (на показатели и отгрузку не влияет); финальный (приёмочный) решает отгрузку."
           color: Theme.textFaint; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    Repeater {
        model: pf.fatRows
        delegate: VCard {
            id: fCard
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: fCol.implicitHeight + 20
            ColumnLayout {
                id: fCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                spacing: 4
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Text { text: "#" + (fCard.modelData.seq || "?") + " " + fCard.modelData.kind_label
                           color: Theme.text; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
                    VChip { text: fCard.modelData.status_label; tone: fCard.modelData.tone }
                    Text { text: fCard.modelData.template_name + " · " + fCard.modelData.kit_label
                           color: Theme.textMuted; font.pixelSize: Theme.fsXs; Layout.fillWidth: true; elide: Text.ElideRight }
                }
                Text { visible: fCard.modelData.note !== ""; text: fCard.modelData.note
                       color: Theme.textMuted; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Text { text: "Начат " + fCard.modelData.created_label
                             + (fCard.modelData.finished_label !== ""
                                ? " · завершён " + fCard.modelData.finished_label
                                  + " (" + fCard.modelData.finished_by_name + ")" : "")
                             + " · замечаний: " + fCard.modelData.counts.findings
                             + (fCard.modelData.counts.open > 0 ? ", открыто " + fCard.modelData.counts.open : "")
                       color: Theme.textFaint; font.pixelSize: Theme.fsXs }
                // замечания сессии (к проекту, не к человеку)
                Repeater {
                    model: fCard.modelData.findings
                    delegate: RowLayout {
                        id: fndRow
                        required property var modelData
                        Layout.fillWidth: true; spacing: 6
                        Text { text: "•"; color: Theme.textFaint; font.pixelSize: Theme.fsS }
                        VChip { text: fndRow.modelData.severity_label
                                tone: fndRow.modelData.blocking ? "danger" : "warning" }
                        Text { text: fndRow.modelData.text
                               color: fndRow.modelData.status === "closed" ? Theme.textFaint : Theme.text
                               font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true
                               font.strikeout: fndRow.modelData.status === "closed" }
                        Text { visible: fndRow.modelData.status === "closed"
                               text: "закрыто: " + fndRow.modelData.close_reason
                               color: Theme.textFaint; font.pixelSize: Theme.fsXs; elide: Text.ElideRight
                               Layout.maximumWidth: 260 }
                        VButton { visible: fndRow.modelData.can_close === true; text: "Закрыть"; kind: "ghost"
                                  compact: true; onClicked: closeFindingDialog.setup(fndRow.modelData) }
                    }
                }
                Flow {
                    Layout.fillWidth: true; spacing: 6
                    VButton { visible: fCard.modelData.can_add_finding === true; text: "Замечание"; kind: "ghost"
                              compact: true; iconName: "plus"
                              tip: "Замечание относится к проекту, а не к человеку; раздел и градация — из чек-листа этого испытания"
                              onClicked: findingDialog.ask(fCard.modelData) }
                    Repeater {
                        model: fCard.modelData.actions
                        delegate: VButton {
                            required property var modelData
                            text: modelData.label; kind: modelData.danger ? "danger" : "primary"; compact: true
                            onClicked: fatFinishDialog.setup(fCard.modelData, modelData)
                        }
                    }
                }
            }
        }
    }

    // ---------- документы доставки
    RowLayout {
        Layout.fillWidth: true; Layout.topMargin: 4
        SectionTitle { text: "Документы доставки" + (pf.docRows.length ? " (" + pf.docRows.length + ")" : ""); iconName: "file-text" }
        Item { Layout.fillWidth: true }
        VButton { text: "Выписать документ"; kind: "ghost"; compact: true; iconName: "plus"
                  enabled: backend.online === true && (pf.v.doc_templates || []).length > 0
                  tip: (pf.v.doc_templates || []).length === 0
                       ? "Сначала начальник логистики создаёт активную форму документа"
                       : "Форма заполнится данными проекта и комплекта; транспорт и примечания — вручную"
                  onClicked: docDialog.ask() }
    }
    Text { visible: pf.docRows.length === 0
           text: "Документов нет — ТОРГ-12, УПД и упаковочные листы выписываются по настраиваемым формам."
           color: Theme.textFaint; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    Repeater {
        model: pf.docRows
        delegate: VCard {
            id: dCard
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: dCol.implicitHeight + 20
            ColumnLayout {
                id: dCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                spacing: 4
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Text { text: dCard.modelData.template_title; color: Theme.text; font.pixelSize: Theme.fs
                           font.weight: Font.DemiBold }
                    VChip { text: dCard.modelData.template_code; tone: "neutral" }
                    Text { text: dCard.modelData.kit_label + " · изм. " + dCard.modelData.updated_label
                           color: Theme.textMuted; font.pixelSize: Theme.fsXs; Layout.fillWidth: true; elide: Text.ElideRight }
                    VButton { visible: dCard.modelData.can_edit === true; text: "Править"; kind: "ghost"
                              compact: true; iconName: "pencil"
                              onClicked: docEditDialog.setup(dCard.modelData) }
                }
                Text { text: dCard.modelData.rendered
                       color: Theme.textMuted; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap
                       Layout.fillWidth: true; font.family: "monospace" }
            }
        }
    }

    // ================================ диалоги
    Cmd { id: pnrCmd; dialog: pnrDialog }
    Cmd { id: pnrStatusCmd; dialog: pnrStatusDialog }
    Cmd { id: fatCmd; dialog: fatDialog }
    Cmd { id: findingCmd; dialog: findingDialog }
    Cmd { id: closeFindingCmd; dialog: closeFindingDialog }
    Cmd { id: fatFinishCmd; dialog: fatFinishDialog }
    Cmd { id: docCmd; dialog: docDialog }
    Cmd { id: docEditCmd; dialog: docEditDialog }

    FormDialog {
        id: pnrDialog
        title: "Запрос ПНР"
        subtitle: "Внутренняя наладка — на производстве; на площадке — выезд к заказчику. После создания запрос ведёт РП проекта."
        confirmText: "Создать запрос"
        canConfirm: pnrTitle.text.trim() !== ""
        function ask() { error = ""; pnrTitle.text = ""; pnrDate.text = ""; pnrNote.text = ""
                         pnrKind.currentIndex = 0; pnrPower.checked = false; pnrKit.currentIndex = 0; open() }
        onConfirmed: {
            var payload = { id: backend.newId(), project_id: pf.project.id,
                            kind: (pf.v.pnr_kinds || [])[pnrKind.currentIndex].id,
                            title: pnrTitle.text.trim(), need_power: pnrPower.checked }
            if (pnrDate.text.trim() !== "") payload.planned_on = pnrDate.text.trim()
            if (pnrNote.text.trim() !== "") payload.note = pnrNote.text.trim()
            var kit = (pf.v.kit_options || [])[pnrKit.currentIndex]
            if (kit && kit.id !== "") payload.kit_id = kit.id
            pnrCmd.run("pnr.create", payload)
        }
        VSelect { id: pnrKind; label: "Вид"; model: pf.v.pnr_kinds || [] }
        VField { id: pnrTitle; label: "Что наладить"; required: true; placeholder: "Наладка шкафа" }
        VSelect { id: pnrKit; label: "Комплект"; model: pf.v.kit_options || [] }
        CheckBox { id: pnrPower; text: "Нужно питание для проверки" }
        VField { id: pnrDate; label: "Плановая дата"; placeholder: "2026-12-01"; hint: "Формат: ГГГГ-ММ-ДД" }
        VField { id: pnrNote; label: "Примечание" }
    }

    FormDialog {
        id: pnrStatusDialog
        property var req: null
        property var act: null
        title: req ? "ПНР «" + req.title + "» → «" + (act ? act.label : "") + "»" : ""
        confirmText: "Выполнить"
        confirmKind: act && act.danger ? "danger" : "primary"
        canConfirm: !act || !act.reason || pnrReason.text.trim() !== ""
        function setup(req, act) { this.req = req; this.act = act; error = ""; pnrReason.text = ""; open() }
        onConfirmed: pnrStatusCmd.run("pnr.set_status", { id: pnrStatusDialog.req.id, to: pnrStatusDialog.act.to,
                                      reason: pnrReason.text.trim() !== "" ? pnrReason.text.trim() : null },
                                      pnrStatusDialog.req.version)
        VField { id: pnrReason; label: "Причина"; required: pnrStatusDialog.act && pnrStatusDialog.act.reason
                 hint: "Обязательна для отмены — попадёт в журнал проекта" }
    }

    FormDialog {
        id: fatDialog
        title: "Начать испытание (FAT)"
        subtitle: "Чек-лист испытания — снимок шаблона на момент начала: правка шаблона это испытание не перепишет. Предварительный не влияет на показатели; итог финального решает отгрузку."
        confirmText: "Начать"
        canConfirm: (pf.v.fat_templates || []).length > 0
        function ask() { error = ""; fatKind.currentIndex = 0; fatTpl.currentIndex = 0; fatKit.currentIndex = 0
                         fatNote.text = ""; open() }
        onConfirmed: {
            var payload = { id: backend.newId(), project_id: pf.project.id,
                            kind: (pf.v.fat_kinds || [])[fatKind.currentIndex].id,
                            template_id: (pf.v.fat_templates || [])[fatTpl.currentIndex].id }
            if (fatNote.text.trim() !== "") payload.note = fatNote.text.trim()
            var kit = (pf.v.kit_options || [])[fatKit.currentIndex]
            if (kit && kit.id !== "") payload.kit_id = kit.id
            fatCmd.run("fat.create", payload)
        }
        VSelect { id: fatKind; label: "Вид испытания"; model: pf.v.fat_kinds || [] }
        VSelect { id: fatTpl; label: "Шаблон чек-листа"; model: pf.v.fat_templates || [] }
        VSelect { id: fatKit; label: "Комплект"; model: pf.v.kit_options || [] }
        VField { id: fatNote; label: "Примечание" }
    }

    FormDialog {
        id: findingDialog
        property var fat: null
        title: "Замечание к испытанию"
        subtitle: fat ? fat.kind_label + " · шаблон «" + fat.template_name + "». Замечание относится к проекту, а не к человеку." : ""
        confirmText: "Добавить"
        canConfirm: findingText.text.trim() !== ""
        function ask(fat) { this.fat = fat; error = ""; findingText.text = ""
                            findingSection.model = fat.section_options; findingSection.currentIndex = 0
                            findingSeverity.model = fat.severity_options; findingSeverity.currentIndex = 0; open() }
        onConfirmed: findingCmd.run("fat.add_finding", { id: backend.newId(), fat_id: findingDialog.fat.id,
                                     section_code: findingSection.currentValue.id,
                                     severity: findingSeverity.currentValue.id,
                                     text: findingText.text.trim() })
        VSelect { id: findingSection; label: "Раздел чек-листа" }
        VSelect { id: findingSeverity; label: "Градация" }
        VField { id: findingText; label: "Суть замечания"; required: true }
    }

    FormDialog {
        id: closeFindingDialog
        property var finding: null
        title: "Закрыть замечание"
        subtitle: finding ? finding.severity_label + ": " + finding.text : ""
        confirmText: "Закрыть"
        canConfirm: closeReason.text.trim() !== ""
        function setup(finding) { this.finding = finding; error = ""; closeReason.text = ""; open() }
        onConfirmed: closeFindingCmd.run("fat.close_finding", { id: closeFindingDialog.finding.id,
                                         reason: closeReason.text.trim() }, closeFindingDialog.finding.version)
        VField { id: closeReason; label: "Что сделано"; required: true
                 hint: "Обязательно — строка останется в истории замечания" }
    }

    FormDialog {
        id: fatFinishDialog
        property var fat: null
        property var act: null
        title: fat ? fat.kind_label + " → «" + (act ? act.label : "") + "»" : ""
        subtitle: "Итог неизменяем: повторная проверка оформляется новым испытанием (история сохраняется)."
        confirmText: "Завершить испытание"
        confirmKind: act && act.danger ? "danger" : "primary"
        canConfirm: !act || !act.reason || fatReason.text.trim() !== ""
        function setup(fat, act) { this.fat = fat; this.act = act; error = ""; fatReason.text = ""; open() }
        onConfirmed: fatFinishCmd.run("fat.finish", { id: fatFinishDialog.fat.id, result: fatFinishDialog.act.to,
                                      reason: fatReason.text.trim() !== "" ? fatReason.text.trim() : null },
                                      fatFinishDialog.fat.version)
        VField { id: fatReason; label: "Причина"; required: fatFinishDialog.act && fatFinishDialog.act.reason
                 hint: "Обязательна для итога «неуспешен» — попадёт в журнал проекта" }
    }

    FormDialog {
        id: docDialog
        title: "Выписать документ доставки"
        subtitle: "Поля формы заполнятся данными проекта и комплекта; ниже — свои значения (например, транспорт). Незаполненные плейсхолдеры останутся в тексте видимыми."
        confirmText: "Выписать"
        canConfirm: (pf.v.doc_templates || []).length > 0
        function ask() { error = ""; docTpl.currentIndex = 0; docKit.currentIndex = 0
                         docExtra.text = ""; open() }
        onConfirmed: {
            var payload = { id: backend.newId(), project_id: pf.project.id,
                            template_id: (pf.v.doc_templates || [])[docTpl.currentIndex].id }
            var kit = (pf.v.kit_options || [])[docKit.currentIndex]
            if (kit && kit.id !== "") payload.kit_id = kit.id
            var extra = {}
            var lines = docExtra.text.split("\n")
            for (var i = 0; i < lines.length; i++) {
                var eq = lines[i].indexOf("=")
                if (eq > 0) extra[lines[i].substring(0, eq).trim()] = lines[i].substring(eq + 1).trim()
            }
            if (Object.keys(extra).length > 0) payload.values = extra
            docCmd.run("delivery_doc.create", payload)
        }
        VSelect { id: docTpl; label: "Форма"; model: pf.v.doc_templates || [] }
        VSelect { id: docKit; label: "Комплект"; model: pf.v.kit_options || [] }
        Text { text: "Свои значения (по строке «поле=значение», например: transport=автомобиль, водитель):"
               color: Theme.textMuted; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 80; clip: true
                     TextArea { id: docExtra; placeholderText: "truck=автомобиль, прицеп" } }
    }

    FormDialog {
        id: docEditDialog
        property var doc: null
        title: "Документ «" + (doc ? doc.template_title : "") + "»"
        subtitle: "Правка текста документа (версионируется; история правок — в журнале аудита)."
        confirmText: "Сохранить"
        canConfirm: docText.text.trim() !== ""
        function setup(doc) { this.doc = doc; error = ""; docText.text = doc.rendered; open() }
        onConfirmed: docEditCmd.run("delivery_doc.update", { id: docEditDialog.doc.id,
                                    rendered: docText.text }, docEditDialog.doc.version)
        ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 200; clip: true
                     TextArea { id: docText; wrapMode: TextEdit.Wrap; font.family: "monospace" } }
    }
}
