import QtQuick
import QtQuick.Layouts

// Строка журнала движений: № и время, вид (приход/выдача/сторно/половина перемещения), количество со знаком, склад,
// проект, маршрут перемещения, основание.
// Проведённая запись неизменяема; единственное действие — «Сторно» (встречное движение), один раз. У половины
// перемещения сторно нет: перемещение исправляют обратным перемещением (A-28).
Rectangle {
    id: row
    property var m: ({})
    property bool showItem: false
    property bool canPost: false
    signal reverseRequested()
    implicitHeight: col.implicitHeight + 20
    radius: Theme.radius
    color: Theme.surfaceAlt
    opacity: row.m.reversed === true ? 0.72 : 1
    ColumnLayout {
        id: col
        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: 14; anchors.rightMargin: 14
        spacing: 4
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Text { text: "№ " + row.m.doc_no; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.DemiBold; Layout.preferredWidth: 52 }
            VChip { text: (row.m.kind_label || "") + (row.m.reverses_doc_no ? " № " + row.m.reverses_doc_no : ""); tone: row.m.is_storno === true ? "danger" : (row.m.tone || "neutral"); iconName: row.m.is_storno === true ? "undo-2" : row.m.is_transfer === true ? "arrow-left-right" : (row.m.kind === "receipt" ? "package-plus" : "package-minus") }
            Text { text: (row.showItem ? row.m.item_code + " · " + row.m.item_name : "склад " + row.m.warehouse_code)
                   color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { text: row.m.qty_label || ""; color: row.m.kind === "receipt" ? Theme.success : Theme.warning; font.family: Theme.family; font.pixelSize: Theme.fsM; font.weight: Font.Bold }
            VChip { visible: row.m.reversed === true; text: "Сторнировано"; tone: "neutral" }
            VButton { visible: row.m.is_storno !== true && row.m.reversed !== true && row.m.is_transfer !== true; text: "Сторно"; compact: true; kind: "ghost"; iconName: "undo-2"; enabled: row.canPost; onClicked: row.reverseRequested() }
        }
        Text { text: row.m.posted_label + " UTC" + (row.showItem ? "  ·  склад " + row.m.warehouse_code : "") + (row.m.project_code ? "  ·  проект " + row.m.project_code : "") + (row.m.is_transfer === true ? "  ·  перемещение " + row.m.transfer_label : "") + (row.m.author ? "  ·  " + row.m.author : "")
               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 62 }
        Text { visible: text !== ""; text: [row.m.source_doc || "", row.m.reason || ""].filter(s => s !== "").join("  ·  ")
               color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: 62 }
    }
}
