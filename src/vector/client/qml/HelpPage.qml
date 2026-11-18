import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Раздел «Сокращения и подсказки» (D-37, просьба владельца: «аббревиатуры обязательно расшифровывай и делай подсказки
// для всего»). Открывается пунктом меню и клавишей F1, доступен всем и без связи (справочник встроен в программу).
// * Слева — справочник сокращений из hints.GLOSSARY (тот же, что во всплывающих подсказках и в руководстве):
//   сокращение, полное название, пояснение «что это у нас». Поиск — по сокращению, названию и пояснению.
// * Справа — как пользоваться подсказками и горячие клавиши.
Item {
    id: page
    objectName: "helpPage"
    property var shell: null
    // Строки справочника, подходящие под поиск (без учёта регистра и «ё/е»).
    readonly property var rows: {
        var q = search.text.trim().toLowerCase().replace(/ё/g, "е")
        var all = backend.glossary || []
        if (q === "") return all
        return all.filter(r => (r.abbr + " " + r.full + " " + r.note).toLowerCase().replace(/ё/g, "е").indexOf(q) >= 0)
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 28
        spacing: 16
        PageHeader {
            title: "Сокращения и подсказки"
            subtitle: "Наведите мышь на кнопку, пункт меню, вкладку, метку статуса или подпись поля — появится подсказка."
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            spacing: 16

            // ---- справочник сокращений
            VCard {
                Layout.fillWidth: true; Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 20
                    spacing: 12
                    SectionTitle { text: "Сокращения (" + page.rows.length + ")"; iconName: "circle-help" }
                    VField { id: search; objectName: "glossarySearch"; placeholder: "Найти: ТКП, НДС, наряд, единица…"
                             tip: "Поиск по сокращению, полному названию и пояснению." }
                    EmptyState { visible: page.rows.length === 0; Layout.fillWidth: true; Layout.topMargin: 24
                                 iconName: "search"; title: "Ничего не найдено"; text: "Измените запрос." }
                    ListView {
                        id: list
                        objectName: "glossaryList"
                        Layout.fillWidth: true; Layout.fillHeight: true
                        clip: true; spacing: 2
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}
                        model: page.rows
                        delegate: Rectangle {
                            required property var modelData
                            width: ListView.view.width
                            implicitHeight: gRow.implicitHeight + 14
                            radius: Theme.radiusS
                            color: gHover.hovered ? Theme.hover : "transparent"
                            HoverHandler { id: gHover }
                            Accessible.role: Accessible.ListItem
                            Accessible.name: modelData.abbr + " — " + modelData.full
                            RowLayout {
                                id: gRow
                                anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 8; anchors.rightMargin: 8
                                spacing: 14
                                Text { text: modelData.abbr; color: Theme.primary; font.family: Theme.family; font.pixelSize: Theme.fs
                                       font.weight: Font.Bold; Layout.preferredWidth: 110; Layout.alignment: Qt.AlignTop; elide: Text.ElideRight }
                                ColumnLayout { spacing: 2; Layout.fillWidth: true
                                    Text { text: modelData.full; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs
                                           wrapMode: Text.Wrap; Layout.fillWidth: true }
                                    Text { visible: modelData.note !== ""; text: modelData.note; color: Theme.textMuted
                                           font.family: Theme.family; font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true } }
                            }
                        }
                    }
                }
            }

            // ---- как пользоваться и горячие клавиши
            VCard {
                Layout.preferredWidth: 380; Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 20
                    spacing: 10
                    SectionTitle { text: "Как работают подсказки"; iconName: "info" }
                    Repeater {
                        model: [
                            "Наведите мышь и подождите полсекунды — появится пояснение: что сделает кнопка, что в разделе, что значит статус.",
                            "В подсказке кнопки сказано, нужна ли связь с сервером: «Только при связи» или «Можно без связи — уйдёт при подключении».",
                            "Сокращения в тексте подсказки расшифрованы ниже основного пояснения.",
                            "Если текст не помещается и обрезан многоточием, подсказка покажет его полностью.",
                            "Неактивная (серая) кнопка подсказку не показывает — причина обычно написана в плашке рядом."
                        ]
                        delegate: RowLayout {
                            required property string modelData
                            Layout.fillWidth: true; spacing: 8
                            Rectangle { width: 5; height: 5; radius: 3; color: Theme.textMuted; Layout.alignment: Qt.AlignTop; Layout.topMargin: 7 }
                            Text { text: modelData; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        }
                    }
                    SectionTitle { text: "Горячие клавиши"; iconName: "sliders-horizontal"; Layout.topMargin: 12 }
                    Repeater {
                        model: [["F1", "Сокращения и подсказки (этот раздел)"], ["F5", "Обновить: отправить очередь и получить свежие данные"],
                                ["Ctrl+1", "Заказчики"], ["Ctrl+2", "Тендеры и ТКП"], ["Ctrl+3", "Проекты"], ["Ctrl+4", "Синхронизация"],
                                ["Ctrl+5", "Склад"], ["Ctrl+6", "Закупка"], ["Ctrl+7", "Производство"], ["Ctrl+8", "Изделия и паспорта"],
                                ["Esc", "Закрыть форму без сохранения"]]
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true; spacing: 10
                            Rectangle { implicitWidth: Math.max(56, kt.implicitWidth + 14); implicitHeight: 22; radius: Theme.radiusS
                                        color: Theme.surfaceAlt; border.color: Theme.border
                                Text { id: kt; anchors.centerIn: parent; text: modelData[0]; color: Theme.text; font.family: Theme.family
                                       font.pixelSize: Theme.fsXs; font.weight: Font.DemiBold } }
                            Text { text: modelData[1]; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fs
                                   wrapMode: Text.Wrap; Layout.fillWidth: true }
                        }
                    }
                    Item { Layout.fillHeight: true }
                }
            }
        }
    }
}
