import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    Component.onCompleted: backend.loadQueue()
    readonly property string queueSignature: backend.pendingCount + "/" + backend.problemCount + "/" + backend.lastPull
    onQueueSignatureChanged: Qt.callLater(backend.loadQueue)

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 28
        spacing: 20
        PageHeader {
            title: "Синхронизация"
            subtitle: backend.lastPull ? "Последнее обновление с сервера: " + backend.lastPull.replace("T", " ").substring(0, 19) + " UTC" : "С сервера ещё не загружалось"
            VButton { text: "Синхронизировать"; kind: "primary"; iconName: "refresh-cw"; enabled: backend.sessionMode === "online"; onClicked: backend.syncNow() }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 14
            Repeater {
                model: [
                    { t: "В очереди", v: backend.pendingCount, tone: "warning", icon: "clock", d: "Сохранено локально, ждёт отправки" },
                    { t: "Требуют решения", v: backend.problemCount, tone: "danger", icon: "triangle-alert", d: "Конфликт версий или отказ сервера" },
                    { t: "Связь", v: backend.sessionMode === "online" ? "Онлайн" : "Офлайн", tone: backend.sessionMode === "online" ? "success" : "neutral", icon: backend.sessionMode === "online" ? "cloud" : "cloud-off", d: backend.sessionMode === "online" ? "Сеанс с сервером активен" : "Работа с локальным кэшем" }
                ]
                delegate: VCard {
                    Layout.fillWidth: true; implicitHeight: 92
                    RowLayout { anchors.fill: parent; anchors.margins: 18; spacing: 14
                        Rectangle { width: 44; height: 44; radius: 12; color: Theme.tone(modelData.tone).bg
                            Icon { anchors.centerIn: parent; name: modelData.icon; size: 20; color: Theme.tone(modelData.tone).fg } }
                        ColumnLayout { spacing: 2; Layout.fillWidth: true
                            Text { text: modelData.t; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                            Text { text: modelData.v; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold }
                            Text { text: modelData.d; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; elide: Text.ElideRight; Layout.fillWidth: true } } }
                }
            }
        }
        Banner { tone: "info"; text: "Изменения, сделанные без связи, отправляются строго по порядку. Если сервер обнаружил конфликт или отказал, отправка останавливается: решите, что делать с изменением. Автоматического слияния нет — деньги, согласования и права никогда не объединяются молча." }

        ListView {
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; spacing: 12
            model: backend.queueItems
            ScrollBar.vertical: ScrollBar {}
            delegate: VCard {
                width: ListView.view.width
                readonly property bool problem: modelData.state === "conflict" || modelData.state === "rejected"
                implicitHeight: qcol.implicitHeight + 32
                border.color: problem ? Qt.alpha(Theme.danger, 0.45) : Theme.border
                id: qcard
                ColumnLayout { id: qcol; anchors.fill: parent; anchors.margins: 16; spacing: 10
                    RowLayout { Layout.fillWidth: true; spacing: 10
                        Icon { name: modelData.state === "conflict" ? "triangle-alert" : modelData.state === "rejected" ? "circle-x" : "clock"; size: 18
                               color: modelData.state === "pending" ? Theme.warning : Theme.danger }
                        Text { text: modelData.label_ru; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM; font.weight: Font.DemiBold; Layout.fillWidth: true }
                        VChip { text: modelData.state === "conflict" ? "Конфликт версий" : modelData.state === "rejected" ? "Отклонено сервером" : "Ожидает отправки"
                                tone: modelData.state === "pending" ? "warning" : "danger" }
                    }
                    Text { text: "Создано: " + modelData.created_at.replace("T", " ").substring(0, 19) + "  ·  попыток: " + modelData.attempts + "  ·  операция " + modelData.operation_id.substring(0, 8)
                           color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs }
                    Text { visible: modelData.message_ru !== ""; text: modelData.message_ru; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    // Сравнение: моё изменение ↔ текущая версия на сервере
                    ColumnLayout {
                        id: diffBox
                        readonly property var rows: modelData.diff
                        readonly property int sv: modelData.server_version
                        visible: modelData.state === "conflict" && rows.length > 0
                        Layout.fillWidth: true; spacing: 0
                        Rectangle { Layout.fillWidth: true; implicitHeight: 30; color: Theme.surfaceAlt; radius: Theme.radiusS
                            RowLayout { anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; spacing: 12
                                Text { text: "Поле"; Layout.preferredWidth: 140; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.Bold }
                                Text { text: "Моё изменение"; Layout.fillWidth: true; Layout.preferredWidth: 1; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.Bold }
                                Text { text: "Сейчас на сервере (v" + diffBox.sv + ")"; Layout.fillWidth: true; Layout.preferredWidth: 1; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.Bold } } }
                        Repeater {
                            model: diffBox.rows
                            delegate: RowLayout {
                                Layout.fillWidth: true; Layout.leftMargin: 10; Layout.rightMargin: 10; Layout.topMargin: 6; spacing: 12
                                Text { text: (modelData.field_label || modelData.field); Layout.preferredWidth: 140; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
                                Text { text: modelData.mine || "—"; Layout.fillWidth: true; Layout.preferredWidth: 1; wrapMode: Text.Wrap
                                       color: modelData.differs ? Theme.warning : Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: modelData.differs ? Font.DemiBold : Font.Normal }
                                Text { text: modelData.server || "—"; Layout.fillWidth: true; Layout.preferredWidth: 1; wrapMode: Text.Wrap
                                       color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs }
                            }
                        }
                    }
                    RowLayout { visible: qcard.problem; spacing: 8
                        VButton { text: "Отменить моё изменение"; iconName: "rotate-ccw"; onClicked: backend.discard(modelData.operation_id) }
                        VButton { visible: modelData.state === "conflict"; text: "Применить поверх текущей версии"; kind: "soft"; iconName: "check"; enabled: backend.sessionMode === "online"
                                  onClicked: backend.reapply(modelData.operation_id) } }
                }
            }
            EmptyState {
                anchors.centerIn: parent; visible: parent.count === 0
                iconName: "circle-check"; tone: "success"; title: "Очередь пуста"; text: "Все изменения этого компьютера подтверждены сервером."
            }
        }
    }
}
