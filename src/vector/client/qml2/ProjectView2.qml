import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../qml"

// Вкладка проекта (интерфейс 2.0, D-98): шапка с действиями, команда, комплекты крупными карточками.
// Клик по комплекту открывает его вкладку (KitView2). Действия создания/назначения — через ленту
// (они контекстны активной вкладке) или кнопки здесь; команды уходят через те же backend-вызовы,
// что и в классике, — права и аудит не меняются.
Item {
    id: view
    objectName: "projectView2"
    property var shell
    property string projectId: ""
    readonly property var p: backend.projects.find(x => x.id === projectId) || null
    readonly property var team: p ? backend.assignments.filter(a => a.project_id === p.id) : []
    readonly property var kits: p ? backend.kits.filter(k => k.project_id === p.id) : []

    Flickable {
        anchors.fill: parent
        contentWidth: width; contentHeight: col.implicitHeight + Theme2.padL
        clip: true; boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        ColumnLayout {
            id: col
            width: parent.width - Theme2.padL * 2
            x: Theme2.padL; y: Theme2.padL
            spacing: Theme2.pad

            // ---------- шапка
            RowLayout { Layout.fillWidth: true; spacing: Theme2.pad
                Rectangle { Layout.preferredWidth: Theme2.compact ? 48 : 64; Layout.preferredHeight: width
                    radius: Theme2.radius; color: Theme2.infoSoft
                    Icon { anchors.centerIn: parent; name: "folder-kanban"; size: Theme2.compact ? 24 : 32; color: Theme2.info } }
                ColumnLayout { spacing: 4; Layout.fillWidth: true
                    Text { text: view.p ? view.p.code : ""; color: Theme2.textMuted; font.family: Theme2.family
                           font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold; font.letterSpacing: 0.5 }
                    Text { objectName: "project2Title"; text: view.p ? view.p.title : ""; color: Theme2.text; font.family: Theme2.family
                           font.pixelSize: Theme2.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    RowLayout { spacing: 6
                        VChip { text: view.p ? view.p.status_label : ""; tone: "info" }
                        VChip { text: view.p ? "карточка v" + view.p.version : ""; tone: "neutral" }
                        VChip { visible: view.p && view.p.pending === true; text: "Не отправлено"; tone: "warning"; iconName: "clock" } } }
                VButton { text: "Тендер-основание"; iconName: "gavel"; kind: "soft"
                          visible: view.p && view.p.tender_id && backend.hint("tender.read", backend.permRevision)
                          implicitHeight: Theme2.compact ? 36 : 44
                          onClicked: view.shell.goPage("tenders", view.p.tender_id) }
            }

            // ---------- команда
            ColumnLayout { Layout.fillWidth: true; spacing: Theme2.gap
                Text { text: "КОМАНДА ПРОЕКТА"; color: Theme2.textFaint; font.family: Theme2.family
                       font.pixelSize: Theme2.fsXs; font.weight: Font.Bold; font.letterSpacing: 1.2 }
                Flow { Layout.fillWidth: true; spacing: 8
                    Repeater {
                        model: view.team
                        delegate: Rectangle {
                            height: Theme2.compact ? 34 : 40; width: trow2.implicitWidth + 20; radius: height / 2
                            color: Theme2.surfaceAlt; border.color: Theme2.border
                            RowLayout { id: trow2; anchors.centerIn: parent; spacing: 8
                                Rectangle { width: Theme2.compact ? 22 : 26; height: width; radius: width / 2
                                    color: modelData.project_role === "gip" ? Theme2.primarySoft : Theme2.surface
                                    Text { anchors.centerIn: parent; text: (modelData.user_name || "?").substring(0, 1)
                                           color: Theme2.primary; font.family: Theme2.family; font.pixelSize: Theme2.fsS; font.weight: Font.Bold } }
                                Text { text: modelData.user_name; color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsS }
                                VChip { text: modelData.role_label
                                        tone: modelData.project_role === "gip" ? "primary" : modelData.project_role === "rp" ? "info" : "neutral" } }
                        }
                    }
                }
                Text { visible: view.team.length === 0
                       text: "Назначений нет (или список недоступен офлайн). Назначает ГИПа администратор, команду — ГИП/РП."
                       color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS }
            }

            // ---------- комплекты
            ColumnLayout { Layout.fillWidth: true; spacing: Theme2.gap
                Text { text: "КОМПЛЕКТЫ (" + view.kits.length + ")"; color: Theme2.textFaint
                       font.family: Theme2.family; font.pixelSize: Theme2.fsXs; font.weight: Font.Bold; font.letterSpacing: 1.2 }
                GridLayout {
                    Layout.fillWidth: true
                    columns: Math.max(1, Math.floor(width / (Theme2.compact ? 280 : 340)))
                    columnSpacing: Theme2.gap; rowSpacing: Theme2.gap
                    Repeater {
                        model: view.kits
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: Theme2.compact ? 84 : 108
                            radius: Theme2.radius
                            color: kitArea.containsMouse ? Theme2.hover : Theme2.surface
                            border.color: Theme2.cardBorder
                            MouseArea { id: kitArea; anchors.fill: parent; hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: view.shell.openObject("kit", modelData.id) }
                            ColumnLayout { anchors { fill: parent; margins: Theme2.pad - 4 } spacing: 4
                                RowLayout { Layout.fillWidth: true; spacing: 8
                                    Icon { name: "layers"; size: 16; color: Theme2.primary }
                                    Text { text: modelData.code; color: Theme2.primary; font.family: Theme2.family
                                           font.pixelSize: Theme2.fsM; font.weight: Font.Bold }
                                    VChip { visible: modelData.pending === true; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                                    Item { Layout.fillWidth: true }
                                    Icon { name: "chevron-right"; size: 14; color: Theme2.textFaint } }
                                Text { text: modelData.title; color: Theme2.text; font.family: Theme2.family
                                       font.pixelSize: Theme2.fs; elide: Text.ElideRight; Layout.fillWidth: true }
                                Text { text: "Задачи: " + modelData.open_task_count + " откр. из " + modelData.task_count
                                       + "  ·  Документы: " + modelData.doc_count
                                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                                Text { visible: !Theme2.compact; text: modelData.calc_label || ""
                                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs } }
                        }
                    }
                }
                Text { visible: view.kits.length === 0
                       text: "Комплектов пока нет. Создать комплект может РП проекта — действие «Новый комплект» на ленте (вкладка «Проект»)."
                       color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                       wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }
        }
    }
}
