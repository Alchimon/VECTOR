import QtQuick
import QtQuick.Layouts

// История объекта из журнала аудита сервера (только онлайн).
ColumnLayout {
    id: hl
    property string objectType: ""
    property string objectId: ""
    property var items: []
    property string state: "idle"   // idle | loading | ready | offline | denied
    property string tag: "history-" + objectType
    spacing: 10
    Layout.fillWidth: true
    function load() {
        if (!objectId) return
        state = "loading"
        backend.history(tag, objectType, objectId)
    }
    onObjectIdChanged: load()
    Connections {
        target: backend
        function onQueryDone(t, r) {
            if (t !== hl.tag) return
            if (r.status === "accepted") { hl.items = r.result.slice().reverse(); hl.state = "ready" }
            else if (r.status === "denied") hl.state = "denied"
            else hl.state = "offline"
        }
    }
    SectionTitle { text: "История изменений"; iconName: "history" }
    Text { visible: hl.state === "loading"; text: "Загрузка…"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
    Text { visible: hl.state === "offline"; text: "История доступна при связи с сервером."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
    Text { visible: hl.state === "denied"; text: "Нет права на просмотр истории."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
    Repeater {
        model: hl.state === "ready" ? hl.items : []
        delegate: RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Rectangle { width: 8; height: 8; radius: 4; color: modelData.result === "accepted" ? Theme.primary : Theme.danger; Layout.alignment: Qt.AlignTop; Layout.topMargin: 5 }
            ColumnLayout {
                spacing: 1; Layout.fillWidth: true
                Text { text: backend.commandLabel(modelData.command) + (modelData.result !== "accepted" ? " — отказ" : "") + (modelData.version_after ? "  ·  v" + modelData.version_after : ""); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.DemiBold }
                Text { text: (modelData.actor || "система") + "  ·  " + modelData.at.replace("T", " ").substring(0, 16) + " UTC"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs }
                Text { text: "Основание доступа: " + backend.authorityLabel(modelData.authority || ""); color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; elide: Text.ElideRight; Layout.fillWidth: true }
            }
        }
    }
}
