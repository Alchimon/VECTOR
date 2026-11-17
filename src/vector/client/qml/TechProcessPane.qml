import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Вкладка «Техпроцессы» раздела «Производство» (С5 шаги 1–2; D-36).
//
//  * Технолог создаёт черновик ТП по комплекту (изделию) проекта в работе: операции (№, наименование, участок,
//    норма в минутах на ЕДИНИЦУ изделия) и спецификация — зафиксированная версия расчёта комплекта (состав без цен).
//    Черновик сохраняется через очередь — можно без связи; у комплекта один черновик, ревизии нумеруются.
//  * Утверждает ТП другой технолог (не автор — «четыре глаза») или руководитель отдела, только при связи.
//    Утверждается конкретное содержимое (SHA-256): утверждённый ТП не меняется — нужна новая ревизия.
//  * По утверждённому ТП РП проекта выпускает наряд (вкладка «Наряды»).
MasterDetail {
    id: page
    property var shell
    property string selectedId: ""
    tabsName: "prodTabs"
    readonly property var pv: backend.productionOverview
    readonly property var all: pv.tps || []
    readonly property var items: all.filter(t => search === "" || (t.title + " " + t.kit_label).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: all.find(t => t.id === selectedId) || null
    title: "Производство"
    subtitle: "Техпроцессы · черновиков " + ((pv.counts || {}).tp_draft || 0) + "  ·  " + (pv.as_of || "")
    hasSelection: sel !== null
    emptyIcon: "list-checks"; emptyTitle: "Техпроцессов нет"
    emptyText: pv.empty_tps || ""
    emptyAction: pv.can_tp_create === true ? "Новый ТП" : ""
    onEmptyActionTriggered: editor.openFor(null)
    noSelectionText: "Выберите технологический процесс"
    headerActions: [
        VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() },
        VButton { objectName: "btnNewTp"; visible: page.pv.is_technologist === true; enabled: page.pv.can_tp_create === true
                  text: "Новый ТП"; kind: "primary"; iconName: "plus"; onClicked: editor.openFor(null) }
    ]
    list.model: items
    list.delegate: ListRow {
        title: modelData.title + "  ·  " + modelData.revision_label
        subtitle: modelData.kit_label + "  ·  операций " + modelData.op_count
        iconName: "list-checks"
        chipText: modelData.status_label; chipTone: modelData.status_tone
        pending: modelData.pending === true
        selected: modelData.id === page.selectedId
        onClicked: page.selectedId = modelData.id
    }

    // ---------- карточка
    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Rectangle { width: 52; height: 52; radius: 14; color: Theme.primarySoft
            Icon { anchors.centerIn: parent; name: "list-checks"; size: 26; color: Theme.primary } }
        ColumnLayout { spacing: 4; Layout.fillWidth: true
            Text { text: sel ? sel.kit_label + "  ·  " + sel.revision_label : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { text: sel ? sel.title : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true } }
        VChip { text: sel ? sel.status_label : ""; tone: sel ? sel.status_tone : "neutral" }
    }
    Banner { visible: sel !== null && sel.pending === true; tone: "info"
             text: "Есть изменения, которые ещё не подтвердил сервер (очередь синхронизации). Утвердить ТП можно после отправки." }
    Banner { visible: sel !== null && !sel.approval_valid; tone: "danger"
             text: "Содержимое ТП не совпадает с утверждённым (изменено в обход программы) — по нему нельзя выпускать наряды. Сообщите администратору." }
    Banner { visible: sel !== null && sel.status === "draft" && sel.problems.length > 0; tone: "warning"
             text: sel ? "Для утверждения не хватает: " + sel.problems.join(" ") : "" }
    Banner { visible: sel !== null && sel.status === "draft" && sel.is_own === true; tone: "info"
             text: "Вы автор черновика — утверждает другой технолог или руководитель отдела." }
    Banner { visible: sel !== null && sel.status === "draft" && sel.may_approve === true && page.pv.online !== true; tone: "warning"
             text: "Утвердить ТП можно только при связи с сервером." }
    Banner { visible: sel !== null && sel.status === "approved"; tone: "success"; text: sel ? sel.approved_label + ". Изменить утверждённый ТП нельзя — создайте новую ревизию." : "" }
    Flow {
        Layout.fillWidth: true; spacing: 8
        visible: sel !== null
        VButton { objectName: "btnEditTp"; visible: sel !== null && sel.can_edit === true; text: "Изменить"; iconName: "pencil"; onClicked: editor.openFor(sel) }
        VButton { objectName: "btnApproveTp"; visible: sel !== null && sel.may_approve === true; enabled: sel !== null && sel.can_approve === true
                  text: "Утвердить"; kind: "primary"; iconName: "circle-check"; onClicked: approveForm.open() }
        VButton { objectName: "btnNewRevision"; visible: sel !== null && sel.status === "approved" && page.pv.is_technologist === true
                  enabled: page.pv.kit_options.some(k => sel && k.id === sel.kit_id)
                  text: "Новая ревизия"; iconName: "copy"; onClicked: editor.openFor(sel, true) }
    }

    SectionTitle { text: "Реквизиты"; iconName: "info" }
    KeyValue { k: "Изделие (комплект)"; v: sel ? sel.kit_label : "" }
    KeyValue { k: "Спецификация"; v: sel ? sel.spec_label : "" }
    KeyValue { k: "Норма"; v: sel ? sel.norm_label : "" }
    KeyValue { k: "Автор"; v: sel ? sel.author_name : "" }
    KeyValue { visible: sel !== null && sel.note !== ""; k: "Примечание"; v: sel ? sel.note : "" }
    KeyValue { k: "SHA-256 содержимого"; v: sel ? sel.content_hash : ""; muted: true }

    SectionTitle { text: "Операции"; iconName: "list-checks" }
    Repeater {
        model: sel ? sel.operations : []
        delegate: RowLayout {
            required property var modelData
            objectName: "tpOpRow"
            Layout.fillWidth: true; spacing: 10
            Text { text: modelData.op_no; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; Layout.preferredWidth: 44; font.features: { "tnum": 1 } }
            ColumnLayout { spacing: 0; Layout.fillWidth: true
                Text { text: modelData.name; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { visible: modelData.work_center !== "" || modelData.note !== ""; text: [modelData.work_center, modelData.note].filter(x => x).join("  ·  ")
                       color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
            Text { text: modelData.norm_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.features: { "tnum": 1 } }
        }
    }
    SectionTitle { text: "Состав изделия (из версии расчёта, без цен)"; iconName: "boxes" }
    Text { visible: sel !== null && sel.spec_lines.length === 0; text: "Спецификация не выбрана."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
    Repeater {
        model: sel ? sel.spec_lines : []
        delegate: RowLayout { required property var modelData
            Layout.fillWidth: true; spacing: 10
            Text { text: modelData.code || "—"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.preferredWidth: 140; elide: Text.ElideRight }
            Text { text: modelData.name; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; Layout.fillWidth: true; wrapMode: Text.Wrap }
            Text { text: modelData.qty_label + " на изделие"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS; font.features: { "tnum": 1 } } }
    }
    Item { implicitHeight: 8 }

    FormDialog {
        id: approveForm
        objectName: "tpApproveForm"
        title: "Утвердить ТП"
        subtitle: sel ? sel.kit_label + " · " + sel.title + " (" + sel.revision_label + ")" : ""
        confirmText: "Утвердить"
        note: "Утверждается именно это содержимое (SHA-256). После утверждения ТП не меняется; РП проекта сможет выпускать по нему наряды."
        onConfirmed: apCmd.run("tp.approve", { id: sel.id }, sel.version)
    }
    Cmd { id: apCmd; dialog: approveForm; tag: "tp-approve" }

    // ---------- редактор черновика ТП: реквизиты + таблица операций (ListModel — правка не пересоздаёт строки).
    Popup {
        id: editor
        objectName: "tpEditor"
        property var tp: null            // null — новый ТП
        property bool fresh: true        // создаётся новый ТП (в т. ч. новая ревизия по образцу утверждённого)
        property string tpId: ""
        property string kitId: ""
        property int editRev: 0
        property bool working: false
        property string error: ""
        property string tag: "tp-save"
        readonly property var check: { editRev; return visible ? backend.tpOpsCheck(collect()) : ({ problems: [], by_line: {}, norm_label: "" }) }
        readonly property var kitOpts: page.pv.kit_options || []
        readonly property var specOpts: [{ id: "", label: "не выбрана" }].concat((page.pv.spec_options || []).filter(s => s.kit_id === (fresh ? kitSel.currentValue : kitId)))
        modal: true; focus: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(1040, (Overlay.overlay ? Overlay.overlay.width : 1100) - 48)
        height: Math.min(760, (Overlay.overlay ? Overlay.overlay.height : 800) - 48)
        padding: 0
        closePolicy: Popup.CloseOnEscape
        Overlay.modal: Rectangle { color: Theme.scrim }
        background: Rectangle { color: Theme.surface; radius: Theme.radiusL; border.color: Theme.border }
        ListModel { id: lm }
        function idx(list, id) { for (var i = 0; i < list.length; i++) if (String(list[i].id) === String(id)) return i; return -1 }
        function plain(x) { var t = String(x === undefined || x === null ? "" : x); return t.indexOf(".") >= 0 ? t.replace(/0+$/, "").replace(/\.$/, "") : t }
        // openFor(null) — новый ТП; openFor(tp) — правка черновика; openFor(tp, true) — новая ревизия по образцу.
        function openFor(t, asRevision) {
            fresh = !t || asRevision === true
            tp = t || null; error = ""; working = false
            tpId = fresh ? backend.newId() : t.id
            kitId = t ? t.kit_id : ""
            kitSel.currentIndex = Math.max(0, idx(kitOpts, kitId))
            titleField.text = t ? t.title : ""
            noteField.text = t ? t.note : ""
            lm.clear()
            var ops = t ? t.operations : []
            for (var i = 0; i < ops.length; i++)
                lm.append({ op_no: String(ops[i].op_no), name: ops[i].name, work_center: ops[i].work_center || "", norm_min: plain(ops[i].norm_min), note: ops[i].note || "" })
            if (lm.count === 0) lm.append({ op_no: "10", name: "", work_center: "", norm_min: "", note: "" })
            editRev++
            specSel.currentIndex = Math.max(0, idx(specOpts, t ? t.spec_version_id : ""))
            open()
        }
        function collect() {
            var out = []
            for (var i = 0; i < lm.count; i++) { var r = lm.get(i); out.push({ op_no: r.op_no, name: r.name, work_center: r.work_center, norm_min: r.norm_min, note: r.note }) }
            return out
        }
        function setCell(i, key, value) { lm.setProperty(i, key, value); editRev++ }
        function nextNo() { var m = 0; for (var i = 0; i < lm.count; i++) m = Math.max(m, parseInt(lm.get(i).op_no) || 0); return String(m + 10) }
        function save() {
            working = true; error = ""
            backend.saveTpDraft(tag, { id: tpId, kit_id: fresh ? kitSel.currentValue : kitId, title: titleField.text,
                                       spec_version_id: specSel.currentValue, note: noteField.text, operations: collect() })
        }
        Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== editor.tag) return
                editor.working = false
                if (r.status === "accepted" || r.status === "queued") { editor.close(); page.selectedId = editor.tpId }
                else editor.error = r.message_ru || "ТП не сохранён."
            }
        }
        contentItem: ColumnLayout {
            spacing: 0
            RowLayout {
                Layout.fillWidth: true; Layout.margins: Theme.padL; Layout.bottomMargin: 10; spacing: 12
                Rectangle { width: 40; height: 40; radius: 11; color: Theme.primarySoft
                    Icon { anchors.centerIn: parent; name: "list-checks"; size: 20; color: Theme.primary } }
                ColumnLayout { spacing: 2; Layout.fillWidth: true
                    Text { text: editor.fresh ? (editor.tp ? "Новая ревизия ТП" : "Новый технологический процесс") : (editor.tp ? editor.tp.title : "") + " · черновик"
                           color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsL; font.weight: Font.DemiBold }
                    Text { text: "Норма — минут на ОДНО изделие (0 — если нормы нет); в наряде она умножится на количество. Черновик можно сохранять без связи; утверждает другой технолог или руководитель отдела."
                           color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
                VButton { kind: "ghost"; iconName: "x"; compact: true; onClicked: editor.close(); Accessible.name: "Закрыть" }
            }
            GridLayout {
                Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL
                columns: 2; columnSpacing: 12; rowSpacing: 8
                VSelect { id: kitSel; objectName: "tpKit"; label: "Изделие (комплект проекта) *"; model: editor.kitOpts; enabled: editor.fresh && !editor.tp; Layout.fillWidth: true
                          visible: editor.fresh }
                VSelect { id: specSel; objectName: "tpSpec"; label: "Спецификация (зафиксированная версия расчёта)"; model: editor.specOpts; Layout.fillWidth: true
                          hint: editor.specOpts.length <= 1 ? "У комплекта нет зафиксированных версий расчёта — без спецификации ТП не утвердить." : "" }
                VField { id: titleField; objectName: "tpTitle"; label: "Наименование ТП"; required: true; placeholder: "Сборка шкафа управления"; Layout.fillWidth: true }
                VField { id: noteField; label: "Примечание"; placeholder: "Необязательно"; Layout.fillWidth: true }
            }
            Rectangle {
                Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 12
                implicitHeight: 30; radius: Theme.radiusS; color: Theme.surfaceAlt
                RowLayout { anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 6
                    Repeater {
                        model: [{ t: "№ *", w: 64 }, { t: "Операция *", w: -1 }, { t: "Участок / оборудование", w: 200 }, { t: "Норма, мин/ед. *", w: 120 }, { t: "Примечание", w: 180 }, { t: "", w: 34 }]
                        delegate: Text { required property var modelData
                            text: modelData.t; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.DemiBold
                            Layout.preferredWidth: modelData.w > 0 ? modelData.w : -1; Layout.fillWidth: modelData.w < 0 }
                    }
                }
            }
            ListView {
                id: lv
                objectName: "tpEditorRows"
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL
                clip: true; spacing: 2; model: lm
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    id: rowItem
                    required property int index
                    required property var model
                    readonly property string problem: editor.check.by_line ? (editor.check.by_line[String(index)] || "") : ""
                    width: lv.width; implicitHeight: rc.implicitHeight + 8; radius: Theme.radiusS
                    color: problem !== "" ? Theme.dangerSoft : "transparent"
                    ColumnLayout { id: rc; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                   anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 2
                        RowLayout { Layout.fillWidth: true; spacing: 6
                            CalcCell { Layout.preferredWidth: 64; num: true; text: rowItem.model.op_no; Accessible.name: "№ операции " + (rowItem.index + 1)
                                       onTextEdited: editor.setCell(rowItem.index, "op_no", text) }
                            CalcCell { Layout.fillWidth: true; text: rowItem.model.name; placeholder: "Наименование операции"; Accessible.name: "Операция " + (rowItem.index + 1)
                                       onTextEdited: editor.setCell(rowItem.index, "name", text) }
                            CalcCell { Layout.preferredWidth: 200; text: rowItem.model.work_center; Accessible.name: "Участок " + (rowItem.index + 1)
                                       onTextEdited: editor.setCell(rowItem.index, "work_center", text) }
                            CalcCell { Layout.preferredWidth: 120; num: true; text: rowItem.model.norm_min; placeholder: "0"; Accessible.name: "Норма " + (rowItem.index + 1)
                                       onTextEdited: editor.setCell(rowItem.index, "norm_min", text) }
                            CalcCell { Layout.preferredWidth: 180; text: rowItem.model.note; Accessible.name: "Примечание " + (rowItem.index + 1)
                                       onTextEdited: editor.setCell(rowItem.index, "note", text) }
                            VButton { kind: "ghost"; compact: true; iconName: "trash-2"; Layout.preferredWidth: 34; Accessible.name: "Удалить операцию " + (rowItem.index + 1)
                                      onClicked: { lm.remove(rowItem.index); editor.editRev++ } }
                        }
                        Text { visible: rowItem.problem !== ""; text: rowItem.problem; color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsXs; leftPadding: 4; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 8; spacing: 8
                VButton { objectName: "tpAddOp"; text: "Добавить операцию"; iconName: "plus"; kind: "soft"; compact: true
                          onClicked: { lm.append({ op_no: editor.nextNo(), name: "", work_center: "", norm_min: "", note: "" }); editor.editRev++; lv.positionViewAtEnd() } }
                Item { Layout.fillWidth: true }
                Text { objectName: "tpEditorNorm"; text: "Норма: " + (editor.check.norm_label || "—"); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
            }
            Banner { tone: "danger"; text: editor.error; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 10; Layout.fillWidth: true }
            Rectangle { Layout.fillWidth: true; Layout.topMargin: 12; height: 1; color: Theme.border }
            RowLayout {
                Layout.fillWidth: true; Layout.margins: 16; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; spacing: 8
                Text { visible: (editor.check.problems || []).length > 0; text: "Замечаний: " + (editor.check.problems || []).length + ". Исправьте строки, отмеченные красным."
                       color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Item { Layout.fillWidth: true; visible: (editor.check.problems || []).length === 0 }
                VButton { text: "Отмена"; kind: "ghost"; onClicked: editor.close() }
                VButton { objectName: "tpSave"; text: editor.working ? "Сохранение…" : "Сохранить черновик"; kind: "primary"; iconName: "check"
                          enabled: !editor.working && titleField.text.trim() !== "" && (editor.check.problems || []).length === 0
                                   && (!editor.fresh || kitSel.currentValue !== "")
                          onClicked: editor.save() }
            }
        }
    }
}
