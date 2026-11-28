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

    // Ревизии РКД документа (D-126): статус согласования, автор, открытые замечания, действия
    // контура (переходы по контракту docrev). Кэш-клиент, без запроса к серверу.
    property var docRevisions: []
    property bool docRevCanCreate: false
    function reloadRevisions() {
        if (kind === "document" && object) {
            var rd = backend.documentRevisions(object.id)
            docRevisions = rd.items || []
            // can_create одинаков для всего документа (по роли в проекте), не зависит от ревизии.
            docRevCanCreate = backend.revisionActions(docRevisions.length > 0 ? docRevisions[0].id : "").can_create === true
        } else { docRevisions = []; docRevCanCreate = false }
    }

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
    // Ширина инспектора: из настроек (D-135, перетаскивание разделителя), 0 — по умолчанию из плотности.
    implicitWidth: backend.inspectorWidth > 0 ? backend.inspectorWidth : Theme2.inspectorW
    Rectangle { anchors.left: parent.left; width: 1; height: parent.height; color: Theme2.border }
    // Разделитель «потянуть мышью» (D-135): вертикальная полоса у левого края; двойной щелчок — сброс.
    MouseArea {
        objectName: "inspectorResizer"
        anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
        width: 7; hoverEnabled: true
        cursorShape: Qt.SplitHCursor
        property real startX: 0
        property real startW: 0
        onPressed: function (m) { startX = m.x + insp.x; startW = insp.width }
        onPositionChanged: function (m) {
            if (!pressed) return
            backend.inspectorWidth = startW + (startX - (m.x + insp.x))   // тянем влево — шире
        }
        onDoubleClicked: backend.resetLayout()
        Rectangle { anchors.centerIn: parent; width: 1; height: parent.height; color: parent.containsMouse || parent.pressed ? Theme2.borderStrong : "transparent" }
        Accessible.name: "Ширина панели предпросмотра: потяните мышью; двойной щелчок — сбросить раскладку"
    }

    readonly property var selKits: kind === "project" && object ? backend.kits.filter(k => k.project_id === object.id) : []
    // ---------- вкладка «Обсуждение» документа (D-118): замечания ревизий РКД из клиентского кэша.
    property var discussItems: []
    property int discussOpen: 0
    // ---------- общие комментарии объекта с @упоминаниями (P3-2) — для любого вида объекта.
    property var commentItems: []
    property bool commentCanAdd: false
    property bool commentCanModerate: false
    property string replyToId: ""
    property string replyToName: ""
    property string mentionQuery: ""
    function reloadDiscussion() {
        if (kind === "kit" && object) {
            var rk = backend.kitDiscussion(object.id)
            discussItems = rk.items || []
            discussOpen = rk.open_count || 0
        } else if (kind === "document" && object) {
            var r = backend.documentDiscussion(object.id)
            discussItems = r.items || []
            discussOpen = r.open_count || 0
        } else { discussItems = []; discussOpen = 0 }
        if (object) {
            var oc = backend.objectDiscussion(kind, object.id)
            commentItems = oc.items || []
            commentCanAdd = oc.can_add === true
            commentCanModerate = oc.can_moderate === true
        } else { commentItems = []; commentCanAdd = false; commentCanModerate = false }
    }
    // Автодополнение @упоминания: фрагмент после последнего «@» фильтрует справочник людей.
    function updateMention() {
        var t = commentInput.text, i = t.lastIndexOf("@")
        if (i < 0) { mentionQuery = ""; return }
        var frag = t.substring(i + 1)
        mentionQuery = (frag.length <= 30 && frag.indexOf("\n") < 0 && frag.indexOf("@") < 0) ? frag : ""
    }
    function applyMention(name) {
        var t = commentInput.text, i = t.lastIndexOf("@")
        commentInput.text = t.substring(0, i) + "@" + name + " "
        mentionQuery = ""
        commentInput.forceActiveFocus()
    }
    readonly property var mentionOptions: {
        if (mentionQuery === "") return []
        var q = mentionQuery.toLowerCase(), out = []
        backend.people.forEach(function(p) {
            if (out.length < 5 && p.display_name.toLowerCase().indexOf(q) >= 0) out.push(p)
        })
        return out
    }
    onObjectChanged: { reloadPreview(); reloadDiscussion(); reloadRevisions() }
    Connections { target: backend; function onDataChanged() { insp.reloadDiscussion(); insp.reloadRevisions() } }

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

                // ---------- документ: ревизии РКД (D-126) — статус согласования у заказчика.
                // Шифр ревизии задаёт инженер («REV A», «01»), он не совпадает с номером версии файла;
                // переходы — по контракту docrev (кнопки видны по роли; решит сервер).
                RowLayout { visible: insp.kind === "document" && insp.object !== null; Layout.fillWidth: true
                    Text { text: "Ревизии РКД"; color: Theme2.textMuted; font.family: Theme2.family
                           font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0; font.weight: Font.DemiBold
                           font.pixelSize: Theme2.fsXs; Layout.fillWidth: true }
                    VButton { objectName: "inspNewRevision"; visible: insp.docRevCanCreate
                              text: "Новая ревизия"; iconName: "plus"; implicitHeight: 28
                              tip: "Шифр ревизии (например «02», «REV A») — для заказчика; внутри она ссылается на версию файла"
                              enabled: backend.online
                              onClicked: revForm2.ask() }
                }
                Repeater {
                    model: insp.kind === "document" ? insp.docRevisions : []
                    delegate: Rectangle {
                        Layout.fillWidth: true; radius: Theme2.radiusS
                        color: Theme2.surfaceAlt
                        implicitHeight: revCol.implicitHeight + 16
                        ColumnLayout { id: revCol
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 8; leftMargin: 12; rightMargin: 12 }
                            spacing: 4
                            RowLayout { spacing: 8
                                Text { text: "Ревизия " + modelData.label; color: Theme2.text
                                       font.family: Theme2.family; font.pixelSize: Theme2.fsS; font.weight: Font.Bold }
                                VChip { text: modelData.status_label
                                        tone: modelData.status === "approved" ? "success"
                                            : modelData.status === "sent" ? "info"
                                            : modelData.status === "customer_comments" || modelData.status === "returned_to_engineer" ? "warning"
                                            : "neutral" }
                                Item { Layout.fillWidth: true }
                                Text { visible: modelData.open_comments > 0
                                       text: modelData.open_comments + " откр. замеч."; color: Theme2.warning
                                       font.family: Theme2.family; font.pixelSize: Theme2.fsXs } }
                            Text { text: modelData.author_name + " · " + modelData.created_label
                                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                            Text { visible: (modelData.note || "") !== ""; text: modelData.note
                                   color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                                   wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            Flow { spacing: 6; Layout.fillWidth: true
                                Repeater {
                                    model: backend.revisionActions(modelData.id).items
                                    delegate: VButton {
                                        objectName: "revAction"
                                        text: modelData.label; kind: modelData.kind; implicitHeight: 28
                                        enabled: backend.online
                                        tip: modelData.needs_reason ? "Потребуется причина (основание)" : ""
                                        onClicked: revTransForm2.ask(insp.docRevisions[index], modelData.to,
                                                                     modelData.label, modelData.needs_reason,
                                                                     backend.revisionActions(insp.docRevisions[index].id).version || 1)
                                    }
                                }
                            }
                        }
                    }
                }
                Text { visible: insp.kind === "document" && insp.object && insp.docRevisions.length === 0
                       text: "Ревизий РКД ещё нет — шифр ревизии («01», «REV A») создаётся для отправки заказчику и не совпадает с номером версии файла."
                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                       wrapMode: Text.WordWrap; Layout.fillWidth: true }

                // ---------- документ: таймлайн файловых версий
                Text { visible: insp.kind === "document" && insp.object && insp.docRevisions.length > 0; Layout.topMargin: 6
                       text: "Версии файла"
                       color: Theme2.textMuted; font.family: Theme2.family
                       font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0; font.weight: Font.DemiBold
                       font.pixelSize: Theme2.fsXs }
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
                    model: (insp.kind === "document" || insp.kind === "kit") ? insp.discussItems : []
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
                                Text { text: (modelData.doc_code ? modelData.doc_code + " · " : "") + "Рев. " + modelData.revision_label + (modelData.page !== "" ? " · лист " + modelData.page : "")
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

                // ---------- пустое состояние замечаний РКД
                ColumnLayout { visible: (insp.kind === "document" || insp.kind === "kit") && insp.discussItems.length === 0
                               Layout.fillWidth: true; spacing: 6; Layout.topMargin: 24
                    Icon { name: "message-circle"; size: 30; color: Theme2.textFaint; Layout.alignment: Qt.AlignHCenter }
                    Text { text: "Замечаний нет"; color: Theme2.textMuted; font.family: Theme2.family
                           font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold; Layout.alignment: Qt.AlignHCenter }
                    Text { text: "Замечания появляются в ходе проверки ревизий (внутренние) и от заказчика (на отправленную ревизию) — в контуре РКД."
                           color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                           wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; Layout.fillWidth: true }
                }

                // ================= КОММЕНТАРИИ (P3-2): треды и @упоминания к любому объекту.
                RowLayout { Layout.topMargin: (insp.kind === "document" || insp.kind === "kit") ? 12 : 0
                    Rectangle { width: 3; height: 16; radius: 1.5; color: Theme2.primary }
                    Text { text: "Комментарии" + (insp.commentItems.length > 0 ? " · " + insp.commentItems.length : "")
                           color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold }
                    Item { Layout.fillWidth: true }
                }

                Repeater {
                    model: insp.commentItems
                    delegate: Rectangle {
                        Layout.fillWidth: true; Layout.leftMargin: modelData.depth * 20; radius: Theme2.radiusS
                        color: modelData.deleted ? "transparent" : Theme2.surfaceAlt
                        border.color: "transparent"
                        implicitHeight: cmCol.implicitHeight + 16
                        ColumnLayout { id: cmCol; anchors { left: parent.left; right: parent.right; top: parent.top; margins: 8; leftMargin: 12; rightMargin: 12 } spacing: 4
                            RowLayout { spacing: 8
                                Text { text: modelData.author_name; color: Theme2.text; font.family: Theme2.family
                                       font.pixelSize: Theme2.fsXs; font.weight: Font.DemiBold }
                                Text { visible: modelData.created_label !== ""; text: modelData.created_label
                                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                                VChip { visible: modelData.edited && !modelData.deleted; text: "изменён"; tone: "info" }
                                VChip { visible: modelData.pending; text: "Отправляется"; tone: "warning"; iconName: "clock" }
                                Item { Layout.fillWidth: true }
                                VButton { visible: !modelData.deleted && !modelData.pending && insp.commentCanAdd
                                          text: "Ответить"; kind: "ghost"; compact: true
                                          onClicked: { insp.replyToId = modelData.id; insp.replyToName = modelData.author_name; commentInput.forceActiveFocus() } }
                                VButton { visible: !modelData.deleted && !modelData.pending && modelData.mine
                                          text: "Изменить"; kind: "ghost"; compact: true
                                          onClicked: commentEditForm.ask(modelData) }
                                VButton { visible: !modelData.deleted && !modelData.pending && (modelData.mine || insp.commentCanModerate)
                                          text: "Удалить"; kind: "ghost"; compact: true
                                          onClicked: commentDelForm.ask(modelData) }
                            }
                            Text { visible: modelData.deleted; text: "Комментарий удалён"
                                   color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                                   font.italic: true }
                            Text { visible: !modelData.deleted; text: modelData.body
                                   color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                                   wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            Text { visible: !modelData.deleted && modelData.mention_names.length > 0
                                   text: "Упомянуты: " + modelData.mention_names.join(", ")
                                   color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                        }
                    }
                }

                // ---------- пустое состояние комментариев
                Text { visible: insp.commentItems.length === 0
                       text: insp.commentCanAdd ? "Комментариев пока нет — начните обсуждение ниже."
                                                : "Комментариев нет. Комментировать могут участники проекта."
                       color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                       wrapMode: Text.WordWrap; Layout.fillWidth: true }

                // ---------- форма нового комментария / ответа (P3-2)
                ColumnLayout { visible: insp.commentCanAdd; Layout.fillWidth: true; spacing: 6
                    Rectangle { visible: insp.replyToId !== ""; Layout.fillWidth: true; height: 26; radius: Theme2.radiusS
                                color: Theme2.primarySoft
                        RowLayout { anchors { fill: parent; leftMargin: 10; rightMargin: 6 } spacing: 8
                            Text { text: "Ответ для: " + insp.replyToName; color: Theme2.primary
                                   font.family: Theme2.family; font.pixelSize: Theme2.fsXs; Layout.fillWidth: true
                                   elide: Text.ElideRight }
                            VButton { text: "Отмена"; kind: "ghost"; compact: true
                                      onClicked: { insp.replyToId = ""; insp.replyToName = "" } }
                        }
                    }
                    ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 64
                        TextArea { id: commentInput; objectName: "commentInput"
                                   placeholderText: "Комментарий… @Имя — упомянуть коллегу"
                                   color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                                   wrapMode: TextEdit.Wrap; selectByMouse: true
                                   onTextChanged: insp.updateMention()
                                   background: Rectangle { radius: Theme2.radiusS; color: Theme2.surface
                                                           border.color: commentInput.activeFocus ? Theme2.primary : Theme2.border } }
                    }
                    // Кандидаты для @упоминания (справочник людей, фильтр по фрагменту после «@»).
                    Flow { visible: insp.mentionOptions.length > 0; Layout.fillWidth: true; spacing: 4
                        Repeater {
                            model: insp.mentionOptions
                            delegate: Rectangle { radius: Theme2.radiusS; color: Theme2.surfaceAlt
                                                  implicitWidth: mt.implicitWidth + 16; implicitHeight: 24
                                Text { id: mt; anchors.centerIn: parent; text: "@" + modelData.display_name
                                       color: Theme2.primary; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                                MouseArea { anchors.fill: parent; onClicked: insp.applyMention(modelData.display_name)
                                            cursorShape: Qt.PointingHandCursor }
                            }
                        }
                    }
                    RowLayout { Layout.fillWidth: true
                        Text { text: "Упомянутый получит уведомление. Упомянуть можно только участника проекта."
                               color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                               wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        VButton { text: "Отправить"; objectName: "commentSend"; iconName: "send"
                                  enabled: commentInput.text.trim() !== ""
                                  onClicked: {
                                      backend.addComment("insp-comment-" + insp.kind, insp.kind, insp.object.id,
                                                         commentInput.text.trim(), insp.replyToId)
                                      commentInput.text = ""; insp.replyToId = ""; insp.replyToName = ""
                                  } }
                    }
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

    // ---------- диалог «Новая ревизия» (D-126): шифр ревизии + привязка к версии файла (docrev.create).
    // Можно офлайн (очередь): сервер при применении проверит уникальность шифра и версию файла.
    FormDialog {
        id: revForm2
        objectName: "inspRevForm"
        property string tag: "insp-rev-" + Math.random().toString(36).slice(2)
        title: "Новая ревизия РКД"
        subtitle: insp.object ? insp.object.code + " · " + insp.object.title : ""
        note: "Шифр ревизии («01», «REV A») — внешний номер для заказчика, уникален в документе и не повышается при возврате на доработку. Ревизия ссылается на конкретную версию файла."
        confirmText: "Создать"
        canConfirm: rLabel2.text.trim() !== "" && rVer2.currentIndex >= 0 && (insp.object ? (insp.object.versions || []).length > 0 : false)
        function ask() { rLabel2.text = ""; rNote2.text = ""; rVer2.currentIndex = 0; open() }
        onConfirmed: { working = true; error = ""
            var vers = insp.object ? (insp.object.versions || []) : []
            backend.execute(revForm2.tag, "docrev.create", ({ id: backend.newId(), document_id: insp.object.id,
                label: rLabel2.text.trim(), doc_version_id: vers[rVer2.currentIndex].id,
                note: rNote2.text.trim() }), 0) }
        VField { id: rLabel2; label: "Шифр ревизии"; required: true; placeholder: "Например: 01 или REV A" }
        VSelect { id: rVer2; label: "Версия файла"
                  model: insp.object ? (insp.object.versions || []).map(function(v) {
                      return { id: v.id, label: "Версия " + v.version_no + " · " + (v.file_name || "") } }) : [] }
        VField { id: rNote2; label: "Примечание"; placeholder: "Состав изменений, основание" }
        Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== revForm2.tag) return
                revForm2.working = false
                if (r.status === "accepted") revForm2.close()
                else revForm2.error = r.message_ru || "Ревизия не создана."
            }
        }
    }

    // ---------- диалог «Переход ревизии» (D-126): смена статуса по контракту docrev; причина —
    // когда контракт требует (возврат на доработку, отзыв разрешения, согласование заказчиком).
    FormDialog {
        id: revTransForm2
        objectName: "inspRevTransForm"
        property var rev: null
        property string toStatus: ""
        property string actionLabel: ""
        property bool reasonRequired: false
        property int revVersion: 1
        property string tag: "insp-revtr-" + Math.random().toString(36).slice(2)
        title: revTransForm2.actionLabel
        subtitle: revTransForm2.rev ? "Ревизия " + revTransForm2.rev.label + " · " + (revTransForm2.rev.status_label || "") : ""
        note: revTransForm2.reasonRequired ? "Причина обязательна: она попадёт в журнал проекта и уведомление автору."
                             : "Переход выполняется по контракту документооборота РКД; сервер проверит права и версию."
        confirmText: "Подтвердить"
        canConfirm: (!revTransForm2.reasonRequired || trReason2.text.trim() !== "") && backend.online
        function ask(r, to, label, needReason, ver) {
            rev = r; toStatus = to; actionLabel = label; reasonRequired = needReason; revVersion = ver
            trReason2.text = ""; open()
        }
        onConfirmed: { working = true; error = ""
            backend.execute(tag, "docrev.transition", ({ id: revTransForm2.rev.id, to: revTransForm2.toStatus,
                reason: trReason2.text.trim() }), revTransForm2.revVersion) }
        VField { id: trReason2; label: revTransForm2.reasonRequired ? "Причина (обязательно)" : "Причина (если нужна)"
                 required: revTransForm2.reasonRequired; placeholder: "Например: письмо № 45-12 от 12.11" }
        Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== revTransForm2.tag) return
                revTransForm2.working = false
                if (r.status === "accepted") revTransForm2.close()
                else revTransForm2.error = r.message_ru || "Переход не выполнен."
            }
        }
    }

    // ---------- диалог «Изменить комментарий» (P3-2): только автор, сервер пропускает в течение 24 часов.
    FormDialog {
        id: commentEditForm
        objectName: "commentEditForm"
        property var item: null
        property string tag: "insp-comment-edit-" + Math.random().toString(36).slice(2)
        title: "Изменить комментарий"
        note: "Правка возможна в течение 24 часов после публикации; в ленте появится отметка «изменён»."
        confirmText: "Сохранить"
        canConfirm: ceText.text.trim() !== ""
        function ask(c) { item = c; ceText.text = c.body; error = ""; open() }
        onConfirmed: { working = true; error = ""
                       backend.editComment(tag, item.id, ceText.text.trim(), item.version) }
        ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 90
            TextArea { id: ceText; objectName: "commentEditText"; color: Theme2.text
                       font.family: Theme2.family; font.pixelSize: Theme2.fs; wrapMode: TextEdit.Wrap; selectByMouse: true
                       background: Rectangle { radius: Theme2.radiusS; color: Theme2.surface
                                               border.color: ceText.activeFocus ? Theme2.primary : Theme2.border } } }
        Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== commentEditForm.tag) return
                commentEditForm.working = false
                if (r.status === "accepted") commentEditForm.close()
                else commentEditForm.error = r.message_ru || "Комментарий не изменён."
            }
        }
    }

    // ---------- подтверждение удаления комментария (P3-2): мягкое удаление, ответы остаются.
    FormDialog {
        id: commentDelForm
        objectName: "commentDelForm"
        property var item: null
        property string tag: "insp-comment-del-" + Math.random().toString(36).slice(2)
        title: "Удалить комментарий?"
        subtitle: item ? item.author_name + " · " + item.created_label : ""
        note: "Текст будет скрыт, в ленте останется отметка «Комментарий удалён»; ответы на него сохранятся."
        confirmText: "Удалить"
        confirmKind: "danger"
        function ask(c) { item = c; error = ""; open() }
        onConfirmed: { working = true; error = ""
                       backend.deleteComment(tag, item.id, item.version) }
        Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== commentDelForm.tag) return
                commentDelForm.working = false
                if (r.status === "accepted") commentDelForm.close()
                else commentDelForm.error = r.message_ru || "Комментарий не удалён."
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
