import QtQuick
import QtQuick.Layouts

// Строка черновой позиции пополнения расходника (D-79): позиция, склад, количество, статус и подтверждения
// обеих сторон (склад и закупка подтверждают по одному разу, только при связи; отклонить может любая).
// Кнопки активны по признакам can_confirm_wh/can_confirm_pr/can_dismiss из stock_view; решает сервер.
Rectangle {
    id: row
    property var t: ({})
    signal confirmRequested()
    signal dismissRequested()
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
            Icon { name: "package-plus"; size: 15
                   color: row.t.status === "open" ? Theme.tone("warning").fg : Theme.textMuted }
            Text { text: row.t.item_code + " · " + row.t.item_name
                   color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold
                   elide: Text.ElideRight; Layout.fillWidth: true }
            VChip { text: row.t.status_label || ""; tone: row.t.status_tone || "neutral" }
            VButton { objectName: "btnReplenConfirm"; visible: row.t.can_confirm_wh === true || row.t.can_confirm_pr === true
                      text: row.t.can_confirm_pr === true ? "Подтвердить закупку" : "Подтвердить"
                      kind: "primary"; compact: true; iconName: "check"; onClicked: row.confirmRequested() }
            VButton { objectName: "btnReplenDismiss"; visible: row.t.can_dismiss === true; text: "Отклонить"
                      kind: "ghost"; compact: true; iconName: "x"; onClicked: row.dismissRequested() }
        }
        Text { text: row.t.warehouse_code + "  ·  пополнить на " + (row.t.qty_label || "")
               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS
               elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 25 }
        Text { text: "Создан системой " + (row.t.created_label || "")
                     + (row.t.wh_confirmer ? "  ·  склад: " + row.t.wh_confirmer : "  ·  склад: ждём")
                     + (row.t.pr_confirmer ? "  ·  закупка: " + row.t.pr_confirmer : "  ·  закупка: ждём")
                     + (row.t.decision_note ? "  ·  " + row.t.decision_note : "")
               color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs
               elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 25 }
    }
}
