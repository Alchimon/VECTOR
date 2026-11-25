import QtQuick
import QtQuick.Layouts

// Строка предложения перемещения между проектами (D-78): позиция, склад, «откуда → куда», количество, основание,
// статус и — для открытого предложения — решения: «Перебросить резерв» (склад), «Закупим» (закупка), «Отказать»
// (любая сторона), «Отозвать» (автор, можно без связи — очередь). Предложение — не резерв, не заказ, не движение.
// Кнопки активны по признакам can_accept/can_order/can_reject/can_withdraw из stock_view; решает сервер.
Rectangle {
    id: row
    property var t: ({})
    signal acceptRequested()
    signal orderRequested()
    signal rejectRequested()
    signal withdrawRequested()
    implicitHeight: col.implicitHeight + 20
    radius: Theme.radius
    color: Theme.surfaceAlt
    ColumnLayout {
        id: col
        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: 14; anchors.rightMargin: 14
        spacing: 4
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Icon { name: "arrow-left-right"; size: 15
                   color: row.t.status === "proposed" ? Theme.tone("warning").fg : Theme.textMuted }
            Text { text: row.t.item_code + " · " + row.t.item_name
                   color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold
                   elide: Text.ElideRight; Layout.fillWidth: true }
            VChip { text: row.t.status_label || ""; tone: row.t.status_tone || "neutral" }
            VButton { objectName: "btnPropAccept"; visible: row.t.can_accept === true; text: "Перебросить резерв"
                      kind: "primary"; compact: true; iconName: "bookmark"; onClicked: row.acceptRequested() }
            VButton { objectName: "btnPropOrder"; visible: row.t.can_order === true; text: "Закупим"
                      kind: "soft"; compact: true; iconName: "shopping-cart"; onClicked: row.orderRequested() }
            VButton { objectName: "btnPropReject"; visible: row.t.can_reject === true; text: "Отказать"
                      kind: "ghost"; compact: true; iconName: "x"; onClicked: row.rejectRequested() }
            VButton { objectName: "btnPropWithdraw"; visible: row.t.can_withdraw === true; text: "Отозвать"
                      kind: "ghost"; compact: true; iconName: "undo-2"; onClicked: row.withdrawRequested() }
        }
        Text { text: row.t.warehouse_code + ": " + (row.t.from_label || "") + " → " + (row.t.to_label || "")
                     + "  ·  " + (row.t.qty_label || "") + "  ·  " + (row.t.reason || "")
               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS
               elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 25 }
        Text { text: "Предложил(а) " + (row.t.author || "—") + (row.t.created_label ? ", " + row.t.created_label : "")
                     + (row.t.decider ? "  ·  решил(а) " + row.t.decider + (row.t.decided_label ? ", " + row.t.decided_label : "") : "")
                     + (row.t.decision_note ? "  ·  " + row.t.decision_note : "")
               color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs
               elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 25 }
    }
}
