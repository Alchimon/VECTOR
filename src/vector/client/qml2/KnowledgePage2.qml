import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../qml"

// Раздел «Знания» 2.0 (P3-3, blueprint 57): база знаний компании и проектов, общая база
// подрядчиков/поставщиков и каталог оборудования. Три вкладки с общим поиском.
// Статьи можно править без связи (очередь, «Не отправлено»); подрядчики и оборудование —
// только онлайн (команды online_required), поэтому их формы закрыты без связи.
Item {
    id: page
    objectName: "knowledgePage2"
    property var shell
    property string tab: "articles"            // articles | vendors | equipment
    property string selectedId: ""
    property string search: ""
    property string scopeFilter: ""            // "" — все статьи; company | project
    readonly property bool online: backend.online
    readonly property var projectsById: {
        var m = ({}); for (var i = 0; i < backend.projects.length; i++) m[backend.projects[i].id] = backend.projects[i]; return m
    }

    // ---------- выборки
    readonly property var articles: backend.kbArticles.filter(a =>
        (scopeFilter === "" || a.scope === scopeFilter)
        && (search === "" || (a.title + " " + a.code + " " + a.kind_label + " " + (a.tags || []).join(" ")).toLowerCase().indexOf(search.toLowerCase()) >= 0))
    readonly property var vendors: backend.vendors.filter(v =>
        search === "" || (v.name + " " + (v.inn || "") + " " + v.kind_label).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var equipment: backend.equipment.filter(e =>
        search === "" || (e.name + " " + e.code + " " + e.category + " " + (e.manufacturer || "")).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var currentItems: tab === "articles" ? articles : tab === "vendors" ? vendors : equipment
    readonly property var sel: currentItems.find(x => x.id === selectedId) || null

    function projectName(pid) {
        var p = projectsById[pid]
        return p ? p.code + " · " + p.title : ""
    }

    // Переход из палитры/ссылки: shell.go("knowledge", "articles|<id>") — вкладка + выделение.
    function handleNavTarget() {
        if (!shell || shell.navTarget.page !== "knowledge" || shell.navTarget.id === "") return
        var parts = shell.navTarget.id.split("|")
        if (parts.length === 2) { page.tab = parts[0]; page.selectedId = parts[1] }
    }
    Component.onCompleted: handleNavTarget()

    // ---------- формы
    function openArticleCreate() {
        aForm.editId = ""; aForm.editVersion = 0; aForm.editScope = ""
        aCode.text = ""; aTitle.text = ""; aBody.text = ""; aTags.text = ""
        aKind.currentIndex = 3; aScope.currentIndex = 0; aProject.currentIndex = 0
        aForm.open()
    }
    function openArticleEdit() {
        aForm.editId = sel.id; aForm.editVersion = sel.version; aForm.editScope = sel.scope
        aCode.text = sel.code; aTitle.text = sel.title; aBody.text = sel.body_md
        aTags.text = (sel.tags || []).join(", ")
        aKind.currentIndex = Math.max(0, ["standard", "template_ref", "note", "howto"].indexOf(sel.kind))
        aScope.currentIndex = sel.scope === "company" ? 0 : 1
        var pi = 0
        for (var i = 0; i < aProject.model.length; i++) if (aProject.model[i].id === sel.project_id) { pi = i; break }
        aProject.currentIndex = pi
        aForm.open()
    }
    function openVendorCreate() {
        vForm.editId = ""; vForm.editVersion = 0
        vName.text = ""; vInn.text = ""; vPhone.text = ""; vEmail.text = ""; vNote.text = ""
        vKind.currentIndex = 0; vForm.open()
    }
    function openVendorEdit() {
        vForm.editId = sel.id; vForm.editVersion = sel.version
        vName.text = sel.name; vInn.text = sel.inn || ""
        vPhone.text = (sel.contacts || {}).phone || ""; vEmail.text = (sel.contacts || {}).email || ""
        vNote.text = sel.rating_note || ""
        vKind.currentIndex = Math.max(0, ["supplier", "contractor", "both"].indexOf(sel.kind))
        vForm.open()
    }
    function openEquipCreate() {
        eForm.editId = ""; eForm.editVersion = 0
        eCode.text = ""; eName.text = ""; eCat.text = ""; eMfr.text = ""; eParams.text = ""
        eForm.open()
    }
    function openEquipEdit() {
        eForm.editId = sel.id; eForm.editVersion = sel.version
        eCode.text = sel.code; eName.text = sel.name; eCat.text = sel.category; eMfr.text = sel.manufacturer || ""
        var ps = []
        var keys = Object.keys(sel.params || {})
        for (var i = 0; i < keys.length; i++) ps.push(keys[i] + "=" + sel.params[keys[i]])
        eParams.text = ps.join("; ")
        eForm.open()
    }
    // «мощность=7.5 кВт; масса=120 кг» → объект; плохая строка — ошибка под полем.
    function parseParams(text) {
        var out = ({})
        var parts = text.split(";")
        for (var i = 0; i < parts.length; i++) {
            var s = parts[i].trim()
            if (s === "") continue
            var eq = s.indexOf("=")
            if (eq <= 0 || eq === s.length - 1) return null
            out[s.substring(0, eq).trim()] = s.substring(eq + 1).trim()
        }
        return out
    }
    function tagsPayload(text) {
        var raw = text.split(","), out = []
        for (var i = 0; i < raw.length; i++) { var t = raw[i].trim(); if (t !== "") out.push(t) }
        return out
    }
    function archiveCommand() { return tab === "articles" ? "kb.archive" : tab === "vendors" ? "vendor.archive" : "equipment.archive" }

    ColumnLayout {
        anchors { fill: parent; margins: Theme2.pageMargin }
        spacing: Theme2.gap + 4

        // ---------- заголовок, поиск, действия
        RowLayout {
            Layout.fillWidth: true; spacing: Theme2.pad
            ColumnLayout { spacing: 2; Layout.fillWidth: true
                Text { text: "Знания"; color: Theme2.text; font.family: Theme2.family
                       font.pixelSize: Theme2.fsXl; font.weight: Font.Bold }
                Text { text: backend.kbArticles.length === 0 && backend.vendors.length === 0 && backend.equipment.length === 0
                       ? "Стандарты и инструкции компании, база подрядчиков и каталог оборудования."
                       : backend.kbArticles.length + " статей · " + backend.vendors.length + " подрядчиков · " + backend.equipment.length + " ед. оборудования"
                       color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                       wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }
            VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() }
            VButton { objectName: "kbCreate"
                      text: tab === "articles" ? "Новая статья" : tab === "vendors" ? "Новый подрядчик" : "Новое оборудование"
                      kind: "primary"; iconName: "plus"
                      visible: tab === "articles" ? backend.hint("kb.create", backend.permRevision)
                             : tab === "vendors" ? backend.hint("vendor.create", backend.permRevision)
                             : backend.hint("equipment.create", backend.permRevision)
                      enabled: tab === "articles" || page.online
                      tip: tab !== "articles" && !page.online
                           ? "Справочники компании правятся только при связи с сервером."
                           : tab === "articles" ? "Можно сохранять без связи: статья уйдёт на сервер при синхронизации." : ""
                      onClicked: tab === "articles" ? page.openArticleCreate()
                               : tab === "vendors" ? page.openVendorCreate() : page.openEquipCreate() }
            VSearch { objectName: "knowledge2Search"; Layout.preferredWidth: Theme2.compact ? 220 : 300
                      implicitHeight: Theme2.compact ? 36 : 44
                      placeholderText: "Поиск по разделу…"
                      onTextChanged: page.search = text }
        }

        // ---------- вкладки + фильтр раздела статей
        RowLayout {
            Layout.fillWidth: true; spacing: Theme2.pad
            VSegmented {
                objectName: "knowledgeTabs"
                model: [{ id: "articles", label: "Статьи", tip: "Стандарты (СТО), инструкции и заметки компании и проектов." },
                        { id: "vendors", label: "Подрядчики", tip: "Общая база подрядчиков и поставщиков компании: контакты, опыт работы." },
                        { id: "equipment", label: "Оборудование", tip: "Каталог оборудования с паспортными характеристиками." }]
                current: page.tab
                onPicked: function(id) { page.tab = id; page.selectedId = "" }
            }
            VSegmented {
                visible: page.tab === "articles"
                objectName: "knowledgeScope"
                model: [{ id: "", label: "Все разделы" }, { id: "company", label: "Компания" }, { id: "project", label: "Проекты" }]
                current: page.scopeFilter
                onPicked: function(id) { page.scopeFilter = id }
            }
            Item { Layout.fillWidth: true }
        }

        // ---------- список карточек + карточка выбранного объекта
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: Theme2.gap + 4

            ListView {
                id: list
                objectName: "knowledge2List"
                Layout.preferredWidth: Theme2.compact ? 320 : 400
                Layout.maximumWidth: Layout.preferredWidth
                Layout.fillHeight: true
                clip: true; spacing: Theme2.gap
                model: page.currentItems
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: Rectangle {
                    id: card
                    objectName: "kbCard"
                    width: list.width
                    implicitHeight: (Theme2.compact ? 64 : 76) + Theme2.pad
                    radius: Theme2.radius
                    color: ma.containsMouse ? Theme2.hover : Theme2.surface
                    border.color: page.selectedId === modelData.id ? Theme2.primary : Theme2.cardBorder
                    border.width: page.selectedId === modelData.id ? 2 : 1

                    Rectangle { visible: page.selectedId === modelData.id; anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter; width: 4
                                height: parent.height - 24; radius: 2; color: Theme2.primary }

                    MouseArea {
                        id: ma
                        anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: page.selectedId = modelData.id
                    }

                    RowLayout {
                        anchors { fill: parent; leftMargin: Theme2.pad; rightMargin: Theme2.pad
                                  topMargin: Theme2.pad - 4; bottomMargin: Theme2.pad - 4 }
                        spacing: Theme2.pad
                        Rectangle { Layout.preferredWidth: Theme2.compact ? 40 : 48; Layout.preferredHeight: width
                                    radius: Theme2.radius; color: Theme2.primarySoft
                            Icon { anchors.centerIn: parent; size: 22; color: Theme2.primary
                                   name: page.tab === "articles" ? "bookmark" : page.tab === "vendors" ? "truck" : "wrench" } }
                        ColumnLayout { spacing: 3; Layout.fillWidth: true
                            Text { text: page.tab === "articles" ? modelData.title : modelData.name
                                   color: Theme2.text; font.family: Theme2.family
                                   font.pixelSize: Theme2.fsM; font.weight: Font.DemiBold
                                   Layout.fillWidth: true; elide: Text.ElideRight }
                            Text { color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                                   Layout.fillWidth: true; elide: Text.ElideRight
                                   text: page.tab === "articles"
                                       ? modelData.code + " · " + modelData.kind_label
                                         + (modelData.scope === "project" ? " · " + page.projectName(modelData.project_id) : " · Компания")
                                       : page.tab === "vendors"
                                       ? modelData.kind_label + (modelData.inn ? " · ИНН " + modelData.inn : "")
                                       : modelData.code + " · " + modelData.category }
                        }
                        VChip { visible: !modelData.active; text: "Архив"; tone: "neutral"; iconName: "archive" }
                        VChip { visible: modelData.pending === true; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                        Icon { name: "chevron-right"; size: 16; color: Theme2.textFaint; Layout.alignment: Qt.AlignVCenter }
                    }
                }

                // Пустое состояние списка — дружелюбное, с пояснением.
                ColumnLayout {
                    visible: page.currentItems.length === 0
                    anchors.centerIn: parent; spacing: Theme2.gap
                    Icon { name: page.tab === "articles" ? "bookmark" : page.tab === "vendors" ? "truck" : "wrench"
                           size: 44; color: Theme2.textFaint; Layout.alignment: Qt.AlignHCenter }
                    Text { text: page.search !== "" ? "По запросу «" + page.search + "» ничего не найдено"
                             : page.tab === "articles" ? "Статей пока нет"
                             : page.tab === "vendors" ? "Подрядчиков пока нет" : "Оборудования пока нет"
                           color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsM
                           font.weight: Font.DemiBold; Layout.alignment: Qt.AlignHCenter }
                    Text { text: page.tab === "articles"
                             ? "Начните со стандарта или инструкции — они увидят все сотрудники."
                             : page.tab === "vendors"
                             ? "Общая база подрядчиков и поставщиков компании: ведёт закупка."
                             : "Паспортные характеристики оборудования: ведут ведущий инженер и технолог."
                           color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                           wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                           Layout.preferredWidth: 320 }
                }
            }

            // ---------- карточка выбранного объекта (правая панель страницы)
            Flickable {
                Layout.fillWidth: true; Layout.fillHeight: true
                contentWidth: width; contentHeight: detailCol.implicitHeight
                clip: true; boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                ColumnLayout {
                    id: detailCol
                    width: parent.width
                    spacing: Theme2.gap + 4

                    // Нет выбора — подсказка вместо карточки.
                    ColumnLayout {
                        visible: page.sel === null && page.currentItems.length > 0
                        Layout.fillWidth: true; Layout.topMargin: 60; spacing: Theme2.gap
                        Icon { name: "arrow-left"; size: 32; color: Theme2.textFaint; Layout.alignment: Qt.AlignHCenter }
                        Text { text: "Выберите запись в списке слева"
                               color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsM
                               Layout.alignment: Qt.AlignHCenter }
                    }

                    // Шапка карточки: название, состояние, действия.
                    Rectangle {
                        objectName: "kbDetail"
                        visible: page.sel !== null
                        Layout.fillWidth: true; implicitHeight: headCol.implicitHeight + Theme2.pad * 2
                        radius: Theme2.radius; color: Theme2.surface; border.color: Theme2.border
                        ColumnLayout {
                            id: headCol
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme2.pad }
                            spacing: Theme2.gap
                            RowLayout { Layout.fillWidth: true; spacing: Theme2.pad
                                Rectangle { Layout.preferredWidth: 52; Layout.preferredHeight: 52; radius: 14
                                            color: Theme2.primarySoft
                                    Icon { anchors.centerIn: parent; size: 26; color: Theme2.primary
                                           name: page.tab === "articles" ? "bookmark" : page.tab === "vendors" ? "truck" : "wrench" } }
                                ColumnLayout { spacing: 4; Layout.fillWidth: true
                                    Text { objectName: "kbTitle"
                                           text: page.sel ? (page.tab === "articles" ? page.sel.title : page.sel.name) : ""
                                           color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsXl
                                           font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                    RowLayout { spacing: 6
                                        VChip { text: page.sel && page.sel.pending ? "Не отправлено на сервер" : "Синхронизировано"
                                                tone: page.sel && page.sel.pending ? "warning" : "success"
                                                iconName: page.sel && page.sel.pending ? "clock" : "check" }
                                        VChip { visible: page.sel && !page.sel.active; text: "В архиве"; tone: "neutral"; iconName: "archive" }
                                        VChip { text: page.sel ? "версия " + (page.sel.version || "—") : ""; tone: "neutral" } } }
                                VButton { objectName: "kbEdit"; text: "Изменить"; iconName: "pencil"
                                          visible: page.sel && (page.tab === "articles" ? backend.hint("kb.update", backend.permRevision)
                                                   : page.tab === "vendors" ? backend.hint("vendor.update", backend.permRevision)
                                                   : backend.hint("equipment.update", backend.permRevision))
                                          enabled: page.sel && page.sel.version > 0 && (page.tab === "articles" || page.online)
                                          tip: page.tab !== "articles" && !page.online
                                               ? "Справочники компании правятся только при связи с сервером." : ""
                                          onClicked: page.tab === "articles" ? page.openArticleEdit()
                                                   : page.tab === "vendors" ? page.openVendorEdit() : page.openEquipEdit() }
                                VButton { objectName: "kbArchive"; iconName: "archive"
                                          text: page.sel && page.sel.active ? "В архив" : "Вернуть"
                                          kind: "soft"
                                          visible: page.sel && (page.tab === "articles" ? backend.hint("kb.archive", backend.permRevision)
                                                   : page.tab === "vendors" ? backend.hint("vendor.archive", backend.permRevision)
                                                   : backend.hint("equipment.archive", backend.permRevision))
                                          enabled: page.sel && page.sel.version > 0 && (page.tab === "articles" || page.online)
                                          tip: page.tab !== "articles" && !page.online
                                               ? "Справочники компании правятся только при связи с сервером."
                                               : "Запись не удаляется: останется в списке с пометкой «Архив»."
                                          onClicked: archCmd.run(page.archiveCommand(), { id: page.sel.id, active: !page.sel.active }, page.sel.version) }
                            }
                            Banner { Layout.fillWidth: true; visible: page.sel && page.sel.pending; tone: "warning"
                                     text: page.sel && page.sel.pending ? "Изменения сохранены на этом компьютере и ещё не подтверждены сервером." : "" }
                        }
                    }

                    // ---------- содержимое: статья
                    Rectangle {
                        visible: page.sel !== null && page.tab === "articles"
                        Layout.fillWidth: true; implicitHeight: aBody.implicitHeight + Theme2.pad * 2
                        radius: Theme2.radius; color: Theme2.surfaceAlt; border.color: Theme2.border
                        ColumnLayout { id: aDetail
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme2.pad }
                            spacing: 10
                            RowLayout { spacing: 6
                                VChip { text: page.sel ? page.sel.kind_label : ""; tone: "primary" }
                                VChip { text: page.sel ? page.sel.code : ""; tone: "neutral" }
                                VChip { text: page.sel && page.sel.scope === "project" ? "Проект: " + page.projectName(page.sel.project_id) : "Компания"
                                        tone: "neutral"; iconName: page.sel && page.sel.scope === "project" ? "folder-kanban" : "building-2" } }
                            Flow { visible: page.sel && (page.sel.tags || []).length > 0
                                   Layout.fillWidth: true; spacing: 6
                                Repeater { model: page.sel ? (page.sel.tags || []) : []
                                    delegate: VChip { text: "#" + modelData; tone: "neutral" } } }
                            Text { objectName: "kbBody"
                                   text: page.sel ? page.sel.body_md : ""
                                   color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsS
                                   wrapMode: Text.Wrap; Layout.fillWidth: true }
                            KeyValue { k: "Обновлено"; muted: true
                                       v: page.sel && page.sel.updated_at ? page.sel.updated_at.replace("T", " ").substring(0, 16) + " UTC" : "" } } }

                    // ---------- содержимое: подрядчик
                    Rectangle {
                        visible: page.sel !== null && page.tab === "vendors"
                        Layout.fillWidth: true; implicitHeight: vBody.implicitHeight + Theme2.pad * 2
                        radius: Theme2.radius; color: Theme2.surfaceAlt; border.color: Theme2.border
                        ColumnLayout { id: vBody
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme2.pad }
                            spacing: 10
                            KeyValue { k: "Наименование"; v: page.sel ? page.sel.name : "" }
                            KeyValue { k: "Вид"; v: page.sel ? page.sel.kind_label : "" }
                            KeyValue { k: "ИНН"; v: page.sel ? (page.sel.inn || "") : "" }
                            KeyValue { k: "Телефон"; v: page.sel ? ((page.sel.contacts || {}).phone || "") : "" }
                            KeyValue { k: "E-mail"; v: page.sel ? ((page.sel.contacts || {}).email || "") : "" }
                            KeyValue { k: "Опыт работы"; v: page.sel ? (page.sel.rating_note || "") : "" }
                            KeyValue { k: "Обновлено"; muted: true
                                       v: page.sel && page.sel.updated_at ? page.sel.updated_at.replace("T", " ").substring(0, 16) + " UTC" : "" } } }

                    // ---------- содержимое: оборудование
                    Rectangle {
                        visible: page.sel !== null && page.tab === "equipment"
                        Layout.fillWidth: true; implicitHeight: eBody.implicitHeight + Theme2.pad * 2
                        radius: Theme2.radius; color: Theme2.surfaceAlt; border.color: Theme2.border
                        ColumnLayout { id: eBody
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme2.pad }
                            spacing: 10
                            KeyValue { k: "Код"; v: page.sel ? page.sel.code : "" }
                            KeyValue { k: "Наименование"; v: page.sel ? page.sel.name : "" }
                            KeyValue { k: "Категория"; v: page.sel ? page.sel.category : "" }
                            KeyValue { k: "Изготовитель"; v: page.sel ? (page.sel.manufacturer || "") : "" }
                            Repeater { model: page.sel ? Object.keys(page.sel.params || {}) : []
                                delegate: KeyValue { k: modelData; v: String((page.sel.params || {})[modelData]) } }
                            KeyValue { k: "Обновлено"; muted: true
                                       v: page.sel && page.sel.updated_at ? page.sel.updated_at.replace("T", " ").substring(0, 16) + " UTC" : "" } } }

                    HistoryList { visible: page.sel !== null; Layout.fillWidth: true
                                  objectType: page.tab === "articles" ? "kb_article" : page.tab
                                  objectId: page.selectedId }
                }
            }
        }
    }

    Cmd { id: archCmd }

    // ---------- форма статьи (офлайн-очередь)
    FormDialog {
        id: aForm
        property string editId: ""
        property int editVersion: 0
        property string editScope: ""          // при правке раздел и проект менять нельзя
        title: editId ? "Изменить статью" : "Новая статья"
        subtitle: "Можно сохранять без связи: изменение уйдёт на сервер при синхронизации."
        canConfirm: aCode.text.trim() !== "" && aTitle.text.trim() !== "" && aBody.text.trim() !== ""
        onConfirmed: {
            var payload = { id: editId || backend.newId(), kind: ["standard", "template_ref", "note", "howto"][aKind.currentIndex],
                            code: aCode.text.trim(), title: aTitle.text.trim(), body_md: aBody.text.trim(),
                            tags: page.tagsPayload(aTags.text) }
            if (!editId) {
                payload.scope = aScope.currentIndex === 0 ? "company" : "project"
                if (payload.scope === "project") payload.project_id = aProject.currentValue
            }
            cmd.run(editId ? "kb.update" : "kb.create", payload, editVersion)
        }
        VSelect { id: aScope; label: "Раздел"; enabled: aForm.editId === ""
                  model: [{ id: "company", label: "Компания — видят все сотрудники" },
                          { id: "project", label: "Проект — видят участники проекта" }]
                  hint: aForm.editId === "" ? "Стандарт компании пишут ведущий инженер и руководитель отдела; статью проекта — его участники."
                                            : "Раздел статьи не меняется." }
        VSelect { id: aProject; label: "Проект"; visible: aForm.editId === "" && aScope.currentIndex === 1
                  model: backend.projects
                  hint: "Статья будет видна участникам этого проекта." }
        VSelect { id: aKind; label: "Вид"
                  model: [{ id: "standard", label: "Стандарт (СТО)" }, { id: "template_ref", label: "Шаблон" },
                          { id: "note", label: "Заметка" }, { id: "howto", label: "Инструкция" }] }
        VField { id: aCode; label: "Код"; required: true; placeholder: "СТО-01.03"
                 hint: "Человекочитаемый, уникален в разделе: по нему статью ищут и цитируют." }
        VField { id: aTitle; label: "Заголовок"; required: true; placeholder: "Порядок согласования изменений РКД" }
        Text { text: "Текст статьи *"; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS
               VTipArea { text: "Можно использовать Markdown-разметку: заголовки, списки, таблицы." } }
        ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 140
            TextArea { id: aBody; objectName: "kbBodyField"; placeholderText: "1. Изменение оформляют извещением…"
                       color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: TextEdit.Wrap
                       background: Rectangle { radius: Theme.radiusS; color: Theme.surface
                                               border.color: aBody.activeFocus ? Theme.primary : Theme.border } } }
        VField { id: aTags; label: "Теги"; placeholder: "ркд, согласование"
                 hint: "Через запятую, до 12 штук. По тегам статьи ищутся." }
    }
    Cmd { id: cmd; dialog: aForm; onDone: r => { if ((r.status === "accepted" || r.status === "queued") && r.result && r.result.kb_article) page.selectedId = r.result.kb_article.id } }

    // ---------- форма подрядчика (только онлайн)
    FormDialog {
        id: vForm
        property string editId: ""
        property int editVersion: 0
        title: editId ? "Изменить подрядчика" : "Новый подрядчик"
        subtitle: "Общая база компании; правки — только при связи с сервером."
        canConfirm: vName.text.trim() !== "" && vInn.error === ""
        onConfirmed: {
            var payload = { id: editId || backend.newId(), name: vName.text.trim(),
                            kind: ["supplier", "contractor", "both"][vKind.currentIndex],
                            contacts: {} }
            if (vInn.text.trim() !== "") payload.inn = vInn.text.trim()
            else if (editId) payload.inn = null
            if (vPhone.text.trim() !== "") payload.contacts.phone = vPhone.text.trim()
            if (vEmail.text.trim() !== "") payload.contacts.email = vEmail.text.trim()
            if (vNote.text.trim() !== "") payload.rating_note = vNote.text.trim()
            else if (editId) payload.rating_note = null
            vCmd.run(editId ? "vendor.update" : "vendor.create", payload, editVersion)
        }
        VField { id: vName; label: "Наименование"; required: true; placeholder: "ООО «Монтажстрой»" }
        VSelect { id: vKind; label: "Вид"
                  model: [{ id: "supplier", label: "Поставщик" }, { id: "contractor", label: "Подрядчик" },
                          { id: "both", label: "Поставщик и подрядчик" }] }
        VField { id: vInn; label: "ИНН"; placeholder: "10 или 12 цифр"
                 error: text !== "" && !/^(\d{10}|\d{12})$/.test(text.trim()) ? "ИНН — 10 или 12 цифр." : ""
                 hint: "Контрольная сумма ИНН пока не проверяется." }
        VField { id: vPhone; label: "Телефон"; placeholder: "+7 900 000-00-00" }
        VField { id: vEmail; label: "E-mail"; placeholder: "info@montazhstroy.ru" }
        VField { id: vNote; label: "Опыт работы"; placeholder: "Монтаж КИПиА на котельной, 2025 — без нареканий"
                 hint: "Свободный комментарий: где работали, качество, замечания." }
    }
    Cmd { id: vCmd; dialog: vForm; onDone: r => { if (r.status === "accepted" && r.result && r.result.vendor) page.selectedId = r.result.vendor.id } }

    // ---------- форма оборудования (только онлайн)
    FormDialog {
        id: eForm
        property string editId: ""
        property int editVersion: 0
        title: editId ? "Изменить оборудование" : "Новое оборудование"
        subtitle: "Каталог оборудования компании; правки — только при связи с сервером."
        canConfirm: eCode.text.trim() !== "" && eName.text.trim() !== "" && eCat.text.trim() !== "" && eParams.error === ""
        onConfirmed: {
            var payload = { id: editId || backend.newId(), code: eCode.text.trim(), name: eName.text.trim(),
                            category: eCat.text.trim(), params: page.parseParams(eParams.text) || {} }
            if (eMfr.text.trim() !== "") payload.manufacturer = eMfr.text.trim()
            else if (editId) payload.manufacturer = null
            eCmd.run(editId ? "equipment.update" : "equipment.create", payload, editVersion)
        }
        VField { id: eCode; label: "Код"; required: true; placeholder: "ПН-100"
                 hint: "Уникальный код позиции в каталоге." }
        VField { id: eName; label: "Наименование"; required: true; placeholder: "Насос центробежный К 80-65-160" }
        VField { id: eCat; label: "Категория"; required: true; placeholder: "Насосы" }
        VField { id: eMfr; label: "Изготовитель"; placeholder: "АО «Гидромаш»" }
        VField { id: eParams; label: "Характеристики"; placeholder: "подача=50 м³/ч; напор=32 м; мощность=7.5 кВт"
                 error: text.trim() !== "" && page.parseParams(text) === null ? "Формат: параметр=значение, через «;»." : ""
                 hint: "Формат: параметр=значение, через точку с запятой." }
    }
    Cmd { id: eCmd; dialog: eForm; onDone: r => { if (r.status === "accepted" && r.result && r.result.equipment) page.selectedId = r.result.equipment.id } }
}
