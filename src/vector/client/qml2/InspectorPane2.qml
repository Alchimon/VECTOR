import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../qml"

// Инспектор справа (интерфейс 2.0, D-98/D-101): предпросмотр и свойства выбранного объекта — то, чего
// не хватало в классике («нет окон предпросмотра»). Три вкладки по утверждённому концепту B:
// «Свойства», «Версии», «Обсуждение». Поддерживает проект, комплект и документ; пустой выбор —
// вежливая подсказка. Действия крупные и видимые (принцип «контекстные действия на виду»).
Rectangle {
    id: insp
    objectName: "inspector2"
    property var object: null            // объект кэша backend (project/kit/document)
    property string kind: ""             // project | kit | document
    property var team: []                // команда проекта (для kind=project)
    property string inspectorTab: "props"   // props | versions | discuss
    signal openRequested(string kind, string id)
    signal goPage(string page, string id)
    signal editDocument(var doc)

    color: Theme2.surface
    implicitWidth: Theme2.inspectorW
    Rectangle { anchors.left: parent.left; width: 1; height: parent.height; color: Theme2.border }

    readonly property var selKits: kind === "project" && object ? backend.kits.filter(k => k.project_id === object.id) : []
    readonly property var selDocs: kind === "kit" && object ? backend.documents.filter(d => d.kit_id === object.id)
                                   : kind === "project" && object ? backend.documents.filter(d => (backend.kits.find(k => k.id === d.kit_id) || {}).project_id === object.id) : []

    Flickable {
        anchors.fill: parent
        contentWidth: width; contentHeight: body.implicitHeight + Theme2.padL
        clip: true; boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        ColumnLayout {
            id: body
            width: parent.width - Theme2.padL * 2
            x: Theme2.padL; y: Theme2.padL
            spacing: Theme2.gap + 4

            // ---------- пустое состояние
            ColumnLayout { visible: insp.object === null; Layout.fillWidth: true; spacing: Theme2.gap; Layout.topMargin: 40
                Icon { name: "eye"; size: 34; color: Theme2.textFaint; Layout.alignment: Qt.AlignHCenter }
                Text { text: "Предпросмотр"; color: Theme2.textMuted; font.family: Theme2.family
                       font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0; font.weight: Font.DemiBold
                       font.pixelSize: Theme2.fsM; Layout.alignment: Qt.AlignHCenter }
                Text { text: "Выберите объект в списке — здесь появятся его свойства, версии и действия."
                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                       wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; Layout.fillWidth: true }
            }

            // ---------- шапка объекта
            RowLayout { visible: insp.object !== null; Layout.fillWidth: true; spacing: Theme2.gap
                Rectangle { width: Theme2.compact ? 42 : 54; height: width; radius: Theme2.radius
                    color: insp.kind === "project" ? Theme2.infoSoft : insp.kind === "kit" ? Theme2.primarySoft : Theme2.warningSoft
                    Icon { anchors.centerIn: parent; size: Theme2.compact ? 20 : 26
                           name: insp.kind === "project" ? "folder-kanban" : insp.kind === "kit" ? "layers" : "file-text"
                           color: insp.kind === "project" ? Theme2.info : insp.kind === "kit" ? Theme2.primary : Theme2.warning } }
                ColumnLayout { spacing: 3; Layout.fillWidth: true
                    Text { objectName: "inspTitle"; text: insp.object ? (insp.object.code ? insp.object.code + " · " : "") + insp.object.title : ""
                           color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsM
                           font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    RowLayout { spacing: 6
                        VChip { visible: insp.object && insp.object.status_label; text: insp.object ? insp.object.status_label || "" : ""; tone: "info" }
                        VChip { visible: insp.object && insp.object.pending === true; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                    }
                }
            }

            // ---------- вкладки инспектора (концепт B, D-101): Свойства / Версии / Обсуждение
            RowLayout { visible: insp.object !== null; Layout.fillWidth: true; spacing: 4
                Repeater {
                    model: [
                        { id: "props",    label: "Свойства" },
                        { id: "versions", label: "Версии" },
                        { id: "discuss",  label: "Обсуждение" }
                    ]
                    delegate: AbstractButton {
                        id: itab
                        property bool active: insp.inspectorTab === modelData.id
                        Accessible.name: modelData.label
                        Layout.fillWidth: true; implicitHeight: Theme2.compact ? 30 : 34
                        hoverEnabled: true
                        onClicked: insp.inspectorTab = modelData.id
                        background: Rectangle {
                            radius: Theme2.radiusS
                            color: itab.active ? Theme2.primarySoft : itab.hovered ? Theme2.hover : "transparent"
                        }
                        contentItem: Text { text: modelData.label; horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter; color: itab.active ? Theme2.primary : Theme2.textMuted
                            font.family: Theme2.family; font.pixelSize: Theme2.fsS
                            font.weight: itab.active ? Font.DemiBold : Font.Normal }
                    }
                }
            }

            // ================= ВКЛАДКА «СВОЙСТВА» =================
            ColumnLayout { visible: insp.inspectorTab === "props" && insp.object !== null
                           Layout.fillWidth: true; spacing: Theme2.gap + 4
            // ---------- свойства проекта
            ColumnLayout { visible: insp.kind === "project" && insp.object !== null; Layout.fillWidth: true; spacing: 6
                KeyValue { k: "Тендер-основание"; v: insp.object ? insp.object.tender_title || "—" : "" }
                KeyValue { k: "Версия карточки"; v: insp.object ? "v" + insp.object.version : "" }
                KeyValue { k: "Комплектов"; v: insp.selKits.length }
                KeyValue { k: "Документов"; v: insp.selDocs.length }
            }

            // ---------- команда проекта (аватарки)
            ColumnLayout { visible: insp.kind === "project" && insp.team.length > 0; Layout.fillWidth: true; spacing: 6
                Text { text: "Команда"; color: Theme2.textMuted; font.family: Theme2.family
                       font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0; font.weight: Font.DemiBold
                       font.pixelSize: Theme2.fsXs }
                Flow { Layout.fillWidth: true; spacing: 6
                    Repeater {
                        model: insp.team
                        delegate: Rectangle {
                            height: 30; width: tmprow.implicitWidth + 16; radius: 15
                            color: Theme2.surfaceAlt; border.color: Theme2.border
                            RowLayout { id: tmprow; anchors.centerIn: parent; spacing: 6
                                Rectangle { width: 20; height: 20; radius: 10; color: Theme2.primarySoft
                                    Text { anchors.centerIn: parent; text: (modelData.user_name || "?").substring(0, 1)
                                           color: Theme2.primary; font.family: Theme2.family; font.pixelSize: 10; font.weight: Font.Bold } }
                                Text { text: modelData.user_name + " · " + modelData.role_label
                                       color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs } }
                        }
                    }
                }
            }

            // ---------- комплекты проекта (мини-предпросмотр)
            ColumnLayout { visible: insp.kind === "project" && insp.selKits.length > 0; Layout.fillWidth: true; spacing: 6
                Text { text: "Комплекты"; color: Theme2.textMuted; font.family: Theme2.family
                       font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0; font.weight: Font.DemiBold
                       font.pixelSize: Theme2.fsXs }
                Repeater {
                    model: insp.selKits
                    delegate: Rectangle {
                        Layout.fillWidth: true; implicitHeight: Theme2.compact ? 34 : 40; radius: Theme2.radiusS
                        color: kitMa.containsMouse ? Theme2.hover : Theme2.surfaceAlt
                        border.color: Theme2.border
                        RowLayout { anchors { fill: parent; leftMargin: 10; rightMargin: 10 } spacing: 8
                            Icon { name: "layers"; size: 14; color: Theme2.primary }
                            Text { text: modelData.code + " · " + modelData.title; color: Theme2.text
                                   font.family: Theme2.family; font.pixelSize: Theme2.fsS
                                   Layout.fillWidth: true; elide: Text.ElideRight }
                            Text { text: modelData.open_task_count + "/" + modelData.task_count + " задач"
                                   color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs } }
                        MouseArea { id: kitMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onClicked: insp.openRequested("kit", modelData.id) }
                    }
                }
            }

            // ---------- свойства комплекта
            ColumnLayout { visible: insp.kind === "kit" && insp.object !== null; Layout.fillWidth: true; spacing: 6
                KeyValue { k: "Задачи"; v: insp.object ? insp.object.open_task_count + " откр. из " + insp.object.task_count : "" }
                KeyValue { k: "Документы"; v: insp.object ? "" + insp.object.doc_count : "" }
                KeyValue { k: "Расчёт"; v: insp.object ? insp.object.calc_label || "—" : "" }
                KeyValue { k: "Версия карточки"; v: insp.object ? "v" + insp.object.version : "" }
            }

            // ---------- свойства документа
            ColumnLayout { visible: insp.kind === "document" && insp.object !== null; Layout.fillWidth: true; spacing: 6
                KeyValue { k: "Код"; v: insp.object ? insp.object.code : "" }
                KeyValue { k: "Тип"; v: insp.object ? (insp.object.kind === "sheet" ? "Таблица (ВЕКТОР)" : insp.object.kind === "doc" ? "Текст (ВЕКТОР)" : "Файл") : "" }
                KeyValue { k: "Версия карточки"; v: insp.object ? "v" + insp.object.version : "" }
            }

            // ---------- действия (крупные, на виду)
            ColumnLayout { visible: insp.object !== null; Layout.fillWidth: true; spacing: 8; Layout.topMargin: 8
                VButton { objectName: "inspOpen"; text: insp.kind === "document" ? "Открыть в редакторе" : "Открыть"
                          kind: "primary"; iconName: insp.kind === "document" ? "pencil" : "folder-kanban"
                          Layout.fillWidth: true; implicitHeight: Theme2.compact ? 36 : 44
                          onClicked: insp.kind === "document" ? insp.editDocument(insp.object)
                                     : insp.openRequested(insp.kind, insp.object ? insp.object.id : "") }
                VButton { visible: insp.kind === "project" && insp.object && insp.object.tender_id
                          text: "Тендер-основание"; kind: "soft"; iconName: "gavel"; Layout.fillWidth: true
                          onClicked: insp.goPage("tenders", insp.object.tender_id) }
                VButton { visible: insp.kind === "document" && insp.object && insp.object.kind
                          text: "Печать / PDF"; iconName: "download"; Layout.fillWidth: true
                          tip: "Сохранить документ в PDF (работает и без связи)"
                          onClicked: insp.editDocument(insp.object) }
            }

            Text { visible: insp.object !== null; Layout.topMargin: 4
                   text: insp.kind === "project" ? "Двойной щелчок по карточке в списке тоже открывает проект во вкладке."
                       : insp.kind === "document" ? "Документ откроется во вкладке редактора — можно держать несколько документов рядом."
                       : "Комплект откроется во вкладке — задачи, документы и расчёт рядом."
                   color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                   wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }   // конец вкладки «Свойства»

            // ================= ВКЛАДКА «ВЕРСИИ» (концепт B, D-101): история версий объекта.
            // Сейчас карточка знает только текущую версию (номер + метка времени сохранения);
            // полный журнал ревизий появится с пакетом v3 (объектное хранилище версий).
            ColumnLayout { visible: insp.inspectorTab === "versions" && insp.object !== null
                           Layout.fillWidth: true; spacing: Theme2.gap
                Repeater {
                    model: insp.object !== null ? [insp.object] : []
                    delegate: RowLayout {
                        Layout.fillWidth: true; spacing: 10
                        Rectangle { width: 10; height: 10; radius: 5; color: Theme2.success }
                        ColumnLayout { spacing: 2; Layout.fillWidth: true
                            Text { text: "Версия v" + modelData.version; color: Theme2.text
                                   font.family: Theme2.family; font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold }
                            Text { text: "Текущая версия карточки объекта. Журнал изменений появится в ближайшем обновлении."
                                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                                   wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        }
                    }
                }
                Text { text: "История версий — основа утверждённого концепта: здесь будут таймлайн изменений, авторы и сравнение версий."
                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                       wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }

            // ================= ВКЛАДКА «ОБСУЖДЕНИЕ» (концепт B, D-101): комментарии к объекту.
            ColumnLayout { visible: insp.inspectorTab === "discuss" && insp.object !== null
                           Layout.fillWidth: true; spacing: Theme2.gap
                ColumnLayout { Layout.fillWidth: true; spacing: 6; Layout.topMargin: 24
                    Icon { name: "message-circle"; size: 30; color: Theme2.textFaint; Layout.alignment: Qt.AlignHCenter }
                    Text { text: "Пока пусто"; color: Theme2.textMuted; font.family: Theme2.family
                           font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold; Layout.alignment: Qt.AlignHCenter }
                    Text { text: "Комментарии к объекту появятся с модулем согласований: отметки, решения и вопросы по месту — не в почте."
                           color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                           wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; Layout.fillWidth: true }
                }
            }
        }
    }
}
