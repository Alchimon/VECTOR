import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

// Окно «Производственный календарь» (этап 7, R-20; D-48 — ответ владельца на A-52 п. 3: «производственный календарь
// дело хорошее, особенно если он будет подтягиваться с нужных ресурсов на каждый год»).
//
//  * Годы — прошлый, текущий, следующий; у каждого — откуда календарь: загружен (xmlcalendar.ru или файл), встроен в
//    программу или его нет (тогда — только дни недели графика).
//  * Особые дни выбранного года: праздники и перенесённые выходные, сокращённые на час, рабочие выходные по переносу;
//    правки администратора помечены.
//  * Администратор (при связи): «Загрузить из интернета» (скачивает этот компьютер — серверу интернет не нужен),
//    «Загрузить из файла» (XML xmlcalendar.ru на флешке), «Изменить день» (региональный праздник, решение компании).
//    С октября программа администратора сама загружает календарь на следующий год.
// Данные — снимок pull (cv = backend.calendarOverview); решает сервер.
Popup {
    id: pop
    objectName: "calendarPopup"
    readonly property var cv: backend.calendarOverview
    modal: true; focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(720, (Overlay.overlay ? Overlay.overlay.width : 800) - 48)
    // Высота — по содержимому, но не выше окна программы.
    height: Math.min(640, (Overlay.overlay ? Overlay.overlay.height : 700) - 48,
                     calHead.implicitHeight + calInfo.implicitHeight + calList.contentHeight + 2 * Theme.padL + 60)
    padding: 0
    closePolicy: Popup.CloseOnEscape
    Overlay.modal: Rectangle { color: Theme.scrim }
    background: Rectangle { color: Theme.surface; radius: Theme.radiusL; border.color: Theme.border }
    contentItem: ColumnLayout {
        spacing: 10
        RowLayout { id: calHead; Layout.fillWidth: true; Layout.margins: Theme.padL; Layout.bottomMargin: 0; spacing: 6
            Icon { name: "calendar-days"; size: 20; color: Theme.textMuted }
            Text { text: "Производственный календарь"; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsL; font.weight: Font.DemiBold; Layout.fillWidth: true }
            VButton { kind: "ghost"; iconName: "x"; compact: true; onClicked: pop.close(); Accessible.name: "Закрыть" } }
        ColumnLayout { id: calInfo; spacing: 8; Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL
            Text { text: pop.cv.hint || ""; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
            RowLayout { Layout.fillWidth: true; spacing: 8
                VSegmented { objectName: "calendarYears"; current: String(pop.cv.year || "")
                             model: (pop.cv.years || []).map(function (y) { return { id: String(y.year), label: String(y.year), icon: "calendar",
                                                                                     tip: "Календарь на " + y.year + " год: " + y.label } })
                             onPicked: function (id) { backend.setCalendarYear(parseInt(id, 10)) } }
                Item { Layout.fillWidth: true }
                VButton { objectName: "btnCalendarDownload"; visible: pop.cv.manage === true; enabled: pop.cv.can_manage === true && !backend.busy
                          text: "Из интернета"; iconName: "download"; compact: true
                          onClicked: { calCmd.err = ""; backend.calendarDownload("calendar-download", pop.cv.year) } }
                VButton { objectName: "btnCalendarFile"; visible: pop.cv.manage === true; enabled: pop.cv.can_manage === true && !backend.busy
                          text: "Из файла"; iconName: "upload"; compact: true; onClicked: fileDlg.open() }
                VButton { objectName: "btnCalendarDay"; visible: pop.cv.manage === true; enabled: pop.cv.can_manage === true
                          text: "Изменить день"; iconName: "pencil"; compact: true; onClicked: dayForm.openFor(null) } }
            KeyValue { objectName: "calendarYearSource"; k: "Календарь на " + (pop.cv.year || "") + " год"; v: pop.cv.year_label || ""; kWidth: 170 }
            KeyValue { k: "Особые дни"; v: pop.cv.counts_label || ""; kWidth: 170 }
            Banner { objectName: "calendarAutoNote"; visible: (pop.cv.auto_note || "") !== ""; tone: "info"; text: pop.cv.auto_note || ""; Layout.fillWidth: true }
            Banner { visible: (pop.cv.block || "") !== ""; tone: "warning"; text: pop.cv.block || ""; Layout.fillWidth: true }
            Banner { objectName: "calendarError"; visible: calCmd.err !== ""; tone: "danger"; text: calCmd.err; Layout.fillWidth: true }
        }
        ListView {
            id: calList
            objectName: "calendarDays"
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.bottomMargin: Theme.padL
            clip: true; spacing: 6; model: pop.cv.rows || []
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle { id: cr; required property var modelData
                objectName: "calendarDayRow"
                width: ListView.view.width; implicitHeight: crow.implicitHeight + 12; radius: Theme.radius; color: Theme.surfaceAlt
                opacity: cr.modelData.past ? 0.7 : 1
                RowLayout { id: crow; anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.margins: 8; spacing: 8
                    Text { text: cr.modelData.date_label + ", " + cr.modelData.weekday; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold; Layout.preferredWidth: 130 }
                    VChip { text: cr.modelData.kind_label; tone: cr.modelData.tone }
                    ColumnLayout { spacing: 0; Layout.fillWidth: true
                        Text { text: cr.modelData.title; visible: text !== ""; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsS; elide: Text.ElideRight; Layout.fillWidth: true }
                        Text { text: cr.modelData.origin_label; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs; elide: Text.ElideRight; Layout.fillWidth: true } }
                    VButton { objectName: "btnCalendarEditDay"; visible: pop.cv.manage === true; enabled: pop.cv.can_manage === true; text: "Изменить"; compact: true; kind: "ghost"; iconName: "pencil"
                              tip: "Поправить этот день: другой тип, название, «обычный день по графику» или убрать правку администратора."
                              onClicked: dayForm.openFor(cr.modelData) }
                }
            }
        }
        Text { visible: pop.cv.empty === true; text: "Особых дней нет — календаря на этот год нет ни в базе, ни в программе: действуют только дни недели графика."
               color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.leftMargin: Theme.padL; Layout.rightMargin: Theme.padL; Layout.bottomMargin: Theme.padL }
    }

    // Итог загрузки из интернета / из файла: ошибка — в окне (уведомление показывает и backend).
    QtObject { id: calCmd; property string err: "" }
    Connections { target: backend
        function onCommandDone(t, r) {
            if (t === "calendar-download" || t === "calendar-file")
                calCmd.err = (r.status === "accepted") ? "" : (r.message_ru || "Календарь не загружен.")
            if (t === "calendar-day") {
                dayForm.working = false
                if (r.status === "accepted") dayForm.close(); else dayForm.error = r.message_ru || "День не сохранён."
            }
        } }

    FileDialog {
        id: fileDlg
        title: "Производственный календарь — файл XML (формат xmlcalendar.ru)"
        fileMode: FileDialog.OpenFile
        nameFilters: ["Календарь XML (*.xml)", "Все файлы (*)"]
        onAccepted: { calCmd.err = ""; backend.calendarImportFile("calendar-file", pop.cv.year, selectedFile.toString()) }
    }

    // ---------- изменить день
    FormDialog {
        id: dayForm
        objectName: "calendarDayForm"
        property var d: null
        title: "Изменить день календаря"
        subtitle: "Региональный праздник, решение компании работать или отдыхать. Правка важнее календаря и остаётся при его повторной загрузке."
        confirmText: "Сохранить день"
        confirmTip: "Сохранить правку дня производственного календаря (только при связи). Графики, которые учитывают календарь, сразу считают этот день по-новому."
        canConfirm: backend.calendarDayIso(dDate.text) !== ""
        function idx(list, id) { for (var i = 0; i < list.length; i++) if (list[i].id === id) return i; return 0 }
        function openFor(x) {
            d = x
            dDate.text = x ? x.date_label : ""
            dKind.currentIndex = idx(dKind.model, x ? (x.manual ? x.kind : "") : "holiday")
            dTitle.text = x ? x.title : ""
            error = ""
            open()
        }
        onConfirmed: { working = true; error = ""; backend.calendarDaySet("calendar-day", dDate.text, dKind.currentValue, dTitle.text.trim()) }
        VField { id: dDate; objectName: "calDayDate"; label: "Дата (ДД.ММ.ГГГГ)"; required: true; placeholder: "30.12.2026"
                 error: text !== "" && backend.calendarDayIso(text) === "" ? "Дата — ДД.ММ.ГГГГ, например 30.12.2026." : "" }
        VSelect { id: dKind; objectName: "calDayKind"; label: "Тип дня"; model: pop.cv.kind_options || [] }
        VField { id: dTitle; objectName: "calDayTitle"; label: "Название (необязательно)"; placeholder: "День города" }
    }
}
