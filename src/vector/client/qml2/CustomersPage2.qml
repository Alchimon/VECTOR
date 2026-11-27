import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../qml"

// Раздел «Заказчики» 2.0 (D-129): просторный список карточек слева, карточка выбранного заказчика —
// справа внутри страницы (реквизиты, тендеры заказчика, история). Данные и команды те же, что
// у классики (backend.customers, customer.create/update, tender.create); создание и правка работают
// и без связи (команда встаёт в очередь, объект помечается «Не отправлено»).
Item {
    id: page
    objectName: "customersPage2"
    property var shell
    property string selectedId: ""
    property string search: ""
    readonly property var items: backend.customers.filter(c => search === "" || (c.name + " " + (c.inn || "")).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: backend.customers.find(c => c.id === selectedId) || null
    readonly property var selTenders: sel ? backend.tenders.filter(t => t.customer_id === sel.id) : []

    // Переход к заказчику из другого раздела (shell.go("customers", id)) — выделяем его в списке.
    function handleNavTarget() {
        if (shell && shell.navTarget.page === "customers" && shell.navTarget.id !== "")
            page.selectedId = shell.navTarget.id
    }
    Component.onCompleted: handleNavTarget()

    function openCreate() { form.editId = ""; fName.text = ""; fInn.text = ""; fNote.text = ""; form.open() }
    function openEdit() { form.editId = sel.id; fName.text = sel.name; fInn.text = sel.inn || ""; fNote.text = sel.note || ""; form.open() }

    ColumnLayout {
        anchors { fill: parent; margins: Theme2.pageMargin }
        spacing: Theme2.gap + 4

        // ---------- заголовок, поиск, действия
        RowLayout {
            Layout.fillWidth: true; spacing: Theme2.pad
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { text: "Заказчики"; color: Theme2.text; font.family: Theme2.family
                       font.pixelSize: Theme2.fsXl; font.weight: Font.Bold }
                Text { text: backend.customers.length === 0
                       ? "Добавьте первого заказчика — с него начинается цепочка «тендер → ТКП → проект»."
                       : backend.customers.length + " в справочнике"
                       color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                       wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }
            VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() }
            VButton { objectName: "cu2Create"; text: "Новый заказчик"; kind: "primary"; iconName: "plus"
                      visible: backend.hint("customer.create", backend.permRevision); onClicked: page.openCreate() }
            VSearch { objectName: "customers2Search"; Layout.preferredWidth: Theme2.compact ? 220 : 300
                      implicitHeight: Theme2.compact ? 36 : 44
                      placeholderText: "Поиск по названию или ИНН…"
                      onTextChanged: page.search = text }
        }

        // ---------- список карточек + карточка заказчика
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: Theme2.gap + 4

            ListView {
                id: list
                objectName: "customers2List"
                Layout.preferredWidth: Theme2.compact ? 320 : 400
                Layout.maximumWidth: Layout.preferredWidth
                Layout.fillHeight: true
                clip: true; spacing: Theme2.gap
                model: page.items
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: Rectangle {
                    id: card
                    objectName: "cu2Card"
                    readonly property var tendersCount: backend.tenders.filter(t => t.customer_id === modelData.id).length
                    width: list.width
                    implicitHeight: (Theme2.compact ? 64 : 76) + Theme2.pad
                    radius: Theme2.radius
                    color: ma.containsMouse ? Theme2.hover : Theme2.surface
                    border.color: page.selectedId === modelData.id ? Theme2.primary : Theme2.cardBorder
                    border.width: page.selectedId === modelData.id ? 2 : 1

                    // Индикатор выбора — полоска слева, как в списке проектов 2.0.
                    Rectangle { visible: page.selectedId === modelData.id; anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter; width: 4
                                height: parent.height - 24; radius: 2; color: Theme2.primary }

                    MouseArea {
                        id: ma
                        anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: page.selectedId = modelData.id
                    }

                    RowLayout {
                        anchors { fill: parent; leftMargin: Theme2.pad; rightMargin: Theme2.pad
                                  topMargin: Theme2.pad - 4; bottomMargin: Theme2.pad - 4 }
                        spacing: Theme2.pad
                        Rectangle { Layout.preferredWidth: Theme2.compact ? 40 : 48; Layout.preferredHeight: width
                                    radius: Theme2.radius; color: Theme2.primarySoft
                            Icon { anchors.centerIn: parent; name: "building-2"; size: 22; color: Theme2.primary } }
                        ColumnLayout { spacing: 3; Layout.fillWidth: true
                            Text { text: modelData.name; color: Theme2.text; font.family: Theme2.family
                                   font.pixelSize: Theme2.fsM; font.weight: Font.DemiBold
                                   Layout.fillWidth: true; elide: Text.ElideRight }
                            RowLayout { spacing: 12
                                Text { text: modelData.inn ? "ИНН " + modelData.inn : "ИНН не указан"
                                       color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS }
                                Text { text: card.tendersCount + " тенд."
                                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsS } }
                        }
                        VChip { visible: modelData.pending === true; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                        Icon { name: "chevron-right"; size: 16; color: Theme2.textFaint; Layout.alignment: Qt.AlignVCenter }
                    }
                }

                // Пустое состояние списка — дружелюбное, с пояснением.
                ColumnLayout {
                    visible: page.items.length === 0
                    anchors.centerIn: parent; spacing: Theme2.gap
                    Icon { name: "building-2"; size: 44; color: Theme2.textFaint; Layout.alignment: Qt.AlignHCenter }
                    Text { text: page.search !== "" ? "По запросу «" + page.search + "» ничего не найдено" : "Заказчиков пока нет"
                           color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsM
                           font.weight: Font.DemiBold; Layout.alignment: Qt.AlignHCenter }
                    Text { text: "С заказчика начинается цепочка «тендер → ТКП → проект»."
                           color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                           wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                           Layout.preferredWidth: 320 }
                }
            }

            // ---------- карточка выбранного заказчика (правая панель страницы)
            Flickable {
                Layout.fillWidth: true; Layout.fillHeight: true
                contentWidth: width; contentHeight: detailCol.implicitHeight
                clip: true; boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                ColumnLayout {
                    id: detailCol
                    width: parent.width
                    spacing: Theme2.gap + 4

                    // Нет выбора — подсказка вместо карточки.
                    ColumnLayout {
                        visible: page.sel === null && page.items.length > 0
                        Layout.fillWidth: true; Layout.topMargin: 60; spacing: Theme2.gap
                        Icon { name: "arrow-left"; size: 32; color: Theme2.textFaint; Layout.alignment: Qt.AlignHCenter }
                        Text { text: "Выберите заказчика в списке слева"
                               color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsM
                               Layout.alignment: Qt.AlignHCenter }
                    }

                    // Шапка карточки: название, состояние синхронизации, действия.
                    Rectangle {
                        objectName: "cu2Detail"
                        visible: page.sel !== null
                        Layout.fillWidth: true; implicitHeight: headCol.implicitHeight + Theme2.pad * 2
                        radius: Theme2.radius; color: Theme2.surface; border.color: Theme2.border
                        ColumnLayout {
                            id: headCol
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme2.pad }
                            spacing: Theme2.gap
                            RowLayout { Layout.fillWidth: true; spacing: Theme2.pad
                                Rectangle { Layout.preferredWidth: 52; Layout.preferredHeight: 52; radius: 14
                                            color: Theme2.primarySoft
                                    Icon { anchors.centerIn: parent; name: "building-2"; size: 26; color: Theme2.primary } }
                                ColumnLayout { spacing: 4; Layout.fillWidth: true
                                    Text { objectName: "cu2Name"; text: page.sel ? page.sel.name : ""
                                           color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsXl
                                           font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                    RowLayout { spacing: 6
                                        VChip { text: page.sel && page.sel.pending ? "Не отправлено на сервер" : "Синхронизировано"
                                                tone: page.sel && page.sel.pending ? "warning" : "success"
                                                iconName: page.sel && page.sel.pending ? "clock" : "check" }
                                        VChip { text: page.sel ? "версия " + (page.sel.version || "—") : ""; tone: "neutral" } } }
                                VButton { objectName: "cu2Edit"; text: "Изменить"; iconName: "pencil"
                                          visible: backend.hint("customer.update", backend.permRevision)
                                          enabled: page.sel && page.sel.version > 0; onClicked: page.openEdit() }
                            }
                            Banner { Layout.fillWidth: true; visible: page.sel && page.sel.pending; tone: "warning"
                                     text: page.sel && page.sel.pending ? "Изменения сохранены на этом компьютере и ещё не подтверждены сервером." : "" }
                        }
                    }

                    // Реквизиты.
                    Rectangle {
                        visible: page.sel !== null
                        Layout.fillWidth: true; implicitHeight: kv.implicitHeight + Theme2.pad * 2
                        radius: Theme2.radius; color: Theme2.surfaceAlt; border.color: Theme2.border
                        ColumnLayout { id: kv
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme2.pad }
                            spacing: 10
                            KeyValue { k: "Наименование"; v: page.sel ? page.sel.name : "" }
                            KeyValue { k: "ИНН"; v: page.sel ? (page.sel.inn || "") : "" }
                            KeyValue { k: "Примечание"; v: page.sel ? (page.sel.note || "") : "" }
                            KeyValue { k: "Обновлено"; v: page.sel && page.sel.updated_at ? page.sel.updated_at.replace("T", " ").substring(0, 16) + " UTC" : ""; muted: true } } }

                    // Тендеры заказчика.
                    RowLayout { visible: page.sel !== null; Layout.fillWidth: true
                        Text { text: "ТЕНДЕРЫ ЗАКАЗЧИКА (" + page.selTenders.length + ")"
                               color: Theme2.textMuted; font.family: Theme2.family
                               font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0
                               font.weight: Font.DemiBold; font.pixelSize: Theme2.fsXs; Layout.fillWidth: true }
                        VButton { objectName: "cu2NewTender"; text: "Новый тендер"; kind: "soft"; compact: true; iconName: "plus"
                                  visible: backend.hint("tender.create", backend.permRevision); onClicked: tForm.open() } }
                    Repeater {
                        model: page.selTenders
                        delegate: Rectangle {
                            id: tRow
                            required property var modelData
                            objectName: "cu2TenderRow"
                            Layout.fillWidth: true; implicitHeight: tInner.implicitHeight + Theme2.gap + 4
                            radius: Theme2.radius
                            color: tMa.containsMouse ? Theme2.hover : Theme2.surface
                            border.color: Theme2.border
                            MouseArea { id: tMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: page.shell.go("tenders", tRow.modelData.id) }
                            RowLayout { id: tInner
                                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter
                                          leftMargin: Theme2.gap + 6; rightMargin: Theme2.gap + 6 }
                                spacing: Theme2.gap
                                Icon { name: "gavel"; size: 15; color: Theme2.textFaint }
                                ColumnLayout { spacing: 2; Layout.fillWidth: true
                                    Text { text: tRow.modelData.title; color: Theme2.text; font.family: Theme2.family
                                           font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold
                                           Layout.fillWidth: true; elide: Text.ElideRight }
                                    Text { text: tRow.modelData.submission_deadline ? "Подача до " + tRow.modelData.submission_deadline : "Срок подачи не указан"
                                           color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs } }
                                VChip { text: tRow.modelData.status_label; tone: Theme.tenderTone(tRow.modelData.status) }
                                VChip { visible: tRow.modelData.pending === true; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                            }
                        }
                    }
                    Text { visible: page.sel !== null && page.selTenders.length === 0
                           text: "Тендеров пока нет."; color: Theme2.textMuted
                           font.family: Theme2.family; font.pixelSize: Theme2.fsS }
                    HistoryList { visible: page.sel !== null; Layout.fillWidth: true
                                  objectType: "customer"; objectId: page.selectedId }
                }
            }
        }
    }

    // ---------- формы (те же, что в классике)
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
        subtitle: page.sel ? "Заказчик: " + page.sel.name : ""
        canConfirm: tTitle.text.trim() !== "" && tDate.error === ""
        onConfirmed: tCmd.run("tender.create", { id: backend.newId(), customer_id: page.sel.id, title: tTitle.text.trim(), submission_deadline: tDate.text.trim() || null })
        onOpened: { tTitle.text = ""; tDate.text = "" }
        VField { id: tTitle; label: "Предмет тендера"; required: true; placeholder: "Поставка и наладка АСУ ТП котельной" }
        VField { id: tDate; label: "Срок подачи заявки"; placeholder: "ГГГГ-ММ-ДД"; error: text !== "" && !/^\d{4}-\d{2}-\d{2}$/.test(text.trim()) ? "Формат даты: ГГГГ-ММ-ДД." : "" }
    }
    Cmd { id: tCmd; dialog: tForm }
}
