import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Раздел «Аналитика» (D-73, этап 7 blueprint 52): сводная аналитика портфеля проектов — только чтение
// из снимка pull (работает и без связи), денег здесь нет (права на цены отдельные; сроки и количества).
//  * «Проекты» — сколько проектов в каждом статусе, сколько активных рисков;
//  * «Потоки» — по каждому из шести потоков: сколько завершено, медианы плана и факта в днях;
//  * «Закупки» — lead-time заказов (размещён → закрыт), медиана и опоздавшие к ожидаемой дате;
//  * «Производство и качество» — наряды и замены в них, рекламации по источникам.
// Виден по праву norm.read (производственное руководство: РП, ГИП, руководитель отдела, ведущий инженер).
Item {
    id: page
    objectName: "analyticsPage"
    property var shell
    readonly property var v: backend.portfolioAnalyticsView(backend.dataRevision)
    readonly property var proc: v.procurement || ({})
    readonly property var prod: v.production || ({})

    Flickable {
        anchors.fill: parent; contentWidth: width; contentHeight: col.implicitHeight + 24
        clip: true; boundsBehavior: Flickable.StopAtBounds
        ColumnLayout {
            id: col
            width: parent.width - 24; x: 12; y: 12; spacing: 10
            RowLayout {
                Layout.fillWidth: true
                PageHeader { title: "Аналитика"; subtitle: "Портфель проектов: сроки потоков, закупки, производство и качество (агрегаты без персональных данных)" }
                Item { Layout.fillWidth: true }
                VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() }
            }

            SectionTitle { text: "Проекты"; iconName: "folder-kanban" }
            Flow {
                Layout.fillWidth: true; spacing: 8
                Repeater {
                    model: page.v.project_rows || []
                    delegate: VCard { required property var modelData
                        Layout.preferredWidth: 210
                        ColumnLayout { spacing: 2
                            Text { text: modelData.label; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                            Text { text: String(modelData.count); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold }
                        }
                    }
                }
                VCard { Layout.preferredWidth: 210
                    ColumnLayout { spacing: 2
                        Text { text: "Активные риски"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                        Text { text: String(page.v.active_risks || 0); color: page.v.active_risks > 0 ? Theme.warning : Theme.text
                               font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold }
                    }
                }
            }

            SectionTitle { text: "Потоки"; iconName: "network" }
            Repeater {
                model: page.v.streams || []
                delegate: VCard { required property var modelData
                    Layout.fillWidth: true
                    RowLayout { Layout.fillWidth: true; spacing: 10
                        Text { text: modelData.label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs
                               font.weight: Font.DemiBold; Layout.preferredWidth: 220; elide: Text.ElideRight }
                        Text { text: "завершено: " + modelData.terminal; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS }
                        Item { Layout.fillWidth: true }
                        Text { text: modelData.plan_n > 0 ? "план (медиана): " + modelData.plan_median + " дн. (по " + modelData.plan_n + ")" : "плановые даты не заданы"
                               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                        Text { text: modelData.fact_n > 0 ? "факт (медиана): " + modelData.fact_median + " дн. (по " + modelData.fact_n + ")" : "факта пока нет"
                               color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS }
                    }
                }
            }

            SectionTitle { text: "Закупки"; iconName: "shopping-cart" }
            VCard { Layout.fillWidth: true
                ColumnLayout { spacing: 2; Layout.fillWidth: true
                    Text { text: "Размещённых и закрытых заказов: " + (page.proc.placed_total || 0)
                                 + "  ·  lead-time (размещён → закрыт), медиана: " + (page.proc.lead_median || "—")
                                 + " дн." + (page.proc.lead_n > 0 ? " (по " + page.proc.lead_n + ")" : " (закрытых заказов нет)")
                           color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Text { text: (page.proc.late_count > 0
                                  ? "Опоздали к ожидаемой дате: " + page.proc.late_count + " (медиана опоздания " + page.proc.late_median + " дн.)"
                                  : "Опоздавших к ожидаемой дате нет (или даты не заданы)")
                           color: page.proc.late_count > 0 ? Theme.danger : Theme.textMuted
                           font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                }
            }

            SectionTitle { text: "Производство и качество"; iconName: "factory" }
            VCard { Layout.fillWidth: true
                ColumnLayout { spacing: 2; Layout.fillWidth: true
                    Text { text: "Нарядов: " + (page.prod.work_orders || 0) + "  ·  замен в составе: " + (page.prod.substitutions || 0)
                                 + "  ·  рекламаций: " + (page.v.claims_total || 0)
                           color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Repeater {
                        model: page.v.claims || []
                        delegate: Text { required property var modelData
                            text: modelData.label + ": " + modelData.total + " (закрыто " + modelData.closed + ")"
                            color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                    }
                }
            }
            Item { implicitHeight: 8 }
        }
    }
}
