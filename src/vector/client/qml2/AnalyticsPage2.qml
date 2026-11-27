import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../qml"

// Раздел «Аналитика» 2.0 (D-127): сводная аналитика портфеля проектов в стиле оболочки 2.0 —
// карточки-плитки вместо таблиц, акцентные числа, понятные подписи. Только чтение из снимка pull
// (работает и без связи); денег здесь нет — права на цены отдельные, в сводке лишь сроки и количества.
// Виден по праву norm.read (производственное руководство: РП, ГИП, руководитель отдела, ведущий инженер).
Item {
    id: page
    objectName: "analyticsPage2"
    property var shell
    readonly property var v: backend.portfolioAnalyticsView(backend.dataRevision)
    readonly property var proc: v.procurement || ({})
    readonly property var prod: v.production || ({})

    Flickable {
        anchors.fill: parent
        contentWidth: width; contentHeight: col.implicitHeight + Theme2.pageMargin
        clip: true; boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        ColumnLayout {
            id: col
            width: parent.width - Theme2.pageMargin * 2
            x: Theme2.pageMargin; y: Theme2.pageMargin
            spacing: Theme2.gap + 4

            // ---------- шапка страницы
            RowLayout { Layout.fillWidth: true; spacing: Theme2.pad
                ColumnLayout { spacing: 4; Layout.fillWidth: true
                    Text { text: "Аналитика"; color: Theme2.text; font.family: Theme2.family
                           font.pixelSize: Theme2.fsXl; font.weight: Font.Bold }
                    Text { text: "Портфель проектов: сроки потоков, закупки, производство и качество — агрегаты без персональных данных"
                           color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                           wrapMode: Text.WordWrap; Layout.fillWidth: true } }
                VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() }
            }

            // ---------- проекты по статусам + активные риски (плитки)
            Text { text: "Проекты"; color: Theme2.textMuted; font.family: Theme2.family
                   font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0
                   font.weight: Font.DemiBold; font.pixelSize: Theme2.fsXs }
            Flow {
                Layout.fillWidth: true; spacing: Theme2.gap
                Repeater {
                    model: page.v.project_rows || []
                    delegate: Rectangle {
                        required property var modelData
                        objectName: "an2ProjectTile"
                        width: Theme2.compact ? 168 : 200; height: Theme2.compact ? 64 : 72
                        radius: Theme2.radius; color: Theme2.surfaceAlt; border.color: Theme2.border
                        ColumnLayout { anchors.fill: parent; anchors.margins: Theme2.gap + 2; spacing: 0
                            Text { text: modelData.label; color: Theme2.textMuted; font.family: Theme2.family
                                   font.pixelSize: Theme2.fsXs; elide: Text.ElideRight; Layout.fillWidth: true }
                            Text { text: String(modelData.count); color: Theme2.text; font.family: Theme2.family
                                   font.pixelSize: Theme2.fsL; font.weight: Font.Bold } }
                    }
                }
                Rectangle {
                    objectName: "an2RiskTile"
                    width: Theme2.compact ? 168 : 200; height: Theme2.compact ? 64 : 72
                    radius: Theme2.radius
                    color: (page.v.active_risks || 0) > 0 ? Theme2.warningSoft : Theme2.surfaceAlt
                    border.color: (page.v.active_risks || 0) > 0 ? Qt.alpha(Theme2.warning, 0.5) : Theme2.border
                    ColumnLayout { anchors.fill: parent; anchors.margins: Theme2.gap + 2; spacing: 0
                        RowLayout { spacing: 6
                            Icon { name: "triangle-alert"; size: 13
                                   color: (page.v.active_risks || 0) > 0 ? Theme2.warning : Theme2.textMuted }
                            Text { text: "Активные риски"; color: Theme2.textMuted; font.family: Theme2.family
                                   font.pixelSize: Theme2.fsXs } }
                        Text { text: String(page.v.active_risks || 0)
                               color: (page.v.active_risks || 0) > 0 ? Theme2.warning : Theme2.text
                               font.family: Theme2.family; font.pixelSize: Theme2.fsL; font.weight: Font.Bold } }
                }
            }

            // ---------- потоки проектов: план и факт по шести потокам
            Text { text: "Потоки"; color: Theme2.textMuted; font.family: Theme2.family; Layout.topMargin: 6
                   font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0
                   font.weight: Font.DemiBold; font.pixelSize: Theme2.fsXs }
            Repeater {
                model: page.v.streams || []
                delegate: Rectangle {
                    id: sCard
                    required property var modelData
                    objectName: "an2StreamRow"
                    Layout.fillWidth: true; implicitHeight: sRow.implicitHeight + Theme2.gap + 6
                    radius: Theme2.radius; color: Theme2.surfaceAlt; border.color: Theme2.border
                    RowLayout {
                        id: sRow
                        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter
                                  leftMargin: Theme2.gap + 4; rightMargin: Theme2.gap + 4 }
                        spacing: Theme2.gap + 2
                        Text { text: sCard.modelData.label; color: Theme2.text; font.family: Theme2.family
                               font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold
                               Layout.preferredWidth: Theme2.compact ? 180 : 220; elide: Text.ElideRight }
                        VChip { text: "завершено: " + sCard.modelData.terminal
                                tone: sCard.modelData.terminal > 0 ? "success" : "neutral" }
                        Item { Layout.fillWidth: true }
                        Text { text: sCard.modelData.plan_n > 0
                                     ? "план (медиана): " + sCard.modelData.plan_median + " дн. · по " + sCard.modelData.plan_n
                                     : "плановые даты не заданы"
                               color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                        Text { text: sCard.modelData.fact_n > 0
                                     ? "факт (медиана): " + sCard.modelData.fact_median + " дн. · по " + sCard.modelData.fact_n
                                     : "факта пока нет"
                               color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsXs } }
                }
            }

            // ---------- закупки: lead-time и опоздания
            Text { text: "Закупки"; color: Theme2.textMuted; font.family: Theme2.family; Layout.topMargin: 6
                   font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0
                   font.weight: Font.DemiBold; font.pixelSize: Theme2.fsXs }
            Rectangle {
                objectName: "an2Procurement"
                Layout.fillWidth: true; implicitHeight: pCol.implicitHeight + Theme2.pad
                radius: Theme2.radius; color: Theme2.surfaceAlt; border.color: Theme2.border
                ColumnLayout {
                    id: pCol
                    anchors { left: parent.left; right: parent.right; top: parent.top
                              margins: Theme2.gap + 4; leftMargin: Theme2.gap + 8; rightMargin: Theme2.gap + 8 }
                    spacing: 6
                    RowLayout { spacing: Theme2.gap + 6
                        Icon { name: "shopping-cart"; size: 16; color: Theme2.primary }
                        Text { text: "Размещённых и закрытых заказов: " + (page.proc.placed_total || 0)
                                     + "  ·  lead-time (размещён → закрыт), медиана: " + (page.proc.lead_median || "—")
                                     + " дн." + (page.proc.lead_n > 0 ? " (по " + page.proc.lead_n + " заказам)"
                                                                        : " (закрытых заказов пока нет)")
                               color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                               wrapMode: Text.WordWrap; Layout.fillWidth: true } }
                    Text { text: (page.proc.late_count > 0
                                  ? "Опоздали к ожидаемой дате: " + page.proc.late_count
                                    + " (медиана опоздания " + page.proc.late_median + " дн.)"
                                  : "Опоздавших к ожидаемой дате нет (или даты не заданы)")
                           color: page.proc.late_count > 0 ? Theme2.danger : Theme2.textMuted
                           font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                           wrapMode: Text.WordWrap; Layout.fillWidth: true } }
            }

            // ---------- производство и качество
            Text { text: "Производство и качество"; color: Theme2.textMuted; font.family: Theme2.family
                   Layout.topMargin: 6
                   font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0
                   font.weight: Font.DemiBold; font.pixelSize: Theme2.fsXs }
            Rectangle {
                objectName: "an2Production"
                Layout.fillWidth: true; implicitHeight: qCol.implicitHeight + Theme2.pad
                radius: Theme2.radius; color: Theme2.surfaceAlt; border.color: Theme2.border
                ColumnLayout {
                    id: qCol
                    anchors { left: parent.left; right: parent.right; top: parent.top
                              margins: Theme2.gap + 4; leftMargin: Theme2.gap + 8; rightMargin: Theme2.gap + 8 }
                    spacing: 6
                    RowLayout { spacing: Theme2.gap + 6
                        Icon { name: "factory"; size: 16; color: Theme2.primary }
                        Text { text: "Нарядов: " + (page.prod.work_orders || 0)
                                     + "  ·  замен в составе: " + (page.prod.substitutions || 0)
                                     + "  ·  рекламаций: " + (page.v.claims_total || 0)
                               color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                               wrapMode: Text.WordWrap; Layout.fillWidth: true } }
                    Repeater {
                        model: page.v.claims || []
                        delegate: Text { required property var modelData
                            text: modelData.label + ": " + modelData.total + " (закрыто " + modelData.closed + ")"
                            color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs } }
                }
            }
            Item { implicitHeight: 8 }
        }
    }
}
