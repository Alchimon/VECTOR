import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Каркас раздела: заголовок + поиск слева, список, справа — карточка выбранного объекта.
Item {
    id: md
    property string title: ""
    property string subtitle: ""
    property alias search: searchField.text
    property alias list: listView
    property alias headerActions: hdr.actions
    default property alias detail: detailHost.data
    property bool hasSelection: false
    property string emptyIcon: "inbox"
    property string emptyTitle: "Пока пусто"
    property string emptyText: ""
    property string emptyAction: ""
    property string noSelectionText: "Выберите запись слева"
    signal emptyActionTriggered()

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 28; anchors.bottomMargin: 20
        spacing: 20
        PageHeader { id: hdr; title: md.title; subtitle: md.subtitle }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            spacing: 20
            VCard {
                Layout.preferredWidth: 400; Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 12
                    spacing: 10
                    VSearch { id: searchField; Layout.fillWidth: true }
                    ListView {
                        id: listView
                        Layout.fillWidth: true; Layout.fillHeight: true
                        clip: true; spacing: 2
                        keyNavigationEnabled: true
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}
                    }
                    EmptyState {
                        visible: listView.count === 0
                        Layout.alignment: Qt.AlignHCenter; Layout.bottomMargin: 80
                        iconName: md.search !== "" ? "search" : md.emptyIcon
                        title: md.search !== "" ? "Ничего не найдено" : md.emptyTitle
                        text: md.search !== "" ? "Измените запрос поиска." : md.emptyText
                        actionText: md.search !== "" ? "" : md.emptyAction
                        onAction: md.emptyActionTriggered()
                    }
                }
            }
            VCard {
                Layout.fillWidth: true; Layout.fillHeight: true
                ScrollView {
                    id: sv
                    anchors.fill: parent; anchors.margins: 2
                    visible: md.hasSelection
                    clip: true
                    contentWidth: availableWidth
                    Item {
                        width: sv.availableWidth
                        implicitHeight: detailHost.implicitHeight + 56
                        ColumnLayout {
                            id: detailHost
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 28
                            spacing: 18
                        }
                    }
                }
                EmptyState { anchors.centerIn: parent; visible: !md.hasSelection; iconName: "file-text"; title: md.noSelectionText; text: ""; tone: "neutral" }
            }
        }
    }
}
