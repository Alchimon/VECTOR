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
    readonly property var items: backend.projects.filter(p => search === "" || (p.code + " " + p.title + " " + (p.tender_title || "")).toLowerCase().indexOf(search.toLowerCase()) >= 0)

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
                width: list.width
                implicitHeight: Theme2.cardMinH + Theme2.pad
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

                    Rectangle { Layout.preferredWidth: Theme2.compact ? 40 : 52; Layout.preferredHeight: width
                        radius: Theme2.radiusS; color: Theme2.surfaceAlt
                        // Бейдж модуля ХАБ из графического пака (D-99): проекты — раздел ядра платформы.
                        Image { anchors.centerIn: parent; width: parent.width - (Theme2.compact ? 10 : 14); height: width
                                source: "../assets/vektor/app_icons/png_64/vektor_hub.png"
                                sourceSize: Qt.size(64, 64); fillMode: Image.PreserveAspectFit; smooth: true; mipmap: true } }

                    ColumnLayout { spacing: Theme2.compact ? 2 : 4; Layout.fillWidth: true
                        RowLayout { spacing: 10
                            Text { text: modelData.code; color: Theme2.primary; font.family: Theme2.family
                                   font.pixelSize: Theme2.fsM; font.weight: Font.Bold }
                            Text { text: modelData.title; color: Theme2.text; font.family: Theme2.family
                                   font.pixelSize: Theme2.fsM; font.weight: Font.DemiBold
                                   Layout.fillWidth: true; elide: Text.ElideRight }
                        }
                        RowLayout { spacing: 14
                            Text { visible: (modelData.tender_title || "") !== ""
                                   text: "Тендер: " + (modelData.tender_title || "")
                                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                                   Layout.maximumWidth: card.width * 0.45; elide: Text.ElideRight }
                            Text { text: modelData.kit_count + " компл."
                                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS }
                            Text { text: "карточка v" + modelData.version
                                   color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsS }
                        }
                    }
                    VChip { text: modelData.status_label; tone: "info" }
                    VChip { visible: modelData.pending === true; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                    Icon { name: "chevron-right"; size: 16; color: Theme2.textFaint }
                }
            }

            // Пустое состояние — дружелюбное, с пояснением (вместо мёртвой колонки классики).
            ColumnLayout {
                visible: page.items.length === 0
                anchors.centerIn: parent; spacing: Theme2.gap
                Icon { name: "folder-kanban"; size: 44; color: Theme2.textFaint; Layout.alignment: Qt.AlignHCenter }
                Text { text: page.search !== "" ? "По запросу «" + page.search + "» ничего не найдено" : "Проектов пока нет"
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
