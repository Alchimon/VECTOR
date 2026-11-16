"""UI-проверки задач и документов комплекта (issue #1): настоящий QML, сервер in-process.

Что проверяем «глазами пользователя»:
* выбор комплекта на странице проекта показывает его задачи и документы;
* кнопка перехода задачи доходит до сервера (и по-прежнему проверяется там);
* новая версия документа загружается через форму и появляется в списке версий;
* без связи кнопка «Новая версия» недоступна — файл не попадает в очередь;
* скачивание сохраняет файл байт-в-байт, а повреждённый в пути файл не сохраняется.
"""
import hashlib

import pytest

pytestmark = pytest.mark.ui


def _open_kit(ui, tab):
    ui.login("a.smirnova")
    ui.go("projects", ui.ids["project"])
    return ui.kit_work(ui.ids["kits"]["АТХ"], tab)


def _buttons(ui, text):
    return [i for i in ui.items("kind") if i.property("text") == text and i.isVisible()]


def test_kit_shows_tasks_and_documents_with_counts(ui):
    kw = _open_kit(ui, "tasks")
    titles = [t["title"] for t in kw.property("kitTasks").toVariant()]
    # Сначала «в работе», затем открытые, выполненные, отменённые.
    assert titles[0] == "Разработать функциональную схему автоматизации"
    assert titles[-1] == "Отдельная схема питания шкафов"
    kits = {k["code"]: k for k in ui.backend.kits}
    assert (kits["АТХ"]["task_count"], kits["АТХ"]["open_task_count"], kits["АТХ"]["doc_count"]) == (5, 3, 3)
    docs = {d["code"]: d for d in ui.backend.documents}
    assert [v["version_no"] for v in docs["АТХ-001"]["versions"]] == [3, 2, 1]
    assert docs["АТХ-003"]["current_label"] == "файла ещё нет"


def test_take_task_into_work_via_button(ui):
    _open_kit(ui, "tasks")
    btn = _buttons(ui, "Взять в работу")[0]
    ui.scroll_detail_to(btn)     # кнопка ниже сгиба карточки — сначала прокручиваем, как сделал бы пользователь
    ui.click(btn)
    # FormDialog — это Popup (не QQuickItem), поэтому ищем по дереву QObject.
    from PySide6.QtCore import QMetaObject, QObject
    dlg = next(o for o in ui.win.findChildren(QObject)
               if o.property("confirmText") == "Взять в работу" and o.property("visible"))
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    statuses = sorted(t["status"] for t in ui.backend.tasks if t["kit_id"] == ui.ids["kits"]["АТХ"])
    assert statuses.count("in_progress") == 2


def test_upload_new_version_through_form(ui, tmp_path):
    _open_kit(ui, "documents")
    f = tmp_path / "АТХ-003_ПЗ.docx"
    f.write_bytes(b"PK\x03\x04" + "Пояснительная записка".encode() * 300)
    form = ui.find("versionForm")
    did = ui.ids["docs"]["АТХ-003"]
    doc = next(d for d in ui.backend.documents if d["id"] == did)
    from PySide6.QtCore import QMetaObject, Q_ARG
    QMetaObject.invokeMethod(form, "ask", Q_ARG("QVariant", doc))
    ui.wait_idle()
    form.setProperty("path", str(f))
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert form.property("error") == "" and not form.property("visible")
    doc = next(d for d in ui.backend.documents if d["id"] == did)
    assert doc["current_version_no"] == 1
    assert doc["versions"][0]["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    assert doc["versions"][0]["file_name"] == "АТХ-003_ПЗ.docx"


def test_offline_disables_new_version_button(ui):
    _open_kit(ui, "documents")
    assert all(b.property("enabled") for b in _buttons(ui, "Новая версия"))
    ui.transport.online = False
    ui.backend.syncNow()
    ui.wait_idle()
    assert ui.backend.online is False
    assert _buttons(ui, "Новая версия") and not any(b.property("enabled") for b in _buttons(ui, "Новая версия"))
    assert ui.backend.pendingCount == 0


def test_download_saves_verified_file_and_rejects_corrupted(ui, tmp_path):
    _open_kit(ui, "documents")
    did = ui.ids["docs"]["АТХ-002"]
    assert ui.backend.suggestedFileName(did, 1) == "АТХ-002_перечень_сигналов.xlsx"
    dest = tmp_path / "перечень.xlsx"
    ui.backend.downloadDocumentVersion("t", did, 1, dest.as_uri())
    ui.wait_idle()
    v = next(d for d in ui.backend.documents if d["id"] == did)["versions"][0]
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == v["sha256"]
    ui.transport.corrupt_next_download = True
    bad = tmp_path / "bad.xlsx"
    ui.backend.downloadDocumentVersion("t", did, 1, bad.as_uri())
    ui.wait_idle()
    assert not bad.exists()
