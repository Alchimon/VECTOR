import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../qml"

// Палитра команд (Ctrl+K, интерфейс 2.0, D-98): поиск по разделам и действиям — как в Linear/VS Code.
// Команды отдаёт оболочка (Shell2.commands): {label, hint, icon, run}. Стрелки ↑/↓ — выбор, Enter — выполнить,
// Esc — закрыть. Работает и без мыши — клавиатура «первого класса» (принцип 2.0).
Popup {
    id: pal
    objectName: "commandPalette"
    property var commands: []            // [{label, hint, icon, run}]
    property string query: ""
    // Обработчик внутренней ссылки (P3-1): оболочка передаёт Shell2.openLink; вставленная vector://… —
    // первый результат «Перейти по ссылке».
    property var linkHandler: null
    readonly property bool isLink: /^vector:\/\/\S+$/.test(query.trim())
    readonly property var filtered: {
        var q = query.trim().toLowerCase()
        var base = (q === "") ? commands
            : commands.filter(c => (c.label + " " + (c.hint || "")).toLowerCase().indexOf(q) >= 0)
        if (isLink && linkHandler !== null)
            return [{ label: "Перейти по ссылке: " + query.trim(), hint: "Внутренняя ссылка ВЕКТОР на объект",
                      icon: "link", run: function() { pal.linkHandler(query.trim()) } }].concat(base)
        return base
    }
    property int current: 0
    signal picked(var cmd)

    modal: true; focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(560, (Overlay.overlay ? Overlay.overlay.width : 800) - 80)
    implicitHeight: Math.min(col.implicitHeight + 2, (Overlay.overlay ? Overlay.overlay.height : 700) - 120)
    padding: 1
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    Overlay.modal: Rectangle { color: Theme2.scrim }
    background: Rectangle { color: Theme2.surface; radius: Theme2.radiusL; border.color: Theme2.borderStrong }

    onOpened: { query = ""; current = 0; field.forceActiveFocus() }
    onFilteredChanged: current = 0

    function run(cmd) { pal.close(); pal.runCmd(cmd) }
    function runCmd(cmd) { if (cmd && cmd.run) cmd.run() }

    ColumnLayout {
        id: col
        anchors.left: parent.left; anchors.right: parent.right
        spacing: 0
        RowLayout {
            Layout.fillWidth: true; Layout.margins: 6; spacing: 8
            Icon { name: "search"; size: 18; color: Theme2.textFaint; Layout.leftMargin: 8 }
            TextField {
                id: field
                objectName: "paletteField"
                Layout.fillWidth: true; Layout.rightMargin: 8
                implicitHeight: Theme2.compact ? 38 : 46
                placeholderText: "Поиск и команды…  (↑↓ — выбор, Enter — выполнить)"
                placeholderTextColor: Theme2.textFaint
                color: Theme2.text
                font.family: Theme2.family; font.pixelSize: Theme2.fsM
                background: Item {}
                onTextChanged: pal.query = text
                Keys.onDownPressed: pal.current = Math.min(pal.current + 1, pal.filtered.length - 1)
                Keys.onUpPressed: pal.current = Math.max(pal.current - 1, 0)
                Keys.onReturnPressed: if (pal.filtered.length > 0) pal.run(pal.filtered[pal.current])
                Keys.onEnterPressed: if (pal.filtered.length > 0) pal.run(pal.filtered[pal.current])
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme2.border }
        ListView {
            id: list
            objectName: "paletteList"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(contentHeight, Theme2.compact ? 320 : 420)
            clip: true
            model: pal.filtered
            currentIndex: pal.current
            delegate: AbstractButton {
                id: row
                width: list.width
                implicitHeight: Theme2.compact ? 40 : 48
                onClicked: pal.run(modelData)
                background: Rectangle {
                    color: index === pal.current ? Theme2.primarySoft : row.hovered ? Theme2.hover : "transparent"
                }
                contentItem: RowLayout {
                    spacing: 12
                    Rectangle { Layout.leftMargin: 12; width: 30; height: 30; radius: 8
                        color: index === pal.current ? Theme2.surface : Theme2.surfaceAlt
                        Icon { anchors.centerIn: parent; name: modelData.icon || "search"; size: 15
                               color: index === pal.current ? Theme2.primary : Theme2.textMuted } }
                    ColumnLayout { spacing: 1; Layout.fillWidth: true
                        Text { text: modelData.label; color: Theme2.text; font.family: Theme2.family
                               font.pixelSize: Theme2.fs; Layout.fillWidth: true; elide: Text.ElideRight }
                        Text { visible: (modelData.hint || "") !== ""; text: modelData.hint || ""
                               color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                               Layout.fillWidth: true; elide: Text.ElideRight } }
                    Text { visible: (modelData.keys || "") !== ""; text: modelData.keys || ""; Layout.rightMargin: 12
                           color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                }
            }
            Text { visible: pal.filtered.length === 0; anchors.centerIn: parent
                   text: "Ничего не найдено — попробуйте другое слово"
                   color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fs }
        }
    }
}
