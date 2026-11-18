import QtQuick
import QtQuick.Layouts

// Состояние связи и очереди — всегда на виду (офлайн ≠ ошибка ≠ конфликт). Подсказка при наведении (D-37) объясняет
// состояние и что делать.
Rectangle {
    id: p
    readonly property string mode: backend.sessionMode === "online" && backend.online ? "online"
                                   : backend.sessionMode === "offline" && backend.online ? "reconnect" : "offline"
    readonly property var t: Theme.tone(mode === "online" ? "success" : mode === "reconnect" ? "info" : "warning")
    Layout.fillWidth: true
    implicitHeight: col.implicitHeight + 20
    radius: Theme.radius
    color: t.bg
    VTipArea {
        text: (p.mode === "online" ? "Связь с сервером есть: изменения отправляются сразу."
               : p.mode === "reconnect" ? "Сервер снова доступен, но вы вошли без связи. Нажмите «Подключиться» и введите пароль — очередь уйдёт на сервер."
               : "Нет связи с сервером. Можно смотреть данные и делать изменения, помеченные «можно без связи» — они ждут в очереди.")
              + (backend.problemCount > 0 ? "\nЕсть изменения, которые требуют вашего решения, — раздел «Синхронизация»."
                 : backend.pendingCount > 0 ? "\nВ очереди — изменения, ещё не отправленные на сервер (изм. — изменений)." : "")
    }
    ColumnLayout {
        id: col
        anchors.fill: parent; anchors.margins: 10
        spacing: 4
        RowLayout {
            spacing: 8
            Icon { name: p.mode === "online" ? "cloud" : "cloud-off"; size: 16; color: p.t.fg }
            Text {
                text: p.mode === "online" ? "Онлайн" : p.mode === "reconnect" ? "Сервер доступен" : "Офлайн — локальный режим"
                color: p.t.fg; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.DemiBold
            }
        }
        Text {
            Layout.fillWidth: true
            text: backend.problemCount > 0 ? "Требуют решения: " + backend.problemCount
                  : backend.pendingCount > 0 ? "В очереди: " + backend.pendingCount + " изм."
                  : p.mode === "reconnect" ? "Войдите, чтобы отправить очередь"
                  : "Все изменения отправлены"
            color: backend.problemCount > 0 ? Theme.danger : Theme.textMuted
            font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap
        }
    }
}
