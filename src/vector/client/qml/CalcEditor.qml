import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Редактор позиций расчёта комплекта: широкое модальное окно с таблицей полей ввода.
//
//  * Рабочая копия — ListModel: правка поля не пересоздаёт строки (фокус и курсор не прыгают).
//  * Каждое изменение сразу проверяется локально (backend.calcPreview → vector.core.calc, те же правила, что
//    на сервере): сумма строки, замечание у строки, итог. Числа — строками, без float: «1 234,5» допустимо.
//  * Пустая цена = «неизвестна» (не ноль): позиция не входит в сумму, итог помечается неполным.
//  * «Сохранить» отправляет все позиции целиком через очередь (можно без связи). Если кто-то успел сохранить
//    свою версию раньше — будет конфликт на экране «Синхронизация», ничего не перезапишется молча.
//  * «Вставить из Excel»: строки, скопированные из таблицы (колонки через Tab): обозначение, наименование,
//    ед., количество, цена. Ничего не округляется; строки с ошибками остаются видны с замечаниями.
Popup {
    id: ed
    objectName: "calcEditor"
    property var kit: null
    property int editRev: 0                 // счётчик правок — пересчитывает проверку
    property bool working: false
    property string error: ""
    property string tag: "calc-" + Math.random().toString(36).slice(2)
    // Проверка рабочей копии; byId — замечания и суммы по line_id (пустые строки-заготовки в проверку не идут).
    readonly property var pv: {
        editRev
        var r = visible ? backend.calcPreview(collect(), curField.text) : ({ rows: [] })
        var m = {}
        for (var i = 0; i < (r.rows || []).length; i++) m[r.rows[i].line_id] = r.rows[i]
        r.byId = m
        return r
    }
    modal: true; focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(1180, (Overlay.overlay ? Overlay.overlay.width : 1200) - 48)
    height: Math.min(820, (Overlay.overlay ? Overlay.overlay.height : 860) - 48)
    padding: 0
    closePolicy: Popup.CloseOnEscape
    Overlay.modal: Rectangle { color: Theme.scrim }
    background: Rectangle { color: Theme.surface; radius: Theme.radiusL; border.color: Theme.border }

    ListModel { id: lm }

    function openFor(k, v) {
        kit = k; error = ""; working = false
        lm.clear()
        var src = v.lines || []
        for (var i = 0; i < src.length; i++) lm.append(rowOf(src[i]))
        if (lm.count === 0) lm.append(rowOf({}))
        curField.text = v.currency || ""
        noteField.text = v.note || ""
        editRev++
        open()
    }
    function rowOf(x) {
        // ListModel хранит строки; отсутствующее значение = "" (при отправке станет null = «не задано»).
        return { line_id: x.line_id || backend.newId(), code: x.code || "", name: x.name || "", unit: x.unit || "",
                 qty: x.qty !== undefined && x.qty !== null ? plain(x.qty) : "",
                 unit_price: x.unit_price !== undefined && x.unit_price !== null ? plain(x.unit_price) : "", note: x.note || "" }
    }
    // «12.5000» → «12.5»: показываем введённое число без хвостовых нулей канонической формы.
    function plain(s) { s = String(s); return s.indexOf(".") >= 0 ? s.replace(/0+$/, "").replace(/\.$/, "") : s }
    function setCell(i, key, value) { lm.setProperty(i, key, value); editRev++ }
    // Позиции для проверки и сохранения. Полностью пустые строки (заготовки) пропускаются.
    function collect() {
        var out = []
        for (var i = 0; i < lm.count; i++) {
            var r = lm.get(i)
            if (!r.code && !r.name && !r.unit && !r.qty && !r.unit_price && !r.note) continue
            out.push({ line_id: r.line_id, code: r.code, name: r.name, unit: r.unit, qty: r.qty, unit_price: r.unit_price, note: r.note })
        }
        return out
    }
    function pasteRows(text) {
        var added = 0
        var lines = String(text || "").split(/\r?\n/)
        for (var i = 0; i < lines.length; i++) {
            if (lines[i].trim() === "") continue
            var c = lines[i].split("\t")
            if (c.length < 2) c = ["", lines[i]]
            lm.append({ line_id: backend.newId(), code: (c[0] || "").trim(), name: (c[1] || "").trim(), unit: (c[2] || "").trim(),
                        qty: (c[3] || "").trim(), unit_price: (c[4] || "").trim(), note: (c[5] || "").trim() })
            added++
        }
        // Пустая строка-заготовка в начале больше не нужна.
        if (added > 0 && lm.count > added) {
            var f = lm.get(0)
            if (!f.name && !f.qty && !f.unit_price && !f.code) lm.remove(0)
        }
        editRev++
        return added
    }
    function save() {
        working = true; error = ""
        backend.saveCalcDraft(tag, kit.id, curField.text, noteField.text, collect())
    }
    Connections {
        target: backend
        function onCommandDone(t, r) {
            if (t !== ed.tag) return
            ed.working = false
            if (r.status === "accepted" || r.status === "queued") ed.close()
            else ed.error = r.message_ru || "Расчёт не сохранён."
        }
    }
    // Невидимый буфер для чтения текста из системного буфера обмена (QML не даёт прямого доступа к нему).
    TextEdit { id: clip; visible: false }

    contentItem: ColumnLayout {
        spacing: 0
        // ---------- заголовок
        RowLayout {
            Layout.fillWidth: true; Layout.margins: Theme.padL; Layout.bottomMargin: 10; spacing: 12
            Rectangle { width: 40; height: 40; radius: 11; color: Theme.primarySoft
                Icon { anchors.centerIn: parent; name: "calculator"; size: 20; color: Theme.primary } }
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { text: "Расчёт комплекта " + (ed.kit ? ed.kit.code : ""); color: Theme.text; font.family: Theme.family
                       font.pixelSize: Theme.fsL; font.weight: Font.DemiBold }
                Text { text: "Пустая цена — «неизвестна» (не ноль). Сумма = количество × цена, без округления. Валюта не подставляется сама. Можно сохранять без связи."
                       color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
            VButton { kind: "ghost"; iconName: "x"; compact: true; onClicked: ed.close(); Accessible.name: "Закрыть" }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; spacing: 12
            VField { id: curField; objectName: "calcCurrency"; label: "Валюта (ISO)"; placeholder: "RUB"; Layout.preferredWidth: 130; Layout.fillWidth: false
                     input.onTextEdited: ed.editRev++ }
            VField { id: noteField; label: "Примечание к расчёту"; placeholder: "Необязательно"; Layout.fillWidth: true }
        }
        // ---------- шапка таблицы
        Rectangle {
            Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 12
            implicitHeight: 30; radius: Theme.radiusS; color: Theme.surfaceAlt
            RowLayout { anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 6
                Repeater {
                    model: [{ t: "№", w: 28 }, { t: "Обозначение", w: 120 }, { t: "Наименование *", w: -1 }, { t: "Ед. *", w: 64 },
                            { t: "Кол-во *", w: 96 }, { t: "Цена за ед.", w: 120 }, { t: "Сумма", w: 120 }, { t: "", w: 34 }]
                    delegate: Text { required property var modelData
                        text: modelData.t; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.DemiBold
                        Layout.preferredWidth: modelData.w > 0 ? modelData.w : -1; Layout.fillWidth: modelData.w < 0 }
                }
            }
        }
        // ---------- строки
        ListView {
            id: lv
            objectName: "calcEditorRows"
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL
            clip: true; spacing: 2; model: lm
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                id: rowItem
                required property int index
                required property var model
                readonly property var info: ed.pv.byId[model.line_id] || ({})
                width: lv.width; implicitHeight: rowCol.implicitHeight + 8; radius: Theme.radiusS
                color: info.problem ? Theme.dangerSoft : "transparent"
                ColumnLayout { id: rowCol; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                               anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 2
                    RowLayout { Layout.fillWidth: true; spacing: 6
                        Text { text: rowItem.index + 1; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.preferredWidth: 28 }
                        CalcCell { Layout.preferredWidth: 120; placeholder: "—"; text: rowItem.model.code
                                   Accessible.name: "Обозначение " + (rowItem.index + 1)
                                   onTextEdited: ed.setCell(rowItem.index, "code", text) }
                        CalcCell { Layout.fillWidth: true; placeholder: "Наименование позиции"; text: rowItem.model.name
                                   Accessible.name: "Наименование " + (rowItem.index + 1)
                                   onTextEdited: ed.setCell(rowItem.index, "name", text) }
                        CalcCell { Layout.preferredWidth: 64; placeholder: "шт"; text: rowItem.model.unit
                                   Accessible.name: "Единица " + (rowItem.index + 1)
                                   onTextEdited: ed.setCell(rowItem.index, "unit", text) }
                        CalcCell { Layout.preferredWidth: 96; num: true; placeholder: "0"; text: rowItem.model.qty
                                   Accessible.name: "Количество " + (rowItem.index + 1)
                                   onTextEdited: ed.setCell(rowItem.index, "qty", text) }
                        CalcCell { Layout.preferredWidth: 120; num: true; placeholder: "неизвестна"; text: rowItem.model.unit_price
                                   Accessible.name: "Цена " + (rowItem.index + 1)
                                   onTextEdited: ed.setCell(rowItem.index, "unit_price", text) }
                        Text { text: rowItem.info.amount_label || "—"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs
                               font.weight: Font.DemiBold; font.features: { "tnum": 1 }; horizontalAlignment: Text.AlignRight
                               elide: Text.ElideLeft; Layout.preferredWidth: 120 }
                        VButton { kind: "ghost"; compact: true; iconName: "trash-2"; Layout.preferredWidth: 34
                                  Accessible.name: "Удалить позицию " + (rowItem.index + 1)
                                  onClicked: { lm.remove(rowItem.index); ed.editRev++ } }
                    }
                    Text { visible: !!rowItem.info.problem; text: rowItem.info.problem || ""; color: Theme.danger; font.family: Theme.family
                           font.pixelSize: Theme.fsXs; leftPadding: 34; wrapMode: Text.Wrap; Layout.fillWidth: true }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.topMargin: 8; spacing: 8
            VButton { objectName: "calcAddLine"; text: "Добавить позицию"; iconName: "plus"; kind: "soft"; compact: true
                      onClicked: { lm.append(ed.rowOf({})); ed.editRev++; lv.positionViewAtEnd() } }
            VButton { text: "Вставить из Excel"; iconName: "copy"; kind: "ghost"; compact: true
                      onClicked: { clip.text = ""; clip.paste(); var n = ed.pasteRows(clip.text)
                                   if (n === 0) ed.error = "В буфере обмена нет строк таблицы. Скопируйте в Excel колонки: обозначение, наименование, ед., количество, цена." } }
            Item { Layout.fillWidth: true }
            Text { text: (ed.pv.rows || []).length + " поз."; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS }
        }
        // ---------- итог и ошибки
        Rectangle { Layout.fillWidth: true; Layout.topMargin: 12; height: 1; color: Theme.border }
        RowLayout {
            Layout.fillWidth: true; Layout.margins: 16; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; spacing: 12
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { objectName: "calcEditorTotal"; text: "Итог: " + (ed.pv.total_label || ""); color: Theme.text; font.family: Theme.family
                       font.pixelSize: Theme.fsM; font.weight: Font.Bold; font.features: { "tnum": 1 }; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { visible: ed.pv.problem_count > 0
                       text: "Замечаний: " + ed.pv.problem_count + ". " + ((ed.pv.general_problems || []).join(" ") || "Исправьте строки, отмеченные красным.")
                       color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { visible: ed.error !== ""; text: ed.error; color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsS
                       wrapMode: Text.Wrap; Layout.fillWidth: true }
            }
            VButton { text: "Отмена"; kind: "ghost"; onClicked: ed.close() }
            VButton { objectName: "calcSave"; text: ed.working ? "Сохранение…" : "Сохранить расчёт"; kind: "primary"; iconName: "check"
                      enabled: !ed.working && ed.pv.valid === true; onClicked: ed.save() }
        }
    }
}
