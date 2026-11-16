import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

// Работа внутри комплекта: задачи и документы с версиями файлов (issue #1).
//
// Что важно для понимания поведения:
//  * Задачи и карточки документов создаются/меняются и БЕЗ связи — команда ложится в офлайн-очередь
//    (у записи появляется метка «Не отправлено»), сервер перепроверит всё при синхронизации.
//  * Новая версия документа (файл) — ТОЛЬКО онлайн: файл не кладётся в очередь, чтобы не «тихо»
//    отправить устаревший чертёж через неделю. Кнопка в офлайне недоступна и объясняет почему.
//  * Скачивание сохраняет файл только после сверки SHA-256 (см. SyncClient.download_document_version).
//  * Кнопки переходов — подсказки по ролям; окончательно право проверяет сервер.
ColumnLayout {
    id: kw
    property var kit: null                  // выбранный комплект (объект из backend.kits)
    property var team: []                   // назначения проекта (для выбора исполнителя)
    property string tab: "tasks"            // tasks | documents
    readonly property var kitTasks: kit ? backend.tasks.filter(t => t.kit_id === kit.id) : []
    readonly property var kitDocs: kit ? backend.documents.filter(d => d.kit_id === kit.id) : []
    // Исполнителем может быть только участник проекта (сервер проверит: assignee_not_in_project).
    readonly property var assigneeOptions: {
        var seen = {}, out = [{ id: "", label: "Не назначен" }]
        for (var i = 0; i < team.length; i++) {
            var a = team[i]
            if (!seen[a.user_id]) { seen[a.user_id] = true; out.push({ id: a.user_id, label: a.user_name }) }
        }
        return out
    }
    readonly property var docOptions: {
        var out = [{ id: "", label: "Без документа" }]
        for (var i = 0; i < kitDocs.length; i++) out.push({ id: kitDocs[i].id, label: kitDocs[i].code + " · " + kitDocs[i].title })
        return out
    }
    spacing: 10
    visible: kit !== null

    function indexOf(options, id) {
        for (var i = 0; i < options.length; i++) if (options[i].id === (id || "")) return i
        return 0
    }

    // ---------- заголовок и переключатель «Задачи / Документы»
    RowLayout {
        Layout.fillWidth: true; Layout.topMargin: 6; spacing: 10
        SectionTitle { text: kit ? "Комплект " + kit.code : ""; iconName: "layers" }
        Item { Layout.fillWidth: true }
        Rectangle {
            id: seg
            implicitWidth: segRow.implicitWidth + 6; implicitHeight: 34; radius: 10
            color: Theme.surfaceAlt; border.color: Theme.border
            Row {
                id: segRow; anchors.centerIn: parent; spacing: 2
                Repeater {
                    model: [{ id: "tasks", label: "Задачи (" + kw.kitTasks.length + ")", icon: "list-checks" },
                            { id: "documents", label: "Документы (" + kw.kitDocs.length + ")", icon: "file-text" }]
                    delegate: Rectangle {
                        required property var modelData
                        width: segIn.implicitWidth + 22; height: 28; radius: 8
                        color: kw.tab === modelData.id ? Theme.surface : "transparent"
                        border.color: kw.tab === modelData.id ? Theme.border : "transparent"
                        Accessible.role: Accessible.PageTab; Accessible.name: modelData.label
                        activeFocusOnTab: true
                        Keys.onSpacePressed: kw.tab = modelData.id
                        RowLayout { id: segIn; anchors.centerIn: parent; spacing: 6
                            Icon { name: modelData.icon; size: 14; color: kw.tab === modelData.id ? Theme.primary : Theme.textMuted }
                            Text { text: modelData.label; font.family: Theme.family; font.pixelSize: Theme.fsS
                                   font.weight: kw.tab === modelData.id ? Font.DemiBold : Font.Normal
                                   color: kw.tab === modelData.id ? Theme.text : Theme.textMuted } }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: kw.tab = modelData.id }
                    }
                }
            }
        }
        VButton {
            text: kw.tab === "tasks" ? "Новая задача" : "Новый документ"; kind: "soft"; compact: true; iconName: "plus"
            onClicked: kw.tab === "tasks" ? taskForm.openNew() : docForm.openNew()
        }
    }

    // ================================================================== задачи
    Repeater {
        model: kw.tab === "tasks" ? kw.kitTasks : []
        delegate: VCard {
            id: taskCard
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: tRow.implicitHeight + 24
            opacity: modelData.terminal ? 0.7 : 1
            ColumnLayout {
                id: tRow
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
                spacing: 6
                RowLayout { Layout.fillWidth: true; spacing: 8
                    VChip { text: modelData.status_label
                            tone: modelData.status === "done" ? "success" : modelData.status === "in_progress" ? "info"
                                : modelData.status === "cancelled" ? "neutral" : "primary" }
                    Text { text: modelData.title; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM
                           font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true
                           font.strikeout: modelData.status === "cancelled" }
                    VChip { visible: modelData.pending; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                    VButton { kind: "ghost"; compact: true; iconName: "pencil"; Accessible.name: "Изменить задачу"
                              enabled: modelData.version > 0 && !modelData.terminal
                              onClicked: taskForm.openEdit(modelData) }
                }
                // Строка свойств: кто, до какого числа, к какому документу относится.
                Flow { Layout.fillWidth: true; spacing: 14
                    Row { spacing: 5
                        Icon { name: "user"; size: 14 }
                        Text { text: modelData.assignee_name || (modelData.assignee_user_id ? "Исполнитель (нет в кэше)" : "Исполнитель не назначен")
                               color: modelData.mine ? Theme.primary : Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS
                               font.weight: modelData.mine ? Font.DemiBold : Font.Normal } }
                    Row { spacing: 5; visible: !!modelData.due_date
                        Icon { name: "clock"; size: 14 }
                        Text { text: "до " + (modelData.due_date || ""); color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS } }
                    Row { spacing: 5; visible: modelData.document_code !== ""
                        Icon { name: "paperclip"; size: 14 }
                        Text { text: modelData.document_code; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS } }
                }
                Text { visible: !!modelData.description; text: modelData.description || ""; color: Theme.textMuted; wrapMode: Text.Wrap
                       font.family: Theme.family; font.pixelSize: Theme.fsS; Layout.fillWidth: true; maximumLineCount: 3; elide: Text.ElideRight }
                Text { visible: !!modelData.status_reason; text: "Причина: " + (modelData.status_reason || ""); color: Theme.textFaint
                       font.family: Theme.family; font.pixelSize: Theme.fsXs; Layout.fillWidth: true; wrapMode: Text.Wrap }
                Flow { Layout.fillWidth: true; spacing: 6; visible: modelData.next.length > 0 && modelData.version > 0
                    Repeater {
                        model: modelData.next
                        delegate: VButton {
                            required property var modelData
                            text: modelData.label; compact: true
                            kind: modelData.id === "cancelled" ? "ghost" : modelData.id === "done" ? "primary" : "soft"
                            iconName: modelData.id === "done" ? "check" : modelData.id === "in_progress" ? "play"
                                    : modelData.id === "cancelled" ? "x" : "rotate-ccw"
                            onClicked: trans.ask(taskCard.modelData, modelData)
                        }
                    }
                }
            }
        }
    }
    EmptyState {
        visible: kw.tab === "tasks" && kw.kitTasks.length === 0
        Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 10; Layout.bottomMargin: 10
        iconName: "list-checks"; title: "Задач пока нет"
        text: "Задачу создаёт ГИП или РП проекта; исполнитель видит её у себя и отмечает ход работы."
    }

    // ================================================================== документы
    Banner {
        visible: kw.tab === "documents" && !backend.online
        Layout.fillWidth: true; tone: "warning"
        text: "Нет связи с сервером: загрузка и скачивание файлов недоступны. Карточки документов можно создавать и изменять — они уйдут при синхронизации."
    }
    Repeater {
        model: kw.tab === "documents" ? kw.kitDocs : []
        delegate: VCard {
            id: docCard
            required property var modelData
            property bool expanded: modelData.versions.length > 0 && modelData.versions.length <= 3
            Layout.fillWidth: true
            implicitHeight: dCol.implicitHeight + 24
            ColumnLayout {
                id: dCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
                spacing: 6
                RowLayout { Layout.fillWidth: true; spacing: 10
                    Rectangle { width: 36; height: 36; radius: 10; color: Theme.primarySoft
                        Icon { anchors.centerIn: parent; name: "file-text"; size: 18; color: Theme.primary } }
                    ColumnLayout { spacing: 1; Layout.fillWidth: true
                        Text { text: modelData.code; color: Theme.primary; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.Bold }
                        Text { text: modelData.title; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM
                               elide: Text.ElideRight; Layout.fillWidth: true } }
                    VChip { visible: modelData.pending; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                    VChip { text: modelData.current_label; tone: modelData.current_version_no > 0 ? "info" : "neutral" }
                    VButton { kind: "ghost"; compact: true; iconName: "pencil"; Accessible.name: "Изменить карточку документа"
                              enabled: modelData.version > 0; onClicked: docForm.openEdit(modelData) }
                    VButton {
                        text: "Новая версия"; compact: true; kind: "soft"; iconName: "upload"
                        // Документ, созданный офлайн, ещё не существует на сервере — к нему нечего привязать файл.
                        enabled: backend.online && !modelData.pending && modelData.version > 0
                        // Причина недоступности показана текстом (баннер «нет связи» / строка ниже), а не во всплывающей
                        // подсказке: её не видно с клавиатуры и на сенсорном экране.
                        Accessible.description: !backend.online ? "Загрузка файла — только при связи с сервером."
                                              : modelData.pending ? "Сначала дождитесь отправки документа на сервер." : ""
                        onClicked: verForm.ask(modelData)
                    }
                }
                Text { visible: modelData.pending; Layout.fillWidth: true; wrapMode: Text.Wrap
                       text: "Карточка ещё не отправлена на сервер — файл можно будет загрузить после синхронизации."
                       color: Theme.warning; font.family: Theme.family; font.pixelSize: Theme.fsXs }
                // Список версий: новые сверху. Файлы неизменяемы — у каждой версии свой SHA-256.
                RowLayout { visible: modelData.versions.length > 0; spacing: 6
                    Text { text: (docCard.expanded ? "Скрыть версии" : "Показать версии") + " (" + modelData.versions.length + ")"
                           color: Theme.primary; font.family: Theme.family; font.pixelSize: Theme.fsS
                           MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: docCard.expanded = !docCard.expanded } } }
                Repeater {
                    model: docCard.expanded ? modelData.versions : []
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true; implicitHeight: vRow.implicitHeight + 14; radius: Theme.radiusS
                        color: Theme.surfaceAlt
                        RowLayout { id: vRow; anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 6; spacing: 10
                            Text { text: "в." + modelData.version_no; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS
                                   font.weight: Font.Bold; Layout.preferredWidth: 34 }
                            ColumnLayout { spacing: 0; Layout.fillWidth: true
                                Text { text: modelData.file_name; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS
                                       elide: Text.ElideMiddle; Layout.fillWidth: true }
                                Text { text: modelData.size_label + "  ·  " + (modelData.author_name || "—") + "  ·  " + modelData.created_label
                                             + (modelData.comment ? "  ·  " + modelData.comment : "")
                                       color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs
                                       elide: Text.ElideRight; Layout.fillWidth: true }
                                Text { text: "SHA-256 " + modelData.sha256.substring(0, 16) + "…"; color: Theme.textFaint
                                       font.family: "monospace"; font.pixelSize: Theme.fsXs } }
                            VButton { kind: "ghost"; compact: true; iconName: "download"; Accessible.name: "Скачать версию " + modelData.version_no
                                      enabled: backend.online
                                      onClicked: kw.download(docCard.modelData.id, modelData.version_no) }
                        }
                    }
                }
            }
        }
    }
    EmptyState {
        visible: kw.tab === "documents" && kw.kitDocs.length === 0
        Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 10; Layout.bottomMargin: 10
        iconName: "file-text"; title: "Документов пока нет"
        text: "Создайте карточку документа (можно без связи), затем загрузите файл первой версии."
    }

    // ================================================================== формы
    FormDialog {
        id: taskForm
        property string editId: ""
        property int editVersion: 0
        title: editId ? "Изменить задачу" : "Новая задача"
        subtitle: "Исполнитель — участник проекта. Можно сохранять без связи: задача уйдёт на сервер при синхронизации."
        canConfirm: tTitle.text.trim() !== "" && tDue.error === ""
        function openNew() {
            editId = ""; editVersion = 0; tTitle.text = ""; tDesc.text = ""; tDue.text = ""
            tWho.currentIndex = 0; tDoc.currentIndex = 0; open()
        }
        function openEdit(t) {
            editId = t.id; editVersion = t.version; tTitle.text = t.title; tDesc.text = t.description || ""; tDue.text = t.due_date || ""
            tWho.currentIndex = kw.indexOf(kw.assigneeOptions, t.assignee_user_id); tDoc.currentIndex = kw.indexOf(kw.docOptions, t.document_id); open()
        }
        onConfirmed: {
            if (editId) {
                // При изменении пустое значение означает «очистить» (null), а не «не трогать».
                tCmd.run("task.update", { id: editId, title: tTitle.text.trim(), description: tDesc.text.trim() || null,
                                          due_date: tDue.text.trim() || null, assignee_user_id: tWho.currentValue || null,
                                          document_id: tDoc.currentValue || null }, editVersion)
            } else {
                tCmd.run("task.create", { id: backend.newId(), kit_id: kw.kit.id, title: tTitle.text.trim(), description: tDesc.text.trim(),
                                          due_date: tDue.text.trim(), assignee_user_id: tWho.currentValue, document_id: tDoc.currentValue })
            }
        }
        VField { id: tTitle; label: "Что сделать"; required: true; placeholder: "Разработать схему автоматизации" }
        VField { id: tDesc; label: "Подробности"; placeholder: "Необязательно" }
        VSelect { id: tWho; label: "Исполнитель"; model: kw.assigneeOptions
                  hint: kw.team.length === 0 ? "Список участников недоступен офлайн — назначьте позже." : "" }
        VField { id: tDue; label: "Срок"; placeholder: "ГГГГ-ММ-ДД"
                 // Формат проверяем сразу, саму дату (например 31 февраля) — сервер.
                 error: text.trim() === "" || /^\d{4}-\d{2}-\d{2}$/.test(text.trim()) ? "" : "Формат даты: ГГГГ-ММ-ДД" }
        VSelect { id: tDoc; label: "Документ"; model: kw.docOptions }
    }
    Cmd { id: tCmd; dialog: taskForm }

    FormDialog {
        id: trans
        property var task: null
        property var step: null
        title: step ? step.label : ""
        subtitle: task ? task.title : ""
        confirmText: step ? step.label : "OK"
        confirmKind: step && step.id === "cancelled" ? "danger" : "primary"
        canConfirm: step && (!step.reason || trReason.text.trim() !== "")
        function ask(t, s) { task = t; step = s; trReason.text = ""; open() }
        onConfirmed: trCmd.run("task.transition", { id: task.id, to: step.id, reason: trReason.text.trim() || null }, task.version)
        VField { id: trReason; label: trans.step && trans.step.reason ? "Причина (обязательно)" : "Комментарий"
                 required: !!(trans.step && trans.step.reason) }
    }
    Cmd { id: trCmd; dialog: trans }

    FormDialog {
        id: docForm
        property string editId: ""
        property int editVersion: 0
        title: editId ? "Изменить карточку документа" : "Новый документ"
        subtitle: "Обозначение уникально в пределах проекта. Файл загружается отдельно — кнопкой «Новая версия»."
        canConfirm: dCode.text.trim() !== "" && dTitle.text.trim() !== ""
        function openNew() { editId = ""; editVersion = 0; dCode.text = kw.kit ? kw.kit.code + "-" : ""; dTitle.text = ""; open() }
        function openEdit(d) { editId = d.id; editVersion = d.version; dCode.text = d.code; dTitle.text = d.title; open() }
        onConfirmed: editId ? dCmd.run("document.update", { id: editId, code: dCode.text.trim(), title: dTitle.text.trim() }, editVersion)
                            : dCmd.run("document.create", { id: backend.newId(), kit_id: kw.kit.id, code: dCode.text.trim(), title: dTitle.text.trim() })
        VField { id: dCode; label: "Обозначение"; required: true; placeholder: "АТХ-001" }
        VField { id: dTitle; label: "Наименование"; required: true; placeholder: "Схема автоматизации" }
    }
    Cmd { id: dCmd; dialog: docForm }

    FormDialog {
        id: verForm
        objectName: "versionForm"
        property var doc: null
        property alias path: vPath.text
        property string tag: "ver-" + Math.random().toString(36).slice(2)
        title: "Новая версия документа"
        subtitle: doc ? doc.code + " · " + doc.title + (doc.current_version_no > 0 ? " — станет версией " + (doc.current_version_no + 1)
                                                                                    : " — первая версия") : ""
        note: "Файл проверяется по SHA-256 при загрузке и при каждом скачивании. Если коллега успеет загрузить свою версию раньше, вы увидите конфликт — ничего не перезапишется."
        confirmText: "Загрузить"
        canConfirm: vPath.text.trim() !== "" && backend.online
        function ask(d) { doc = d; vPath.text = ""; vComment.text = ""; open() }
        onConfirmed: { working = true; error = ""; backend.addDocumentVersion(tag, doc.id, vPath.text.trim(), vComment.text, doc.version) }
        RowLayout { Layout.fillWidth: true; spacing: 8
            VField { id: vPath; label: "Файл"; required: true; readOnly: true; placeholder: "Выберите файл…"; Layout.fillWidth: true }
            VButton { text: "Выбрать…"; iconName: "paperclip"; Layout.alignment: Qt.AlignBottom; onClicked: openDlg.open() } }
        VField { id: vComment; label: "Комментарий к версии"; placeholder: "Что изменилось" }
        Connections {
            target: backend
            function onCommandDone(t, r) {
                if (t !== verForm.tag) return
                verForm.working = false
                if (r.status === "accepted") verForm.close()
                else verForm.error = r.message_ru || "Версия не создана."
            }
        }
    }

    FileDialog {
        id: openDlg
        title: "Файл новой версии"
        fileMode: FileDialog.OpenFile
        onAccepted: vPath.text = selectedFile.toString()
    }

    // Скачивание: «Сохранить как» с именем файла, под которым автор загрузил версию.
    property string _dlDoc: ""
    property int _dlVer: 0
    function download(docId, versionNo) {
        _dlDoc = docId; _dlVer = versionNo
        saveDlg.selectedFile = saveDlg.currentFolder + "/" + backend.suggestedFileName(docId, versionNo)
        saveDlg.open()
    }
    FileDialog {
        id: saveDlg
        title: "Сохранить версию документа"
        fileMode: FileDialog.SaveFile
        onAccepted: backend.downloadDocumentVersion("dl-" + kw._dlDoc, kw._dlDoc, kw._dlVer, selectedFile.toString())
    }
}
