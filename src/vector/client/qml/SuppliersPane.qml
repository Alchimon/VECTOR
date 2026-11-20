import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Вкладка «Поставщики» раздела «Закупка» (D-32, D-33, A-34): справочник поставщиков, реквизиты и договоры.
//
//  * Ведёт закупка; видят закупка, склад (принимает поставки), бухгалтерия и согласующий заказы.
//  * Реквизиты (КПП, ОГРН/ОГРНИП, адрес, банк, БИК, расчётный и корр. счёт, ставка НДС по умолчанию)
//    необязательны, но если указаны — проверяются уже в форме теми же правилами, что на сервере (контрольные
//    цифры ОГРН, ключ счёта от БИК). «Реквизиты не заполнены» — подсказка, не запрет (A-39).
//  * Договоры: номер (уникален у поставщика), дата заключения, срок действия (пусто — бессрочный), предмет.
//    Не удаляются — отключаются. В заказе указывается действующий договор этого поставщика.
//  * Правка — можно без связи (очередь), как у заказчиков и номенклатуры; сервер перепроверит код и ИНН.
//  * Код — точный и уникальный, после создания не меняется. ИНН необязателен; если указан — 10 или 12 цифр
//    с верной контрольной суммой (проверяется уже в форме теми же правилами, что на сервере).
//  * Поставщика не удаляют, а отключают: новые заказы ему не размещаются, размещённые остаются в силе.
MasterDetail {
    id: page
    property var shell
    property string selectedId: ""
    tabsName: "procTabs"
    readonly property var sv: backend.suppliersOverview
    readonly property var all: sv.suppliers || []
    readonly property var items: all.filter(s => search === "" || (s.code + " " + s.name + " " + (s.inn || "")).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: all.find(s => s.id === selectedId) || null
    readonly property var orders: (backend.ordersOverview.orders || []).filter(o => sel !== null && o.supplier_id === sel.id)
    title: "Закупка"
    subtitle: all.length + " в справочнике поставщиков"
    hasSelection: sel !== null
    emptyIcon: "factory"; emptyTitle: "Поставщиков нет"
    emptyText: sv.empty_text || ""
    emptyAction: sv.can_edit === true ? "Добавить поставщика" : ""
    onEmptyActionTriggered: supForm.openFor(null)
    noSelectionText: "Выберите поставщика"
    headerActions: [
        VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() },
        VButton { objectName: "btnNewSupplier"; text: "Добавить поставщика"; kind: "primary"; iconName: "plus"
                  visible: page.sv.can_edit === true; onClicked: supForm.openFor(null) }
    ]
    list.model: items
    list.delegate: ListRow {
        title: modelData.code + "  ·  " + modelData.name
        subtitle: modelData.inn_label + "  ·  " + modelData.requisites_label + (modelData.contracts.length ? "  ·  договоров " + modelData.contracts.length : "")
        iconName: "factory"
        chipText: modelData.state_label; chipTone: modelData.state_tone
        pending: modelData.pending === true
        selected: modelData.id === page.selectedId
        onClicked: page.selectedId = modelData.id
    }

    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Rectangle { width: 52; height: 52; radius: 14; color: Theme.primarySoft
            Icon { anchors.centerIn: parent; name: "factory"; size: 26; color: Theme.primary } }
        ColumnLayout { spacing: 4; Layout.fillWidth: true
            Text { text: sel ? sel.code : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
            Text { text: sel ? sel.name : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true } }
        VChip { text: sel ? sel.state_label : ""; tone: sel ? sel.state_tone : "neutral" }
        VButton { objectName: "btnEditSupplier"; visible: page.sv.can_edit === true; text: "Изменить"; iconName: "pencil"; onClicked: supForm.openFor(sel) }
    }
    Banner { visible: sel !== null && sel.pending === true; tone: "info"; text: "Есть изменения, которые ещё не подтвердил сервер (очередь синхронизации)." }
    Banner { visible: sel !== null && sel.active === false; tone: "neutral"; text: "Поставщик отключён: новые заказы ему не размещаются; размещённые остаются в силе." }
    KeyValue { k: "Контакт"; v: sel ? (sel.contact || "") : "" }
    KeyValue { k: "Примечание"; v: sel ? (sel.note || "") : "" }
    // ---------- реквизиты (A-34)
    RowLayout { Layout.fillWidth: true
        SectionTitle { text: "Реквизиты"; iconName: "building-2"; Layout.fillWidth: true }
        VChip { objectName: "spReqChip"; text: sel ? sel.requisites_label : ""; tone: sel && sel.requisites_complete ? "success" : "neutral" } }
    KeyValue { k: "ИНН"; v: sel && sel.inn ? sel.inn : "не указан"; muted: sel !== null && !sel.inn }
    KeyValue { k: "КПП"; v: sel ? (sel.kpp || "") : "" }
    KeyValue { k: "ОГРН / ОГРНИП"; v: sel ? (sel.ogrn || "") : "" }
    KeyValue { k: "Адрес"; v: sel ? (sel.address || "") : "" }
    KeyValue { k: "Банк"; v: sel ? sel.bank_label : "" }
    KeyValue { k: "Расчётный счёт"; v: sel ? (sel.bank_account || "") : "" }
    KeyValue { k: "Корр. счёт"; v: sel ? (sel.corr_account || "") : "" }
    KeyValue { k: "Ставка НДС"; v: sel ? sel.vat_rate_label + " (подставляется в новые строки заказа)" : ""; muted: sel !== null && !sel.vat_rate }
    // ---------- договоры (A-34)
    RowLayout { Layout.fillWidth: true
        SectionTitle { text: "Договоры"; iconName: "file-text"; Layout.fillWidth: true }
        VButton { objectName: "btnNewContract"; visible: page.sv.can_edit === true && sel !== null; text: "Добавить договор"; compact: true; kind: "soft"
                  iconName: "plus"; onClicked: conForm.openFor(null) } }
    Text { visible: sel !== null && sel.contracts.length === 0; text: "Договоров нет — заказы этому поставщику оформляются без договора."
           color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.fillWidth: true; wrapMode: Text.Wrap }
    Repeater {
        objectName: "spContracts"
        model: sel ? sel.contracts : []
        delegate: RowLayout {
            required property var modelData
            Layout.fillWidth: true; spacing: 10
            Icon { name: "file-text"; size: 16; color: modelData.usable ? Theme.primary : Theme.textFaint }
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { text: modelData.label + (modelData.pending ? "  · не отправлено" : ""); color: modelData.usable ? Theme.text : Theme.textMuted
                       font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { visible: (modelData.subject || "") !== ""; text: modelData.subject || ""; color: Theme.textMuted
                       font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
            VButton { visible: page.sv.can_edit === true; text: "Изменить"; compact: true; kind: "ghost"; iconName: "pencil"; onClicked: conForm.openFor(modelData) }
        }
    }
    SectionTitle { text: "Заказы этому поставщику"; iconName: "clipboard-list" }
    Text { visible: page.orders.length === 0; text: "Под текущий фильтр вкладки «Заказы» заказов нет."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
    Repeater {
        model: page.orders
        delegate: KeyValue { required property var modelData
                             k: modelData.title; v: (modelData.status === "placed" ? modelData.delivery_label
                                                  : (modelData.status === "draft" && modelData.approval_state ? modelData.approval_label : modelData.status_label))
                                // Сумма: «с НДС», если НДС посчитан (D-33), с пометкой «итог неполный»; склад денег не видит.
                                + "  ·  " + (modelData.amount_label || modelData.total_label) }
    }
    HistoryList { objectType: "supplier"; objectId: page.sel && page.sel.pending !== true ? page.selectedId : "" }

    // ---------- создать / изменить
    FormDialog {
        id: supForm
        objectName: "supForm"
        property var obj: null
        title: obj ? "Поставщик " + obj.code : "Новый поставщик"
        subtitle: obj ? "Код не меняется. Сохранение — можно без связи (очередь)." : "Код — точный, без пробелов (по нему поставщика ищут). Сохранение — можно без связи (очередь)."
        formWidth: 760
        // Замечания по реквизитам — те же правила, что на сервере (поле → текст); пересчитываются при вводе.
        readonly property var reqErr: backend.requisitesProblems({ kpp: spKpp.text, ogrn: spOgrn.text, address: spAddr.text, bank_name: spBank.text,
                                                                   bik: spBik.text, bank_account: spAcc.text, corr_account: spCorr.text }, spInn.text)
        canConfirm: (obj !== null || spCode.text.trim() !== "") && spName.text.trim() !== "" && spInn.error === "" && Object.keys(reqErr).length === 0
        // Реквизиты: пустое поле → null (очистка), заполненное — как введено (сервер уберёт пробелы в цифрах).
        function reqPayload() {
            function v(t) { return t.trim() || null }
            return { kpp: v(spKpp.text), ogrn: v(spOgrn.text), address: v(spAddr.text), bank_name: v(spBank.text), bik: v(spBik.text),
                     bank_account: v(spAcc.text), corr_account: v(spCorr.text), vat_rate: spVat.currentValue || null }
        }
        function openFor(o) {
            obj = o || null          // из Python приходит undefined вместо null
            spCode.text = o ? o.code : ""; spName.text = o ? o.name : ""; spInn.text = o ? (o.inn || "") : ""
            spContact.text = o ? (o.contact || "") : ""; spNote.text = o ? (o.note || "") : ""; spActive.checked = o ? o.active !== false : true
            spKpp.text = o ? (o.kpp || "") : ""; spOgrn.text = o ? (o.ogrn || "") : ""; spAddr.text = o ? (o.address || "") : ""
            spBank.text = o ? (o.bank_name || "") : ""; spBik.text = o ? (o.bik || "") : ""; spAcc.text = o ? (o.bank_account || "") : ""
            spCorr.text = o ? (o.corr_account || "") : ""
            var rates = page.sv.vat_rates || []
            spVat.currentIndex = 0
            for (var i = 0; i < rates.length; i++) if (rates[i].id === (o ? (o.vat_rate || "") : "")) spVat.currentIndex = i
            open()
        }
        onConfirmed: {
            if (obj === null) {
                var nid = backend.newId()
                cSup.created = nid
                cSup.run("supplier.create", Object.assign({ id: nid, code: spCode.text.trim(), name: spName.text.trim(), inn: spInn.text.trim(),
                                                            contact: spContact.text.trim(), note: spNote.text.trim() }, reqPayload()))
            } else {
                // null — явная очистка поля; "" из пустого поля для правки превращаем в null.
                cSup.run("supplier.update", Object.assign({ id: obj.id, name: spName.text.trim(), inn: spInn.text.trim() || null,
                                                            contact: spContact.text.trim() || null, note: spNote.text.trim() || null,
                                                            active: spActive.checked }, reqPayload()), obj.version)
            }
        }
        GridLayout {
            Layout.fillWidth: true; columns: 2; columnSpacing: 12; rowSpacing: 10
            VField { id: spCode; objectName: "spCode"; visible: supForm.obj === null; label: "Код"; required: true; placeholder: "ЭЛЕКТРОСНАБ"; Layout.fillWidth: true }
            VField { id: spName; objectName: "spName"; label: "Название"; required: true; placeholder: "ООО «…»"; Layout.fillWidth: true
                     Layout.columnSpan: supForm.obj === null ? 1 : 2 }
            VField { id: spInn; objectName: "spInn"; label: "ИНН"; placeholder: "10 или 12 цифр, необязательно"; error: backend.innProblem(text); Layout.fillWidth: true }
            VField { id: spKpp; objectName: "spKpp"; label: "КПП"; placeholder: "9 знаков, у организации"; error: supForm.reqErr.kpp || ""; Layout.fillWidth: true }
            VField { id: spOgrn; objectName: "spOgrn"; label: "ОГРН / ОГРНИП"; placeholder: "13 или 15 цифр"; error: supForm.reqErr.ogrn || ""; Layout.fillWidth: true }
            VSelect { id: spVat; objectName: "spVat"; label: "Ставка НДС по умолчанию"; model: page.sv.vat_rates || []; Layout.fillWidth: true }
            VField { id: spAddr; label: "Юридический адрес"; error: supForm.reqErr.address || ""; Layout.fillWidth: true; Layout.columnSpan: 2 }
            VField { id: spBank; label: "Банк"; placeholder: "АО «…»"; error: supForm.reqErr.bank_name || ""; Layout.fillWidth: true }
            VField { id: spBik; objectName: "spBik"; label: "БИК"; placeholder: "9 цифр"; error: supForm.reqErr.bik || ""; Layout.fillWidth: true }
            VField { id: spAcc; objectName: "spAcc"; label: "Расчётный счёт"; placeholder: "20 цифр, вместе с БИК"; error: supForm.reqErr.bank_account || ""; Layout.fillWidth: true }
            VField { id: spCorr; label: "Корр. счёт"; placeholder: "20 цифр"; error: supForm.reqErr.corr_account || ""; Layout.fillWidth: true }
            VField { id: spContact; label: "Контакт"; placeholder: "Менеджер, телефон, почта"; Layout.fillWidth: true }
            VField { id: spNote; label: "Примечание"; Layout.fillWidth: true }
        }
        CheckBox { id: spActive; visible: supForm.obj !== null; text: "Поставщик действует (отключённому новые заказы не согласуются и не размещаются)" }
    }
    Cmd { id: cSup; dialog: supForm; property string created: ""
          onDone: r => { if ((r.status === "accepted" || r.status === "queued") && created) { page.selectedId = created; created = "" } } }

    // ---------- договор: добавить / изменить (A-34; можно без связи — очередь)
    FormDialog {
        id: conForm
        objectName: "conForm"
        property var obj: null
        readonly property bool dateOk: /^\d{4}-\d{2}-\d{2}$/.test(cnSigned.text.trim())
        readonly property bool untilOk: cnUntil.text.trim() === "" || /^\d{4}-\d{2}-\d{2}$/.test(cnUntil.text.trim())
        title: obj ? "Договор № " + obj.number : (page.sel ? "Новый договор: " + page.sel.code : "")
        subtitle: "Номер уникален у поставщика и не меняется. Срок действия пусто — бессрочный. Договор не удаляют, а отключают: в новых заказах его не указать, размещённые остаются."
        canConfirm: (obj !== null || cnNumber.text.trim() !== "") && dateOk && untilOk
                    && (cnUntil.text.trim() === "" || cnUntil.text.trim() >= cnSigned.text.trim())
        function openFor(o) {
            obj = o || null
            cnNumber.text = o ? o.number : ""; cnSigned.text = o ? (o.signed_on || "") : ""; cnUntil.text = o ? (o.valid_until || "") : ""
            cnSubject.text = o ? (o.subject || "") : ""; cnNote.text = o ? (o.note || "") : ""; cnActive.checked = o ? o.active !== false : true
            open()
        }
        onConfirmed: {
            if (obj === null)
                cCon.run("contract.create", { id: backend.newId(), supplier_id: page.sel.id, number: cnNumber.text.trim(), signed_on: cnSigned.text.trim(),
                                              valid_until: cnUntil.text.trim() || null, subject: cnSubject.text.trim() || null, note: cnNote.text.trim() || null })
            else
                cCon.run("contract.update", { id: obj.id, signed_on: cnSigned.text.trim(), valid_until: cnUntil.text.trim() || null,
                                              subject: cnSubject.text.trim() || null, note: cnNote.text.trim() || null, active: cnActive.checked }, obj.version)
        }
        VField { id: cnNumber; objectName: "cnNumber"; visible: conForm.obj === null; label: "Номер договора"; required: true; placeholder: "П-14" }
        VField { id: cnSigned; objectName: "cnSigned"; label: "Дата заключения"; required: true; placeholder: "ГГГГ-ММ-ДД"
                 error: text.trim() === "" || conForm.dateOk ? "" : "Дата в виде ГГГГ-ММ-ДД" }
        VField { id: cnUntil; objectName: "cnUntil"; label: "Действует до"; placeholder: "ГГГГ-ММ-ДД, пусто — бессрочный"
                 error: !conForm.untilOk ? "Дата в виде ГГГГ-ММ-ДД" : (text.trim() !== "" && conForm.dateOk && text.trim() < cnSigned.text.trim() ? "Срок действия раньше даты заключения" : "") }
        VField { id: cnSubject; label: "Предмет"; placeholder: "Кабельная продукция, комплектующие …" }
        VField { id: cnNote; label: "Примечание" }
        CheckBox { id: cnActive; visible: conForm.obj !== null; text: "Договор действует (отключённый в новых заказах не указать)" }
    }
    Cmd { id: cCon; dialog: conForm }
}
