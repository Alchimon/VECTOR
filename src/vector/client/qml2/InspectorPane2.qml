import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
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

    // Данные вкладки «Версии» для комплекта — зафиксированные версии расчёта (слот kitCalc,
    // перевычисляется по backend.dataRevision). Объявлены на корне: у вложенных элементов свойства
    // не видны как insp.*.
    readonly property var kitCalcData: kind === "kit" && object
                                       ? backend.kitCalc(object.id, 0, backend.dataRevision) : ({})
    readonly property var kitCalcVersions: (kitCalcData.versions || [])

    // Мини-предпросмотр содержимого офисного документа (концепт A, D-103): первые строки файла
    // без открытия редактора. Слот backend.documentPreview — асинхронный, ответ — queryDone.
    property var previewLines: []
    property string previewTitle: ""
    property string previewNote: ""
    readonly property bool previewWanted: kind === "document" && object !== null
                                          && (object.current_version_no || 0) > 0
                                          && /\.(vsheetx|vdocx)$/.test(((object.versions || [])[0] || {}).file_name || "")
    function reloadPreview() {
        previewLines = []; previewTitle = ""; previewNote = ""
        if (kind !== "document" || !object || (object.versions || []).length === 0) return
        if ((object.current_version_no || 0) === 0) { previewNote = "Файла ещё нет — предпросмотр появится после загрузки версии."; return }
        if (!previewWanted) { previewNote = "Предпросмотр содержимого — для документов ВЕКТОР (.vsheetx/.vdocx); внешний файл откроется после скачивания."; return }
        if (!backend.online) { previewNote = "Предпросмотр требует связи с сервером."; return }
        backend.documentPreview("inspPreview", object.id, object.current_version_no)
    }
    Connections {
        target: backend
        function onQueryDone(t, res) {
            if (t !== "inspPreview") return
            if (res.status === "accepted" && insp.object && res.document_id === insp.object.id) {
                insp.previewTitle = res.title || ""
                insp.previewLines = res.lines || []
                insp.previewNote = insp.previewLines.length > 0 ? "" : "Документ пуст."
            } else if (res.status !== "accepted") {
                insp.previewNote = res.message_ru || "Предпросмотр недоступен."
                insp.previewLines = []
            }
        }
    }

    color: Theme2.surface
    implicitWidth: Theme2.inspectorW
    Rectangle { anchors.left: parent.left; width: 1; height: parent.height; color: Theme2.border }

    readonly property var selKits: kind === "project" && object ? backend.kits.filter(k => k.project_id === object.id) : []
    // ---------- вкладка «Обсуждение» документа (D-118): замечания ревизий РКД из клиентского кэша.
    property var discussItems: []
    property int discussOpen: 0
    function reloadDiscussion() {
        if (kind === "document" && object) {
            var r = backend.documentDiscussion(object.id)
            discussItems = r.items || []
            discussOpen = r.open_count || 0
        } else { discussItems = []; discussOpen = 0 }
    }
    onObjectChanged: { reloadPreview(); reloadDiscussion() }
    Connections { target: backend; function onDataChanged() { insp.reloadDiscussion() } }

    // Последние версии документов проекта (D-116): плоский список по всем комплектам, новые сверху, до 10.
    readonly property var projDocVersions: {
        if (kind !== "project" || object === null) return []
        var out = []
        selDocs.forEach(function(d) {
            (d.versions || []).forEach(function(v) {
                var o = Object.assign({}, v); o.doc_code = d.code; out.push(o)
            })
        })
        out.sort(function(a, b) { return (a.created_at || "") > (b.created_at || "") ? -1 : 1 })
        return out.slice(0, 10)
    }

    // Команда проекта выбранного объекта (для формы «Новая задача»): участники назначений проекта.
    readonly property var selDocs: kind === "kit" && object ? backend.documents.filter(d => d.kit_id === object.id)
                                   : kind === "project" && object ? backend.documents.filter(d => (backend.kits.find(k => k.id === d.kit_id) || {}).project_id === object.id) : []
    readonly property var teamOptions: {
        if (object === null) return [{ id: "", label: "Не назначен" }]
        var pid = kind === "project" ? object.id
                : kind === "kit" ? object.project_id
                : kind === "document" ? ((backend.kits.find(function(k) { return k.id === object.kit_id }) || {}).project_id || "") : ""
        var seen = {}, out = [{ id: "", label: "Не назначен" }]
        backend.assignments.forEach(function(a) {
            if (a.project_id === pid && !seen[a.user_id]) { seen[a.user_id] = true; out.push({ id: a.user_id, label: a.user_name }) }
        })
        return out
    }

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
                        VChip { visible: insp.object !== null && (insp.object.status_label || "") !== ""; text: insp.object ? insp.object.status_label || "" : ""; tone: "info" }
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

            // ---------- предпросмотр содержимого (концепт A, D-103): первые строки файла без редактора
            ColumnLayout { objectName: "docPreview"
                           visible: insp.kind === "document" && insp.object !== null
                                    && (insp.previewLines.length > 0 || insp.previewNote !== "")
                           Layout.fillWidth: true; spacing: 6
                Text { text: "Предпросмотр содержимого" + (insp.previewTitle !== "" ? " · " + insp.previewTitle : "")
                       color: Theme2.textMuted; font.family: Theme2.family
                       font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0; font.weight: Font.DemiBold
                       font.pixelSize: Theme2.fsXs }
                Repeater {
                    model: insp.previewLines
                    delegate: Text { text: modelData; color: Theme2.text; font.family: Theme2.family
                                     font.pixelSize: Theme2.fsXs; Layout.fillWidth: true; elide: Text.ElideRight
                                     maximumLineCount: 1 }
                }
                Text { visible: insp.previewNote !== ""; text: insp.previewNote
                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                       wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }

            // ---------- действия (крупные, на виду)
            ColumnLayout { visible: insp.object !== null; Layout.fillWidth: true; spacing: 8; Layout.topMargin: 8
                VButton { objectName: "inspOpen"; text: insp.kind === "document" ? "Открыть в редакторе" : "Открыть"
                          kind: "primary"; iconName: insp.kind === "document" ? "pencil" : "folder-kanban"
                          Layout.fillWidth: true; implicitHeight: Theme2.compact ? 36 : 44
                          onClicked: insp.kind === "document" ? insp.editDocument(insp.object)
                                     : insp.openRequested(insp.kind, insp.object ? insp.object.id : "") }
                VButton { objectName: "inspNewVersion"
                          visible: insp.kind === "document" && insp.object !== null
                          text: (insp.object && (insp.object.current_version_no || 0) > 0 ? "Новая версия" : "Загрузить файл")
                          kind: "soft"; iconName: "upload"; Layout.fillWidth: true
                          tip: "Файл проверяется по SHA-256; в офлайн-очередь не ставится — нужна связь"
                          enabled: backend.online && insp.object && insp.object.pending !== true && (insp.object.version || 0) > 0
                          onClicked: verForm2.ask(insp.object) }
                VButton { visible: insp.kind === "project" && insp.object && insp.object.tender_id
                          text: "Тендер-основание"; kind: "soft"; iconName: "gavel"; Layout.fillWidth: true
                          onClicked: insp.goPage("tenders", insp.object.tender_id) }
                VButton { objectName: "inspNewDoc"
                          visible: insp.kind === "kit" && insp.object !== null
                          text: "Новый документ"; kind: "soft"; iconName: "file-text"; Layout.fillWidth: true
                          tip: "Карточка документа в этом комплекте (файл добавляется версиями)"
                          onClicked: docForm2.ask(insp.object) }
                VButton { objectName: "inspEditCard"
                          visible: (insp.kind === "kit" || insp.kind === "document") && insp.object !== null
                          text: "Изменить карточку"; kind: "soft"; iconName: "pencil"; Layout.fillWidth: true
                          tip: "Обозначение и название; версия карточки защищает от перезаписи чужих правок"
                          onClicked: editForm2.ask(insp.kind, insp.object) }
                VButton { objectName: "inspDownload"
                          visible: insp.kind === "document" && insp.object !== null && (insp.object.current_version_no || 0) > 0
                          text: "Скачать версию " + (insp.object ? (insp.object.current_version_no || "") : "")
                          iconName: "download"; Layout.fillWidth: true
                          tip: "Файл сохраняется после сверки SHA-256"
                          onClicked: saveDlg2.open() }
                VButton { objectName: "inspNewTask"
                          visible: insp.kind === "document" && insp.object !== null
                          text: "Новая задача"; kind: "soft"; iconName: "list-checks"; Layout.fillWidth: true
                          tip: "Задача по этому документу в комплект — исполнителю и со сроком"
                          enabled: backend.online
                          onClicked: taskForm2.ask(insp.object) }
            }

            Text { visible: insp.object !== null; Layout.topMargin: 4
                   text: insp.kind === "project" ? "Двойной щелчок по карточке в списке тоже открывает проект во вкладке."
                       : insp.kind === "document" ? "Документ откроется во вкладке редактора — можно держать несколько документов рядом."
                       : "Комплект откроется во вкладке — задачи, документы и расчёт рядом."
                   color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                   wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }   // конец вкладки «Свойства»

            // ================= ВКЛАДКА «ВЕРСИИ» (концепт B, D-101/D-102): настоящий таймлайн версий.
            // Документ — файловые версии из кэша (номер, автор, дата, размер, короткий хэш, отметка
            // «текущая»); комплект — зафиксированные версии расчёта (итог, автор, дата, хэш);
            // проект — версия карточки + сводка версий документов.
            ColumnLayout { visible: insp.inspectorTab === "versions" && insp.object !== null
                           Layout.fillWidth: true; spacing: Theme2.gap

                // ---------- документ: таймлайн файловых версий
                Repeater {
                    model: insp.kind === "document" && insp.object ? insp.object.versions || [] : []
                    delegate: RowLayout {
                        Layout.fillWidth: true; spacing: 10
                        Rectangle { width: 10; height: 10; radius: 5; Layout.alignment: Qt.AlignTop; Layout.topMargin: 4
                                    color: modelData.version_no === (insp.object.current_version_no || 0)
                                           ? Theme2.success : Theme2.textFaint }
                        ColumnLayout { spacing: 2; Layout.fillWidth: true
                            RowLayout { spacing: 8
                                Text { text: "Версия " + modelData.version_no; color: Theme2.text
                                       font.family: Theme2.family; font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold }
                                VChip { visible: modelData.version_no === (insp.object.current_version_no || 0)
                                        text: "Текущая"; tone: "success" }
                                Item { Layout.fillWidth: true }
                                Text { text: modelData.size_label || ""; color: Theme2.textFaint
                                       font.family: Theme2.family; font.pixelSize: Theme2.fsXs } }
                            Text { text: (modelData.author_name || "—") + " · " + (modelData.created_label || "")
                                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                            Text { visible: (modelData.comment || "") !== ""
                                   text: "«" + (modelData.comment || "") + "»"
                                   color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                                   font.italic: true; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            Text { visible: (modelData.sha256 || "") !== ""
                                   text: "SHA-256: " + (modelData.sha256 || "").substring(0, 16) + "…"
                                   color: Theme2.textFaint; font.family: Theme2.mono; font.pixelSize: Theme2.fsXs }
                        }
                    }
                }
                Text { visible: insp.kind === "document" && insp.object && (insp.object.versions || []).length === 0
                       text: "Файла ещё нет — версия появится после первой загрузки или сохранения в редакторе."
                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                       wrapMode: Text.WordWrap; Layout.fillWidth: true }

                // ---------- комплект: версии расчёта
                Repeater {
                    model: insp.kind === "kit" ? insp.kitCalcVersions : []
                    delegate: RowLayout {
                        Layout.fillWidth: true; spacing: 10
                        Rectangle { width: 10; height: 10; radius: 5; Layout.alignment: Qt.AlignTop; Layout.topMargin: 4
                                    color: index === 0 ? Theme2.success : Theme2.textFaint }
                        ColumnLayout { spacing: 2; Layout.fillWidth: true
                            RowLayout { spacing: 8
                                Text { text: "Расчёт " + (modelData.label || ""); color: Theme2.text
                                       font.family: Theme2.family; font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold }
                                Item { Layout.fillWidth: true }
                                Text { text: modelData.total_label || ""; color: Theme2.textMuted
                                       font.family: Theme2.mono; font.pixelSize: Theme2.fsXs } }
                            Text { text: (modelData.author_name || "—") + " · " + (modelData.frozen_label || "")
                                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                            Text { visible: (modelData.reason || "") !== ""
                                   text: "Основание: " + (modelData.reason || "")
                                   color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                                   wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            Text { visible: (modelData.hash_short || "") !== ""
                                   text: "Хэш: " + (modelData.hash_short || "")
                                   color: Theme2.textFaint; font.family: Theme2.mono; font.pixelSize: Theme2.fsXs } }
                    }
                }
                Text { visible: insp.kind === "kit" && insp.kitCalcVersions.length === 0
                       text: "Фиксаций расчёта ещё не было — зафиксировать может ГИП проекта (вкладка «Расчёт» комплекта)."
                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                       wrapMode: Text.WordWrap; Layout.fillWidth: true }

                // ---------- проект: версия карточки + сводка по документам
                ColumnLayout { visible: insp.kind === "project" && insp.object !== null
                               Layout.fillWidth: true; spacing: 6
                    RowLayout { spacing: 10
                        Rectangle { width: 10; height: 10; radius: 5; color: Theme2.success }
                        Text { text: "Карточка проекта v" + (insp.object ? insp.object.version : "")
                               color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold } }
                    KeyValue { k: "Версий документов всего"
                               v: insp.selDocs.reduce(function(n, d) { return n + (d.versions || []).length }, 0) }
                    KeyValue { k: "Документов с файлом"; v: insp.selDocs.filter(function(d) { return (d.current_version_no || 0) > 0 }).length }
                    Text { text: "Версии конкретного документа — в его инспекторе: выберите документ в дереве проекта слева."
                           color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                           wrapMode: Text.WordWrap; Layout.fillWidth: true }
                }

                // ---------- проект: последние версии документов проекта (D-116) — живая хронология
                Text { visible: insp.kind === "project" && insp.projDocVersions.length > 0; Layout.topMargin: 8
                       text: "Последние версии документов"
                       color: Theme2.textMuted; font.family: Theme2.family
                       font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0; font.weight: Font.DemiBold
                       font.pixelSize: Theme2.fsXs }
                Repeater {
                    model: insp.kind === "project" ? insp.projDocVersions : []
                    delegate: RowLayout {
                        Layout.fillWidth: true; spacing: 10
                        Rectangle { width: 10; height: 10; radius: 5; Layout.alignment: Qt.AlignTop; Layout.topMargin: 4
                                    color: index === 0 ? Theme2.success : Theme2.textFaint }
                        ColumnLayout { spacing: 2; Layout.fillWidth: true
                            RowLayout { spacing: 8
                                Text { text: modelData.doc_code + " · версия " + modelData.version_no; color: Theme2.text
                                       font.family: Theme2.family; font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold }
                                Item { Layout.fillWidth: true }
                                Text { text: modelData.size_label || ""; color: Theme2.textFaint
                                       font.family: Theme2.mono; font.pixelSize: Theme2.fsXs } }
                            Text { text: (modelData.author_name || "—") + " · " + (modelData.created_label || "")
                                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                            Text { visible: (modelData.comment || "") !== ""
                                   text: "«" + modelData.comment + "»"
                                   color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                                   font.italic: true; wrapMode: Text.WordWrap; Layout.fillWidth: true } }
                    }
                }
            }

            // ================= ВКЛАДКА «ОБСУЖДЕНИЕ» (концепт B, D-101): замечания и ответы.
            // Для документа — настоящие замечания ревизий РКД (D-118); проект/комплект — заглушка.
            ColumnLayout { visible: insp.inspectorTab === "discuss" && insp.object !== null
                           Layout.fillWidth: true; spacing: Theme2.gap

                // ---------- документ: замечания ревизий
                Repeater {
                    model: insp.kind === "document" ? insp.discussItems : []
                    delegate: Rectangle {
                        Layout.fillWidth: true; radius: Theme2.radiusS
                        color: modelData.open ? Qt.alpha(Theme2.warning, 0.07) : Theme2.surfaceAlt
                        border.color: modelData.open ? Qt.alpha(Theme2.warning, 0.5) : "transparent"
                        implicitHeight: cCol.implicitHeight + 16
                        ColumnLayout { id: cCol; anchors { left: parent.left; right: parent.right; top: parent.top; margins: 8; leftMargin: 12; rightMargin: 12 } spacing: 4
                            RowLayout { spacing: 8
                                VChip { text: modelData.source_label + (modelData.external_ref !== "" ? " · " + modelData.external_ref : "")
                                        tone: modelData.source === "customer" ? "warning" : "info" }
                                VChip { visible: modelData.open; text: "Открыто"; tone: "warning" }
                                Item { Layout.fillWidth: true }
                                Text { text: "Рев. " + modelData.revision_label + (modelData.page !== "" ? " · лист " + modelData.page : "")
                                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs } }
                            Text { text: modelData.text; color: Theme2.text; font.family: Theme2.family
                                   font.pixelSize: Theme2.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            Text { text: modelData.author_name + " · " + modelData.created_label
                                   color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                            ColumnLayout { visible: modelData.response_text !== ""; spacing: 2; Layout.topMargin: 2
                                Rectangle { Layout.fillWidth: true; height: 1; color: Theme2.border }
                                Text { text: "Ответ: " + modelData.response_text; color: Theme2.textMuted
                                       font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                                       wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                Text { visible: modelData.response_author !== ""
                                       text: modelData.response_author + " · " + modelData.response_label
                                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs } }
                        }
                    }
                }

                // ---------- пустые состояния
                ColumnLayout { visible: insp.kind === "document" && insp.discussItems.length === 0
                               Layout.fillWidth: true; spacing: 6; Layout.topMargin: 24
                    Icon { name: "message-circle"; size: 30; color: Theme2.textFaint; Layout.alignment: Qt.AlignHCenter }
                    Text { text: "Замечаний нет"; color: Theme2.textMuted; font.family: Theme2.family
                           font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold; Layout.alignment: Qt.AlignHCenter }
                    Text { text: "Замечания появляются в ходе проверки ревизий (внутренние) и от заказчика (на отправленную ревизию) — в контуре РКД."
                           color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                           wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; Layout.fillWidth: true }
                }
                ColumnLayout { visible: insp.kind !== "document"
                               Layout.fillWidth: true; spacing: 6; Layout.topMargin: 24
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

    // ---------- диалог «Новая версия» (D-102): тот же контур, что и в классике (KitWork):
    // файл выбирается с диска, SHA-256 считает клиент, сервер пересчитывает; в очередь не ставится.
    FormDialog {
        id: verForm2
        objectName: "inspVersionForm"
        property var doc: null
        property alias path: vPath2.text
        property string tag: "insp-ver-" + Math.random().toString(36).slice(2)
        title: "Новая версия документа"
        subtitle: doc ? doc.code + " · " + doc.title + ((doc.current_version_no || 0) > 0 ? " — станет версией " + (doc.current_version_no + 1)
                                                                                          : " — первая версия") : ""
        note: "Файл проверяется по SHA-256 при загрузке и при каждом скачивании. Если коллега успеет загрузить свою версию раньше, вы увидите конфликт — ничего не перезапишется."
        confirmText: "Загрузить"
        canConfirm: vPath2.text.trim() !== "" && backend.online
        function ask(d) { doc = d; vPath2.text = ""; vComment2.text = ""; open() }
        onConfirmed: { working = true; error = ""; backend.addDocumentVersion(tag, doc.id, vPath2.text.trim(), vComment2.text, doc.version) }
        RowLayout { Layout.fillWidth: true; spacing: 8
            VField { id: vPath2; label: "Файл"; required: true; readOnly: true; placeholder: "Выберите файл…"; Layout.fillWidth: true }
            VButton { text: "Выбрать…"; iconName: "paperclip"; Layout.alignment: Qt.AlignBottom; onClicked: openDlg2.open() } }
        VField { id: vComment2; label: "Комментарий к версии"; placeholder: "Что изменилось" }
        Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== verForm2.tag) return
                verForm2.working = false
                if (r.status === "accepted") verForm2.close()
                else verForm2.error = r.message_ru || "Версия не создана."
            }
        }
    }

    // ---------- диалог «Новая задача» (D-109): задача по документу — в его комплект, с привязкой к документу.
    FormDialog {
        id: taskForm2
        objectName: "inspTaskForm"
        property var doc: null
        property string tag: "insp-task-" + Math.random().toString(36).slice(2)
        title: "Новая задача по документу"
        subtitle: doc ? doc.code + " · " + doc.title : ""
        note: "Задача попадёт в комплект документа и будет видна исполнителю в «Задачах» комплекта и на «Главной»."
        confirmText: "Поставить задачу"
        canConfirm: tTitle2.text.trim() !== "" && backend.online
        function ask(d) { doc = d; tTitle2.text = ""; tDue2.text = ""; tWho2.currentIndex = 0; open() }
        onConfirmed: { working = true; error = ""
            backend.execute(tag, "task.create", ({ id: backend.newId(), kit_id: doc.kit_id,
                title: tTitle2.text.trim(), description: "",
                assignee_user_id: tWho2.currentValue || null, due_date: tDue2.text.trim() || null,
                document_id: doc.id }), 0) }
        VField { id: tTitle2; label: "Что нужно сделать"; required: true; placeholder: "Например: сверить перечень сигналов" }
        VSelect { id: tWho2; label: "Исполнитель"; model: insp.teamOptions }
        VField { id: tDue2; label: "Срок"; placeholder: "ГГГГ-ММ-ДД" }
        Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== taskForm2.tag) return
                taskForm2.working = false
                if (r.status === "accepted") taskForm2.close()
                else taskForm2.error = r.message_ru || "Задача не создана."
            }
        }
    }

    // ---------- диалог «Новый документ» (D-112): карточка в выбранном комплекте, файл — версиями.
    FormDialog {
        id: docForm2
        objectName: "inspDocForm"
        property var kit: null
        property string tag: "insp-doc-" + Math.random().toString(36).slice(2)
        title: "Новый документ"
        subtitle: kit ? "Комплект " + kit.code + " · " + kit.title : ""
        note: "Создаётся карточка документа: обозначение уникально в проекте. Файл добавляется командой «Новая версия»."
        confirmText: "Создать"
        canConfirm: dCode2.text.trim() !== "" && dTitle2.text.trim() !== "" && backend.online
        function ask(k) { kit = k; dCode2.text = ""; dTitle2.text = ""; open() }
        onConfirmed: { working = true; error = ""
            backend.execute(tag, "document.create", ({ id: backend.newId(), kit_id: kit.id,
                code: dCode2.text.trim(), title: dTitle2.text.trim() }), 0) }
        VField { id: dCode2; label: "Обозначение"; required: true; placeholder: "Например: АТХ-004" }
        VField { id: dTitle2; label: "Название"; required: true; placeholder: "Например: Спецификация" }
        Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== docForm2.tag) return
                docForm2.working = false
                if (r.status === "accepted") docForm2.close()
                else docForm2.error = r.message_ru || "Документ не создан."
            }
        }
    }

    // ---------- диалог «Изменить карточку» (D-114): комплект или документ, с ожидаемой версией.
    FormDialog {
        id: editForm2
        objectName: "inspEditForm"
        property string editKind: ""
        property var obj: null
        property string tag: "insp-edit-" + Math.random().toString(36).slice(2)
        title: editKind === "kit" ? "Изменить комплект" : "Изменить документ"
        subtitle: obj ? obj.code + " · карточка v" + obj.version : ""
        note: "Если коллега успеет изменить карточку раньше, вы увидите конфликт версий — чужие правки не перезапишутся."
        confirmText: "Сохранить"
        canConfirm: eCode2.text.trim() !== "" && eTitle2.text.trim() !== "" && backend.online
        function ask(k, o) { editKind = k; obj = o; eCode2.text = o.code; eTitle2.text = o.title; open() }
        onConfirmed: { working = true; error = ""
            backend.execute(tag, editKind === "kit" ? "kit.update" : "document.update",
                            ({ id: obj.id, code: eCode2.text.trim(), title: eTitle2.text.trim() }), obj.version) }
        VField { id: eCode2; label: editForm2.editKind === "kit" ? "Код комплекта" : "Обозначение"; required: true }
        VField { id: eTitle2; label: "Название"; required: true }
        Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== editForm2.tag) return
                editForm2.working = false
                if (r.status === "accepted") editForm2.close()
                else editForm2.error = r.message_ru || "Карточка не изменена."
            }
        }
    }

    FileDialog {
        id: openDlg2
        title: "Файл новой версии"
        fileMode: FileDialog.OpenFile
        onAccepted: vPath2.text = selectedFile.toString()
    }

    FileDialog {
        id: saveDlg2
        title: "Сохранить файл версии"
        fileMode: FileDialog.SaveFile
        currentFile: insp.object ? backend.suggestedFileName(insp.object.id, insp.object.current_version_no || 1) : ""
        onAccepted: backend.downloadDocumentVersion("insp-dl-" + Date.now(), insp.object.id,
                                                    insp.object.current_version_no || 1, selectedFile.toString())
    }
}
