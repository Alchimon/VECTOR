import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Раздел «Проекты»: список проектов (поиск по шифру и названию) и карточка выбранного проекта:
// тендер-основание, команда проекта (назначения ГИП/РП/участников), комплекты, проектный итог (ГИП/РП/бухгалтерия).
// selectedKitId — открытый комплект: под карточкой показывается KitWork (задачи, документы, расчёт).
// shell.navTarget позволяет открыть проект из другого раздела (например, из тендера).
MasterDetail {
    id: page
    property var shell
    property string selectedId: shell && shell.navTarget.page === "projects" ? shell.navTarget.id : ""
    readonly property var items: backend.projects.filter(p => search === "" || (p.code + " " + p.title).toLowerCase().indexOf(search.toLowerCase()) >= 0)
    readonly property var sel: backend.projects.find(p => p.id === selectedId) || null
    readonly property var selKits: sel ? backend.kits.filter(k => k.project_id === sel.id) : []
    readonly property var team: sel ? backend.assignments.filter(a => a.project_id === sel.id) : []
    // Кто сейчас замещает в проекте (D-35): «Лебедев Максим — ГИП, замещает Волкова Павла до 22.11.2026».
    readonly property var deputies: sel ? backend.projectDeputies.filter(d => d.project_id === sel.id) : []
    // Итог проекта видят ГИП/РП проекта и бухгалтерия (A-18); сервер всё равно не пришлёт ревизии остальным.
    readonly property bool canSeeTotal: sel !== null && ((backend.me.roles || []).indexOf("accountant") >= 0
        || team.some(a => a.user_id === backend.me.user_id && (a.project_role === "gip" || a.project_role === "rp")))
    // Выбранный комплект: его задачи и документы показываются ниже сетки комплектов (KitWork).
    // Если комплект один — выбираем его сразу, чтобы не заставлять делать лишний щелчок.
    property string selectedKitId: ""
    readonly property var selKit: selKits.find(k => k.id === selectedKitId) || (selKits.length === 1 ? selKits[0] : null)
    onSelectedIdChanged: selectedKitId = ""
    title: "Проекты"
    subtitle: backend.projects.length + " доступно вам"
    hasSelection: sel !== null
    emptyIcon: "folder-kanban"; emptyTitle: "Проектов пока нет"
    emptyText: "Проект появляется после явного действия «Начать РКД» по выигранному тендеру — или когда вас назначат в проект."
    noSelectionText: "Выберите проект"

    headerActions: [ VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() } ]
    list.model: items
    list.delegate: ListRow {
        title: modelData.code + "  ·  " + modelData.title
        subtitle: modelData.kit_count + " компл.  ·  " + (modelData.tender_title || "")
        iconName: "folder-kanban"
        chipText: modelData.status_label; chipTone: "info"
        selected: modelData.id === page.selectedId
        onClicked: page.selectedId = modelData.id
    }

    RowLayout {
        Layout.fillWidth: true; spacing: 14
        Rectangle { width: 52; height: 52; radius: 14; color: Theme.infoSoft
            Icon { anchors.centerIn: parent; name: "folder-kanban"; size: 26; color: Theme.info } }
        ColumnLayout { spacing: 4; Layout.fillWidth: true
            Text { text: sel ? sel.code : ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; font.letterSpacing: 0.5 }
            Text { text: sel ? sel.title : ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsXl; font.weight: Font.Bold; wrapMode: Text.Wrap; Layout.fillWidth: true }
            RowLayout { spacing: 6
                VChip { text: sel ? sel.status_label : ""; tone: "info" }
                VChip { text: sel ? "версия " + sel.version : ""; tone: "neutral" } } }
        VButton { text: "Тендер"; iconName: "gavel"; visible: sel && backend.hint("tender.read", backend.permRevision); onClicked: page.shell.go("tenders", sel.tender_id) }
    }

    // ---------- команда проекта
    RowLayout { Layout.fillWidth: true
        SectionTitle { text: "Команда проекта"; iconName: "users" }
        VButton { text: "Назначить"; kind: "soft"; compact: true; iconName: "user-plus"; onClicked: assign.open() } }
    Banner { visible: page.team.length > 0 && !page.team.some(a => a.project_role === "gip"); tone: "info"; text: "ГИП (главный инженер проекта) не назначен. Назначает только администратор." }
    Flow {
        Layout.fillWidth: true; spacing: 8
        Repeater {
            model: page.team
            delegate: Rectangle {
                height: 40; width: tr.implicitWidth + 20; radius: 20; color: Theme.surfaceAlt; border.color: Theme.border
                RowLayout { id: tr; anchors.centerIn: parent; spacing: 8
                    Rectangle { width: 26; height: 26; radius: 13; color: modelData.project_role === "gip" ? Theme.primarySoft : Theme.surface
                        Text { anchors.centerIn: parent; text: (modelData.user_name || "?").substring(0, 1); color: Theme.primary; font.family: Theme.family; font.pixelSize: Theme.fsS; font.weight: Font.Bold } }
                    Text { text: modelData.user_name; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs }
                    VChip { text: modelData.role_label; tone: modelData.project_role === "gip" ? "primary" : modelData.project_role === "rp" ? "info" : "neutral" }
                    Icon { name: "x"; size: 14
                        MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor
                                    onClicked: { revoke.aid = modelData.id; revoke.who = modelData.user_name + " — " + modelData.role_label; revoke.open() } } } }
            }
        }
    }
    Text { visible: page.team.length === 0; text: "Назначений нет (или список недоступен офлайн)."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
    Flow {
        objectName: "projectDeputies"
        visible: page.deputies.length > 0
        Layout.fillWidth: true; spacing: 8
        Repeater {
            model: page.deputies
            delegate: Rectangle {
                height: 36; width: dr.implicitWidth + 20; radius: 18; color: Theme.surface; border.color: Theme.border
                RowLayout { id: dr; anchors.centerIn: parent; spacing: 8
                    Icon { name: "user-check"; size: 14; color: Theme.primary }
                    Text { text: modelData.user_name + " — " + modelData.role_label + ", замещает " + modelData.for_name + " до " + modelData.until_label
                           color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS } }
            }
        }
    }

    // ---------- комплекты
    RowLayout { Layout.fillWidth: true; Layout.topMargin: 6
        SectionTitle { text: "Комплекты (" + page.selKits.length + ")"; iconName: "layers" }
        VButton { text: "Новый комплект"; kind: "soft"; compact: true; iconName: "plus"; onClicked: { kitForm.editId = ""; kitForm.open() } } }
    GridLayout {
        Layout.fillWidth: true; columns: Math.max(1, Math.floor(width / 260)); columnSpacing: 10; rowSpacing: 10
        Repeater {
            model: page.selKits
            delegate: VCard {
                id: kitCard
                readonly property bool current: page.selKit !== null && page.selKit.id === modelData.id
                Layout.fillWidth: true; implicitHeight: 104
                border.color: current ? Theme.primary : Theme.border
                border.width: current ? 2 : 1
                activeFocusOnTab: true
                Accessible.role: Accessible.Button; Accessible.name: "Комплект " + modelData.code
                Keys.onReturnPressed: page.selectedKitId = modelData.id
                Keys.onSpacePressed: page.selectedKitId = modelData.id
                // Щелчок по карточке выбирает комплект; кнопка «изменить» поверх обрабатывает свой щелчок сама.
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.selectedKitId = modelData.id }
                ColumnLayout { anchors.fill: parent; anchors.margins: 14; spacing: 4
                    RowLayout { Layout.fillWidth: true
                        Text { text: modelData.code; color: Theme.primary; font.family: Theme.mono; font.pixelSize: Theme.fsM; font.weight: Font.Bold; Layout.fillWidth: true }
                        VChip { visible: modelData.pending; text: "Не отправлено"; tone: "warning"; iconName: "clock" }
                        VButton { kind: "ghost"; compact: true; iconName: "pencil"; Accessible.name: "Изменить комплект"; enabled: modelData.version > 0
                                  onClicked: { kitForm.editId = modelData.id; kitForm.editVersion = modelData.version; kCode.text = modelData.code; kTitle.text = modelData.title; kitForm.open() } } }
                    Text { text: modelData.title; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; elide: Text.ElideRight; Layout.fillWidth: true }
                    Text { text: "Задачи: " + modelData.open_task_count + " откр. из " + modelData.task_count + "  ·  Документы: " + modelData.doc_count
                           color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs }
                    Text { text: modelData.calc_label || ""; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs } }
            }
        }
    }
    Text { // подсказка: расшифровка сокращений в тексте (D-37)
        VTipArea { text: backend.tip(parent.text) }
        visible: page.selKits.length === 0; text: "Комплектов пока нет. Создать комплект может РП проекта."; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
    Text { visible: page.selKits.length > 1 && page.selKit === null; text: "Выберите комплект, чтобы увидеть его задачи и документы."
           color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs }
    KitWork { Layout.fillWidth: true; kit: page.selKit; team: page.team; deputies: page.deputies }

    // ---------- потоки, риск-разрешения и журнал событий проекта (D-65, этап 2 blueprint 52)
    ProjectFlow { Layout.fillWidth: true; Layout.topMargin: 6; project: page.sel; visible: page.sel !== null }

    // ---------- ПНР, испытания (FAT) и документы доставки (D-70, этап 6 blueprint 52)
    ProjectPnrFat { Layout.fillWidth: true; Layout.topMargin: 6; project: page.sel; visible: page.sel !== null }

    // ---------- аналитика проекта: план/факт потоков, сводка ПНР/FAT (D-71, этап 7 blueprint 52)
    ProjectAnalytics { Layout.fillWidth: true; Layout.topMargin: 6; project: page.sel; visible: page.sel !== null }

    // ---------- официальный итог проекта: только из зафиксированных версий расчётов комплектов (R-12)
    ProjectTotal { Layout.fillWidth: true; Layout.topMargin: 6; projectId: page.selectedId
                   visible: page.sel !== null && page.canSeeTotal }
    HistoryList { objectType: "project"; objectId: page.selectedId }

    // ---------- формы
    FormDialog {
        id: kitForm
        property string editId: ""
        property int editVersion: 0
        title: editId ? "Изменить комплект" : "Новый комплект"
        subtitle: "Код комплекта уникален в пределах проекта. Можно сохранять без связи."
        canConfirm: kCode.text.trim() !== "" && kTitle.text.trim() !== ""
        onOpened: if (!editId) { kCode.text = ""; kTitle.text = "" }
        onConfirmed: editId ? kCmd.run("kit.update", { id: editId, code: kCode.text.trim(), title: kTitle.text.trim() }, editVersion)
                            : kCmd.run("kit.create", { id: backend.newId(), project_id: sel.id, code: kCode.text.trim(), title: kTitle.text.trim() })
        VField { id: kCode; label: "Код (марка)"; required: true; placeholder: "АТХ" }
        VField { id: kTitle; label: "Наименование"; required: true; placeholder: "Автоматизация технологических процессов" }
    }
    Cmd { id: kCmd; dialog: kitForm }

    FormDialog {
        id: assign
        title: "Назначить в проект"
        subtitle: "ГИП (главного инженера проекта) назначает только администратор; РП (руководителя проекта) — администратор или ГИП; участников — ГИП или РП проекта. Проверяет сервер."
        confirmText: "Назначить"
        canConfirm: aWho.currentValue !== "" && aReason.text.trim() !== ""
        onOpened: aReason.text = ""
        onConfirmed: aCmd.run("project.assign", { project_id: sel.id, user_id: aWho.currentValue, project_role: aRole.currentValue, reason: aReason.text.trim() })
        VSelect { id: aWho; label: "Сотрудник"; model: backend.people; textRole: "display_name" }
        VSelect { id: aRole; label: "Роль в проекте"; model: backend.projectRoleOptions }
        VField { id: aReason; label: "Основание"; required: true; placeholder: "Приказ № … / распределение работ" }
    }
    Cmd { id: aCmd; dialog: assign }

    FormDialog {
        id: revoke
        property int aid: 0
        property string who: ""
        title: "Отозвать назначение"
        subtitle: who
        confirmText: "Отозвать"; confirmKind: "danger"
        canConfirm: rvReason.text.trim() !== ""
        onOpened: rvReason.text = ""
        onConfirmed: rvCmd.run("project.revoke_assignment", { assignment_id: aid, reason: rvReason.text.trim() })
        VField { id: rvReason; label: "Основание"; required: true }
    }
    Cmd { id: rvCmd; dialog: revoke }
}
