import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Редактор черновика заказа поставщику (D-32, D-33): широкое модальное окно — реквизиты и таблица строк.
//
//  * Рабочая копия строк — ListModel (правка поля не пересоздаёт строки, фокус не прыгает).
//  * Каждое изменение сразу проверяется локально (backend.poPreview → vector.core.purchasing — те же правила,
//    что на сервере): сумма строки, замечание у строки, итог. Числа — строками, без float.
//  * Пустая цена = «неизвестна» (не ноль); валюта не подставляется сама и обязательна, если указана хоть одна
//    цена. Цены вводятся так, как в счёте поставщика; «Цены в счёте» — с НДС или без НДС, у строки — ставка НДС.
//    Внизу — три итога: без НДС, НДС, с НДС (A-35, НДС строки округляется до копеек). Режим и ставки для
//    черновика необязательны, но без них заказ не отправить на согласование (подсказка внизу).
//  * Договор — из действующих договоров выбранного поставщика (A-34); можно «без договора».
//  * Сверка со счётом (D-34, A-38): суммы «с НДС» и «НДС» из счёта поставщика (необязательно). Внизу — «Контроль
//    расчёта»: самопроверка НДС вторым способом, строки с долями копейки, совпадение со счётом. Расхождение не
//    мешает сохранить черновик, но при отправке на согласование потребуется причина (её увидит согласующий).
//  * Строку можно привязать к строке потребности — тогда позиция должна совпадать с выбранной для строки
//    потребности (проверит сервер); в потребности появится «заказано, ждём» после размещения.
//  * «Сохранить» — через очередь (можно без связи): новый заказ — po.create, существующий черновик — po.update
//    (expected_version считает SyncClient). Конфликт решается на экране «Синхронизация», молча ничего не
//    перезаписывается.
Popup {
    id: ed
    objectName: "poEditor"
    property var order: null                // null — новый заказ
    property var ov: backend.ordersOverview
    property var needLines: []              // [{id, label, need_id, need_line_id, item_id}] — строки потребностей
    property int editRev: 0
    property bool working: false
    property string error: ""
    property string orderId: ""
    property string tag: "po-" + Math.random().toString(36).slice(2)
    readonly property var pv: {
        editRev
        var r = visible ? backend.poPreview(collect(), curField.text, vatSel.currentValue || "", invGross.text, invVat.text) : ({ rows: [] })
        var m = {}
        for (var i = 0; i < (r.rows || []).length; i++) m[r.rows[i].line_id] = r.rows[i]
        r.byId = m
        return r
    }
    modal: true; focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(1180, (Overlay.overlay ? Overlay.overlay.width : 1200) - 48)
    height: Math.min(820, (Overlay.overlay ? Overlay.overlay.height : 860) - 48)
    padding: 0
    closePolicy: Popup.CloseOnEscape
    Overlay.modal: Rectangle { color: Theme.scrim }
    background: Rectangle { color: Theme.surface; radius: Theme.radiusL; border.color: Theme.border }

    ListModel { id: lm }

    function indexOf(options, id) { options = options || []; for (var i = 0; i < options.length; i++) if (options[i].id === id) return i; return -1 }
    // Договоры выбранного поставщика; текущий договор черновика остаётся в списке, даже если уже не действует.
    readonly property var contractOpts: {
        var sid = supSel.currentValue
        var out = [{ id: "", label: "без договора" }].concat((ov.contracts || []).filter(c => c.supplier_id === sid))
        if (order && order.contract_id && order.supplier_id === sid && indexOf(out, order.contract_id) < 0)
            out.push({ id: order.contract_id, label: order.contract_label })
        return out
    }
    // Ставка НДС новой строки — ставка поставщика по умолчанию (из реквизитов), если указана.
    function defaultRate() { var s = (ov.suppliers || [])[supSel.currentIndex]; return s ? (s.vat_rate || "") : "" }
    function openFor(o) {
        order = o || null; error = ""; working = false
        orderId = o ? o.id : backend.newId()
        lm.clear()
        var src = o ? (o.lines || []) : []
        for (var i = 0; i < src.length; i++) lm.append(rowOf(src[i]))
        if (lm.count === 0) lm.append(rowOf({ vat_rate: defaultRate() }))
        supSel.currentIndex = o ? Math.max(0, indexOf(ov.suppliers || [], o.supplier_id)) : 0
        vatSel.currentIndex = Math.max(0, indexOf(ov.vat_modes || [], o ? (o.vat_mode || "") : ""))
        conSel.currentIndex = Math.max(0, indexOf(contractOpts, o ? (o.contract_id || "") : ""))
        curField.text = o ? (o.currency || "") : ""
        refField.text = o ? (o.supplier_ref || "") : ""
        dateField.text = o ? (o.expected_on || "") : ""
        noteField.text = o ? (o.note || "") : ""
        invGross.text = o && o.invoice_gross ? plain(o.invoice_gross) : ""
        invVat.text = o && o.invoice_vat ? plain(o.invoice_vat) : ""
        editRev++
        open()
    }
    function rowOf(x) {
        // ListModel хранит строки; отсутствующее значение = "" (при отправке станет null = «не задано»).
        return { line_id: x.line_id || backend.newId(), item_id: x.item_id || "",
                 qty: x.qty !== undefined && x.qty !== null ? plain(x.qty) : "",
                 unit_price: x.unit_price !== undefined && x.unit_price !== null ? plain(x.unit_price) : "",
                 need_key: x.need_id ? x.need_id + "/" + x.need_line_id : "", note: x.note || "", vat_rate: x.vat_rate || "" }
    }
    function plain(s) { s = String(s); return s.indexOf(".") >= 0 ? s.replace(/0+$/, "").replace(/\.$/, "") : s }
    function setCell(i, key, value) { lm.setProperty(i, key, value); editRev++ }
    // Строки для проверки и сохранения; полностью пустые заготовки пропускаются.
    function collect() {
        var out = []
        for (var i = 0; i < lm.count; i++) {
            var r = lm.get(i)
            if (!r.item_id && !r.qty && !r.unit_price && !r.note) continue
            var nk = r.need_key ? r.need_key.split("/") : ["", ""]
            out.push({ line_id: r.line_id, item_id: r.item_id, qty: r.qty, unit_price: r.unit_price,
                       need_id: nk[0], need_line_id: nk[1], note: r.note, vat_rate: r.vat_rate })
        }
        return out
    }
    function save() {
        working = true; error = ""
        backend.savePoDraft(tag, { id: orderId, supplier_id: supSel.currentValue, currency: curField.text,
                                   vat_mode: vatSel.currentValue || "", contract_id: conSel.currentValue || "",
                                   supplier_ref: refField.text, expected_on: dateField.text, note: noteField.text,
                                   invoice_gross: invGross.text, invoice_vat: invVat.text, lines: collect() })
    }
    Connections {
        target: backend
        function onCommandDone(t, r) {
            if (t !== ed.tag) return
            ed.working = false
            if (r.status === "accepted" || r.status === "queued") ed.close()
            else ed.error = r.message_ru || "Заказ не сохранён."
        }
    }

    contentItem: ColumnLayout {
        spacing: 0
        // ---------- заголовок
        RowLayout {
            Layout.fillWidth: true; Layout.margins: Theme.padL; Layout.bottomMargin: 10; spacing: 12
            Rectangle { width: 40; height: 40; radius: 11; color: Theme.primarySoft
                Icon { anchors.centerIn: parent; name: "clipboard-list"; size: 20; color: Theme.primary } }
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { text: ed.order ? ed.order.title + " · черновик" : "Новый заказ поставщику"; color: Theme.text; font.family: Theme.family
                       font.pixelSize: Theme.fsL; font.weight: Font.DemiBold }
                Text { text: "Цены — как в счёте поставщика; укажите, с НДС они или без, и ставку НДС строки — программа покажет суммы без НДС, НДС и с НДС. Пустая цена — «неизвестна», не ноль. Валюта не подставляется сама. Черновик можно сохранять без связи; разместить — после согласования."
                       color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
            VButton { kind: "ghost"; iconName: "x"; compact: true; onClicked: ed.close(); Accessible.name: "Закрыть" }
        }
        GridLayout {
            Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL
            columns: 3; columnSpacing: 12; rowSpacing: 8
            VSelect { id: supSel; objectName: "poSupplier"; label: "Поставщик *"; model: ed.ov.suppliers || []; Layout.fillWidth: true; Layout.columnSpan: 2
                      onCurrentValueChanged: conSel.currentIndex = Math.max(0, ed.indexOf(ed.contractOpts, ed.order ? (ed.order.contract_id || "") : "")) }
            VField { id: curField; objectName: "poCurrency"; label: "Валюта (ISO)"; placeholder: "RUB"; Layout.fillWidth: true
                     input.onTextEdited: ed.editRev++ }
            VSelect { id: conSel; objectName: "poContract"; label: "Договор"; model: ed.contractOpts; Layout.fillWidth: true; Layout.columnSpan: 2
                      hint: ed.contractOpts.length <= 1 ? "У поставщика нет действующих договоров — добавьте на вкладке «Поставщики» или заказ без договора." : "" }
            VSelect { id: vatSel; objectName: "poVatMode"; label: "Цены в счёте"; model: ed.ov.vat_modes || []; Layout.fillWidth: true
                      onCurrentIndexChanged: ed.editRev++ }
            VField { id: refField; label: "Счёт / КП поставщика"; placeholder: "Необязательно"; Layout.fillWidth: true }
            VField { id: dateField; label: "Ожидаемая поставка (для справки)"; placeholder: "ГГГГ-ММ-ДД"; Layout.fillWidth: true
                     error: text.trim() === "" || /^\d{4}-\d{2}-\d{2}$/.test(text.trim()) ? "" : "Дата в виде ГГГГ-ММ-ДД" }
            VField { id: noteField; label: "Примечание"; placeholder: "Необязательно"; Layout.fillWidth: true }
            // Суммы из счёта поставщика — для сверки с расчётом (D-34); пусто — сверки нет.
            VField { id: invGross; objectName: "poInvoiceGross"; label: "Итого по счёту, с НДС"; placeholder: "Для сверки, необязательно"; Layout.fillWidth: true
                     error: backend.invoiceProblem(text, "invoice_gross"); input.onTextEdited: ed.editRev++ }
            VField { id: invVat; objectName: "poInvoiceVat"; label: "НДС по счёту"; placeholder: "Для сверки, необязательно"; Layout.fillWidth: true
                     error: backend.invoiceProblem(text, "invoice_vat"); input.onTextEdited: ed.editRev++ }
        }
        Banner { visible: (ed.ov.suppliers || []).length === 0; tone: "warning"; Layout.fillWidth: true
                 Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 8
                 text: "В справочнике нет действующих поставщиков — добавьте поставщика на вкладке «Поставщики»." }
        // ---------- шапка таблицы
        Rectangle {
            Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 12
            implicitHeight: 30; radius: Theme.radiusS; color: Theme.surfaceAlt
            RowLayout { anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 6
                Repeater {
                    model: [{ t: "№", w: 28 }, { t: "Позиция номенклатуры *", w: -1 }, { t: "Кол-во *", w: 96 }, { t: "Ед.", w: 52 },
                            { t: "Цена за ед.", w: 120 }, { t: "НДС", w: 116 }, { t: "Сумма", w: 120 }, { t: "Под строку потребности", w: 180 }, { t: "", w: 34 }]
                    delegate: Text { required property var modelData
                        text: modelData.t; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.DemiBold
                        Layout.preferredWidth: modelData.w > 0 ? modelData.w : -1; Layout.fillWidth: modelData.w < 0 }
                }
            }
        }
        // ---------- строки
        ListView {
            id: lv
            objectName: "poEditorRows"
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL
            clip: true; spacing: 2; model: lm
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                id: rowItem
                required property int index
                required property var model
                readonly property var info: ed.pv.byId[model.line_id] || ({})
                // Строки потребностей, где выбрана та же позиция (сервер проверит то же самое).
                readonly property var needOpts: [{ id: "", label: "—" }].concat(ed.needLines.filter(n => n.item_id === rowItem.model.item_id))
                width: lv.width; implicitHeight: rowCol.implicitHeight + 8; radius: Theme.radiusS
                color: info.problem ? Theme.dangerSoft : "transparent"
                ColumnLayout { id: rowCol; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                               anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 2
                    RowLayout { Layout.fillWidth: true; spacing: 6
                        Text { text: rowItem.index + 1; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.preferredWidth: 28 }
                        VSelect { Layout.fillWidth: true; model: [{ id: "", label: "Выберите позицию…" }].concat(ed.ov.items || [])
                                  currentIndex: Math.max(0, ed.indexOf(model, rowItem.model.item_id))
                                  Accessible.name: "Позиция " + (rowItem.index + 1)
                                  onCurrentValueChanged: if (currentValue !== rowItem.model.item_id) { ed.setCell(rowItem.index, "item_id", currentValue); ed.setCell(rowItem.index, "need_key", "") } }
                        CalcCell { Layout.preferredWidth: 96; num: true; placeholder: "0"; text: rowItem.model.qty
                                   Accessible.name: "Количество " + (rowItem.index + 1)
                                   onTextEdited: ed.setCell(rowItem.index, "qty", text) }
                        Text { text: rowItem.info.unit || ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.preferredWidth: 52 }
                        CalcCell { Layout.preferredWidth: 120; num: true; placeholder: "неизвестна"; text: rowItem.model.unit_price
                                   Accessible.name: "Цена " + (rowItem.index + 1)
                                   onTextEdited: ed.setCell(rowItem.index, "unit_price", text) }
                        VSelect { Layout.preferredWidth: 116; Layout.fillWidth: false; model: ed.ov.vat_rates || []
                                  currentIndex: Math.max(0, ed.indexOf(model, rowItem.model.vat_rate))
                                  Accessible.name: "Ставка НДС " + (rowItem.index + 1)
                                  onCurrentValueChanged: if (currentValue !== rowItem.model.vat_rate) ed.setCell(rowItem.index, "vat_rate", currentValue) }
                        Text { text: rowItem.info.amount_label || "—"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs
                               font.weight: Font.DemiBold; font.features: { "tnum": 1 }; horizontalAlignment: Text.AlignRight
                               elide: Text.ElideLeft; Layout.preferredWidth: 120 }
                        VSelect { Layout.preferredWidth: 180; model: rowItem.needOpts; enabled: rowItem.needOpts.length > 1
                                  currentIndex: Math.max(0, ed.indexOf(rowItem.needOpts, rowItem.model.need_key))
                                  Accessible.name: "Строка потребности " + (rowItem.index + 1)
                                  onCurrentValueChanged: if (currentValue !== rowItem.model.need_key) ed.setCell(rowItem.index, "need_key", currentValue) }
                        VButton { kind: "ghost"; compact: true; iconName: "trash-2"; Layout.preferredWidth: 34
                                  Accessible.name: "Удалить строку " + (rowItem.index + 1)
                                  onClicked: { lm.remove(rowItem.index); ed.editRev++ } }
                    }
                    Text { visible: !!rowItem.info.problem; text: rowItem.info.problem || ""; color: Theme.danger; font.family: Theme.family
                           font.pixelSize: Theme.fsXs; leftPadding: 34; wrapMode: Text.Wrap; Layout.fillWidth: true }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 8; spacing: 8
            VButton { objectName: "poAddLine"; text: "Добавить строку"; iconName: "plus"; kind: "soft"; compact: true
                      onClicked: { lm.append(ed.rowOf({ vat_rate: ed.defaultRate() })); ed.editRev++; lv.positionViewAtEnd() } }
            Item { Layout.fillWidth: true }
            Text { text: (ed.pv.rows || []).length + " стр."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
        }
        // ---------- итог и ошибки
        Rectangle { Layout.fillWidth: true; Layout.topMargin: 12; height: 1; color: Theme.border }
        RowLayout {
            Layout.fillWidth: true; Layout.margins: 16; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; spacing: 12
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { objectName: "poEditorTotal"; text: "Итог: " + (ed.pv.total_label || ""); color: Theme.text; font.family: Theme.family
                       font.pixelSize: Theme.fsM; font.weight: Font.Bold; font.features: { "tnum": 1 }; wrapMode: Text.Wrap; Layout.fillWidth: true }
                // НДС раздельно (A-35): без НДС · НДС · с НДС; не посчитать — «—» и почему.
                Text { objectName: "poEditorVat"; visible: ed.pv.vat !== undefined
                       text: ed.pv.vat ? (ed.pv.vat.known ? "Без НДС " + ed.pv.vat.net_label + "  ·  НДС " + ed.pv.vat.vat_label + "  ·  С НДС " + ed.pv.vat.gross_label
                                                          + (ed.pv.vat.by_rate_label ? "  (" + ed.pv.vat.by_rate_label + ")" : "")
                                                        : "НДС не посчитан: " + ed.pv.vat.why) : ""
                       color: ed.pv.vat && ed.pv.vat.known ? Theme.text : Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs
                       font.features: { "tnum": 1 }; wrapMode: Text.Wrap; Layout.fillWidth: true }
                // Контроль расчёта (D-34): только замечания и сверка со счётом — «всё сошлось» не занимает место.
                Repeater {
                    model: ed.pv.control ? ed.pv.control.checks.filter(c => c.state !== "ok" || c.code === "invoice") : []
                    delegate: RowLayout { required property var modelData; spacing: 6; Layout.fillWidth: true
                        Icon { name: modelData.icon; size: 14; color: Theme.tone(modelData.tone).fg }
                        Text { objectName: "poEditorCheck"; text: modelData.text; color: modelData.state === "info" ? Theme.textMuted : Theme.tone(modelData.tone).fg
                               font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
                }
                // Чего не хватит для согласования — не мешает сохранить черновик.
                Text { objectName: "poEditorReady"; visible: (ed.pv.ready_problems || []).length > 0
                       text: "Для согласования: " + (ed.pv.ready_problems || []).join(" ")
                       color: Theme.warning; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { visible: ed.pv.problem_count > 0
                       text: "Замечаний: " + ed.pv.problem_count + ". " + ((ed.pv.general_problems || []).join(" ") || "Исправьте строки, отмеченные красным.")
                       color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { visible: ed.error !== ""; text: ed.error; color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsS
                       wrapMode: Text.Wrap; Layout.fillWidth: true }
            }
            VButton { text: "Отмена"; kind: "ghost"; onClicked: ed.close() }
            VButton { objectName: "poSave"; text: ed.working ? "Сохранение…" : "Сохранить черновик"; kind: "primary"; iconName: "check"
                      enabled: !ed.working && ed.pv.valid === true && supSel.currentValue !== "" && dateField.error === ""
                               && invGross.error === "" && invVat.error === ""
                      onClicked: ed.save() }
        }
    }
}
