import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Window

// Главное окно программы. Показывает один из экранов по backend.screen:
// «login» — LoginPage, «change_password» — ChangePasswordPage, «main» — Shell (рабочая оболочка).
// Тонкая полоска вверху — индикатор фоновой работы (backend.busy); Toasts — всплывающие уведомления.
ApplicationWindow {
    id: win
    width: 1360; height: 860
    minimumWidth: 1100; minimumHeight: 700
    visible: true
    title: "ВЕКТОР"
    color: Theme.bg
    font.family: Theme.family

    Binding { target: Theme; property: "dark"; value: backend.theme === "dark" }
    Binding { target: Theme; property: "family"; value: uiFontFamily }

    Loader {
        id: screen
        anchors.fill: parent
        sourceComponent: backend.screen === "main" ? shell : backend.screen === "change_password" ? changePwd : loginPage
    }
    Component { id: loginPage; LoginPage {} }
    Component { id: changePwd; ChangePasswordPage {} }
    Component { id: shell; Shell {} }

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
