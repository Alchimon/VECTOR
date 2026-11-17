import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Вкладка «Наряды» раздела «Производство» (С5 шаги 3–6; D-36).
//
//  * Выпуск наряда — РП проекта (мастер, A-42; его замещающий — D-35), резерв — руководитель отдела: по
//    УТВЕРЖДЁННОМУ ТП, количество изделий, исполнители операций (можно позже). При выпуске сервер фиксирует
//    неизменяемый снимок — состав изделия без цен × количество и операции с нормой на наряд (SHA-256); карточка
//    показывает, цел ли снимок.
//  * Замена в составе — ГИП проекта или технолог: «Заменить» у строки → новая позиция, количество на наряд,
//    причина. Сервер сверяет хэш состава, который видел пользователь (кто-то успел заменить раньше — конфликт),
//    и добавляет неизменяемую запись замены; снимок не переписывается.
//  * Материалы — сколько требуется по действующему составу и сколько выдано со склада ИМЕННО на этот наряд
//    (раздел «Склад» → «Выдать» с выбором наряда; сторно учитывается). Не складские позиции закрытию не мешают (A-43).
//  * Закрыть — когда все операции выполнены и материалы выданы; отменить — с причиной.
//  Всё, кроме просмотра, — только при связи: сервер заново проверяет права, статус и версию.
MasterDetail {
    id: page
    property var shell
    property string selectedId: ""
    tabsName: "prodTabs"
    readonly property var pv: backend.productionOverview
    readonly property var all: pv.work_orders || []
    readonly property var items: all.filter(w => search === "" || (w.label + " " + w.title + " " + w.kit_label).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: all.find(w => w.id === selectedId) || null
    // «10.5000» → «10.5», «100» → «100» (для поля ввода количества).
    function plain(x) { var t = String(x === undefined || x === null ? "" : x); return t.indexOf(".") >= 0 ? t.replace(/0+$/, "").replace(/\.$/, "") : t }
    title: "Производство"
    subtitle: "Нарядов в работе: " + ((pv.counts || {}).wo_open || 0) + "  ·  " + (pv.as_of || "")
    hasSelection: sel !== null
    emptyIcon: "clipboard-list"; emptyTitle: "Нарядов нет"
    emptyText: pv.empty_orders || ""
    emptyAction: pv.can_release === true ? "Выпустить наряд" : ""
    onEmptyActionTriggered: releaseForm.openFor()
    noSelectionText: "Выберите наряд"
    headerActions: [
        VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() },
        VButton { objectName: "btnReleaseWo"; visible: page.pv.may_release === true; enabled: page.pv.can_release === true
                  text: "Выпустить наряд"; kind: "primary"; iconName: "plus"; onClicked: releaseForm.openFor() }
    ]
    list.model: items
    list.delegate: ListRow {
        title: modelData.label + "  ·  " + modelData.title
        subtitle: modelData.kit_label + "  ·  " + modelData.qty_label + "  ·  " + modelData.progress_label
        iconName: "clipboard-list"
        chipText: modelData.status_label; chipTone: modelData.status_tone
        pending: modelData.pending === true
        selected: modelData.id === page.selectedId
        onClicked: page.selectedId = modelData.id
    }

    // ---------- карточка
    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Rectangle { width: 52; height: 52; radius: 14; color: Theme.primarySoft
            Icon { anchors.centerIn: parent; name: "clipboard-list"; size: 26; color: Theme.primary } }
        ColumnLayout { spacing: 4; Layout.fillWidth: true
            Text { text: sel ? sel.label + "  ·  " + sel.kit_label : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { text: sel ? sel.title : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true } }
        VChip { text: sel ? sel.status_label : ""; tone: sel ? sel.status_tone : "neutral" }
        VChip { objectName: "woSnapshotChip"; text: sel && sel.snapshot_ok ? "Снимок цел" : "Снимок повреждён"; tone: sel && sel.snapshot_ok ? "success" : "danger"
                iconName: sel && sel.snapshot_ok ? "shield-check" : "triangle-alert" }
    }
    Banner { visible: sel !== null && !sel.snapshot_ok; tone: "danger"
             text: "Снимок наряда не совпадает с контрольной суммой (SHA-256) — данные изменены в обход программы. Работать по нему нельзя: сообщите администратору." }
    Banner { visible: sel !== null && sel.cancel_reason !== ""; tone: "danger"; text: sel ? "Наряд отменён: " + sel.cancel_reason : "" }
    Banner { visible: sel !== null && sel.pending === true; tone: "info"
             text: "Есть события исполнителей, которые ещё не подтвердил сервер (очередь синхронизации)." }
    Banner { visible: sel !== null && sel.manage === true && page.pv.online !== true && (sel.status === "released" || sel.status === "in_progress" || sel.status === "completed"); tone: "warning"
             text: "Выпуск, назначение, замены, закрытие и отмена — только при связи с сервером. Ниже — состояние на момент последнего обновления." }
    Banner { objectName: "woCloseHint"; visible: sel !== null && sel.manage === true && sel.status !== "closed" && sel.status !== "cancelled" && sel.close_problems.length > 0; tone: "warning"
             text: sel ? "Закрыть пока нельзя: " + sel.close_problems.join(" ") : "" }
    Flow {
        Layout.fillWidth: true; spacing: 8
        visible: sel !== null && sel.manage === true
        VButton { objectName: "btnCloseWo"; visible: sel !== null && sel.status !== "closed" && sel.status !== "cancelled"; enabled: sel !== null && sel.can_close === true
                  text: "Закрыть наряд"; kind: "primary"; iconName: "circle-check"; onClicked: closeForm.open() }
        VButton { objectName: "btnCancelWo"; visible: sel !== null && (sel.status === "released" || sel.status === "in_progress"); enabled: sel !== null && sel.can_cancel === true
                  text: "Отменить наряд"; kind: "danger"; iconName: "x"; onClicked: cancelForm.openFor() }
    }

    SectionTitle { text: "Реквизиты"; iconName: "info" }
    KeyValue { k: "Изделие"; v: sel ? sel.kit_label : "" }
    KeyValue { k: "Количество"; v: sel ? sel.qty_label : "" }
    KeyValue { k: "Техпроцесс"; v: sel ? sel.tp_label : "" }
    KeyValue { k: "Спецификация"; v: sel ? sel.spec_label : "" }
    KeyValue { k: "Выпустил"; v: sel ? sel.released_label : "" }
    KeyValue { visible: sel !== null && sel.closed_label !== ""; k: "Закрыл"; v: sel ? sel.closed_label : "" }
    KeyValue { k: "Выполнение"; v: sel ? sel.progress_label + " · факт " + sel.fact_label : "" }
    KeyValue { k: "SHA-256 снимка"; v: sel ? sel.snapshot_hash : ""; muted: true }

    SectionTitle { text: "Операции"; iconName: "list-checks" }
    Repeater {
        model: sel ? sel.operations : []
        delegate: Rectangle {
            required property var modelData
            objectName: "woOpRow"
            Layout.fillWidth: true; implicitHeight: orow.implicitHeight + 16; radius: Theme.radiusS; color: Theme.surfaceAlt
            RowLayout { id: orow; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.margins: 12; spacing: 10
                ColumnLayout { spacing: 2; Layout.fillWidth: true
                    Text { text: modelData.title + (modelData.work_center ? "  ·  " + modelData.work_center : ""); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Text { text: "Исполнитель: " + modelData.assignee_name + "  ·  норма " + modelData.norm_label + "  ·  факт " + modelData.fact_label
                                 + (modelData.qty_done !== null && modelData.qty_done !== undefined ? "  ·  выполнено " + modelData.qty_done + " шт." : "")
                                 + (modelData.problem_count > 0 ? "  ·  проблем: " + modelData.problem_count : "")
                           color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Repeater {
                        model: modelData.events.filter(e => e.kind === "problem").slice(-2)
                        delegate: Text { required property var modelData
                            text: "⚠ " + modelData.when + " — " + modelData.note + (modelData.who ? " (" + modelData.who + ")" : "")
                            color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    } }
                VChip { visible: modelData.pending; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                VChip { text: modelData.state_label; tone: modelData.state_tone }
                VButton { objectName: "btnAssignOp"; visible: sel !== null && sel.manage === true && modelData.state !== "done" && (sel.status === "released" || sel.status === "in_progress")
                          enabled: sel !== null && sel.can_assign === true; text: "Назначить"; compact: true; kind: "soft"; iconName: "user-check"
                          onClicked: assignForm.openFor(modelData) }
            }
        }
    }

    SectionTitle { text: "Состав (действующий: снимок + замены)"; iconName: "boxes" }
    Repeater {
        model: sel ? sel.spec : []
        delegate: RowLayout {
            required property var modelData
            objectName: "woSpecRow"
            Layout.fillWidth: true; spacing: 10
            Text { text: modelData.code || "—"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.preferredWidth: 140; elide: Text.ElideRight }
            Text { text: modelData.name + (modelData.substituted ? "  (замена)" : ""); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; Layout.fillWidth: true; wrapMode: Text.Wrap }
            Text { text: modelData.qty_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.features: { "tnum": 1 } }
            VButton { objectName: "btnSubstitute"; visible: sel !== null && sel.may_substitute === true; enabled: sel !== null && sel.can_substitute === true
                      text: "Заменить"; compact: true; kind: "ghost"; iconName: "arrow-left-right"; onClicked: subForm.openFor(modelData) }
        }
    }
    SectionTitle { visible: sel !== null && sel.substitutions.length > 0; text: "Замены"; iconName: "arrow-left-right" }
    Repeater {
        model: sel ? sel.substitutions : []
        delegate: ColumnLayout { required property var modelData
            Layout.fillWidth: true; spacing: 2
            Text { text: modelData.old_label + "  →  " + modelData.new_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
            Text { text: "Причина: " + modelData.reason + "  ·  " + modelData.who + ", " + modelData.when; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
    }

    SectionTitle { text: "Материалы со склада"; iconName: "package-check" }
    Text { text: "Выдача — в разделе «Склад»: «Выдать» с выбором этого наряда. Сторно выдачи уменьшает «выдано»."
           color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
    Repeater {
        model: sel ? sel.materials : []
        delegate: RowLayout {
            required property var modelData
            objectName: "woMaterialRow"
            Layout.fillWidth: true; spacing: 10
            Text { text: modelData.code || "—"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.preferredWidth: 140; elide: Text.ElideRight }
            Text { text: modelData.name; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; Layout.fillWidth: true; wrapMode: Text.Wrap }
            Text { text: "нужно " + modelData.required_label + " · выдано " + modelData.issued_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS; font.features: { "tnum": 1 } }
            VChip { text: modelData.state_label; tone: modelData.tone }
        }
    }
    Item { implicitHeight: 8 }

    // ---------- формы
    // Выпуск наряда: ТП, количество, исполнители по операциям (список исполнителей приходит с сервера).
    FormDialog {
        id: releaseForm
        objectName: "woReleaseForm"
        property string tag: "wo-release"
        property var assign: ({})
        title: "Выпустить наряд"
        subtitle: "По утверждённому ТП. При выпуске фиксируется неизменяемый снимок состава (без цен) и операций."
        confirmText: "Выпустить"
        note: "Исполнители получат уведомление; назначить или сменить исполнителя можно и позже."
        readonly property var tp: (page.pv.release_tps || [])[rTp.currentIndex] || null
        canConfirm: tp !== null && /^\d+$/.test(rQty.text.trim()) && parseInt(rQty.text.trim()) >= 1
        function openFor() { rTp.currentIndex = 0; rQty.text = "1"; assign = ({}); open() }
        onConfirmed: { working = true; error = ""; backend.releaseWo(tag, tp.id, rQty.text.trim(), assign) }
        VSelect { id: rTp; objectName: "woTp"; label: "Техпроцесс (утверждённый)"; model: page.pv.release_tps || [] }
        VField { id: rQty; objectName: "woQty"; label: "Количество изделий"; required: true
                 error: text.trim() === "" || /^\d+$/.test(text.trim()) ? "" : "Целое число от 1." }
        Repeater {
            model: releaseForm.tp ? releaseForm.tp.operations : []
            delegate: VSelect { required property var modelData
                label: "Исполнитель: " + modelData.op_no + ". " + modelData.name
                model: page.pv.staff_options || []
                onCurrentValueChanged: { var a = Object.assign({}, releaseForm.assign); a[String(modelData.op_no)] = currentValue; releaseForm.assign = a } }
        }
    }
    Connections {
        target: backend
        function onCommandDone(t, r) {
            if (t !== releaseForm.tag) return
            releaseForm.working = false
            if (r.status === "accepted") { releaseForm.close(); if (r.result && r.result.work_order) page.selectedId = r.result.work_order.id }
            else releaseForm.error = r.message_ru || "Наряд не выпущен."
        }
    }

    FormDialog {
        id: assignForm
        objectName: "woAssignForm"
        property var op: null
        title: "Исполнитель операции"
        subtitle: op ? op.title : ""
        confirmText: "Назначить"
        function openFor(o) {
            op = o
            var opts = page.pv.staff_options || []
            aWho.currentIndex = Math.max(0, opts.findIndex(x => x.id === (o.assignee_id || "")))
            open()
        }
        onConfirmed: aCmd.run("wo.assign", { id: op.id, assignee_id: aWho.currentValue === "" ? null : aWho.currentValue }, op.version)
        VSelect { id: aWho; objectName: "woAssignee"; label: "Исполнитель (роль «Производство»)"; model: page.pv.staff_options || [] }
    }
    Cmd { id: aCmd; dialog: assignForm; tag: "wo-assign" }

    FormDialog {
        id: subForm
        objectName: "woSubstituteForm"
        property var line: null
        title: "Замена в составе наряда"
        subtitle: line ? "Было: " + (line.code ? line.code + " · " : "") + line.name + " — " + line.qty_label : ""
        confirmText: "Заменить"
        note: "Снимок не переписывается: добавляется запись замены (кто, когда, почему). Количество — на весь наряд. Если кто-то успел заменить раньше — будет конфликт, и вы увидите новый состав."
        canConfirm: sName.text.trim() !== "" && sUnit.text.trim() !== "" && sQty.text.trim() !== "" && sQty.error === "" && sReason.text.trim() !== ""
        function openFor(l) { line = l; sCode.text = l.code || ""; sName.text = l.name || ""; sUnit.text = l.unit || ""; sQty.text = page.plain(l.qty); sReason.text = ""; open() }
        onConfirmed: sCmd.run("wo.substitute", { id: sel.id, line_id: line.line_id, code: sCode.text.trim(), name: sName.text.trim(), unit: sUnit.text.trim(),
                                                 qty: backend.normalizeQty(sQty.text), reason: sReason.text.trim(), preview_hash: sel.spec_hash }, sel.version)
        RowLayout { Layout.fillWidth: true; spacing: 12
            VField { id: sCode; objectName: "subCode"; label: "Код (обозначение)"; placeholder: "Как в номенклатуре склада" }
            VField { id: sUnit; objectName: "subUnit"; label: "Ед."; required: true; Layout.preferredWidth: 90; Layout.fillWidth: false } }
        VField { id: sName; objectName: "subName"; label: "Наименование"; required: true }
        VField { id: sQty; objectName: "subQty"; label: "Количество на наряд"; required: true; error: backend.qtyProblem(text) }
        VField { id: sReason; objectName: "subReason"; label: "Причина"; required: true; placeholder: "Нет на складе, согласовано с ГИП" }
    }
    Cmd { id: sCmd; dialog: subForm; tag: "wo-substitute" }

    FormDialog {
        id: closeForm
        objectName: "woCloseForm"
        title: "Закрыть наряд"
        subtitle: sel ? sel.label + " · " + sel.title : ""
        confirmText: "Закрыть наряд"
        note: "Сервер проверит: все операции выполнены, материалы действующего состава выданы со склада на этот наряд."
        onConfirmed: cCmd.run("wo.close", { id: sel.id }, sel.version)
    }
    Cmd { id: cCmd; dialog: closeForm; tag: "wo-close" }

    FormDialog {
        id: cancelForm
        objectName: "woCancelForm"
        title: "Отменить наряд"
        subtitle: sel ? sel.label + " · " + sel.title : ""
        confirmText: "Отменить наряд"; confirmKind: "danger"
        note: "Выданные материалы не возвращаются автоматически — оформите сторно выдачи на складе."
        canConfirm: xReason.text.trim() !== ""
        function openFor() { xReason.text = ""; open() }
        onConfirmed: xCmd.run("wo.cancel", { id: sel.id, reason: xReason.text.trim() }, sel.version)
        VField { id: xReason; objectName: "woCancelReason"; label: "Причина"; required: true }
    }
    Cmd { id: xCmd; dialog: cancelForm; tag: "wo-cancel" }
}
