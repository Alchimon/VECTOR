import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// «Склад» (сценарий С4, шаги 2 и 6): остатки, резервы, журнал движений и справочники.
//
// Что важно для понимания поведения:
//  * Остатки, резервы и журнал — снимок с сервера «на момент …» (время последнего обновления). Без связи
//    они только читаются: проводки, резервы и сторно — online_required, в очередь не ставятся.
//  * Доступно = остаток − резервы. Клиент показывает его как подсказку; окончательно количество проверяет
//    сервер в транзакции (параллельная выдача коллегой будет отклонена, а не «уйдёт в минус»).
//  * Проведённое движение не редактируется и не удаляется — ошибку исправляют сторно (встречным движением).
//  * Склады и номенклатуру можно заводить и без связи (очередь), как заказчиков.
Item {
    id: page
    property var shell
    property string tab: "balances"          // balances | reservations | journal | refs
    property string selectedId: ""
    property string search: ""
    readonly property var ov: backend.stockOverview
    readonly property var items: (ov.items || []).filter(i => search === "" || (i.code + " " + i.name).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: (ov.items || []).find(i => i.id === selectedId) || null
    readonly property var whOptions: (ov.warehouses || []).filter(w => w.active === true).map(w => ({ id: w.id, label: w.code + " · " + w.name }))
    readonly property var projectOptions: ov.projects || []
    readonly property bool canPost: ov.can_post === true
    readonly property string offlineWhy: "Проводки, резервы и сторно выполняются только при связи с сервером: склад меняется другими пользователями, и количество проверяется в момент проводки."

    function indexOf(options, id) { for (var i = 0; i < options.length; i++) if (options[i].id === id) return i; return 0 }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 28; anchors.bottomMargin: 20
        spacing: 16
        PageHeader {
            title: "Склад"
            subtitle: (ov.summary ? ov.summary.with_stock + " позиций в наличии из " + ov.summary.items + " · резервов: " + ov.summary.active_reservations : "") + "  ·  " + (ov.as_of || "")
            VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() }
        }
        Banner {
            visible: ov.is_warehouse === true && ov.online !== true
            tone: "warning"
            text: "Нет связи с сервером — остатки показаны " + (ov.as_of || "") + ". " + page.offlineWhy
        }
        VSegmented {
            objectName: "stockTabs"
            current: page.tab
            onPicked: id => page.tab = id
            model: [{ id: "balances", label: "Остатки", icon: "boxes" },
                    { id: "reservations", label: "Резервы (" + (ov.summary ? ov.summary.active_reservations : 0) + ")", icon: "bookmark" },
                    { id: "journal", label: "Журнал движений", icon: "history" },
                    { id: "refs", label: "Справочники", icon: "settings" }]
        }

        // ================================================================ Остатки
        RowLayout {
            visible: page.tab === "balances"
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 20
            VCard {
                Layout.preferredWidth: 420; Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 12; spacing: 10
                    VSearch { Layout.fillWidth: true; placeholderText: "Код или наименование"; onTextChanged: page.search = text }
                    ListView {
                        id: itemList
                        objectName: "stockItems"
                        Layout.fillWidth: true; Layout.fillHeight: true
                        clip: true; spacing: 2; keyNavigationEnabled: true
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}
                        model: page.items
                        delegate: ListRow {
                            title: modelData.code + "  ·  " + modelData.name
                            subtitle: "Доступно " + modelData.available_label + "  ·  остаток " + modelData.on_hand_label
                            iconName: "package"
                            chipText: modelData.state_label; chipTone: modelData.state_tone
                            pending: modelData.pending === true
                            selected: modelData.id === page.selectedId
                            onClicked: page.selectedId = modelData.id
                        }
                    }
                    EmptyState {
                        visible: itemList.count === 0
                        Layout.alignment: Qt.AlignHCenter; Layout.bottomMargin: 80
                        iconName: page.search !== "" ? "search" : "package"
                        title: page.search !== "" ? "Ничего не найдено" : "Номенклатуры пока нет"
                        text: page.search !== "" ? "Измените запрос поиска." : "Заведите позиции номенклатуры на вкладке «Справочники» — по их кодам строки потребности сопоставляются со складом."
                    }
                }
            }
            VCard {
                Layout.fillWidth: true; Layout.fillHeight: true
                EmptyState { anchors.centerIn: parent; visible: page.sel === null; iconName: "package"; title: "Выберите позицию"; text: ""; tone: "neutral" }
                ScrollView {
                    id: sv
                    anchors.fill: parent; anchors.margins: 2
                    visible: page.sel !== null
                    clip: true; contentWidth: availableWidth
                    Item {
                        width: sv.availableWidth
                        implicitHeight: card.implicitHeight + 56
                        ColumnLayout {
                            id: card
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 28
                            spacing: 16
                            RowLayout {
                                Layout.fillWidth: true; spacing: 14
                                Rectangle { width: 52; height: 52; radius: 14; color: Theme.primarySoft
                                    Icon { anchors.centerIn: parent; name: "package"; size: 26; color: Theme.primary } }
                                ColumnLayout { spacing: 4; Layout.fillWidth: true
                                    Text { text: page.sel ? page.sel.code : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
                                    Text { text: page.sel ? page.sel.name : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true } }
                                VChip { text: page.sel ? "ед. изм.: " + page.sel.unit : ""; tone: "neutral" }
                            }
                            // Три числа — главное на карточке.
                            RowLayout {
                                Layout.fillWidth: true; spacing: 12
                                Repeater {
                                    model: page.sel ? [{ k: "Остаток", v: page.sel.on_hand_label, tone: "neutral" },
                                                       { k: "В резерве", v: page.sel.reserved_label, tone: "info" },
                                                       { k: "Доступно", v: page.sel.available_label, tone: page.sel.state === "free" || page.sel.state === "partly_reserved" ? "success" : "warning" }] : []
                                    delegate: Rectangle {
                                        required property var modelData
                                        Layout.fillWidth: true; implicitHeight: 74; radius: Theme.radius
                                        color: Theme.tone(modelData.tone).bg
                                        ColumnLayout { anchors.fill: parent; anchors.margins: 14; spacing: 2
                                            Text { text: modelData.k; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                                            Text { text: modelData.v; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsL; font.weight: Font.Bold } }
                                    }
                                }
                            }
                            Text { text: "Доступно = остаток − резервы. Число — " + (ov.as_of || "") + "; окончательно проверяется сервером при проводке."
                                   color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            RowLayout {
                                visible: ov.is_warehouse === true
                                spacing: 8
                                VButton { objectName: "btnReceive"; text: "Приход"; kind: "primary"; iconName: "package-plus"; enabled: page.canPost && page.sel !== null && page.sel.active === true; onClicked: receiveForm.openFor(page.sel) }
                                VButton { objectName: "btnIssue"; text: "Выдать на проект"; iconName: "package-minus"; enabled: page.canPost && page.sel !== null; onClicked: issueForm.openFor(page.sel) }
                                VButton { objectName: "btnReserve"; text: "Зарезервировать"; kind: "soft"; iconName: "bookmark"; enabled: page.canPost && page.sel !== null && page.sel.active === true; onClicked: reserveForm.openFor(page.sel, "", "", "") }
                                Text { visible: !page.canPost; text: "Только при связи с сервером"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs }
                            }

                            SectionTitle { text: "По складам"; iconName: "warehouse" }
                            Repeater {
                                model: page.sel ? page.sel.by_warehouse : []
                                delegate: Rectangle {
                                    required property var modelData
                                    Layout.fillWidth: true; implicitHeight: 48; radius: Theme.radius; color: Theme.surfaceAlt
                                    RowLayout { anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 12
                                        Text { text: modelData.warehouse_code; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; Layout.preferredWidth: 70 }
                                        Text { text: modelData.warehouse_name; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; elide: Text.ElideRight; Layout.fillWidth: true }
                                        Text { text: "остаток " + modelData.on_hand_label; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                                        Text { text: "резерв " + modelData.reserved_label; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                                        VChip { text: "доступно " + modelData.available_label; tone: Number(modelData.available) > 0 ? "success" : "neutral" } }
                                }
                            }
                            Text { visible: page.sel !== null && page.sel.by_warehouse.length === 0; text: "Движений по позиции ещё не было."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }

                            SectionTitle { text: "Действующие резервы (" + (page.sel ? page.sel.reservations.length : 0) + ")"; iconName: "bookmark" }
                            Repeater {
                                model: page.sel ? page.sel.reservations : []
                                delegate: ReservationRow { Layout.fillWidth: true; r: modelData; canPost: page.canPost && ov.is_warehouse === true
                                    onIssueRequested: issueResForm.openFor(modelData); onReleaseRequested: releaseForm.openFor(modelData) }
                            }
                            Text { visible: page.sel !== null && page.sel.reservations.length === 0; text: "Резервов нет."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }

                            SectionTitle { text: "Последние движения"; iconName: "history" }
                            Repeater {
                                model: page.sel ? page.sel.movements : []
                                delegate: MovementRow { Layout.fillWidth: true; m: modelData; canPost: page.canPost && ov.is_warehouse === true
                                    onReverseRequested: reverseForm.openFor(modelData) }
                            }
                            Text { visible: page.sel !== null && page.sel.movements.length === 0; text: "Движений нет."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
                        }
                    }
                }
            }
        }

        // ================================================================ Резервы
        VCard {
            visible: page.tab === "reservations"
            Layout.fillWidth: true; Layout.fillHeight: true
            ListView {
                id: resList
                anchors.fill: parent; anchors.margins: 14
                clip: true; spacing: 6
                model: page.tab === "reservations" ? (ov.reservations || []) : []
                ScrollBar.vertical: ScrollBar {}
                delegate: ReservationRow { width: ListView.view.width; r: modelData; showItem: true; canPost: page.canPost && ov.is_warehouse === true
                    onIssueRequested: issueResForm.openFor(modelData); onReleaseRequested: releaseForm.openFor(modelData) }
            }
            EmptyState { anchors.centerIn: parent; visible: resList.count === 0; iconName: "bookmark"; title: "Резервов нет"; text: "Резерв закрепляет количество за проектом: оно перестаёт быть доступным для других выдач." }
        }

        // ================================================================ Журнал
        VCard {
            visible: page.tab === "journal"
            Layout.fillWidth: true; Layout.fillHeight: true
            ListView {
                id: mvList
                objectName: "stockJournal"
                anchors.fill: parent; anchors.margins: 14
                clip: true; spacing: 6
                model: page.tab === "journal" ? (ov.movements || []) : []
                ScrollBar.vertical: ScrollBar {}
                delegate: MovementRow { width: ListView.view.width; m: modelData; showItem: true; canPost: page.canPost && ov.is_warehouse === true
                    onReverseRequested: reverseForm.openFor(modelData) }
            }
            EmptyState { anchors.centerIn: parent; visible: mvList.count === 0; iconName: "history"; title: "Журнал пуст"; text: "Здесь появятся приходы, выдачи и сторно. Проведённые записи не меняются и не удаляются." }
        }

        // ================================================================ Справочники
        RowLayout {
            visible: page.tab === "refs"
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 20
            VCard {
                Layout.preferredWidth: 420; Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 10
                    RowLayout { Layout.fillWidth: true
                        SectionTitle { text: "Склады (" + (ov.warehouses || []).length + ")"; iconName: "warehouse" }
                        VButton { text: "Новый склад"; kind: "soft"; compact: true; iconName: "plus"; visible: ov.can_edit_warehouses === true; onClicked: whForm.openFor(null) } }
                    ListView {
                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 2
                        model: ov.warehouses || []
                        delegate: ListRow { title: modelData.code + "  ·  " + modelData.name
                            subtitle: (modelData.note || "") + (modelData.note ? "  ·  " : "") + "позиций в наличии: " + modelData.item_count
                            iconName: "warehouse"; pending: modelData.pending === true
                            chipText: modelData.active === true ? "" : "Отключён"
                            onClicked: if (ov.can_edit_warehouses === true) whForm.openFor(modelData) }
                    }
                }
            }
            VCard {
                Layout.fillWidth: true; Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 10
                    RowLayout { Layout.fillWidth: true
                        SectionTitle { text: "Номенклатура (" + (ov.items || []).length + ")"; iconName: "package" }
                        VButton { objectName: "btnNewItem"; text: "Новая позиция"; kind: "soft"; compact: true; iconName: "plus"; visible: ov.can_edit_refs === true; onClicked: itemForm.openFor(null) } }
                    Text { text: "Код сравнивается точно: по нему строки потребности сопоставляются с номенклатурой. Единицу нельзя менять после первых движений — пересчёта единиц нет."
                           color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    ListView {
                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 2
                        model: ov.items || []
                        delegate: ListRow { title: modelData.code + "  ·  " + modelData.name; subtitle: "ед. изм.: " + modelData.unit + (modelData.note ? "  ·  " + modelData.note : "")
                            iconName: "package"; pending: modelData.pending === true
                            chipText: modelData.active === true ? "" : "Отключена"
                            onClicked: if (ov.can_edit_refs === true) itemForm.openFor(modelData) }
                    }
                }
            }
        }
    }

    // ================================================================ формы проводок (только онлайн)
    FormDialog {
        id: receiveForm
        objectName: "receiveForm"
        property var item: null
        title: "Приход: " + (item ? item.code : "")
        subtitle: item ? item.name + ". Проведённый приход не редактируется — ошибку исправляют сторно." : ""
        confirmText: "Провести приход"
        canConfirm: rQty.text.trim() !== "" && rQty.error === "" && rSrc.text.trim() !== "" && rWh.currentValue !== ""
        function openFor(it) { item = it; rQty.text = ""; rSrc.text = ""; rReason.text = ""; rWh.currentIndex = 0; open() }
        onConfirmed: cReceive.run("stock.receive", { id: backend.newId(), warehouse_id: rWh.currentValue, item_id: item.id,
                                                     qty: backend.normalizeQty(rQty.text), source_doc: rSrc.text.trim(), reason: rReason.text.trim() })
        VSelect { id: rWh; label: "Склад"; model: page.whOptions }
        VField { id: rQty; objectName: "rQty"; label: "Количество" + (receiveForm.item ? ", " + receiveForm.item.unit : ""); required: true; placeholder: "например, 12,5"; error: backend.qtyProblem(text) }
        VField { id: rSrc; objectName: "rSrc"; label: "Основание (накладная, акт)"; required: true; placeholder: "ТН № 412 от 14.11.2026" }
        VField { id: rReason; label: "Комментарий" }
    }
    Cmd { id: cReceive; dialog: receiveForm }

    FormDialog {
        id: issueForm
        property var item: null
        readonly property var whAvail: item ? item.by_warehouse.filter(b => Number(b.available) > 0).map(b => ({ id: b.warehouse_id, label: b.warehouse_code + " — доступно " + b.available_label })) : []
        title: "Выдача на проект: " + (item ? item.code : "")
        subtitle: "Без резерва можно выдать только доступное количество (остаток − резервы других проектов)."
        confirmText: "Провести выдачу"
        canConfirm: iQty.text.trim() !== "" && iQty.error === "" && iReason.text.trim() !== "" && iWh.currentValue !== "" && iProj.currentValue !== ""
        function openFor(it) { item = it; iQty.text = ""; iReason.text = ""; iWh.currentIndex = 0; iProj.currentIndex = 0; open() }
        onConfirmed: cIssue.run("stock.issue", { id: backend.newId(), warehouse_id: iWh.currentValue, item_id: item.id, project_id: iProj.currentValue,
                                                 qty: backend.normalizeQty(iQty.text), reason: iReason.text.trim() })
        Banner { visible: issueForm.whAvail.length === 0; tone: "warning"; text: "Свободного количества нет ни на одном складе (всё в резерве или нет остатка)." }
        VSelect { id: iWh; label: "Склад"; model: issueForm.whAvail }
        VSelect { id: iProj; label: "Проект"; model: page.projectOptions }
        VField { id: iQty; label: "Количество" + (issueForm.item ? ", " + issueForm.item.unit : ""); required: true; error: backend.qtyProblem(text) }
        VField { id: iReason; label: "Основание выдачи"; required: true; placeholder: "Монтаж, бригада …" }
    }
    Cmd { id: cIssue; dialog: issueForm }

    FormDialog {
        id: issueResForm
        property var res: null
        title: "Выдача по резерву"
        subtitle: res ? res.item_code + " · " + res.project_code + " · осталось по резерву " + res.remaining_label : ""
        confirmText: "Провести выдачу"
        canConfirm: irQty.text.trim() !== "" && irQty.error === "" && irReason.text.trim() !== ""
        function openFor(r) { res = r; irQty.text = backend.normalizeQty(r.remaining).replace(/\.?0+$/, ""); irReason.text = ""; open() }
        onConfirmed: cIssueRes.run("stock.issue", { id: backend.newId(), warehouse_id: res.warehouse_id, item_id: res.item_id, reservation_id: res.id,
                                                    qty: backend.normalizeQty(irQty.text), reason: irReason.text.trim() })
        VField { id: irQty; label: "Количество"; required: true; error: backend.qtyProblem(text); hint: "Не больше остатка резерва. Сверх резерва — отдельной выдачей из доступного." }
        VField { id: irReason; label: "Основание выдачи"; required: true }
    }
    Cmd { id: cIssueRes; dialog: issueResForm }

    FormDialog {
        id: reserveForm
        objectName: "reserveForm"
        property var item: null
        property string needId: ""
        property string needLineId: ""
        property string presetProject: ""
        readonly property var whAvail: item ? item.by_warehouse.filter(b => Number(b.available) > 0).map(b => ({ id: b.warehouse_id, label: b.warehouse_code + " — доступно " + b.available_label })) : []
        title: "Резерв: " + (item ? item.code : "")
        subtitle: needId ? "Под строку потребности: количество не больше непокрытого." : "Резерв закрепляет количество за проектом. Это не выдача: остаток не меняется."
        confirmText: "Зарезервировать"
        canConfirm: vQty.text.trim() !== "" && vQty.error === "" && vReason.text.trim() !== "" && vWh.currentValue !== "" && vProj.currentValue !== ""
        function openFor(it, needId_, lineId_, projectId_) {
            item = it; needId = needId_; needLineId = lineId_; presetProject = projectId_
            vQty.text = ""; vReason.text = ""; vWh.currentIndex = 0
            vProj.currentIndex = projectId_ ? page.indexOf(page.projectOptions, projectId_) : 0
            open()
        }
        onConfirmed: {
            var p = { id: backend.newId(), warehouse_id: vWh.currentValue, item_id: item.id, project_id: vProj.currentValue,
                      qty: backend.normalizeQty(vQty.text), reason: vReason.text.trim() }
            if (needId) { p.need_id = needId; p.need_line_id = needLineId }
            cReserve.run("stock.reserve", p)
        }
        Banner { visible: reserveForm.whAvail.length === 0; tone: "warning"; text: "Свободного количества нет — резерв невозможен. Нужна закупка." }
        VSelect { id: vWh; label: "Склад"; model: reserveForm.whAvail }
        VSelect { id: vProj; label: "Проект"; model: page.projectOptions }
        VField { id: vQty; label: "Количество" + (reserveForm.item ? ", " + reserveForm.item.unit : ""); required: true; error: backend.qtyProblem(text) }
        VField { id: vReason; label: "Основание резерва"; required: true; placeholder: "Под монтаж …" }
    }
    Cmd { id: cReserve; dialog: reserveForm }

    FormDialog {
        id: releaseForm
        property var res: null
        title: "Снять резерв"
        subtitle: res ? res.item_code + " · " + res.project_code + ": неизрасходованные " + res.remaining_label + " вернутся в доступное." : ""
        confirmText: "Снять резерв"; confirmKind: "danger"
        canConfirm: relReason.text.trim() !== ""
        function openFor(r) { res = r; relReason.text = ""; open() }
        onConfirmed: cRelease.run("stock.release", { id: res.id, reason: relReason.text.trim() }, res.version)
        VField { id: relReason; label: "Причина"; required: true }
    }
    Cmd { id: cRelease; dialog: releaseForm }

    FormDialog {
        id: reverseForm
        property var mv: null
        title: mv ? "Сторно движения № " + mv.doc_no : ""
        subtitle: mv ? mv.kind_label + " " + mv.qty_label + " · " + mv.item_code + ". Будет проведено встречное движение; исходная запись останется в журнале." : ""
        confirmText: "Провести сторно"; confirmKind: "danger"
        canConfirm: revReason.text.trim() !== ""
        function openFor(m) { mv = m; revReason.text = ""; open() }
        onConfirmed: cReverse.run("stock.reverse", { id: backend.newId(), movement_id: mv.id, reason: revReason.text.trim() })
        Banner { tone: "info"; text: reverseForm.mv && reverseForm.mv.reservation_id ? "Резерв, израсходованный этой выдачей, не восстановится: количество вернётся в доступное." : "" }
        VField { id: revReason; label: "Причина сторно"; required: true; placeholder: "Ошибка ввода количества" }
    }
    Cmd { id: cReverse; dialog: reverseForm }

    // ================================================================ справочники (можно офлайн)
    FormDialog {
        id: whForm
        property var obj: null
        title: obj ? "Склад " + obj.code : "Новый склад"
        subtitle: "Можно сохранять без связи: изменение уйдёт на сервер при синхронизации."
        canConfirm: (obj !== null || whCode.text.trim() !== "") && whName.text.trim() !== ""
        function openFor(o) { obj = o; whCode.text = o ? o.code : ""; whName.text = o ? o.name : ""; whNote.text = o ? (o.note || "") : ""; whActive.checked = o ? o.active === true : true; open() }
        onConfirmed: obj ? cWh.run("warehouse.update", { id: obj.id, name: whName.text.trim(), note: whNote.text.trim() || null, active: whActive.checked }, obj.version)
                         : cWh.run("warehouse.create", { id: backend.newId(), code: whCode.text.trim(), name: whName.text.trim(), note: whNote.text.trim() })
        VField { id: whCode; label: "Код"; required: true; readOnly: whForm.obj !== null; placeholder: "СК-1"; hint: whForm.obj ? "Код склада не меняется." : "" }
        VField { id: whName; label: "Наименование"; required: true }
        VField { id: whNote; label: "Примечание" }
        CheckBox { id: whActive; visible: whForm.obj !== null; text: "Склад действует (на отключённый нельзя приходовать и резервировать)" }
    }
    Cmd { id: cWh; dialog: whForm }

    FormDialog {
        id: itemForm
        objectName: "itemForm"
        property var obj: null
        title: obj ? "Позиция " + obj.code : "Новая позиция номенклатуры"
        subtitle: "Можно сохранять без связи: изменение уйдёт на сервер при синхронизации."
        canConfirm: (obj !== null || itCode.text.trim() !== "") && itName.text.trim() !== "" && itUnit.text.trim() !== ""
        function openFor(o) { obj = o; itCode.text = o ? o.code : ""; itName.text = o ? o.name : ""; itUnit.text = o ? o.unit : ""; itNote.text = o ? (o.note || "") : ""; itActive.checked = o ? o.active === true : true; open() }
        onConfirmed: obj ? cItem.run("item.update", Object.assign({ id: obj.id, name: itName.text.trim(), note: itNote.text.trim() || null, active: itActive.checked },
                                                                  itUnit.text.trim() !== obj.unit ? { unit: itUnit.text.trim() } : {}), obj.version)
                         : cItem.run("item.create", { id: backend.newId(), code: itCode.text.trim(), name: itName.text.trim(), unit: itUnit.text.trim(), note: itNote.text.trim() })
        VField { id: itCode; objectName: "itCode"; label: "Код"; required: true; readOnly: itemForm.obj !== null; placeholder: "КВВГ-4x1.5"
                 error: itemForm.obj === null && itCode.text.trim() !== "" && !/^[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё._\/\-]{0,63}$/.test(itCode.text.trim()) ? "Буквы, цифры и . _ / - без пробелов, до 64 символов." : ""
                 hint: itemForm.obj ? "Код не меняется." : "Совпадает с обозначением в расчёте — тогда строка потребности сопоставится автоматически." }
        VField { id: itName; objectName: "itName"; label: "Наименование"; required: true }
        VField { id: itUnit; objectName: "itUnit"; label: "Единица измерения"; required: true; placeholder: "шт, м, компл" }
        VField { id: itNote; label: "Примечание" }
        CheckBox { id: itActive; visible: itemForm.obj !== null; text: "Позиция действует" }
    }
    Cmd { id: cItem; dialog: itemForm }
}
