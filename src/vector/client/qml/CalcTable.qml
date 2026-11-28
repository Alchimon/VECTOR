import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic

// Таблица позиций расчёта (только чтение): №, позиция (наименование + обозначение), ед., количество, цена, сумма.
// Обозначение показано под наименованием, чтобы таблица помещалась и в узкой карточке проекта.
// Строки приходят уже с подписями (calc_view.preview): «цена неизвестна» вместо пустого места,
// сумма «—», если её нельзя посчитать, текст замечания — у проблемной строки.
Rectangle {
    id: t
    property var rows: []
    // Ширины колонок (ручной ресайз ручками в заголовке, D-138); наименование занимает остаток.
    property var w: ({ pos: 28, unit: 48, qty: 76, price: 116, amount: 128 })
    implicitHeight: col.implicitHeight
    radius: Theme.radius; color: Theme.surface; border.color: Theme.border
    clip: true

    component Cell: Text {
        property bool num: false
        color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs
        font.features: num ? { "tnum": 1 } : ({})
        horizontalAlignment: num ? Text.AlignRight : Text.AlignLeft
        elide: Text.ElideRight
    }
    // Заголовок столбца; tip — подсказка при наведении (D-37), например «ЕД.» — единица измерения.
    component Head: Text {
        id: head
        property bool num: false
        property string tip: ""
        property string col: ""                       // ключ колонки в t.w; непустой — можно тянуть ручку (D-138)
        VTipArea { text: head.tip }
        color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.DemiBold
        font.letterSpacing: 0.4
        horizontalAlignment: num ? Text.AlignRight : Text.AlignLeft
        MouseArea {                                   // ручка ресайза у правого края заголовка
            visible: head.col !== ""
            anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
            anchors.rightMargin: -5                   // захват на половину межколоночного зазора
            width: 10; hoverEnabled: true; cursorShape: Qt.SplitHCursor
            property real startX: 0; property real startW: 0
            onPressed: function (m) { startX = mapToGlobal(m.x, m.y).x; startW = t.w[head.col] }
            onPositionChanged: function (m) {
                if (!pressed) return
                var dx = mapToGlobal(m.x, m.y).x - startX
                var nw = Math.max(28, Math.min(300, Math.round(startW + dx)))
                if (nw !== t.w[head.col]) { var o = Object.assign({}, t.w); o[head.col] = nw; t.w = o }
            }
            onDoubleClicked: { var o = Object.assign({}, t.w); o[head.col] = ({ pos: 28, unit: 48, qty: 76, price: 116, amount: 128 })[head.col]; t.w = o }
            Rectangle { anchors.centerIn: parent; width: 1; height: 14
                        color: parent.containsMouse || parent.pressed ? Theme.borderStrong : "transparent" }
            Accessible.name: "Ширина колонки: потяните мышью; двойной щелчок — вернуть как было"
        }
    }

    function _rowText(r) {  // строка таблицы текстом (табуляции — вставляется в письмо и в таблицу)
        return [r.pos, r.code || "", r.name || "", r.unit || "", r.qty_label, r.price_label, r.amount_label].join("\t")
    }

    Menu {
        id: rowMenu
        objectName: "calcRowMenu"
        property var row: null
        function openFor(r) { rowMenu.row = r; rowMenu.popup() }
        MenuItem { text: "Копировать строку"
                   onTriggered: backend.copyText(rowMenu.row ? t._rowText(rowMenu.row) : "") }
        MenuItem { text: "Копировать позицию"
                   onTriggered: backend.copyText(rowMenu.row ? (rowMenu.row.name || "") : "") }
        MenuSeparator {}
        MenuItem { text: "Копировать всю таблицу"
                   onTriggered: backend.copyText("№\tШифр\tПозиция\tЕд.\tКол-во\tЦена\tСумма\n"
                                                 + t.rows.map(t._rowText).join("\n")) }
    }

    ColumnLayout {
        id: col
        anchors.left: parent.left; anchors.right: parent.right
        spacing: 0
        Rectangle {
            Layout.fillWidth: true; implicitHeight: 32; color: Theme.surfaceAlt; radius: Theme.radius
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: parent.radius; color: Theme.surfaceAlt }
            RowLayout { anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 10
                Head { text: "№"; tip: "Номер позиции в расчёте"; col: "pos"; Layout.preferredWidth: t.w.pos }
                Head { text: "ПОЗИЦИЯ"; tip: "Обозначение (код) и наименование позиции"; Layout.fillWidth: true }
                Head { text: "ЕД."; tip: "ЕД. — единица измерения (шт, м, компл.…)"; col: "unit"; Layout.preferredWidth: t.w.unit }
                Head { text: "КОЛ-ВО"; tip: "КОЛ-ВО — количество"; num: true; col: "qty"; Layout.preferredWidth: t.w.qty }
                Head { text: "ЦЕНА"; tip: "Цена за единицу, в валюте расчёта"; num: true; col: "price"; Layout.preferredWidth: t.w.price }
                Head { text: "СУММА"; tip: "Сумма по позиции: количество × цена"; num: true; col: "amount"; Layout.preferredWidth: t.w.amount }
            }
        }
        Repeater {
            model: t.rows
            delegate: Rectangle {
                required property var modelData
                required property int index
                Layout.fillWidth: true
                implicitHeight: rc.implicitHeight + 14
                color: modelData.problem ? Theme.dangerSoft : index % 2 ? Theme.surfaceAlt : Theme.surface
                MouseArea { anchors.fill: parent; z: 5; acceptedButtons: Qt.RightButton
                            onClicked: rowMenu.openFor(modelData) }
                Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.border }
                ColumnLayout { id: rc; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                               anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 2
                    RowLayout { Layout.fillWidth: true; spacing: 10
                        Cell { text: modelData.pos; color: Theme.textFaint; Layout.preferredWidth: t.w.pos }
                        ColumnLayout { spacing: 0; Layout.fillWidth: true; Layout.minimumWidth: 120
                            Cell { text: modelData.name || "—"; Layout.fillWidth: true }
                            Text { visible: !!modelData.code || !!modelData.note
                                   text: [modelData.code, modelData.note].filter(x => !!x).join(" · ")
                                   color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs
                                   elide: Text.ElideRight; Layout.fillWidth: true } }
                        Cell { text: modelData.unit || "—"; Layout.preferredWidth: t.w.unit }
                        Cell { text: modelData.qty_label; num: true; Layout.preferredWidth: t.w.qty }
                        Cell { text: modelData.price_label; num: true; Layout.preferredWidth: t.w.price
                               color: modelData.price_unknown ? Theme.warning : Theme.text
                               font.italic: modelData.price_unknown === true }
                        Cell { text: modelData.amount_label; num: true; font.weight: Font.DemiBold; Layout.preferredWidth: t.w.amount }
                    }
                    Text { visible: !!modelData.problem; text: modelData.problem || ""; color: Theme.danger; font.family: Theme.family
                           font.pixelSize: Theme.fsXs; leftPadding: t.w.pos + 10; wrapMode: Text.Wrap; Layout.fillWidth: true }
                }
            }
        }
    }
}
