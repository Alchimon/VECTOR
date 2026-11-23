"""UI-проверки вкладки «РКД» комплекта (D-64): ревизии, замечания, трансмитталы, профили шифров.

Что проверяем «глазами пользователя»:
* вкладка «РКД» показывает ревизии демо-данных со статусом и штампом выдачи, замечания с ответами;
* сегменты переключают ревизии/трансмитталы/профили; у трансмиттала виден пакет и кнопка скачивания;
* диалог «Новая ревизия» создаёт ревизию командой docrev.create (через сервер), снимок обновляется pull'ом;
* скачивание пакета трансмиттала сохраняет ZIP байт-в-байт (контрольная сумма сверена SyncClient'ом);
* без связи кнопки действий недоступны (все команды РКД — онлайн).
"""
import hashlib
import zipfile

import pytest

pytestmark = pytest.mark.ui


def _open_rkd(ui, seg="revisions"):
    """Войти РП и открыть комплект АТХ на вкладке «РКД», сегмент seg."""
    ui.login("a.smirnova")
    ui.go("projects", ui.ids["project"])
    kw = ui.kit_work(ui.ids["kits"]["АТХ"], "rkd")
    panel = _panel(ui)
    panel.setProperty("seg", seg)
    ui.wait_idle()
    return kw, panel


def _panel(ui):
    """Видимая панель KitRkd (узнаём по её свойствам seg/v)."""
    return next(i for i in ui.items("docOptions") if i.isVisible())


def _rkd(ui):
    """Снимок вкладки РКД из backend для открытого комплекта АТХ."""
    return ui.backend.kitRkd(ui.ids["kits"]["АТХ"], ui.backend.property("dataRevision"))


def test_rkd_shows_revisions_with_status_issue_and_comments(ui):
    _open_rkd(ui)
    v = _rkd(ui)
    revs = v["revisions"]
    assert len(revs) == 1
    r = revs[0]
    assert r["doc_code"] == "АТХ-001" and r["label"] == "01"
    assert r["status"] == "sent" and r["status_label"] == "Отправлена заказчику"
    assert r["issue_code"] == "IFC" and r["issue_label"] != ""
    # В демо-данных замечаний нет — карточка честно показывает ноль.
    assert r["comments_total"] == 0 and r["comments_open"] == 0
    # РП видит действие «Согласована заказчиком» (с основанием) на отправленной ревизии.
    labels = [a["label"] for a in r["actions"]]
    assert "Согласована заказчиком" in labels and "Замечание заказчика" in labels


def test_rkd_segments_transmittals_and_profiles(ui):
    _, panel = _open_rkd(ui, "transmittals")
    v = _rkd(ui)
    tr = v["transmittals"]
    assert len(tr) == 1 and tr[0]["seq"] == 1
    assert tr[0]["recipient"].startswith("ООО «ТехМаш»")
    assert tr[0]["status_label"] == "Отправлен"
    assert tr[0]["has_package"] and tr[0]["package_name"].endswith(".zip")
    assert tr[0]["items"][0]["label"].startswith("АТХ-001")
    assert tr[0]["can_download"] and tr[0]["can_register_receipt"]

    panel.setProperty("seg", "profiles")
    ui.wait_idle()
    profs = _rkd(ui)["profiles"]
    assert len(profs) == 1
    p = profs[0]
    assert p["name"].startswith("Схема заказчика") and p["scope_label"] == "Проект"
    assert "IFC" in p["statuses_label"] and p["revisions"] == 1 and p["active"]
    assert _rkd(ui)["counts"] == {"revisions": 1, "open_comments": 0, "profiles": 1, "transmittals": 1}


def test_rkd_create_revision_through_form(ui):
    from PySide6.QtCore import QMetaObject, QObject
    _open_rkd(ui)
    # Прямой вызов диалога — как кнопка «Новая ревизия»: заполняем поля и подтверждаем.
    dlg = next(o for o in ui.win.findChildren(QObject)
               if o.property("confirmText") == "Создать" and o.property("title") == "Новая ревизия документа")
    QMetaObject.invokeMethod(dlg, "ask")
    ui.wait_idle()
    assert dlg.property("visible")
    v = _rkd(ui)
    assert len(v["doc_options"]) == 3                       # все три документа комплекта — варианты
    body = dlg.findChildren(QObject)
    # VSelect документа, VField шифра, VSelect версии — по порядку объявления в диалоге.
    doc_sel = next(o for o in body if o.property("label") == "Документ")
    label_f = next(o for o in body if o.property("label") == "Шифр ревизии")
    ver_sel = next(o for o in body if o.property("label") == "Файл ревизии")
    did = ui.ids["docs"]["АТХ-001"]
    idx = next(i for i, d in enumerate(v["doc_options"]) if d["id"] == did)
    doc_sel.setProperty("currentIndex", idx)
    ui.wait_idle()
    label_f.setProperty("text", "02")
    ver_sel.setProperty("currentIndex", 0)                  # последняя версия файла (v3)
    ui.wait_idle()
    assert dlg.property("canConfirm")
    QMetaObject.invokeMethod(dlg, "confirmed")
    ui.wait_idle()
    assert dlg.property("error") == "" and not dlg.property("visible")
    revs = _rkd(ui)["revisions"]
    assert len(revs) == 2
    r2 = next(r for r in revs if r["label"] == "02")
    assert r2["status"] == "in_work" and r2["seq"] == 2
    # Действие автора/участника на новой ревизии — подача на проверку РП.
    assert any(a["label"] == "На проверку РП" for a in r2["actions"])


def test_rkd_package_download_byte_exact(ui, tmp_path):
    _open_rkd(ui, "transmittals")
    tr = _rkd(ui)["transmittals"][0]
    dest = tmp_path / tr["package_name"]
    ui.backend.downloadTransmittalPackage("t", tr["id"], str(dest))
    ui.wait_idle()
    assert dest.exists()
    pkg = next(p for p in ui.backend._stock.get("transmittal_packages") or [] if p["transmittal_id"] == tr["id"])
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == pkg["package_sha256"]
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
    assert "manifest.json" in names and any(n.startswith("01/") for n in names)


def test_rkd_offline_disables_actions(ui):
    _, panel = _open_rkd(ui)
    ui.transport.online = False
    ui.backend.syncNow()                                    # заметить потерю связи
    ui.wait_idle()
    v = _rkd(ui)
    assert all(a == [] for a in [r["actions"] for r in v["revisions"]])
    tr = v["transmittals"][0]
    assert not tr["can_download"] and not tr["can_register_receipt"]
    # Кнопка «Новая ревизия» недоступна без связи.
    from PySide6.QtQuick import QQuickItem
    btn = next(i for i in panel.findChildren(QQuickItem)
               if i.property("text") == "Новая ревизия" and i.isVisible())
    assert not btn.property("enabled")
