import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Раздел «Заказчики»: список (поиск по названию и ИНН) и карточка: реквизиты, примечание, тендеры заказчика.
// Создание и правка работают и без связи (команда ставится в очередь, объект помечается «Не отправлено»).
MasterDetail {
    id: page
    property var shell
    property string selectedId: shell && shell.navTarget.page === "customers" ? shell.navTarget.id : ""
    readonly property var items: backend.customers.filter(c => search === "" || (c.name + " " + (c.inn || "")).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: backend.customers.find(c => c.id === selectedId) || null
    readonly property var selTenders: sel ? backend.tenders.filter(t => t.customer_id === sel.id) : []
    title: "Заказчики"
    subtitle: backend.customers.length + " в справочнике"
    hasSelection: sel !== null
    emptyIcon: "building-2"; emptyTitle: "Заказчиков пока нет"
    emptyText: "Добавьте первого заказчика — с него начинается цепочка «тендер → ТКП → проект»."
    emptyAction: backend.hint("customer.create", backend.permRevision) ? "Добавить заказчика" : ""
    onEmptyActionTriggered: openCreate()
    noSelectionText: "Выберите заказчика"

    function openCreate() { form.editId = ""; fName.text = ""; fInn.text = ""; fNote.text = ""; form.open() }
    function openEdit() { form.editId = sel.id; fName.text = sel.name; fInn.text = sel.inn || ""; fNote.text = sel.note || ""; form.open() }

    headerActions: [
        VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() },
        VButton { text: "Новый заказчик"; kind: "primary"; iconName: "plus"; visible: backend.hint("customer.create", backend.permRevision); onClicked: page.openCreate() }
    ]
    list.model: items
    list.delegate: ListRow {
        title: modelData.name
        subtitle: modelData.inn ? "ИНН " + modelData.inn : "ИНН не указан"
        iconName: "building-2"
        pending: modelData.pending
        selected: modelData.id === page.selectedId
        onClicked: page.selectedId = modelData.id
    }

    // ---------- карточка
    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Rectangle { width: 52; height: 52; radius: 14; color: Theme.primarySoft
            Icon { anchors.centerIn: parent; name: "building-2"; size: 26; color: Theme.primary } }
        ColumnLayout { spacing: 4; Layout.fillWidth: true
            Text { text: sel ? sel.name : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true }
            RowLayout { spacing: 6
                VChip { text: sel && sel.pending ? "Не отправлено на сервер" : "Синхронизировано"; tone: sel && sel.pending ? "warning" : "success"; iconName: sel && sel.pending ? "clock" : "check" }
                VChip { text: sel ? "версия " + (sel.version || "—") : ""; tone: "neutral" } } }
        VButton { text: "Изменить"; iconName: "pencil"; visible: backend.hint("customer.update", backend.permRevision); enabled: sel && sel.version > 0; onClicked: page.openEdit() }
    }
    Banner { visible: sel && sel.pending; tone: "warning"; text: sel && sel.pending ? "Изменения сохранены на этом компьютере и ещё не подтверждены сервером." : "" }
    VCard { Layout.fillWidth: true; implicitHeight: kv.implicitHeight + 32
        ColumnLayout { id: kv; anchors.fill: parent; anchors.margins: 16; spacing: 10
            KeyValue { k: "Наименование"; v: sel ? sel.name : "" }
            KeyValue { k: "ИНН"; v: sel ? (sel.inn || "") : "" }
            KeyValue { k: "Примечание"; v: sel ? (sel.note || "") : "" }
            KeyValue { k: "Обновлено"; v: sel && sel.updated_at ? sel.updated_at.replace("T", " ").substring(0, 16) + " UTC" : ""; muted: true } } }

    RowLayout { Layout.fillWidth: true
        SectionTitle { text: "Тендеры заказчика (" + page.selTenders.length + ")"; iconName: "gavel" }
        VButton { text: "Новый тендер"; kind: "soft"; compact: true; iconName: "plus"; visible: backend.hint("tender.create", backend.permRevision); onClicked: { tForm.open() } } }
    Repeater {
        model: page.selTenders
        delegate: ListRow { Layout.fillWidth: true; width: undefined; title: modelData.title; subtitle: modelData.submission_deadline ? "Подача до " + modelData.submission_deadline : "Срок подачи не указан"
                            chipText: modelData.status_label; chipTone: Theme.tenderTone(modelData.status); pending: modelData.pending; iconName: "gavel"
                            onClicked: page.shell.go("tenders", modelData.id) }
    }
    Text { visible: page.selTenders.length === 0; text: "Тендеров пока нет."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
    HistoryList { objectType: "customer"; objectId: page.selectedId }

    // ---------- формы
    FormDialog {
        id: form
        property string editId: ""
        title: editId ? "Изменить заказчика" : "Новый заказчик"
        subtitle: "Можно сохранять без связи: изменение уйдёт на сервер при синхронизации."
        canConfirm: fName.text.trim() !== "" && fInn.error === ""
        onConfirmed: editId ? cmd.run("customer.update", { id: editId, name: fName.text.trim(), inn: fInn.text.trim() || null, note: fNote.text.trim() || null }, page.sel.version)
                            : cmd.run("customer.create", { id: backend.newId(), name: fName.text.trim(), inn: fInn.text.trim(), note: fNote.text.trim() })
        VField { id: fName; label: "Наименование"; required: true; placeholder: "АО «Энергомаш»" }
        VField { id: fInn; label: "ИНН"; placeholder: "10 или 12 цифр"; error: text !== "" && !/^(\d{10}|\d{12})$/.test(text.trim()) ? "ИНН — 10 или 12 цифр." : ""
                 hint: "Контрольная сумма ИНН пока не проверяется." }
        VField { id: fNote; label: "Примечание" }
    }
    Cmd { id: cmd; dialog: form; onDone: r => { if (r.status === "accepted" && r.result.customer) page.selectedId = r.result.customer.id } }

    FormDialog {
        id: tForm
        title: "Новый тендер"
        subtitle: sel ? "Заказчик: " + sel.name : ""
        canConfirm: tTitle.text.trim() !== "" && tDate.error === ""
        onConfirmed: tCmd.run("tender.create", { id: backend.newId(), customer_id: sel.id, title: tTitle.text.trim(), submission_deadline: tDate.text.trim() || null })
        onOpened: { tTitle.text = ""; tDate.text = "" }
        VField { id: tTitle; label: "Предмет тендера"; required: true; placeholder: "Поставка и наладка АСУ ТП котельной" }
        VField { id: tDate; label: "Срок подачи заявки"; placeholder: "ГГГГ-ММ-ДД"; error: text !== "" && !/^\d{4}-\d{2}-\d{2}$/.test(text.trim()) ? "Формат даты: ГГГГ-ММ-ДД." : "" }
    }
    Cmd { id: tCmd; dialog: tForm }
}
