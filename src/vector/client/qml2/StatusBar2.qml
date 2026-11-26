import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../qml"

// Статус-бар интерфейса 2.0 (D-98) — то, что в классике жило внизу левой колонки, теперь всегда на виду:
// связь и очередь, рабочий день (живой таймер), переключатель плотности и вида интерфейса.
Rectangle {
    id: bar
    objectName: "statusBar2"
    color: Theme2.sidebar
    implicitHeight: Theme2.statusBarH
    Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme2.border }

    // Живой таймер рабочего дня (та же логика, что в WorkdayCard: worked_seconds + часы QML).
    readonly property var w: backend.workdayOverview
    property double receivedAt: Date.now()
    property int tick: 0
    onWChanged: receivedAt = Date.now()
    Timer { interval: 1000; repeat: true; running: bar.w.running === true; onTriggered: bar.tick++ }
    readonly property int wdSeconds: { tick; return (w.worked_seconds || 0) + (w.running === true ? Math.max(0, Math.floor((Date.now() - receivedAt) / 1000)) : 0) }
    function pad2(n) { return n < 10 ? "0" + n : "" + n }
    readonly property string wdClock: pad2(Math.floor(wdSeconds / 3600)) + ":" + pad2(Math.floor(wdSeconds % 3600 / 60))

    RowLayout {
        anchors { fill: parent; leftMargin: Theme2.gap; rightMargin: Theme2.gap }
        spacing: Theme2.pad
        RowLayout { spacing: 6
            Rectangle { width: 8; height: 8; radius: 4
                color: backend.online ? Theme2.success : backend.sessionMode === "offline" ? Theme2.warning : Theme2.danger }
            Text { text: backend.online ? "Онлайн" : backend.sessionMode === "offline" ? "Офлайн (локальный сеанс)" : "Нет связи"
                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                   VTipArea { text: backend.online ? "Связь с сервером есть: изменения уходят сразу."
                              : "Работа идёт офлайн: изменения копятся в очереди и уйдут при подключении." } }
        }
        RowLayout { spacing: 5; visible: backend.pendingCount > 0 || backend.problemCount > 0
            Icon { name: backend.problemCount > 0 ? "triangle-alert" : "clock"; size: 13
                   color: backend.problemCount > 0 ? Theme2.danger : Theme2.warning }
            Text { text: backend.problemCount > 0 ? "Проблемы: " + backend.problemCount : "В очереди: " + backend.pendingCount
                   color: backend.problemCount > 0 ? Theme2.danger : Theme2.warning
                   font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                   VTipArea { text: backend.problemCount > 0
                              ? "Изменения, которые сервер отклонил, — разберите в разделе «Синхронизация»."
                              : "Изменения ждут отправки — уйдут при синхронизации (F5)." } }
        }
        Text { visible: backend.lastPull !== ""; text: "Обновлено " + backend.lastPull
               color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
        Item { Layout.fillWidth: true }
        RowLayout { spacing: 5; visible: bar.w.has_day === true
            Icon { name: "timer"; size: 13; color: bar.w.running === true ? Theme2.success : Theme2.textFaint }
            Text { objectName: "statusBarWorkday"; text: "Рабочий день " + bar.wdClock
                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                   font.features: { "tnum": 1 }
                   VTipArea { text: "Таймер рабочего дня: " + (bar.w.status_label || "") + ". Подробности — в разделе «Рабочее время» (Ctrl+0)." } }
        }
        // Плотность: «Уютно / Компактно» (D-98) — мгновенно, без перезапуска.
        RowLayout { spacing: 4
            Icon { name: "sliders-horizontal"; size: 13; color: Theme2.textFaint }
            Text { objectName: "densityLabel"; text: backend.uiDensity === "compact" ? "Компактно" : "Уютно"
                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                   MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                       onClicked: backend.uiDensity = backend.uiDensity === "compact" ? "cozy" : "compact" }
                   VTipArea { text: "Плотность интерфейса: «Уютно» — просторно, крупный шрифт; «Компактно» — плотнее, для небольших мониторов. Щёлкните, чтобы переключить." } }
        }
        Text { text: "ВЕКТОР " + backend.version; color: Theme2.textFaint
               font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
    }
}
