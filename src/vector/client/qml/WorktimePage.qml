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
// D-43 (ответы владельца на A-48): за прошедший день, где сотрудник работал в программе, но отчёт не написал,
// программа собирает отчёт сама — «На самопроверке»; строки помечены «по таймеру» / «оценка программы». Отправка
// руководителю — только после самопроверки (форма с обязательными отметками) — и для собранного, и для своего отчёта.
// Ночная смена: конец раньше начала — до утра следующего дня.
//
// Часы в строках вводятся как удобно: «2», «1,5» или «1:30». Кнопки — подсказка; решает сервер.
//
// D-46 (ответы владельца на A-49): над списком — таймер рабочего дня (WorkdayCard). Новый отчёт берёт смену из
// таймера — без округления, перерыв — только нажатые «Перерыв».
MasterDetail {
    id: page
    objectName: "worktimePage"
    property var shell
    property string selectedId: ""
    property string overtimePeriodMessage: ""
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
    // Таймер рабочего дня (D-46): подробности дня, «Я начал раньше», руководителю — запросы на начало дня.
    topContent: [
        WorkdayCard { Layout.fillWidth: true },
        Rectangle {
            objectName: "overtimePanel"
            Layout.fillWidth: true
            implicitHeight: overtimeColumn.implicitHeight + 24
            radius: 12; color: Theme.surface; border.color: Theme.border
            ColumnLayout {
                id: overtimeColumn
                anchors.fill: parent; anchors.margins: 12; spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: "Сверхурочные, выходные и праздники"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; Layout.fillWidth: true
                        ToolTip.visible: overtimeTitleHover.hovered; ToolTip.text: "ВЕКТОР фиксирует согласованные часы и причины; денежный расчёт выполняется в 1С."
                        HoverHandler { id: overtimeTitleHover } }
                    Text { text: "Учётный период: " + (page.wv.overtime_period_label || "Не настроен"); color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                    Text { visible: (page.wv.overtime_norm_label || "") !== ""; text: page.wv.overtime_norm_label || ""
                           color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    VButton { objectName: "overtimeNew"; text: "Заявка"; iconName: "plus"; kind: "primary"; compact: true
                        tip: "Создать предварительную заявку или срочно зафиксировать завершённую работу с причиной. Требуется связь."
                        onClicked: overtimeForm.openFor() }
                }
                RowLayout {
                    visible: page.wv.period_manage === true
                    Layout.fillWidth: true; spacing: 8
                    Text { text: "Настройка компании"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                    VSegmented { id: overtimePeriodMode; objectName: "overtimePeriodMode"
                        current: (page.wv.overtime_period_config || {}).mode || "month"
                        model: [{ id: "month", label: "Месяц", icon: "calendar", tip: "Календарный месяц — самый короткий учётный период." },
                                { id: "quarter", label: "Квартал", icon: "calendar-days", tip: "Календарный квартал; границы каждой заявки сохраняются." },
                                { id: "year", label: "Год", icon: "calendar-range", tip: "Календарный год; период не длиннее одного года." }]
                        onPicked: id => current = id }
                    VButton { objectName: "overtimePeriodSave"; text: "Сохранить период"; iconName: "check"; kind: "soft"
                        tip: "Сохранить настройку для новых заявок. Уже созданные заявки не изменятся."
                        onClicked: backend.setOvertimePeriod("overtime-period-set", overtimePeriodMode.current,
                                                            (page.wv.overtime_period_config || {}).version || 0) }
                }
                Text { visible: page.overtimePeriodMessage !== ""; text: page.overtimePeriodMessage
                    color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Connections { target: backend
                    function onCommandDone(tag, result) {
                        if (tag === "overtime-period-set") page.overtimePeriodMessage = result.message_ru || "Настройка периода сохранена."
                    }
                }
                Text { visible: page.wv.period_manage !== true && page.wv.overtime_period_label === "Не настроен"
                    text: "Период ещё не настроен. Попросите администратора выбрать месяц, квартал или год."
                    color: Theme.warning; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Repeater {
                    model: page.wv.overtime_requests || []
                    delegate: Rectangle {
                        required property var modelData
                        objectName: "overtimeRequestRow_" + modelData.id
                        Layout.fillWidth: true; implicitHeight: overtimeRow.implicitHeight + 12
                        radius: Theme.radiusS; color: Theme.surfaceAlt
                        RowLayout {
                            id: overtimeRow
                            anchors.fill: parent; anchors.margins: 8; spacing: 10
                            ColumnLayout { Layout.fillWidth: true; spacing: 2
                                Text { text: modelData.kind_label + " · " + modelData.work_date; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
                                Text { text: modelData.minutes + " мин · " + modelData.mode_label + " · " + modelData.status_label
                                        + (modelData.period ? " · период " + modelData.period.start + " — " + modelData.period.end : "")
                                        + (modelData.reason ? " · " + modelData.reason : "")
                                    color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                Text { visible: !!modelData.ground
                                       text: (modelData.ground_label || "") + (modelData.ground_article ? " (" + modelData.ground_article + ")" : "")
                                       color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                Row { visible: !!modelData.ground; spacing: 4
                                    VChip { visible: modelData.consent_confirmed === true; text: "Согласие получено"; tone: "success" }
                                    VChip { visible: modelData.refusal_right_notified === true; text: "Право отказа разъяснено"; tone: "success" }
                                    Repeater { model: modelData.category_labels || []
                                        delegate: VChip { required property var modelData; text: modelData; tone: "warning" } }
                                }
                            }
                            VButton { objectName: "overtimeApprove_" + modelData.id; visible: page.wv.reviewer && !modelData.is_own && modelData.status === "pending"
                                text: "Согласовать"; iconName: "circle-check"; kind: "primary"; compact: true
                                tip: "Принять часы по заявке; решение попадёт в историю и автор получит уведомление."
                                onClicked: backend.overtimeDecision(backend.newId(), modelData.id, "approve", "", modelData.version) }
                            VButton { visible: page.wv.reviewer && !modelData.is_own && modelData.status === "pending"
                                text: "Отклонить"; iconName: "x"; kind: "danger"; compact: true
                                tip: "Отклонить с обязательной причиной."
                                onClicked: overtimeRejectForm.openFor(modelData) }
                            VButton { objectName: "overtimeClose_" + modelData.id; visible: page.wv.period_manage && !modelData.is_own
                                text: "Закрыть ведомость"; iconName: "lock"; kind: "soft"; compact: true
                                tip: "Закрыть период для сотрудника: снимок только подтверждённых заявок, без ставки и суммы оплаты."
                                onClicked: overtimeCloseForm.openFor(modelData) }
                        }
                    }
                }
                Repeater {
                    model: page.wv.overtime_closures || []
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Text { Layout.fillWidth: true; text: "Закрытая ведомость · " + modelData.user_name + " · "
                                + modelData.period_start + " — " + modelData.period_end + " · версия " + modelData.revision
                            color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap }
                        Text { text: modelData.total_label + (modelData.norm_label ? " из нормы " + modelData.norm_label : "") + " · SHA-256 " + modelData.hash_short
                            color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs }
                    }
                }
                Text { visible: (page.wv.overtime_requests || []).length === 0; text: "Заявок на часы вне графика пока нет."
                    color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
            }
        },
        Rectangle {
            Layout.fillWidth: true
            visible: page.tab === "review" && (page.wv.weekly_metrics.units || []).length > 0
            implicitHeight: metricsColumn.implicitHeight + 24
            radius: 12; color: Theme.surface; border.color: Theme.border
            ToolTip.visible: metricsHover.hovered
            ToolTip.text: "Итоги отправленных дневных отчётов за прошлую полную неделю. Данные сгруппированы по подразделениям и включают подчинённые подразделения; персональные действия сотрудников не собираются. Это рабочая метрика, не табель."
            HoverHandler { id: metricsHover }
            ColumnLayout {
                id: metricsColumn
                anchors.fill: parent; anchors.margins: 12; spacing: 6
                Text { text: "Недельные итоги подразделений · " + page.wv.weekly_metrics.week_start + " — " + page.wv.weekly_metrics.week_end
                    color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
                Repeater {
                    model: page.wv.weekly_metrics.units || []
                    delegate: RowLayout {
                        Layout.fillWidth: true
                        Text { Layout.fillWidth: true; text: modelData.name; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs }
                        Text { text: modelData.reports + " отчётов · " + modelData.hours_label; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
                    }
                }
            }
        }
    ]
    tabs: wv.tabs || []
    onTabPicked: id => { tab = id; selectedId = "" }
    title: "Рабочее время"
    subtitle: tab === "review" ? "На проверке: " + ((wv.counts || {}).to_review || 0) + "  ·  " + (wv.as_of || "")
              : "Сегодня " + (wv.today_label || "") + "  ·  " + (wv.week_label || "")
                + (((wv.counts || {}).self_check || 0) > 0 ? "  ·  на самопроверке: " + wv.counts.self_check : "") + "  ·  " + (wv.as_of || "")
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
        // Отметка дня (D-49) — первой: длинная строка обрезается справа, а отметку руководитель должен видеть.
        subtitle: (modelData.day_mark_short ? modelData.day_mark_short + "  ·  " : "")
                  + modelData.shift_label + "  ·  в строках " + modelData.total_label
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
    Banner { objectName: "reportAssembled"; visible: sel !== null && sel.origin === "auto" && sel.status === "self_check"; tone: "info"
             text: sel ? "Отчёт собран программой " + sel.assembled_label + ". Проверьте смену и строки: «оценка программы» — "
                         + "время, разделённое по числу действий, его стоит уточнить. Руководителю отдела отчёт уйдёт только после самопроверки." : "" }
    // Отметка дня (D-49, A-53 п. 3): работа в праздничный / выходной день, сверхурочно — считает сервер по графику
    // сотрудника и производственному календарю.
    Banner { objectName: "reportDayMark"; visible: sel !== null && sel.day_mark_label !== ""; tone: sel ? sel.day_mark_tone : "warning"
             text: sel ? sel.day_mark_label + ". Считается по смене без перерыва и графику работы сотрудника." : "" }
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
                  text: "Самопроверка и отправка"; kind: "primary"; iconName: "list-checks"; onClicked: submitForm.openFor() }
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
            VChip { visible: lrow.modelData.source_label !== ""; text: lrow.modelData.source_label; Layout.alignment: Qt.AlignTop
                    tone: lrow.modelData.source === "measured" ? "success" : "warning" }
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

    // ==================================================== заявки на часы вне графика (A-55/A-56)
    FormDialog {
        id: overtimeForm
        objectName: "overtimeRequestForm"
        title: "Заявка на часы вне графика"
        subtitle: "Период: " + (page.wv.overtime_period_label || "не настроен") + ". Оплата рассчитывается в 1С."
        confirmText: "Отправить руководителю"
        confirmTip: "Предварительная заявка должна быть отправлена до начала работы; срочную работу можно внести после окончания."
        // D-74: основание из справочника ст. 99/113; согласия — по основанию и своим кадровым категориям.
        readonly property var groundModel: (page.wv.hr_grounds || []).filter(g => (g.kinds || []).indexOf(overtimeKind.current) >= 0)
        readonly property var groundSel: (overtimeGround.currentIndex >= 0 && overtimeGround.currentIndex < groundModel.length)
                                         ? groundModel[overtimeGround.currentIndex] : null
        readonly property bool noticeNeeded: {
            var own = page.wv.own_hr_categories || []
            return (page.wv.hr_category_catalog || []).some(c => c.rule === "consent" && own.indexOf(c.code) >= 0)
        }
        readonly property bool consentNeeded: (groundSel !== null && groundSel.consent === true) || noticeNeeded
        canConfirm: (page.wv.overtime_period_config || {}).mode !== undefined
                     && /^\d{4}-\d{2}-\d{2}$/.test(overtimeDate.text.trim())
                     && overtimeStart.text.trim() !== "" && overtimeEnd.text.trim() !== ""
                     && /^\d+$/.test(overtimeMinutes.text.trim()) && Number(overtimeMinutes.text) > 0
                     && overtimeReason.text.trim() !== ""
                     && groundSel !== null
                     && (!consentNeeded || overtimeConsent.checked)
                     && (!noticeNeeded || overtimeNotice.checked)
        function openFor() {
            error = ""; working = false
            overtimeDate.text = page.wv.today || ""
            overtimeStart.text = ""; overtimeEnd.text = ""; overtimeMinutes.text = ""
            overtimeReason.text = ""; overtimeMode.current = "planned"; overtimeKind.current = "overtime"
            overtimeGround.currentIndex = -1; overtimeConsent.checked = false; overtimeNotice.checked = false
            open()
        }
        onConfirmed: {
            working = true
            backend.overtimeRequest("overtime-request-create", {
                work_date: overtimeDate.text.trim(), start_at: overtimeStart.text.trim(), end_at: overtimeEnd.text.trim(),
                minutes: Number(overtimeMinutes.text), kind: overtimeKind.current, mode: overtimeMode.current,
                reason: overtimeReason.text.trim(),
                ground: groundSel ? groundSel.code : "",
                consent_confirmed: overtimeConsent.checked, refusal_right_notified: overtimeNotice.checked
            })
        }
        VField { id: overtimeDate; objectName: "overtimeWorkDate"; label: "Местная дата начала"; placeholder: "ГГГГ-ММ-ДД"; required: true
            hint: "Дата сотрудника, например 2026-11-17." }
        VField { id: overtimeStart; objectName: "overtimeStartAt"; label: "Начало интервала"; placeholder: "2026-11-17T18:00:00+03:00"; required: true
            hint: "Дата и время ISO-8601 с часовым поясом, например +03:00." }
        VField { id: overtimeEnd; objectName: "overtimeEndAt"; label: "Окончание интервала"; placeholder: "2026-11-17T20:00:00+03:00"; required: true
            hint: "Дата и время ISO-8601 с часовым поясом; срочную запись подают после окончания." }
        VField { id: overtimeMinutes; objectName: "overtimeMinutes"; label: "Фактические минуты"; placeholder: "120"; required: true
            hint: "Целые минуты без округления, не длиннее указанного интервала." }
        VSegmented { id: overtimeKind; objectName: "overtimeKind"; current: "overtime"; onPicked: id => current = id
            model: [{ id: "overtime", label: "Сверхурочно", icon: "clock-3", tip: "Работа сверх нормы в рабочий день." },
                    { id: "weekend", label: "Выходной", icon: "calendar-off", tip: "Работа в выходной по графику." },
                    { id: "holiday", label: "Федеральный праздник", icon: "calendar-star", tip: "Работа в федеральный нерабочий праздничный день." }] }
        VSegmented { id: overtimeMode; objectName: "overtimeMode"; current: "planned"; onPicked: id => current = id
            model: [{ id: "planned", label: "Заранее", icon: "calendar-check", tip: "Получить решение руководителя до начала интервала." },
                    { id: "urgent_after", label: "Срочно — после факта", icon: "alarm-clock", tip: "Только после окончания; причина обязательна, затем нужно решение руководителя." }] }
        VSelect { id: overtimeGround; objectName: "overtimeGround"; label: "Основание по ТК РФ"
            textRole: "label_ru"; valueRole: "code"
            model: overtimeForm.groundModel
            hint: "Справочник по статьям 99 и 113 ТК РФ; варианты зависят от вида работы."
            tip: "Основание фиксируется неизменяемо вместе с заявкой; свободный текст не принимается." }
        Text { visible: overtimeForm.groundSel !== null
               text: overtimeForm.groundSel ? overtimeForm.groundSel.article + " · " + overtimeForm.groundSel.consent_label : ""
               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Text { visible: (page.wv.own_hr_category_labels || []).length > 0
               text: "Ваши кадровые категории: " + (page.wv.own_hr_category_labels || []).join(", ")
               color: Theme.warning; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
        CheckBox { id: overtimeConsent; objectName: "overtimeConsent"; visible: overtimeForm.consentNeeded
                   text: "Письменное согласие работника получено" }
        CheckBox { id: overtimeNotice; objectName: "overtimeNotice"; visible: overtimeForm.noticeNeeded
                   text: "Право отказаться от привлечения разъяснено" }
        VField { id: overtimeReason; objectName: "overtimeReason"; label: "Основание и причина"; required: true
            placeholder: "Неотложный выпуск комплекта документации"; hint: "Причина сохраняется в истории решения." }
        Connections { target: backend
            function onCommandDone(tag, result) {
                if (tag !== "overtime-request-create") return
                overtimeForm.working = false
                if (result.status === "accepted") overtimeForm.close()
                else overtimeForm.error = result.message_ru || "Заявка не отправлена."
            }
        }
    }
    FormDialog {
        id: overtimeRejectForm
        objectName: "overtimeRejectForm"
        property var request: null
        title: "Отклонить заявку на часы"
        subtitle: request ? request.work_date + " · " + request.minutes + " мин · " + request.kind_label : ""
        confirmText: "Отклонить с причиной"
        confirmKind: "danger"
        canConfirm: request !== null && overtimeRejectReason.text.trim() !== ""
        function openFor(row) { request = row; overtimeRejectReason.text = ""; error = ""; working = false; open() }
        onConfirmed: {
            working = true
            backend.overtimeDecision("overtime-request-reject", request.id, "reject", overtimeRejectReason.text.trim(), request.version)
        }
        VField { id: overtimeRejectReason; objectName: "overtimeRejectReason"; label: "Причина отказа"; required: true
            placeholder: "Нужно перенести работы на плановую смену" }
        Connections { target: backend
            function onCommandDone(tag, result) {
                if (tag !== "overtime-request-reject") return
                overtimeRejectForm.working = false
                if (result.status === "accepted") overtimeRejectForm.close()
                else overtimeRejectForm.error = result.message_ru || "Решение не сохранено."
            }
        }
    }

    FormDialog {
        id: overtimeCloseForm
        objectName: "overtimeCloseForm"
        property var request: null
        property string supersedesId: ""
        title: supersedesId ? "Исправить закрытую ведомость" : "Закрыть ведомость часов"
        subtitle: request ? request.user_id + " · период по настройке компании, дата " + request.work_date : ""
        confirmText: supersedesId ? "Создать корректирующую версию" : "Закрыть период"
        confirmTip: "Создать неизменяемый снимок согласованных заявок. Исправление будет новой версией с причиной."
        canConfirm: request !== null && closeReason.text.trim() !== ""
        note: supersedesId ? "Будет создана новая версия, предыдущая останется в аудите. Здесь учитываются только одобренные заявки вне графика; это не полный табель." :
                            "Ожидающие решения заявки блокируют закрытие. Снимок содержит только одобренные заявки вне графика, без суммы оплаты."
        function openFor(row) {
            request = row; supersedesId = ""; error = ""; working = false; closeReason.text = ""
            var candidates = (page.wv.overtime_closures || []).filter(c => c.user_id === row.user_id
                && row.work_date >= c.period_start && row.work_date <= c.period_end)
            candidates.sort((a, b) => b.revision - a.revision)
            if (candidates.length) supersedesId = candidates[0].id
            open()
        }
        onConfirmed: {
            working = true
            var payload = { id: backend.newId(), user_id: request.user_id, anchor_date: request.work_date,
                            reason: closeReason.text.trim() }
            if (supersedesId) payload.supersedes_id = supersedesId
            backend.overtimeClose("overtime-close", payload)
        }
        VField { id: closeReason; objectName: "overtimeCloseReason"; label: "Основание закрытия / корректировки"; required: true
            placeholder: "Сверено с подписанным первичным документом" }
        Connections { target: backend
            function onCommandDone(tag, result) {
                if (tag !== "overtime-close") return
                overtimeCloseForm.working = false
                if (result.status === "accepted") overtimeCloseForm.close()
                else overtimeCloseForm.error = result.message_ru || "Ведомость не закрыта."
            }
        }
    }

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
        title: "Самопроверка и отправка"
        subtitle: sel ? sel.date_label + "  ·  смена " + sel.shift_label + "  ·  в строках " + sel.total_label + "  ·  смена без перерыва " + sel.net_label : ""
        confirmText: "Отправить"
        confirmTip: "Отправить отчёт руководителю отдела на проверку. Можно без связи: уйдёт при подключении."
        note: "Отметьте каждый пункт — без самопроверки отчёт не уходит. Отчёт уйдёт руководителю отдела (можно без связи — отправится при подключении). После отправки править можно только через «Исправить» с основанием."
        // Отметки самопроверки (A-48 п. 4): ключ → отмечено; «Отправить» — только когда отмечены все.
        property var marks: ({})
        readonly property bool allMarked: (page.wv.self_check_items || []).every(it => marks[it.id] === true)
        canConfirm: allMarked
        function openFor() { marks = ({}); error = ""; open() }
        onConfirmed: { working = true; backend.submitReport("report-submit", sel.id, marks) }
        Repeater {
            model: page.wv.self_check_items || []
            delegate: CheckBox { required property var modelData
                objectName: "selfCheck_" + modelData.id
                text: modelData.label; checked: submitForm.marks[modelData.id] === true
                onToggled: { var m = Object.assign({}, submitForm.marks); m[modelData.id] = checked; submitForm.marks = m } }
        }
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
        property string timerNote: ""    // «смена — по таймеру рабочего дня…» (D-46) для нового отчёта
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
            // Новый отчёт: смена — из таймера рабочего дня, если день им отмечен (D-46: без округления, перерыв —
            // только нажатые «Перерыв»); иначе — обычные 09:00–18:00, сотрудник поправит.
            var t = r ? ({ from_timer: false }) : backend.workdayReportDefaults(workDate)
            timerNote = t.from_timer ? t.note : ""
            startField.text = r && r.shift_start ? r.shift_start : (t.from_timer ? t.shift_start : "09:00")
            endField.text = r && r.shift_end ? r.shift_end : (t.from_timer ? t.shift_end : "18:00")
            breakField.text = r ? String(r.break_minutes) : (t.from_timer ? String(t.break_minutes) : "60")
            commentField.text = r ? r.comment : ""
            lm.clear()
            var ls = r ? r.lines : []
            for (var i = 0; i < ls.length; i++)
                // source / orig_* — откуда время строки, собранной программой (D-43): пока строка не изменена, отметка
                // сохраняется; правка делает строку «своей».
                lm.append({ project_id: ls[i].project_id || "", task_id: ls[i].task_id || "", hours: ls[i].hours_input, note: ls[i].note,
                            source: ls[i].source || "", source_label: ls[i].source_label || "", orig_project: ls[i].project_id || "",
                            orig_task: ls[i].task_id || "", orig_minutes: ls[i].minutes, orig_note: ls[i].note })
            if (lm.count === 0) lm.append(blank())
            editRev++
            open()
        }
        // Пустая строка таблицы (все роли ListModel заданы сразу — иначе новые свойства не появятся).
        function blank() { return { project_id: "", task_id: "", hours: "", note: "", source: "", source_label: "", orig_project: "", orig_task: "", orig_minutes: 0, orig_note: "" } }
        function collect() {
            var out = []
            for (var i = 0; i < lm.count; i++) { var r = lm.get(i)
                out.push({ project_id: r.project_id, task_id: r.task_id, hours: r.hours, note: r.note, source: r.source,
                           orig: { project_id: r.orig_project, task_id: r.orig_task, minutes: r.orig_minutes, note: r.orig_note } }) }
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
                    Text { text: "Часы в строке — как удобно: 2, 1,5 или 1:30. Проект — только тот, где вы участник; задача — необязательно. Конец смены раньше начала — ночная смена (до утра следующего дня). Сохранять можно без связи."
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
            Banner { objectName: "reportTimerNote"; tone: "info"; text: editor.timerNote
                     Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 10; Layout.fillWidth: true }
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
                        Text { visible: rowItem.model.source !== ""; leftPadding: 4; Layout.fillWidth: true; wrapMode: Text.Wrap
                               text: "Время строки: " + rowItem.model.source_label + " — если исправить строку, она станет вашей (без отметки)."
                               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs }
                        Text { visible: rowItem.problem !== ""; text: rowItem.problem; color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsXs; leftPadding: 4; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 8; spacing: 8
                VButton { objectName: "reportAddLine"; text: "Добавить строку"; iconName: "plus"; kind: "soft"; compact: true
                          onClicked: { lm.append(editor.blank()); editor.editRev++; lv.positionViewAtEnd() } }
                Item { Layout.fillWidth: true }
                Text { objectName: "reportEditorTotals"
                       text: (editor.check.overnight === true ? "Ночная смена  ·  " : "") + "В строках: " + ((editor.check.totals || {}).total_label || "—") + "  ·  смена без перерыва: " + ((editor.check.totals || {}).net_label || "—")
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
