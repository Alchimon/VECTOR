import QtQuick
import QtQuick.Layouts

// Официальный итог проекта (R-12): последняя ревизия, собранная из ЗАФИКСИРОВАННЫХ версий расчётов комплектов.
// Итоги по валютам не смешиваются (курс и пересчёт — не наше решение, Q-открыт); неполные итоги помечены.
// Каждая фиксация создаёт новую ревизию; прежние видны в истории ниже, ничего не перезаписывается.
ColumnLayout {
    id: pt
    property string projectId: ""
    property bool showHistory: false
    readonly property var pc: projectId ? backend.projectCalc(projectId, backend.dataRevision) : ({ revisions: [] })
    readonly property var latest: pc.latest || null
    spacing: 8

    RowLayout { Layout.fillWidth: true
        SectionTitle { text: "Итог проекта"; iconName: "wallet" }
        Item { Layout.fillWidth: true }
        VChip { visible: pt.latest !== null; text: pt.latest ? pt.latest.label + " · " + pt.latest.created_label : ""; tone: "success"; iconName: "lock" } }
    Text { visible: pt.latest === null; objectName: "projectTotalEmpty"
           text: "Официального итога ещё нет: он появится после первой фиксации расчёта комплекта ГИП проекта. Черновики в итог не входят."
           color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }

    // Плитки: по одной на валюту.
    Flow {
        visible: pt.latest !== null
        Layout.fillWidth: true; spacing: 10
        Repeater {
            model: pt.latest ? pt.latest.totals : []
            delegate: VCard {
                required property var modelData
                width: Math.max(240, tc.implicitWidth + 32); height: tc.implicitHeight + 26
                border.color: modelData.complete ? Theme.border : Theme.warning
                ColumnLayout { id: tc; anchors.left: parent.left; anchors.top: parent.top; anchors.margins: 13; spacing: 2
                    Text { text: modelData.currency_label + " · " + modelData.kits + " компл."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs }
                    Text { objectName: "projectTotalValue"; text: modelData.label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsL
                           font.weight: Font.Bold; font.features: { "tnum": 1 } }
                    Text { visible: !modelData.complete; text: "итог неполный"; color: Theme.warning; font.family: Theme.family; font.pixelSize: Theme.fsXs } }
            }
        }
    }
    Banner { visible: pt.latest !== null && pt.latest.mixed_currencies; tone: "info"; Layout.fillWidth: true
             text: "Комплекты рассчитаны в разных валютах — итоги показаны отдельно и не суммируются: правило пересчёта не утверждено." }

    // Состав последней ревизии.
    Repeater {
        model: pt.latest ? pt.latest.items : []
        delegate: Rectangle {
            required property var modelData
            Layout.fillWidth: true; implicitHeight: 36; radius: Theme.radiusS; color: Theme.surfaceAlt
            RowLayout { anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 10
                Text { text: modelData.kit_code; color: Theme.primary; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.Bold; Layout.preferredWidth: 90 }
                Text { text: modelData.kit_title || ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; elide: Text.ElideRight; Layout.fillWidth: true }
                VChip { text: "v" + modelData.version_no; tone: "neutral" }
                Text { text: modelData.total_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold
                       font.features: { "tnum": 1 }; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 260; elide: Text.ElideLeft }
            }
        }
    }
    Text { visible: pt.latest !== null; text: pt.latest ? "Основание: «" + pt.latest.reason + "» · " + pt.latest.author_name : ""
           color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }

    // История ревизий.
    Text { visible: (pt.pc.revisions || []).length > 1
           text: (pt.showHistory ? "Скрыть историю ревизий" : "История ревизий (" + pt.pc.revisions.length + ")")
           color: Theme.primary; font.family: Theme.family; font.pixelSize: Theme.fsS
           MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pt.showHistory = !pt.showHistory } }
    Repeater {
        model: pt.showHistory ? pt.pc.revisions : []
        delegate: Rectangle {
            required property var modelData
            Layout.fillWidth: true; implicitHeight: hc.implicitHeight + 14; radius: Theme.radiusS; color: Theme.surface; border.color: Theme.border
            ColumnLayout { id: hc; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                           anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 1
                Text { text: modelData.label + " · " + modelData.created_label + " · " + modelData.author_name + " · «" + modelData.reason + "»"
                       color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                Text { text: modelData.totals.map(t => t.label).join("  |  ") + "  ·  состав: " + modelData.composition
                       color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }
            }
        }
    }
    HistoryList { objectType: "project_calc"; objectId: pt.projectId }
}
