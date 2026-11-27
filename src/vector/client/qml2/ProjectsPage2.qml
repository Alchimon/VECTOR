import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../qml"

// Раздел «Проекты» в интерфейсе 2.0 (D-98): вместо master-detail — просторный список карточек во всю
// ширину; предпросмотр и действия — в инспекторе справа (InspectorPane2), двойной щелчок открывает
// проект во вкладке (как в браузере). Данные — те же backend.projects; логика прав и команд не меняется.
Item {
    id: page
    objectName: "projectsPage2"
    property var shell                         // Shell2: selectObject/openObject, navTarget
    property string selectedId: ""
    property string search: ""
    property string filter: "active"          // all | active | archive (D-106: чипы-фильтры)
    readonly property var filtered: backend.projects.filter(function(p) {
        if (filter === "active") return p.status === "rkd_in_progress" || p.status === "closing"
        if (filter === "archive") return p.status === "archived"
        return true
    })
    // Карточки — по коду убывания (свежие проекты сверху, D-123).
    readonly property var items: filtered.filter(p => search === "" || (p.code + " " + p.title + " " + (p.tender_title || "")).toLowerCase().indexOf(search.toLowerCase()) >= 0)
                                         .slice().sort((a, b) => (a.code < b.code) ? 1 : -1)

    // Переход к проекту из другого раздела (например, из тендера) — сразу открываем его вкладку.
    function handleNavTarget() {
        if (shell && shell.navTarget.page === "projects" && shell.navTarget.id !== "")
            shell.openObject("project", shell.navTarget.id)
    }
    Component.onCompleted: handleNavTarget()

    ColumnLayout {
        anchors { fill: parent; margins: Theme2.pageMargin }
        spacing: Theme2.gap + 4

        // ---------- заголовок и поиск
        RowLayout {
            Layout.fillWidth: true; spacing: Theme2.pad
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { text: "Проекты"; color: Theme2.text; font.family: Theme2.family
                       font.pixelSize: Theme2.fsXl; font.weight: Font.Bold }
                Text { text: backend.projects.length === 0
                       ? "Проект появляется после действия «Начать РКД» по выигранному тендеру — или когда вас назначат в проект."
                       : items.length + " из " + backend.projects.length + " доступных вам"
                       color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS }
            }
            VSearch { objectName: "projects2Search"; Layout.preferredWidth: Theme2.compact ? 240 : 340
                      implicitHeight: Theme2.compact ? 36 : 44
                      placeholderText: "Поиск по шифру, названию, заказчику…"
                      onTextChanged: page.search = text }
        }

        // ---------- фильтры-чипы (D-106): активные / архив / все
        RowLayout { spacing: 6; visible: backend.projects.length > 0
            Repeater {
                model: [{ id: "active", label: "Активные" }, { id: "archive", label: "Архив" }, { id: "all", label: "Все" }]
                delegate: AbstractButton {
                    id: fchip
                    property bool active: page.filter === modelData.id
                    Accessible.name: modelData.label
                    implicitHeight: 28; implicitWidth: ftext.implicitWidth + 22
                    hoverEnabled: true
                    onClicked: page.filter = modelData.id
                    background: Rectangle { radius: 14
                        color: fchip.active ? Theme2.primarySoft : fchip.hovered ? Theme2.hover : Theme2.surface
                        border.color: fchip.active ? Theme2.primary : Theme2.border }
                    contentItem: Text { id: ftext; text: modelData.label; horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        color: fchip.active ? Theme2.primary : Theme2.textMuted
                        font.family: Theme2.family; font.pixelSize: Theme2.fsS
                        font.weight: fchip.active ? Font.DemiBold : Font.Normal }
                }
            }
        }

        // ---------- список карточек
        ListView {
            id: list
            objectName: "projects2List"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            spacing: Theme2.gap
            model: page.items
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            delegate: Rectangle {
                id: card
                // Богатая карточка проекта по утверждённому концепту A (D-101): иконка-тайл, шифр и
                // название, заказчик, ответственный (ГИП), чип статуса, счётчики комплектов/документов,
                // зелёная точка активности. Предпросмотр — в инспекторе, двойной щелчок — вкладка.
                readonly property var tender: backend.tenders.find(t => t.id === modelData.tender_id) || null
                readonly property string customerName: tender ? (tender.customer_name || "") : ""
                readonly property var gip: backend.assignments.find(a => a.project_id === modelData.id && a.project_role === "gip") || null
                readonly property int docCount: backend.documents.filter(function(d) {
                    var k = backend.kits.find(function(kk) { return kk.id === d.kit_id })
                    return k && k.project_id === modelData.id
                }).length
                // Прогресс задач проекта (концепт A): открытые/все по комплектам проекта.
                readonly property var kitIds: backend.kits.filter(function(k) { return k.project_id === modelData.id })
                                                          .map(function(k) { return k.id })
                readonly property var projTasks: backend.tasks.filter(function(t) { return card.kitIds.indexOf(t.kit_id) >= 0 })
                readonly property int tasksOpen: projTasks.filter(function(t) { return t.status === "open" || t.status === "in_progress" }).length
                readonly property real taskProgress: projTasks.length > 0 ? 1 - tasksOpen / projTasks.length : 0
                readonly property bool inWork: modelData.status === "rkd_in_progress" || modelData.status === "closing"
                width: list.width
                implicitHeight: (Theme2.compact ? 88 : 112) + Theme2.pad
                radius: Theme2.radius
                color: ma.containsMouse ? Theme2.hover : Theme2.surface
                border.color: page.selectedId === modelData.id ? Theme2.primary : Theme2.cardBorder
                border.width: page.selectedId === modelData.id ? 2 : 1

                // Индикатор выбора — полоска слева, как в современных почтовых клиентах.
                Rectangle { visible: page.selectedId === modelData.id; anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter; width: 4
                            height: parent.height - 24; radius: 2; color: Theme2.primary }

                MouseArea {
                    id: ma
                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: { page.selectedId = modelData.id; page.shell.selectObject("project", modelData) }
                    onDoubleClicked: { page.selectedId = modelData.id; page.shell.openObject("project", modelData.id) }
                }

                RowLayout {
                    anchors { fill: parent; leftMargin: Theme2.pad; rightMargin: Theme2.pad
                              topMargin: Theme2.pad - 4; bottomMargin: Theme2.pad - 4 }
                    spacing: Theme2.pad

                    // Иконка-тайл: эмблема ХАБа из графического пака (D-99) на мягкой подложке — как в концепте A.
                    Rectangle { Layout.preferredWidth: Theme2.compact ? 52 : 64; Layout.preferredHeight: width
                                Layout.leftMargin: 4; radius: Theme2.radius
                                color: Theme2.surfaceAlt; border.color: Theme2.border
                        Image { anchors.centerIn: parent; width: parent.width - 14; height: width
                                source: "../assets/vektor/app_icons/png_64/vektor_hub.png"
                                sourceSize: Qt.size(64, 64); fillMode: Image.PreserveAspectFit; smooth: true; mipmap: true } }

                    ColumnLayout { spacing: Theme2.compact ? 3 : 6; Layout.fillWidth: true
                        RowLayout { spacing: 10
                            Text { text: modelData.code; color: Theme2.primary; font.family: Theme2.mono
                                   font.pixelSize: Theme2.fsM; font.weight: Font.Bold }
                            Text { text: modelData.title; color: Theme2.text; font.family: Theme2.family
                                   font.pixelSize: Theme2.fsM; font.weight: Font.DemiBold
                                   Layout.fillWidth: true; elide: Text.ElideRight }
                        }
                        // Заказчик и ответственный — кто платит и кто отвечает, видно сразу (концепт A).
                        RowLayout { spacing: 16
                            RowLayout { visible: card.customerName !== ""; spacing: 6
                                Icon { name: "building-2"; size: 13; color: Theme2.textFaint }
                                Text { text: card.customerName
                                       color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                                       Layout.maximumWidth: card.width * 0.34; elide: Text.ElideRight } }
                            RowLayout { visible: card.gip !== null; spacing: 6
                                Icon { name: "user"; size: 13; color: Theme2.textFaint }
                                Text { text: "ГИП: " + (card.gip ? card.gip.user_name : "")
                                       color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS } }
                        }
                        RowLayout { spacing: 14
                            // Точка активности: проект в работе — зелёная, как в концепте A.
                            Rectangle { width: 8; height: 8; radius: 4
                                        color: card.inWork ? Theme2.success : Theme2.textFaint }
                            Text { text: modelData.kit_count + " компл."
                                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS }
                            Text { text: card.docCount + " докум."
                                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS }
                            Text { text: "карточка v" + modelData.version
                                   color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsS }
                        }
                        // Полоса прогресса задач (концепт A): сколько закрыто от общего числа.
                        RowLayout { visible: card.projTasks.length > 0; spacing: 8
                            Rectangle { Layout.fillWidth: true; height: 4; radius: 2; color: Theme2.surfaceAlt
                                Rectangle { width: parent.width * card.taskProgress; height: parent.height; radius: 2
                                            color: card.taskProgress >= 1 ? Theme2.success : Theme2.info } }
                            Text { text: "Задачи: " + (card.projTasks.length - card.tasksOpen) + "/" + card.projTasks.length
                                   color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                        }
                    }
                    ColumnLayout { spacing: 6; Layout.alignment: Qt.AlignTop
                        VChip { text: modelData.status_label; tone: card.inWork ? "success" : "info" }
                        VChip { visible: modelData.pending === true; text: "Не отправлено"; tone: "warning"; iconName: "clock" } }
                    Icon { name: "chevron-right"; size: 16; color: Theme2.textFaint; Layout.alignment: Qt.AlignVCenter }
                }
            }

            // Пустое состояние — дружелюбное, с пояснением (вместо мёртвой колонки классики).
            ColumnLayout {
                visible: page.items.length === 0
                anchors.centerIn: parent; spacing: Theme2.gap
                Icon { name: "folder-kanban"; size: 44; color: Theme2.textFaint; Layout.alignment: Qt.AlignHCenter }
                Text { text: page.search !== "" ? "По запросу «" + page.search + "» ничего не найдено"
                           : page.filter === "archive" ? "В архиве пусто — завершённые проекты появятся здесь"
                           : page.filter === "all" ? "Проектов пока нет"
                           : "Активных проектов нет — загляните в «Архив» или «Все»"
                       color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsM
                       font.weight: Font.DemiBold; Layout.alignment: Qt.AlignHCenter }
                Text { text: "Проект появляется после действия «Начать РКД» по выигранному тендеру — или когда вас назначат в проект."
                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                       wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                       Layout.preferredWidth: 420 }
            }
        }
    }
}
