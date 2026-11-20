import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Раздел «Уведомления» (D-35, ответ владельца на A-40: «руководителю уведомление отправлять — да»).
// Уведомления создаёт сервер в момент события и отдаёт в обновлении (pull) — свои за 90 дней:
// * заказ отправлен на согласование — согласующим закупок и руководителям отдела (и их замещающим);
// * заказ согласован / возвращён / отозван — закупке, руководителям и согласующим;
// * оформлено / отменено замещение — замещающему, носителю роли и руководителям отдела.
// «Открыть» — перейти к объекту (заказ — «Закупка», замещение — «Замещения») и отметить прочитанным.
// Отметить прочитанным можно только при связи (команда notification.read); офлайн список только читается.
Item {
    id: page
    objectName: "notificationsPage"
    property var shell: null
    readonly property var nv: backend.notificationsOverview

    // Открыть объект уведомления: отметить прочитанным (если есть связь) и перейти в раздел.
    function openItem(n) {
        if (n.unread && page.nv.online === true) backend.markNotificationsRead("notif-read", [n.id])
        if (n.page === "procurement") {
            backend.setOrdersFilter(backend.orderFilterFor(n.object_id || ""))
            page.shell.go("procurement", n.object_id || "")
        } else if (n.page !== "") {
            page.shell.go(n.page, n.object_id || "")
        }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 28
        spacing: 16
        PageHeader {
            title: "Уведомления"
            subtitle: (page.nv.unread || 0) > 0 ? "Непрочитанных: " + page.nv.unread : "Все уведомления прочитаны"
            VButton { text: "Обновить"; iconName: "refresh-cw"; onClicked: backend.syncNow() }
            VButton { objectName: "btnReadAll"; text: "Прочитать все"; iconName: "check-check"; kind: "primary"
                      enabled: page.nv.can_mark === true; onClicked: backend.markAllNotificationsRead("notif-read-all") }
        }
        Banner { visible: page.nv.online !== true; tone: "warning"
                 text: "Нет связи с сервером: показаны уведомления на момент последнего обновления; отметить прочитанными можно после подключения." }
        EmptyState { visible: (page.nv.items || []).length === 0; Layout.fillWidth: true; Layout.fillHeight: true
                     iconName: "bell"; title: "Уведомлений нет"; text: page.nv.empty_text || "" }

        Flickable {
            visible: (page.nv.items || []).length > 0
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            contentHeight: list.implicitHeight; boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            ColumnLayout {
                id: list
                width: parent.width; spacing: 10
                Repeater {
                    model: page.nv.items || []
                    delegate: VCard {
                        id: card
                        objectName: "notificationRow"
                        Layout.fillWidth: true
                        implicitHeight: nrow.implicitHeight + 28
                        // Непрочитанное — с полосой слева и жирным заголовком.
                        Rectangle { visible: modelData.unread; width: 4; radius: 2; color: Theme.primary
                                    anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.margins: 8 }
                        RowLayout {
                            id: nrow
                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 20; anchors.rightMargin: 16; spacing: 14
                            Rectangle { width: 36; height: 36; radius: 18; color: Theme.tone(modelData.tone).bg; Layout.alignment: Qt.AlignTop
                                        Icon { anchors.centerIn: parent; name: modelData.icon; size: 18; color: Theme.tone(modelData.tone).fg } }
                            ColumnLayout { Layout.fillWidth: true; spacing: 3
                                RowLayout { Layout.fillWidth: true; spacing: 8
                                    Text { text: modelData.title; color: Theme.text; font.family: Theme.family; font.pixelSize: Theme.fs
                                           font.weight: modelData.unread ? Font.Bold : Font.Normal; elide: Text.ElideRight; Layout.fillWidth: true }
                                    VChip { text: modelData.kind_label; tone: modelData.tone } }
                                Text { visible: !!modelData.body; text: modelData.body || ""; color: Theme.textMuted; font.family: Theme.family
                                       font.pixelSize: Theme.fsS; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                Text { text: modelData.when; color: Theme.textFaint; font.family: Theme.family; font.pixelSize: Theme.fsXs } }
                            VButton { objectName: "btnOpenNotification"; visible: modelData.page !== ""; text: "Открыть"; compact: true; kind: "soft"
                                      iconName: "chevron-right"; onClicked: page.openItem(modelData) }
                            VButton { objectName: "btnReadNotification"; visible: modelData.unread && page.nv.online === true; compact: true; kind: "ghost"
                                      iconName: "check"; text: "Прочитано"; onClicked: backend.markNotificationsRead("notif-read", [modelData.id]) }
                        }
                    }
                }
                Item { implicitHeight: 8 }
            }
        }
    }
}
