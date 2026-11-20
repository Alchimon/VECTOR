import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Вкладка «Мои операции» раздела «Производство» (С5 шаг 5; D-36) — рабочее место исполнителя.
//
//  * Операции, назначенные мне в открытых нарядах: сначала «в работе», затем «пауза», затем «ожидает».
//  * Кнопки по состоянию: Начать → (Пауза ⇄ Продолжить) → Завершить; «Проблема» — в любой момент до завершения
//    (описание обязательно; РП проекта получит уведомление).
//  * Всё работает БЕЗ СВЯЗИ: событие ставится в очередь со временем этого компьютера (A-44), операция сразу
//    показывает новое состояние и таймер — по тем же правилам, что применит сервер. При синхронизации сервер
//    проверяет ещё раз; если наряд уже закрыт, операцию переназначили или события перемешались — это будет
//    ВИДИМЫЙ конфликт в разделе «Синхронизация» (сервер ничего не «додумывает»).
//  * Таймер — сумма интервалов «в работе»; табло обновляется раз в секунду, пока операция в работе.
Item {
    id: pane
    objectName: "myOpsPane"
    property var shell
    property var tabs: []
    property string tab: ""
    signal tabPicked(string id)
    readonly property var pv: backend.productionOverview
    readonly property var items: pv.my_ops || []
    property int tick: 0
    Timer { interval: 1000; repeat: true; running: pane.visible && pane.items.some(o => o.running); onTriggered: pane.tick++ }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 28; anchors.bottomMargin: 20
        spacing: 16
        PageHeader {
            title: "Производство"
            subtitle: "Мои операции · в работе " + ((pane.pv.counts || {}).my_active || 0) + "  ·  " + (pane.pv.as_of || "")
            VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() }
        }
        VSegmented { visible: pane.tabs.length > 1; objectName: "prodTabs"; model: pane.tabs; current: pane.tab; onPicked: id => pane.tabPicked(id) }
        Banner { visible: pane.pv.online !== true; tone: "info"
                 text: "Нет связи с сервером — работайте как обычно: события сохраняются на этом компьютере (время — по его часам) и уйдут на сервер при подключении." }
        EmptyState { visible: pane.items.length === 0; Layout.fillWidth: true; Layout.fillHeight: true
                     iconName: "factory"; title: "Операций нет"; text: pane.pv.empty_my || "" }
        Flickable {
            visible: pane.items.length > 0
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            contentHeight: col.implicitHeight; boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            ColumnLayout {
                id: col
                width: parent.width; spacing: 12
                Repeater {
                    model: pane.items
                    delegate: VCard {
                        id: card
                        objectName: "myOpCard"
                        required property var modelData
                        readonly property var o: modelData
                        Layout.fillWidth: true
                        implicitHeight: ocol.implicitHeight + 32
                        // Полоса слева — цвет состояния (в работе — основной цвет).
                        Rectangle { width: 4; radius: 2; color: Theme.tone(card.o.state_tone).fg
                                    anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.margins: 8 }
                        ColumnLayout {
                            id: ocol
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.margins: 16; anchors.leftMargin: 22; spacing: 8
                            RowLayout { Layout.fillWidth: true; spacing: 10
                                ColumnLayout { spacing: 2; Layout.fillWidth: true
                                    Text { text: card.o.wo_label + "  ·  " + (card.o.wo_qty || 0) + " шт."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                                    Text { text: card.o.title; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM; font.weight: Font.Bold
                                           wrapMode: Text.Wrap; Layout.fillWidth: true }
                                    Text { visible: card.o.work_center !== ""; text: "Участок: " + card.o.work_center; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS } }
                                VChip { visible: card.o.pending; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                                VChip { text: card.o.state_label; tone: card.o.state_tone }
                            }
                            RowLayout { Layout.fillWidth: true; spacing: 18
                                // Табло таймера: в работе — тикает раз в секунду.
                                RowLayout { spacing: 8
                                    Icon { name: "timer"; size: 20; color: card.o.running ? Theme.primary : Theme.textMuted }
                                    Text { objectName: "opTimer"
                                           text: { pane.tick; return card.o.running ? backend.opTimer(card.o.fact_seconds, card.o.running_since, card.o.state) : card.o.clock_label }
                                           color: card.o.running ? Theme.primary : Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl
                                           font.weight: Font.Bold; font.features: { "tnum": 1 } } }
                                ColumnLayout { spacing: 0
                                    Text { text: "Норма на наряд"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs }
                                    Text { text: card.o.norm_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs } }
                                ColumnLayout { visible: card.o.problem_count > 0; spacing: 0
                                    Text { text: "Проблем"; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs }
                                    Text { text: card.o.problem_count; color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold } }
                                Item { Layout.fillWidth: true }
                            }
                            Flow { Layout.fillWidth: true; spacing: 8
                                Repeater {
                                    model: card.o.actions
                                    delegate: VButton { required property var modelData
                                        objectName: "opAction-" + modelData.kind
                                        text: modelData.label; iconName: modelData.icon; kind: modelData.btn
                                        onClicked: modelData.kind === "finish" ? finishForm.openFor(card.o) : backend.opEvent("op-ev", card.o.id, modelData.kind, "", "") }
                                }
                                VButton { objectName: "opAction-problem"; visible: card.o.can_problem; text: "Проблема"; iconName: "triangle-alert"; kind: "danger"
                                          onClicked: problemForm.openFor(card.o) }
                            }
                            // Последние события (свои неотправленные — с пометкой).
                            Repeater {
                                model: card.o.events.slice(-3)
                                delegate: Text { required property var modelData
                                    text: modelData.when + " — " + modelData.kind_label + (modelData.note ? ": " + modelData.note : "") + (modelData.local ? "  (не отправлено)" : "")
                                    color: modelData.local ? Theme.warning : Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs
                                    wrapMode: Text.Wrap; Layout.fillWidth: true }
                            }
                        }
                    }
                }
                Item { implicitHeight: 8 }
            }
        }
    }

    // Итог события (принято / в очереди / отказ с причиной) показывает общее уведомление backend._finish_command.
    FormDialog {
        id: finishForm
        objectName: "opFinishForm"
        property var o: null
        title: "Завершить операцию"
        subtitle: o ? o.wo_label + " · " + o.title : ""
        confirmText: "Завершить"
        note: "Завершённая операция больше не принимает событий. Время — по часам этого компьютера."
        canConfirm: fQty.error === ""
        function openFor(x) { o = x; fQty.text = ""; open() }
        onConfirmed: { backend.opEvent("op-finish", o.id, "finish", fQty.text.trim(), ""); close() }
        VField { id: fQty; objectName: "opQtyDone"; label: "Выполнено изделий"; placeholder: finishForm.o ? "весь наряд — " + finishForm.o.wo_qty : ""
                 error: text.trim() === "" || (/^\d+$/.test(text.trim()) && finishForm.o && parseInt(text.trim()) <= finishForm.o.wo_qty) ? "" : "Целое от 0 до количества в наряде." }
    }
    FormDialog {
        id: problemForm
        objectName: "opProblemForm"
        property var o: null
        title: "Сообщить о проблеме"
        subtitle: o ? o.wo_label + " · " + o.title : ""
        confirmText: "Сообщить"; confirmKind: "danger"
        note: "РП проекта получит уведомление. Состояние операции не меняется; без связи сообщение уйдёт при подключении."
        canConfirm: pNote.text.trim() !== ""
        function openFor(x) { o = x; pNote.text = ""; open() }
        onConfirmed: { backend.opEvent("op-problem", o.id, "problem", "", pNote.text.trim()); close() }
        VField { id: pNote; objectName: "opProblemNote"; label: "Что случилось"; required: true; placeholder: "Нет кабеля нужного сечения" }
    }
}
