pragma Singleton
import QtQuick

// Токены дизайна ВЕКТОР: пастельная светлая/тёмная темы. Компоненты используют только токены.
QtObject {
    property bool dark: false
    property string family: "Inter"

    readonly property color bg:          dark ? "#11141B" : "#F5F6FA"
    readonly property color sidebar:     dark ? "#151924" : "#FBFBFE"
    readonly property color surface:     dark ? "#1A1F2B" : "#FFFFFF"
    readonly property color surfaceAlt:  dark ? "#212737" : "#F2F4F9"
    readonly property color hover:       dark ? "#252C3D" : "#EEF1F8"
    readonly property color border:      dark ? "#2C3346" : "#E4E7EF"
    readonly property color borderStrong:dark ? "#3A4259" : "#CDD3E0"
    readonly property color text:        dark ? "#E8EBF4" : "#1B2131"
    readonly property color textMuted:   dark ? "#9AA3B8" : "#5F6B82"
    readonly property color textFaint:   dark ? "#6D7690" : "#98A1B3"

    readonly property color primary:     dark ? "#93A5FF" : "#4C63E6"
    readonly property color primaryHover:dark ? "#A9B8FF" : "#3E54CF"
    readonly property color primarySoft: dark ? "#252D52" : "#EAEDFD"
    readonly property color textOnPrimary:   dark ? "#10142A" : "#FFFFFF"
    readonly property color accent:      dark ? "#B9A8FF" : "#8A73F0"

    readonly property color success:     dark ? "#6BD2A4" : "#23875C"
    readonly property color successSoft: dark ? "#1B3A2E" : "#E2F4EA"
    readonly property color warning:     dark ? "#EDBB5C" : "#A86808"
    readonly property color warningSoft: dark ? "#3B3021" : "#FCF1DC"
    readonly property color danger:      dark ? "#F38B8B" : "#C93A3A"
    readonly property color dangerSoft:  dark ? "#432629" : "#FBE7E7"
    readonly property color info:        dark ? "#7DBDEB" : "#2873AA"
    readonly property color infoSoft:    dark ? "#1D3247" : "#E3F0FA"
    readonly property color neutralSoft: dark ? "#262C3B" : "#EEF0F5"

    readonly property color focus:       primary
    readonly property color scrim:       dark ? "#99000000" : "#5C1B2131"

    readonly property int radiusS: 6
    readonly property int radius: 10
    readonly property int radiusL: 14
    readonly property int gap: 8
    readonly property int pad: 16
    readonly property int padL: 24

    readonly property int fsXs: 11
    readonly property int fsS: 12
    readonly property int fs: 13
    readonly property int fsM: 15
    readonly property int fsL: 18
    readonly property int fsXl: 24
    readonly property int fsXxl: 30

    function tone(name) {
        switch (name) {
        case "success": return { fg: success, bg: successSoft }
        case "warning": return { fg: warning, bg: warningSoft }
        case "danger":  return { fg: danger,  bg: dangerSoft }
        case "info":    return { fg: info,    bg: infoSoft }
        case "primary": return { fg: primary, bg: primarySoft }
        default:        return { fg: textMuted, bg: neutralSoft }
        }
    }
    function tenderTone(s) {
        return ({ draft: "neutral", in_work: "info", bid_submitted: "primary", won: "success", lost: "danger", cancelled: "neutral" })[s] || "neutral"
    }
    function tkpTone(s) {
        return ({ draft: "neutral", approved: "success", sent_recorded: "primary" })[s] || "neutral"
    }
}
