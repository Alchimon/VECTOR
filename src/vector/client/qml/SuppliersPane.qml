import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Вкладка «Поставщики» раздела «Закупка» (D-32, A-34): справочник поставщиков.
//
//  * Ведёт закупка; видят закупка, склад (принимает поставки) и бухгалтерия.
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
        subtitle: modelData.inn_label + (modelData.contact ? "  ·  " + modelData.contact : "")
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
    KeyValue { k: "ИНН"; v: sel && sel.inn ? sel.inn : "не указан"; muted: sel !== null && !sel.inn }
    KeyValue { k: "Контакт"; v: sel ? (sel.contact || "") : "" }
    KeyValue { k: "Примечание"; v: sel ? (sel.note || "") : "" }
    SectionTitle { text: "Заказы этому поставщику"; iconName: "clipboard-list" }
    Text { visible: page.orders.length === 0; text: "Под текущий фильтр вкладки «Заказы» заказов нет."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
    Repeater {
        model: page.orders
        delegate: KeyValue { required property var modelData
                             k: modelData.title; v: (modelData.status === "placed" ? modelData.delivery_label : modelData.status_label) + "  ·  " + modelData.total_label }
    }
    HistoryList { objectType: "supplier"; objectId: page.sel && page.sel.pending !== true ? page.selectedId : "" }

    // ---------- создать / изменить
    FormDialog {
        id: supForm
        objectName: "supForm"
        property var obj: null
        title: obj ? "Поставщик " + obj.code : "Новый поставщик"
        subtitle: obj ? "Код не меняется. Сохранение — можно без связи (очередь)." : "Код — точный, без пробелов (по нему поставщика ищут). Сохранение — можно без связи (очередь)."
        canConfirm: (obj !== null || spCode.text.trim() !== "") && spName.text.trim() !== "" && spInn.error === ""
        function openFor(o) {
            obj = o || null          // из Python приходит undefined вместо null
            spCode.text = o ? o.code : ""; spName.text = o ? o.name : ""; spInn.text = o ? (o.inn || "") : ""
            spContact.text = o ? (o.contact || "") : ""; spNote.text = o ? (o.note || "") : ""; spActive.checked = o ? o.active !== false : true
            open()
        }
        onConfirmed: {
            if (obj === null) {
                var nid = backend.newId()
                cSup.created = nid
                cSup.run("supplier.create", { id: nid, code: spCode.text.trim(), name: spName.text.trim(), inn: spInn.text.trim(),
                                              contact: spContact.text.trim(), note: spNote.text.trim() })
            } else {
                // null — явная очистка поля; "" из пустого поля для правки превращаем в null.
                cSup.run("supplier.update", { id: obj.id, name: spName.text.trim(), inn: spInn.text.trim() || null,
                                              contact: spContact.text.trim() || null, note: spNote.text.trim() || null,
                                              active: spActive.checked }, obj.version)
            }
        }
        VField { id: spCode; objectName: "spCode"; visible: supForm.obj === null; label: "Код"; required: true; placeholder: "ЭЛЕКТРОСНАБ" }
        VField { id: spName; objectName: "spName"; label: "Название"; required: true; placeholder: "ООО «…»" }
        VField { id: spInn; objectName: "spInn"; label: "ИНН"; placeholder: "10 или 12 цифр, необязательно"; error: backend.innProblem(text) }
        VField { id: spContact; label: "Контакт"; placeholder: "Менеджер, телефон, почта" }
        VField { id: spNote; label: "Примечание" }
        CheckBox { id: spActive; visible: supForm.obj !== null; text: "Поставщик действует (отключённому новые заказы не размещаются)" }
    }
    Cmd { id: cSup; dialog: supForm; property string created: ""
          onDone: r => { if ((r.status === "accepted" || r.status === "queued") && created) { page.selectedId = created; created = "" } } }
}
