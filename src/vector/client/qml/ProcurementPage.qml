import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// «Закупка» (сценарий С4, шаги 1–2): потребности из зафиксированных версий расчёта и их покрытие складом.
//
//  * Потребность формирует закупка явным действием из ПОСЛЕДНЕЙ зафиксированной версии комплекта (A-22).
//    Потребность — не заказ: она показывает, что нужно, что уже зарезервировано и чего не хватает.
//  * В потребности нет цен: цены — данные расчёта, закупке в этом срезе не раскрываются.
//  * Строка сопоставляется с номенклатурой по ТОЧНОМУ коду и единице; иначе — «не сопоставлена» с причиной.
//  * «Доступно» по складам — снимок «на момент …»; ГИП/РП видят потребность своего проекта без остатков
//    (у них нет права на склад) — там «неизвестно», а не «0».
//  * Формирование и резерв — только при связи с сервером.
MasterDetail {
    id: page
    property var shell
    property string selectedId: ""
    readonly property var nv: backend.needsOverview
    readonly property var sov: backend.stockOverview
    readonly property var all: nv.needs || []
    readonly property var items: all.filter(n => search === "" || n.title.toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: all.find(n => n.id === selectedId) || null
    title: "Закупка"
    subtitle: all.length + " потребност" + (all.length % 10 === 1 && all.length % 100 !== 11 ? "ь" : (all.length % 10 >= 2 && all.length % 10 <= 4 && (all.length % 100 < 10 || all.length % 100 >= 20) ? "и" : "ей")) + "  ·  " + (nv.as_of || "")
    hasSelection: sel !== null
    emptyIcon: "shopping-cart"; emptyTitle: "Потребностей пока нет"
    emptyText: nv.can_generate === true ? ((nv.candidates || []).length > 0 ? "Есть зафиксированные расчёты без потребности — сформируйте её." : "Потребность формируется из зафиксированной версии расчёта комплекта. Зафиксированных версий без потребности нет.")
                                        : "Потребность формирует закупка из зафиксированной версии расчёта комплекта."
    emptyAction: nv.can_generate === true && (nv.candidates || []).length > 0 ? "Сформировать потребность" : ""
    onEmptyActionTriggered: genForm.open()
    noSelectionText: "Выберите потребность"
    headerActions: [
        VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() },
        VButton { objectName: "btnGenerate"; text: "Сформировать потребность"; kind: "primary"; iconName: "plus"
                  visible: (backend.me.roles || []).indexOf("procurement") >= 0
                  enabled: nv.can_generate === true && (nv.candidates || []).length > 0
                  onClicked: genForm.open() }
    ]
    list.model: items
    list.delegate: ListRow {
        title: modelData.title
        subtitle: modelData.kit_title + "  ·  строк " + modelData.line_count + ", сопоставлено " + modelData.matched_count
        iconName: "shopping-cart"
        chipText: modelData.superseded === true ? "Есть v" + modelData.latest_calc_version_no : (modelData.open_count > 0 ? "Открыто: " + modelData.open_count : (modelData.unmatched_count > 0 ? "Не сопоставлено: " + modelData.unmatched_count : "Покрыта"))
        chipTone: modelData.superseded === true ? "warning" : (modelData.open_count > 0 ? "info" : (modelData.unmatched_count > 0 ? "neutral" : "success"))
        selected: modelData.id === page.selectedId
        onClicked: page.selectedId = modelData.id
    }

    // ---------- карточка
    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Rectangle { width: 52; height: 52; radius: 14; color: Theme.primarySoft
            Icon { anchors.centerIn: parent; name: "shopping-cart"; size: 26; color: Theme.primary } }
        ColumnLayout { spacing: 4; Layout.fillWidth: true
            Text { text: sel ? sel.project_code + " · " + sel.project_title : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { text: sel ? "Комплект " + sel.kit_code + ", расчёт v" + sel.calc_version_no : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true } }
        VButton { text: "Проект"; iconName: "folder-kanban"; visible: sel !== null && backend.projects.some(p => p.id === sel.project_id); onClicked: page.shell.go("projects", sel.project_id) }
    }
    Banner { visible: sel !== null && sel.superseded === true; tone: "warning"
             text: sel ? "Расчёт комплекта зафиксирован заново (v" + sel.latest_calc_version_no + "). Эта потребность — по v" + sel.calc_version_no + "; сформируйте потребность по новой версии." : "" }
    Banner { visible: nv.sees_stock !== true; tone: "info"; text: "Складские остатки вам не видны — в колонке «Доступно» указано «неизвестно» (это не ноль)." }
    RowLayout {
        Layout.fillWidth: true; spacing: 8
        VChip { text: sel ? "Покрыто резервом: " + sel.covered_count : ""; tone: "success"; iconName: "circle-check" }
        VChip { text: sel ? "Открыто: " + sel.open_count : ""; tone: "info" }
        VChip { text: sel ? "Не сопоставлено: " + sel.unmatched_count : ""; tone: "neutral" }
        Item { Layout.fillWidth: true }
        Text { text: sel ? "Сформирована " + sel.created_at.replace("T", " ").substring(0, 16) + " UTC" : ""; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs }
    }
    SectionTitle { text: "Строки потребности"; iconName: "list-checks" }
    Repeater {
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
                    Text { text: (modelData.code ? modelData.code + " · " : "") + modelData.name; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                    Text { text: modelData.qty_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM; font.weight: Font.Bold }
                    VChip { text: modelData.state_label; tone: modelData.state_tone }
                    VButton { visible: nv.can_reserve === true && !!modelData.item_id && Number(modelData.coverage.open) > 0
                              text: "Зарезервировать"; compact: true; kind: "soft"; iconName: "bookmark"
                              onClicked: lineReserve.openFor(modelData) }
                }
                Text { visible: modelData.item_id !== null && modelData.item_id !== undefined
                       text: "Зарезервировано " + modelData.reserved_label + "  ·  не покрыто " + modelData.open_label + "  ·  доступно на складах " + modelData.available_label
                       color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.fillWidth: true; Layout.leftMargin: 32; wrapMode: Text.Wrap }
                Text { visible: (modelData.match_note || "") !== ""; text: modelData.match_note || ""
                       color: Theme.warning; font.family: Theme.family; font.pixelSize: Theme.fsXs; Layout.fillWidth: true; Layout.leftMargin: 32; wrapMode: Text.Wrap }
            }
        }
    }
    HistoryList { objectType: "procurement_need"; objectId: page.selectedId }

    // ---------- сформировать потребность
    FormDialog {
        id: genForm
        objectName: "genForm"
        title: "Сформировать потребность"
        subtitle: "Из последней зафиксированной версии расчёта комплекта. Строки копируются без цен; это не заказ поставщику."
        confirmText: "Сформировать"
        canConfirm: gVer.currentValue !== "" && gVer.currentValue !== undefined
        onConfirmed: cGen.run("need.generate", { id: backend.newId(), calc_version_id: gVer.currentValue })
        VSelect { id: gVer; label: "Версия расчёта"
                  model: (nv.candidates || []).map(c => ({ id: c.calc_version_id, label: c.project_code + " · " + c.kit_code + " v" + c.version_no + " — строк " + c.line_count })) }
    }
    Cmd { id: cGen; dialog: genForm; onDone: r => { if (r.status === "accepted" && r.result.need) page.selectedId = r.result.need.id } }

    // ---------- резерв под строку
    FormDialog {
        id: lineReserve
        objectName: "lineReserve"
        property var line: null
        readonly property var stockItem: line ? (sov.items || []).find(i => i.id === line.item_id) || null : null
        readonly property var whAvail: stockItem ? stockItem.by_warehouse.filter(b => Number(b.available) > 0).map(b => ({ id: b.warehouse_id, label: b.warehouse_code + " — доступно " + b.available_label })) : []
        title: line ? "Резерв под строку: " + (line.code || line.name) : ""
        subtitle: line ? "Не покрыто " + line.open_label + ". Резерв больше непокрытого сервер не примет." : ""
        confirmText: "Зарезервировать"
        canConfirm: lrQty.text.trim() !== "" && lrQty.error === "" && lrReason.text.trim() !== "" && lrWh.currentValue !== ""
        function openFor(l) { line = l; lrQty.text = backend.normalizeQty(l.coverage.open).replace(/\.?0+$/, ""); lrReason.text = ""; lrWh.currentIndex = 0; open() }
        onConfirmed: cLine.run("stock.reserve", { id: backend.newId(), warehouse_id: lrWh.currentValue, item_id: line.item_id, project_id: page.sel.project_id,
                                                  qty: backend.normalizeQty(lrQty.text), reason: lrReason.text.trim(), need_id: page.sel.id, need_line_id: line.line_id })
        Banner { visible: lineReserve.whAvail.length === 0; tone: "warning"; text: "Свободного количества на складах нет — нужна закупка." }
        VSelect { id: lrWh; label: "Склад"; model: lineReserve.whAvail }
        VField { id: lrQty; label: "Количество"; required: true; error: backend.qtyProblem(text) }
        VField { id: lrReason; objectName: "lrReason"; label: "Основание резерва"; required: true; placeholder: "Под монтаж …" }
    }
    Cmd { id: cLine; dialog: lineReserve }
}
