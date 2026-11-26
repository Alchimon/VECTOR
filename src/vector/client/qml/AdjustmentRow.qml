import QtQuick
import QtQuick.Layouts

// Строка корректировки остатка (D-77): позиция, склад, «было → станет» с разницей, основание (акт), статус и —
// для ждущей подписи — действия: «Подписать» (руководитель отдела, не автор) и «Отменить» (автор).
// Кнопки активны по признакам can_sign_row/can_cancel_row из stock_view; окончательное решение — за сервером
// (версия, статус, четыре глаза, предохранители stale_balance/ниже резервов).
Rectangle {
    id: row
    property var a: ({})
    signal signRequested()
    signal cancelRequested()
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
            Icon { name: "clipboard-check"; size: 15
                   color: row.a.status === "pending" ? Theme.tone("warning").fg : Theme.textMuted }
            Text { text: row.a.item_code + " · " + row.a.item_name
                   color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold
                   elide: Text.ElideRight; Layout.fillWidth: true }
            VChip { text: row.a.status_label || ""; tone: row.a.status_tone || "neutral" }
            VButton { objectName: "btnAdjSign"; visible: row.a.can_sign_row === true; text: "Подписать"; kind: "primary"
                      compact: true; iconName: "check"; onClicked: row.signRequested() }
            VButton { objectName: "btnAdjCancel"; visible: row.a.can_cancel_row === true; text: "Отменить"; kind: "ghost"
                      compact: true; iconName: "undo-2"; onClicked: row.cancelRequested() }
        }
        Text { text: row.a.warehouse_code + ": было " + (row.a.on_hand_label || "") + " → станет " + (row.a.new_qty_label || "")
                     + " (" + (row.a.delta_label || "") + ")  ·  " + (row.a.reason || "")
               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS
               elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 25 }
        Text { text: "Создал(а) " + (row.a.author || "—") + (row.a.requested_label ? ", " + row.a.requested_label : "")
                     + (row.a.status === "signed" && row.a.signer ? "  ·  подписал(а) " + row.a.signer : "")
                     + (row.a.status === "cancelled" && row.a.cancel_reason ? "  ·  отменено: " + row.a.cancel_reason : "")
               color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs
               elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 25 }
    }
}
