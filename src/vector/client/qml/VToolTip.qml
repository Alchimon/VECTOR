import QtQuick
import QtQuick.Controls.Basic

// Всплывающая подсказка в фирменном стиле (D-37): появляется при наведении мыши через полсекунды, тёмная на светлой
// теме и светлая на тёмной, переносит длинный текст (не шире 380 px). Используется базовыми компонентами (VButton,
// NavItem, VChip, KeyValue, SectionTitle, VField, VSelect, VSegmented, ListRow, PageHeader, VTipArea): каждый
// показывает её по своему тексту, а текст подсказки берёт из backend.tip() (src/vector/client/hints.py).
ToolTip {
    id: tt
    delay: 500
    timeout: 20000
    padding: 8
    leftPadding: 10; rightPadding: 10
    FontMetrics { id: fm; font.family: Theme.family; font.pixelSize: Theme.fsS }
    // Ширина — по самой длинной строке, но не больше 380: короткая подсказка не растягивается, длинная переносится.
    readonly property real longest: {
        var w = 0, lines = (tt.text || "").split("\n")
        for (var i = 0; i < lines.length; i++) w = Math.max(w, fm.advanceWidth(lines[i]))
        return Math.ceil(w) + 2
    }
    contentWidth: Math.min(380, longest)
    contentItem: Text {
        text: tt.text
        wrapMode: Text.Wrap
        color: Theme.dark ? Theme.bg : Theme.surface
        font.family: Theme.family; font.pixelSize: Theme.fsS
        lineHeight: 1.15
    }
    background: Rectangle { color: Theme.text; radius: Theme.radiusS; opacity: 0.96 }
}
