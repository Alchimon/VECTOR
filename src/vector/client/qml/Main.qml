import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Window
import "../qml2"

// Главное окно программы. Показывает один из экранов по backend.screen:
// «login» — LoginPage, «change_password» — ChangePasswordPage, «main» — рабочая оболочка.
// Оболочка — по backend.uiMode (D-98): «classic» — привычная Shell.qml, «v2» — новая Shell2.qml
// (лента команд, вкладки объектов, предпросмотр, статус-бар); переключается без перезапуска.
// Тонкая полоска вверху — индикатор фоновой работы (backend.busy); Toasts — всплывающие уведомления.
ApplicationWindow {
    id: win
    width: 1360; height: 860
    minimumWidth: 1100; minimumHeight: 700
    visible: true
    title: "ВЕКТОР ПЛАТФОРМА"
    color: Theme.bg
    font.family: Theme.family
    // Стандартные элементы Qt (галочки CheckBox и т. п.) берут цвет подписи из палитры окна — задаём его из темы,
    // иначе в тёмной теме подпись галочки тёмная на тёмном фоне.
    palette.windowText: Theme.text
    palette.text: Theme.text
    palette.base: Theme.surface

    Binding { target: Theme; property: "dark"; value: backend.theme === "dark" }
    Binding { target: Theme; property: "family"; value: uiFontFamily }
    // Интерфейс 2.0: та же тема (dark) + плотность из настроек (backend.uiDensity, D-98).
    Binding { target: Theme2; property: "dark"; value: backend.theme === "dark" }
    Binding { target: Theme2; property: "compact"; value: backend.uiDensity === "compact" }

    Loader {
        id: screen
        anchors.fill: parent
        sourceComponent: backend.screen === "main" ? (backend.uiMode === "v2" ? shell2 : shell)
                         : backend.screen === "change_password" ? changePwd : loginPage
    }
    Component { id: loginPage; LoginPage {} }
    Component { id: changePwd; ChangePasswordPage {} }
    Component { id: shell; Shell {} }
    Component { id: shell2; Shell2 {} }

    Toasts {}

    Rectangle {  // индикатор фоновой работы
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        height: 2; color: "transparent"; visible: backend.busy
        Rectangle {
            id: bar; height: 2; width: parent.width * 0.3; color: Theme.primary; radius: 1
            SequentialAnimation on x { loops: Animation.Infinite; running: backend.busy
                NumberAnimation { from: -bar.width; to: win.width; duration: 1100; easing.type: Easing.InOutQuad } }
        }
    }
}
