import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Таймер рабочего дня (D-46 — ответы владельца на A-49 пп. 2–5; D-47 — на A-51: график работы, забытое «Завершить»,
// фактическое окончание дня).
//  * compact: true — в боковой панели: состояние, «сколько натикало» (ЧЧ:ММ:СС) и одна-две кнопки;
//  * compact: false — полоса в разделе «Рабочее время»: ещё график работы, начало, перерывы, нажатия, «Я начал
//    раньше», «Я работал дольше» (день завершён автоматически) и, руководителю, «Запросы по рабочему дню».
// Время — по часам компьютера, без округления; перерыв — только по кнопке «Перерыв». Нажатия работают без связи
// (очередь). Живой таймер: к worked_seconds из снимка прибавляются секунды, прошедшие по часам QML, пока день идёт.
VCard {
    id: wc
    objectName: compact ? "workdayWidget" : "workdayCard"
    property bool compact: false
    readonly property var w: backend.workdayOverview
    // ---------- живой таймер
    property double receivedAt: Date.now()
    property int tick: 0
    onWChanged: receivedAt = Date.now()
    Timer { interval: 1000; repeat: true; running: wc.w.running === true; onTriggered: wc.tick++ }
    readonly property int seconds: { tick; return (wc.w.worked_seconds || 0) + (wc.w.running === true ? Math.max(0, Math.floor((Date.now() - receivedAt) / 1000)) : 0) }
    function pad(n) { return n < 10 ? "0" + n : "" + n }
    readonly property string clock: pad(Math.floor(seconds / 3600)) + ":" + pad(Math.floor(seconds % 3600 / 60)) + ":" + pad(seconds % 60)
    function mark(ev) { backend.workdayMark("workday-mark", ev) }

    implicitHeight: body.implicitHeight + (compact ? 20 : 28)
    color: compact ? Theme.surfaceAlt : Theme.surface
    ColumnLayout {
        id: body
        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        anchors.margins: wc.compact ? 10 : 14
        spacing: wc.compact ? 6 : 10
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Rectangle { visible: !wc.compact; width: 44; height: 44; radius: 12; color: Theme.tone(wc.w.status_tone || "neutral").bg
                Icon { anchors.centerIn: parent; name: "timer"; size: 22; color: Theme.tone(wc.w.status_tone || "neutral").fg } }
            ColumnLayout { spacing: 0; Layout.fillWidth: true
                Text { text: wc.compact ? "Рабочий день" : "Рабочий день · " + (wc.w.date_label || "") + (wc.w.night === true ? " (ночная смена)" : "")
                       color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true
                       VTipArea { text: "Таймер рабочего дня: время считается по часам этого компьютера, без округления. Перерыв — только по кнопке «Перерыв». Работает и без связи." } }
                Text { objectName: wc.compact ? "workdayClockMini" : "workdayClock"; text: wc.clock; color: Theme.text; font.family: Theme.family
                       font.pixelSize: wc.compact ? 20 : 28; font.weight: Font.Bold; font.features: { "tnum": 1 }
                       Accessible.name: "Отработано " + wc.clock }
            }
            VChip { objectName: wc.compact ? "workdayStatusMini" : "workdayStatus"; text: wc.w.status_label || ""; tone: wc.w.status_tone || "neutral"; Layout.alignment: Qt.AlignTop }
        }
        // Кнопки таймера: только допустимые в текущем состоянии (невидимые раскладка пропускает). Не Flow: у Flow
        // высота зависит от ширины, а внутри ColumnLayout это зацикливает раскладку (окно «зависает»). В боковой
        // панели — столбец кнопок во всю ширину, на странице — строка.
        GridLayout {
            Layout.fillWidth: true; columns: wc.compact ? 1 : 7; columnSpacing: 6; rowSpacing: 6
            VButton { objectName: wc.compact ? "btnWorkdayStartMini" : "btnWorkdayStart"; visible: wc.w.can_start === true; text: "Начать рабочий день"; kind: "primary"; iconName: "play"; compact: wc.compact; Layout.fillWidth: wc.compact; onClicked: wc.mark("start") }
            VButton { objectName: wc.compact ? "btnWorkdayPauseMini" : "btnWorkdayPause"; visible: wc.w.can_pause === true; text: "Перерыв"; iconName: "coffee"; compact: wc.compact; Layout.fillWidth: wc.compact; onClicked: wc.mark("pause") }
            VButton { objectName: wc.compact ? "btnWorkdayResumeMini" : "btnWorkdayResume"; visible: wc.w.can_resume === true; text: "Продолжить"; kind: wc.w.status === "paused" ? "primary" : "secondary"; iconName: "play"; compact: wc.compact; Layout.fillWidth: wc.compact; onClicked: wc.mark("resume")
                      tip: "Продолжить рабочий день после перерыва (или вернуться к работе после «Завершить день»). Можно без связи: сохранится в очереди и уйдёт на сервер при подключении." }
            VButton { objectName: wc.compact ? "btnWorkdayFinishMini" : "btnWorkdayFinish"; visible: wc.w.can_finish === true; text: "Завершить день"; iconName: "square"; compact: wc.compact; Layout.fillWidth: wc.compact; onClicked: finishConfirm.open() }
            VButton { objectName: wc.compact ? "btnWorkdayEarlierMini" : "btnWorkdayEarlier"; visible: !wc.compact && wc.w.has_day === true; enabled: wc.w.can_request === true; text: "Я начал раньше"; iconName: "sunrise"
                      tip: wc.w.can_request === true ? backend.tip(text) : wc.w.request_block; onClicked: earlierForm.openFor() }
            VButton { objectName: wc.compact ? "btnWorkdayEndLaterMini" : "btnWorkdayEndLater"; visible: !wc.compact && wc.w.auto_finished === true; enabled: wc.w.can_request_end === true; text: "Я работал дольше"; iconName: "sunset"
                      tip: wc.w.can_request_end === true ? "День завершён автоматически (забыли «Завершить»). Укажите, во сколько закончили на самом деле, — руководитель согласует, и таймер посчитает день до этого времени." : wc.w.end_block
                      onClicked: endForm.openFor() }
            VButton { objectName: wc.compact ? "btnWorkdayRequestsMini" : "btnWorkdayRequests"; visible: !wc.compact && (wc.w.review || []).length > 0; text: "Запросы по рабочему дню (" + (wc.w.to_decide || 0) + ")"
                      kind: (wc.w.to_decide || 0) > 0 ? "soft" : "ghost"; iconName: "user-check"; onClicked: requestsPopup.open()
                      tip: "Сотрудники просят считать день с более раннего начала (пропустили «Начать») или до более позднего окончания (день завершён автоматически) — согласуйте или отклоните (при связи)." }
        }
        Text { visible: !wc.compact; text: wc.w.next || ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
        // График работы (D-47): от него — фактическое начало и напоминание о конце дня.
        Text { visible: !wc.compact; objectName: "workdaySchedule"
               text: "График: " + (wc.w.schedule_label || "") + (wc.w.schedule_source ? " (" + wc.w.schedule_source + ")" : "")
                     + (wc.w.has_day === true && wc.w.schedule_day !== true && wc.w.exempt !== true ? " · сегодня по графику выходной" : "")
               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true
               VTipArea { text: "График назначает администратор (раздел «Оргструктура» → «Графики работы»). Фактическое начало дня — не раньше начала по графику; в конце дня по графику программа напомнит завершить день." } }
        // Подробности дня: начало, перерывы, завершение — строкой; нажатия — отдельной строкой с переносом.
        RowLayout {
            visible: !wc.compact && wc.w.has_day === true
            Layout.fillWidth: true; spacing: 14
            Text { objectName: wc.compact ? "workdayStartedMini" : "workdayStarted"; text: "Начало: " + (wc.w.started_label || "—") + (wc.w.approved === true ? " (согласовано; нажато в " + wc.w.pressed_label + ")" : "")
                   color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS }
            Text { objectName: wc.compact ? "workdayBreaksMini" : "workdayBreaks"; text: "Перерывы: " + (wc.w.break_label || "нет"); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS
                   VTipArea { text: "Перерыв считается только по кнопке «Перерыв» — программа сама перерывы не угадывает." } }
            Text { visible: (wc.w.finished_label || "") !== ""; text: "Завершён: " + (wc.w.finished_label || ""); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS }
            Item { Layout.fillWidth: true }
        }
        Text { visible: !wc.compact && wc.w.has_day === true; objectName: wc.compact ? "workdayEventsMini" : "workdayEvents"
               text: "Нажатия: " + (wc.w.events || []).map(e => e.time + " " + e.label.toLowerCase() + (e.local ? " (не отправлено)" : "")).join(" · ")
               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Banner { visible: !wc.compact && (wc.w.approved_label || "") !== ""; objectName: wc.compact ? "workdayApprovedMini" : "workdayApproved"; tone: "success"; text: wc.w.approved_label || "" }
        Banner { visible: !wc.compact && (wc.w.pending_request || {}).id !== undefined; objectName: wc.compact ? "workdayPendingRequestMini" : "workdayPendingRequest"; tone: "warning"
                 text: (wc.w.pending_request || {}).id ? "На согласовании у руководителя: начало дня с " + wc.w.pending_request.start_label + " — «" + wc.w.pending_request.reason + "»." : "" }
        Banner { visible: !wc.compact && wc.w.auto_finished === true; objectName: wc.compact ? "workdayAutoFinishedMini" : "workdayAutoFinished"; tone: "warning"; text: wc.w.end_label || "" }
        Banner { visible: !wc.compact && (wc.w.pending_end_request || {}).id !== undefined; objectName: wc.compact ? "workdayPendingEndMini" : "workdayPendingEnd"; tone: "warning"
                 text: (wc.w.pending_end_request || {}).id ? "На согласовании у руководителя: окончание дня в " + wc.w.pending_end_request.time_label + " — «" + wc.w.pending_end_request.reason + "»." : "" }
        Banner { visible: !wc.compact && wc.w.unsent === true; tone: "info"; text: "Есть нажатия, которые ещё не подтвердил сервер (очередь синхронизации) — таймер уже их учитывает." }
    }

    // ---------- «Завершить день?» — чтобы не нажать случайно
    FormDialog {
        id: finishConfirm
        objectName: wc.compact ? "workdayFinishConfirmMini" : "workdayFinishConfirm"
        title: "Завершить рабочий день?"
        subtitle: "Отработано " + wc.clock + ". Смена попадёт в дневной отчёт. Если вернётесь к работе — «Продолжить»."
        confirmText: "Завершить день"
        onConfirmed: { close(); wc.mark("finish") }
    }

    // ---------- «Я начал раньше» (A-49 п. 4)
    FormDialog {
        id: earlierForm
        objectName: wc.compact ? "workdayEarlierFormMini" : "workdayEarlierForm"
        title: "Фактическое начало рабочего дня"
        subtitle: "Нажали «Начать рабочий день» в " + (wc.w.pressed_label || "—") + ". Если работа началась раньше, укажите время и причину — руководитель согласует, и таймер посчитает день с фактического начала."
        confirmText: "Отправить на согласование"
        confirmTip: "Запрос уйдёт руководителю подразделения (тому, кто проверяет ваши дневные отчёты). Можно без связи — отправится при подключении."
        readonly property var check: startField.text.trim() === "" ? ({ ok: false, message: "" }) : backend.workdayStartCheck(startField.text)
        canConfirm: check.ok === true && reasonField.text.trim() !== ""
        note: check.ok === true ? check.message : ""
        function openFor() { startField.text = wc.w.request_default || ""; reasonField.text = ""; open() }
        onConfirmed: { working = true; backend.workdayStartRequest("workday-request", wc.w.id, check.start_at, reasonField.text) }
        VField { id: startField; objectName: wc.compact ? "workdayEarlierTimeMini" : "workdayEarlierTime"; label: "Фактическое начало"; placeholder: "08:00"; required: true
                 hint: "ЧЧ:ММ по часам компьютера: раньше нажатия «Начать», но не раньше начала рабочего дня по графику" + (wc.w.schedule_start_label ? " (" + wc.w.schedule_start_label + ")." : ".")
                 error: earlierForm.check.ok === false ? earlierForm.check.message : "" }
        VField { id: reasonField; objectName: wc.compact ? "workdayEarlierReasonMini" : "workdayEarlierReason"; label: "Причина"; required: true; placeholder: "Был на объекте, забыл нажать «Начать»" }
        Connections { target: backend
            function onCommandDone(t, r) { if (t !== "workday-request") return; earlierForm.working = false
                if (r.status === "accepted" || r.status === "queued") earlierForm.close(); else earlierForm.error = r.message_ru || "Запрос не отправлен." } }
    }

    // ---------- «Я работал дольше» (D-47, A-51 п. 5): день завершён автоматически, фактическое окончание — руководителю
    FormDialog {
        id: endForm
        objectName: wc.compact ? "workdayEndFormMini" : "workdayEndForm"
        title: "Фактическое окончание рабочего дня"
        subtitle: wc.w.end_label || ""
        confirmText: "Отправить на согласование"
        confirmTip: "Запрос уйдёт тому же руководителю, что решает о фактическом начале дня. Можно без связи — отправится при подключении."
        readonly property var check: endField.text.trim() === "" ? ({ ok: false, message: "" }) : backend.workdayEndCheck(endField.text)
        canConfirm: check.ok === true && endReason.text.trim() !== ""
        note: check.ok === true ? check.message : ""
        function openFor() { endField.text = wc.w.end_default || ""; endReason.text = ""; open() }
        onConfirmed: { working = true; backend.workdayEndRequest("workday-end-request", wc.w.end_day_id, check.end_at, endReason.text) }
        VField { id: endField; objectName: wc.compact ? "workdayEndTimeMini" : "workdayEndTime"; label: "Фактическое окончание"; placeholder: "19:30"; required: true
                 hint: "ЧЧ:ММ по часам компьютера: позже автоматического завершения и не позже, чем сейчас."
                 error: endForm.check.ok === false ? endForm.check.message : "" }
        VField { id: endReason; objectName: wc.compact ? "workdayEndReasonMini" : "workdayEndReason"; label: "Причина"; required: true; placeholder: "Задержался на объекте, забыл нажать «Завершить»" }
        Connections { target: backend
            function onCommandDone(t, r) { if (t !== "workday-end-request") return; endForm.working = false
                if (r.status === "accepted" || r.status === "queued") endForm.close(); else endForm.error = r.message_ru || "Запрос не отправлен." } }
    }

    // ---------- руководителю: запросы на фактическое начало и окончание дня
    Popup {
        id: requestsPopup
        objectName: wc.compact ? "workdayRequestsPopupMini" : "workdayRequestsPopup"
        modal: true; focus: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(640, (Overlay.overlay ? Overlay.overlay.width : 800) - 48)
        // Высота — по содержимому (один запрос — компактное окно), но не больше 560 и не выше окна программы.
        height: Math.min(560, (Overlay.overlay ? Overlay.overlay.height : 700) - 48,
                         reqHead.implicitHeight + reqHint.implicitHeight + reqList.contentHeight + 2 * Theme.padL + 20)
        padding: 0
        closePolicy: Popup.CloseOnEscape
        Overlay.modal: Rectangle { color: Theme.scrim }
        background: Rectangle { color: Theme.surface; radius: Theme.radiusL; border.color: Theme.border }
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout { id: reqHead; Layout.fillWidth: true; Layout.margins: Theme.padL; Layout.bottomMargin: 0
                Text { text: "Запросы по рабочему дню"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsL; font.weight: Font.DemiBold; Layout.fillWidth: true }
                VButton { kind: "ghost"; iconName: "x"; compact: true; onClicked: requestsPopup.close(); Accessible.name: "Закрыть" } }
            Text { id: reqHint; text: "Начало дня — сотрудник пропустил «Начать рабочий день» и просит считать день с более раннего времени (не раньше начала по графику). Окончание дня — день завершён автоматически, а сотрудник работал дольше. Согласовать или отклонить (с причиной) — при связи с сервером."
                   color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL }
            ListView {
                id: reqList
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.bottomMargin: Theme.padL
                clip: true; spacing: 8; model: wc.w.review || []
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle { id: rq; required property var modelData
                    objectName: wc.compact ? "workdayRequestRowMini" : "workdayRequestRow"
                    width: ListView.view.width; implicitHeight: rqc.implicitHeight + 16; radius: Theme.radius; color: Theme.surfaceAlt
                    ColumnLayout { id: rqc; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.margins: 10; spacing: 4
                        RowLayout { Layout.fillWidth: true
                            Text { text: rq.modelData.title; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            VChip { text: rq.modelData.kind_label; tone: "neutral" }
                            VChip { text: rq.modelData.status_label; tone: rq.modelData.status_tone } }
                        Text { text: "Причина: " + rq.modelData.reason + (rq.modelData.comment ? "  ·  решение: " + rq.modelData.comment : "")
                               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        Text { visible: rq.modelData.block !== ""; text: rq.modelData.block; color: Theme.warning; font.family: Theme.family; font.pixelSize: Theme.fsXs }
                        RowLayout { visible: rq.modelData.may_decide === true; spacing: 6
                            VButton { objectName: wc.compact ? "btnApproveStartMini" : "btnApproveStart"; text: "Согласовать"; kind: "primary"; compact: true; iconName: "circle-check"; enabled: rq.modelData.can_decide === true
                                      onClicked: decideForm.openFor(rq.modelData, "approve") }
                            VButton { objectName: wc.compact ? "btnRejectStartMini" : "btnRejectStart"; text: "Отклонить"; kind: "danger"; compact: true; iconName: "circle-x"; enabled: rq.modelData.can_decide === true
                                      onClicked: decideForm.openFor(rq.modelData, "reject") } }
                    }
                }
            }
        }
    }
    FormDialog {
        id: decideForm
        objectName: wc.compact ? "workdayDecideFormMini" : "workdayDecideForm"
        property var req: ({})
        property string decision: "approve"
        readonly property string what: req.kind === "end" ? "окончание" : "начало"
        title: (decision === "approve" ? "Согласовать фактическое " : "Отклонить фактическое ") + what + " дня"
        subtitle: req.title || ""
        confirmText: decision === "approve" ? "Согласовать" : "Отклонить"
        confirmKind: decision === "approve" ? "primary" : "danger"
        confirmTip: decision === "approve" ? (req.kind === "end" ? "Таймер сотрудника посчитает день до указанного времени; сотрудник получит уведомление." : "Таймер сотрудника посчитает день с указанного времени; сотрудник получит уведомление.") : "Запрос будет отклонён с вашей причиной; сотрудник получит уведомление."
        canConfirm: decision === "approve" || decideComment.text.trim() !== ""
        note: "Причина сотрудника: " + (req.reason || "")
        function openFor(r, d) { req = r; decision = d; decideComment.text = ""; open() }
        onConfirmed: decideCmd.run(req.command || "workday.start_decide", { id: req.id, decision: decision, comment: decideComment.text }, req.version)
        VField { id: decideComment; objectName: wc.compact ? "workdayDecideCommentMini" : "workdayDecideComment"; label: decideForm.decision === "approve" ? "Комментарий" : "Причина отказа"
                 required: decideForm.decision === "reject"; placeholder: decideForm.decision === "approve" ? "Необязательно" : "В это время вас не было на объекте" }
    }
    Cmd { id: decideCmd; dialog: decideForm; tag: "workday-decide" }
}
