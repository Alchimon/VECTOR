import QtQuick

// Компактная метка статуса («Черновик», «Выигран», «Не отправлено»): text, tone (цвет из Theme.tone), iconName.
// maxWidth > 0 — длинный текст сокращается многоточием.
// tip — подсказка при наведении (D-37): что значит статус; по умолчанию — из словаря по тексту метки. Если текст
// сокращён многоточием, полный текст тоже показывается в подсказке.
Rectangle {
    id: c
    property string text: ""
    property string tip: backend.tip(text)
    readonly property string tipText: {
        var full = naturalWidth > width + 1 ? c.text : ""
        return full !== "" && tip !== "" ? full + "\n" + tip : (full !== "" ? full : tip)
    }
    property string tone: "neutral"
    property string iconName: ""
    property int maxWidth: 0          // 0 — без ограничения; иначе текст сокращается с «…»
    readonly property var t: Theme.tone(tone)
    implicitHeight: 22
    TextMetrics { id: tm; text: c.text; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.DemiBold }
    readonly property int iconW: iconName !== "" ? 17 : 0
    readonly property int naturalWidth: iconW + Math.ceil(tm.advanceWidth) + 16
    implicitWidth: maxWidth > 0 ? Math.min(maxWidth, naturalWidth) : naturalWidth
    radius: 11
    color: t.bg
    Accessible.role: Accessible.StaticText; Accessible.name: text; Accessible.description: tip
    HoverHandler { id: chipHover }
    VToolTip { visible: chipHover.hovered && c.tipText !== ""; text: c.tipText }
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 5
        Icon { visible: c.iconName !== ""; name: c.iconName; color: c.t.fg; size: 12; anchors.verticalCenter: parent.verticalCenter }
        Text { id: label; text: c.text; elide: Text.ElideRight
            width: c.maxWidth > 0 ? Math.min(Math.ceil(tm.advanceWidth), c.maxWidth - 16 - c.iconW) : Math.ceil(tm.advanceWidth) + 1; color: c.t.fg; font.family: Theme.family; font.pixelSize: Theme.fsXs; font.weight: Font.DemiBold; anchors.verticalCenter: parent.verticalCenter }
    }
}
