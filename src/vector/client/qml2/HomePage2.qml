// Главная 2.0 (D-108) — рабочий стол: приветствие, что требует внимания, ближайшие сроки,
// проекты под рукой. Честные данные из backend, никаких муляжей: пустой список говорит об этом.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../qml"

ScrollView {
    id: page
    objectName: "homePage2"
    required property var shell
    clip: true

    contentWidth: availableWidth
    contentItem: Flickable {
        contentWidth: page.availableWidth
        contentHeight: contentColumn.implicitHeight + 40
        ColumnLayout {
            id: contentColumn
            width: parent.width
            spacing: 18

            // ---------- приветствие
            ColumnLayout { Layout.topMargin: 26; Layout.leftMargin: 28; Layout.rightMargin: 28; spacing: 2
                Text { text: {
                        var h = new Date().getHours()
                        var w = h < 5 ? "Доброй ночи" : h < 12 ? "Доброе утро" : h < 18 ? "Добрый день" : "Добрый вечер"
                        return w + ", " + (backend.me.display_name || backend.me.login || "")
                    }
                    color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsXl; font.weight: Font.DemiBold }
                Text { text: new Date().toLocaleDateString(Qt.locale("ru_RU"), "dddd, d MMMM yyyy") + " · " + (backend.online ? "соединение с сервером активно" : "автономный режим — показаны кэшированные данные")
                       color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsS }
            }

            // ---------- полоса внимания: просроченные задачи (D-111)
            Rectangle { visible: page.overdueTasks.length > 0; Layout.leftMargin: 28; Layout.rightMargin: 28
                        Layout.fillWidth: true; radius: Theme2.radiusL
                        color: Qt.alpha(Theme2.warning, 0.10); border.color: Qt.alpha(Theme2.warning, 0.5)
                        implicitHeight: overdueCol.implicitHeight + 18
                ColumnLayout { id: overdueCol; anchors { left: parent.left; right: parent.right; top: parent.top; margins: 9; leftMargin: 14; rightMargin: 14 } spacing: 4
                    RowLayout { spacing: 8
                        Icon { name: "triangle-alert"; size: 15; color: Theme2.warning }
                        Text { text: "Просрочено задач: " + page.overdueTasks.length + " — сроки прошли, нужно решение"
                               color: Theme2.text; font.family: Theme2.family; font.pixelSize: Theme2.fsS; font.weight: Font.DemiBold } }
                    Text { text: page.overdueTasks.slice(0, 3).map(function(t) { return t.title + " (до " + t.due_date + ")" }).join(" · ")
                           color: Theme2.textMuted; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                           elide: Text.ElideRight; Layout.fillWidth: true }
                }
            }

            // ---------- три сводные карточки
            RowLayout { Layout.leftMargin: 28; Layout.rightMargin: 28; spacing: Theme2.gap
                Repeater {
                    model: [
                        { icon: "list-checks", title: "Мои задачи",
                          value: ("" + page.myOpenTasks.length),
                          note: page.myOpenTasks.length > 0 ? "открытых и в работе" : "ничего не назначено",
                          tone: Theme2.info },
                        { icon: "bell", title: "Уведомления",
                          value: ("" + (backend.notificationsOverview.unread || 0)),
                          note: (backend.notificationsOverview.unread || 0) > 0 ? "непрочитанных" : "всё прочитано",
                          tone: Theme2.warning },
                        { icon: "folder-kanban", title: "Проекты",
                          value: ("" + page.activeProjects.length),
                          note: "в работе",
                          tone: Theme2.primary }
                    ]
                    delegate: Rectangle {
                        Layout.fillWidth: true; Layout.preferredHeight: 96; radius: Theme2.radiusL
                        color: Theme2.surface; border.color: Theme2.cardBorder
                        RowLayout { anchors.fill: parent; anchors.margins: 16; spacing: 14
                            Rectangle { width: 44; height: 44; radius: 12; color: Qt.alpha(modelData.tone, 0.14)
                                Icon { anchors.centerIn: parent; name: modelData.icon; size: 20; color: modelData.tone } }
                            ColumnLayout { spacing: 2; Layout.fillWidth: true
                                Text { text: modelData.value; color: Theme2.text; font.family: Theme2.family
                                       font.pixelSize: 22; font.weight: Font.Bold }
                                Text { text: modelData.title + " · " + modelData.note; color: Theme2.textMuted
                                       font.family: Theme2.family; font.pixelSize: Theme2.fsXs; elide: Text.ElideRight; Layout.fillWidth: true } }
                        }
                    }
                }
            }

            // ---------- две колонки: задачи и проекты
            RowLayout { Layout.leftMargin: 28; Layout.rightMargin: 28; Layout.bottomMargin: 24
                        spacing: Theme2.gap; Layout.fillWidth: true

                // Мои ближайшие задачи
                Rectangle { objectName: "homeTasks"; Layout.fillWidth: true; Layout.preferredWidth: 3
                            radius: Theme2.radiusL; color: Theme2.surface; border.color: Theme2.cardBorder
                            implicitHeight: tasksCol.implicitHeight + 24
                    ColumnLayout { id: tasksCol; anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 } spacing: 8
                        RowLayout { spacing: 8
                            Icon { name: "list-checks"; size: 15; color: Theme2.info }
                            Text { text: "Мои задачи"; color: Theme2.text; font.family: Theme2.family
                                   font.pixelSize: Theme2.fsM; font.weight: Font.DemiBold } }
                        ColumnLayout { spacing: 6; Layout.fillWidth: true
                        Repeater {
                            model: page.myOpenTasks.slice(0, 6)
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true; implicitHeight: taskRow.implicitHeight + 14; radius: Theme2.radiusS
                                color: taskMa.containsMouse ? Theme2.hover : Theme2.surfaceAlt
                                ColumnLayout { id: taskRow; anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10; leftMargin: 12; rightMargin: 12 } spacing: 3
                                    Text { text: modelData.title; color: Theme2.text; font.family: Theme2.family
                                           font.pixelSize: Theme2.fsS; Layout.fillWidth: true; elide: Text.ElideRight }
                                    RowLayout { spacing: 10
                                        Text { visible: (modelData.due_date || "") !== ""; text: "до " + (modelData.due_date || "")
                                               color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                                        Text { text: modelData.status_label || ""
                                               color: modelData.status === "in_progress" ? Theme2.info : Theme2.textMuted
                                               font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                                        Text { visible: (modelData.document_code || "") !== ""; text: modelData.document_code || ""
                                               color: Theme2.textFaint; font.family: Theme2.mono; font.pixelSize: Theme2.fsXs }
                                    }
                                }
                                MouseArea { id: taskMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: page.shell.openObject("kit", modelData.kit_id) }
                            }
                        }
                        Text { visible: page.myOpenTasks.length === 0; text: "Назначенных открытых задач нет — всё сделано или ещё не поставлено."
                               color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsS; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        }
                    }
                }

                // Проекты под рукой
                Rectangle { Layout.fillWidth: true; Layout.preferredWidth: 2
                            radius: Theme2.radiusL; color: Theme2.surface; border.color: Theme2.cardBorder
                            implicitHeight: projsCol.implicitHeight + 24
                    ColumnLayout { id: projsCol; anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 } spacing: 8
                        RowLayout { spacing: 8
                            Icon { name: "folder-kanban"; size: 15; color: Theme2.primary }
                            Text { text: "Проекты в работе"; color: Theme2.text; font.family: Theme2.family
                                   font.pixelSize: Theme2.fsM; font.weight: Font.DemiBold } }
                        ColumnLayout { spacing: 6; Layout.fillWidth: true
                        Repeater {
                            model: page.activeProjects.slice(0, 5)
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true; implicitHeight: projRow.implicitHeight + 14; radius: Theme2.radiusS
                                color: projMa.containsMouse ? Theme2.hover : Theme2.surfaceAlt
                                RowLayout { id: projRow; anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10; leftMargin: 12; rightMargin: 12 } spacing: 10
                                    ColumnLayout { spacing: 2; Layout.fillWidth: true
                                        Text { text: modelData.code + " · " + modelData.title; color: Theme2.text
                                               font.family: Theme2.family; font.pixelSize: Theme2.fsS; Layout.fillWidth: true; elide: Text.ElideRight }
                                        Text { text: modelData.status_label || ""; color: Theme2.textFaint
                                               font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                                    }
                                    Icon { name: "chevron-right"; size: 14; color: Theme2.textFaint }
                                }
                                MouseArea { id: projMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: page.shell.openObject("project", modelData.id) }
                            }
                        }
                        Text { visible: page.activeProjects.length === 0; text: "Проектов в работе нет."
                               color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsS; Layout.fillWidth: true }
                        }
                    }
                }
            }

            // ---------- последние уведомления (D-115): полоса во всю ширину, щелчок ведёт в раздел события
            Rectangle { Layout.leftMargin: 28; Layout.rightMargin: 28; Layout.bottomMargin: 28; Layout.fillWidth: true
                        radius: Theme2.radiusL; color: Theme2.surface; border.color: Theme2.cardBorder
                        implicitHeight: notifCol.implicitHeight + 24
                ColumnLayout { id: notifCol; anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 } spacing: 8
                    RowLayout { spacing: 8
                        Icon { name: "bell"; size: 15; color: Theme2.warning }
                        Text { text: "Последние уведомления"; color: Theme2.text; font.family: Theme2.family
                               font.pixelSize: Theme2.fsM; font.weight: Font.DemiBold; Layout.fillWidth: true }
                        Text { text: "Все…"; color: Theme2.primary; font.family: Theme2.family; font.pixelSize: Theme2.fsXs
                               MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                   onClicked: page.shell.go("notifications") } }
                    }
                    Repeater {
                        model: page.notifItems.slice(0, 5)
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true; implicitHeight: nRow.implicitHeight + 12; radius: Theme2.radiusS
                            color: nMa.containsMouse ? Theme2.hover : (modelData.unread ? Qt.alpha(Theme2.warning, 0.07) : Theme2.surfaceAlt)
                            RowLayout { id: nRow; anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 8; leftMargin: 10; rightMargin: 10 } spacing: 10
                                Rectangle { visible: modelData.unread; width: 7; height: 7; radius: 4; color: Theme2.warning }
                                ColumnLayout { spacing: 1; Layout.fillWidth: true
                                    Text { text: modelData.title || ""; color: Theme2.text; font.family: Theme2.family
                                           font.pixelSize: Theme2.fsS; Layout.fillWidth: true; elide: Text.ElideRight
                                           font.weight: modelData.unread ? Font.DemiBold : Font.Normal }
                                    Text { text: (modelData.actor_name !== "" ? modelData.actor_name + " · " : "") + (modelData.when || "")
                                           color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsXs }
                                }
                                Icon { visible: (modelData.page || "") !== ""; name: "chevron-right"; size: 13; color: Theme2.textFaint }
                            }
                            MouseArea { id: nMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onClicked: if ((modelData.page || "") !== "") page.shell.go(modelData.page) }
                        }
                    }
                    Text { visible: page.notifItems.length === 0; text: "Уведомлений нет."
                           color: Theme2.textFaint; font.family: Theme2.family; font.pixelSize: Theme2.fsS; Layout.fillWidth: true }
                }
            }
        }
    }

    // ---------- данные
    readonly property var notifItems: backend.notificationsOverview.items || []
    readonly property var myOpenTasks: backend.tasks.filter(function(t) {
        return t.mine === true && (t.status === "open" || t.status === "in_progress")
    })
    readonly property var activeProjects: backend.projects.filter(function(p) {
        return p.status === "rkd_in_progress" || p.status === "closing"
    })
    // D-111: просроченные мои задачи (срок истёк до сегодня) — внимание на «Главной».
    readonly property var overdueTasks: {
        var today = new Date().toISOString().slice(0, 10)
        return myOpenTasks.filter(function(t) { return (t.due_date || "") !== "" && t.due_date < today })
    }
}
