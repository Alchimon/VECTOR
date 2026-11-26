pragma Singleton
import QtQuick
import "../qml"

// Токены интерфейса 2.0 (D-98, docs/blueprint/56-ui-ergonomics.md): «воздух», крупная типографика,
// щедрые отступы, мягкие радиусы. Цвета — те же, что у классической темы (Theme): единая фирменная
// палитра и контраст WCAG AA уже проверены тестом test_theme_contrast.py; здесь меняются только метрики.
//
// compact=false («Уютно») — по умолчанию: базовый шрифт 15, карточки с полями 20–24, строки ≥ 52.
// compact=true («Компактно») — для старых мониторов 1366×768: шрифт 13, поля 10–14, строки ≥ 38.
// Переключается в статус-баре и в настройках (backend.uiDensity), применяется мгновенно.
QtObject {
    property bool dark: false
    property bool compact: false
    readonly property string family: Theme.family

    // ---- цвета: делегируем классической теме (единая палитра, один источник правды)
    readonly property color bg:           Theme.bg
    readonly property color sidebar:      Theme.sidebar
    readonly property color surface:      Theme.surface
    readonly property color surfaceAlt:   Theme.surfaceAlt
    readonly property color hover:        Theme.hover
    readonly property color border:       Theme.border
    readonly property color borderStrong: Theme.borderStrong
    readonly property color text:         Theme.text
    readonly property color textMuted:    Theme.textMuted
    readonly property color textFaint:    Theme.textFaint
    readonly property color primary:      Theme.primary
    readonly property color primaryHover: Theme.primaryHover
    readonly property color primarySoft:  Theme.primarySoft
    readonly property color textOnPrimary: Theme.textOnPrimary
    readonly property color accent:       Theme.accent
    readonly property color success:      Theme.success
    readonly property color successSoft:  Theme.successSoft
    readonly property color warning:      Theme.warning
    readonly property color warningSoft:  Theme.warningSoft
    readonly property color danger:       Theme.danger
    readonly property color dangerSoft:   Theme.dangerSoft
    readonly property color info:         Theme.info
    readonly property color infoSoft:     Theme.infoSoft
    readonly property color neutralSoft:  Theme.neutralSoft
    readonly property color focus:        Theme.focus
    readonly property color scrim:        Theme.scrim
    readonly property url   logo:         Theme.logo
    readonly property url   mark:         Theme.mark

    function tone(name) { return Theme.tone(name) }

    // ---- метрики: шаг сетки 8px (все размеры кратны 4)
    readonly property int unit: 8
    readonly property int gap:   compact ? 8  : 12   // между элементами внутри карточек
    readonly property int pad:   compact ? 12 : 20   // внутренние поля карточек
    readonly property int padL:  compact ? 18 : 28   // большие поля (страницы, панели)
    readonly property int pageMargin: compact ? 14 : 24  // поля рабочей области

    // ---- типографика (база выросла с 13 до 15 в «Уютно» — главная жалоба «всё мелкое»)
    readonly property int fsXs:  compact ? 11 : 12
    readonly property int fsS:   compact ? 12 : 13
    readonly property int fs:    compact ? 13 : 15   // основной текст
    readonly property int fsM:   compact ? 15 : 17   // заголовки карточек
    readonly property int fsL:   compact ? 18 : 21   // заголовки панелей
    readonly property int fsXl:  compact ? 22 : 27   // заголовки страниц
    readonly property int fsXxl: compact ? 26 : 32

    // ---- высоты и размеры
    readonly property int rowH:      compact ? 38 : 52   // строка списка
    readonly property int cardMinH:  compact ? 64 : 88   // минимальная высота карточки списка
    readonly property int topBarH:   compact ? 46 : 56   // верхняя панель (крошки, палитра, пользователь)
    readonly property int ribbonH:   compact ? 82 : 106  // лента команд
    readonly property int ribbonTabsH: compact ? 34 : 40 // высота полосы разделов ленты
    readonly property int railW:     compact ? 56 : 64   // узкая иконка-рейл слева
    readonly property int inspectorW: compact ? 320 : 400 // инспектор/предпросмотр справа
    readonly property int statusBarH: compact ? 24 : 30  // статус-бар
    readonly property int tabsH:     compact ? 34 : 40   // вкладки открытых объектов

    // ---- радиусы (мягче, чем в классике — Fluent)
    readonly property int radiusS: 8
    readonly property int radius:  12
    readonly property int radiusL: 16

    // ---- «воздушная» карточка: тонкая рамка + едва заметная тень (без layer-эффектов — offscreen-стенд)
    readonly property color cardBorder: Qt.alpha(border, 0.9)
    readonly property color cardShadow: dark ? "#40000000" : "#142E3846"
}
