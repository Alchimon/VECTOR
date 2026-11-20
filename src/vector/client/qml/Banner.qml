import QtQuick
import QtQuick.Layouts

// Плашка-сообщение на всю ширину: tone — info | success | warning | danger (цвет и иконка из Theme.tone),
// text — текст. Используется для состояний «офлайн», «нет доступа», «данные на момент…».
// tip — подсказка при наведении (D-37): по умолчанию расшифровка сокращений из текста (ТП, РКД, SHA-256…).
Rectangle {
    id: bn
    property string tone: "info"
    property string text: ""
    property string tip: backend.tip(text)
    property string iconName: tone === "danger" ? "circle-x" : tone === "warning" ? "triangle-alert" : tone === "success" ? "circle-check" : "info"
    readonly property var t: Theme.tone(tone)
    visible: text !== ""
    Layout.fillWidth: true
    implicitHeight: row.implicitHeight + 20
    radius: Theme.radius
    color: t.bg
    Accessible.role: Accessible.StaticText; Accessible.name: text; Accessible.description: tip
    VTipArea { text: bn.tip }
    RowLayout {
        id: row
        anchors.fill: parent; anchors.margins: 10; anchors.leftMargin: 12
        spacing: 10
        Icon { name: bn.iconName; color: bn.t.fg; size: 16; Layout.alignment: Qt.AlignTop }
        Text { text: bn.text; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs; wrapMode: Text.Wrap; Layout.fillWidth: true }
    }
}
