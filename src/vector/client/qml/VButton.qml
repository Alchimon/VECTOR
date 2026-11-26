import QtQuick
import QtQuick.Controls.Basic

// kind: primary | secondary | ghost | danger | soft
// tip — всплывающая подсказка (D-37). По умолчанию берётся из словаря подсказок по тексту кнопки (у кнопки-иконки —
// по Accessible.name): что сделает кнопка, нужна ли связь, расшифровка сокращений. Своё пояснение — tip: "…".
// Если в словаре нет ни пояснения, ни сокращений, у кнопки-иконки подсказкой служит её Accessible.name.
Button {
    id: b
    property string kind: "secondary"
    property string iconName: ""
    property bool compact: false
    property string tip: backend.tip(b.text !== "" ? b.text : b.Accessible.name)
    readonly property string tipText: tip !== "" ? tip : (b.text === "" ? b.Accessible.name : "")
    implicitHeight: compact ? 30 : 36
    leftPadding: compact ? 10 : 14
    rightPadding: compact ? 10 : 14
    font.family: Theme.family
    font.pixelSize: Theme.fs
    font.weight: Font.DemiBold
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: text
    Accessible.description: tipText
    VToolTip { visible: b.hovered && b.tipText !== ""; text: b.tipText }

    readonly property color fg: !enabled ? Theme.textFaint
        : kind === "primary" ? Theme.textOnPrimary
        : kind === "danger" ? Theme.danger
        : kind === "soft" ? Theme.primary
        : Theme.text
    readonly property color bgc: !enabled ? Theme.surfaceAlt
        : kind === "primary" ? (b.down ? Theme.primaryHover : b.hovered ? Theme.primaryHover : Theme.primary)
        : kind === "danger" ? (b.hovered ? Theme.dangerSoft : Theme.surface)
        : kind === "soft" ? (b.hovered ? Qt.darker(Theme.primarySoft, Theme.dark ? 0.85 : 1.04) : Theme.primarySoft)
        : kind === "ghost" ? (b.hovered ? Theme.hover : "transparent")
        : (b.hovered ? Theme.hover : Theme.surface)

    contentItem: Item {
        implicitWidth: row.implicitWidth
        implicitHeight: row.implicitHeight
        Row {
            id: row
            spacing: 7
            anchors.centerIn: parent
            Icon { visible: b.iconName !== ""; name: b.iconName; color: b.fg; size: 16; anchors.verticalCenter: parent.verticalCenter }
            Text {
                visible: b.text !== ""
                text: b.text; color: b.fg; font: b.font
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
    background: Rectangle {
        radius: Theme.radius - 2
        color: b.bgc
        border.width: (b.kind === "secondary" || b.kind === "danger") ? 1 : 0
        border.color: b.kind === "danger" ? Qt.alpha(Theme.danger, 0.35) : Theme.border
        Behavior on color { ColorAnimation { duration: 120 } }
        Rectangle {
            anchors.fill: parent; anchors.margins: -3
            radius: parent.radius + 3
            color: "transparent"
            border.width: 2; border.color: Qt.alpha(Theme.focus, 0.55)
            visible: b.visualFocus
        }
    }
}
