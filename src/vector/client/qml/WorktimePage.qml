import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Раздел «Рабочее время» (этап 7, R-20; D-42). Дневной отчёт: смена (начало, конец, перерыв) и строки «на что ушло
// время» — проект и задача (если работа проектная), часы, что сделано.
//
//  * «Мои отчёты» — у каждого сотрудника. Черновик и «Отправить на проверку» — можно без связи (очередь). Отправить
//    можно полный отчёт: смена указана, есть строки, часов в строках не больше смены без перерыва. Исправить уже
//    отправленный или принятый — «Исправить» (при связи, с основанием): отчёт снова становится черновиком.
//  * «На проверке» — руководителю отдела (и замещающему): «Принять» или «Вернуть» с причиной (при связи). Свой отчёт
//    руководитель не проверяет — это делает другой («четыре глаза»).
//
// Часы в строках вводятся как удобно: «2», «1,5» или «1:30». Кнопки — подсказка; решает сервер.
MasterDetail {
    id: page
    objectName: "worktimePage"
    property var shell
    property string selectedId: ""
    readonly property var wv: backend.worktimeOverview
    readonly property var all: tab === "review" ? (wv.review || []) : (wv.mine || [])
    readonly property var items: all.filter(r => search === "" || (r.date_label + " " + r.user_name + " " + r.status_label).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: (wv.mine || []).concat(wv.review || []).find(r => r.id === selectedId) || null
    // Открыть отчёт (переход из уведомления): выбрать вкладку, где он есть.
    function openObject(id) {
        if (!id) return
        tab = (wv.review || []).some(r => r.id === id) ? "review" : "mine"
        selectedId = id
    }
    Component.onCompleted: { tab = wv.default_tab || "mine"; if (shell && shell.navTarget.page === "worktime") openObject(shell.navTarget.id) }
    Connections { target: shell; ignoreUnknownSignals: true
                  function onNavTargetChanged() { if (shell.navTarget.page === "worktime" && shell.navTarget.id) page.openObject(shell.navTarget.id) } }
    tab: "mine"
    tabsName: "worktimeTabs"
    tabs: wv.tabs || []
    onTabPicked: id => { tab = id; selectedId = "" }
    title: "Рабочее время"
    subtitle: tab === "review" ? "На проверке: " + ((wv.counts || {}).to_review || 0) + "  ·  " + (wv.as_of || "")
              : "Сегодня " + (wv.today_label || "") + "  ·  " + (wv.week_label || "") + "  ·  " + (wv.as_of || "")
    hasSelection: sel !== null
    emptyIcon: tab === "review" ? "user-check" : "clock"
    emptyTitle: tab === "review" ? "Нечего проверять" : "Отчётов пока нет"
    emptyText: tab === "review" ? (wv.empty_review || "") : (wv.empty_mine || "")
    emptyAction: tab === "mine" ? "Отчёт за сегодня" : ""
    onEmptyActionTriggered: page.openToday()
    noSelectionText: "Выберите отчёт"
    // «Отчёт за сегодня»: открыть уже заведённый или создать новый.
    function openToday() {
        tab = "mine"
        if (wv.today_id) { selectedId = wv.today_id; return }
        editor.openFor(null, wv.today)
    }
    headerActions: [
        VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() },
        VButton { objectName: "btnOtherDay"; text: "За другой день"; iconName: "calendar"; onClicked: dayForm.openFor() },
        VButton { objectName: "btnToday"; text: page.wv.today_id ? "Отчёт за сегодня" : "Заполнить за сегодня"; kind: "primary"
                  iconName: page.wv.today_id ? "clock" : "plus"; onClicked: page.openToday() }
    ]
    list.model: items
    list.delegate: ListRow {
        title: modelData.date_label + (modelData.is_own ? "" : "  ·  " + modelData.user_name)
        subtitle: modelData.shift_label + "  ·  в строках " + modelData.total_label
        iconName: modelData.is_own ? "clock" : "user"
        chipText: modelData.status_label; chipTone: modelData.status_tone
        pending: modelData.pending === true
        selected: modelData.id === page.selectedId
        onClicked: page.selectedId = modelData.id
    }

    // ---------- карточка
    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Rectangle { width: 52; height: 52; radius: 14; color: Theme.primarySoft
            Icon { anchors.centerIn: parent; name: "clock"; size: 26; color: Theme.primary } }
        ColumnLayout { spacing: 4; Layout.fillWidth: true
            Text { text: sel ? (sel.is_own ? "Мой дневной отчёт" : "Дневной отчёт · " + sel.user_name) : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { objectName: "reportTitle"; text: sel ? sel.date_label : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true } }
        VChip { text: sel ? sel.status_label : ""; tone: sel ? sel.status_tone : "neutral" }
    }
    Banner { visible: sel !== null && sel.pending === true; tone: "info"
             text: "Есть изменения, которые ещё не подтвердил сервер (очередь синхронизации). Они уйдут при связи." }
    Banner { objectName: "reportReturned"; visible: sel !== null && sel.status === "returned"; tone: "danger"
             text: sel ? "Возвращён: " + sel.review_comment + (sel.reviewed_label ? "  (" + sel.reviewed_label + ")" : "") : "" }
    Banner { objectName: "reportProblems"; visible: sel !== null && sel.is_own && sel.problems.length > 0; tone: "warning"
             text: sel ? "Чтобы отправить на проверку: " + sel.problems.join(" ") : "" }
    Banner { visible: sel !== null && sel.block_review !== ""; tone: "info"; text: sel ? sel.block_review : "" }
    Banner { visible: sel !== null && (sel.may_review || sel.may_reopen) && page.wv.online !== true; tone: "warning"
             text: "Принять, вернуть и исправить отправленный отчёт можно только при связи с сервером." }
    Banner { visible: sel !== null && sel.status === "accepted"; tone: "success"
             text: sel ? "Принят: " + sel.reviewed_label + "." + (sel.review_comment ? " " + sel.review_comment : "") : "" }
    Text { visible: sel !== null && sel.next !== ""; text: sel ? sel.next : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
    Flow {
        Layout.fillWidth: true; spacing: 8
        visible: sel !== null
        VButton { objectName: "btnEditReport"; visible: sel !== null && sel.can_edit === true; text: "Изменить"; iconName: "pencil"; onClicked: editor.openFor(sel, sel.work_date) }
        VButton { objectName: "btnSubmitReport"; visible: sel !== null && sel.may_submit === true; enabled: sel !== null && sel.can_submit === true
                  text: "Отправить на проверку"; kind: "primary"; iconName: "send"; onClicked: submitForm.open() }
        VButton { objectName: "btnReopenReport"; visible: sel !== null && sel.may_reopen === true; enabled: sel !== null && sel.can_reopen === true
                  text: "Исправить"; iconName: "rotate-ccw"; onClicked: reopenForm.openFor() }
        VButton { objectName: "btnAcceptReport"; visible: sel !== null && sel.may_review === true; enabled: sel !== null && sel.can_review === true
                  text: "Принять"; kind: "primary"; iconName: "circle-check"; onClicked: reviewForm.openFor("accept")
                  tip: "Принять дневной отчёт сотрудника (руководитель отдела, не свой). Только при связи с сервером." }
        VButton { objectName: "btnReturnReport"; visible: sel !== null && sel.may_review === true; enabled: sel !== null && sel.can_review === true
                  text: "Вернуть"; kind: "danger"; iconName: "undo-2"; onClicked: reviewForm.openFor("return")
                  tip: "Вернуть отчёт сотруднику на исправление — с причиной. Только при связи с сервером." }
    }

    SectionTitle { text: "Смена и итоги"; iconName: "clock" }
    KeyValue { kWidth: 170; visible: sel !== null && !sel.is_own; k: "Сотрудник"; v: sel ? sel.user_name : "" }
    KeyValue { kWidth: 170; k: "Смена"; v: sel ? sel.shift_label : "" }
    KeyValue { kWidth: 170; objectName: "reportTotal"; k: "В строках"; v: sel ? sel.total_label : "" }
    KeyValue { kWidth: 170; k: "Смена без перерыва"; v: sel ? sel.net_label : "" }
    KeyValue { kWidth: 170; objectName: "reportUnallocated"; k: "Не распределено"; v: sel ? sel.unallocated_label : "" }
    KeyValue { kWidth: 170; visible: sel !== null && sel.comment !== ""; k: "Комментарий"; v: sel ? sel.comment : "" }
    KeyValue { kWidth: 170; visible: sel !== null && sel.submitted_label !== ""; k: "Отправлен"; v: sel ? sel.submitted_label : "" }

    SectionTitle { text: "На что ушло время"; iconName: "list-checks" }
    Text { visible: sel !== null && sel.lines.length === 0; text: "Строк пока нет — нажмите «Изменить» и добавьте, чем занимались."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
    Repeater {
        model: sel ? sel.lines : []
        delegate: RowLayout { id: lrow; required property var modelData
            objectName: "reportLineRow"
            Layout.fillWidth: true; spacing: 10
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { text: lrow.modelData.note; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { text: lrow.modelData.project_label + (lrow.modelData.task_label ? "  ·  " + lrow.modelData.task_label : "")
                       color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
            Text { text: lrow.modelData.hours_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; font.features: { "tnum": 1 }; Layout.alignment: Qt.AlignTop }
        }
    }

    SectionTitle { visible: sel !== null; text: "История"; iconName: "history" }
    Repeater {
        model: sel ? sel.history : []
        delegate: RowLayout { id: hrow; required property var modelData
            objectName: "reportHistoryRow"
            Layout.fillWidth: true; spacing: 10
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { text: hrow.modelData.label + "  ·  " + hrow.modelData.who; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { visible: hrow.modelData.extra !== ""; text: hrow.modelData.extra; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
            Text { text: hrow.modelData.when; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; Layout.alignment: Qt.AlignTop } }
    }
    Item { implicitHeight: 8 }

    // ==================================================== формы
    // Отчёт за другой день (не в будущем и не старше 31 дня): если отчёт за этот день уже есть — открыть его.
    FormDialog {
        id: dayForm
        objectName: "reportDayForm"
        title: "Отчёт за другой день"
        subtitle: "Например, если забыли заполнить вчера. Новый отчёт — не старше " + (page.wv.backdate_label || "31 дня") + "."
        confirmText: "Продолжить"
        readonly property bool valid: /^\d{4}-\d{2}-\d{2}$/.test(dayField.text.trim())
        canConfirm: valid
        function openFor() { dayField.text = page.wv.today || ""; open() }
        onConfirmed: {
            var d = dayField.text.trim()
            var have = (page.wv.mine || []).find(r => r.work_date === d)
            close()
            page.tab = "mine"
            if (have) page.selectedId = have.id
            else editor.openFor(null, d)
        }
        VField { id: dayField; objectName: "reportDay"; label: "Дата рабочего дня"; placeholder: "ГГГГ-ММ-ДД"; required: true
                 hint: "Формат ГГГГ-ММ-ДД, например 2026-11-17."; onAccepted: if (dayForm.valid) dayForm.confirmed() }
    }

    FormDialog {
        id: submitForm
        objectName: "reportSubmitForm"
        title: "Отправить отчёт на проверку"
        subtitle: sel ? sel.date_label + "  ·  в строках " + sel.total_label + "  ·  смена без перерыва " + sel.net_label : ""
        confirmText: "Отправить"
        confirmTip: "Отправить отчёт руководителю отдела на проверку. Можно без связи: уйдёт при подключении."
        note: "Отчёт уйдёт руководителю отдела (можно без связи — отправится при подключении). После отправки править можно только через «Исправить» с основанием."
        onConfirmed: { working = true; backend.submitReport("report-submit", sel.id) }
        Connections { target: backend
            function onCommandDone(t, r) { if (t !== "report-submit") return; submitForm.working = false
                if (r.status === "accepted" || r.status === "queued") submitForm.close(); else submitForm.error = r.message_ru || "Отчёт не отправлен." } }
    }

    FormDialog {
        id: reopenForm
        objectName: "reportReopenForm"
        title: "Исправить отправленный отчёт"
        subtitle: sel ? sel.date_label + " — «" + sel.status_label + "»" : ""
        confirmText: "Открыть для исправления"
        canConfirm: reopenReason.text.trim() !== ""
        note: "Отчёт снова станет черновиком: исправьте его и отправьте ещё раз. Основание сохранится в истории" + (sel && sel.status === "accepted" ? "; принявший руководитель получит уведомление." : ".")
        function openFor() { reopenReason.text = ""; open() }
        onConfirmed: reopenCmd.run("report.reopen", { id: sel.id, reason: reopenReason.text }, sel.version)
        VField { id: reopenReason; objectName: "reportReopenReason"; label: "Основание исправления"; required: true; placeholder: "Забыл указать совещание по проекту" }
    }
    Cmd { id: reopenCmd; dialog: reopenForm; tag: "report-reopen" }

    FormDialog {
        id: reviewForm
        objectName: "reportReviewForm"
        title: "Проверка дневного отчёта"
        subtitle: sel ? sel.user_name + "  ·  " + sel.date_label + "  ·  в строках " + sel.total_label : ""
        confirmText: rvDec.current === "accept" ? "Принять" : "Вернуть"
        confirmTip: rvDec.current === "accept" ? "Отчёт будет принят; сотрудник получит уведомление." : "Отчёт вернётся сотруднику с вашей причиной."
        confirmKind: rvDec.current === "accept" ? "primary" : "danger"
        canConfirm: rvDec.current === "accept" || rvComment.text.trim() !== ""
        function openFor(dec) { rvDec.current = dec; rvComment.text = ""; open() }
        onConfirmed: reviewCmd.run("report.review", { id: sel.id, decision: rvDec.current, comment: rvComment.text }, sel.version)
        VSegmented { id: rvDec; objectName: "reportDecision"; current: "accept"; onPicked: id => current = id
                     model: [{ id: "accept", label: "Принять", icon: "circle-check", tip: "Отчёт верный — принять." },
                             { id: "return", label: "Вернуть", icon: "undo-2", tip: "Вернуть сотруднику на исправление; причину он увидит в отчёте." }] }
        VField { id: rvComment; objectName: "reportReviewComment"; label: rvDec.current === "accept" ? "Комментарий" : "Причина возврата — что исправить"
                 required: rvDec.current === "return"; placeholder: rvDec.current === "accept" ? "Необязательно" : "Укажите задачу во второй строке" }
    }
    Cmd { id: reviewCmd; dialog: reviewForm; tag: "report-review" }

    // ---------- редактор отчёта: смена + таблица строк (ListModel — правка не пересоздаёт строки).
    Popup {
        id: editor
        objectName: "reportEditor"
        property var rep: null           // null — новый отчёт
        property string repId: ""
        property string workDate: ""
        property int editRev: 0
        property bool working: false
        property string error: ""
        readonly property string tag: "report-save"
        readonly property var check: { editRev; return visible ? backend.reportCheck(collect()) : ({ ok: true, rows: {}, totals: {}, submit_problems: [] }) }
        readonly property var projOpts: page.wv.project_options || []
        modal: true; focus: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(1040, (Overlay.overlay ? Overlay.overlay.width : 1100) - 48)
        height: Math.min(720, (Overlay.overlay ? Overlay.overlay.height : 800) - 48)
        padding: 0
        closePolicy: Popup.CloseOnEscape
        Overlay.modal: Rectangle { color: Theme.scrim }
        background: Rectangle { color: Theme.surface; radius: Theme.radiusL; border.color: Theme.border }
        ListModel { id: lm }
        function idx(list, id) { for (var i = 0; i < list.length; i++) if (String(list[i].id) === String(id || "")) return i; return 0 }
        function taskOpts(pid) { return [{ id: "", label: "без задачи" }].concat((page.wv.task_options || []).filter(t => t.project_id === pid)) }
        // openFor(null, день) — новый отчёт за день; openFor(отчёт) — правка черновика или возвращённого.
        function openFor(r, day) {
            rep = r || null; error = ""; working = false
            repId = r ? r.id : backend.newId()
            workDate = r ? r.work_date : day
            startField.text = r && r.shift_start ? r.shift_start : "09:00"
            endField.text = r && r.shift_end ? r.shift_end : "18:00"
            breakField.text = r ? String(r.break_minutes) : "60"
            commentField.text = r ? r.comment : ""
            lm.clear()
            var ls = r ? r.lines : []
            for (var i = 0; i < ls.length; i++)
                lm.append({ project_id: ls[i].project_id || "", task_id: ls[i].task_id || "", hours: ls[i].hours_input, note: ls[i].note })
            if (lm.count === 0) lm.append({ project_id: "", task_id: "", hours: "", note: "" })
            editRev++
            open()
        }
        function collect() {
            var out = []
            for (var i = 0; i < lm.count; i++) { var r = lm.get(i); out.push({ project_id: r.project_id, task_id: r.task_id, hours: r.hours, note: r.note }) }
            return { work_date: workDate, shift_start: startField.text, shift_end: endField.text, break_minutes: breakField.text,
                     comment: commentField.text, lines: out }
        }
        function setCell(i, key, value) { lm.setProperty(i, key, value); editRev++ }
        function save() {
            working = true; error = ""
            var p = collect(); p.id = repId
            backend.saveReport(tag, p)
        }
        Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== editor.tag) return
                editor.working = false
                if (r.status === "accepted" || r.status === "queued") { editor.close(); page.tab = "mine"; page.selectedId = editor.repId }
                else editor.error = r.message_ru || "Отчёт не сохранён."
            }
        }
        contentItem: ColumnLayout {
            spacing: 0
            RowLayout {
                Layout.fillWidth: true; Layout.margins: Theme.padL; Layout.bottomMargin: 10; spacing: 12
                Rectangle { width: 40; height: 40; radius: 11; color: Theme.primarySoft
                    Icon { anchors.centerIn: parent; name: "clock"; size: 20; color: Theme.primary } }
                ColumnLayout { spacing: 2; Layout.fillWidth: true
                    Text { text: (editor.rep ? "Дневной отчёт" : "Новый дневной отчёт") + " · " + editor.workDate.split("-").reverse().join(".")
                           color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsL; font.weight: Font.DemiBold }
                    Text { text: "Часы в строке — как удобно: 2, 1,5 или 1:30. Проект — только тот, где вы участник; задача — необязательно. Сохранять можно без связи."
                           color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
                VButton { kind: "ghost"; iconName: "x"; compact: true; onClicked: editor.close(); Accessible.name: "Закрыть" }
            }
            GridLayout {
                Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL
                columns: 4; columnSpacing: 12; rowSpacing: 8
                VField { id: startField; objectName: "reportShiftStart"; label: "Начало смены"; placeholder: "09:00"; Layout.fillWidth: true; Layout.preferredWidth: 1
                         onTextChanged: editor.editRev++ }
                VField { id: endField; objectName: "reportShiftEnd"; label: "Конец смены"; placeholder: "18:00"; Layout.fillWidth: true; Layout.preferredWidth: 1
                         onTextChanged: editor.editRev++ }
                VField { id: breakField; objectName: "reportBreak"; label: "Перерыв, мин"; placeholder: "60"; Layout.fillWidth: true; Layout.preferredWidth: 1
                         onTextChanged: editor.editRev++ }
                VField { id: commentField; objectName: "reportComment"; label: "Комментарий к отчёту"; placeholder: "Необязательно"; Layout.fillWidth: true; Layout.preferredWidth: 2 }
            }
            Rectangle {
                Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 12
                implicitHeight: 30; radius: Theme.radiusS; color: Theme.surfaceAlt
                RowLayout { anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 6
                    Repeater {
                        model: [{ t: "Проект", w: 230 }, { t: "Задача", w: 200 }, { t: "Часы *", w: 80 }, { t: "Что сделано *", w: -1 }, { t: "", w: 34 }]
                        delegate: Text { required property var modelData
                            text: modelData.t; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.DemiBold
                            Layout.preferredWidth: modelData.w > 0 ? modelData.w : -1; Layout.fillWidth: modelData.w < 0 }
                    }
                }
            }
            ListView {
                id: lv
                objectName: "reportEditorRows"
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL
                clip: true; spacing: 2; model: lm
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    id: rowItem
                    required property int index
                    required property var model
                    readonly property string problem: editor.check.rows ? (editor.check.rows[String(index)] || "") : ""
                    width: lv.width; implicitHeight: rc.implicitHeight + 8; radius: Theme.radiusS
                    color: problem !== "" ? Theme.dangerSoft : "transparent"
                    ColumnLayout { id: rc; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                   anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 2
                        RowLayout { Layout.fillWidth: true; spacing: 6
                            VSelect { Layout.preferredWidth: 230; Layout.fillWidth: false; model: editor.projOpts; tip: "Проект, на который ушло это время (только где вы участник). «Не по проекту» — общие дела отдела."
                                      currentIndex: editor.idx(editor.projOpts, rowItem.model.project_id)
                                      onCurrentValueChanged: if (currentValue !== rowItem.model.project_id) { editor.setCell(rowItem.index, "project_id", currentValue); editor.setCell(rowItem.index, "task_id", "") } }
                            VSelect { readonly property var opts: editor.taskOpts(rowItem.model.project_id)
                                      Layout.preferredWidth: 200; Layout.fillWidth: false; model: opts; enabled: rowItem.model.project_id !== ""
                                      tip: "Задача проекта — необязательно."
                                      currentIndex: editor.idx(opts, rowItem.model.task_id)
                                      onCurrentValueChanged: if (currentValue !== rowItem.model.task_id) editor.setCell(rowItem.index, "task_id", currentValue) }
                            CalcCell { Layout.preferredWidth: 80; num: true; text: rowItem.model.hours; placeholder: "1:30"; Accessible.name: "Часы, строка " + (rowItem.index + 1)
                                       onTextEdited: editor.setCell(rowItem.index, "hours", text) }
                            CalcCell { Layout.fillWidth: true; text: rowItem.model.note; placeholder: "Что сделано"; Accessible.name: "Что сделано, строка " + (rowItem.index + 1)
                                       onTextEdited: editor.setCell(rowItem.index, "note", text) }
                            VButton { kind: "ghost"; compact: true; iconName: "trash-2"; Layout.preferredWidth: 34; Accessible.name: "Удалить строку " + (rowItem.index + 1)
                                      onClicked: { lm.remove(rowItem.index); editor.editRev++ } }
                        }
                        Text { visible: rowItem.problem !== ""; text: rowItem.problem; color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsXs; leftPadding: 4; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 8; spacing: 8
                VButton { objectName: "reportAddLine"; text: "Добавить строку"; iconName: "plus"; kind: "soft"; compact: true
                          onClicked: { lm.append({ project_id: "", task_id: "", hours: "", note: "" }); editor.editRev++; lv.positionViewAtEnd() } }
                Item { Layout.fillWidth: true }
                Text { objectName: "reportEditorTotals"
                       text: "В строках: " + ((editor.check.totals || {}).total_label || "—") + "  ·  смена без перерыва: " + ((editor.check.totals || {}).net_label || "—")
                             + "  ·  не распределено: " + ((editor.check.totals || {}).unallocated_label || "—")
                       color: (editor.check.totals || {}).tone === "danger" ? Theme.danger : Theme.text
                       font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
            }
            Banner { tone: "danger"; text: editor.error; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 10; Layout.fillWidth: true }
            Rectangle { Layout.fillWidth: true; Layout.topMargin: 12; height: 1; color: Theme.border }
            RowLayout {
                Layout.fillWidth: true; Layout.margins: 16; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; spacing: 8
                Text { objectName: "reportEditorProblem"
                       text: editor.check.ok === false ? editor.check.message
                             : ((editor.check.submit_problems || []).length > 0 ? "Сохранить можно; чтобы отправить: " + editor.check.submit_problems.join(" ") : "")
                       color: editor.check.ok === false ? Theme.danger : Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                VButton { text: "Отмена"; kind: "ghost"; onClicked: editor.close() }
                VButton { objectName: "reportSave"; text: editor.working ? "Сохранение…" : "Сохранить"; kind: "primary"; iconName: "check"
                          enabled: !editor.working && editor.check.ok === true
                          onClicked: editor.save() }
            }
        }
    }
}
