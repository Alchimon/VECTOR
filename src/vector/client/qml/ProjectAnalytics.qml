import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Блок «Аналитика проекта» на карточке проекта (D-71, этап 7 blueprint 52):
//  * план/факт по потокам: плановые даты из карточек потоков, фактические — из журнала событий
//    (первый выход из начального статуса и вход в конечный); длительности в днях;
//  * сводка этапа 6: запросы ПНР (всего/выполнено), испытания FAT (предварительные/финальные, успешные/
//    неуспешные), замечания (всего/блокирующие/закрытые), документы доставки;
//  * если проект в архиве — факты неизменяемого снимка закрытия с контрольной суммой.
//
// Только чтение из снимка pull — работает и без связи; команд здесь нет.
ColumnLayout {
    id: pa
    property var project: null
    readonly property var v: project ? backend.projectAnalytics(project.id, backend.dataRevision) : ({})
    readonly property var streamRows: v.streams || []
    readonly property var facts: v.facts || ({})
    spacing: 8

    RowLayout {
        Layout.fillWidth: true
        SectionTitle { text: "Аналитика проекта"; iconName: "sliders-horizontal" }
        Item { Layout.fillWidth: true }
        Text { text: pa.v.total_streams ? "потоков завершено: " + (pa.v.done_streams || 0) + " из " + pa.v.total_streams : ""
               color: Theme.textMuted; font.pixelSize: Theme.fsS }
    }

    // ---------- план/факт по потокам
    Repeater {
        model: pa.streamRows
        delegate: VCard {
            id: aCard
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: aCol.implicitHeight + 16
            ColumnLayout {
                id: aCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8
                spacing: 2
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Text { text: aCard.modelData.kind_label; color: Theme.text; font.pixelSize: Theme.fsS
                           font.weight: Font.DemiBold; Layout.preferredWidth: 170; elide: Text.ElideRight }
                    VChip { text: aCard.modelData.status_label; tone: aCard.modelData.terminal ? "success" : "info" }
                    Item { Layout.fillWidth: true }
                    Text { text: aCard.modelData.plan_days !== null && aCard.modelData.plan_days !== undefined
                                 ? "план " + aCard.modelData.plan_days + " дн." : "план не задан"
                           color: Theme.textFaint; font.pixelSize: Theme.fsXs }
                    Text { text: aCard.modelData.fact_days !== null && aCard.modelData.fact_days !== undefined
                                 ? "факт " + aCard.modelData.fact_days + " дн." : ""
                           color: Theme.text; font.pixelSize: Theme.fsXs }
                }
                Text { text: (aCard.modelData.actual_start !== "" ? "начат " + aCard.modelData.actual_start : "не начат")
                             + (aCard.modelData.actual_finish !== "" ? " · завершён " + aCard.modelData.actual_finish : "")
                       color: Theme.textMuted; font.pixelSize: Theme.fsXs }
            }
        }
    }

    // ---------- сводка этапа 6
    Flow {
        Layout.fillWidth: true; Layout.topMargin: 4; spacing: 8
        VCard {
            Layout.preferredWidth: 250; implicitHeight: sumCol.implicitHeight + 16
            ColumnLayout {
                id: sumCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8
                spacing: 2
                Text { text: "ПНР"; color: Theme.textMuted; font.pixelSize: Theme.fsXs }
                Text { text: "запросов: " + ((pa.facts.pnr || {}).total || 0)
                             + ", выполнено " + ((pa.facts.pnr || {}).done || 0)
                             + (((pa.facts.pnr || {}).site || 0) > 0 ? ", выездных " + pa.facts.pnr.site : "")
                       color: Theme.text; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Text { text: "FAT"; color: Theme.textMuted; font.pixelSize: Theme.fsXs; Layout.topMargin: 4 }
                Text { text: "испытаний: " + ((pa.facts.fat || {}).total || 0)
                             + " (предв. " + ((pa.facts.fat || {}).preliminary || 0)
                             + ", финальных " + ((pa.facts.fat || {}).final || 0) + ")"
                             + "; успешных " + ((pa.facts.fat || {}).passed || 0)
                             + (((pa.facts.fat || {}).failed || 0) > 0 ? ", неуспешных " + pa.facts.fat.failed : "")
                       color: Theme.text; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }
        }
        VCard {
            Layout.preferredWidth: 250; implicitHeight: sumCol2.implicitHeight + 16
            ColumnLayout {
                id: sumCol2
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8
                spacing: 2
                Text { text: "Замечания FAT"; color: Theme.textMuted; font.pixelSize: Theme.fsXs }
                Text { text: "всего " + ((pa.facts.findings || {}).total || 0)
                             + ", блокирующих " + ((pa.facts.findings || {}).blocking || 0)
                             + ", закрыто " + ((pa.facts.findings || {}).closed || 0)
                       color: Theme.text; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Text { text: "Документы доставки"; color: Theme.textMuted; font.pixelSize: Theme.fsXs; Layout.topMargin: 4 }
                Text { text: "выписано: " + (pa.facts.delivery_docs || 0)
                       color: Theme.text; font.pixelSize: Theme.fsS }
            }
        }
    }

    // ---------- снимок закрытия (архив): зафиксированные факты
    Banner {
        visible: pa.v.closeout !== null && pa.v.closeout !== undefined
        Layout.fillWidth: true; tone: "success"
        text: pa.v.closeout ? "Архивный снимок от " + pa.v.closeout.created_label
                              + " (" + pa.v.closeout.closed_by_name + ") фиксирует итоговые факты; SHA-256: "
                              + ("" + pa.v.closeout.sha256).substring(0, 16) + "…" : ""
    }
}
