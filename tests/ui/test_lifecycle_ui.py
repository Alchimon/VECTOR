"""UI-проверки этапа 6 «Изделия и паспорта» (С5 шаги 7–9, D-38) — настоящий QML, сервер in-process, демо-данные.

Демо: наряд № 2 по ТП АТХ на 1 комплект закрыт; по нему зарегистрирован экземпляр «АТХ № 0001 (серия 2026)»,
передан заказчику; паспорт-черновик составил ведущий инженер m.lebedev (участник проекта): одно испытание, схема
АТХ-001 версии 3, раздел о гарантиях не заполнен. Наряд № 1 — в работе (регистрировать по нему нельзя).
"""
import pytest
from PySide6.QtCore import QMetaObject

from tests.ui.test_purchasing_ui import _call, _popup, _v, qml_warnings  # noqa: F401
from vector.core import lifecycle as lc

pytestmark = pytest.mark.ui


def _open(ui):
    """Раздел «Изделия и паспорта» с открытой карточкой единственного демо-экземпляра; вернуть (страницу, экземпляр)."""
    iid = _v(ui.backend, "lifecycleOverview")["items"][0]["id"]
    ui.go("products", iid)
    page = ui.find("productsPage")
    assert page.property("selectedId") == iid
    return page, _v(ui.backend, "lifecycleOverview")["items"][0]


def test_rp_sees_instance_card_links_passport_and_history(ui, qml_warnings, tmp_path):  # noqa: F811
    """РП: карточка с точными связями и составом, паспорт, история; сервисное событие и черновой экспорт."""
    ui.login("a.smirnova")
    assert ui.nav()["Изделия и паспорта"] is True
    lv = _v(ui.backend, "lifecycleOverview")
    assert lv["counts"] == {"total": 1, "handed_over": 1, "no_passport": 0}
    assert lv["may_register"] is False                       # наряд № 2 исчерпан, наряд № 1 не закрыт
    page, i = _open(ui)
    assert i["label"] == "АТХ № 0001 (серия 2026)" and i["status_label"] == "Передан заказчику" and i["composition_ok"]
    links = {x["k"]: x["v"] for x in i["links"]}
    assert links["Наряд"].startswith("№ 2 на 1 шт.") and len(links["Снимок наряда, SHA-256"]) == 64
    shu = next(ln for ln in i["lines"] if ln["code"] == "ШУ-1")
    assert shu["qty_label"] == "3 шт"
    assert [e["kind"] for e in i["events"]] == ["handover"] and i["can_service"] and not i["can_handover"]
    p = i["passport"]
    assert p["template_ok"] and len(p["tests"]) == 1 and p["documents"][0]["version_no"] == 3
    assert p["problems"] == ["Не заполнен раздел «Ресурсы, сроки службы и хранения, гарантии изготовителя»."]
    assert p["can_export"] and p["can_edit"]
    # Сервисное событие: форма требует описание; запись попадает в историю (только при связи).
    sf = _popup(ui, "serviceForm")
    _call(sf, "openFor")
    ui.wait_idle()
    assert sf.property("canConfirm") is False
    _popup(ui, "servDesc").setProperty("text", "Пусконаладка у заказчика завершена")
    ui.wait_idle()
    QMetaObject.invokeMethod(sf, "confirmed")
    ui.wait_idle()
    ev = _v(ui.backend, "lifecycleOverview")["items"][0]["events"]
    assert [e["kind"] for e in ev] == ["handover", "service"] and ev[-1]["summary"].startswith("Ввод в эксплуатацию")
    # Черновой экспорт: файл HTML с пометкой «Черновик, не сертификат», запись в журнале экспортов.
    dest = tmp_path / "паспорт.html"
    ui.backend.exportPassport("t-exp", p["id"], p["version"], str(dest))
    ui.wait_idle()
    assert lc.DRAFT_LABEL in dest.read_text(encoding="utf-8")
    assert len(_v(ui.backend, "lifecycleOverview")["items"][0]["passport"]["exports"]) == 1
    assert qml_warnings == []


def test_lead_engineer_edits_passport_offline_but_cannot_export(ui, qml_warnings):  # noqa: F811
    """Ведущий инженер — участник проекта: правит черновик паспорта (и без связи — через очередь), экспорт — нет."""
    ui.login("m.lebedev")
    assert ui.nav()["Изделия и паспорта"] is True
    page, i = _open(ui)
    p = i["passport"]
    assert p["can_edit"] and not p["may_export"] and not i["manage"]
    ui.transport.online = False
    ed = _popup(ui, "passportEditor")
    _call(ed, "openFor", p)
    ui.wait_idle()
    _call(ed, "setText", "resources", "Гарантийный срок — 24 месяца со дня ввода в эксплуатацию.")
    ui.wait_idle()
    assert _v(ed, "check")["ok"] is True
    QMetaObject.invokeMethod(ed, "save")
    ui.wait_idle()
    p2 = _v(ui.backend, "lifecycleOverview")["items"][0]["passport"]
    assert p2["pending"] and p2["problems"] == [] and ui.backend.property("pendingCount") == 1
    ui.transport.online = True
    ui.backend.syncNow()
    ui.wait_idle()
    p3 = _v(ui.backend, "lifecycleOverview")["items"][0]["passport"]
    assert not p3["pending"] and p3["version"] == p["version"] + 1
    assert qml_warnings == []


def test_section_hidden_for_warehouse(ui, qml_warnings):  # noqa: F811
    """Кладовщик к экземплярам и паспортам доступа не имеет — раздела в меню нет."""
    ui.login("i.belova")
    assert ui.nav().get("Изделия и паспорта") is False
    assert _v(ui.backend, "lifecycleOverview")["items"] == []
    assert qml_warnings == []
