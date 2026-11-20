import QtQuick

// Выполнение команды из формы: «работаю» → закрыть при успехе/постановке в очередь, иначе показать причину в форме.
QtObject {
    id: c
    property var dialog: null
    property string tag: "cmd-" + Math.random().toString(36).slice(2)
    signal done(var r)
    function run(command, payload, expectedVersion) {
        if (dialog) { dialog.working = true; dialog.error = "" }
        backend.execute(tag, command, payload, expectedVersion || 0)
    }
    property Connections _conn: Connections {
        target: backend
        function onCommandDone(t, r) {
            if (t !== c.tag) return
            if (c.dialog) {
                c.dialog.working = false
                if (r.status === "accepted" || r.status === "queued") c.dialog.close()
                else c.dialog.error = r.message_ru || "Действие не выполнено."
            }
            c.done(r)
        }
    }
}
