import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Блок «Потоки и риски» на карточке проекта (D-65, этап 2 blueprint 52):
//  * шесть потоков проекта (договор, РКД, закупка, производство, отгрузка, площадка) со статусами, ответственными
//    и плановыми датами; переходы — кнопки из контракта workflows (шлюзы и обязательные причины проверяет сервер);
//  * риск-разрешения РП (закупка/сборка до согласования РКД): причина, срок, охват, снятие;
//  * рамка проекта: «Завершается»/«Архив»/«Отменён» — с причиной; архив — только когда потоки в финале и нет
//    действующих рисков (проверяет сервер; при архиве создаётся неизменяемый снимок закрытия с контрольной суммой);
//  * журнал событий проекта — неизменяемая лента вех.
//
// Всё читается из снимка pull (и офлайн); действия — онлайн-команды, кнопки — подсказка по роли (РП проекта).
ColumnLayout {
    id: pf
    property var project: null
    readonly property var v: project ? backend.projectDelivery(project.id, backend.dataRevision) : ({})
    readonly property var streams: v.streams || []
    readonly property var risks: v.risks || []
    readonly property var events: v.events || []
    readonly property var prj: v.project || null
    readonly property var counts: v.counts || ({})
    spacing: 8

    RowLayout {
        Layout.fillWidth: true
        SectionTitle { text: "Потоки и риски"; iconName: "git-compare" }
        Item { Layout.fillWidth: true }
        Text { visible: (pf.counts.active_risks || 0) + (pf.counts.expired_risks || 0) > 0
               text: (pf.counts.active_risks ? pf.counts.active_risks + " действует" : "")
                     + (pf.counts.active_risks && pf.counts.expired_risks ? " · " : "")
                     + (pf.counts.expired_risks ? pf.counts.expired_risks + " истекло" : "")
               color: pf.counts.expired_risks ? Theme.danger : Theme.warning; font.pixelSize: Theme.fsS }
    }

    // ---------- рамка проекта: завершение, архив, отмена
    RowLayout {
        visible: pf.prj && (pf.prj.actions || []).length > 0
        Layout.fillWidth: true; spacing: 8
        Text { text: "Статус проекта:"; color: Theme.textMuted; font.pixelSize: Theme.fsS }
        VChip { text: pf.prj ? pf.prj.status_label : ""; tone: "info" }
        Repeater {
            model: pf.prj ? pf.prj.actions : []
            delegate: VButton {
                required property var modelData
                text: modelData.label; kind: modelData.danger ? "danger" : "ghost"; compact: true
                onClicked: prjDialog.setup(modelData)
            }
        }
    }

    // ---------- потоки
    Flow {
        Layout.fillWidth: true; spacing: 8
        Repeater {
            model: pf.streams
            delegate: VCard {
                id: sCard
                required property var modelData
                Layout.preferredWidth: 300
                implicitHeight: sCol.implicitHeight + 24
                ColumnLayout {
                    id: sCol
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
                    spacing: 4
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Text { text: sCard.modelData.kind_label; color: Theme.text; font.pixelSize: Theme.fs
                               font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                        VChip { text: sCard.modelData.status_label
                                tone: sCard.modelData.terminal ? "success" : "info" }
                    }
                    Text { text: "Ответственный: " + sCard.modelData.responsible_name
                                 + (sCard.modelData.planned_due !== "" ? " · до " + sCard.modelData.planned_due : "")
                           color: Theme.textMuted; font.pixelSize: Theme.fsXs }
                    Text { visible: sCard.modelData.note !== ""; text: sCard.modelData.note
                           color: Theme.textMuted; font.pixelSize: Theme.fsXs; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    Flow {
                        Layout.fillWidth: true; spacing: 6
                        Repeater {
                            model: sCard.modelData.actions
                            delegate: VButton {
                                required property var modelData
                                text: modelData.label; kind: "ghost"; compact: true
                                tip: modelData.gate ? modelData.gate_hint : ""
                                onClicked: streamDialog.setup(sCard.modelData, modelData)
                            }
                        }
                        VButton { visible: sCard.modelData.can_plan; text: "План"; kind: "ghost"; compact: true
                                  iconName: "pencil"; onClicked: planDialog.setup(sCard.modelData) }
                    }
                }
            }
        }
    }

    // ---------- риск-разрешения
    RowLayout {
        Layout.fillWidth: true; Layout.topMargin: 4
        SectionTitle { text: "Риск-разрешения"; iconName: "triangle-alert" }
        Item { Layout.fillWidth: true }
        VButton { text: "Принять риск"; kind: "ghost"; compact: true; iconName: "plus"
                  enabled: pf.v.can_manage === true
                  tip: "Осознанно разрешить шаг до выполнения стандартного условия — с обязательной причиной и сроком"
                  onClicked: riskDialog.ask() }
    }
    Text { visible: pf.risks.length === 0
           text: "Риск-разрешений нет — проект идёт по стандартным условиям (шлюзы потоков требуют согласованной РКД)."
           color: Theme.textFaint; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    Repeater {
        model: pf.risks
        delegate: VCard {
            id: rCard
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: rCol.implicitHeight + 20
            ColumnLayout {
                id: rCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                spacing: 4
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Text { text: rCard.modelData.kind_label; color: Theme.text; font.pixelSize: Theme.fs
                           font.weight: Font.DemiBold }
                    VChip { text: rCard.modelData.state_label; tone: rCard.modelData.tone }
                    Text { text: "до " + rCard.modelData.due_on + " · " + rCard.modelData.scope_label
                           color: Theme.textMuted; font.pixelSize: Theme.fsXs; Layout.fillWidth: true; elide: Text.ElideRight }
                }
                Text { text: "Причина: " + rCard.modelData.reason
                       color: Theme.textMuted; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Text { visible: rCard.modelData.mitigation !== ""; text: "Снижение риска: " + rCard.modelData.mitigation
                       color: Theme.textMuted; font.pixelSize: Theme.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Text { text: "Принял: " + rCard.modelData.accepted_by_name + ", " + rCard.modelData.created_label
                             + (rCard.modelData.resolved_label !== ""
                                ? " · снято: " + rCard.modelData.resolved_by_name + ", " + rCard.modelData.resolved_label
                                  + " (" + rCard.modelData.resolution_reason + ")" : "")
                       color: Theme.textFaint; font.pixelSize: Theme.fsXs; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Flow {
                    Layout.fillWidth: true; spacing: 6
                    Repeater {
                        model: rCard.modelData.actions
                        delegate: VButton {
                            required property var modelData
                            text: modelData.label; kind: "ghost"; compact: true
                            onClicked: resolveDialog.setup(rCard.modelData, modelData)
                        }
                    }
                }
            }
        }
    }

    // ---------- снимок закрытия (архив)
    Banner {
        visible: pf.v.closeout !== null && pf.v.closeout !== undefined
        Layout.fillWidth: true
        tone: "success"
        text: pf.v.closeout ? "Проект закрыт: неизменяемый снимок от " + pf.v.closeout.created_label
                              + " (" + pf.v.closeout.closed_by_name + "), контрольная сумма SHA-256: "
                              + ("" + pf.v.closeout.content_sha256).substring(0, 16) + "…" : ""
    }

    // ---------- журнал событий
    RowLayout {
        Layout.fillWidth: true; Layout.topMargin: 4
        SectionTitle { text: "Журнал событий" + (pf.counts.events ? " (" + pf.counts.events + ")" : ""); iconName: "history" }
        Item { Layout.fillWidth: true }
    }
    Text { visible: pf.events.length === 0; text: "Событий пока нет — вехи появятся по мере работы с проектом."
           color: Theme.textFaint; font.pixelSize: Theme.fsS }
    Repeater {
        model: pf.events.slice(0, 12)
        delegate: RowLayout {
            required property var modelData
            Layout.fillWidth: true; spacing: 8
            Text { text: modelData.at_label; color: Theme.textFaint; font.pixelSize: Theme.fsXs
                   Layout.preferredWidth: 110 }
            Text { text: modelData.summary; color: Theme.text; font.pixelSize: Theme.fsS
                   wrapMode: Text.WordWrap; Layout.fillWidth: true }
            Text { text: modelData.actor_name; color: Theme.textMuted; font.pixelSize: Theme.fsXs }
        }
    }

    // ================================ диалоги
    Cmd { id: streamCmd; dialog: streamDialog }
    Cmd { id: planCmd; dialog: planDialog }
    Cmd { id: riskCmd; dialog: riskDialog }
    Cmd { id: resolveCmd; dialog: resolveDialog }
    Cmd { id: prjCmd; dialog: prjDialog }

    FormDialog {
        id: streamDialog
        property var stream: null
        property var act: null
        title: stream ? stream.kind_label + " → «" + (act ? act.label : "") + "»" : ""
        subtitle: act && act.gate ? "Шлюз: переход требует РКД «Согласована заказчиком» или действующего риск-разрешения — сервер проверит в момент выполнения." : ""
        confirmText: "Выполнить"
        canConfirm: !act || !act.reason || streamReason.text.trim() !== ""
        function setup(stream, act) { this.stream = stream; this.act = act; error = ""; streamReason.text = ""; open() }
        onConfirmed: streamCmd.run("workstream.set_status", { id: stream.id, to: act.to,
                                   reason: streamReason.text.trim() !== "" ? streamReason.text.trim() : null },
                                   stream.version)
        VField { id: streamReason; label: "Причина"; required: streamDialog.act && streamDialog.act.reason
                 hint: "Обязательна для этого перехода — попадёт в журнал проекта" }
    }

    FormDialog {
        id: planDialog
        property var stream: null
        title: "План потока «" + (stream ? stream.kind_label : "") + "»"
        subtitle: "Ответственный — участник команды проекта (или его замещающий); даты плановые."
        confirmText: "Сохранить"
        function setup(stream) {
            this.stream = stream; error = ""
            var opts = [{ id: "", label: "— не назначен —" }]
            var asg = backend.assignments
            for (var i = 0; i < asg.length; i++)
                if (asg[i].project_id === pf.project.id)
                    opts.push({ id: asg[i].user_id, label: asg[i].user_name + " — " + asg[i].role_label })
            respSel.model = opts
            respSel.currentIndex = 0
            for (var j = 0; j < opts.length; j++) if (opts[j].id === stream.responsible_user_id) respSel.currentIndex = j
            startF.text = stream.planned_start; dueF.text = stream.planned_due; noteF.text = stream.note
            open()
        }
        onConfirmed: planCmd.run("workstream.update", { id: planDialog.stream.id,
                                 responsible_user_id: respSel.currentValue ? respSel.currentValue.id || null : null,
                                 planned_start: startF.text.trim() !== "" ? startF.text.trim() : null,
                                 planned_due: dueF.text.trim() !== "" ? dueF.text.trim() : null,
                                 note: noteF.text.trim() !== "" ? noteF.text.trim() : null }, planDialog.stream.version)
        VSelect { id: respSel; label: "Ответственный" }
        VField { id: startF; label: "Плановое начало"; placeholder: "2026-11-20"; hint: "Формат: ГГГГ-ММ-ДД" }
        VField { id: dueF; label: "Плановый срок"; placeholder: "2026-12-31" }
        VField { id: noteF; label: "Примечание" }
    }

    FormDialog {
        id: riskDialog
        title: "Принять риск-разрешение"
        subtitle: "Осознанное исключение из стандартного условия: обязательны причина и срок. Действующее разрешение одного вида — одно; статусы документов разрешение не меняет."
        confirmText: "Принять риск"
        confirmKind: "danger"
        canConfirm: reasonF.text.trim() !== "" && dueRiskF.text.trim() !== ""
        function ask() { error = ""; kindSel.currentIndex = 0; reasonF.text = ""; mitigF.text = ""; dueRiskF.text = ""; kitChecks.selected = []; open() }
        onConfirmed: {
            var payload = { id: backend.newId(), project_id: pf.project.id,
                            kind: riskDialog.kindModel[kindSel.currentIndex].id,
                            reason: reasonF.text.trim(), due_on: dueRiskF.text.trim() }
            if (mitigF.text.trim() !== "") payload.mitigation = mitigF.text.trim()
            if (kitChecks.selected.length > 0) payload.kit_ids = kitChecks.selected
            riskCmd.run("risk.accept", payload)
        }
        readonly property var kindModel: pf.v.risk_kinds || []
        VSelect { id: kindSel; label: "Вид разрешения"; model: riskDialog.kindModel }
        VField { id: reasonF; label: "Причина"; required: true; hint: "Почему шаг необходим до выполнения условия" }
        VField { id: mitigF; label: "Снижение риска"; hint: "Что делаем, чтобы риск не реализовался (необязательно)" }
        VField { id: dueRiskF; label: "Срок действия"; required: true; placeholder: "2026-12-31"
                 hint: "Не в прошлом; после срока разрешение истекает и требует решения" }
        Text { visible: (pf.v.kit_options || []).length > 0; text: "Охват (без выбора — весь проект):"
               color: Theme.textMuted; font.pixelSize: Theme.fsS }
        Repeater {
            model: pf.v.kit_options || []
            delegate: CheckBox {
                required property var modelData
                text: modelData.label
                checked: kitChecks.selected.indexOf(modelData.id) >= 0
                onToggled: {
                    var s = kitChecks.selected.slice()
                    var i = s.indexOf(modelData.id)
                    if (checked && i < 0) s.push(modelData.id)
                    if (!checked && i >= 0) s.splice(i, 1)
                    kitChecks.selected = s
                }
            }
        }
        QtObject { id: kitChecks; property var selected: [] }
    }

    FormDialog {
        id: resolveDialog
        property var risk: null
        property var act: null
        title: act ? act.label : ""
        subtitle: risk ? risk.kind_label + " (до " + risk.due_on + ")" : ""
        confirmText: "Снять разрешение"
        confirmKind: "danger"
        canConfirm: resolveReason.text.trim() !== ""
        function setup(risk, act) { this.risk = risk; this.act = act; error = ""; resolveReason.text = ""; open() }
        onConfirmed: resolveCmd.run("risk.resolve", { id: resolveDialog.risk.id,
                                    resolution: resolveDialog.act.resolution, reason: resolveReason.text.trim() },
                                    resolveDialog.risk.version)
        VField { id: resolveReason; label: "Причина"; required: true
                 hint: "Почему разрешение снимается — строка останется в истории" }
    }

    FormDialog {
        id: prjDialog
        property var act: null
        title: act ? "Проект → «" + act.label + "»" : ""
        subtitle: act && act.to === "archived"
                  ? "Архив возможен, когда все потоки в конечных статусах и нет действующих риск-разрешений; сервер создаст неизменяемый снимок закрытия. В архиве — только чтение."
                  : (act && act.to === "cancelled" ? "Отмена проекта — с обязательной причиной; отменённый проект доступен только для чтения." : "")
        confirmText: "Выполнить"
        confirmKind: act && act.danger ? "danger" : "primary"
        canConfirm: !act || !act.reason || prjReason.text.trim() !== ""
        function setup(act) { this.act = act; error = ""; prjReason.text = ""; open() }
        onConfirmed: prjCmd.run("project.transition", { id: pf.project.id, to: prjDialog.act.to,
                                reason: prjReason.text.trim() !== "" ? prjReason.text.trim() : null },
                                pf.prj.version)
        VField { id: prjReason; label: "Причина"; required: prjDialog.act && prjDialog.act.reason
                 hint: "Попадёт в журнал событий проекта" }
    }
}
