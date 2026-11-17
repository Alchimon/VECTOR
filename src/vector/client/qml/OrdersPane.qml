import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Вкладка «Заказы поставщикам» раздела «Закупка» (сценарий С4, шаг 5; D-32, D-33).
//
//  * Черновик готовит закупка — можно без связи (очередь). Согласование, размещение, отмена, закрытие и приём
//    поставки — только при связи: сервер заново проверяет права, статус, версию и количества.
//  * Согласование обязательно (D-33, A-34): закупка отправляет → роль «Согласование закупок» согласует или
//    возвращает с причиной (не свой заказ — «четыре глаза») → закупка размещает ровно согласованное.
//    На согласовании и после него условия заморожены; изменить — «Отозвать» и отправить заново.
//  * НДС раздельно (A-35): без НДС, НДС, с НДС — в карточке; у строки — ставка и НДС.
//  * Размещённый заказ не меняется (строки заморожены контрольной суммой); у него можно уточнить только номер
//    счёта поставщика, ожидаемую дату и примечание. Нужны другие условия — отмена/закрытие и новый заказ.
//  * Поставка — не статус: «ждём / частично / полностью» считается по приходам на склад без сторно.
//    Приём делает склад по строке заказа — не больше, чем ещё ждём; излишек — обычным приходом.
//  * Цены, суммы и НДС видят закупка, бухгалтерия и согласующий; склад — количества и ставки.
MasterDetail {
    id: page
    property var shell
    property string selectedId: ""
    tabsName: "procTabs"
    readonly property var pov: backend.ordersOverview
    readonly property var all: pov.orders || []
    readonly property var items: all.filter(o => search === "" || (o.title + " " + o.supplier_label + " " + o.supplier_ref).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: all.find(o => o.id === selectedId) || null
    readonly property var sov: backend.stockOverview
    readonly property var whChoices: (sov.warehouses || []).filter(w => w.active === true).map(w => ({ id: w.id, label: w.code + " · " + w.name }))
    // Строки потребностей с выбранной позицией — для привязки строки заказа (D-32).
    readonly property var needLines: {
        var out = []
        var ns = backend.needsOverview.needs || []
        for (var i = 0; i < ns.length; i++)
            for (var j = 0; j < ns[i].lines.length; j++) {
                var l = ns[i].lines[j]
                if (l.item_id) out.push({ id: ns[i].id + "/" + l.line_id, item_id: l.item_id,
                                          label: ns[i].title + " · " + l.pos + ". не покрыто " + l.open_label })
            }
        return out
    }
    readonly property string offlineWhy: "Согласование, размещение, отмена, закрытие и приём поставки выполняются только при связи с сервером. Черновики можно готовить и без связи."
    title: "Закупка"
    subtitle: (pov.counts ? "черновиков " + pov.counts.draft + " · на согласовании " + pov.counts.on_approval + " · ждём поставку " + pov.counts.waiting : "") + "  ·  " + (pov.as_of || "")
    hasSelection: sel !== null
    emptyIcon: "clipboard-list"; emptyTitle: "Заказов нет"
    emptyText: pov.empty_text || ""
    emptyAction: pov.can_create === true && all.length === 0 ? "Новый заказ" : ""
    onEmptyActionTriggered: editor.openFor(null)
    noSelectionText: "Выберите заказ"
    headerActions: [
        VSegmented { objectName: "poFilter"; current: page.pov.filter || "active"; onPicked: id => backend.setOrdersFilter(id)
                     model: [{ id: "active", label: "В работе", icon: "loader" }, { id: "approval", label: "Согласование", icon: "shield-check" },
                             { id: "draft", label: "Черновики", icon: "pencil" },
                             { id: "placed", label: "Размещённые", icon: "send" }, { id: "closed", label: "Архив", icon: "archive" }] },
        VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() },
        VButton { objectName: "btnNewOrder"; text: "Новый заказ"; kind: "primary"; iconName: "plus"
                  visible: page.pov.can_create === true; onClicked: editor.openFor(null) }
    ]
    list.model: items
    list.delegate: ListRow {
        title: modelData.title + "  ·  " + modelData.supplier_label
        subtitle: modelData.line_count_label + "  ·  " + modelData.total_label
        iconName: "clipboard-list"
        // Черновик в согласовании показывает состояние согласования, размещённый — поставку.
        chipText: modelData.status === "placed" ? modelData.delivery_label
                  : (modelData.status === "draft" && modelData.approval_state ? modelData.approval_label : modelData.status_label)
        chipTone: modelData.status === "placed" ? modelData.delivery_tone
                  : (modelData.status === "draft" && modelData.approval_state ? modelData.approval_tone : modelData.status_tone)
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
            Text { text: sel ? sel.supplier_label : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { text: sel ? sel.title : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true } }
        VChip { text: sel ? sel.status_label : ""; tone: sel ? sel.status_tone : "neutral" }
        VChip { visible: sel !== null && sel.delivery_label !== ""; text: sel ? sel.delivery_label : ""; tone: sel ? sel.delivery_tone : "neutral" }
        VChip { objectName: "poApprovalChip"; visible: sel !== null && sel.status === "draft" && !!sel.approval_state
                text: sel ? sel.approval_label : ""; tone: sel ? sel.approval_tone : "neutral" }
    }
    Banner { visible: sel !== null && sel.pending === true; tone: "info"
             text: "Есть изменения, которые ещё не подтвердил сервер (очередь синхронизации). Разместить заказ можно после отправки." }
    Banner { visible: sel !== null && sel.hash_warning !== ""; tone: "danger"; text: sel ? sel.hash_warning : "" }
    Banner { visible: sel !== null && sel.status !== "closed" && sel.status !== "cancelled" && page.pov.online !== true; tone: "warning"; text: page.offlineWhy }
    Banner { visible: sel !== null && sel.problem !== ""; tone: "warning"; text: sel ? "Черновик не пройдёт проверку: " + sel.problem : "" }
    // Согласование (D-33).
    Banner { visible: sel !== null && sel.return_reason !== ""; tone: "danger"
             text: sel ? "Возвращён на доработку: " + sel.return_reason + ". Исправьте и отправьте на согласование снова." : "" }
    Banner { visible: sel !== null && sel.approval_stale === true; tone: "danger"
             text: "Содержание заказа не совпадает с согласованным (изменено в обход программы) — разместить нельзя. Отзовите и отправьте на согласование заново, сообщите администратору." }
    Banner { visible: sel !== null && sel.on_approval === true && page.pov.can_create === true; tone: "info"
             text: "Заказ на согласовании: условия не меняются. Чтобы изменить — «Отозвать с согласования»." }
    Banner { visible: sel !== null && sel.is_own_for_approver === true; tone: "info"
             text: "Вы создали или отправили этот заказ — согласует другой сотрудник с ролью «Согласование закупок»." }
    Banner { visible: sel !== null && sel.problem === "" && (sel.ready_problems || []).length > 0 && sel.can_edit === true; tone: "warning"
             text: sel ? "Для согласования не хватает: " + (sel.ready_problems || []).join(" ") : "" }
    // Действия — подсказки по ролям и связи; решение принимает сервер.
    Flow {
        Layout.fillWidth: true; spacing: 8
        visible: sel !== null
        VButton { objectName: "btnEditOrder"; visible: sel !== null && sel.can_edit === true; text: "Изменить"; iconName: "pencil"; onClicked: editor.openFor(sel) }
        VButton { objectName: "btnEditInfo"; visible: sel !== null && sel.can_edit_info === true; text: "Уточнить счёт и дату"; iconName: "pencil"; onClicked: infoForm.openFor(sel) }
        VButton { objectName: "btnSubmit"; visible: sel !== null && sel.can_edit === true; enabled: sel !== null && sel.can_submit === true
                  text: "На согласование"; kind: "primary"; iconName: "shield-check"; onClicked: submitForm.openFor(sel) }
        VButton { objectName: "btnWithdraw"; visible: sel !== null && sel.frozen === true && page.pov.can_create === true
                  enabled: sel !== null && sel.can_withdraw === true; text: "Отозвать с согласования"; iconName: "undo-2"; onClicked: withdrawForm.openFor(sel) }
        VButton { objectName: "btnApprove"; visible: sel !== null && sel.on_approval === true && page.pov.is_approver === true
                  enabled: sel !== null && sel.can_approve === true; text: "Согласовать"; kind: "primary"; iconName: "circle-check"; onClicked: approveForm.openFor(sel) }
        VButton { objectName: "btnReturn"; visible: sel !== null && sel.on_approval === true && page.pov.is_approver === true
                  enabled: sel !== null && sel.can_return === true; text: "Вернуть на доработку"; kind: "danger"; iconName: "rotate-ccw"; onClicked: returnForm.openFor(sel) }
        VButton { objectName: "btnPlace"; visible: sel !== null && sel.status === "draft" && sel.approval_state === "approve" && page.pov.can_create === true
                  enabled: sel !== null && sel.can_place === true
                  text: "Разместить у поставщика"; kind: "primary"; iconName: "send"; onClicked: placeForm.open() }
        VButton { objectName: "btnClose"; visible: sel !== null && sel.status === "placed" && page.pov.can_create === true; enabled: sel !== null && sel.can_close === true
                  text: "Закрыть заказ"; iconName: "circle-check"; onClicked: closeForm.openFor(sel) }
        VButton { objectName: "btnCancelOrder"; visible: sel !== null && (sel.status === "draft" || sel.status === "placed") && page.pov.can_create === true
                  enabled: sel !== null && sel.can_cancel === true; text: "Отменить"; kind: "danger"; iconName: "x"; onClicked: cancelForm.openFor(sel) }
    }
    SectionTitle { text: "Реквизиты"; iconName: "info" }
    KeyValue { k: "Поставщик"; v: sel ? sel.supplier_label : "" }
    KeyValue { k: "Счёт / КП поставщика"; v: sel ? sel.supplier_ref : "" }
    KeyValue { k: "Договор"; v: sel ? sel.contract_label : ""; muted: sel !== null && !sel.contract_id }
    KeyValue { k: "Валюта"; v: sel ? sel.currency_label : ""; muted: sel !== null && !sel.currency }
    KeyValue { k: "Цены в счёте"; v: sel ? sel.vat_mode_label : ""; muted: sel !== null && !sel.vat_mode }
    KeyValue { k: "Итог"; v: sel ? sel.total_label : ""; muted: sel !== null && sel.prices_visible !== true }
    // НДС раздельно (A-35) — тем, кто видит цены; не посчитать — почему.
    KeyValue { objectName: "poVatNet"; visible: sel !== null && !!sel.vat; k: "Без НДС"
               v: sel && sel.vat ? (sel.vat.known ? sel.vat.net_label : "— (" + sel.vat.why + ")") : ""; muted: sel !== null && !!sel.vat && !sel.vat.known }
    KeyValue { objectName: "poVatSum"; visible: sel !== null && !!sel.vat; k: "НДС"
               v: sel && sel.vat ? (sel.vat.known ? sel.vat.vat_label + (sel.vat.by_rate_label ? "  (" + sel.vat.by_rate_label + ")" : "") : "—") : ""
               muted: sel !== null && !!sel.vat && !sel.vat.known }
    KeyValue { objectName: "poVatGross"; visible: sel !== null && !!sel.vat; k: "С НДС"
               v: sel && sel.vat ? (sel.vat.known ? sel.vat.gross_label : "—") : ""; muted: sel !== null && !!sel.vat && !sel.vat.known }
    KeyValue { k: "Ожидаемая поставка"; v: sel ? sel.expected_label : "" }
    KeyValue { k: "Примечание"; v: sel ? sel.note : "" }
    KeyValue { visible: sel !== null && sel.placed_label !== ""; k: "Размещение"; v: sel ? sel.placed_label : "" }
    KeyValue { visible: sel !== null && sel.closed_label !== ""; k: sel && sel.status === "cancelled" ? "Отмена" : "Закрытие"
               v: sel ? sel.closed_label + (sel.close_reason ? " — " + sel.close_reason : "") : "" }
    SectionTitle { text: "Строки заказа"; iconName: "list-checks" }
    Repeater {
        objectName: "poLines"
        model: sel ? sel.lines : []
        delegate: Rectangle {
            required property var modelData
            Layout.fillWidth: true; implicitHeight: lcol.implicitHeight + 20; radius: Theme.radius; color: Theme.surfaceAlt
            ColumnLayout {
                id: lcol
                anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 4
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Text { text: modelData.pos + "."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.preferredWidth: 22 }
                    Text { text: modelData.item_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                    Text { text: modelData.qty_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM; font.weight: Font.Bold }
                    VButton { objectName: "btnReceiveLine"; visible: page.sel !== null && page.sel.can_receive === true && modelData.can_receive === true
                              text: "Принять"; compact: true; kind: "soft"; iconName: "package-check"; onClicked: receiveForm.openFor(modelData) }
                }
                Text { visible: page.sel !== null && page.sel.prices_visible === true
                       text: "Цена " + modelData.price_label + "  ·  сумма " + modelData.amount_label + "  ·  " + modelData.vat_label
                       color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.fillWidth: true; Layout.leftMargin: 32 }
                Text { visible: page.sel !== null && (page.sel.status === "placed" || page.sel.status === "closed")
                       text: "Поставлено " + modelData.received_label + "  ·  ждём " + modelData.remaining_label
                       color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.fillWidth: true; Layout.leftMargin: 32 }
                Text { visible: (modelData.need_label || "") !== ""; text: modelData.need_label || ""
                       color: Theme.info; font.family: Theme.family; font.pixelSize: Theme.fsXs; Layout.fillWidth: true; Layout.leftMargin: 32; wrapMode: Text.Wrap }
                Text { visible: (modelData.note || "") !== ""; text: modelData.note || ""
                       color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; Layout.fillWidth: true; Layout.leftMargin: 32; wrapMode: Text.Wrap }
            }
        }
    }
    Text { visible: page.sel !== null && page.sel.prices_visible !== true; text: page.sel ? "Ставки НДС строк: " + page.sel.lines.map(l => l.vat_rate_label).join(", ") : ""
           color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; Layout.fillWidth: true; wrapMode: Text.Wrap }
    // История согласования (D-33): кто отправил, согласовал, вернул (с причиной), отозвал.
    SectionTitle { visible: page.sel !== null && (page.sel.approval_events || []).length > 0; text: "Согласование"; iconName: "shield-check" }
    Repeater {
        objectName: "poApprovalEvents"
        model: page.sel ? (page.sel.approval_events || []) : []
        delegate: ColumnLayout { required property var modelData
            Layout.fillWidth: true; spacing: 2
            Text { text: modelData.label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold
                   wrapMode: Text.Wrap; Layout.fillWidth: true }
            Text { visible: modelData.comment !== ""; text: modelData.comment; color: Theme.textMuted; font.family: Theme.family
                   font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
    }
    HistoryList { objectType: "purchase_order"; objectId: page.sel && page.sel.order_no ? page.selectedId : "" }

    // ---------- редактор черновика
    PoEditor { id: editor; needLines: page.needLines }
    Connections { target: editor; function onClosed() { if (!page.sel && editor.orderId) page.selectedId = editor.orderId } }

    // ---------- уточнить справочные поля размещённого заказа
    FormDialog {
        id: infoForm
        objectName: "infoForm"
        property var o: null
        title: o ? "Уточнить: " + o.title : ""
        subtitle: o && o.status === "draft" ? "Пока заказ на согласовании или согласован, меняются только справочные поля. Условия заморожены — изменить их можно, отозвав заказ с согласования."
                                            : "У размещённого заказа меняются только справочные поля. Строки, поставщик, договор, валюта и НДС заморожены."
        canConfirm: ifDate.error === ""
        function openFor(x) { o = x; ifRef.text = x.supplier_ref || ""; ifDate.text = x.expected_on || ""; ifNote.text = x.note || ""; open() }
        onConfirmed: cInfo.run("po.update", { id: o.id, supplier_ref: ifRef.text.trim() || null, expected_on: ifDate.text.trim() || null,
                                              note: ifNote.text.trim() || null }, o.version)
        VField { id: ifRef; label: "Счёт / КП поставщика" }
        VField { id: ifDate; label: "Ожидаемая поставка (для справки)"; placeholder: "ГГГГ-ММ-ДД"
                 error: text.trim() === "" || /^\d{4}-\d{2}-\d{2}$/.test(text.trim()) ? "" : "Дата в виде ГГГГ-ММ-ДД" }
        VField { id: ifNote; label: "Примечание" }
    }
    Cmd { id: cInfo; dialog: infoForm }

    // ---------- разместить
    FormDialog {
        id: placeForm
        objectName: "placeForm"
        title: page.sel ? "Разместить " + page.sel.title.toLowerCase() + "?" : ""
        subtitle: page.sel ? page.sel.supplier_label + " · " + page.sel.line_count_label + " · " + page.sel.total_label : ""
        note: "Заказ согласован. После размещения строки, поставщик, договор, валюта и НДС не меняются (сервер запоминает их контрольную сумму). Склад начнёт принимать поставку по строкам заказа."
        confirmText: "Разместить"
        onConfirmed: cPlace.run("po.place", { id: page.sel.id }, page.sel.version)
    }
    Cmd { id: cPlace; dialog: placeForm }

    // ---------- согласование (D-33)
    FormDialog {
        id: submitForm
        objectName: "submitForm"
        property var o: null
        title: o ? "Отправить на согласование: " + o.title.toLowerCase() : ""
        subtitle: o ? o.supplier_label + " · " + o.line_count_label + " · " + o.total_label : ""
        note: "Согласующий увидит ровно это содержание. Пока заказ на согласовании, условия не меняются; изменить — отозвать."
        confirmText: "Отправить"
        function openFor(x) { o = x; sbComment.text = ""; open() }
        onConfirmed: cSubmit.run("po.submit", Object.assign({ id: o.id }, sbComment.text.trim() ? { comment: sbComment.text.trim() } : {}), o.version)
        VField { id: sbComment; objectName: "sbComment"; label: "Комментарий согласующему"; placeholder: "Необязательно: срочность, обоснование цены …" }
    }
    Cmd { id: cSubmit; dialog: submitForm }
    FormDialog {
        id: withdrawForm
        objectName: "withdrawForm"
        property var o: null
        title: o ? "Отозвать с согласования: " + o.title.toLowerCase() : ""
        subtitle: "Заказ снова станет черновиком, который можно править. Разместить его можно будет только после нового согласования."
        confirmText: "Отозвать"
        function openFor(x) { o = x; wdReason.text = ""; open() }
        onConfirmed: cWithdraw.run("po.withdraw", Object.assign({ id: o.id }, wdReason.text.trim() ? { reason: wdReason.text.trim() } : {}), o.version)
        VField { id: wdReason; label: "Причина"; placeholder: "Необязательно: поменялась цена, другой поставщик …" }
    }
    Cmd { id: cWithdraw; dialog: withdrawForm }
    FormDialog {
        id: approveForm
        objectName: "approveForm"
        property var o: null
        title: o ? "Согласовать " + o.title.toLowerCase() + "?" : ""
        subtitle: o ? o.supplier_label + " · договор " + o.contract_label + " · " + o.total_label
                      + (o.vat && o.vat.known ? " · с НДС " + o.vat.gross_label : "") : ""
        note: "Согласуется ровно это содержание (строки, цены, НДС, договор). Если закупка его изменит — понадобится новое согласование."
        confirmText: "Согласовать"
        function openFor(x) { o = x; apComment.text = ""; open() }
        onConfirmed: cApprove.run("po.approve", Object.assign({ id: o.id }, apComment.text.trim() ? { comment: apComment.text.trim() } : {}), o.version)
        VField { id: apComment; objectName: "apComment"; label: "Комментарий"; placeholder: "Необязательно" }
    }
    Cmd { id: cApprove; dialog: approveForm }
    FormDialog {
        id: returnForm
        objectName: "returnForm"
        property var o: null
        title: o ? "Вернуть на доработку: " + o.title.toLowerCase() : ""
        subtitle: "Закупка увидит причину в карточке заказа, исправит и отправит снова."
        confirmText: "Вернуть"; confirmKind: "danger"
        canConfirm: rtReason.text.trim() !== ""
        function openFor(x) { o = x; rtReason.text = ""; open() }
        onConfirmed: cReturn.run("po.return", { id: o.id, reason: rtReason.text.trim() }, o.version)
        VField { id: rtReason; objectName: "rtReason"; label: "Причина"; required: true; placeholder: "Нет договора, цена выше согласованной …" }
    }
    Cmd { id: cReturn; dialog: returnForm }

    // ---------- закрыть
    FormDialog {
        id: closeForm
        objectName: "closeForm"
        property var o: null
        title: o ? "Закрыть " + o.title.toLowerCase() : ""
        subtitle: o ? (o.close_needs_reason ? "Поставлено не всё — укажите причину (недопоставка, отказ поставщика …). После закрытия поставки по заказу не принимаются."
                                            : "Всё поставлено. После закрытия поставки по заказу не принимаются.") : ""
        confirmText: "Закрыть заказ"
        canConfirm: o !== null && (!o.close_needs_reason || clReason.text.trim() !== "")
        function openFor(x) { o = x; clReason.text = ""; open() }
        onConfirmed: cClose.run("po.close", Object.assign({ id: o.id }, clReason.text.trim() ? { reason: clReason.text.trim() } : {}), o.version)
        VField { id: clReason; objectName: "clReason"; label: "Причина"; required: closeForm.o !== null && closeForm.o.close_needs_reason }
    }
    Cmd { id: cClose; dialog: closeForm }

    // ---------- отменить
    FormDialog {
        id: cancelForm
        objectName: "cancelForm"
        property var o: null
        title: o ? "Отменить " + o.title.toLowerCase() : ""
        subtitle: "Отменить можно, пока по заказу ничего не поставлено. Отменённый заказ остаётся в архиве с причиной."
        confirmText: "Отменить заказ"; confirmKind: "danger"
        canConfirm: cnReason.text.trim() !== ""
        function openFor(x) { o = x; cnReason.text = ""; open() }
        onConfirmed: cCancel.run("po.cancel", { id: o.id, reason: cnReason.text.trim() }, o.version)
        VField { id: cnReason; objectName: "cnReason"; label: "Причина"; required: true; placeholder: "Дубль, поставщик отказал …" }
    }
    Cmd { id: cCancel; dialog: cancelForm }

    // ---------- принять поставку (склад)
    FormDialog {
        id: receiveForm
        objectName: "poReceiveForm"
        property var line: null
        title: line ? "Принять поставку: " + line.item_label : ""
        subtitle: line && page.sel ? page.sel.title + " · ждём " + line.remaining_label + ". Больше, чем ждём, сервер не примет — излишек оформляется обычным приходом." : ""
        confirmText: "Провести приход"
        canConfirm: prQty.text.trim() !== "" && prQty.error === "" && prSrc.text.trim() !== "" && prWh.currentValue !== ""
        function openFor(l) { line = l; prQty.text = page.editor_plain(l.remaining); prSrc.text = ""; prReason.text = ""; prWh.currentIndex = 0; open() }
        onConfirmed: cReceive.run("po.receive", Object.assign({ id: backend.newId(), order_id: page.sel.id, line_id: line.line_id, warehouse_id: prWh.currentValue,
                                                               qty: backend.normalizeQty(prQty.text), source_doc: prSrc.text.trim() },
                                                             prReason.text.trim() ? { reason: prReason.text.trim() } : {}))
        Banner { visible: page.whChoices.length === 0; tone: "warning"; text: "Нет действующих складов." }
        VSelect { id: prWh; objectName: "prWh"; label: "Склад"; model: page.whChoices }
        VField { id: prQty; objectName: "prQty"; label: "Количество"; required: true; error: backend.qtyProblem(text) }
        VField { id: prSrc; objectName: "prSrc"; label: "Основание (накладная)"; required: true; placeholder: "ТН № … от …" }
        VField { id: prReason; label: "Комментарий"; placeholder: "Необязательно" }
    }
    Cmd { id: cReceive; dialog: receiveForm }

    // «12.5000» → «12.5» для поля ввода.
    function editor_plain(s) { s = String(s || ""); return s.indexOf(".") >= 0 ? s.replace(/0+$/, "").replace(/\.$/, "") : s }
}
