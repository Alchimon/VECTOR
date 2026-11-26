import QtQuick
import QtQuick.Controls.Basic

// Поле поиска в списке (иконка лупы, тема из Theme). Фильтрация выполняется на странице по тексту поля.
TextField {
    id: s
    implicitHeight: 36
    placeholderText: "Поиск"
    placeholderTextColor: Theme.textFaint
    color: Theme.text
    leftPadding: 36
    font.family: Theme.family; font.pixelSize: Theme.fs
    selectByMouse: true
    Accessible.name: "Поиск"
    background: Rectangle {
        radius: Theme.radius - 2; color: Theme.surface
        border.width: s.activeFocus ? 2 : 1; border.color: s.activeFocus ? Theme.primary : Theme.border
        Icon { name: "search"; size: 16; x: 12; anchors.verticalCenter: parent.verticalCenter }
    }
}
