import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../qml"

// Вкладка комплекта (интерфейс 2.0, D-98): шапка комплекта + вся работа классического KitWork
// (задачи, документы с версиями, РКД, расчёт) — компонент переиспользован без изменений,
// меняется только «рамка»: воздух вокруг и крупный заголовок. Офисный редактор и просмотр
// версий открываются из KitWork, как раньше.
Item {
    id: view
    objectName: "kitView2"
    property var shell
    property string kitId: ""
    readonly property var kit: backend.kits.find(k => k.id === kitId) || null
    readonly property var project: kit ? backend.projects.find(p => p.id === kit.project_id) || null : null
    readonly property var team: project ? backend.assignments.filter(a => a.project_id === project.id) : []
    readonly property var deputies: project ? backend.projectDeputies.filter(d => d.project_id === project.id) : []

    ColumnLayout {
        anchors { fill: parent; margins: Theme2.pageMargin }
        spacing: Theme2.gap

        // ---------- шапка комплекта
        RowLayout { Layout.fillWidth: true; spacing: Theme2.pad
            Rectangle { Layout.preferredWidth: Theme2.compact ? 40 : 52; Layout.preferredHeight: width
                radius: Theme2.radius; color: Theme2.primarySoft
                Icon { anchors.centerIn: parent; name: "layers"; size: Theme2.compact ? 20 : 26; color: Theme2.primary } }
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { visible: view.project !== null; text: view.project ? view.project.code + " · " + view.project.title : ""
                       color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                       elide: Text.ElideRight; Layout.fillWidth: true
                       MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                           onClicked: view.shell.openObject("project", view.project ? view.project.id : "") }
                       VTipArea { text: "Открыть проект" } }
                RowLayout { spacing: 10
                    Text { text: view.kit ? view.kit.code : ""; color: Theme2.primary; font.family: Theme2.family
                           font.pixelSize: Theme2.fsL; font.weight: Font.Bold }
                    Text { text: view.kit ? view.kit.title : ""; color: Theme2.text; font.family: Theme2.family
                           font.pixelSize: Theme2.fsL; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true } }
            }
            VChip { visible: view.kit && view.kit.pending === true; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
        }

        // ---------- работа комплекта (классический компонент: задачи / документы / РКД / расчёт)
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true
            radius: Theme2.radius; color: Theme2.surface; border.color: Theme2.cardBorder
            Flickable {
                anchors { fill: parent; margins: Theme2.pad }
                contentWidth: width; contentHeight: kw.implicitHeight
                clip: true; boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                KitWork { id: kw; width: parent.width; kit: view.kit; team: view.team; deputies: view.deputies }
            }
        }
    }
}
