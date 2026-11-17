import QtQuick
import QtQuick.Layouts

// Строка резерва: позиция (по желанию), склад, проект, количество / израсходовано / осталось, статус, действия.
Rectangle {
    id: row
    property var r: ({})
    property bool showItem: false
    property bool canPost: false
    signal issueRequested()
    signal releaseRequested()
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
            Icon { name: "bookmark"; size: 15; color: Theme.textMuted }
            Text { text: (row.showItem ? row.r.item_code + " · " + row.r.item_name + "  —  " : "") + row.r.project_code + "  ·  склад " + row.r.warehouse_code
                   color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            VChip { text: row.r.status_label || ""; tone: row.r.status_tone || "neutral" }
            VButton { visible: row.r.status === "active"; text: "Выдать"; compact: true; iconName: "package-minus"; enabled: row.canPost; onClicked: row.issueRequested() }
            VButton { visible: row.r.status === "active"; text: "Снять"; compact: true; kind: "ghost"; iconName: "x"; enabled: row.canPost; onClicked: row.releaseRequested() }
        }
        Text { text: "Резерв " + row.r.qty_label + "  ·  израсходовано " + row.r.consumed_label + "  ·  осталось " + row.r.remaining_label
               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.fillWidth: true; Layout.leftMargin: 25 }
        Text { text: (row.r.reason || "") + (row.r.close_reason ? "  ·  снят: " + row.r.close_reason : "") + (row.r.need_id ? "  ·  под потребность" : "") + (row.r.author ? "  ·  " + row.r.author : "")
               color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 25 }
    }
}
