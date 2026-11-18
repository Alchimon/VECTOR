"""UI-проверки этапа 6 «Качество» (С5 шаг 10, D-39) — настоящий QML, сервер in-process, демо-данные.

Демо: рекламация № 1 по «АТХ № 0001 (серия 2026)» — в состоянии «Действия»: корректирующее действие выполнил
d.kuznetsov, предупреждающее (исполнитель m.lebedev) ещё не выполнено. Запрос на изменение № 1 по рекламации подал
d.kuznetsov — ждёт оценки. m.lebedev сейчас замещает ГИП в проекте (D-35), p.volkov — ГИП, a.smirnova — РП.
"""
import pytest
from PySide6.QtCore import QMetaObject

from tests.ui.test_purchasing_ui import _call, _popup, _v, qml_warnings  # noqa: F401

pytestmark = pytest.mark.ui


def _qv(ui):
    """Текущее представление раздела «Качество»."""
    return _v(ui.backend, "qualityOverview")


def _confirm(ui, form):
    """Нажать «подтвердить» в форме и дождаться ответа сервера."""
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()


def test_action_owner_marks_done_and_sees_why_he_cannot_check(ui, qml_warnings):  # noqa: F811
    """Исполнитель действия CAPA отмечает его; после отправки на проверку он же проверять не может — и видит почему."""
    ui.login("m.lebedev")
    assert ui.nav()["Качество"] is True
    c = _qv(ui)["claims"][0]
    assert (c["label"], c["status_label"], c["instance_label"]) == ("Рекламация № 1", "Выполнение действий", "АТХ № 0001 (серия 2026)")
    assert [a["done"] for a in c["actions"]] == [True, False] and not c["can_submit"]
    ui.go("quality", c["id"])
    page = ui.find("qualityPage")
    assert page.property("selectedId") == c["id"] and page.property("tab") == "claims"
    mine = c["actions"][1]
    assert mine["can_done"] and mine["kind_label"] == "Предупреждающее"
    df = _popup(ui, "actionDoneForm")
    _call(df, "openFor", mine)
    ui.wait_idle()
    assert df.property("canConfirm") is False
    _popup(ui, "actionResult").setProperty("text", "Выпущена версия 4 схемы с нумерацией по маркировке шкафа")
    ui.wait_idle()
    _confirm(ui, df)
    c = _qv(ui)["claims"][0]
    assert [a["done"] for a in c["actions"]] == [True, True] and c["can_submit"]    # замещает ГИП — ведёт рекламацию
    ui.backend.qualityCommand("t-sub", "claim.submit_check", {"id": c["id"]}, c["version"])
    ui.wait_idle()
    c = _qv(ui)["claims"][0]
    assert c["status"] == "effectiveness_check" and c["may_check"] and not c["can_check"]
    assert "вы выполняли действия" in c["check_block"] and "вы отправили" in c["check_block"]
    assert qml_warnings == []


def test_gip_assesses_decides_and_drafts_change_order_but_cannot_approve_own(ui, qml_warnings):  # noqa: F811
    """ГИП: оценка влияния и решение по чужому запросу → извещение с точной версией; утвердить своё — нельзя."""
    ui.login("p.volkov")
    r = _qv(ui)["ecrs"][0]
    assert (r["label"], r["status_label"], r["claim_label"]) == ("Запрос № 1", "Подан", "Рекламация № 1")
    assert r["can_assess"] and not r["may_decide"]
    ui.go("quality", r["id"])
    page = ui.find("qualityPage")
    assert page.property("tab") == "ecrs"
    af = _popup(ui, "ecrAssessForm")
    _call(af, "openFor")
    ui.wait_idle()
    _popup(ui, "ecrImpactEdit").setProperty("text", "Схема АТХ-001; один изготовленный экземпляр; один день")
    ui.wait_idle()
    _confirm(ui, af)
    r = _qv(ui)["ecrs"][0]
    assert r["status_label"] == "Оценён" and r["may_decide"] and r["can_decide"] and r["decide_block"] == ""
    dfm = _popup(ui, "ecrDecideForm")
    _call(dfm, "openFor")
    ui.wait_idle()
    _popup(ui, "ecrDecisionReason").setProperty("text", "Ошибка в документации — исправить")
    ui.wait_idle()
    _confirm(ui, dfm)
    r = _qv(ui)["ecrs"][0]
    assert r["status_label"] == "Принят" and r["can_eco"]
    # Извещение: строка «схема АТХ-001, версия 3» (последняя загруженная) — сохраняется через очередь.
    ed = _popup(ui, "ecoEditor")
    _call(ed, "openFor", None, r)
    ui.wait_idle()
    assert _v(ed, "chk")["ok"] is False                           # нет описания и строк
    _popup(ui, "ecoDescription").setProperty("text", "Нумерация клемм по маркировке шкафа")
    sel = _popup(ui, "ecoDocSel")
    opts = _v(ed, "docOpts")
    sel.setProperty("currentIndex", next(i for i, d in enumerate(opts) if d["label"].startswith("АТХ-001")))
    ui.wait_idle()
    QMetaObject.invokeMethod(_popup(ui, "ecoAddDoc"), "clicked")
    ui.wait_idle()
    assert _v(ed, "chk")["ok"] is True
    _confirm(ui, ed)
    o = _qv(ui)["ecos"][0]
    assert o["label"] == "Извещение № 1" and o["status_label"] == "Черновик"
    assert o["items"][0]["label"].startswith("Документ АТХ-001") and o["items"][0]["label"].endswith("версия 3")
    # Составитель не утверждает («четыре глаза»): кнопка видна, но недоступна, рядом — почему.
    assert o["may_approve"] and not o["can_approve"] and "четыре глаза" in o["approve_block"]
    assert qml_warnings == []


def test_rp_registers_claim_offline(ui, qml_warnings):  # noqa: F811
    """РП регистрирует рекламацию без связи — она в очереди и видна как «не отправлена»; после связи — с номером."""
    ui.login("a.smirnova")
    v = _qv(ui)
    assert v["may_claim"] and len(v["claims"]) == 1
    ui.go("quality")
    ui.transport.online = False
    f = _popup(ui, "claimForm")
    _call(f, "openFor")
    ui.wait_idle()
    assert f.property("canConfirm") is False                      # суть не заполнена
    _popup(ui, "claimDescription").setProperty("text", "Шум вентилятора шкафа управления")
    ui.wait_idle()
    assert f.property("canConfirm") is True
    _confirm(ui, f)
    new = [c for c in _qv(ui)["claims"] if c["pending"]]
    assert [c["label"] for c in new] == ["Рекламация (не отправлена)"] and ui.backend.property("pendingCount") == 1
    ui.transport.online = True
    ui.backend.syncNow()
    ui.wait_idle()
    assert sorted(c["label"] for c in _qv(ui)["claims"]) == ["Рекламация № 1", "Рекламация № 2"]
    assert qml_warnings == []


def test_section_hidden_for_warehouse(ui, qml_warnings):  # noqa: F811
    """Кладовщик к рекламациям и изменениям доступа не имеет — раздела в меню нет."""
    ui.login("i.belova")
    assert ui.nav().get("Качество") is False
    assert _qv(ui)["claims"] == []
    assert qml_warnings == []
