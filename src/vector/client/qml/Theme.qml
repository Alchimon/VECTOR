pragma Singleton
import QtQuick

// Токены дизайна ВЕКТОР Платформа: светлая и тёмная темы. Компоненты используют только токены (жёстких цветов
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
    // Моноширинный шрифт (шифры проектов, артикулы, коды документов, числа-колонки) — D-99.
    property string mono: "JetBrains Mono"

    readonly property color bg:dark ? "#121417" : "#F6F8FA"
    readonly property color sidebar:dark ? "#181B20" : "#FFFFFF"
    readonly property color surface:dark ? "#181B20" : "#FFFFFF"
    readonly property color surfaceAlt:dark ? "#1F242B" : "#EFF1F4"
    readonly property color hover:dark ? "#21262D" : "#EAEEF2"
    readonly property color border:dark ? "#2A303A" : "#D0D7DE"
    readonly property color borderStrong:dark ? "#39424E" : "#B8C0C8"
    readonly property color text:dark ? "#E6EDF3" : "#1F2328"
    readonly property color textMuted:dark ? "#8B949E" : "#57606A"
    readonly property color textFaint:dark ? "#6E7681" : "#6E7781"

    readonly property color primary:dark ? "#E2E8F0" : "#31648C"
    readonly property color primaryHover:dark ? "#EDF2F8" : "#2A5678"
    readonly property color primarySoft:dark ? "#262D36" : "#DDE8F3"
    readonly property color textOnPrimary:   dark ? "#121417" : "#FFFFFF"
    readonly property color accent:dark ? "#9AA7B8" : "#6E5CA8"

    // Семантика наследует акценты модулей экосистемы (темн.: светлый тон того же цвета, чтобы держать AA).
    readonly property color success:dark ? "#2E9E76" : "#177233"
    readonly property color successSoft:dark ? "#1A2728" : "#DAF0E0"
    readonly property color warning:dark ? "#D99B26" : "#8A5700"
    readonly property color warningSoft:dark ? "#2F2A21" : "#FFF1D6"
    readonly property color danger:dark ? "#E56B55" : "#B3261E"
    readonly property color dangerSoft:dark ? "#2C2325" : "#FBE5E3"
    readonly property color info:dark ? "#6B93E0" : "#0A6E7C"
    readonly property color infoSoft:dark ? "#202733" : "#DCF0F3"
    readonly property color neutralSoft:dark ? "#232830" : "#EAEEF2"

    // Акценты модулей экосистемы (темн.) — переключатель модулей, цветовая индикация разделов, карточки.
    readonly property color accHub:dark ? "#E2E8F0" : "#31648C"
    readonly property color accMars:dark ? "#E05A47" : "#B3261E"
    readonly property color accJupiter:dark ? "#D99B26" : "#8A5700"
    readonly property color accMercury:dark ? "#2E9E76" : "#177233"
    readonly property color accVenus:dark ? "#4A77D4" : "#3A5FCD"

    readonly property color focus:       primary
    readonly property color scrim:       dark ? "#99000000" : "#5C2E3846"

    // Фирменные изображения — графический пак экосистемы (D-99, assets/vektor/) в ОБЕИХ темах:
    // серебристый логотип читается и на светлом фоне (тёмные кромки букв), эмблема — металл на прозрачном.
    // Прежние файлы D-28 (assets/brand/) не удаляются — их наличие проверяет тест-страж бренда.
    readonly property url logo:     Qt.resolvedUrl("../assets/vektor/app_icons/logo/vektor_platform_horizontal.png")
    readonly property url mark:     Qt.resolvedUrl("../assets/vektor/app_icons/logo/vektor_emblem_only.png")
    readonly property url backdrop: Qt.resolvedUrl("../assets/vektor/backgrounds/space.jpg")
    // Насколько заметен фон на бренд-панели входа (под вуалью brandBg).
    readonly property real backdropOpacity: dark ? 0.45 : 0.45

    // Бренд-панель (экран входа) всегда тёмная в обеих темах: фирменный фон «космос/чертёж» тёмный,
    // поэтому поверх него — светлые тексты и вуаль цвета холста (дизайн-система: оверлей ≈0,82).
    readonly property color brandBg:    dark ? "#121417" : "#121417"
    readonly property color brandText:  dark ? "#E6EDF3" : "#E6EDF3"
    readonly property color brandMuted: dark ? "#8B949E" : "#8B949E"

    // Радиусы Neo-Utilitarian (D-99): плоские поверхности, скругления 4–8 px, без тяжёлых теней и градиентов.
    readonly property int radiusS: 4
    readonly property int radius: 6
    readonly property int radiusL: 8
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
