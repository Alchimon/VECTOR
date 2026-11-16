import QtQuick
import QtQuick.Layouts

// Таблица позиций расчёта (только чтение): №, позиция (наименование + обозначение), ед., количество, цена, сумма.
// Обозначение показано под наименованием, чтобы таблица помещалась и в узкой карточке проекта.
// Строки приходят уже с подписями (calc_view.preview): «цена неизвестна» вместо пустого места,
// сумма «—», если её нельзя посчитать, текст замечания — у проблемной строки.
Rectangle {
    id: t
    property var rows: []
    // Ширины колонок; наименование занимает остаток.
    readonly property var w: ({ pos: 28, unit: 48, qty: 76, price: 116, amount: 128 })
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
    component Head: Text {
        property bool num: false
        color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.DemiBold
        font.letterSpacing: 0.4
        horizontalAlignment: num ? Text.AlignRight : Text.AlignLeft
    }

    ColumnLayout {
        id: col
        anchors.left: parent.left; anchors.right: parent.right
        spacing: 0
        Rectangle {
            Layout.fillWidth: true; implicitHeight: 32; color: Theme.surfaceAlt; radius: Theme.radius
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: parent.radius; color: Theme.surfaceAlt }
            RowLayout { anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 10
                Head { text: "№"; Layout.preferredWidth: t.w.pos }
                Head { text: "ПОЗИЦИЯ"; Layout.fillWidth: true }
                Head { text: "ЕД."; Layout.preferredWidth: t.w.unit }
                Head { text: "КОЛ-ВО"; num: true; Layout.preferredWidth: t.w.qty }
                Head { text: "ЦЕНА"; num: true; Layout.preferredWidth: t.w.price }
                Head { text: "СУММА"; num: true; Layout.preferredWidth: t.w.amount }
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
