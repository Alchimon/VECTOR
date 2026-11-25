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
    property string tab: "glossary"          // "glossary" — сокращения; "guide" — руководство; "about" — о программе и лицензии
    property int guideIdx: 0                 // выбранный раздел руководства
    readonly property var guide: backend.helpSections || []
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
            title: "Справка"
            subtitle: "Наведите мышь на кнопку, пункт меню, вкладку, метку статуса или подпись поля — появится подсказка."
        }
        // Переключение: справочник сокращений ↔ встроенное руководство пользователя.
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            VButton { objectName: "tabGlossary"; text: "Сокращения и подсказки"; iconName: "circle-help"
                      kind: page.tab === "glossary" ? "primary" : "soft"; onClicked: page.tab = "glossary" }
            VButton { objectName: "tabGuide"; text: "Руководство"; iconName: "file-text"
                      kind: page.tab === "guide" ? "primary" : "soft"; onClicked: page.tab = "guide"
                      tip: "Полное руководство пользователя встроено в программу и доступно без связи." }
            // «О программе» (D-81): версия, лицензия установки, состояние модулей — честно и всегда доступно.
            VButton { objectName: "tabAbout"; text: "О программе"; iconName: "info"
                      kind: page.tab === "about" ? "primary" : "soft"; onClicked: page.tab = "about"
                      tip: "Версия программы, лицензия (заказчик, вид, сроки, поддержка) и состояние модулей." }
            Item { Layout.fillWidth: true }
        }
        RowLayout {
            visible: page.tab === "glossary"
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
                        model: [["F1", "Справка (этот раздел)"], ["F5", "Обновить: отправить очередь и получить свежие данные"],
                                ["Ctrl+1", "Заказчики"], ["Ctrl+2", "Тендеры и ТКП"], ["Ctrl+3", "Проекты"], ["Ctrl+4", "Синхронизация"],
                                ["Ctrl+5", "Склад"], ["Ctrl+6", "Закупка"], ["Ctrl+7", "Производство"], ["Ctrl+8", "Изделия и паспорта"], ["Ctrl+9", "Качество"], ["Ctrl+0", "Рабочее время"],
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

        // ---- встроенное руководство пользователя (issue #2): разделы слева, текст справа; работает без связи
        RowLayout {
            visible: page.tab === "guide"
            Layout.fillWidth: true; Layout.fillHeight: true
            spacing: 16

            VCard {
                Layout.preferredWidth: 320; Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 20
                    spacing: 10
                    SectionTitle { text: "Разделы (" + page.guide.length + ")"; iconName: "file-text" }
                    ListView {
                        id: guideList
                        objectName: "guideSections"
                        Layout.fillWidth: true; Layout.fillHeight: true
                        clip: true; spacing: 2
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}
                        model: page.guide
                        delegate: Rectangle {
                            required property var modelData
                            required property int index
                            width: ListView.view.width
                            implicitHeight: gsRow.implicitHeight + 12
                            radius: Theme.radiusS
                            color: page.guideIdx === index ? Theme.surfaceAlt : (gsHover.hovered ? Theme.hover : "transparent")
                            HoverHandler { id: gsHover }
                            Accessible.role: Accessible.ListItem
                            Accessible.name: modelData.title
                            TapHandler { onTapped: page.guideIdx = index }
                            Text {
                                id: gsRow
                                anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 10; anchors.rightMargin: 10
                                text: modelData.title
                                color: page.guideIdx === index ? Theme.primary : Theme.text
                                font.family: Theme.family; font.pixelSize: Theme.fs
                                font.weight: page.guideIdx === index ? Font.DemiBold : Font.Normal
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }
            }

            VCard {
                Layout.fillWidth: true; Layout.fillHeight: true
                Flickable {
                    id: guideBody
                    anchors.fill: parent; anchors.margins: 24
                    contentWidth: width
                    contentHeight: guideText.implicitHeight
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}
                    Text {
                        id: guideText
                        objectName: "guideBody"
                        width: guideBody.width
                        text: page.guide.length > 0 ? page.guide[Math.min(page.guideIdx, page.guide.length - 1)].body : ""
                        textFormat: Text.MarkdownText
                        color: Theme.text
                        font.family: Theme.family; font.pixelSize: Theme.fs
                        wrapMode: Text.Wrap
                        lineHeight: 1.25
                    }
                }
            }
        }

        // ---- «О программе»: версия, лицензия и модули (D-81). Лицензия — из последнего снимка (pull); без
        // файла лицензии — режим демо/стенда. Решение о доступе принимает сервер; здесь — честная информация.
        Flickable {
            visible: page.tab === "about"
            Layout.fillWidth: true; Layout.fillHeight: true
            contentWidth: width; contentHeight: aboutCol.implicitHeight
            clip: true; boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            ColumnLayout {
                id: aboutCol
                width: parent.width; spacing: 12
                VCard {
                    Layout.fillWidth: true
                    ColumnLayout {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.margins: 20
                        spacing: 6
                        SectionTitle { text: "ВЕКТОР — рабочая среда инженерной компании"; iconName: "info" }
                        KeyValue { k: "Версия программы"; v: backend.licenseInfo.app_version || "" }
                        KeyValue { k: "Режим"; v: backend.licenseInfo.mode === "licensed" ? "Лицензия" : "Демо / тестовый стенд" }
                        KeyValue { visible: backend.licenseInfo.mode === "licensed"; k: "Заказчик"; v: backend.licenseInfo.customer || "" }
                        KeyValue { visible: backend.licenseInfo.mode === "licensed"; k: "Лицензия"; v: (backend.licenseInfo.kind_label_ru || "")
                                     + (backend.licenseInfo.valid_until ? " до " + backend.licenseInfo.valid_until : "") }
                        KeyValue { objectName: "aboutSupport"; k: "Поддержка"; v: backend.licenseInfo.support_label_ru || "" }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.Wrap
                            text: "Истёкшая подписка не блокирует данные: раздел переходит в режим «только чтение» — смотреть, печатать и выгружать можно всегда."
                            color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS
                        }
                    }
                }
                VCard {
                    Layout.fillWidth: true
                    ColumnLayout {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.margins: 20
                        spacing: 6
                        SectionTitle { text: "Модули"; iconName: "layers" }
                        Repeater {
                            model: backend.licenseInfo.modules || []
                            delegate: RowLayout {
                                required property var modelData
                                width: parent ? parent.width : 0; spacing: 10
                                Text { text: modelData.title_ru; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs
                                       Layout.fillWidth: true; elide: Text.ElideRight }
                                Text { text: modelData.label_ru; font.family: Theme.family; font.pixelSize: Theme.fsS
                                       color: modelData.state === "active" ? Theme.success
                                            : modelData.state === "grace" ? Theme.warning
                                            : Theme.textMuted }
                            }
                        }
                    }
                }
            }
        }
    }
}
