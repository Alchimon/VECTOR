import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Вкладка «Расчёт» комплекта (сценарий С3, R-04/R-11/R-12).
//
// Как это устроено (для понимания поведения):
//  * Черновик расчёта правится и сохраняется БЕЗ связи — сохранение ложится в офлайн-очередь целиком
//    (все позиции), сервер примет его, только если с тех пор никто не сохранил свою версию; иначе — конфликт,
//    который решает пользователь (денежные данные никогда не сливаются молча).
//  * «Зафиксировать» — только онлайн и только ГИП проекта: сервер создаёт неизменяемую версию vN и новую
//    ревизию проектного итога. Официальный итог проекта считается ТОЛЬКО из зафиксированных версий.
//  * Итог черновика и сравнение с зафиксированной версией считаются локально теми же правилами, что и на
//    сервере (vector.core.calc), поэтому видны и офлайн.
//  * Неизвестная цена ≠ 0: такие позиции не входят в сумму, а итог помечается как неполный.
ColumnLayout {
    id: kc
    property var kit: null
    property int compareNo: 0                  // 0 — сравнивать с последней зафиксированной версией
    property bool showUnchanged: false
    readonly property var v: kit ? backend.kitCalc(kit.id, compareNo, backend.dataRevision) : ({})
    readonly property var pv: v.preview || ({ rows: [] })
    readonly property var rows: pv.rows || []
    readonly property bool hasDraft: v.has_draft === true
    spacing: 10
    onKitChanged: { compareNo = 0; showUnchanged = false }

    // ---------- сводка: итог черновика, состояние, действия
    VCard {
        Layout.fillWidth: true
        implicitHeight: sumCol.implicitHeight + 32
        ColumnLayout {
            id: sumCol
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 16
            spacing: 10
            RowLayout {
                Layout.fillWidth: true; spacing: 14
                Rectangle { width: 46; height: 46; radius: 12; color: Theme.primarySoft; Layout.alignment: Qt.AlignTop
                    Icon { anchors.centerIn: parent; name: "calculator"; size: 22; color: Theme.primary } }
                ColumnLayout { spacing: 2; Layout.fillWidth: true
                    Text { text: "Итог черновика"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
                    Text { objectName: "calcDraftTotal"
                           text: kc.hasDraft ? kc.pv.total_money_label : "Расчёта ещё нет"
                           color: Theme.text
                           font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold
                           font.features: { "tnum": 1 }; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    // Неполнота итога — рядом с суммой и цветом предупреждения: «неизвестно» не выдаётся за ноль.
                    Text { objectName: "calcDraftIncomplete"; visible: kc.hasDraft && kc.pv.unknown_price_count > 0
                           text: kc.pv.unknown_price_count + " поз. без цены — итог неполный"
                           color: Theme.warning; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.DemiBold }
                    Flow { Layout.fillWidth: true; spacing: 6
                        VChip { visible: kc.hasDraft; text: "черновик · версия " + kc.v.draft_version; tone: "neutral" }
                        VChip { visible: kc.v.pending === true; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                        VChip { visible: !!kc.v.last; text: kc.v.last ? "зафиксировано: " + kc.v.last.label : ""; tone: "success"; iconName: "lock" }
                        VChip { visible: !kc.v.last; text: "не зафиксирован"; tone: "neutral" }
                        VChip { visible: !!kc.v.last && kc.v.has_draft && !kc.v.same_as_last && kc.v.has_changes_vs_base
                                text: "есть правки после фиксации"; tone: "info"; iconName: "git-compare" }
                        Text { visible: kc.v.updated_label !== "" && kc.v.updated_label !== undefined
                               text: "сохранено " + (kc.v.updated_label || "") + (kc.v.updated_by_name ? " · " + kc.v.updated_by_name : "")
                               color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; topPadding: 4 } }
                }
                ColumnLayout { spacing: 6; Layout.alignment: Qt.AlignTop
                    VButton { objectName: "calcEditButton"
                              text: kc.v.has_draft ? "Изменить позиции" : "Начать расчёт"; iconName: kc.v.has_draft ? "pencil" : "plus"
                              kind: kc.v.has_draft ? "secondary" : "primary"; Layout.fillWidth: true
                              enabled: kc.v.can_edit !== false
                              onClicked: editor.openFor(kc.kit, kc.v) }
                    VButton { objectName: "calcFreezeButton"
                              visible: kc.v.can_freeze === true
                              text: "Зафиксировать"; iconName: "lock"; kind: "primary"; Layout.fillWidth: true
                              enabled: kc.freezeBlocker === ""
                              onClicked: freeze.ask() }
                }
            }
            // Почему нельзя зафиксировать — объясняем, а не просто гасим кнопку.
            Banner { objectName: "calcFreezeHint"; visible: kc.v.can_freeze === true && kc.freezeBlocker !== ""; tone: "info"
                     text: kc.freezeBlocker; Layout.fillWidth: true }
            Banner { visible: kc.hasDraft && kc.pv.unknown_price_count > 0; tone: "warning"; Layout.fillWidth: true
                     text: "Позиции без цены не входят в сумму (это не ноль). Фиксировать неполный расчёт можно — пометка «неполный» сохранится в версии и в итоге проекта." }
            Banner { visible: kc.hasDraft && kc.pv.valid === false; tone: "danger"; Layout.fillWidth: true
                     text: "В сохранённом черновике есть ошибки: " + (kc.pv.first_problem || "") }
            Text { visible: !!kc.v.note; text: "Примечание: " + (kc.v.note || ""); color: Theme.textMuted; font.family: Theme.family
                   font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
        }
    }
    readonly property string freezeBlocker:
        !v.has_draft ? "Сначала сохраните расчёт." :
        v.pending ? "Есть неотправленные правки расчёта — дождитесь синхронизации, чтобы зафиксировать именно то, что видит сервер." :
        !backend.online ? "Фиксация выполняется только при связи с сервером." :
        rows.length === 0 ? "В расчёте нет позиций." :
        v.same_as_last ? "Черновик не менялся после последней фиксации (" + (v.last ? v.last.label : "") + ")." :
        pv.valid === false ? "Исправьте ошибки в позициях." : ""

    // ---------- позиции черновика
    SectionTitle { visible: kc.hasDraft; text: "Позиции (" + kc.rows.length + ")"; iconName: "list-checks"; Layout.topMargin: 4 }
    CalcTable {
        visible: kc.hasDraft && kc.rows.length > 0
        Layout.fillWidth: true
        rows: kc.rows
    }
    EmptyState {
        visible: !kc.v.has_draft
        Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 6; Layout.bottomMargin: 6
        iconName: "calculator"; title: "Расчёт комплекта ещё не начат"
        text: "Добавьте позиции: наименование, единицу, количество и цену. Цену можно оставить пустой — она будет считаться неизвестной, а итог — неполным."
    }

    // ---------- сравнение с зафиксированной версией
    RowLayout {
        visible: kc.hasDraft && (kc.v.versions || []).length > 0
        Layout.fillWidth: true; Layout.topMargin: 6; spacing: 10
        SectionTitle { text: "Сравнение"; iconName: "git-compare" }
        Item { Layout.fillWidth: true }
        Text { text: "с версией:"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS }
        // Выбор версии для сравнения — чипы (новые слева); по умолчанию последняя зафиксированная.
        Repeater {
            model: kc.v.versions || []
            delegate: Rectangle {
                required property var modelData
                readonly property bool on: modelData.version_no === kc.v.compare_no
                objectName: "calcCompare-" + modelData.label
                width: vl.implicitWidth + 18; height: 26; radius: 13
                color: on ? Theme.primarySoft : Theme.surface; border.color: on ? Theme.primary : Theme.border
                activeFocusOnTab: true
                Accessible.role: Accessible.RadioButton; Accessible.name: "Сравнить с версией " + modelData.label
                Keys.onSpacePressed: kc.compareNo = modelData.version_no
                Text { id: vl; anchors.centerIn: parent; text: modelData.label; color: parent.on ? Theme.primary : Theme.textMuted
                       font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: parent.on ? Font.DemiBold : Font.Normal }
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: kc.compareNo = modelData.version_no }
            }
        }
        Text { text: kc.showUnchanged ? "скрыть без изменений" : "показать без изменений"; color: Theme.primary
               font.family: Theme.family; font.pixelSize: Theme.fsS; leftPadding: 8
               MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: kc.showUnchanged = !kc.showUnchanged } }
    }
    Text { visible: kc.hasDraft; objectName: "calcDiffSummary"; text: kc.v.diff_summary || ""
           color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
    Repeater {
        model: kc.v.has_draft && kc.v.last ? (kc.v.diff_rows || []).filter(r => kc.showUnchanged || r.kind !== "unchanged") : []
        delegate: Rectangle {
            required property var modelData
            readonly property var tn: Theme.tone(modelData.kind === "added" ? "success" : modelData.kind === "removed" ? "danger"
                                                : modelData.kind === "changed" ? "info" : "neutral")
            Layout.fillWidth: true; implicitHeight: dRow.implicitHeight + 14; radius: Theme.radiusS
            color: Theme.surface; border.color: Theme.border
            Rectangle { width: 3; height: parent.height - 10; radius: 2; anchors.left: parent.left; anchors.leftMargin: 5
                        anchors.verticalCenter: parent.verticalCenter; color: tn.fg }
            RowLayout { id: dRow; anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 10; spacing: 10
                VChip { text: modelData.kind_label; tone: modelData.kind === "added" ? "success" : modelData.kind === "removed" ? "danger"
                                                          : modelData.kind === "changed" ? "info" : "neutral" }
                ColumnLayout { spacing: 1; Layout.fillWidth: true
                    Text { text: (modelData.code ? modelData.code + " · " : "") + (modelData.name || "")
                           color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; elide: Text.ElideRight; Layout.fillWidth: true
                           font.strikeout: modelData.kind === "removed" }
                    Text { visible: modelData.changes_label !== ""; text: modelData.changes_label || ""
                           color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true } }
            }
        }
    }

    // ---------- зафиксированные версии
    SectionTitle { visible: (kc.v.versions || []).length > 0; text: "Зафиксированные версии"; iconName: "history"; Layout.topMargin: 6 }
    Repeater {
        model: kc.v.versions || []
        delegate: VCard {
            id: verCard
            required property var modelData
            property bool expanded: false
            Layout.fillWidth: true; implicitHeight: verCol.implicitHeight + 22
            ColumnLayout { id: verCol; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 11; spacing: 6
                RowLayout { Layout.fillWidth: true; spacing: 10
                    Rectangle { width: 38; height: 26; radius: 7; color: Theme.successSoft
                        Text { anchors.centerIn: parent; text: modelData.label; color: Theme.success; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.Bold } }
                    ColumnLayout { spacing: 0; Layout.fillWidth: true
                        Text { text: modelData.total_label; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold
                               font.features: { "tnum": 1 }; elide: Text.ElideRight; Layout.fillWidth: true }
                        Text { text: modelData.frozen_label + " · " + modelData.author_name + " · " + modelData.line_count + " поз. · «" + modelData.reason + "»"
                               color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; elide: Text.ElideRight; Layout.fillWidth: true } }
                    Text { text: "SHA-256 " + modelData.hash_short + "…"; color: Theme.textFaint; font.family: "monospace"; font.pixelSize: Theme.fsXs }
                    VButton { kind: "ghost"; compact: true; iconName: verCard.expanded ? "chevron-down" : "chevron-right"
                              Accessible.name: (verCard.expanded ? "Скрыть позиции версии " : "Показать позиции версии ") + modelData.label
                              onClicked: verCard.expanded = !verCard.expanded }
                }
                CalcTable { visible: verCard.expanded; Layout.fillWidth: true
                            rows: verCard.expanded ? backend.calcPreview(verCard.modelData.lines, verCard.modelData.currency || "").rows : [] }
            }
        }
    }
    HistoryList { objectType: "calc_draft"; objectId: kc.kit ? kc.kit.id : "" }

    // ---------- формы
    CalcEditor { id: editor }

    FormDialog {
        id: freeze
        objectName: "calcFreezeDialog"
        title: "Зафиксировать расчёт комплекта"
        subtitle: kc.kit ? kc.kit.code + " — будет создана неизменяемая версия v" + (((kc.v.versions || []).length) + 1)
                           + " и новая ревизия итога проекта." : ""
        note: "Итог: " + (kc.pv.total_label || "") + ". Официальный итог проекта считается только из зафиксированных версий; прежние версии и ревизии сохраняются в истории."
        confirmText: "Зафиксировать"
        canConfirm: fReason.text.trim() !== "" && backend.online
        function ask() { fReason.text = ""; open() }
        onConfirmed: fCmd.run("calc.freeze", { id: kc.kit.id, reason: fReason.text.trim() }, kc.v.draft_version)
        VField { id: fReason; objectName: "calcFreezeReason"; label: "Основание фиксации"; required: true; placeholder: "Расчёт к ТКП № … / согласовано с заказчиком" }
    }
    Cmd { id: fCmd; dialog: freeze }
}
