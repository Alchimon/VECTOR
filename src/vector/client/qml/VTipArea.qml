import QtQuick

// Невидимая зона подсказки для произвольного элемента (D-37): положить внутрь элемента и задать text — при наведении
// мыши появится подсказка VToolTip. Для элементов, у которых нет своей подсказки (картинки, тексты, иконки):
//     Image { … VTipArea { text: "Знак «ВЕКТОР»" } }
// Пустой text — подсказки нет. Нажатия не перехватывает (HoverHandler только следит за мышью).
Item {
    id: area
    property string text: ""
    anchors.fill: parent
    HoverHandler { id: hh }
    VToolTip { visible: hh.hovered && area.text !== ""; text: area.text }
}
