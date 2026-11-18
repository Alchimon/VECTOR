import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Раздел «Качество» (этап 6, сценарий С5 шаг 10; D-39). Три вкладки:
//
//  * Рекламации и CAPA (корректирующие и предупреждающие действия). Рекламация — по конкретному экземпляру изделия
//    (заводской номер). Путь: зарегистрирована → анализ причины (ответственный) → действия (план: причина и хотя бы
//    одно корректирующее) → проверка эффективности → закрыта. Проверяет эффективность ДРУГОЙ человек — не тот, кто
//    составлял план, выполнял действия или отправлял на проверку; «не эффективно» возвращает к действиям.
//  * Запросы на изменение (ECR): подан → оценён (РП или ГИП) → принят или отклонён (ГИП проекта, не автор).
//  * Извещения об изменении (ECO): по принятому запросу, с ТОЧНЫМИ затронутыми версиями документов и редакциями
//    техпроцесса → утверждено (ГИП, не составитель и не правивший) → внедрено (на какие версии заменены) →
//    проверено (не тот, кто внедрял). Видно, каких экземпляров касается изменение.
//
// D-40 (ответ владельца на A-46): рекламация бывает и без заводского номера — на партию (наряд) или на проект; у неё
// есть тяжесть и категория дефекта, срок ответа заказчику (видно «срок подходит / прошёл») и ответы заказчику.
// ОТК (отдел технического контроля) ведёт рекламации и проверяет эффективность наравне с РП и ГИП.
//
// Без связи можно зарегистрировать рекламацию, подать запрос и править черновик извещения (очередь); решения,
// отметки и проверки — только при связи. Кнопки — подсказка по ролям; решает сервер, отказ приходит понятным текстом.
MasterDetail {
    id: page
    objectName: "qualityPage"
    property var shell
    property string selectedId: ""
    readonly property var qv: backend.qualityOverview
    readonly property var all: tab === "claims" ? (qv.claims || []) : tab === "ecrs" ? (qv.ecrs || []) : (qv.ecos || [])
    readonly property var items: all.filter(i => search === "" || (i.label + " " + i.title + " " + i.project_code + " " + (i.instance_label || "")).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: all.find(i => i.id === selectedId) || null
    readonly property var team: sel && qv.teams ? (qv.teams[sel.project_id] || []) : []
    function today() { var d = new Date(); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0") }
    // Открыть объект любого типа (переход из уведомления): выбрать вкладку, где он есть.
    function openObject(id) {
        if (!id) return
        if ((qv.claims || []).some(x => x.id === id)) tab = "claims"
        else if ((qv.ecrs || []).some(x => x.id === id)) tab = "ecrs"
        else if ((qv.ecos || []).some(x => x.id === id)) tab = "ecos"
        selectedId = id
    }
    Component.onCompleted: if (shell && shell.navTarget.page === "quality") openObject(shell.navTarget.id)
    // Переход к объекту, когда раздел уже открыт (например, ОТК начинает с «Качества» и открывает уведомление о сроке).
    Connections { target: shell; ignoreUnknownSignals: true
                  function onNavTargetChanged() { if (shell.navTarget.page === "quality" && shell.navTarget.id) page.openObject(shell.navTarget.id) } }
    tab: "claims"
    tabsName: "qualityTabs"
    tabs: [{ id: "claims", label: "Рекламации и CAPA", icon: "shield-x", tip: "Рекламации по экземплярам изделий и корректирующие и предупреждающие действия (CAPA)." },
           { id: "ecrs", label: "Запросы на изменение", icon: "file-pen-line", tip: "Запросы на изменение (ECR): что и почему предлагается изменить; решает ГИП проекта." },
           { id: "ecos", label: "Извещения об изменении", icon: "file-check", tip: "Извещения об изменении (ECO): точные версии, которые меняются, утверждение, внедрение и проверка." }]
    onTabPicked: id => { tab = id; selectedId = "" }
    title: "Качество"
    subtitle: "Открытых рекламаций: " + ((qv.counts || {}).claims_open || 0) + "  ·  запросов на решении: " + ((qv.counts || {}).ecrs_open || 0)
              + "  ·  извещений в работе: " + ((qv.counts || {}).ecos_open || 0) + "  ·  " + (qv.as_of || "")
    hasSelection: sel !== null
    emptyIcon: tab === "claims" ? "shield-x" : tab === "ecrs" ? "file-pen-line" : "file-check"
    emptyTitle: tab === "claims" ? "Рекламаций нет" : tab === "ecrs" ? "Запросов на изменение нет" : "Извещений нет"
    emptyText: tab === "claims" ? (qv.may_claim ? "Рекламация регистрируется по экземпляру изделия, по партии (наряду) или по проекту, если номера нет." : "Рекламации регистрируют РП и ГИП проекта, ОТК (отдел технического контроля) и руководитель отдела.")
             : tab === "ecrs" ? "Запрос на изменение может подать любой участник проекта — например, по итогам анализа рекламации."
             : "Извещение составляется по принятому запросу на изменение."
    emptyAction: tab === "claims" && qv.may_claim === true ? "Зарегистрировать рекламацию" : tab === "ecrs" && qv.may_ecr === true ? "Подать запрос" : ""
    onEmptyActionTriggered: tab === "claims" ? claimForm.openFor() : ecrForm.openFor(null)
    noSelectionText: tab === "claims" ? "Выберите рекламацию" : tab === "ecrs" ? "Выберите запрос на изменение" : "Выберите извещение"
    headerActions: [
        VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() },
        VButton { objectName: "btnClaimNew"; visible: page.tab === "claims" && page.qv.may_claim === true; text: "Зарегистрировать рекламацию"; kind: "primary"; iconName: "plus"
                  tip: "Рекламация по экземпляру изделия, по партии (наряду) или по проекту без номера изделия. Можно и без связи — уйдёт с очередью."; onClicked: claimForm.openFor() },
        VButton { objectName: "btnEcrNew"; visible: page.tab === "ecrs" && page.qv.may_ecr === true; text: "Подать запрос"; kind: "primary"; iconName: "plus"
                  tip: "Запрос на изменение (ECR): что и почему изменить. Можно и без связи."; onClicked: ecrForm.openFor(null) }
    ]
    list.model: items
    list.delegate: ListRow {
        title: modelData.label + (modelData.project_code ? "  ·  " + modelData.project_code : "")
        subtitle: page.tab === "claims" ? (modelData.deadline_alert === true ? modelData.deadline_short + "  ·  " : "") + modelData.instance_label + "  ·  " + modelData.title : modelData.title
        iconName: page.tab === "claims" ? "shield-x" : page.tab === "ecrs" ? "file-pen-line" : "file-check"
        chipText: modelData.status_label; chipTone: modelData.deadline === "overdue" ? "danger" : modelData.status_tone
        pending: modelData.pending === true
        selected: modelData.id === page.selectedId
        onClicked: page.selectedId = modelData.id
    }

    // ---------- общая шапка карточки
    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Rectangle { width: 52; height: 52; radius: 14; color: Theme.primarySoft
            Icon { anchors.centerIn: parent; name: page.tab === "claims" ? "shield-x" : page.tab === "ecrs" ? "file-pen-line" : "file-check"; size: 26; color: Theme.primary } }
        ColumnLayout { spacing: 4; Layout.fillWidth: true
            Text { text: sel ? sel.project_code + (sel.instance_label ? "  ·  " + sel.instance_label : "") + (sel.ecr_label ? "  ·  по " + sel.ecr_label.toLowerCase().replace("запрос", "запросу") : "") : ""
                   color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { objectName: "qualityTitle"; text: sel ? sel.label + (sel.title && page.tab !== "claims" ? " — " + sel.title : "") : ""
                   color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true } }
        VChip { visible: sel !== null && sel.pending === true; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
        VChip { objectName: "qualityStatusChip"; text: sel ? sel.status_label : ""; tone: sel ? sel.status_tone : "neutral" }
    }
    Banner { visible: sel !== null && sel.next !== ""; tone: "info"; text: sel ? sel.next : "" }
    Banner { visible: sel !== null && page.qv.online !== true; tone: "warning"
             text: "Нет связи с сервером: решения, отметки и проверки недоступны. Зарегистрировать рекламацию, подать запрос и править черновик извещения можно — уйдут с очередью." }

    // ==================================================== рекламация
    ColumnLayout {
        visible: page.tab === "claims" && sel !== null
        Layout.fillWidth: true; spacing: 12
        Flow {
            Layout.fillWidth: true; spacing: 8
            VButton { objectName: "btnClaimStart"; visible: sel !== null && sel.can_start === true; text: "Начать анализ"; kind: "primary"; iconName: "search"
                      tip: "Назначить ответственного за анализ причины (участник проекта)."; onClicked: startForm.openFor() }
            VButton { objectName: "btnClaimPlan"; visible: sel !== null && sel.can_plan === true; text: sel && sel.first_plan ? "Причина и план CAPA" : "Добавить действия"
                      kind: sel && sel.first_plan ? "primary" : "soft"; iconName: "list-checks"
                      tip: "CAPA — корректирующие и предупреждающие действия: корректирующее устраняет причину, предупреждающее не даёт ей повториться."
                      onClicked: planForm.openFor() }
            VButton { objectName: "btnClaimSubmit"; visible: sel !== null && sel.status === "action" && sel.manage === true; enabled: sel !== null && sel.can_submit === true
                      text: "На проверку эффективности"; kind: "primary"; iconName: "send"
                      tip: "Когда все действия выполнены — передать на проверку эффективности другому человеку."
                      onClicked: subCmd.run("claim.submit_check", { id: sel.id }, sel.version) }
            VButton { objectName: "btnClaimCheck"; visible: sel !== null && sel.may_check === true; enabled: sel !== null && sel.can_check === true
                      text: "Проверить эффективность"; kind: "primary"; iconName: "clipboard-check"
                      tip: "Проверка эффективности: устранена ли причина. Проверяет человек, не участвовавший в плане и действиях."
                      onClicked: checkForm.openFor() }
            VButton { objectName: "btnClaimEcr"; visible: sel !== null && sel.can_ecr === true && sel.status !== "registered"; text: "Запрос на изменение"; kind: "soft"; iconName: "file-pen-line"
                      tip: "Если причина — в документации или техпроцессе: подать запрос на изменение (ECR) по этой рекламации."
                      onClicked: ecrForm.openFor(sel) }
        }
        Banner { objectName: "claimCheckBlock"; visible: sel !== null && (sel.check_block || "") !== ""; tone: "warning"; text: sel ? sel.check_block || "" : "" }
        Banner { visible: sel !== null && (sel.submit_block || "") !== "" && sel.manage === true; tone: "info"; text: sel ? sel.submit_block || "" : "" }
        Cmd { id: subCmd; tag: "claim-submit" }   // итог (успех или причина отказа) показывает всплывающее уведомление

        Banner { objectName: "claimDeadlineBanner"; visible: sel !== null && page.tab === "claims" && sel.deadline_alert === true
                 tone: sel && sel.deadline === "overdue" ? "danger" : "warning"
                 text: sel && sel.deadline_alert === true ? "Ответ заказчику: " + String(sel.deadline_label || "").toLowerCase() + ". Запишите ответ или согласуйте с заказчиком новый срок." : "" }
        Flow {
            Layout.fillWidth: true; spacing: 8
            VButton { objectName: "btnClaimRespond"; visible: sel !== null && sel.can_respond === true; text: "Ответ заказчику"; kind: sel && sel.deadline_alert ? "primary" : "soft"; iconName: "mail-check"
                      tip: "Записать отправленный заказчику ответ (промежуточный или окончательный). Ответ после установки срока закрывает срок."
                      onClicked: respondForm.openFor() }
            VButton { objectName: "btnClaimDue"; visible: sel !== null && sel.can_set_due === true; text: "Изменить срок ответа"; kind: "soft"; iconName: "calendar-clock"
                      tip: "Новый срок ответа заказчику — по согласованию с ним или для следующего (окончательного) ответа. Причина обязательна."
                      onClicked: dueForm.openFor() }
            VButton { objectName: "btnClaimClassify"; visible: sel !== null && sel.can_classify === true; text: "Классификация дефекта"; kind: "soft"; iconName: "tag"
                      tip: "Уточнить тяжесть и категорию дефекта. Причина изменения попадёт в историю."
                      onClicked: classifyForm.openFor() }
        }
        SectionTitle { text: "Рекламация"; iconName: "shield-x" }
        KeyValue { kWidth: 170; k: "Охват"; v: sel ? sel.scope_label || "" : "" }
        KeyValue { kWidth: 170; k: sel && sel.scope === "batch" ? "Партия" : sel && sel.scope === "project" ? "Проект" : "Экземпляр"; v: sel ? sel.instance_label || "—" : "" }
        KeyValue { kWidth: 170; visible: sel !== null && sel.scope === "batch"; k: "Экземпляры партии"
                   v: sel && (sel.batch_instances || []).length ? sel.batch_instances.map(i => i.label).join(", ") : "номера не зарегистрированы" }
        RowLayout { Layout.fillWidth: true; spacing: 12
            Text { text: "Тяжесть дефекта"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; Layout.preferredWidth: 170
                   VTipArea { text: "Насколько дефект опасен для людей, оборудования и работы изделия: критический, значительный, незначительный." } }
            VChip { objectName: "claimSeverityChip"; text: sel ? sel.severity_label || "не указана" : ""; tone: sel ? sel.severity_tone || "neutral" : "neutral"; iconName: sel ? sel.severity_icon || "" : "" }
            Item { Layout.fillWidth: true } }
        KeyValue { kWidth: 170; k: "Категория дефекта"; v: sel ? sel.category_label || "пока не указана" : ""; muted: sel !== null && !sel.category_label
                   tip: "Откуда дефект: документация, изготовление, покупные комплектующие, программа, транспортировка, эксплуатация. Обычно ясна после анализа причины." }
        RowLayout { visible: sel !== null && sel.customer === true; Layout.fillWidth: true; spacing: 12
            Text { text: "Срок ответа заказчику"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; Layout.preferredWidth: 170; wrapMode: Text.Wrap
                   VTipArea { text: "До какой даты заказчику нужно ответить. «Ответ дан» — после установки срока записан ответ заказчику." } }
            VChip { objectName: "claimDeadlineChip"; text: sel ? sel.deadline_label || "Срок не задан" : ""; tone: sel ? sel.deadline_tone || "neutral" : "neutral"; iconName: "calendar-clock" }
            Item { Layout.fillWidth: true } }
        KeyValue { kWidth: 170; k: "Получена"; v: sel ? sel.received_label + "  ·  " + sel.source_label : "" }
        KeyValue { kWidth: 170; visible: sel !== null && sel.reference !== ""; k: "Документ заказчика"; v: sel ? sel.reference || "" : "" }
        KeyValue { kWidth: 170; k: "Ответственный"; v: sel && sel.responsible ? sel.responsible : "ещё не назначен" }
        Text { text: "Суть рекламации"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Text { text: sel ? sel.description || "" : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Text { visible: sel !== null && sel.cause !== ""; text: "Найденная причина"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Text { objectName: "claimCause"; visible: sel !== null && sel.cause !== ""; text: sel ? sel.cause || "" : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }

        SectionTitle { text: "Действия CAPA"; iconName: "list-checks" }
        Text { visible: sel !== null && (sel.actions || []).length === 0; text: "Действий ещё нет — они появятся после анализа причины."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Repeater {
            model: sel && page.tab === "claims" ? sel.actions : []
            delegate: RowLayout { id: arow; required property var modelData
                objectName: "claimActionRow"
                Layout.fillWidth: true; spacing: 10
                Icon { name: arow.modelData.icon; size: 16; color: arow.modelData.done ? Theme.success : Theme.textMuted; Layout.alignment: Qt.AlignTop }
                ColumnLayout { spacing: 2; Layout.fillWidth: true
                    Text { text: arow.modelData.kind_label + ": " + arow.modelData.description; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Text { text: "Исполнитель: " + arow.modelData.owner + "  ·  срок " + arow.modelData.due_label + (arow.modelData.done ? "  ·  выполнил " + arow.modelData.done_label : "")
                           color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Text { visible: (arow.modelData.result || "") !== ""; text: "Результат: " + (arow.modelData.result || ""); color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true } }
                VChip { visible: arow.modelData.done; text: "Выполнено"; tone: "success" }
                VButton { objectName: "btnActionDone"; visible: arow.modelData.can_done === true; text: "Выполнено"; kind: "soft"; compact: true; iconName: "check"
                          tip: "Отметить действие выполненным и записать результат."; onClicked: doneForm.openFor(arow.modelData) }
            }
        }

        SectionTitle { text: "Проверки эффективности"; iconName: "clipboard-check" }
        Text { visible: sel !== null && (sel.checks || []).length === 0; text: "Проверок ещё не было."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Repeater {
            model: sel && page.tab === "claims" ? sel.checks : []
            delegate: RowLayout { id: crow; required property var modelData
                objectName: "claimCheckRow"
                Layout.fillWidth: true; spacing: 10
                VChip { text: crow.modelData.label; tone: crow.modelData.tone; Layout.alignment: Qt.AlignTop }
                ColumnLayout { spacing: 2; Layout.fillWidth: true
                    Text { text: crow.modelData.evidence; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Text { text: crow.modelData.who + "  ·  " + crow.modelData.when; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs } } }
        }
        Text { visible: sel !== null && (sel.closure_note || "") !== ""; text: sel ? sel.closure_note || "" : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }

        SectionTitle { visible: sel !== null && sel.customer === true; text: "Ответы заказчику"; iconName: "mail" }
        Text { visible: sel !== null && sel.customer === true && (sel.responses || []).length === 0; text: "Ответов ещё не было."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Repeater {
            model: sel && page.tab === "claims" ? sel.responses || [] : []
            delegate: RowLayout { id: rrow; required property var modelData
                objectName: "claimResponseRow"
                Layout.fillWidth: true; spacing: 10
                VChip { text: rrow.modelData.kind_label; tone: rrow.modelData.kind === "final" ? "success" : "info"; Layout.alignment: Qt.AlignTop }
                ColumnLayout { spacing: 2; Layout.fillWidth: true
                    Text { text: rrow.modelData.summary; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Text { text: "Отправлен " + rrow.modelData.date_label + (rrow.modelData.reference ? "  ·  " + rrow.modelData.reference : "") + "  ·  записал " + rrow.modelData.who
                           color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true } } }
        }

        SectionTitle { visible: sel !== null && (sel.change_requests || []).length > 0; text: "Запросы на изменение по рекламации"; iconName: "file-pen-line" }
        Repeater {
            model: sel && page.tab === "claims" ? sel.change_requests : []
            delegate: RowLayout { id: lrow; required property var modelData
                Layout.fillWidth: true; spacing: 8
                Text { text: lrow.modelData.label + "  ·  " + lrow.modelData.status_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                VButton { text: "Открыть"; kind: "ghost"; compact: true; iconName: "chevron-right"; onClicked: page.openObject(lrow.modelData.id) } }
        }
    }

    // ==================================================== запрос на изменение
    ColumnLayout {
        visible: page.tab === "ecrs" && sel !== null
        Layout.fillWidth: true; spacing: 12
        Flow {
            Layout.fillWidth: true; spacing: 8
            VButton { objectName: "btnEcrAssess"; visible: sel !== null && sel.can_assess === true; text: "Оценить влияние"; kind: "primary"; iconName: "sliders-horizontal"
                      tip: "Что затронет изменение: документы, техпроцесс, изготовленные экземпляры, сроки, затраты."; onClicked: assessForm.openFor() }
            VButton { objectName: "btnEcrDecide"; visible: sel !== null && sel.may_decide === true; enabled: sel !== null && sel.can_decide === true
                      text: "Решение"; kind: "primary"; iconName: "gavel"; tip: "Принять или отклонить запрос (ГИП проекта, не автор запроса)."; onClicked: decideForm.openFor() }
            VButton { objectName: "btnEcoCreate"; visible: sel !== null && sel.can_eco === true; text: "Составить извещение"; kind: "primary"; iconName: "file-check"
                      tip: "Извещение об изменении (ECO) с точными версиями документов и редакциями техпроцесса, которые меняются."
                      onClicked: ecoEditor.openFor(null, sel) }
            VButton { visible: sel !== null && (sel.eco_id || "") !== ""; text: "Открыть извещение"; kind: "soft"; iconName: "chevron-right"; onClicked: page.openObject(sel.eco_id) }
            VButton { visible: sel !== null && (sel.claim_id || "") !== ""; text: "Открыть рекламацию"; kind: "soft"; iconName: "chevron-right"; onClicked: page.openObject(sel.claim_id) }
        }
        Banner { objectName: "ecrDecideBlock"; visible: sel !== null && (sel.decide_block || "") !== ""; tone: "warning"; text: sel ? sel.decide_block || "" : "" }
        SectionTitle { text: "Запрос"; iconName: "file-pen-line" }
        KeyValue { kWidth: 170; k: "Автор"; v: sel ? sel.author || "—" : "" }
        KeyValue { kWidth: 170; visible: sel !== null && (sel.kit_code || "") !== ""; k: "Изделие"; v: sel ? sel.kit_code || "" : "" }
        KeyValue { kWidth: 170; visible: sel !== null && (sel.claim_label || "") !== ""; k: "По рекламации"; v: sel ? sel.claim_label || "" : "" }
        KeyValue { kWidth: 170; visible: sel !== null && (sel.eco_label || "") !== ""; k: "Извещение"; v: sel ? sel.eco_label || "" : "" }
        Text { text: "Причина изменения"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Text { text: sel ? sel.reason || "" : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Text { text: "Что предлагается изменить"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Text { text: sel ? sel.proposal || "" : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Text { visible: sel !== null && (sel.impact || "") !== ""; text: "Оценка влияния  ·  " + (sel ? sel.assessed_label || "" : ""); color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Text { objectName: "ecrImpact"; visible: sel !== null && (sel.impact || "") !== ""; text: sel ? sel.impact || "" : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Text { visible: sel !== null && (sel.decision_reason || "") !== ""; text: "Обоснование решения  ·  " + (sel ? sel.decided_label || "" : ""); color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Text { visible: sel !== null && (sel.decision_reason || "") !== ""; text: sel ? sel.decision_reason || "" : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
    }

    // ==================================================== извещение об изменении
    ColumnLayout {
        visible: page.tab === "ecos" && sel !== null
        Layout.fillWidth: true; spacing: 12
        Flow {
            Layout.fillWidth: true; spacing: 8
            VButton { objectName: "btnEcoEdit"; visible: sel !== null && sel.can_edit === true; text: "Править извещение"; kind: "soft"; iconName: "pencil"
                      tip: "Черновик можно править и без связи — уйдёт с очередью."; onClicked: ecoEditor.openFor(sel, null) }
            VButton { objectName: "btnEcoApprove"; visible: sel !== null && sel.may_approve === true; enabled: sel !== null && sel.can_approve === true
                      text: "Утвердить"; kind: "primary"; iconName: "circle-check"; tip: "Утверждает ГИП проекта — не тот, кто составлял или правил извещение («четыре глаза»)."
                      onClicked: apprCmd.run("eco.approve", { id: sel.id }, sel.version) }
            VButton { objectName: "btnEcoImplement"; visible: sel !== null && sel.can_implement === true; text: "Отметить внедрение"; kind: "primary"; iconName: "check-check"
                      tip: "Указать, на какие версии документов и редакции техпроцесса заменены затронутые."; onClicked: implForm.openFor() }
            VButton { objectName: "btnEcoVerify"; visible: sel !== null && sel.may_verify === true; enabled: sel !== null && sel.can_verify === true
                      text: "Проверить внедрение"; kind: "primary"; iconName: "clipboard-check"; tip: "Проверяет не тот, кто отмечал внедрение."; onClicked: verifyForm.openFor() }
            VButton { visible: sel !== null && (sel.ecr_id || "") !== ""; text: "Открыть запрос"; kind: "soft"; iconName: "chevron-right"; onClicked: page.openObject(sel.ecr_id) }
        }
        Banner { objectName: "ecoApproveBlock"; visible: sel !== null && (sel.approve_block || "") !== ""; tone: "warning"; text: sel ? sel.approve_block || "" : "" }
        Banner { visible: sel !== null && (sel.verify_block || "") !== ""; tone: "warning"; text: sel ? sel.verify_block || "" : "" }
        Cmd { id: apprCmd; tag: "eco-approve" }
        Text { text: sel ? sel.description || "" : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
        SectionTitle { text: "Что меняется (точные версии)"; iconName: "git-compare" }
        Repeater {
            model: sel && page.tab === "ecos" ? sel.items : []
            delegate: RowLayout { id: irow; required property var modelData
                objectName: "ecoItemRow"
                Layout.fillWidth: true; spacing: 10
                Icon { name: irow.modelData.kind === "document" ? "file-text" : "list-checks"; size: 16; color: Theme.textMuted; Layout.alignment: Qt.AlignTop }
                Text { text: irow.modelData.label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true } }
        }
        KeyValue { kWidth: 170; visible: sel !== null && (sel.approved_label || "") !== ""; k: "Утвердил"; v: sel ? sel.approved_label || "" : "" }
        KeyValue { kWidth: 170; visible: sel !== null && (sel.implemented_label || "") !== ""; k: "Внедрение отметил"; v: sel ? sel.implemented_label || "" : "" }
        KeyValue { kWidth: 170; visible: sel !== null && (sel.verified_label || "") !== ""; k: "Проверил"; v: sel ? sel.verified_label + (sel.verify_note ? "  ·  " + sel.verify_note : "") : "" }
        SectionTitle { text: "Каких экземпляров касается"; iconName: "package-check" }
        Text { visible: sel !== null && (sel.instances || []).length === 0; text: "Изготовленных экземпляров с затронутыми версиями нет."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
        Repeater {
            model: sel && page.tab === "ecos" ? sel.instances : []
            delegate: KeyValue { required property var modelData; objectName: "ecoInstanceRow"; kWidth: 210; k: modelData.label; v: modelData.why_label
                                 tip: "Экземпляр собран по затронутой редакции техпроцесса или его паспорт ссылается на затронутую версию документа." }
        }
    }

    // ---------- история (журнал решений) — для всех вкладок
    SectionTitle { visible: sel !== null; text: "История"; iconName: "history" }
    Repeater {
        model: sel ? sel.history : []
        delegate: RowLayout { id: hrow; required property var modelData
            objectName: "qualityHistoryRow"
            Layout.fillWidth: true; spacing: 10
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { text: hrow.modelData.label + "  ·  " + hrow.modelData.who; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { visible: hrow.modelData.extra !== ""; text: hrow.modelData.extra; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
            Text { text: hrow.modelData.when; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; Layout.alignment: Qt.AlignTop } }
    }

    // ==================================================== формы
    // Команда качества через backend.qualityCommand (целые числа остаются целыми, id новых объектов — там же).
    component QCmd: QtObject {
        id: qc
        property var dialog: null
        property string tag: "q-" + Math.random().toString(36).slice(2)
        function run(command, payload, version) {
            if (dialog) { dialog.working = true; dialog.error = "" }
            backend.qualityCommand(tag, command, payload, version || 0)
        }
        property Connections _conn: Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== qc.tag || !qc.dialog) return
                qc.dialog.working = false
                if (r.status === "accepted" || r.status === "queued") qc.dialog.close()
                else qc.dialog.error = r.message_ru || "Действие не выполнено."
            }
        }
    }

    // Регистрация рекламации (D-40): охват — экземпляр (заводской номер), партия (наряд) или проект без номера изделия;
    // тяжесть дефекта обязательна, категория — если уже ясна; для рекламации от заказчика — срок ответа.
    FormDialog {
        id: claimForm
        objectName: "claimForm"
        title: "Новая рекламация"
        subtitle: "По экземпляру, партии (наряду) или проекту без номера изделия. Можно и без связи — уйдёт с очередью."
        confirmText: "Зарегистрировать рекламацию"
        formWidth: 880        // две колонки; выбор тяжести (три варианта) помещается в колонку
        readonly property string target: clScope.current === "batch" ? clBatch.currentValue : clScope.current === "project" ? clProj.currentValue : clInst.currentValue
        readonly property var payload: ({ scope: clScope.current, instance_id: clScope.current === "instance" ? clInst.currentValue : "",
                                          work_order_id: clScope.current === "batch" ? clBatch.currentValue : "",
                                          project_id: clScope.current === "project" ? clProj.currentValue : "",
                                          received_date: clDate.text.trim(), source: clSrc.current, description: clDesc.text, reference: clRef.text,
                                          severity: clSev.current, category: clCat.currentValue,
                                          response_due: clSrc.current === "customer" ? clDue.text.trim() : "" })
        readonly property var chk: visible ? backend.qualityCheck("claim", payload) : ({ ok: true })
        canConfirm: target !== "" && chk.ok
        note: chk.ok ? "" : chk.message
        function plusDays(n) { var b = clDate.text.trim(); var d = /^\d{4}-\d{2}-\d{2}$/.test(b) ? new Date(b + "T00:00:00") : new Date(); d.setDate(d.getDate() + n)
                               clDue.text = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0") }
        function openFor() { clScope.current = (page.qv.instance_options || []).length ? "instance" : (page.qv.batch_options || []).length ? "batch" : "project"
                             clInst.currentIndex = 0; clBatch.currentIndex = 0; clProj.currentIndex = 0; clSrc.current = "customer"; clDate.text = page.today()
                             clDue.text = ""; clSev.current = ""; clCat.currentIndex = 0; clDesc.text = ""; clRef.text = ""; open() }
        onConfirmed: clCmd.run("claim.register", payload, 0)
        // Поля в две колонки — форма помещается в окно 1440×900 без прокрутки.
        Text { text: "К чему относится"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS
               VTipArea { text: "Если заводской номер неизвестен — выберите партию (наряд, по которому изделия выпущены) или проект." } }
        VSegmented { id: clScope; objectName: "claimScope"; model: page.qv.scopes || []; current: "instance"; onPicked: id => current = id }
        VSelect { id: clInst; objectName: "claimInstance"; visible: clScope.current === "instance"; label: "Экземпляр изделия"; model: page.qv.instance_options || []; Layout.fillWidth: true
                  hint: "Заводской номер изделия, по которому пришла рекламация." }
        VSelect { id: clBatch; objectName: "claimBatch"; visible: clScope.current === "batch"; label: "Партия (наряд)"; model: page.qv.batch_options || []; Layout.fillWidth: true
                  hint: "Выполненный наряд: рекламация на изделия, выпущенные по нему, без заводского номера." }
        VSelect { id: clProj; objectName: "claimProject"; visible: clScope.current === "project"; label: "Проект"; model: page.qv.claim_projects || []; Layout.fillWidth: true
                  hint: "Нет ни номера, ни партии — рекламация на поставку по проекту в целом." }
        GridLayout {
            Layout.fillWidth: true; columns: 2; columnSpacing: 16; rowSpacing: 10
            ColumnLayout { spacing: 4; Layout.fillWidth: true; Layout.preferredWidth: 1
                Text { text: "Источник"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                VSegmented { id: clSrc; objectName: "claimSource"; model: page.qv.sources || []; current: "customer"; onPicked: id => current = id } }
            ColumnLayout { spacing: 4; Layout.fillWidth: true; Layout.preferredWidth: 1
                Text { text: "Тяжесть дефекта *"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS
                       VTipArea { text: "Критический — опасен или изделие не работает; значительный — работает с ограничениями; незначительный — на работу не влияет." } }
                VSegmented { id: clSev; objectName: "claimSeverity"; model: page.qv.severities || []; current: ""; onPicked: id => current = id } }
            VField { id: clDate; label: "Дата получения"; required: true; placeholder: "ГГГГ-ММ-ДД"; hint: "Не позже сегодняшнего дня."; Layout.fillWidth: true; Layout.preferredWidth: 1 }
            ColumnLayout { spacing: 2; Layout.fillWidth: true; Layout.preferredWidth: 1
                VField { id: clDue; objectName: "claimDue"; enabled: clSrc.current === "customer"; label: "Срок ответа заказчику"; required: clSrc.current === "customer"
                         placeholder: clSrc.current === "customer" ? "ГГГГ-ММ-ДД" : "не нужен для внутренней"; Layout.fillWidth: true
                         hint: "Из договора или письма заказчика; программа срок не подставляет." }
                RowLayout { visible: clSrc.current === "customer"; spacing: 4
                    VButton { text: "+5 дн."; kind: "ghost"; compact: true; tip: "Срок — через 5 дней от даты получения."; onClicked: claimForm.plusDays(5) }
                    VButton { text: "+10 дн."; kind: "ghost"; compact: true; tip: "Срок — через 10 дней от даты получения."; onClicked: claimForm.plusDays(10) }
                    VButton { text: "+30 дн."; kind: "ghost"; compact: true; tip: "Срок — через 30 дней от даты получения."; onClicked: claimForm.plusDays(30) } } }
            VSelect { id: clCat; objectName: "claimCategory"; label: "Категория дефекта"; model: page.qv.categories || []; Layout.fillWidth: true; Layout.preferredWidth: 1
                      hint: "Если пока не ясно — укажете при плане действий." }
            VField { id: clRef; label: "Документ заказчика"; placeholder: "Письмо № 45 от 12.11.2026"; hint: "Необязательно."; Layout.fillWidth: true; Layout.preferredWidth: 1 }
        }
        Text { text: "Суть рекламации *"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS
               VTipArea { text: "Что не так: признаки неисправности, когда и при каких условиях проявилась." } }
        ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 70
            TextArea { id: clDesc; objectName: "claimDescription"; placeholderText: "Что не так и при каких условиях"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: TextEdit.Wrap
                       background: Rectangle { radius: Theme.radiusS; color: Theme.surface; border.color: clDesc.activeFocus ? Theme.primary : Theme.border } } }
    }
    QCmd { id: clCmd; dialog: claimForm; tag: "claim-register" }

    FormDialog {
        id: startForm
        objectName: "claimStartForm"
        title: "Начать анализ причины"
        subtitle: sel ? sel.label + " · " + (sel.instance_label || "") : ""
        confirmText: "Назначить"
        canConfirm: stWho.currentValue !== ""
        function openFor() { stWho.currentIndex = 0; open() }
        onConfirmed: stCmd.run("claim.start_analysis", { id: sel.id, responsible_id: stWho.currentValue }, sel.version)
        VSelect { id: stWho; objectName: "claimResponsible"; label: "Ответственный за анализ"; model: page.team; hint: "Участник проекта; получит уведомление." }
    }
    QCmd { id: stCmd; dialog: startForm; tag: "claim-start" }

    // План CAPA: причина (при первом плане) и действия — таблица строк «вид · что сделать · исполнитель · срок».
    FormDialog {
        id: planForm
        objectName: "claimPlanForm"
        formWidth: 860
        property int rev: 0
        title: sel && sel.first_plan ? "Причина и план CAPA" : "Дополнительные действия"
        subtitle: "CAPA — корректирующие (устраняют причину) и предупреждающие (не дают ей повториться) действия. Нужно хотя бы одно корректирующее."
        confirmText: "Утвердить план"
        readonly property var chk: { rev; return visible ? backend.qualityCheck("actions", { first: sel ? sel.first_plan === true : false, cause: plCause.text, actions: collect(),
                                                                                           need_category: sel ? sel.needs_category === true : false, category: plCat.currentValue }) : ({ ok: true, rows: {} }) }
        canConfirm: am.count > 0 && chk.ok
        note: chk.ok ? "" : chk.message
        ListModel { id: am }
        function collect() { var out = []; for (var i = 0; i < am.count; i++) { var r = am.get(i); out.push({ kind: r.kind, description: r.description, owner_id: r.owner_id, due_date: r.due_date }) } return out }
        function addRow(kind) { am.append({ kind: kind, description: "", owner_id: page.team.length ? page.team[0].id : "", due_date: "" }); rev++ }
        function openFor() { am.clear(); plCause.text = sel ? sel.cause || "" : ""; plCat.currentIndex = 0; addRow("corrective"); open() }
        onConfirmed: { var p = { id: sel.id, actions: collect() }; if (sel.first_plan) p.cause = plCause.text
                       if (sel.needs_category && plCat.currentValue) p.category = plCat.currentValue
                       plCmd.run("claim.plan", p, sel.version) }
        Text { visible: sel !== null && sel.first_plan === true; text: "Найденная причина *"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
        ScrollView { visible: sel !== null && sel.first_plan === true; Layout.fillWidth: true; Layout.preferredHeight: 70
            TextArea { id: plCause; objectName: "claimCauseEdit"; placeholderText: "Почему это произошло"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: TextEdit.Wrap
                       onTextChanged: planForm.rev++
                       background: Rectangle { radius: Theme.radiusS; color: Theme.surface; border.color: plCause.activeFocus ? Theme.primary : Theme.border } } }
        VSelect { id: plCat; objectName: "planCategory"; visible: sel !== null && sel.needs_category === true; label: "Категория дефекта *"
                  model: (page.qv.categories || []).filter(c => c.id !== ""); onCurrentIndexChanged: planForm.rev++
                  hint: "Откуда дефект — по найденной причине: документация, изготовление, покупные комплектующие и т. д." }
        Repeater {
            model: am
            delegate: Rectangle { id: prow; required property int index; required property var model
                readonly property string problem: planForm.chk.rows ? (planForm.chk.rows[String(index)] || "") : ""
                objectName: "claimActionEdit"
                Layout.fillWidth: true; implicitHeight: pc.implicitHeight + 8; radius: Theme.radiusS
                color: problem !== "" ? Theme.dangerSoft : "transparent"
                ColumnLayout { id: pc; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.margins: 4; spacing: 2
                    RowLayout { Layout.fillWidth: true; spacing: 6
                        VSegmented { model: page.qv.action_kinds || []; current: prow.model.kind; onPicked: id => { am.setProperty(prow.index, "kind", id); planForm.rev++ } }
                        CalcCell { Layout.fillWidth: true; text: prow.model.description; placeholder: "Что сделать"; Accessible.name: "Действие " + (prow.index + 1)
                                   onTextEdited: { am.setProperty(prow.index, "description", text); planForm.rev++ } }
                        ComboBox { Layout.preferredWidth: 170; model: page.team; textRole: "label"; valueRole: "id"; Accessible.name: "Исполнитель " + (prow.index + 1)
                                   currentIndex: Math.max(0, page.team.findIndex(t => t.id === prow.model.owner_id))
                                   onActivated: i => { am.setProperty(prow.index, "owner_id", page.team[i].id); planForm.rev++ }
                                   VToolTip { visible: parent.hovered; text: "Исполнитель — участник проекта; получит уведомление." } }
                        CalcCell { Layout.preferredWidth: 110; text: prow.model.due_date; placeholder: "ГГГГ-ММ-ДД"; Accessible.name: "Срок " + (prow.index + 1)
                                   onTextEdited: { am.setProperty(prow.index, "due_date", text); planForm.rev++ } }
                        VButton { kind: "ghost"; compact: true; iconName: "trash-2"; Accessible.name: "Удалить действие " + (prow.index + 1); onClicked: { am.remove(prow.index); planForm.rev++ } } }
                    Text { visible: prow.problem !== ""; text: prow.problem; color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsXs; leftPadding: 4; wrapMode: Text.Wrap; Layout.fillWidth: true } }
            }
        }
        RowLayout { spacing: 8
            VButton { objectName: "claimAddCorrective"; text: "Корректирующее"; iconName: "plus"; kind: "soft"; compact: true; tip: "Действие, которое устраняет найденную причину."; onClicked: planForm.addRow("corrective") }
            VButton { text: "Предупреждающее"; iconName: "plus"; kind: "soft"; compact: true; tip: "Действие, которое не даёт причине повториться (в других изделиях и проектах)."; onClicked: planForm.addRow("preventive") } }
    }
    QCmd { id: plCmd; dialog: planForm; tag: "claim-plan" }

    FormDialog {
        id: doneForm
        objectName: "actionDoneForm"
        property var action: null
        title: "Действие выполнено"
        subtitle: action ? action.kind_label + ": " + action.description : ""
        confirmText: "Отметить"
        canConfirm: dnRes.text.trim() !== ""
        function openFor(a) { action = a; dnRes.text = ""; open() }
        onConfirmed: dnCmd.run("claim.action_done", { id: sel.id, action_id: action.id, result: dnRes.text }, 0)
        VField { id: dnRes; objectName: "actionResult"; label: "Что сделано (результат)"; required: true; placeholder: "Заменён разъём, выпущено изменение схемы" }
    }
    QCmd { id: dnCmd; dialog: doneForm; tag: "claim-action-done" }

    FormDialog {
        id: checkForm
        objectName: "claimCheckForm"
        title: "Проверка эффективности"
        subtitle: "Устранена ли причина рекламации? «Не эффективно» вернёт рекламацию к действиям."
        confirmText: "Записать проверку"
        confirmKind: chEff.current === "no" ? "danger" : "primary"
        canConfirm: chEv.text.trim() !== ""
        function openFor() { chEff.current = "yes"; chEv.text = ""; open() }
        onConfirmed: chCmd.run("claim.check", { id: sel.id, effective: chEff.current === "yes", evidence: chEv.text }, sel.version)
        VSegmented { id: chEff; objectName: "checkEffective"; current: "yes"; onPicked: id => current = id
                     model: [{ id: "yes", label: "Эффективно", icon: "circle-check", tip: "Причина устранена — рекламация будет закрыта." },
                             { id: "no", label: "Не эффективно", icon: "circle-x", tip: "Причина не устранена — рекламация вернётся к действиям." }] }
        VField { id: chEv; objectName: "checkEvidence"; label: "Чем подтверждено"; required: true; placeholder: "Повторные испытания, протокол № 35 — замечаний нет" }
    }
    QCmd { id: chCmd; dialog: checkForm; tag: "claim-check" }

    // Классификация дефекта (D-40): тяжесть и категория, причина изменения обязательна.
    FormDialog {
        id: classifyForm
        objectName: "claimClassifyForm"
        title: "Классификация дефекта"
        subtitle: sel ? sel.label + " · сейчас: " + (sel.severity_label || "") + (sel.category_label ? ", " + sel.category_label : "") : ""
        confirmText: "Сохранить классификацию"
        readonly property var chk: visible ? backend.qualityCheck("classify", { severity: cfSev.current, category: cfCat.currentValue, reason: cfReason.text }) : ({ ok: true })
        canConfirm: chk.ok
        note: chk.ok ? "" : chk.message
        function openFor() { cfSev.current = sel.severity || ""; var cats = page.qv.categories || []; var i = cats.findIndex(c => c.id === (sel.category || ""))
                             cfCat.currentIndex = i >= 0 ? i : 0; cfReason.text = ""; open() }
        onConfirmed: cfCmd.run("claim.classify", { id: sel.id, severity: cfSev.current, category: cfCat.currentValue, reason: cfReason.text }, sel.version)
        VSegmented { id: cfSev; objectName: "classifySeverity"; model: page.qv.severities || []; current: ""; onPicked: id => current = id }
        VSelect { id: cfCat; objectName: "classifyCategory"; label: "Категория дефекта"; model: page.qv.categories || []
                  hint: "Откуда дефект. Указанную категорию можно сменить, но не убрать." }
        VField { id: cfReason; objectName: "classifyReason"; label: "Причина изменения"; required: true; placeholder: "Анализ показал перегрев клемм — опасно" }
    }
    QCmd { id: cfCmd; dialog: classifyForm; tag: "claim-classify" }

    // Новый срок ответа заказчику (D-40): по согласованию с заказчиком или для следующего (окончательного) ответа.
    FormDialog {
        id: dueForm
        objectName: "claimDueForm"
        title: "Срок ответа заказчику"
        subtitle: sel ? sel.label + (sel.due_label ? " · сейчас: до " + sel.due_label : "") : ""
        confirmText: "Установить срок"
        readonly property var chk: visible ? backend.qualityCheck("due", { response_due: duDate.text.trim(), received_date: sel ? sel.received_date : "", reason: duReason.text }) : ({ ok: true })
        canConfirm: chk.ok
        note: chk.ok ? "" : chk.message
        function openFor() { duDate.text = ""; duReason.text = ""; open() }
        onConfirmed: duCmd.run("claim.set_due", { id: sel.id, response_due: duDate.text.trim(), reason: duReason.text }, sel.version)
        VField { id: duDate; objectName: "dueDate"; label: "Новый срок"; required: true; placeholder: "ГГГГ-ММ-ДД"; hint: "Не раньше сегодняшнего дня. Отсчёт «ответ дан» начнётся заново." }
        VField { id: duReason; objectName: "dueReason"; label: "Причина"; required: true; placeholder: "Согласовано с заказчиком письмом № 15 / окончательный ответ" }
    }
    QCmd { id: duCmd; dialog: dueForm; tag: "claim-due" }

    // Ответ заказчику (D-40): неизменяемая запись — вид, дата отправки, что ответили, номер письма.
    FormDialog {
        id: respondForm
        objectName: "claimRespondForm"
        title: "Ответ заказчику"
        subtitle: sel ? sel.label + (sel.deadline_label ? " · " + sel.deadline_label : "") : ""
        confirmText: "Записать ответ"
        readonly property var chk: visible ? backend.qualityCheck("respond", { kind: rsKind.current, response_date: rsDate.text.trim(), summary: rsSummary.text, reference: rsRef.text }) : ({ ok: true })
        canConfirm: chk.ok
        note: chk.ok ? "" : chk.message
        function openFor() { rsKind.current = (sel.responses || []).length ? "final" : "interim"; rsDate.text = page.today(); rsSummary.text = ""; rsRef.text = ""; open() }
        onConfirmed: rsCmd.run("claim.respond", { id: sel.id, kind: rsKind.current, response_date: rsDate.text.trim(), summary: rsSummary.text, reference: rsRef.text }, sel.version)
        VSegmented { id: rsKind; objectName: "respondKind"; model: page.qv.response_kinds || []; current: "interim"; onPicked: id => current = id }
        VField { id: rsDate; label: "Дата отправки"; required: true; placeholder: "ГГГГ-ММ-ДД"; hint: "Не позже сегодняшнего дня и не раньше получения рекламации." }
        VField { id: rsRef; label: "Номер письма"; placeholder: "Исх. № 12 от 14.11.2026 (необязательно)" }
        VField { id: rsSummary; objectName: "respondSummary"; label: "Что ответили"; required: true; placeholder: "Приняли в работу, анализ причины — до 30.11" }
    }
    QCmd { id: rsCmd; dialog: respondForm; tag: "claim-respond" }

    FormDialog {
        id: ecrForm
        objectName: "ecrForm"
        formWidth: 620
        property string claimId: ""
        title: "Запрос на изменение"
        subtitle: claimId !== "" ? "По рекламации: " + (sel ? sel.label : "") : "Что и почему предлагается изменить. Можно и без связи — уйдёт с очередью."
        confirmText: "Подать запрос"
        readonly property string projectId: claimId !== "" && sel ? sel.project_id : erProj.currentValue
        readonly property var kitOpts: [{ id: "", label: "не относится к конкретному изделию" }].concat((page.qv.kit_options || []).filter(k => k.project_id === projectId))
        readonly property var chk: visible ? backend.qualityCheck("ecr", { title: erTitle.text, reason: erReason.text, proposal: erProp.text }) : ({ ok: true })
        canConfirm: projectId !== "" && chk.ok
        note: chk.ok ? "" : chk.message
        function openFor(claim) { claimId = claim ? claim.id : ""; erProj.currentIndex = 0; erKit.currentIndex = 0; erTitle.text = ""; erReason.text = claim ? claim.title : ""; erProp.text = ""; open() }
        onConfirmed: { var p = { project_id: projectId, title: erTitle.text, reason: erReason.text, proposal: erProp.text }
                       if (erKit.currentValue !== "") p.kit_id = erKit.currentValue
                       if (claimId !== "") p.claim_id = claimId
                       erCmd.run("ecr.submit", p, 0) }
        VSelect { id: erProj; visible: ecrForm.claimId === ""; label: "Проект"; model: page.qv.ecr_projects || [] }
        VSelect { id: erKit; label: "Изделие"; model: ecrForm.kitOpts; hint: "Необязательно: к какому изделию проекта относится изменение." }
        VField { id: erTitle; objectName: "ecrTitle"; label: "Заголовок"; required: true; placeholder: "Замена разъёма питания на герметичный" }
        VField { id: erReason; objectName: "ecrReason"; label: "Причина изменения"; required: true; placeholder: "Почему нужно менять" }
        VField { id: erProp; objectName: "ecrProposal"; label: "Что предлагается изменить"; required: true; placeholder: "Какие документы, техпроцесс, состав" }
    }
    QCmd { id: erCmd; dialog: ecrForm; tag: "ecr-submit" }

    FormDialog {
        id: assessForm
        objectName: "ecrAssessForm"
        title: "Оценка влияния"
        subtitle: sel ? sel.label + " — " + sel.title : ""
        confirmText: "Записать оценку"
        canConfirm: asImpact.text.trim() !== ""
        function openFor() { asImpact.text = ""; open() }
        onConfirmed: asCmd.run("ecr.assess", { id: sel.id, impact: asImpact.text }, sel.version)
        Text { text: "Что затронет изменение *"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS
               VTipArea { text: "Документы и техпроцесс, изготовленные экземпляры, сроки, затраты." } }
        ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 100
            TextArea { id: asImpact; objectName: "ecrImpactEdit"; placeholderText: "Схема и техпроцесс; один изготовленный экземпляр; два дня"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: TextEdit.Wrap
                       background: Rectangle { radius: Theme.radiusS; color: Theme.surface; border.color: asImpact.activeFocus ? Theme.primary : Theme.border } } }
    }
    QCmd { id: asCmd; dialog: assessForm; tag: "ecr-assess" }

    FormDialog {
        id: decideForm
        objectName: "ecrDecideForm"
        title: "Решение по запросу"
        subtitle: sel ? sel.label + " — " + sel.title : ""
        confirmText: dcDec.current === "accept" ? "Принять" : "Отклонить"
        confirmKind: dcDec.current === "accept" ? "primary" : "danger"
        canConfirm: dcReason.text.trim() !== ""
        function openFor() { dcDec.current = "accept"; dcReason.text = ""; open() }
        onConfirmed: dcCmd.run("ecr.decide", { id: sel.id, decision: dcDec.current, reason: dcReason.text }, sel.version)
        VSegmented { id: dcDec; objectName: "ecrDecision"; current: "accept"; onPicked: id => current = id
                     model: [{ id: "accept", label: "Принять", icon: "circle-check", tip: "Изменение нужно — дальше составляется извещение." },
                             { id: "reject", label: "Отклонить", icon: "circle-x", tip: "Изменение не делаем; обоснование увидит автор." }] }
        VField { id: dcReason; objectName: "ecrDecisionReason"; label: "Обоснование"; required: true }
    }
    QCmd { id: dcCmd; dialog: decideForm; tag: "ecr-decide" }

    // Внедрение: для каждой строки — новая версия документа (номер) или утверждённая новая редакция техпроцесса.
    FormDialog {
        id: implForm
        objectName: "ecoImplementForm"
        formWidth: 720
        property var vals: ({})
        property int rev: 0
        title: "Внедрение извещения"
        subtitle: "На какие версии заменены затронутые. Документ — уже загруженная новая версия; техпроцесс — утверждённая новая редакция того же изделия."
        confirmText: "Отметить внедрение"
        readonly property bool complete: { rev; if (!sel || !sel.items) return false
            for (var i = 0; i < sel.items.length; i++) { var v = vals[String(i)]; if (v === undefined || v === "" || v === 0) return false } return true }
        canConfirm: complete
        function openFor() { var v = {}; for (var i = 0; i < sel.items.length; i++) { var it = sel.items[i]
                                 v[String(i)] = it.kind === "document" ? (it.suggest || "") : (it.tp_options.length ? it.tp_options[it.tp_options.length - 1].id : "") }
                             vals = v; rev++; open() }
        onConfirmed: { var res = []
                       for (var i = 0; i < sel.items.length; i++) { var it = sel.items[i]; var v = vals[String(i)]
                           res.push(it.kind === "document" ? { index: i, to_version: parseInt(v) } : { index: i, to_tp_id: v }) }
                       imCmd.run("eco.implement", { id: sel.id, results: res }, sel.version) }
        Repeater {
            model: sel && page.tab === "ecos" ? sel.items : []
            delegate: ColumnLayout { id: mrow; required property var modelData; required property int index
                objectName: "ecoImplementRow"
                Layout.fillWidth: true; spacing: 4
                Text { text: mrow.modelData.label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                VField { visible: mrow.modelData.kind === "document"; label: "Новая версия"; placeholder: "номер версии"
                         text: String(implForm.vals[String(mrow.index)] || ""); hint: mrow.modelData.hint
                         onTextChanged: { var v = implForm.vals; v[String(mrow.index)] = text.trim(); implForm.vals = v; implForm.rev++ } }
                VSelect { visible: mrow.modelData.kind === "tech_process"; label: "Новая редакция"; model: mrow.modelData.tp_options; hint: mrow.modelData.hint
                          currentIndex: mrow.modelData.tp_options.length - 1
                          onCurrentIndexChanged: if (currentIndex >= 0 && mrow.modelData.tp_options.length > currentIndex) { var v = implForm.vals; v[String(mrow.index)] = mrow.modelData.tp_options[currentIndex].id; implForm.vals = v; implForm.rev++ } } }
        }
    }
    QCmd { id: imCmd; dialog: implForm; tag: "eco-implement" }

    FormDialog {
        id: verifyForm
        objectName: "ecoVerifyForm"
        title: "Проверка внедрения"
        subtitle: sel ? sel.label + " — " + sel.title : ""
        confirmText: "Записать проверку"
        canConfirm: vfNote.text.trim() !== ""
        function openFor() { vfNote.text = ""; open() }
        onConfirmed: vfCmd.run("eco.verify", { id: sel.id, note: vfNote.text }, sel.version)
        VField { id: vfNote; objectName: "ecoVerifyNote"; label: "Что проверено"; required: true; placeholder: "Новые версии выданы в цех, техпроцесс применяется" }
    }
    QCmd { id: vfCmd; dialog: verifyForm; tag: "eco-verify" }

    // Редактор извещения: заголовок, описание, строки «документ + версия» или «техпроцесс (редакция)».
    FormDialog {
        id: ecoEditor
        objectName: "ecoEditor"
        formWidth: 760
        property string ecoId: ""
        property string ecrId: ""
        property string projectId: ""
        property bool fresh: true
        property int rev: 0
        title: fresh ? "Новое извещение об изменении" : "Извещение — черновик"
        subtitle: "Укажите точные версии, которые меняются. Черновик можно сохранять без связи."
        confirmText: "Сохранить извещение"
        readonly property var docOpts: ((page.qv.ref_options || {}).document || []).filter(d => d.project_id === projectId)
        readonly property var tpOpts: ((page.qv.ref_options || {}).tech_process || []).filter(d => d.project_id === projectId)
        readonly property var chk: { rev; return visible ? backend.qualityCheck("eco", { description: edDesc.text, items: collect() }) : ({ ok: true }) }
        canConfirm: chk.ok
        note: chk.ok ? "" : chk.message
        ListModel { id: em }
        function collect() { var out = []; for (var i = 0; i < em.count; i++) { var r = em.get(i)
                                 out.push(r.kind === "document" ? { kind: "document", document_id: r.ref_id, from_version: r.version } : { kind: "tech_process", tp_id: r.ref_id }) } return out }
        function openFor(eco, ecr) {
            fresh = eco === null; em.clear()
            ecoId = eco ? eco.id : backend.newId(); ecrId = eco ? eco.ecr_id : ecr.id; projectId = eco ? eco.project_id : ecr.project_id
            edTitle.text = eco ? eco.title : ecr.title; edDesc.text = eco ? eco.description : ""
            var its = eco ? eco.items : []
            for (var i = 0; i < its.length; i++) em.append({ kind: its[i].kind, ref_id: its[i].document_id || its[i].tp_id, version: its[i].from_version || 0, label: its[i].label })
            rev++; open()
        }
        onConfirmed: { working = true; error = ""; backend.saveEco("eco-save", { id: ecoId, ecr_id: ecrId, title: edTitle.text, description: edDesc.text, items: collect() }) }
        Connections { target: backend
            function onCommandDone(t, r) { if (t !== "eco-save") return; ecoEditor.working = false
                if (r.status === "accepted" || r.status === "queued") ecoEditor.close(); else ecoEditor.error = r.message_ru || "Извещение не сохранено." } }
        VField { id: edTitle; objectName: "ecoTitle"; label: "Заголовок"; placeholder: "По умолчанию — как у запроса" }
        VField { id: edDesc; objectName: "ecoDescription"; label: "Что и как изменяется"; required: true; onTextChanged: ecoEditor.rev++ }
        Repeater {
            model: em
            delegate: RowLayout { id: erow; required property int index; required property var model
                objectName: "ecoItemEdit"
                Layout.fillWidth: true; spacing: 8
                Icon { name: erow.model.kind === "document" ? "file-text" : "list-checks"; size: 16; color: Theme.textMuted }
                Text { text: erow.model.label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                VButton { kind: "ghost"; compact: true; iconName: "trash-2"; Accessible.name: "Убрать строку " + (erow.index + 1); onClicked: { em.remove(erow.index); ecoEditor.rev++ } } }
        }
        RowLayout { Layout.fillWidth: true; spacing: 8
            VSelect { id: edDoc; objectName: "ecoDocSel"; label: "Документ (последняя версия)"; model: ecoEditor.docOpts; Layout.fillWidth: true
                      hint: ecoEditor.docOpts.length === 0 ? "В проекте нет документов с версиями (или нет связи, чтобы получить их список)." : "Будет закреплена текущая версия документа — та, что меняется." }
            VButton { objectName: "ecoAddDoc"; text: "Добавить"; iconName: "plus"; kind: "soft"; compact: true; Layout.alignment: Qt.AlignBottom; enabled: edDoc.currentValue !== ""
                      onClicked: { var d = ecoEditor.docOpts[edDoc.currentIndex]
                                   em.append({ kind: "document", ref_id: d.id, version: d.current_version_no, label: "Документ " + d.label.replace(/ \(последняя версия \d+\)$/, "") + ", версия " + d.current_version_no }); ecoEditor.rev++ } } }
        RowLayout { Layout.fillWidth: true; spacing: 8
            VSelect { id: edTp; objectName: "ecoTpSel"; label: "Техпроцесс (редакция)"; model: ecoEditor.tpOpts; Layout.fillWidth: true
                      hint: "Редакция техпроцесса, которая меняется." }
            VButton { objectName: "ecoAddTp"; text: "Добавить"; iconName: "plus"; kind: "soft"; compact: true; Layout.alignment: Qt.AlignBottom; enabled: edTp.currentValue !== ""
                      onClicked: { var t = ecoEditor.tpOpts[edTp.currentIndex]; em.append({ kind: "tech_process", ref_id: t.id, version: 0, label: "Техпроцесс " + t.label }); ecoEditor.rev++ } } }
    }
}
