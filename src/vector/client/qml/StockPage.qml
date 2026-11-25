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
//  * Перемещение между складами (A-28) — одна операция из двух записей журнала (расход и приход); перемещается только
//    доступное; ошибочное перемещение исправляют обратным, а не сторно.
//  * Склады и номенклатуру можно заводить и без связи (очередь), как заказчиков.
Item {
    id: page
    property var shell
    property string tab: "balances"          // balances | reservations | transit | adjustments | proposals | journal | refs
    property string selectedId: ""
    property string search: ""
    readonly property var ov: backend.stockOverview
    // Справочник аналогов и пересчёта единиц (D-25, A-27): ведут склад и закупка, только при связи.
    readonly property var dv: backend.directoryOverview
    readonly property var items: (ov.items || []).filter(i => search === "" || (i.code + " " + i.name).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: (ov.items || []).find(i => i.id === selectedId) || null
    readonly property var whOptions: (ov.warehouses || []).filter(w => w.active === true).map(w => ({ id: w.id, label: w.code + " · " + w.name }))
    readonly property var projectOptions: ov.projects || []
    readonly property bool canPost: ov.can_post === true
    readonly property string offlineWhy: "Проводки, перемещения, резервы и сторно выполняются только при связи с сервером: склад меняется другими пользователями, и количество проверяется в момент проводки."

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
                    { id: "transit", label: "В пути (" + (ov.summary ? ov.summary.in_transit : 0) + ")", icon: "truck" },
                    { id: "adjustments", label: "Корректировки (" + (ov.summary ? ov.summary.adjust_pending : 0) + ")", icon: "clipboard-check" },
                    { id: "proposals", label: "Предложения (" + (ov.summary ? ov.summary.proposals_open : 0) + ")", icon: "arrow-left-right" },
                    { id: "journal", label: "Журнал движений", icon: "history" },
                    { id: "refs", label: "Справочники", icon: "settings" },
                    { id: "directory", label: "Аналоги и пересчёт", icon: "arrow-left-right" }]
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
                                                       { k: "Доступно", v: page.sel.available_label, tone: page.sel.state === "free" || page.sel.state === "partly_reserved" ? "success" : "warning" }]
                                                       // «В пути» (D-29) — четвёртая плашка, только когда что-то отгружено и ещё не принято.
                                                       .concat(Number(page.sel.in_transit) > 0 ? [{ k: "В пути", v: page.sel.in_transit_label, tone: "warning" }] : []) : []
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
                                VButton { objectName: "btnTransfer"; text: "Отгрузить на склад"; iconName: "arrow-left-right"; enabled: page.canPost && page.sel !== null && ov.can_transfer === true; onClicked: transferForm.openFor(page.sel) }
                                VButton { objectName: "btnReserve"; text: "Зарезервировать"; kind: "soft"; iconName: "bookmark"; enabled: page.canPost && page.sel !== null && page.sel.active === true; onClicked: reserveForm.openFor(page.sel, "", "", "") }
                                VButton { objectName: "btnAdjust"; text: "Корректировка"; kind: "soft"; iconName: "clipboard-check"; enabled: ov.can_adjust === true && page.sel !== null; onClicked: adjustForm.openFor(page.sel) }
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

                            // Перемещения этой позиции «в пути» (D-29) — принять или вернуть можно прямо с карточки.
                            SectionTitle { visible: page.sel !== null && page.sel.transfers.length > 0; text: "В пути"; iconName: "truck" }
                            Repeater {
                                model: page.sel ? page.sel.transfers : []
                                delegate: TransferRow { Layout.fillWidth: true; t: modelData; showItem: false
                                    onAcceptRequested: acceptForm.openFor(modelData); onReturnRequested: returnForm.openFor(modelData)
                                    onLostRequested: lostForm.openFor(modelData) }
                            }

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

        // ================================================================ В пути (D-29)
        // Все перемещения «в пути» (сначала) и недавно закрытые — из pull. Склад-получатель принимает, при отказе
        // принять — склад возвращает отправителю. Без связи список только для чтения.
        VCard {
            visible: page.tab === "transit"
            Layout.fillWidth: true; Layout.fillHeight: true
            ListView {
                id: trList
                objectName: "transitList"
                anchors.fill: parent; anchors.margins: 14
                clip: true; spacing: 6
                model: page.tab === "transit" ? (ov.transfers || []) : []
                ScrollBar.vertical: ScrollBar {}
                delegate: TransferRow { width: ListView.view.width; t: modelData
                    onAcceptRequested: acceptForm.openFor(modelData); onReturnRequested: returnForm.openFor(modelData)
                                    onLostRequested: lostForm.openFor(modelData) }
            }
            EmptyState { anchors.centerIn: parent; visible: trList.count === 0; iconName: "truck"; title: "Перемещений нет"
                         text: "Отгрузка на другой склад создаёт перемещение «в пути»: количество списывается у отправителя и появляется у получателя только после приёмки." }
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
            EmptyState { anchors.centerIn: parent; visible: mvList.count === 0; iconName: "history"; title: "Журнал пуст"; text: "Здесь появятся приходы, выдачи, перемещения и сторно. Проведённые записи не меняются и не удаляются." }
        }

        // ================================================================ Корректировки остатка (D-77)
        VCard {
            visible: page.tab === "adjustments"
            Layout.fillWidth: true; Layout.fillHeight: true
            ListView {
                id: adjList
                objectName: "stockAdjustments"
                anchors.fill: parent; anchors.margins: 14
                clip: true; spacing: 6
                model: page.tab === "adjustments" ? (ov.adjustments || []) : []
                ScrollBar.vertical: ScrollBar {}
                delegate: AdjustmentRow { width: ListView.view.width; a: modelData
                    onSignRequested: adjSignForm.openFor(modelData)
                    onCancelRequested: adjCancelForm.openFor(modelData) }
            }
            EmptyState { anchors.centerIn: parent; visible: adjList.count === 0; iconName: "clipboard-check"; title: "Корректировок нет"
                         text: "Здесь появятся корректировки остатка по акту (инвентаризация). Корректировку создаёт склад с карточки позиции, в учёт она попадает только после подписи руководителя отдела." }
        }

        // ================================================================ Предложения перемещения (D-78)
        VCard {
            visible: page.tab === "proposals"
            Layout.fillWidth: true; Layout.fillHeight: true
            ListView {
                id: propList
                objectName: "stockProposals"
                anchors.fill: parent; anchors.margins: 14
                clip: true; spacing: 6
                model: page.tab === "proposals" ? (ov.proposals || []) : []
                ScrollBar.vertical: ScrollBar {}
                delegate: ProposalRow { width: ListView.view.width; t: modelData
                    onAcceptRequested: propDecideForm.openFor(modelData, "accept")
                    onOrderRequested: propDecideForm.openFor(modelData, "order")
                    onRejectRequested: propDecideForm.openFor(modelData, "reject")
                    onWithdrawRequested: propWithdrawForm.openFor(modelData) }
            }
            EmptyState { anchors.centerIn: parent; visible: propList.count === 0; iconName: "arrow-left-right"; title: "Предложений нет"
                         text: "Здесь появятся предложения переместить позицию из другого проекта (С4). Предложение — не резерв и не движение: оно ждёт решения склада или закупки." }
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
                    Text { text: "Код сравнивается точно: по нему (и по другим обозначениям на вкладке «Аналоги и пересчёт») строки потребности сопоставляются с номенклатурой. Единицу нельзя менять после первых движений."
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

        // ================================================================ Аналоги и пересчёт (D-25, A-27)
        Banner { visible: page.tab === "directory" && page.dv.online !== true; tone: "warning"
                 text: "Справочник меняется только при связи с сервером: обозначения и коэффициенты проверяются на уникальность и точность в момент изменения." }
        RowLayout {
            visible: page.tab === "directory"
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 20
            VCard {
                Layout.fillWidth: true; Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 10
                    RowLayout { Layout.fillWidth: true
                        SectionTitle { text: "Другие обозначения (" + (page.dv.aliases || []).length + ")"; iconName: "link" }
                        VButton { objectName: "btnNewAlias"; text: "Добавить"; kind: "soft"; compact: true; iconName: "plus"; visible: page.dv.can_edit === true; onClicked: aliasForm.openFor(null) } }
                    Text { // подсказка: расшифровка сокращений в тексте (D-37)
                        VTipArea { text: backend.tip(parent.text) }
                        text: "Аналоги и написания из расчётов/ТКП. Строка потребности с таким обозначением сопоставится автоматически, если единица совпадает с единицей позиции."
                           color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    ListView {
                        id: aliasList; objectName: "aliasList"
                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 2
                        model: page.tab === "directory" ? (page.dv.aliases || []) : []
                        ScrollBar.vertical: ScrollBar {}
                        delegate: ListRow { title: modelData.alias + "  →  " + modelData.item_label; subtitle: modelData.note || ""
                            iconName: "link"; chipText: modelData.active === true ? "" : modelData.state_label
                            onClicked: if (page.dv.can_edit === true) aliasForm.openFor(modelData) }
                    }
                    EmptyState { visible: aliasList.count === 0; Layout.alignment: Qt.AlignHCenter; iconName: "link"; title: "Обозначений нет"
                                 text: "Добавьте, если в расчётах позицию называют иначе, чем в номенклатуре." }
                }
            }
            VCard {
                Layout.fillWidth: true; Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 10
                    RowLayout { Layout.fillWidth: true
                        SectionTitle { text: "Пересчёт единиц (" + (page.dv.conversions || []).length + ")"; iconName: "arrow-left-right" }
                        VButton { objectName: "btnNewConv"; text: "Добавить"; kind: "soft"; compact: true; iconName: "plus"; visible: page.dv.can_edit === true; onClicked: convForm.openFor(null) } }
                    Text { text: "Используется только при ручном сопоставлении: закупка видит и подтверждает коэффициент, он фиксируется в решении. Количество не округляется — неточный пересчёт отклоняется."
                           color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    ListView {
                        id: convList; objectName: "convList"
                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 2
                        model: page.tab === "directory" ? (page.dv.conversions || []) : []
                        ScrollBar.vertical: ScrollBar {}
                        delegate: ListRow { title: modelData.rule_label; subtitle: modelData.scope_label + (modelData.note ? "  ·  " + modelData.note : "")
                            iconName: "arrow-left-right"; chipText: modelData.active === true ? "" : modelData.state_label
                            onClicked: if (page.dv.can_edit === true) convForm.openFor(modelData) }
                    }
                    EmptyState { visible: convList.count === 0; Layout.alignment: Qt.AlignHCenter; iconName: "arrow-left-right"; title: "Пересчётов нет"
                                 text: "Например, «1 км = 1000 м» — общий, или «1 бухта = 100 м» — для конкретного кабеля." }
                }
            }
        }
    }

    // ================================================================ формы справочника аналогов и пересчёта (только онлайн)
    FormDialog {
        id: aliasForm
        objectName: "aliasForm"
        property var obj: null
        title: obj ? "Обозначение «" + obj.alias + "»" : "Другое обозначение позиции"
        subtitle: "Как позицию называют в расчётах и ТКП. Строка потребности с таким обозначением и той же единицей сопоставится с позицией автоматически."
        canConfirm: alAlias.text.trim() !== "" && (obj !== null || (alItem.currentValue !== "" && alItem.currentValue !== undefined))
        function openFor(o) { obj = o; alAlias.text = o ? o.alias : ""; alNote.text = o ? (o.note || "") : ""; alActive.checked = o ? o.active === true : true
                              alItem.currentIndex = o ? page.indexOf(page.dv.items || [], o.item_id) : 0; open() }
        onConfirmed: obj ? cAlias.run("item_alias.update", Object.assign({ id: obj.id, note: alNote.text.trim() || null, active: alActive.checked },
                                                                        alAlias.text.trim() !== obj.alias ? { alias: alAlias.text.trim() } : {}), obj.version)
                         : cAlias.run("item_alias.create", { id: backend.newId(), item_id: alItem.currentValue, alias: alAlias.text.trim(), note: alNote.text.trim() })
        VSelect { id: alItem; objectName: "alItem"; label: "Позиция номенклатуры"; model: page.dv.items || []; enabled: aliasForm.obj === null
                  hint: aliasForm.obj ? "Позицию у обозначения не меняют — заведите новое обозначение, чтобы история не потерялась." : "" }
        VField { id: alAlias; objectName: "alAlias"; label: "Другое обозначение"; required: true; placeholder: "КВВГ 4х1,5" }
        VField { id: alNote; label: "Примечание"; placeholder: "Так пишут в ТКП поставщика" }
        CheckBox { id: alActive; visible: aliasForm.obj !== null; text: "Обозначение действует (отключённое не используется автоматом)" }
    }
    Cmd { id: cAlias; dialog: aliasForm }

    FormDialog {
        id: convForm
        objectName: "convForm"
        property var obj: null
        readonly property var scopes: [{ id: "", label: "Общий — для любой позиции в этой единице" }].concat(page.dv.items || [])
        readonly property var scopeItem: (page.dv.items || []).find(i => i.id === cvScope.currentValue) || null
        title: obj ? "Пересчёт " + obj.rule_label : "Новый пересчёт единиц"
        subtitle: "«1 [из] = [коэффициент] [в]». Используется только при ручном сопоставлении строки потребности — закупка подтверждает коэффициент. Обратный пересчёт не создаётся сам."
        canConfirm: cvFactor.text.trim() !== "" && cvFactor.error === "" && (obj !== null || (cvFrom.text.trim() !== "" && cvTo.text.trim() !== ""))
        function openFor(o) { obj = o; cvFrom.text = o ? o.from_unit : ""; cvTo.text = o ? o.to_unit : ""; cvFactor.text = o ? backend.normalizeQty(o.factor).replace(/\.?0+$/, "") : ""
                              cvNote.text = o ? (o.note || "") : ""; cvActive.checked = o ? o.active === true : true
                              cvScope.currentIndex = o && o.item_id ? page.indexOf(scopes, o.item_id) : 0; open() }
        onConfirmed: obj ? cConv.run("unit_conversion.update", { id: obj.id, factor: backend.normalizeQty(cvFactor.text), note: cvNote.text.trim() || null, active: cvActive.checked }, obj.version)
                         : cConv.run("unit_conversion.create", { id: backend.newId(), from_unit: cvFrom.text.trim(), to_unit: cvTo.text.trim(),
                                                                factor: backend.normalizeQty(cvFactor.text), item_id: cvScope.currentValue, note: cvNote.text.trim() })
        VSelect { id: cvScope; label: "Для какой позиции"; model: convForm.scopes; enabled: convForm.obj === null
                  onCurrentValueChanged: if (convForm.scopeItem && convForm.obj === null) cvTo.text = convForm.scopeItem.unit }
        RowLayout { Layout.fillWidth: true; spacing: 12
            VField { id: cvFrom; objectName: "cvFrom"; Layout.fillWidth: true; label: "Из единицы"; required: true; readOnly: convForm.obj !== null; placeholder: "км" }
            VField { id: cvTo; objectName: "cvTo"; Layout.fillWidth: true; label: "В единицу"; required: true; readOnly: convForm.obj !== null || convForm.scopeItem !== null; placeholder: "м"
                     hint: convForm.scopeItem ? "Единица позиции." : "" } }
        VField { id: cvFactor; objectName: "cvFactor"; label: "Коэффициент"; required: true; placeholder: "1000"; error: backend.qtyProblem(text)
                 hint: "Больше нуля, не больше 4 знаков после запятой. Изменение не затрагивает уже принятые решения." }
        VField { id: cvNote; label: "Примечание" }
        CheckBox { id: cvActive; visible: convForm.obj !== null; text: "Пересчёт действует" }
    }
    Cmd { id: cConv; dialog: convForm }

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

    // Наряды проекта, на которые можно выдать материал (D-36): выдача с привязкой к наряду учитывается в «Материалах»
    // наряда и нужна для его закрытия. «без наряда» — обычная выдача на проект.
    function woOptions(pid) {
        return [{ id: "", label: "без наряда" }].concat((backend.productionOverview.work_orders || [])
            .filter(w => w.project_id === pid && (w.status === "released" || w.status === "in_progress" || w.status === "completed"))
            .map(w => ({ id: w.id, label: w.label + " · " + w.title })))
    }

    FormDialog {
        id: issueForm
        objectName: "issueForm"
        property var item: null
        readonly property var whAvail: item ? item.by_warehouse.filter(b => Number(b.available) > 0).map(b => ({ id: b.warehouse_id, label: b.warehouse_code + " — доступно " + b.available_label })) : []
        title: "Выдача на проект: " + (item ? item.code : "")
        subtitle: "Без резерва можно выдать только доступное количество (остаток − резервы других проектов)."
        confirmText: "Провести выдачу"
        canConfirm: iQty.text.trim() !== "" && iQty.error === "" && iReason.text.trim() !== "" && iWh.currentValue !== "" && iProj.currentValue !== ""
        function openFor(it) { item = it; iQty.text = ""; iReason.text = ""; iWh.currentIndex = 0; iProj.currentIndex = 0; iWo.currentIndex = 0; open() }
        onConfirmed: cIssue.run("stock.issue", { id: backend.newId(), warehouse_id: iWh.currentValue, item_id: item.id, project_id: iProj.currentValue,
                                                 qty: backend.normalizeQty(iQty.text), reason: iReason.text.trim(), work_order_id: iWo.currentValue })
        Banner { visible: issueForm.whAvail.length === 0; tone: "warning"; text: "Свободного количества нет ни на одном складе (всё в резерве или нет остатка)." }
        VSelect { id: iWh; label: "Склад"; model: issueForm.whAvail }
        VSelect { id: iProj; objectName: "iProj"; label: "Проект"; model: page.projectOptions; onCurrentValueChanged: iWo.currentIndex = 0 }
        VSelect { id: iWo; objectName: "iWo"; label: "Наряд"; model: page.woOptions(iProj.currentValue); visible: model.length > 1
                  hint: "Выдача на наряд засчитывается в его материалы (нужно для закрытия наряда)." }
        VField { id: iQty; label: "Количество" + (issueForm.item ? ", " + issueForm.item.unit : ""); required: true; error: backend.qtyProblem(text) }
        VField { id: iReason; label: "Основание выдачи"; required: true; placeholder: "Монтаж, бригада …" }
    }
    Cmd { id: cIssue; dialog: issueForm }

    // Перемещение между складами (A-28, D-29 — отгрузка; приёмка и возврат — формы ниже): откуда — только склады, где позиция доступна; куда — любой другой действующий.
    FormDialog {
        id: transferForm
        objectName: "transferForm"
        property var item: null
        readonly property var fromOptions: item ? item.by_warehouse.filter(b => Number(b.available) > 0).map(b => ({ id: b.warehouse_id, label: b.warehouse_code + " — " + b.available_label })) : []
        readonly property var toOptions: page.whOptions.filter(w => w.id !== tFrom.currentValue)
        title: "Отгрузка на другой склад: " + (item ? item.code : "")
        subtitle: "Количество спишется со склада-отправителя сразу и будет «в пути», пока склад-получатель его не примет (или не вернёт). Отгружается только доступное: резервы остаются на своём складе."
        confirmText: "Отгрузить"
        canConfirm: tQty.text.trim() !== "" && tQty.error === "" && tReason.text.trim() !== "" && tFrom.currentValue !== "" && tTo.currentValue !== "" && tEta.error === ""
        function openFor(it) { item = it; tQty.text = ""; tReason.text = ""; tDoc.text = ""; tEta.text = ""; tFrom.currentIndex = 0; tTo.currentIndex = 0; open() }
        onConfirmed: cTransfer.run("stock.transfer", { id: backend.newId(), item_id: item.id, from_warehouse_id: tFrom.currentValue, to_warehouse_id: tTo.currentValue,
                                                       qty: backend.normalizeQty(tQty.text), reason: tReason.text.trim(), source_doc: tDoc.text.trim(),
                                                       expected_on: tEta.text.trim() || null })
        Banner { visible: transferForm.fromOptions.length === 0; tone: "warning"; text: "Свободного количества нет ни на одном складе (всё в резерве или нет остатка)." }
        RowLayout { Layout.fillWidth: true; spacing: 12
            VSelect { id: tFrom; objectName: "tFrom"; Layout.preferredWidth: 200; label: "Откуда (доступно)"; model: transferForm.fromOptions }
            Icon { name: "arrow-left-right"; size: 18; color: Theme.textMuted; Layout.alignment: Qt.AlignBottom; Layout.bottomMargin: 10 }
            VSelect { id: tTo; objectName: "tTo"; Layout.preferredWidth: 200; label: "Куда"; model: transferForm.toOptions } }
        VField { id: tQty; objectName: "tQty"; label: "Количество" + (transferForm.item ? ", " + transferForm.item.unit : ""); required: true; error: backend.qtyProblem(text) }
        VField { id: tReason; objectName: "tReason"; label: "Зачем перемещаем"; required: true; placeholder: "На монтажный участок, бригада …" }
        RowLayout { Layout.fillWidth: true; spacing: 12
            VField { id: tDoc; objectName: "tDoc"; Layout.fillWidth: true; label: "Накладная на перемещение (если есть)"; placeholder: "№ 3 от 17.11.2026" }
            // Срок прибытия — для справки (D-30): формат как у остальных дат программы; не раньше дня отгрузки проверит сервер.
            VField { id: tEta; objectName: "tEta"; Layout.preferredWidth: 200; label: "Ожидается (для справки)"; placeholder: "ГГГГ-ММ-ДД"
                     error: text !== "" && !/^\d{4}-\d{2}-\d{2}$/.test(text.trim()) ? "Формат ГГГГ-ММ-ДД" : "" } }
    }
    Cmd { id: cTransfer; dialog: transferForm }

    // Приёмка перемещения на складе-получателе (D-29): по версии перемещения — если его уже приняли или вернули с
    // другого места, сервер вернёт конфликт/«уже закрыто», ничего не проведя. Комментарий необязателен.
    // Пришло меньше (D-30): приёмщик вводит фактическое количество — разница оформляется АКТОМ НЕДОСТАЧИ, для
    // которого обязательны причина и подпись приёмщика (флажок). Недостача считается в Decimal на стороне Python.
    FormDialog {
        id: acceptForm
        objectName: "acceptForm"
        property var tr: null
        readonly property var sh: tr ? backend.transferShortage(tr.qty, acQty.text, tr.unit || "") : ({})
        readonly property bool short: sh.has_shortage === true
        // Способ подписи акта — настройка компании stock.act_signature (D-31): флажок / флажок и пароль / КЭП.
        readonly property var sig: ov.act_signature || ({ needs_password: true, blocked: false, hint: "" })
        title: "Принять перемещение"
        subtitle: tr ? tr.item_code + " · отгружено " + tr.qty_label + " · " + tr.route_label + ": принятое придёт на склад " + tr.to_code + " и станет доступным." : ""
        confirmText: short ? "Принять и подписать акт" : "Принять"
        confirmKind: short ? "danger" : "primary"
        canConfirm: tr !== null && acQty.error === "" && (!short || (acShortReason.text.trim() !== "" && acSign.checked
                                                                         && sig.blocked !== true && (sig.needs_password !== true || acPwd.text !== "")))
        function openFor(t) { tr = t; acQty.text = ""; acComment.text = ""; acShortReason.text = ""; acSign.checked = false; acPwd.text = ""; open() }
        onConfirmed: {
            var p = { id: tr.id, comment: acComment.text.trim() }
            if (acQty.text.trim() !== "") p.accepted_qty = backend.normalizeQty(acQty.text)
            if (short) {
                p.shortage_reason = acShortReason.text.trim(); p.sign_act = true
                // Пароль уходит только в эту онлайн-команду: в очередь, кэш и хэш запроса он не попадает (redact).
                if (sig.needs_password === true) p.sign_password = acPwd.text
            }
            acPwd.text = ""
            cAccept.run("stock.transfer_accept", p, tr.version)
        }
        VField { id: acQty; objectName: "acQty"; label: "Фактически пришло" + (acceptForm.tr ? ", " + (acceptForm.tr.unit || "") : "")
                 placeholder: acceptForm.tr ? "пусто — всё: " + acceptForm.tr.qty_label : ""; error: acceptForm.sh.problem || "" }
        Banner { visible: acceptForm.short; tone: "danger"
                 text: "Недостача " + (acceptForm.sh.shortage_label || "") + " — будет оформлен акт недостачи. Недостача спишется из учёта: она не вернётся ни на один склад." }
        VField { id: acShortReason; objectName: "acShortReason"; visible: acceptForm.short; label: "Причина недостачи (в акт)"; required: true; placeholder: "Повреждена упаковка, бой …" }
        // Подпись акта (D-31): флажок вошедшего кладовщика + пароль или КЭП — по настройке. Текст переносится —
        // длинная строка не должна растягивать диалог.
        CheckBox { id: acSign; objectName: "acSign"; visible: acceptForm.short; Layout.fillWidth: true; Layout.preferredWidth: 1
                   enabled: acceptForm.sig.blocked !== true
                   text: "Подписываю акт как приёмщик склада: количество пересчитано"
                   contentItem: Text { text: acSign.text; wrapMode: Text.Wrap; leftPadding: acSign.indicator.width + acSign.spacing
                                       verticalAlignment: Text.AlignVCenter; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs } }
        VField { id: acPwd; objectName: "acPwd"; visible: acceptForm.short && acceptForm.sig.needs_password === true
                 label: "Ваш пароль (подпись акта)"; password: true; required: true; hint: acceptForm.sig.hint || "" }
        Banner { visible: acceptForm.short && acceptForm.sig.blocked === true; tone: "danger"; text: acceptForm.sig.hint || "" }
        VField { id: acComment; objectName: "acComment"; label: "Комментарий (если нужен)"; placeholder: "Принято без замечаний" }
    }
    Cmd { id: cAccept; dialog: acceptForm }

    // Возврат перемещения отправителю (D-29): количество приходит обратно на склад-отправитель; причина обязательна.
    FormDialog {
        id: returnForm
        objectName: "returnForm"
        property var tr: null
        title: "Вернуть перемещение отправителю"
        subtitle: tr ? tr.item_code + " · " + tr.qty_label + " вернётся на склад " + tr.from_code + " и снова станет доступным там." : ""
        confirmText: "Вернуть"; confirmKind: "danger"
        canConfirm: rtReason.text.trim() !== ""
        function openFor(t) { tr = t; rtReason.text = ""; open() }
        onConfirmed: cReturn.run("stock.transfer_cancel", { id: tr.id, reason: rtReason.text.trim() }, tr.version)
        VField { id: rtReason; objectName: "rtReason"; label: "Почему возвращаем"; required: true; placeholder: "Пересорт, отгружено не туда …" }
    }
    Cmd { id: cReturn; dialog: returnForm }

    // Корректировка остатка по акту (инвентаризация, D-77): склад фиксирует фактический остаток и основание;
    // проводка в журнале появляется только после подписи руководителя отдела (adjSignForm ниже).
    FormDialog {
        id: adjustForm
        objectName: "adjustForm"
        property var item: null
        readonly property var whRows: item ? item.by_warehouse.map(b => ({ id: b.warehouse_id, label: b.warehouse_code + " — учётный " + b.on_hand_label })) : []
        // У позиции без учётного остатка (ещё не было прихода) склад выбирается из всех действующих.
        readonly property var whOpts: whRows.length > 0 ? whRows : page.whOptions
        title: "Корректировка остатка: " + (item ? item.code : "")
        subtitle: "Введите фактический остаток по пересчёту и основание (акт). Проводка выполнится только после подписи руководителя отдела — до этого в журнале ничего не изменится."
        confirmText: "Создать корректировку"
        canConfirm: adjQty.text.trim() !== "" && adjQty.error === "" && adjReason.text.trim() !== ""
                    && adjustForm.whOpts.length > 0 && adjWh.currentValue !== ""
        function openFor(it) { item = it; adjQty.text = ""; adjReason.text = ""; adjWh.currentIndex = 0; open() }
        onConfirmed: cAdjust.run("stock.adjust", { id: backend.newId(), warehouse_id: adjWh.currentValue, item_id: item.id,
                                                   new_qty: backend.normalizeQty(adjQty.text), reason: adjReason.text.trim() })
        Banner { tone: "info"; text: "По каждой паре склад + позиция может быть только одна корректировка, ждущая подписи." }
        VSelect { id: adjWh; objectName: "adjWh"; label: "Склад"; model: adjustForm.whOpts }
        VField { id: adjQty; objectName: "adjQty"; label: "Фактический остаток" + (adjustForm.item ? ", " + adjustForm.item.unit : ""); required: true; placeholder: "0, если совсем нет"; error: backend.qtyProblem(text) }
        VField { id: adjReason; objectName: "adjReason"; label: "Основание (акт инвентаризации)"; required: true; placeholder: "Акт инвентаризации № 4 от 20.11.2026" }
    }
    Cmd { id: cAdjust; dialog: adjustForm }

    // Подпись корректировки руководителем отдела (D-77): способ подписи — настройка stock.act_signature, как у актов.
    FormDialog {
        id: adjSignForm
        objectName: "adjSignForm"
        property var adj: null
        readonly property var sig: ov.act_signature || ({ needs_password: true, blocked: false, hint: "" })
        title: "Подписать корректировку остатка"
        subtitle: adj ? adj.item_code + " · " + adj.warehouse_code + ": было " + adj.on_hand_label + " → станет " + adj.new_qty_label + " (" + adj.delta_label + "). Основание: " + adj.reason : ""
        confirmText: "Подписать корректировку"
        canConfirm: adj !== null && adjSign.checked && sig.blocked !== true && (sig.needs_password !== true || adjPwd.text !== "")
        function openFor(a) { adj = a; adjSign.checked = false; adjPwd.text = ""; open() }
        onConfirmed: {
            var p = { id: adj.id, sign_act: true }
            // Пароль уходит только в эту онлайн-команду: в очередь, кэш и хэш запроса он не попадает (redact).
            if (sig.needs_password === true) p.sign_password = adjPwd.text
            adjPwd.text = ""
            cAdjSign.run("stock.adjust_sign", p, adj.version)
        }
        Banner { tone: "info"; text: "Если остаток изменился с момента создания корректировки, сервер её отклонит — её нужно будет создать заново." }
        CheckBox { id: adjSign; objectName: "adjSign"; Layout.fillWidth: true; Layout.preferredWidth: 1
                   enabled: adjSignForm.sig.blocked !== true
                   text: "Подписываю корректировку как руководитель отдела: расхождение подтверждено"
                   contentItem: Text { text: adjSign.text; wrapMode: Text.Wrap; leftPadding: adjSign.indicator.width + adjSign.spacing
                                       verticalAlignment: Text.AlignVCenter; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs } }
        VField { id: adjPwd; objectName: "adjPwd"; visible: adjSignForm.sig.needs_password === true
                 label: "Ваш пароль (подпись корректировки)"; password: true; required: true; hint: adjSignForm.sig.hint || "" }
        Banner { visible: adjSignForm.sig.blocked === true; tone: "danger"; text: adjSignForm.sig.hint || "" }
    }
    Cmd { id: cAdjSign; dialog: adjSignForm }

    // Отмена неподписанной корректировки её автором (D-77).
    FormDialog {
        id: adjCancelForm
        objectName: "adjCancelForm"
        property var adj: null
        title: "Отменить корректировку"
        subtitle: adj ? adj.item_code + " · " + adj.warehouse_code + " — запись останется в истории как отменённая; в журнал движений ничего не попадало." : ""
        confirmText: "Отменить корректировку"; confirmKind: "danger"
        canConfirm: adjCanReason.text.trim() !== ""
        function openFor(a) { adj = a; adjCanReason.text = ""; open() }
        onConfirmed: cAdjCancel.run("stock.adjust_cancel", { id: adj.id, reason: adjCanReason.text.trim() }, adj.version)
        VField { id: adjCanReason; objectName: "adjCanReason"; label: "Почему отменяем"; required: true; placeholder: "Ошибка в основании, пересчитаем …" }
    }
    Cmd { id: cAdjCancel; dialog: adjCancelForm }

    // Решение по предложению перемещения (D-78): склад перебрасывает резерв (accept), закупка решает «закупим»
    // (order), любая сторона отказывает с причиной (reject). Только при связи: сервер проверяет остатки в моменте.
    FormDialog {
        id: propDecideForm
        objectName: "propDecideForm"
        property var prop: null
        property string decision: ""
        title: ({ accept: "Перебросить резерв по предложению", order: "Решение «закупим»",
                  reject: "Отказать в перемещении" })[decision] || "Решение по предложению"
        subtitle: prop ? prop.item_code + " · " + prop.warehouse_code + ": " + prop.from_label + " → " + prop.to_label
                         + "  ·  " + prop.qty_label + (decision === "accept"
                            ? ". Резервы проекта-донора будут сняты (старшие сначала), недостающее — из свободного; резерв получателя появится сразу."
                            : "") : ""
        confirmText: ({ accept: "Перебросить резерв", order: "Подтвердить закупку", reject: "Отказать" })[decision] || "Подтвердить"
        confirmKind: decision === "reject" ? "danger" : "primary"
        canConfirm: prop !== null && (decision !== "reject" || pdNote.text.trim() !== "")
        function openFor(x, d) { prop = x; decision = d; pdNote.text = ""; open() }
        onConfirmed: cPropDecide.run("proposal.decide", { id: prop.id, decision: decision, note: pdNote.text.trim() }, prop.version)
        Banner { visible: propDecideForm.decision === "order"; tone: "info"
                 text: "Сам заказ поставщику создаётся отдельно на странице «Закупка» — это решение только закрывает предложение." }
        VField { id: pdNote; objectName: "pdNote"; label: propDecideForm.decision === "reject" ? "Причина отказа" : "Комментарий (по желанию)"
                 required: propDecideForm.decision === "reject"; placeholder: "Комментарий к решению" }
    }
    Cmd { id: cPropDecide; dialog: propDecideForm }

    // Отзыв своего предложения автором (D-78) — в очереди, работает и без связи.
    FormDialog {
        id: propWithdrawForm
        objectName: "propWithdrawForm"
        property var prop: null
        title: "Отозвать предложение"
        subtitle: prop ? prop.item_code + " · " + prop.qty_label + " — предложение останется в истории как отозванное." : ""
        confirmText: "Отозвать"; confirmKind: "danger"
        canConfirm: pwReason.text.trim() !== ""
        function openFor(x) { prop = x; pwReason.text = ""; open() }
        onConfirmed: cPropWithdraw.run("proposal.withdraw", { id: prop.id, reason: pwReason.text.trim() }, prop.version)
        VField { id: pwReason; objectName: "pwReason"; label: "Почему отзываем"; required: true; placeholder: "Уже не нужно, ошибка в количестве …" }
    }
    Cmd { id: cPropWithdraw; dialog: propWithdrawForm }

    // Акт об утрате (D-31): не пришло ничего. Прихода нет, отправителю не возвращается — количество списывается
    // актом. Причина обязательна; подпись приёмщика — как в акте недостачи (настройка stock.act_signature).
    FormDialog {
        id: lostForm
        objectName: "lostForm"
        property var tr: null
        readonly property var sig: ov.act_signature || ({ needs_password: true, blocked: false, hint: "" })
        title: "Оформить утрату перемещения"
        subtitle: tr ? tr.item_code + " · " + tr.qty_label + " · " + tr.route_label + ": на склад " + tr.to_code + " не пришло ничего." : ""
        confirmText: "Подписать акт об утрате"; confirmKind: "danger"
        canConfirm: tr !== null && lsReason.text.trim() !== "" && lsSign.checked && sig.blocked !== true
                    && (sig.needs_password !== true || lsPwd.text !== "")
        function openFor(t) { tr = t; lsReason.text = ""; lsSign.checked = false; lsPwd.text = ""; open() }
        onConfirmed: {
            var p = { id: tr.id, reason: lsReason.text.trim(), sign_act: true }
            if (sig.needs_password === true) p.sign_password = lsPwd.text
            lsPwd.text = ""
            cLost.run("stock.transfer_lost", p, tr.version)
        }
        Banner { tone: "danger"
                 text: "Количество будет списано из учёта актом об утрате: оно не придёт на получатель и не вернётся отправителю. Если груз найдётся — оформите обычный приход с основанием «найдено по акту об утрате № …»." }
        VField { id: lsReason; objectName: "lsReason"; label: "Что известно (в акт)"; required: true
                 placeholder: "Груз не прибыл; претензия перевозчику № …, ответственный …" }
        CheckBox { id: lsSign; objectName: "lsSign"; Layout.fillWidth: true; Layout.preferredWidth: 1
                   enabled: lostForm.sig.blocked !== true
                   text: "Подписываю акт как приёмщик склада: груз не поступил"
                   contentItem: Text { text: lsSign.text; wrapMode: Text.Wrap; leftPadding: lsSign.indicator.width + lsSign.spacing
                                       verticalAlignment: Text.AlignVCenter; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs } }
        VField { id: lsPwd; objectName: "lsPwd"; visible: lostForm.sig.needs_password === true
                 label: "Ваш пароль (подпись акта)"; password: true; required: true; hint: lostForm.sig.hint || "" }
        Banner { visible: lostForm.sig.blocked === true; tone: "danger"; text: lostForm.sig.hint || "" }
    }
    Cmd { id: cLost; dialog: lostForm }

    FormDialog {
        id: issueResForm
        property var res: null
        title: "Выдача по резерву"
        subtitle: res ? res.item_code + " · " + res.project_code + " · осталось по резерву " + res.remaining_label : ""
        confirmText: "Провести выдачу"
        canConfirm: irQty.text.trim() !== "" && irQty.error === "" && irReason.text.trim() !== ""
        function openFor(r) { res = r; irQty.text = backend.normalizeQty(r.remaining).replace(/\.?0+$/, ""); irReason.text = ""; irWo.currentIndex = 0; open() }
        onConfirmed: cIssueRes.run("stock.issue", { id: backend.newId(), warehouse_id: res.warehouse_id, item_id: res.item_id, reservation_id: res.id,
                                                    qty: backend.normalizeQty(irQty.text), reason: irReason.text.trim(), work_order_id: irWo.currentValue })
        VField { id: irQty; label: "Количество"; required: true; error: backend.qtyProblem(text); hint: "Не больше остатка резерва. Сверх резерва — отдельной выдачей из доступного." }
        VField { id: irReason; label: "Основание выдачи"; required: true }
        VSelect { id: irWo; label: "Наряд"; model: issueResForm.res ? page.woOptions(issueResForm.res.project_id) : []; visible: model.length > 1 }
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
