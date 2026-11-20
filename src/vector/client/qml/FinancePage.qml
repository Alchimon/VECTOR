import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// «Итоги проектов»: официальные итоги (последние ревизии) всех доступных проектов.
// Для бухгалтерии — единственный вид проектных денег: без черновиков и позиций (A-18), только ревизии.
MasterDetail {
    id: page
    property var shell
    property string selectedId: ""
    readonly property var all: backend.financeOverview
    readonly property var items: all.filter(p => search === "" || (p.project_code + " " + p.project_title).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: all.find(p => p.project_id === selectedId) || null
    title: "Итоги проектов"
    subtitle: all.length + " с зафиксированным итогом"
    hasSelection: sel !== null
    emptyIcon: "wallet"; emptyTitle: "Итогов пока нет"
    emptyText: "Итог проекта появляется после первой фиксации расчёта комплекта ГИП проекта. Черновики расчётов сюда не попадают."
    noSelectionText: "Выберите проект"
    headerActions: [ VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() } ]
    list.model: items
    list.delegate: ListRow {
        title: modelData.project_code + "  ·  " + modelData.project_title
        subtitle: modelData.latest.totals.map(t => t.label).join("  |  ")
        iconName: "wallet"
        chipText: "r" + modelData.latest.revision_no; chipTone: modelData.latest.totals.every(t => t.complete) ? "success" : "warning"
        selected: modelData.project_id === page.selectedId
        onClicked: page.selectedId = modelData.project_id
    }

    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Rectangle { width: 52; height: 52; radius: 14; color: Theme.successSoft
            Icon { anchors.centerIn: parent; name: "wallet"; size: 26; color: Theme.success } }
        ColumnLayout { spacing: 4; Layout.fillWidth: true
            Text { text: sel ? sel.project_code : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
            Text { text: sel ? sel.project_title : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true } }
        VButton { text: "Проект"; iconName: "folder-kanban"; visible: sel !== null && backend.projects.some(p => p.id === sel.project_id)
                  onClicked: page.shell.go("projects", sel.project_id) }
    }
    ProjectTotal { Layout.fillWidth: true; projectId: page.selectedId }
}
