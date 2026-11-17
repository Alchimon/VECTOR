import QtQuick
import QtQuick.Layouts

// Строка перемещения между складами (D-29): позиция (по желанию), маршрут «откуда → куда», количество, статус и —
// для перемещений «в пути» — действия склада: «Принять» (приход на складе-получателе) и «Вернуть» (приход обратно на
// складе-отправителе), а если не пришло ничего — «Утрата» (акт об утрате, D-31). Пока перемещение в пути, количества
// нет ни на одном складе.
// Кнопки активны по признаку can_act из stock_view (роль «склад», есть связь, не историческая запись v8);
// окончательное решение — за сервером (версия перемещения, статус, состояние складов).
Rectangle {
    id: row
    property var t: ({})
    property bool showItem: true
    signal acceptRequested()
    signal returnRequested()
    signal lostRequested()
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
            Icon { name: "truck"; size: 15; color: row.t.status === "in_transit" ? Theme.tone("warning").fg : Theme.textMuted }
            Text { text: (row.showItem ? row.t.item_code + " · " + row.t.item_name + "  —  " : "") + row.t.route_label + "  ·  " + row.t.qty_label
                   color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            // Срок прибытия — только для справки (D-30): прошёл — пометка, никаких действий программа не делает.
            VChip { visible: row.t.overdue === true; text: "срок прошёл"; tone: "danger"; iconName: "clock" }
            VChip { text: row.t.status_label || ""; tone: row.t.status_tone || "neutral" }
            VButton { visible: row.t.status === "in_transit"; text: "Принять"; compact: true; iconName: "check"; enabled: row.t.can_act === true; onClicked: row.acceptRequested() }
            VButton { visible: row.t.status === "in_transit"; text: "Вернуть"; compact: true; kind: "ghost"; iconName: "undo-2"; enabled: row.t.can_act === true; onClicked: row.returnRequested() }
            VButton { objectName: "btnLost"; visible: row.t.status === "in_transit"; text: "Утрата"; compact: true; kind: "ghost"; iconName: "package-x"; enabled: row.t.can_act === true; onClicked: row.lostRequested() }
        }
        Text { text: "Отгружено " + (row.t.created_label || "") + " UTC" + (row.t.author ? "  ·  " + row.t.author : "") + "  ·  " + (row.t.reason || "")
                     + (row.t.source_doc ? "  ·  " + row.t.source_doc : "") + (row.t.expected_label ? "  ·  ожидается " + row.t.expected_label : "")
               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 25 }
        Text { visible: row.t.status !== "in_transit" && row.t.status !== "lost"
               text: (row.t.legacy === true ? "Перемещение одним шагом (до появления статуса «в пути»)" : (row.t.status === "received" ? "Принято " : "Возвращено ") + (row.t.closed_label || "") + " UTC"
                     + (row.t.closer ? "  ·  " + row.t.closer : "") + (row.t.close_reason ? "  ·  " + row.t.close_reason : ""))
               color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 25 }
        // Акт недостачи (D-30): номер, количества, причина и подпись приёмщика. Акт неизменяем.
        RowLayout {
            visible: !!row.t.act_label
            Layout.fillWidth: true; Layout.leftMargin: 25; spacing: 6
            Icon { name: "file-exclamation-point"; size: 14; color: Theme.tone("danger").fg }
            Text { text: row.t.act_label || ""; color: Theme.tone("danger").fg; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.DemiBold
                   elide: Text.ElideRight; Layout.fillWidth: true }
        }
        Text { visible: !!row.t.act_signed_label; text: row.t.act_signed_label || ""
               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 45 }
        // Акт об утрате (D-31): не пришло ничего — количество списано актом.
        RowLayout {
            visible: !!row.t.loss_label
            Layout.fillWidth: true; Layout.leftMargin: 25; spacing: 6
            Icon { name: "package-x"; size: 14; color: Theme.tone("danger").fg }
            Text { text: row.t.loss_label || ""; color: Theme.tone("danger").fg; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.DemiBold
                   elide: Text.ElideRight; Layout.fillWidth: true }
        }
        Text { visible: !!row.t.loss_signed_label; text: row.t.loss_signed_label || ""
               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 45 }
    }
}
