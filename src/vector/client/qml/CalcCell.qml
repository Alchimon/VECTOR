import QtQuick
import QtQuick.Controls.Basic

// Ячейка редактора расчёта: компактное поле ввода; числовые — выравнивание вправо и моноширинные цифры.
TextField {
    id: c
    property bool num: false
    property string placeholder: ""
    placeholderText: placeholder; placeholderTextColor: Theme.textFaint
    implicitHeight: 32; leftPadding: 8; rightPadding: 8
    color: Theme.text; selectByMouse: true
    font.family: Theme.family; font.pixelSize: Theme.fs; font.features: num ? { "tnum": 1 } : ({})
    horizontalAlignment: num ? Text.AlignRight : Text.AlignLeft
    background: Rectangle { radius: Theme.radiusS; color: Theme.surface
        border.width: c.activeFocus ? 2 : 1; border.color: c.activeFocus ? Theme.primary : Theme.border }
}
