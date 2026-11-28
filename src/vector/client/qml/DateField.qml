import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Поле даты (D-136): ввод в формате ГГГГ-ММ-ДД с кнопкой календаря (Popup с DayOfWeekRow/MonthGrid)
// и быстрым выбором «Сегодня» / «+7 дней» / «Очистить». Текстовое значение — в text (как у VField);
// error подсвечивает неверный формат (саму дату, напр. 31 февраля, проверяет сервер).
ColumnLayout {
    id: f
    property string text: ""
    property string label: ""
    property string placeholder: "ГГГГ-ММ-ДД"
    property string hint: ""
    property bool required: false
    // Дата из поля валидна (пусто — тоже валидно: «не задан»/«очистить»).
    readonly property bool valid: text.trim() === "" || /^\d{4}-\d{2}-\d{2}$/.test(text.trim())
    readonly property string error: valid ? "" : "Формат даты: ГГГГ-ММ-ДД"
    property string tip: backend.tip(label)
    readonly property string tipText: tip !== "" ? tip : hint
    spacing: 6

    function _fmt(d) {  // Date → «ГГГГ-ММ-ДД»
        return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0")
    }
    function setToday()      { text = _fmt(new Date()) }
    function setPlusWeek()   { var d = new Date(); d.setDate(d.getDate() + 7); text = _fmt(d) }
    function clearDate()     { text = "" }

    RowLayout {
        spacing: 6
        Text { visible: f.label !== ""; text: f.label + (f.required ? " *" : ""); color: Theme.textMuted
               font.family: Theme.family; font.pixelSize: Theme.fsS
               VTipArea { text: f.tipText } }   // подсказка при наведении на подпись (как в VField)
    }
    RowLayout {
        Layout.fillWidth: true; spacing: 8
        TextField {
            id: input
            Layout.fillWidth: true
            text: f.text
            onTextChanged: f.text = text
            placeholderText: f.placeholder
            color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs
            leftPadding: 10; rightPadding: 10; topPadding: 8; bottomPadding: 8
            Accessible.name: f.label !== "" ? f.label : f.placeholder
            background: Rectangle {
                radius: Theme.radiusS
                color: input.readOnly ? Theme.surfaceAlt : Theme.surface
                border.color: f.error !== "" ? Theme.danger : input.activeFocus ? Theme.focus : Theme.border
            }
        }
        // Календарь (D-136): выбор дня щелчком; «Сегодня»/«+7 дней» — типовые сроки; «Очистить» — снять дату.
        VButton { objectName: "btnDateCalendar"; kind: "soft"; iconName: "calendar"; compact: true
                  tip: "Выбрать дату в календаре."; onClicked: cal.open() }
        VButton { text: "Сегодня"; kind: "ghost"; compact: true; tip: "Подставить сегодняшнюю дату."; onClicked: f.setToday() }
        VButton { text: "+7 дней"; kind: "ghost"; compact: true; tip: "Подставить дату через неделю."; onClicked: f.setPlusWeek() }
        VButton { text: "Очистить"; kind: "ghost"; compact: true; visible: f.text.trim() !== ""
                  tip: "Снять дату (поле пустое — срок не задан)."; onClicked: f.clearDate() }
    }
    Text { visible: f.error !== ""; text: f.error; color: Theme.danger; font.family: Theme.family; font.pixelSize: Theme.fsXs }
    Text { visible: f.error === "" && f.hint !== ""; text: f.hint; color: Theme.textMuted
           font.family: Theme.family; font.pixelSize: Theme.fsXs; wrapMode: Text.Wrap; Layout.fillWidth: true }

    Popup {
        id: cal
        modal: true; focus: true
        x: Math.round((parent ? parent.width : 400) / 2 - width / 2)
        y: input.y + input.height + 6
        padding: 12
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: Theme.surface; radius: Theme.radiusL; border.color: Theme.border }
        contentItem: ColumnLayout {
            spacing: 8
            // Текущий месяц/год и навигация.
            RowLayout {
                Layout.fillWidth: true
                VButton { kind: "ghost"; iconName: "chevron-left"; compact: true; onClicked: grid.month = (grid.month + 11) % 12; Accessible.name: "Предыдущий месяц" }
                Text { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
                       text: Qt.locale().monthName(grid.month) + " " + grid.year
                       color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; font.weight: Font.DemiBold }
                VButton { kind: "ghost"; iconName: "chevron-right"; compact: true; onClicked: grid.month = (grid.month + 1) % 12; Accessible.name: "Следующий месяц" }
            }
            DayOfWeekRow { Layout.fillWidth: true; locale: grid.locale
                           delegate: Text { required property string shortName; text: shortName; horizontalAlignment: Text.AlignHCenter
                                            color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsXs } }
            MonthGrid {
                id: grid
                Layout.fillWidth: true
                locale: Qt.locale()
                delegate: Rectangle {
                    required property var model
                    implicitWidth: 34; implicitHeight: 30; radius: Theme.radiusS
                    color: dayMa.containsMouse ? Theme.hover : "transparent"
                    opacity: model.month === grid.month ? 1 : 0.35
                    border.color: f.text === f._fmt(model.date) ? Theme.primary : "transparent"
                    Text { anchors.centerIn: parent; text: model.day; color: Theme.text
                           font.family: Theme.family; font.pixelSize: Theme.fsS }
                    MouseArea { id: dayMa; anchors.fill: parent; hoverEnabled: true
                                onClicked: { f.text = f._fmt(model.date); cal.close() } }
                    Accessible.name: f._fmt(model.date)
                }
            }
        }
    }
}
