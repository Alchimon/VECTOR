import QtQuick

// Иконка Lucide, перекрашенная под токен темы. Item-обёртка задаёт размер для Layout (implicit = size).
Item {
    id: root
    property string name: "circle-help"
    property color color: Theme.textMuted
    property int size: 18
    implicitWidth: size; implicitHeight: size
    width: size; height: size
    Image {
        anchors.fill: parent
        sourceSize: Qt.size(root.size * 2, root.size * 2)
        source: "image://icon/" + root.name + "?c=" + root.color
        smooth: true; mipmap: true
        fillMode: Image.PreserveAspectFit
    }
}
