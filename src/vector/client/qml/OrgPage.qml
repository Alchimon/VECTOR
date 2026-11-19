import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Раздел «Оргструктура» (этап 7, R-21; D-44 — ответ владельца на A-48 п. 1: «создать отделы, структуры, филиалы,
// связи между ними, дерево зависимостей»).
//
//  * Слева — дерево: филиалы, структуры (дирекции, управления, службы) и отделы с отступом по уровню; поиск — по
//    названию, коду, руководителю и сотрудникам (найденное показывается вместе с вышестоящими).
//  * Справа — карточка подразделения: вид, код, во что входит, путь, руководитель, сотрудники с должностями,
//    подчинённые подразделения, последние изменения.
//  * Администратор (право org.manage) при связи заводит и правит подразделения, переносит их в дереве (без кругов),
//    назначает руководителя, включает и переводит сотрудников, отправляет пустое подразделение в архив.
//  * Всем — строка «кто проверяет мои отчёты»: дневной отчёт уходит руководителю своего подразделения (или
//    вышестоящего, если своего нет).
//  * Графики работы (D-47, ответ на A-51 п. 1): справочник «Графики работы» (видят все; администратор заводит и
//    правит), график подразделения (действует на вложенные) и сотрудника лично, «учёт рабочего времени не ведётся».
//    От графика считаются фактическое начало дня и напоминание о конце дня.
// Данные — снимок pull (ov = backend.orgOverview); править — только при связи (решает сервер).
MasterDetail {
    id: page
    objectName: "orgPage"
    property var shell
    property string selectedId: ""
    readonly property var ov: backend.orgOverview
    readonly property var sel: selectedId !== "" && ov.cards ? (ov.cards[selectedId] || null) : null
    // Открыть подразделение (переход из уведомления «Вы — руководитель подразделения»).
    function openObject(id) { if (id) selectedId = id }
    Component.onCompleted: { if (shell && shell.navTarget.page === "org") openObject(shell.navTarget.id) }
    Connections { target: shell; ignoreUnknownSignals: true
                  function onNavTargetChanged() { if (shell.navTarget.page === "org" && shell.navTarget.id) page.openObject(shell.navTarget.id) } }
    onSearchChanged: backend.setOrgQuery(search)
    title: "Оргструктура"
    subtitle: (ov.mine || {}).text || ""
    hasSelection: sel !== null
    emptyIcon: "network"
    emptyTitle: "Оргструктура не заведена"
    emptyText: ov.manage === true ? "Заведите филиалы, структуры и отделы, назначьте руководителей и включите сотрудников."
                                  : "Её заводит администратор. Пока дневные отчёты видят все руководители отделов."
    emptyAction: ov.can_manage === true ? "Новое подразделение" : ""
    onEmptyActionTriggered: unitForm.openFor(null, "")
    noSelectionText: "Выберите подразделение"
    headerActions: [
        VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() },
        VButton { objectName: "btnSchedules"; text: "Графики работы"; iconName: "calendar-clock"; onClicked: schedulesPopup.open()
                  tip: "Графики работы компании: начало и конец рабочего дня, рабочие дни недели, перерыв. От графика считаются фактическое начало дня и напоминание о конце дня." },
        VButton { objectName: "btnNewUnit"; visible: page.ov.manage === true; enabled: page.ov.can_manage === true
                  text: "Новое подразделение"; kind: "primary"; iconName: "plus"; onClicked: unitForm.openFor(null, page.selectedId) }
    ]
    list.model: ov.rows || []
    // Строка дерева: отступ по уровню, значок вида, название, вид · код · руководитель, сотрудников, архив.
    list.delegate: Item {
        width: ListView.view ? ListView.view.width : 300
        height: 62
        ListRow {
            objectName: "orgRow"
            x: Math.min(modelData.depth, 6) * 18
            width: parent.width - x
            title: modelData.name
            subtitle: modelData.subtitle
            iconName: modelData.icon
            chipText: modelData.active ? (modelData.members > 0 ? String(modelData.members) : "") : "В архиве"
            chipTone: modelData.active ? "neutral" : "warning"
            selected: modelData.id === page.selectedId
            onClicked: page.selectedId = modelData.id
        }
    }

    // ---------- карточка подразделения
    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Rectangle { width: 52; height: 52; radius: 14; color: Theme.primarySoft
            Icon { anchors.centerIn: parent; name: sel ? sel.icon : "network"; size: 26; color: Theme.primary } }
        ColumnLayout { spacing: 4; Layout.fillWidth: true
            Text { text: sel ? sel.kind_label : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
            Text { objectName: "orgUnitTitle"; text: sel ? sel.name : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true } }
        VChip { text: sel && sel.active ? "Действует" : "В архиве"; tone: sel && sel.active ? "success" : "warning" }
    }
    Banner { visible: sel !== null && page.ov.manage === true && page.ov.online !== true; tone: "warning"
             text: "Править оргструктуру можно только при связи с сервером. Ниже — состояние на момент последнего обновления." }
    Banner { visible: sel !== null && sel.active === true && !sel.head_user_id; tone: "info"
             text: "Руководитель не назначен: дневные отчёты сотрудников уходят руководителю вышестоящего подразделения." }
    ColumnLayout {
        Layout.fillWidth: true; spacing: 8
        KeyValue { k: "Вид подразделения"; v: sel ? sel.kind_label : ""; kWidth: 170 }
        KeyValue { k: "Код"; v: sel ? sel.code : ""; kWidth: 170 }
        KeyValue { k: "Входит в"; v: sel ? sel.parent_name : ""; kWidth: 170 }
        KeyValue { objectName: "orgUnitPath"; k: "Путь"; v: sel ? sel.path : ""; kWidth: 170 }
        KeyValue { objectName: "orgUnitHead"; k: "Руководитель"; v: sel ? sel.head_name : ""; muted: sel !== null && !sel.head_user_id; kWidth: 170 }
        KeyValue { k: "Сотрудников"; v: sel ? String(sel.members_list.length) : ""; kWidth: 170 }
        KeyValue { objectName: "orgUnitSchedule"; k: "График работы"; v: sel ? sel.schedule_label + " — " + sel.schedule_source : ""; kWidth: 170
                   tip: "Действует на сотрудников подразделения и вложенных подразделений, если им не назначен свой график." }
    }
    Flow {
        Layout.fillWidth: true; spacing: 8
        visible: sel !== null && page.ov.manage === true
        VButton { objectName: "btnEditUnit"; visible: sel !== null && sel.active === true; enabled: page.ov.can_manage === true
                  text: "Изменить"; iconName: "pencil"; onClicked: unitForm.openFor(sel, "") }
        VButton { objectName: "btnAddMember"; visible: sel !== null && sel.active === true; enabled: page.ov.can_manage === true
                  text: "Включить сотрудника"; kind: "primary"; iconName: "user-plus"; onClicked: memberForm.openFor("", sel.id, "") }
        VButton { objectName: "btnUnitSchedule"; visible: sel !== null && sel.active === true && page.ov.schedule_manage === true; enabled: page.ov.can_schedule === true
                  text: "График подразделения"; iconName: "calendar-clock"; onClicked: assignForm.openFor("unit", sel.id, sel.name, sel.schedule_own_id) }
        VButton { objectName: "btnArchiveUnit"; visible: sel !== null && sel.active === true; enabled: page.ov.can_manage === true && sel !== null && sel.can_archive === true
                  text: "В архив"; iconName: "archive"; onClicked: archiveForm.openFor() }
        VButton { objectName: "btnRestoreUnit"; visible: sel !== null && sel.active === false; enabled: page.ov.can_manage === true
                  text: "Вернуть из архива"; iconName: "rotate-ccw"; onClicked: restoreCmd.run("org.unit_restore", { id: sel.id }, sel.version) }
    }
    Text { visible: sel !== null && page.ov.manage === true && sel.active === true && sel.can_archive !== true
           text: "В архив — только пустое подразделение: сначала переведите сотрудников и подчинённые подразделения."
           color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }

    SectionTitle { text: "Сотрудники"; iconName: "users" }
    Text { visible: sel !== null && sel.members_list.length === 0; text: "В подразделении пока никого нет."
           color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
    Repeater {
        model: sel ? sel.members_list : []
        delegate: RowLayout {
            objectName: "orgMemberRow"
            Layout.fillWidth: true; spacing: 10
            Icon { name: modelData.is_head ? "user-check" : "user"; size: 16; color: modelData.is_head ? Theme.primary : Theme.textMuted }
            ColumnLayout { spacing: 1; Layout.fillWidth: true
                Text { text: modelData.name; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                Text { visible: modelData.position !== ""; text: modelData.position; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; elide: Text.ElideRight; Layout.fillWidth: true }
                Text { objectName: "orgMemberSchedule"; text: "График: " + modelData.schedule; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; elide: Text.ElideRight; Layout.fillWidth: true } }
            VChip { visible: modelData.is_head; text: "Руководитель"; tone: "primary" }
            VButton { objectName: "btnMemberSchedule"; visible: page.ov.schedule_manage === true; enabled: page.ov.can_schedule === true; text: "График"; compact: true; kind: "ghost"
                      iconName: "calendar-clock"; onClicked: assignForm.openFor("user", modelData.user_id, modelData.name, modelData.schedule_own_id)
                      tip: "Назначить сотруднику свой график работы или отметить «учёт рабочего времени не ведётся» (служебная учётная запись). Только при связи." }
            VButton { visible: page.ov.manage === true; enabled: page.ov.can_manage === true; text: "Перевести"; compact: true; kind: "ghost"
                      iconName: "arrow-left-right"; onClicked: memberForm.openFor(modelData.user_id, sel.id, modelData.position) }
        }
    }

    SectionTitle { visible: sel !== null && sel.children.length > 0; text: "Подчинённые подразделения"; iconName: "network" }
    Repeater {
        model: sel ? sel.children : []
        delegate: ListRow {
            Layout.fillWidth: true
            title: modelData.name
            subtitle: modelData.kind_label
            iconName: "chevron-right"
            chipText: modelData.active ? "" : "В архиве"; chipTone: "warning"
            onClicked: page.selectedId = modelData.id
        }
    }

    SectionTitle { text: "Последние изменения"; iconName: "history" }
    Repeater {
        model: sel ? (page.ov.events || []).filter(e => e.object_id === sel.id || e.unit_id === sel.id).slice(0, 10) : []
        delegate: ColumnLayout {
            Layout.fillWidth: true; spacing: 1
            Text { text: modelData.label + (modelData.object !== sel.name ? ": " + modelData.object : ""); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
            Text { text: modelData.who + "  ·  " + modelData.when; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs }
        }
    }

    // ---------- новое подразделение / изменить
    FormDialog {
        id: unitForm
        objectName: "orgUnitForm"
        property var u: null
        property string savedId: ""        // id сохраняемого подразделения — выбрать его в дереве после успеха
        title: u ? "Изменить подразделение" : "Новое подразделение"
        subtitle: u ? u.path : "Филиал, структура (дирекция, управление, служба) или отдел. Укажите, во что оно входит, — так строится дерево."
        confirmText: "Сохранить"
        note: "Руководитель проверяет дневные отчёты сотрудников подразделения и подчинённых подразделений — отдельная роль для этого не нужна. Должность прав не даёт. Подразделение не может входить в само себя или в своё подчинённое."
        canConfirm: uName.text.trim() !== "" && uKind.currentValue !== ""
        function idx(list, id) { for (var i = 0; i < list.length; i++) if (list[i].id === id) return i; return 0 }
        function openFor(x, parentId) {
            u = x
            uKind.currentIndex = x ? idx(uKind.model, x.kind) : 2
            uName.text = x ? x.name : ""; uCode.text = x ? x.code : ""
            uParent.currentIndex = idx(uParent.model, x ? (x.parent_id || "") : (parentId || ""))
            uHead.currentIndex = idx(uHead.model, x ? (x.head_user_id || "") : "")
            open()
        }
        onConfirmed: {
            var p = { kind: uKind.currentValue, name: uName.text.trim(), code: uCode.text.trim(),
                      parent_id: uParent.currentValue, head_user_id: uHead.currentValue }
            p.id = u ? u.id : backend.newId()
            savedId = p.id
            if (u) uCmd.run("org.unit_update", p, u.version)
            else uCmd.run("org.unit_create", p)
        }
        VSelect { id: uKind; objectName: "uKind"; label: "Вид подразделения"; model: page.ov.kind_options || [] }
        VField { id: uName; objectName: "uName"; label: "Название"; required: true; placeholder: "Отдел АСУ ТП" }
        VField { id: uCode; objectName: "uCode"; label: "Код подразделения"; placeholder: "01.2 (не обязательно)" }
        VSelect { id: uParent; objectName: "uParent"; label: "Входит в"; model: page.ov.parent_options || [] }
        VSelect { id: uHead; objectName: "uHead"; label: "Руководитель"; model: page.ov.head_options || [] }
    }
    Cmd { id: uCmd; dialog: unitForm; tag: "org-unit"
          onDone: r => { if (r.status === "accepted") page.selectedId = unitForm.savedId } }

    // ---------- включить / перевести сотрудника
    FormDialog {
        id: memberForm
        objectName: "orgMemberForm"
        property string userId: ""
        title: userId ? "Перевести сотрудника" : "Включить сотрудника"
        subtitle: "Дневные отчёты сотрудника пойдут руководителю выбранного подразделения (или вышестоящего, если своего нет)."
        confirmText: "Сохранить"
        note: "Выберите «— вывести из оргструктуры —», чтобы убрать сотрудника из всех подразделений. Сотрудник получит уведомление."
        canConfirm: mUser.currentValue !== ""
        function idx(list, id) { for (var i = 0; i < list.length; i++) if (list[i].id === id) return i; return -1 }
        function openFor(user, unitId, position) {
            userId = user
            mUser.currentIndex = user ? idx(mUser.model, user) : -1
            mUnit.currentIndex = Math.max(0, idx(mUnit.model, unitId))
            mPos.text = position || ""
            open()
        }
        onConfirmed: mCmd.run("org.member_set", { user_id: mUser.currentValue, unit_id: mUnit.currentValue, position: mPos.text.trim() })
        VSelect { id: mUser; objectName: "mUser"; label: "Сотрудник"; model: page.ov.people_options || [] }
        VSelect { id: mUnit; objectName: "mUnit"; label: "Подразделение"
                  model: (page.ov.unit_options || []).concat([{ id: "", label: "— вывести из оргструктуры —" }]) }
        VField { id: mPos; objectName: "mPos"; label: "Должность"; placeholder: "Инженер-проектировщик (не обязательно)" }
    }
    Cmd { id: mCmd; dialog: memberForm; tag: "org-member" }

    // ---------- в архив
    FormDialog {
        id: archiveForm
        title: "Отправить подразделение в архив"
        subtitle: sel ? sel.path : ""
        confirmText: "Отправить в архив"; confirmKind: "danger"
        canConfirm: aReason.text.trim() !== ""
        function openFor() { aReason.text = ""; open() }
        onConfirmed: aCmd.run("org.unit_archive", { id: sel.id, reason: aReason.text.trim() }, sel.version)
        VField { id: aReason; label: "Причина"; required: true; placeholder: "Реорганизация, приказ № …" }
    }
    Cmd { id: aCmd; dialog: archiveForm; tag: "org-archive" }
    Cmd { id: restoreCmd; tag: "org-restore" }

    // ---------- графики работы (D-47): справочник
    Popup {
        id: schedulesPopup
        objectName: "schedulesPopup"
        modal: true; focus: true
        anchors.centerIn: Overlay.overlay
        width: Math.min(680, (Overlay.overlay ? Overlay.overlay.width : 800) - 48)
        // Высота — по содержимому, но не выше окна программы.
        height: Math.min(600, (Overlay.overlay ? Overlay.overlay.height : 700) - 48,
                         schHead.implicitHeight + schHint.implicitHeight + schList.contentHeight + 2 * Theme.padL + 40)
        padding: 0
        closePolicy: Popup.CloseOnEscape
        Overlay.modal: Rectangle { color: Theme.scrim }
        background: Rectangle { color: Theme.surface; radius: Theme.radiusL; border.color: Theme.border }
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout { id: schHead; Layout.fillWidth: true; Layout.margins: Theme.padL; Layout.bottomMargin: 0
                Text { text: "Графики работы"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsL; font.weight: Font.DemiBold; Layout.fillWidth: true }
                VButton { objectName: "btnNewSchedule"; visible: page.ov.schedule_manage === true; enabled: page.ov.can_schedule === true; text: "Новый график"; kind: "primary"; compact: true; iconName: "plus"
                          onClicked: scheduleForm.openFor(null) }
                VButton { kind: "ghost"; iconName: "x"; compact: true; onClicked: schedulesPopup.close(); Accessible.name: "Закрыть" } }
            Text { id: schHint; text: "Какой график действует для сотрудника: назначенный лично → подразделения (или вышестоящего) → «по умолчанию» в компании. Сейчас по умолчанию: " + (page.ov.default_schedule || "") + ". Время — по часам компьютера сотрудника."
                   color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL }
            ListView {
                id: schList
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.bottomMargin: Theme.padL
                clip: true; spacing: 8; model: page.ov.schedules || []
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle { id: sr; required property var modelData
                    objectName: "scheduleRow"
                    width: ListView.view.width; implicitHeight: src.implicitHeight + 16; radius: Theme.radius; color: Theme.surfaceAlt
                    RowLayout { id: src; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.margins: 10; spacing: 8
                        Icon { name: "calendar-clock"; size: 18; color: Theme.textMuted }
                        ColumnLayout { spacing: 1; Layout.fillWidth: true
                            Text { text: sr.modelData.name; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                            Text { text: sr.modelData.label + (sr.modelData.assigned > 0 ? "  ·  назначен: " + sr.modelData.assigned : ""); color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; elide: Text.ElideRight; Layout.fillWidth: true } }
                        VChip { visible: sr.modelData.is_default === true; text: "По умолчанию"; tone: "primary" }
                        VChip { visible: sr.modelData.active !== true; text: "В архиве"; tone: "warning" }
                        VButton { objectName: "btnEditSchedule"; visible: page.ov.schedule_manage === true; enabled: page.ov.can_schedule === true; text: "Изменить"; compact: true; kind: "ghost"; iconName: "pencil"
                                  onClicked: scheduleForm.openFor(sr.modelData) }
                    }
                }
            }
            Text { visible: (page.ov.schedules || []).length === 0; text: "Своих графиков пока нет — действует встроенный: 09:00–18:00, пн–пт, перерыв 60 мин."
                   color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.bottomMargin: Theme.padL }
        }
    }

    // ---------- новый график / изменить
    FormDialog {
        id: scheduleForm
        objectName: "scheduleForm"
        property var s: null
        readonly property var dayNames: ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
        title: s ? "Изменить график работы" : "Новый график работы"
        subtitle: "Начало и конец рабочего дня — по часам компьютера сотрудника. Конец раньше начала — ночной график (день заканчивается утром)."
        confirmText: "Сохранить"
        note: "От графика считаются: фактическое начало дня (не раньше начала по графику), напоминание в конце дня и автоматическое завершение забытого дня."
        canConfirm: sName.text.trim() !== "" && sStart.text.trim() !== "" && sEnd.text.trim() !== ""
        function openFor(x) {
            s = x
            sName.text = x ? x.name : ""; sStart.text = x ? x.start_time : "09:00"; sEnd.text = x ? x.end_time : "18:00"
            sBreak.text = x ? String(x.break_minutes) : "60"
            var wd = x ? x.weekdays : "1111100"
            for (var i = 0; i < 7; i++) dayRep.itemAt(i).checked = wd.charAt(i) === "1"
            sDefault.checked = x ? x.is_default === true : false
            sArchive.checked = x ? x.active !== true : false
            open()
        }
        onConfirmed: {
            var wd = ""
            for (var i = 0; i < 7; i++) wd += dayRep.itemAt(i).checked ? "1" : "0"
            var b = parseInt(sBreak.text, 10)
            var p = { id: s ? s.id : backend.newId(), name: sName.text.trim(), start_time: sStart.text.trim(), end_time: sEnd.text.trim(),
                      break_minutes: isNaN(b) ? -1 : b, weekdays: wd, is_default: sDefault.checked, active: !sArchive.checked }
            if (s) sCmd.run("schedule.save", p, s.version); else sCmd.run("schedule.save", p)
        }
        VField { id: sName; objectName: "sName"; label: "Название"; required: true; placeholder: "Пятидневка 8–17" }
        RowLayout { Layout.fillWidth: true; spacing: 10
            VField { id: sStart; objectName: "sStart"; label: "Начало (ЧЧ:ММ)"; required: true; placeholder: "08:00"; Layout.fillWidth: true }
            VField { id: sEnd; objectName: "sEnd"; label: "Конец (ЧЧ:ММ)"; required: true; placeholder: "17:00"; Layout.fillWidth: true }
            VField { id: sBreak; objectName: "sBreak"; label: "Перерыв, мин"; placeholder: "60"; Layout.fillWidth: true } }
        Text { text: "Рабочие дни недели"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
        RowLayout { Layout.fillWidth: true; spacing: 2
            Repeater { id: dayRep; model: 7
                CheckBox { objectName: "sDay" + index; text: scheduleForm.dayNames[index] } } }
        CheckBox { id: sDefault; objectName: "sDefault"; text: "По умолчанию в компании (для всех, кому не назначен другой)" }
        CheckBox { id: sArchive; objectName: "sArchive"; visible: scheduleForm.s !== null; text: "В архиве (больше не назначается; назначенным — график выше или «по умолчанию»)" }
    }
    Cmd { id: sCmd; dialog: scheduleForm; tag: "schedule-save" }

    // ---------- назначить график подразделению / сотруднику
    FormDialog {
        id: assignForm
        objectName: "scheduleAssignForm"
        property string kind: "unit"
        property string targetId: ""
        property string targetName: ""
        title: kind === "unit" ? "График подразделения" : "График сотрудника"
        subtitle: targetName
        confirmText: "Назначить"
        confirmTip: "Сохранить назначение графика работы (только при связи). Сотрудник получит уведомление о своём графике."
        note: kind === "unit" ? "График действует на сотрудников подразделения и вложенных подразделений, если им не назначен свой."
                              : "Свой график важнее графика подразделения. «Учёт рабочего времени не ведётся» — для служебных учётных записей: окно «Начните рабочий день» не показывается. Сотрудник получит уведомление."
        function idx(list, id) { for (var i = 0; i < list.length; i++) if (list[i].id === id) return i; return 0 }
        function openFor(k, id, name, current) {
            kind = k; targetId = id; targetName = name
            saSchedule.currentIndex = idx(saSchedule.model, current || "")
            open()
        }
        onConfirmed: {
            var v = saSchedule.currentValue
            asCmd.run("schedule.assign", { target_kind: kind, target_id: targetId,
                                           schedule_id: v === "__exempt__" ? "" : v, exempt: v === "__exempt__" })
        }
        VSelect { id: saSchedule; objectName: "saSchedule"; label: "График работы"
                  model: assignForm.kind === "unit" ? (page.ov.schedule_unit_options || []) : (page.ov.schedule_user_options || []) }
    }
    Cmd { id: asCmd; dialog: assignForm; tag: "schedule-assign" }
}
