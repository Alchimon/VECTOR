import QtQuick
import QtQuick.Layouts

// Заголовок секции внутри карточки: иконка + текст заглавными (text, iconName).
// tip — подсказка при наведении (D-37): что в секции; по умолчанию — из словаря по тексту заголовка.
RowLayout {
    id: st
    property string text: ""
    property string tip: backend.tip(text)
    property string iconName: ""
    spacing: 8
    Layout.fillWidth: true
    Icon { visible: iconName !== ""; name: iconName; size: 16; color: Theme.textMuted }
    Text { text: parent.text; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fsM; font.weight: Font.DemiBold; Layout.fillWidth: true
           VTipArea { text: st.tip } }
}
