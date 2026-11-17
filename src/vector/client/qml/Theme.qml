pragma Singleton
import QtQuick

// Токены дизайна ВЕКТОР: светлая и тёмная темы. Компоненты используют только токены (жёстких цветов в экранах нет).
//
// Палитра выведена из фирменного стиля владельца (D-28, docs/brand/source/):
//  * основной цвет (primary) — сланцево-синий из надписи и сетки логотипа;
//  * мята / бирюза (info, accent-гамма знака), персик (warning), коралл (danger), лаванда (accent) — из знака «V»
//    и фоновой «чертёжной» композиции; фон окна — светлая «бумага» фонового изображения.
// Контраст проверяет тест tests/client/test_theme_contrast.py (WCAG 2.1 AA): основной и вторичный текст ≥ 4,5:1
// на всех поверхностях, бледные подписи ≥ 3:1, цвет каждого тона на своей подложке ≥ 4,5:1.
QtObject {
    property bool dark: false
    property string family: "Inter"

    readonly property color bg:          dark ? "#12161C" : "#F4F6F8"
    readonly property color sidebar:     dark ? "#161B22" : "#FAFBFC"
    readonly property color surface:     dark ? "#1B212A" : "#FFFFFF"
    readonly property color surfaceAlt:  dark ? "#212833" : "#EFF2F5"
    readonly property color hover:       dark ? "#27303C" : "#E8EDF2"
    readonly property color border:      dark ? "#2D3643" : "#DDE3EA"
    readonly property color borderStrong:dark ? "#3B4655" : "#C5CED8"
    readonly property color text:        dark ? "#E6EBF0" : "#2E3846"
    readonly property color textMuted:   dark ? "#9DA9B6" : "#566273"
    readonly property color textFaint:   dark ? "#7A8694" : "#7F8A99"

    readonly property color primary:     dark ? "#9DBCDD" : "#3E6188"
    readonly property color primaryHover:dark ? "#B5CDE7" : "#33547A"
    readonly property color primarySoft: dark ? "#22324A" : "#E3EBF4"
    readonly property color textOnPrimary:   dark ? "#0E1621" : "#FFFFFF"
    readonly property color accent:      dark ? "#B6A6E3" : "#6E5CA8"

    readonly property color success:     dark ? "#7DD0A0" : "#297649"
    readonly property color successSoft: dark ? "#1B3A2A" : "#E0F2E7"
    readonly property color warning:     dark ? "#EBC08E" : "#8A561A"
    readonly property color warningSoft: dark ? "#3A2E20" : "#FBEEDD"
    readonly property color danger:      dark ? "#F0A096" : "#B03F37"
    readonly property color dangerSoft:  dark ? "#44282A" : "#F9E4E1"
    readonly property color info:        dark ? "#8FD3CC" : "#28717C"
    readonly property color infoSoft:    dark ? "#193638" : "#DFF1F1"
    readonly property color neutralSoft: dark ? "#242C37" : "#ECEFF3"

    readonly property color focus:       primary
    readonly property color scrim:       dark ? "#99000000" : "#5C2E3846"

    // Фирменные изображения (строит tools/brand_assets.py из исходников владельца). Пути — от этого файла.
    readonly property url logo:     Qt.resolvedUrl(dark ? "../assets/brand/logo-dark.png" : "../assets/brand/logo-light.png")
    readonly property url mark:     Qt.resolvedUrl(dark ? "../assets/brand/mark-dark.png" : "../assets/brand/mark-light.png")
    readonly property url backdrop: Qt.resolvedUrl(dark ? "../assets/brand/bg-dark.jpg" : "../assets/brand/bg-light.jpg")
    // Насколько заметен фон под рабочими экранами: «чертёж» не должен спорить с данными (на входе — сильнее).
    readonly property real backdropOpacity: dark ? 0.45 : 0.45

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
