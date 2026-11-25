import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Редактор офисного документа (Ф1.4, D-95): модальное окно поверх рабочей копии .vsheetx/.vdocx.
//
//  * Таблица: сетка A1-ячеек (ввод значения или формулы «=…»; после правки — пересчёт в ядре, Kahn).
//    Ячейка с формулой показывает вычисленное значение; при фокусе — саму формулу (как в Excel).
//  * Текст: список абзацев (заголовки стилем). Таблицы внутри документа — пометкой-строкой (Ф1.4: абзацы).
//  * «Сохранить» — новая неизменяемая версия документа (онлайн). Если коллега успел сохранить свою —
//    честный конфликт, ничего не перезаписывается; пользователь решает сам.
//  * Без связи кнопка «Сохранить» недоступна — файл версии не поставить в офлайн-очередь (контракт).
Popup {
    id: ed
    objectName: "officeEditor"
    property string tag: "office-" + Math.random().toString(36).slice(2)
    property var doc: null                // карточка документа из backend.documents
    property int versionNo: 0             // открытая версия файла
    property string kind: ""              // sheet | doc
    property bool working: false
    property string error: ""
    // Состояние представления (таблица): размеры и ячейки (после загрузки и правок).
    property var view: ({})
    // Текст: ListModel строк-абзацев (block/style/level/align/text).
    property int editRev: 0               // счётчик правок — пересчёт привязок

    modal: true; focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(1140, (Overlay.overlay ? Overlay.overlay.width : 1140) - 48)
    height: Math.min(800, (Overlay.overlay ? Overlay.overlay.height : 860) - 48)
    padding: 0
    closePolicy: Popup.CloseOnEscape
    Overlay.modal: Rectangle { color: Theme.scrim }
    background: Rectangle { color: Theme.surface; radius: Theme.radiusL; border.color: Theme.border }

    ListModel { id: paras }               // абзацы текста (kind=doc)

    function openFor(d, verNo) {
        doc = d; versionNo = verNo; error = ""; working = true; view = ({}); paras.clear(); editRev++
        open()
        backend.officeLoad(tag, d.id, verNo)
    }
    function save() {
        working = true; error = ""
        backend.officeSave(tag, doc ? doc.version : 0)
    }

    Connections {
        target: backend
        function onQueryDone(t, r) {
            if (t !== ed.tag) return
            ed.working = false
            if (r.status !== "accepted") { ed.error = r.message_ru || "Документ не открылся."; return }
            ed.kind = r.kind
            if (r.kind === "sheet") ed.view = r.view
            else { paras.clear(); for (var i = 0; i < r.rows.length; i++) paras.append(r.rows[i]) }
            ed.editRev++
        }
        function onCommandDone(t, r) {
            if (t !== ed.tag) return
            ed.working = false
            if (r.status === "accepted") ed.close()
            else ed.error = r.message_ru || "Не сохранено."
        }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 16; spacing: 10
        RowLayout { Layout.fillWidth: true; spacing: 10
            Rectangle { width: 36; height: 36; radius: 10; color: Theme.primarySoft
                Icon { anchors.centerIn: parent; name: ed.kind === "sheet" ? "layers" : "file-text"; size: 18; color: Theme.primary } }
            ColumnLayout { spacing: 1; Layout.fillWidth: true
                Text { text: (doc ? doc.code + " · " + doc.title : "Документ") + " — версия " + ed.versionNo
                       color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM; font.weight: Font.Bold
                       elide: Text.ElideRight; Layout.fillWidth: true }
                Text { text: ed.kind === "sheet" ? "Таблица ВЕКТОР: значения и формулы «=…» (пересчёт сразу)"
                                                 : "Текстовый документ ВЕКТОР: абзацы и заголовки"
                       color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS } }
            VChip { visible: ed.editRev > 0 && ed.kind !== ""; text: "Изменено"; tone: "warning"; iconName: "pencil" }
        }
        Text { visible: ed.error !== ""; text: ed.error; color: Theme.danger
               font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Banner { visible: !backend.online; Layout.fillWidth: true; tone: "warning"
                 text: "Нет связи с сервером: править можно, но сохранить новой версией — только при связи." }

        // ===================== ТАБЛИЦА (kind=sheet): заголовки колонок + сетка =====================
        ColumnLayout {
            visible: ed.kind === "sheet"; Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
            Row { Layout.fillWidth: true; spacing: 0
                Rectangle { width: 44; height: 26; color: "transparent" }      // угол (номера строк)
                Repeater {
                    model: ed.view.cols || 0
                    delegate: Rectangle { width: 110; height: 26; color: Theme.surfaceAlt; border.color: Theme.border
                        Text { anchors.centerIn: parent; text: String.fromCharCode(65 + index) // A…L (сетка ≤ 12)
                               color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs } }
                }
            }
            Flickable {
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentHeight: gridCol.height
                Column { id: gridCol; width: parent.width; spacing: 0
                    Repeater {
                        model: ed.view.rows || 0
                        delegate: Row { spacing: 0
                            property int rowIdx: index
                            Rectangle { width: 44; height: 30; color: Theme.surfaceAlt; border.color: Theme.border
                                Text { anchors.centerIn: parent; text: rowIdx + 1
                                       color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs } }
                            Repeater {
                                model: ed.view.cols || 0
                                delegate: Rectangle {
                                    property int colIdx: index
                                    property string addr: String.fromCharCode(65 + colIdx) + (rowIdx + 1)
                                    property var cell: (ed.editRev, ed.view.cells ? ed.view.cells[addr] : null)
                                    width: 110; height: 30; color: Theme.surface; border.color: Theme.border
                                    TextInput {
                                        anchors.fill: parent; anchors.margins: 4
                                        verticalAlignment: Text.AlignVCenter; clip: true
                                        color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS
                                        // Формула показывается при фокусе, иначе — вычисленное значение.
                                        text: activeFocus ? (cell && cell.formula ? cell.formula : (cell ? cell.text : ""))
                                                          : (cell ? (cell.formula ? cell.computed : cell.text) : "")
                                        font.weight: cell && cell.bold ? Font.Bold : Font.Normal
                                        onEditingFinished: {
                                            if (!activeFocus) return
                                            var entered = text
                                            var cur = cell ? (cell.formula || cell.text) : ""
                                            if (entered === cur) return
                                            var c = backend.officeEditCell(addr, entered)
                                            if (c && c.status !== "rejected") { ed.view.cells[addr] = c; ed.editRev++ }
                                        }
                                        Accessible.name: "Ячейка " + addr
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // ===================== ТЕКСТ (kind=doc): абзацы =====================
        Flickable {
            visible: ed.kind === "doc"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            contentHeight: docCol.height
            ColumnLayout { id: docCol; width: parent.width; spacing: 6
                Repeater {
                    model: paras
                    delegate: ColumnLayout { Layout.fillWidth: true; spacing: 2
                        Text { visible: model.block === "table-note"; text: model.text
                               color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.italic: true }
                        RowLayout {
                            visible: model.block === "para"; Layout.fillWidth: true; spacing: 6
                            ComboBox {
                                Layout.preferredWidth: 118; model: ["текст", "заголовок", "подзаголовок"]
                                currentIndex: model.style === "heading" ? (model.level > 1 ? 2 : 1) : 0
                                onActivated: {
                                    var st = currentIndex === 0 ? "normal" : "heading"
                                    var lv = currentIndex === 2 ? 2 : 1
                                    var r = backend.officeEditPara(index, textField.text, st)
                                    if (r.status !== "rejected") { paras.setProperty(index, "style", st); paras.setProperty(index, "level", lv) }
                                }
                                Accessible.name: "Стиль абзаца " + (index + 1)
                            }
                            TextField {
                                id: textField; Layout.fillWidth: true; text: model.text
                                font.family: Theme.family; font.pixelSize: model.style === "heading" ? Theme.fsM : Theme.fsS
                                font.weight: model.style === "heading" ? Font.Bold : Font.Normal
                                color: Theme.text
                                onEditingFinished: { var r = backend.officeEditPara(index, text, model.style); if (r.status !== "rejected") paras.setProperty(index, "text", text) }
                                Accessible.name: "Абзац " + (index + 1)
                            }
                        }
                    }
                }
                VButton { text: "Добавить абзац"; kind: "ghost"; compact: true; iconName: "plus"
                          onClicked: paras.append({ block: "para", style: "normal", level: 1, align: "left", text: "" }) }
            }
        }

        // ===================== панель действий =====================
        RowLayout { Layout.fillWidth: true; spacing: 10
            Text { visible: ed.working; text: "Подождите…"; color: Theme.textMuted
                   font.family: Theme.family; font.pixelSize: Theme.fsS }
            Item { Layout.fillWidth: true }
            VButton { text: "Отмена"; kind: "ghost"; onClicked: ed.close() }
            VButton {
                text: "Сохранить новой версией"; kind: "primary"; iconName: "check"; enabled: backend.online && !ed.working && ed.kind !== ""
                Accessible.description: !backend.online ? "Сохранение версии файла — только при связи с сервером." : ""
                onClicked: ed.save()
            }
        }
    }
}
