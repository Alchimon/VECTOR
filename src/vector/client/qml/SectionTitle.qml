import QtQuick
import QtQuick.Layouts

// Заголовок секции внутри карточки: иконка + текст (text, iconName). По дизайн-системе D-99 — 12px,
// semibold, ВЕРХНИЙ РЕГИСТР с трекингом (источник текста не меняется — регистр даёт font.capitalization).
// tip — подсказка при наведении (D-37): что в секции; по умолчанию — из словаря по тексту заголовка.
RowLayout {
    id: st
    property string text: ""
    property string tip: backend.tip(text)
    property string iconName: ""
    spacing: 8
    Layout.fillWidth: true
    Icon { visible: iconName !== ""; name: iconName; size: 16; color: Theme.textMuted }
    Text { text: parent.text; color: Theme.textMuted; font.family: Theme.family; font.pixelSize: Theme.fsS
           font.weight: Font.DemiBold; font.capitalization: Font.AllUppercase; font.letterSpacing: 1.1
           Layout.fillWidth: true
           VTipArea { text: st.tip } }
}
