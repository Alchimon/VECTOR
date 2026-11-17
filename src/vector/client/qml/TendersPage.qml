import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Раздел «Тендеры»: список (поиск по названию и заказчику) и карточка тендера с ТКП.
// Действия: создать тендер, перевести по статусам (для отмены/проигрыша — с причиной), создать/изменить/согласовать ТКП,
// «Начать РКД» — создать проект (только из «Выигран», один проект на тендер). Суммы ТКП вводятся строкой и проверяются
// backend.moneyProblem (не больше 4 знаков после запятой, без float).
MasterDetail {
    id: page
    property var shell
    property string selectedId: shell && shell.navTarget.page === "tenders" ? shell.navTarget.id : ""
    readonly property var items: backend.tenders.filter(t => search === "" || (t.title + " " + t.customer_name).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: backend.tenders.find(t => t.id === selectedId) || null
    readonly property var selTkp: sel ? backend.tkps.filter(k => k.tender_id === sel.id) : []
    title: "Тендеры и ТКП"
    subtitle: backend.tenders.filter(t => ["draft", "in_work", "bid_submitted"].indexOf(t.status) >= 0).length + " активных · " + backend.tenders.length + " всего"
    hasSelection: sel !== null
    emptyIcon: "gavel"; emptyTitle: "Тендеров пока нет"
    emptyText: "Тендер создаётся из карточки заказчика или кнопкой «Новый тендер»."
    noSelectionText: "Выберите тендер"

    headerActions: [
        VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() },
        VButton { text: "Новый тендер"; kind: "primary"; iconName: "plus"; visible: backend.hint("tender.create", backend.permRevision); enabled: backend.customers.length > 0; onClicked: newTender.open() }
    ]
    list.model: items
    list.delegate: ListRow {
        title: modelData.title
        subtitle: modelData.customer_name + (modelData.submission_deadline ? "  ·  до " + modelData.submission_deadline : "")
        iconName: "gavel"
        chipText: modelData.status_label; chipTone: Theme.tenderTone(modelData.status)
        pending: modelData.pending
        selected: modelData.id === page.selectedId
        onClicked: page.selectedId = modelData.id
    }

    // ---------- карточка тендера
    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Rectangle { width: 52; height: 52; radius: 14; color: Theme.tone(Theme.tenderTone(sel ? sel.status : "")).bg
            Icon { anchors.centerIn: parent; name: "gavel"; size: 26; color: Theme.tone(Theme.tenderTone(sel ? sel.status : "")).fg } }
        ColumnLayout { spacing: 4; Layout.fillWidth: true
            Text { text: sel ? sel.title : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true }
            RowLayout { spacing: 6
                VChip { text: sel ? sel.status_label : ""; tone: Theme.tenderTone(sel ? sel.status : "") }
                VChip { visible: sel && sel.pending; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                VChip { text: sel ? "версия " + (sel.version || "—") : ""; tone: "neutral" } } }
    }

    // Следующий шаг: явное начало РКД (проект НЕ создаётся автоматически при победе)
    Rectangle {
        visible: sel && sel.can_start_rkd
        Layout.fillWidth: true; implicitHeight: rk.implicitHeight + 36; radius: Theme.radiusL
        gradient: Gradient { orientation: Gradient.Horizontal
            GradientStop { position: 0; color: Theme.successSoft } GradientStop { position: 1; color: Theme.primarySoft } }
        RowLayout { id: rk; anchors.fill: parent; anchors.margins: 18; spacing: 14
            Icon { name: "play"; size: 22; color: Theme.success }
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { text: "Тендер выигран. Следующий шаг — начать разработку РКД"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM; font.weight: Font.DemiBold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { text: "Проект создаётся только этим явным действием и только один на тендер. Требуется связь с сервером."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
            VButton { text: "Начать РКД"; kind: "primary"; iconName: "play"; visible: backend.hint("project.start_rkd", backend.permRevision); onClicked: startRkd.open() } }
    }
    VCard {
        visible: sel && sel.project_id !== ""
        Layout.fillWidth: true; implicitHeight: 64
        RowLayout { anchors.fill: parent; anchors.margins: 14; spacing: 12
            Icon { name: "folder-kanban"; size: 20; color: Theme.primary }
            Text { text: "По тендеру открыт проект"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; Layout.fillWidth: true }
            VButton { text: "Открыть проект"; kind: "soft"; iconName: "chevron-right"; onClicked: page.shell.go("projects", sel.project_id) } }
    }

    VCard { Layout.fillWidth: true; implicitHeight: kv.implicitHeight + 32
        ColumnLayout { id: kv; anchors.fill: parent; anchors.margins: 16; spacing: 10
            KeyValue { k: "Заказчик"; v: sel ? sel.customer_name : "" }
            KeyValue { k: "Срок подачи"; v: sel ? (sel.submission_deadline || "") : "" }
            KeyValue { k: "Ответственный"; v: sel ? sel.owner_name : "" }
            KeyValue { k: "Основание статуса"; v: sel ? (sel.status_reason || "") : "" } } }

    RowLayout {
        visible: sel && sel.next.length > 0
        Layout.fillWidth: true; spacing: 8
        Text { text: "Сменить статус:"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
        Repeater {
            model: sel ? sel.next : []
            delegate: VButton {
                text: modelData.label; compact: true
                kind: modelData.id === "won" ? "soft" : (modelData.id === "lost" || modelData.id === "cancelled") ? "danger" : "secondary"
                enabled: !(sel && sel.pending)
                onClicked: { trans.to = modelData.id; trans.toLabel = modelData.label; trans.needReason = modelData.reason; trans.open() }
            }
        }
        Item { Layout.fillWidth: true }
    }

    // ---------- ТКП
    RowLayout { Layout.fillWidth: true; Layout.topMargin: 6
        SectionTitle { text: "ТКП (" + page.selTkp.length + ")"; iconName: "file-text" }
        VButton { text: "Новое ТКП"; kind: "soft"; compact: true; iconName: "plus"; visible: backend.hint("tkp.create", backend.permRevision); onClicked: { tkpForm.editId = ""; tkpForm.open() } } }
    Repeater {
        model: page.selTkp
        delegate: VCard {
            Layout.fillWidth: true; implicitHeight: tk.implicitHeight + 28
            ColumnLayout { id: tk; anchors.fill: parent; anchors.margins: 14; spacing: 8
                RowLayout { Layout.fillWidth: true; spacing: 8
                    Text { text: modelData.label + "  ·  ред. " + (modelData.revision || 1); color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; Layout.fillWidth: true }
                    VChip { visible: modelData.pending; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                    VChip { text: modelData.status_label; tone: Theme.tkpTone(modelData.status) } }
                Text { text: modelData.amount_label; color: modelData.amount === null || modelData.amount === undefined ? Theme.textFaint : Theme.text
                       font.family: Theme.family; font.pixelSize: Theme.fsL; font.weight: Font.Bold; font.features: { "tnum": 1 } }
                Text { visible: !!modelData.sent_on; text: "Отправка зарегистрирована: " + (modelData.sent_on || "") + (modelData.sent_channel ? ", " + modelData.sent_channel : "") + " — это не подтверждение получения."
                       color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                RowLayout { spacing: 6
                    VButton { visible: modelData.status === "draft"; text: "Изменить"; compact: true; iconName: "pencil"; enabled: modelData.version > 0
                              onClicked: { tkpForm.editId = modelData.id; tkpForm.editVersion = modelData.version; tkLabel.text = modelData.label; tkAmount.text = modelData.amount || ""; tkCur.text = modelData.currency || ""; tkNote.text = modelData.note || ""; tkpForm.open() } }
                    VButton { visible: modelData.status === "draft" && backend.hint("tkp.approve", backend.permRevision); text: "Согласовать"; compact: true; kind: "soft"; iconName: "check"; enabled: !modelData.pending
                              onClicked: { simple.cmdName = "tkp.approve"; simple.objId = modelData.id; simple.ver = modelData.version; simple.title = "Согласовать ТКП «" + modelData.label + "»";
                                           simple.subtitle = "Внутреннее согласование. Согласование своего ТКП запрещено. Согласование привязано к текущей редакции и вашему полномочию: при изменении ТКП или отзыве роли оно станет недействительным."; simple.needReason = false; simple.open() } }
                    VButton { visible: modelData.status === "approved"; text: "Зарегистрировать отправку"; compact: true; kind: "soft"; iconName: "send"
                              onClicked: { sent.objId = modelData.id; sent.ver = modelData.version; sent.open() } }
                    VButton { visible: modelData.status === "approved"; text: "Вернуть в черновик"; compact: true; iconName: "rotate-ccw"
                              onClicked: { simple.cmdName = "tkp.reopen"; simple.objId = modelData.id; simple.ver = modelData.version; simple.title = "Вернуть ТКП в черновик";
                                           simple.subtitle = "Согласование будет снято; понадобится повторное согласование."; simple.needReason = true; simple.open() } } }
            }
        }
    }
    Text { visible: page.selTkp.length === 0; text: "ТКП по тендеру ещё нет. Сумма может быть не указана — это не ноль."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
    HistoryList { objectType: "tender"; objectId: page.selectedId }

    // ---------- формы
    FormDialog {
        id: newTender
        title: "Новый тендер"
        canConfirm: ntTitle.text.trim() !== "" && ntCust.currentValue !== ""
        onOpened: { ntTitle.text = ""; ntDate.text = "" }
        onConfirmed: ntCmd.run("tender.create", { id: backend.newId(), customer_id: ntCust.currentValue, title: ntTitle.text.trim(), submission_deadline: ntDate.text.trim() || null })
        VSelect { id: ntCust; label: "Заказчик"; model: backend.customers; textRole: "name" }
        VField { id: ntTitle; label: "Предмет тендера"; required: true }
        VField { id: ntDate; label: "Срок подачи заявки"; placeholder: "ГГГГ-ММ-ДД"; error: text !== "" && !/^\d{4}-\d{2}-\d{2}$/.test(text.trim()) ? "Формат даты: ГГГГ-ММ-ДД." : "" }
    }
    Cmd { id: ntCmd; dialog: newTender; onDone: r => { if (r.status === "accepted" && r.result.tender) page.selectedId = r.result.tender.id } }

    FormDialog {
        id: trans
        property string to: ""
        property string toLabel: ""
        property bool needReason: false
        title: "Статус: «" + toLabel + "»"
        subtitle: "Смена статуса проверяется сервером (права, текущая версия) и требует связи."
        confirmKind: (to === "lost" || to === "cancelled") ? "danger" : "primary"
        confirmText: "Сменить статус"
        canConfirm: !needReason || reason.text.trim() !== ""
        onOpened: reason.text = ""
        onConfirmed: trCmd.run("tender.transition", { id: sel.id, to: to, reason: reason.text.trim() || null }, sel.version)
        VField { id: reason; label: trans.needReason ? "Основание (обязательно)" : "Комментарий"; required: trans.needReason }
    }
    Cmd { id: trCmd; dialog: trans }

    FormDialog {
        id: startRkd
        title: "Начать разработку РКД"
        subtitle: sel ? "Будет создан проект по тендеру «" + sel.title + "». ГИП проекта назначает администратор." : ""
        confirmText: "Создать проект"
        canConfirm: pCode.text.trim() !== "" && pTitle.text.trim() !== ""
        onOpened: { pCode.text = ""; pTitle.text = sel ? sel.title : "" }
        onConfirmed: rkCmd.run("project.start_rkd", { id: backend.newId(), tender_id: sel.id, code: pCode.text.trim(), title: pTitle.text.trim() })
        VField { id: pCode; label: "Шифр проекта"; required: true; placeholder: "П-2026-014"; hint: "Уникален в пределах компании." }
        VField { id: pTitle; label: "Наименование проекта"; required: true }
    }
    Cmd { id: rkCmd; dialog: startRkd; onDone: r => { if (r.status === "accepted" && r.result.project) page.shell.go("projects", r.result.project.id) } }

    FormDialog {
        id: tkpForm
        property string editId: ""
        property int editVersion: 0
        title: editId ? "Изменить черновик ТКП" : "Новое ТКП"
        subtitle: "Сумма — десятичное число, не более 4 знаков после запятой. Пустая сумма означает «не указана», а не ноль."
        canConfirm: tkLabel.text.trim() !== "" && tkAmount.error === "" && (tkAmount.text.trim() === "" || tkCur.text.trim() !== "")
        onOpened: if (!editId) { tkLabel.text = ""; tkAmount.text = ""; tkCur.text = ""; tkNote.text = "" }
        onConfirmed: {
            var amt = backend.normalizeMoney(tkAmount.text)
            var p = { label: tkLabel.text.trim(), amount: amt === "" ? null : amt, currency: tkCur.text.trim().toUpperCase() || null, note: tkNote.text.trim() || null }
            if (editId) { p.id = editId; tkCmd.run("tkp.update", p, editVersion) }
            else { p.id = backend.newId(); p.tender_id = sel.id; tkCmd.run("tkp.create", p) }
        }
        VField { id: tkLabel; label: "Обозначение"; required: true; placeholder: "ТКП-15/2026" }
        RowLayout { Layout.fillWidth: true; spacing: 12
            VField { id: tkAmount; label: "Сумма"; placeholder: "1 500 000,00"; error: backend.moneyProblem(text); Layout.fillWidth: true }
            VField { id: tkCur; label: "Валюта"; placeholder: "RUB"; Layout.preferredWidth: 110; error: tkAmount.text.trim() !== "" && text.trim() === "" ? "Укажите валюту" : "" } }
        VField { id: tkNote; label: "Примечание" }
    }
    Cmd { id: tkCmd; dialog: tkpForm }

    FormDialog {
        id: simple
        property string cmdName: ""
        property string objId: ""
        property int ver: 0
        property bool needReason: false
        confirmText: "Подтвердить"
        canConfirm: !needReason || sReason.text.trim() !== ""
        onOpened: sReason.text = ""
        onConfirmed: sCmd.run(cmdName, needReason ? { id: objId, reason: sReason.text.trim() } : { id: objId, comment: sReason.text.trim() || null }, ver)
        VField { id: sReason; label: simple.needReason ? "Основание (обязательно)" : "Комментарий" }
    }
    Cmd { id: sCmd; dialog: simple }

    FormDialog {
        id: sent
        property string objId: ""
        property int ver: 0
        title: "Зарегистрировать отправку ТКП"
        subtitle: "Фиксирует факт отправки заказчику. Это не доказательство получения."
        canConfirm: /^\d{4}-\d{2}-\d{2}$/.test(sDate.text.trim())
        onOpened: { sDate.text = new Date().toISOString().substring(0, 10); sCh.text = "" }
        onConfirmed: snCmd.run("tkp.record_sent", { id: objId, sent_on: sDate.text.trim(), channel: sCh.text.trim() || null }, ver)
        VField { id: sDate; label: "Дата отправки"; placeholder: "ГГГГ-ММ-ДД"; required: true }
        VField { id: sCh; label: "Канал"; placeholder: "e-mail, ЭДО, площадка…" }
    }
    Cmd { id: snCmd; dialog: sent }
}
