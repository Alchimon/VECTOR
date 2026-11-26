pragma Singleton
import QtQuick

// Токены дизайна ВЕКТОР ПЛАТФОРМА: светлая и тёмная темы. Компоненты используют только токены (жёстких цветов
// в экранах нет).
//
// Тёмная тема — дизайн-система «Neo-Utilitarian» экосистемы ВЕКТОР (D-99, assets/vektor/manifest.json):
//  * тёмный холст, плоские поверхности, акценты модулей (ХАБ — сланец, МАРС — терракота, Юпитер — охра,
//    Меркурий — малахит, Венера — стальной синий); семантика наследует акценты модулей.
// Светлая тема — палитра D-28 из фирменного стиля владельца (docs/brand/source/): сланцево-синий из надписи
// и сетки логотипа; мята/бирюза, персик, коралл, лаванда — из знака «V» и «чертёжной» композиции.
// Контраст проверяет тест tests/client/test_theme_contrast.py (WCAG 2.1 AA): основной и вторичный текст ≥ 4,5:1
// на всех поверхностях, бледные подписи ≥ 3:1, цвет каждого тона на своей подложке ≥ 4,5:1.
QtObject {
    property bool dark: false
    property string family: "Inter"

    readonly property color bg:          dark ? "#121417" : "#F4F6F8"
    readonly property color sidebar:     dark ? "#181B20" : "#FAFBFC"
    readonly property color surface:     dark ? "#181B20" : "#FFFFFF"
    readonly property color surfaceAlt:  dark ? "#1F242B" : "#EFF2F5"
    readonly property color hover:       dark ? "#21262D" : "#E8EDF2"
    readonly property color border:      dark ? "#2A303A" : "#DDE3EA"
    readonly property color borderStrong:dark ? "#39424E" : "#C5CED8"
    readonly property color text:        dark ? "#E6EDF3" : "#2E3846"
    readonly property color textMuted:   dark ? "#8B949E" : "#566273"
    readonly property color textFaint:   dark ? "#6E7681" : "#7F8A99"

    readonly property color primary:     dark ? "#E2E8F0" : "#3E6188"
    readonly property color primaryHover:dark ? "#EDF2F8" : "#33547A"
    readonly property color primarySoft: dark ? "#262D36" : "#E3EBF4"
    readonly property color textOnPrimary:   dark ? "#121417" : "#FFFFFF"
    readonly property color accent:      dark ? "#9AA7B8" : "#6E5CA8"

    // Семантика наследует акценты модулей экосистемы (темн.: светлый тон того же цвета, чтобы держать AA).
    readonly property color success:     dark ? "#2E9E76" : "#297649"
    readonly property color successSoft: dark ? "#1A2728" : "#E0F2E7"
    readonly property color warning:     dark ? "#D99B26" : "#8A561A"
    readonly property color warningSoft: dark ? "#2F2A21" : "#FBEEDD"
    readonly property color danger:      dark ? "#E56B55" : "#B03F37"
    readonly property color dangerSoft:  dark ? "#2C2325" : "#F9E4E1"
    readonly property color info:        dark ? "#6B93E0" : "#28717C"
    readonly property color infoSoft:    dark ? "#202733" : "#DFF1F1"
    readonly property color neutralSoft: dark ? "#232830" : "#ECEFF3"

    // Акценты модулей экосистемы (темн.) — переключатель модулей, цветовая индикация разделов, карточки.
    readonly property color accHub:      dark ? "#E2E8F0" : "#3E6188"
    readonly property color accMars:     dark ? "#E05A47" : "#B03F37"
    readonly property color accJupiter:  dark ? "#D99B26" : "#8A561A"
    readonly property color accMercury:  dark ? "#2E9E76" : "#297649"
    readonly property color accVenus:    dark ? "#4A77D4" : "#3E6188"

    readonly property color focus:       primary
    readonly property color scrim:       dark ? "#99000000" : "#5C2E3846"

    // Фирменные изображения. Пути — от этого файла.
    // Логотип и фон — графический пак экосистемы (D-99, assets/vektor/); светлые варианты — прежние (D-28).
    readonly property url logo:     Qt.resolvedUrl(dark ? "../assets/vektor/app_icons/logo/vektor_platform_horizontal.png" : "../assets/brand/logo-light.png")
    readonly property url mark:     Qt.resolvedUrl(dark ? "../assets/vektor/app_icons/logo/vektor_emblem_only.png" : "../assets/brand/mark-light.png")
    readonly property url backdrop: Qt.resolvedUrl(dark ? "../assets/vektor/backgrounds/space.jpg" : "../assets/brand/bg-light.jpg")
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
