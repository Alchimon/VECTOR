import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

// Раздел «Изделия и паспорта» (этап 6, сценарий С5 шаги 7–9; D-38).
//
//  * Регистрация экземпляров — РП проекта (резерв — руководитель отдела) по ЗАКРЫТОМУ наряду: серия и заводские
//    номера, не больше, чем изготовлено по наряду. Номер уникален для «изделие + серия». Сервер сохраняет
//    неизменяемый состав каждого экземпляра (снимок наряда + замены по наряду) с контрольной суммой SHA-256.
//  * Карточка — точные связи: проект, наряд и SHA-256 его снимка, редакция ТП, версия расчёта; состав одного
//    изделия; операции наряда (кто и сколько работал); история эксплуатации.
//  * Паспорт — черновик по шаблону-мастеру (разделы ГОСТ 2.601/2.610 — вид документа «паспорт»): тексты разделов,
//    испытания, документы с точными версиями. Правится без связи (очередь); черновой экспорт — только РП и только
//    при связи, с пометкой «Черновик, не сертификат», каждый экспорт записывается в журнал.
//  * Передача заказчику, сервисные события и замены — только при связи; записи истории не меняются и не удаляются.
MasterDetail {
    id: page
    objectName: "productsPage"
    property var shell
    // Переход из другого раздела (shell.go("products", id)) открывает экземпляр с этим id.
    property string selectedId: shell && shell.navTarget.page === "products" ? (shell.navTarget.id || "") : ""
    readonly property var lv: backend.lifecycleOverview
    readonly property var all: lv.items || []
    readonly property var items: all.filter(i => search === "" || (i.label + " " + i.title + " " + i.project_code + " " + i.wo_label).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: all.find(i => i.id === selectedId) || null
    readonly property var pp: sel ? sel.passport : null
    function today() { var d = new Date(); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0") }
    title: "Изделия и паспорта"
    subtitle: "Экземпляров: " + ((lv.counts || {}).total || 0) + "  ·  передано заказчику: " + ((lv.counts || {}).handed_over || 0) + "  ·  " + (lv.as_of || "")
    hasSelection: sel !== null
    emptyIcon: "package-check"; emptyTitle: "Экземпляров нет"
    emptyText: lv.empty || ""
    emptyAction: lv.can_register === true ? "Зарегистрировать экземпляры" : ""
    onEmptyActionTriggered: regForm.openFor()
    noSelectionText: "Выберите экземпляр изделия"
    headerActions: [
        VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() },
        VButton { objectName: "btnRegisterInstances"; visible: page.lv.may_register === true; enabled: page.lv.can_register === true
                  text: "Зарегистрировать экземпляры"; kind: "primary"; iconName: "plus"; onClicked: regForm.openFor() }
    ]
    list.model: items
    list.delegate: ListRow {
        title: modelData.label
        subtitle: modelData.title + "  ·  " + modelData.project_code + "  ·  " + modelData.wo_label
        iconName: "package-check"
        chipText: modelData.status_label; chipTone: modelData.status_tone
        pending: modelData.pending === true
        selected: modelData.id === page.selectedId
        onClicked: page.selectedId = modelData.id
    }

    // ---------- карточка экземпляра
    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Rectangle { width: 52; height: 52; radius: 14; color: Theme.primarySoft
            Icon { anchors.centerIn: parent; name: "package-check"; size: 26; color: Theme.primary } }
        ColumnLayout { spacing: 4; Layout.fillWidth: true
            Text { text: sel ? sel.project_code + "  ·  " + sel.wo_label + "  ·  " + sel.title : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { objectName: "instanceTitle"; text: sel ? sel.label : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true } }
        VChip { text: sel ? sel.status_label : ""; tone: sel ? sel.status_tone : "neutral" }
        VChip { objectName: "instanceCompositionChip"; text: sel && sel.composition_ok ? "Состав цел" : "Состав повреждён"; tone: sel && sel.composition_ok ? "success" : "danger"
                iconName: sel && sel.composition_ok ? "shield-check" : "triangle-alert" }
    }
    Banner { visible: sel !== null && !sel.composition_ok; tone: "danger"
             text: "Состав экземпляра не совпадает со своей контрольной суммой (SHA-256) — данные изменены в обход программы. Сообщите администратору; черновой экспорт паспорта недоступен." }
    Banner { visible: sel !== null && sel.manage === true && page.lv.online !== true; tone: "warning"
             text: "Передача заказчику, сервисные события, замены и черновой экспорт — только при связи с сервером. Паспорт можно править и без связи." }
    Flow {
        Layout.fillWidth: true; spacing: 8
        visible: sel !== null && sel.manage === true
        VButton { objectName: "btnHandover"; visible: sel !== null && sel.status === "registered"; enabled: sel !== null && sel.can_handover === true
                  text: "Передать заказчику"; kind: "primary"; iconName: "truck"; onClicked: handForm.openFor() }
        VButton { objectName: "btnService"; visible: sel !== null && sel.status === "handed_over"; enabled: sel !== null && sel.can_service === true
                  text: "Сервисное событие"; kind: "soft"; iconName: "wrench"; onClicked: servForm.openFor() }
        VButton { objectName: "btnReplace"; enabled: sel !== null && sel.can_replace === true
                  text: "Замена в эксплуатации"; kind: "soft"; iconName: "arrow-left-right"; onClicked: replForm.openFor() }
    }

    SectionTitle { text: "Связи"; iconName: "git-compare" }
    Repeater {
        model: sel ? sel.links : []
        delegate: KeyValue { required property var modelData; kWidth: 210; k: modelData.k; v: modelData.v; muted: modelData.k.indexOf("SHA-256") >= 0 }
    }

    SectionTitle { text: "Паспорт"; iconName: "file-text" }
    Text { visible: sel !== null && !sel.has_passport; Layout.fillWidth: true; wrapMode: Text.Wrap
           text: sel && sel.may_passport ? "Паспорта ещё нет. Создайте черновик — можно и без связи; основные сведения, комплектность и движение программа подставит сама."
                                          : "Паспорта ещё нет. Черновик составляет РП проекта или ведущий инженер — участник проекта."
           color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
    Flow {
        Layout.fillWidth: true; spacing: 8
        VButton { objectName: "btnPassportCreate"; visible: sel !== null && sel.can_passport === true
                  text: "Создать паспорт"; kind: "primary"; iconName: "plus"; onClicked: editor.openFor(null) }
        VButton { objectName: "btnPassportEdit"; visible: pp !== null && pp.can_edit === true
                  text: "Править паспорт"; kind: "soft"; iconName: "pencil"; onClicked: editor.openFor(pp) }
        VButton { objectName: "btnPassportExport"; visible: pp !== null && pp.may_export === true; enabled: pp !== null && pp.can_export === true && sel.composition_ok
                  text: "Черновой экспорт"; kind: "soft"; iconName: "download"; onClicked: page.exportDraft() }
    }
    ColumnLayout {
        visible: pp !== null; Layout.fillWidth: true; spacing: 10
        RowLayout { Layout.fillWidth: true; spacing: 8
            VChip { text: pp ? pp.status_label : ""; tone: "warning"; iconName: "file-text" }
            VChip { visible: pp !== null && pp.pending; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
            VChip { visible: pp !== null && !pp.template_ok; text: "Шаблон изменён"; tone: "danger"; iconName: "triangle-alert" }
            Text { text: pp ? pp.template_label : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.fillWidth: true; elide: Text.ElideRight } }
        Banner { visible: pp !== null && pp.problems.length > 0; tone: "warning"; Layout.fillWidth: true
                 text: pp ? "Ещё не хватает: " + pp.problems.join(" ") : "" }
        KeyValue { visible: pp !== null && pp.updated_label !== ""; k: "Сохранил"; v: pp ? pp.updated_label : "" }
        Repeater {
            model: pp ? pp.sections : []
            // Раздел паспорта: заголовок над текстом (заголовки длинные — в колонку подписи не помещаются).
            delegate: ColumnLayout { required property var modelData
                objectName: "passportSection"
                Layout.fillWidth: true; spacing: 2
                Text { text: modelData.title; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true
                       VTipArea { text: modelData.hint } }
                Text { text: modelData.empty ? "не заполнено" : modelData.text; color: modelData.empty ? Theme.textFaint : Theme.text
                       font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true } }
        }
        Text { text: "Испытания"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
        Text { visible: pp !== null && pp.tests.length === 0; text: "Испытаний не указано."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Repeater {
            model: pp ? pp.tests : []
            delegate: RowLayout { required property var modelData
                objectName: "passportTestRow"
                Layout.fillWidth: true; spacing: 10
                Text { text: modelData.name + (modelData.protocol ? "  ·  протокол " + modelData.protocol : ""); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { text: modelData.date_label; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                VChip { text: modelData.result_label; tone: modelData.tone } }
        }
        Text { text: "Документы"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
        Text { visible: pp !== null && pp.documents.length === 0; text: "Документы не выбраны."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Repeater {
            model: pp ? pp.documents : []
            delegate: KeyValue { required property var modelData; kWidth: 110; k: modelData.version_label; v: modelData.label + (modelData.sha256 ? "  ·  SHA-256 " + modelData.sha_short : "")
                                 tip: "Точная версия документа: номер версии и контрольная сумма SHA-256 её файла." }
        }
        Text { text: "Журнал черновых экспортов"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
        Text { visible: pp !== null && pp.exports.length === 0; text: "Черновой экспорт ещё не выполнялся."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Repeater {
            model: pp ? pp.exports : []
            delegate: Text { required property var modelData; objectName: "passportExportRow"
                text: modelData.label + "  ·  SHA-256 " + modelData.sha_short; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
        }
    }

    SectionTitle { text: "Состав одного изделия"; iconName: "boxes" }
    Repeater {
        model: sel ? sel.lines : []
        delegate: RowLayout {
            required property var modelData
            objectName: "instanceLine"
            Layout.fillWidth: true; spacing: 10
            Text { text: modelData.code; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.preferredWidth: 140; elide: Text.ElideRight }
            Text { text: modelData.name + (modelData.substituted ? "  (замена по наряду)" : ""); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; Layout.fillWidth: true; wrapMode: Text.Wrap }
            Text { text: modelData.qty_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.features: { "tnum": 1 } }
        }
    }

    SectionTitle { text: "Изготовление (операции наряда)"; iconName: "list-checks" }
    Repeater {
        model: sel ? sel.operations : []
        delegate: RowLayout { required property var modelData
            Layout.fillWidth: true; spacing: 10
            Text { text: modelData.title + (modelData.work_center ? "  ·  " + modelData.work_center : ""); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
            Text { text: modelData.who + "  ·  факт " + modelData.fact_label + "  ·  " + modelData.finished_label; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS } }
    }

    SectionTitle { text: "История эксплуатации"; iconName: "history" }
    Text { visible: sel !== null && sel.events.length === 0; text: "Записей пока нет: экземпляр ещё не передавался заказчику."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
    Repeater {
        model: sel ? sel.events : []
        delegate: RowLayout { required property var modelData
            objectName: "instanceEventRow"
            Layout.fillWidth: true; spacing: 10
            Icon { name: modelData.icon; size: 16; color: Theme.textMuted; Layout.alignment: Qt.AlignTop }
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { text: modelData.date_label + "  ·  " + modelData.kind_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
                Text { text: modelData.summary; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { text: "Записал: " + modelData.who; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs } } }
    }

    // ---------- регистрация экземпляров
    FormDialog {
        id: regForm
        objectName: "registerForm"
        title: "Регистрация экземпляров"
        subtitle: "По закрытому наряду: серия и заводские номера, по одному на строке. Не больше, чем изготовлено по наряду."
        confirmText: "Зарегистрировать"
        readonly property var opts: page.lv.register_options || []
        readonly property var wo: opts.length > regWo.currentIndex && regWo.currentIndex >= 0 ? opts[regWo.currentIndex] : null
        readonly property int count: regSerials.text.split("\n").filter(s => s.trim() !== "").length
        canConfirm: wo !== null && regSeries.text.trim() !== "" && count > 0 && count <= (wo ? wo.left : 0)
        function openFor() { regWo.currentIndex = 0; regSeries.text = String(new Date().getFullYear()); regSerials.text = ""; open() }
        onConfirmed: { working = true; error = ""; backend.registerInstances("inst-register", wo.id, regSeries.text, regSerials.text) }
        VSelect { id: regWo; objectName: "regWo"; label: "Закрытый наряд"; model: regForm.opts }
        VField { id: regSeries; objectName: "regSeries"; label: "Серия"; required: true; placeholder: "2026"
                 hint: "Номер уникален в пределах «изделие + серия»: одна и та же серия у разных изделий не мешает." }
        Text { text: "Заводские номера *"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS
               VTipArea { text: "Заводской номер каждого изделия — по одному на строке. Буквы, цифры, точка, дефис, косая черта." } }
        ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 110
            TextArea { id: regSerials; objectName: "regSerials"; placeholderText: "001\n002"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: TextEdit.NoWrap
                       background: Rectangle { radius: Theme.radiusS; color: Theme.surface; border.color: regSerials.activeFocus ? Theme.primary : Theme.border } } }
        Text { text: regForm.wo ? "Номеров: " + regForm.count + " из " + regForm.wo.left + " возможных" : ""
               color: regForm.wo && regForm.count > regForm.wo.left ? Theme.danger : Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
    }
    Connections {
        target: backend
        function onCommandDone(t, r) {
            if (t !== "inst-register") return
            regForm.working = false
            if (r.status === "accepted") regForm.close()
            else regForm.error = r.message_ru || "Экземпляры не зарегистрированы."
        }
    }

    // ---------- передача заказчику
    FormDialog {
        id: handForm
        objectName: "handoverForm"
        title: "Передача заказчику"
        subtitle: sel ? sel.label + " · " + sel.title : ""
        confirmText: "Записать передачу"
        canConfirm: handDate.text.trim() !== "" && handWho.text.trim() !== "" && handDoc.text.trim() !== ""
        function openFor() { handDate.text = page.today(); handWho.text = ""; handDoc.text = ""; handNote.text = ""; open() }
        onConfirmed: hCmd.run("instance.handover", { id: sel.id, date: handDate.text.trim(), recipient: handWho.text, document: handDoc.text, note: handNote.text }, sel.version)
        VField { id: handDate; label: "Дата передачи"; required: true; placeholder: "ГГГГ-ММ-ДД"; hint: "Не позже сегодняшнего дня." }
        VField { id: handWho; objectName: "handWho"; label: "Кто принял у заказчика"; required: true; placeholder: "Фамилия И. О., должность" }
        VField { id: handDoc; objectName: "handDoc"; label: "Документ передачи"; required: true; placeholder: "Акт приёма-передачи № 12 от 16.11.2026" }
        VField { id: handNote; label: "Примечание"; placeholder: "Необязательно" }
    }
    Cmd { id: hCmd; dialog: handForm; tag: "inst-handover" }

    // ---------- сервисное событие
    FormDialog {
        id: servForm
        objectName: "serviceForm"
        title: "Сервисное событие"
        subtitle: sel ? sel.label + " · " + sel.title : ""
        confirmText: "Записать событие"
        canConfirm: servDate.text.trim() !== "" && servDesc.text.trim() !== "" && servKind.currentValue !== ""
        function openFor() { servKind.currentIndex = 0; servDate.text = page.today(); servDesc.text = ""; servDoc.text = ""; open() }
        onConfirmed: sCmd.run("instance.service", { id: sel.id, kind: servKind.currentValue, date: servDate.text.trim(), description: servDesc.text, document: servDoc.text }, 0)
        VSelect { id: servKind; label: "Вид события"; model: page.lv.service_kinds || [] }
        VField { id: servDate; label: "Дата события"; required: true; placeholder: "ГГГГ-ММ-ДД" }
        VField { id: servDesc; objectName: "servDesc"; label: "Что сделано или что произошло"; required: true; placeholder: "Плановое техническое обслуживание" }
        VField { id: servDoc; label: "Документ события"; placeholder: "Акт, наряд сервисной службы (необязательно)" }
    }
    Cmd { id: sCmd; dialog: servForm; tag: "inst-service" }

    // ---------- замена в эксплуатации
    FormDialog {
        id: replForm
        objectName: "replaceForm"
        title: "Замена в эксплуатации"
        subtitle: "Что снято и что установлено вместо. Состав экземпляра при изготовлении не меняется — замена добавляется в историю."
        confirmText: "Записать замену"
        readonly property var lineOpts: [{ line_id: "", label: "не из состава (другое)" }].concat((sel ? sel.lines : []).map(l => ({ line_id: l.line_id, label: (l.code !== "—" ? l.code + " · " : "") + l.name })))
        canConfirm: replDate.text.trim() !== "" && replOld.text.trim() !== "" && replNew.text.trim() !== "" && replReason.text.trim() !== ""
        function openFor() { replLine.currentIndex = 0; replDate.text = page.today(); replOld.text = ""; replOldSn.text = ""; replNew.text = ""; replNewSn.text = ""; replQty.text = "1"; replReason.text = ""; open() }
        onConfirmed: rCmd.run("instance.replace", { id: sel.id, date: replDate.text.trim(), removed: replOld.text, removed_serial: replOldSn.text, installed: replNew.text,
                                                    installed_serial: replNewSn.text, qty: replQty.text.trim().replace(",", "."), reason: replReason.text, line_id: replLine.currentValue }, 0)
        VSelect { id: replLine; label: "Строка состава"; model: replForm.lineOpts; valueRole: "line_id"
                  onCurrentIndexChanged: if (currentIndex > 0 && replOld.text === "") replOld.text = replForm.lineOpts[currentIndex].label }
        VField { id: replDate; label: "Дата замены"; required: true; placeholder: "ГГГГ-ММ-ДД" }
        RowLayout { Layout.fillWidth: true; spacing: 12
            VField { id: replOld; objectName: "replOld"; label: "Что снято"; required: true; Layout.fillWidth: true }
            VField { id: replOldSn; label: "Номер снятого"; placeholder: "Необязательно"; Layout.preferredWidth: 150 } }
        RowLayout { Layout.fillWidth: true; spacing: 12
            VField { id: replNew; objectName: "replNew"; label: "Что установлено"; required: true; Layout.fillWidth: true }
            VField { id: replNewSn; label: "Номер установленного"; placeholder: "Необязательно"; Layout.preferredWidth: 150 } }
        VField { id: replQty; label: "Количество"; placeholder: "1" }
        VField { id: replReason; objectName: "replReason"; label: "Причина замены"; required: true }
    }
    Cmd { id: rCmd; dialog: replForm; tag: "inst-replace" }

    // ---------- черновой экспорт паспорта (файл HTML; «Черновик, не сертификат»)
    function exportDraft() {
        exportDlg.selectedFile = exportDlg.currentFolder + "/" + "Паспорт-черновик " + sel.kit_code + " " + sel.series + "-" + sel.serial_no + ".html"
        exportDlg.open()
    }
    FileDialog {
        id: exportDlg
        title: "Сохранить черновик паспорта"
        fileMode: FileDialog.SaveFile
        nameFilters: ["Документ HTML (*.html)"]
        onAccepted: backend.exportPassport("passport-export", page.pp.id, page.pp.version, selectedFile.toString())
    }

    // ---------- редактор паспорта: тексты разделов, испытания (таблица), документы с точными версиями
    Popup {
        id: editor
        objectName: "passportEditor"
        property bool fresh: true
        property string passportId: ""
        property int editRev: 0
        property bool working: false
        property string error: ""
        property var texts: ({})
        readonly property var check: { editRev; return visible ? backend.passportCheck(texts, collectTests()) : ({ ok: true, message: "", by_line: {} }) }
        readonly property var docOpts: (page.lv.doc_options || []).filter(d => sel && d.project_id === sel.project_id)
        modal: true; focus: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(1040, (Overlay.overlay ? Overlay.overlay.width : 1100) - 48)
        height: Math.min(780, (Overlay.overlay ? Overlay.overlay.height : 820) - 48)
        padding: 0
        closePolicy: Popup.CloseOnEscape
        Overlay.modal: Rectangle { color: Theme.scrim }
        background: Rectangle { color: Theme.surface; radius: Theme.radiusL; border.color: Theme.border }
        ListModel { id: tm }
        ListModel { id: dm }
        function openFor(p) {
            fresh = p === null; error = ""; working = false
            passportId = p ? p.id : backend.newId()
            var t = {}
            var secs = page.lv.text_sections || []
            for (var i = 0; i < secs.length; i++) t[secs[i].key] = p ? (p.fields[secs[i].key] || "") : ""
            texts = t
            tm.clear(); dm.clear()
            var ts = p ? p.tests : []
            for (var j = 0; j < ts.length; j++) tm.append({ name: ts[j].name, result: ts[j].result, date: ts[j].date, protocol: ts[j].protocol || "", note: ts[j].note || "" })
            var ds = p ? p.documents : []
            for (var k = 0; k < ds.length; k++) dm.append({ document_id: ds[k].document_id, version_no: ds[k].version_no, label: ds[k].label })
            editRev++
            open()
        }
        function setText(key, value) { var t = texts; t[key] = value; texts = t; editRev++ }
        function collectTests() {
            var out = []
            for (var i = 0; i < tm.count; i++) { var r = tm.get(i); out.push({ name: r.name, result: r.result, date: r.date, protocol: r.protocol, note: r.note }) }
            return out
        }
        function collectDocs() {
            var out = []
            for (var i = 0; i < dm.count; i++) out.push({ document_id: dm.get(i).document_id, version_no: dm.get(i).version_no })
            return out
        }
        function save() {
            working = true; error = ""
            backend.savePassport("passport-save", { id: passportId, instance_id: sel.id, fields: texts, tests: collectTests(), documents: collectDocs() })
        }
        Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== "passport-save") return
                editor.working = false
                if (r.status === "accepted" || r.status === "queued") editor.close()
                else editor.error = r.message_ru || "Паспорт не сохранён."
            }
        }
        contentItem: ColumnLayout {
            spacing: 0
            RowLayout {
                Layout.fillWidth: true; Layout.margins: Theme.padL; Layout.bottomMargin: 10; spacing: 12
                Rectangle { width: 40; height: 40; radius: 11; color: Theme.primarySoft
                    Icon { anchors.centerIn: parent; name: "file-text"; size: 20; color: Theme.primary } }
                ColumnLayout { spacing: 2; Layout.fillWidth: true
                    Text { text: (editor.fresh ? "Новый паспорт — " : "Паспорт — ") + (sel ? sel.label : "") + " · черновик"
                           color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsL; font.weight: Font.DemiBold }
                    Text { VTipArea { text: backend.tip(parent.text) }
                        text: "Основные сведения, комплектность и движение изделия программа подставит сама. Черновик можно сохранять без связи. Программа не ставит подписи и печати и не подставляет гарантийные условия — их текст утверждает компания."
                        color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
                VButton { kind: "ghost"; iconName: "x"; compact: true; onClicked: editor.close(); Accessible.name: "Закрыть" }
            }
            ScrollView {
                id: esv
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true; contentWidth: availableWidth
                ColumnLayout {
                    width: esv.availableWidth - 2 * Theme.padL; x: Theme.padL
                    spacing: 10
                    Repeater {
                        model: page.lv.text_sections || []
                        delegate: ColumnLayout { required property var modelData
                            Layout.fillWidth: true; spacing: 4
                            Text { text: modelData.title; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.DemiBold
                                   VTipArea { text: modelData.hint } }
                            TextArea { objectName: "passportText_" + modelData.key; Layout.fillWidth: true; Layout.minimumHeight: 56
                                       text: editor.texts[modelData.key] || ""; placeholderText: modelData.hint; placeholderTextColor: Theme.textFaint
                                       color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: TextEdit.Wrap
                                       onTextChanged: if (text !== (editor.texts[modelData.key] || "")) editor.setText(modelData.key, text)
                                       background: Rectangle { radius: Theme.radiusS; color: Theme.surface; border.color: parent.activeFocus ? Theme.primary : Theme.border } } }
                    }
                    RowLayout { Layout.fillWidth: true; Layout.topMargin: 8
                        Text { text: "Испытания"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; Layout.fillWidth: true }
                        VButton { objectName: "passportAddTest"; text: "Добавить испытание"; iconName: "plus"; kind: "soft"; compact: true
                                  onClicked: { tm.append({ name: "", result: "pass", date: page.today(), protocol: "", note: "" }); editor.editRev++ } } }
                    Repeater {
                        model: tm
                        delegate: Rectangle {
                            id: trow
                            required property int index
                            required property var model
                            readonly property string problem: editor.check.by_line ? (editor.check.by_line[String(index)] || "") : ""
                            objectName: "passportTestEdit"
                            Layout.fillWidth: true; implicitHeight: tc.implicitHeight + 8; radius: Theme.radiusS
                            color: problem !== "" ? Theme.dangerSoft : "transparent"
                            ColumnLayout { id: tc; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.margins: 4; spacing: 2
                                RowLayout { Layout.fillWidth: true; spacing: 6
                                    CalcCell { Layout.fillWidth: true; text: trow.model.name; placeholder: "Испытание (например, проверка сопротивления изоляции)"; Accessible.name: "Испытание " + (trow.index + 1)
                                               onTextEdited: { tm.setProperty(trow.index, "name", text); editor.editRev++ } }
                                    VSegmented { model: page.lv.test_results || []; current: trow.model.result
                                                 onPicked: id => { tm.setProperty(trow.index, "result", id); editor.editRev++ } }
                                    CalcCell { Layout.preferredWidth: 110; text: trow.model.date; placeholder: "ГГГГ-ММ-ДД"; Accessible.name: "Дата испытания " + (trow.index + 1)
                                               onTextEdited: { tm.setProperty(trow.index, "date", text); editor.editRev++ } }
                                    CalcCell { Layout.preferredWidth: 130; text: trow.model.protocol; placeholder: "Протокол №"; Accessible.name: "Протокол " + (trow.index + 1)
                                               onTextEdited: { tm.setProperty(trow.index, "protocol", text); editor.editRev++ } }
                                    VButton { kind: "ghost"; compact: true; iconName: "trash-2"; Accessible.name: "Удалить испытание " + (trow.index + 1)
                                              onClicked: { tm.remove(trow.index); editor.editRev++ } } }
                                Text { visible: trow.problem !== ""; text: trow.problem; color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsXs; leftPadding: 4; wrapMode: Text.Wrap; Layout.fillWidth: true } }
                        }
                    }
                    Text { text: "Документы (точная версия: номер и контрольная сумма файла)"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; Layout.topMargin: 8 }
                    Repeater {
                        model: dm
                        delegate: RowLayout { id: drow; required property int index; required property var model
                            Layout.fillWidth: true; spacing: 8
                            Text { text: drow.model.label + "  ·  версия " + drow.model.version_no; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; Layout.fillWidth: true; wrapMode: Text.Wrap }
                            VButton { kind: "ghost"; compact: true; iconName: "trash-2"; Accessible.name: "Убрать документ " + (drow.index + 1); onClicked: { dm.remove(drow.index); editor.editRev++ } } }
                    }
                    RowLayout { Layout.fillWidth: true; spacing: 8
                        VSelect { id: docSel; objectName: "passportDocSel"; label: "Документ проекта"; model: editor.docOpts; Layout.fillWidth: true
                                  hint: editor.docOpts.length === 0 ? "В проекте нет документов с загруженными версиями (или нет связи с сервером, чтобы получить их список)." : "Будет закреплена текущая версия документа." }
                        VButton { objectName: "passportAddDoc"; text: "Добавить документ"; iconName: "paperclip"; kind: "soft"; compact: true; Layout.alignment: Qt.AlignBottom
                                  enabled: docSel.currentValue !== ""
                                  onClicked: { var d = editor.docOpts[docSel.currentIndex]
                                               for (var i = 0; i < dm.count; i++) if (dm.get(i).document_id === d.id) dm.remove(i)
                                               dm.append({ document_id: d.id, version_no: d.version_no, label: d.label.replace(/ \(версия \d+\)$/, "") }); editor.editRev++ } } }
                    Item { implicitHeight: 8 }
                }
            }
            Banner { tone: "danger"; text: editor.error; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 10; Layout.fillWidth: true }
            Rectangle { Layout.fillWidth: true; Layout.topMargin: 12; height: 1; color: Theme.border }
            RowLayout {
                Layout.fillWidth: true; Layout.margins: 16; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; spacing: 8
                Text { visible: !editor.check.ok; text: editor.check.message || ""; color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Item { Layout.fillWidth: true; visible: editor.check.ok }
                VButton { text: "Отмена"; kind: "ghost"; onClicked: editor.close() }
                VButton { objectName: "passportSave"; text: editor.working ? "Сохранение…" : "Сохранить паспорт"; kind: "primary"; iconName: "check"
                          enabled: !editor.working && editor.check.ok; onClicked: editor.save() }
            }
        }
    }
}
