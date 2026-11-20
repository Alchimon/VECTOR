"""UI-проверки этапа 6 «Качество» (С5 шаг 10, D-39) — настоящий QML, сервер in-process, демо-данные.

Демо: рекламация № 1 по «АТХ № 0001 (серия 2026)» — в состоянии «Действия»: корректирующее действие выполнил
d.kuznetsov, предупреждающее (исполнитель m.lebedev) ещё не выполнено. Рекламация № 2 (D-40) — на партию (наряд № 2)
без номера изделия, зарегистрировала ОТК l.egorova, срок ответа заказчику — завтра. Запрос на изменение № 1 по рекламации подал
d.kuznetsov — ждёт оценки. m.lebedev сейчас замещает ГИП в проекте (D-35), p.volkov — ГИП, a.smirnova — РП.
"""
import pytest
from PySide6.QtCore import QMetaObject

from tests.ui.test_purchasing_ui import _call, _popup, _v, qml_warnings  # noqa: F401

pytestmark = pytest.mark.ui


def _qv(ui):
    """Текущее представление раздела «Качество»."""
    return _v(ui.backend, "qualityOverview")


def _claim(ui, label):
    """Рекламация из представления по номеру (порядок списка зависит от сроков и состояний)."""
    return next(c for c in _qv(ui)["claims"] if c["label"] == label)


def _confirm(ui, form):
    """Нажать «подтвердить» в форме и дождаться ответа сервера."""
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()


def test_action_owner_marks_done_and_sees_why_he_cannot_check(ui, qml_warnings):  # noqa: F811
    """Исполнитель действия CAPA отмечает его; после отправки на проверку он же проверять не может — и видит почему."""
    ui.login("m.lebedev")
    assert ui.nav()["Качество"] is True
    c = _claim(ui, "Рекламация № 1")
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
    c = _claim(ui, "Рекламация № 1")
    assert [a["done"] for a in c["actions"]] == [True, True] and c["can_submit"]    # замещает ГИП — ведёт рекламацию
    ui.backend.qualityCommand("t-sub", "claim.submit_check", {"id": c["id"]}, c["version"])
    ui.wait_idle()
    c = _claim(ui, "Рекламация № 1")
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
    assert v["may_claim"] and len(v["claims"]) == 2
    ui.go("quality")
    ui.transport.online = False
    f = _popup(ui, "claimForm")
    _call(f, "openFor")
    ui.wait_idle()
    assert f.property("canConfirm") is False                      # суть не заполнена
    _popup(ui, "claimDescription").setProperty("text", "Шум вентилятора шкафа управления")
    ui.wait_idle()
    assert f.property("canConfirm") is False                      # тяжесть и срок ответа заказчику не указаны (D-40)
    _popup(ui, "claimSeverity").setProperty("current", "minor")
    _call(f, "plusDays", 10)
    ui.wait_idle()
    assert f.property("canConfirm") is True
    _confirm(ui, f)
    new = [c for c in _qv(ui)["claims"] if c["pending"]]
    assert [c["label"] for c in new] == ["Рекламация (не отправлена)"] and ui.backend.property("pendingCount") == 1
    ui.transport.online = True
    ui.backend.syncNow()
    ui.wait_idle()
    assert sorted(c["label"] for c in _qv(ui)["claims"]) == ["Рекламация № 1", "Рекламация № 2", "Рекламация № 3"]
    c3 = _claim(ui, "Рекламация № 3")
    assert (c3["severity_label"], c3["deadline"]) == ("Незначительный", "ok")
    assert qml_warnings == []


def test_section_hidden_for_warehouse(ui, qml_warnings):  # noqa: F811
    """Кладовщик к рекламациям и изменениям доступа не имеет — раздела в меню нет."""
    ui.login("i.belova")
    assert ui.nav().get("Качество") is False
    assert _qv(ui)["claims"] == []
    assert qml_warnings == []


def test_otk_answers_customer_and_reclassifies_batch_claim(ui, qml_warnings):  # noqa: F811
    """ОТК (D-40): видит рекламацию на партию со сроком «подходит», записывает ответ заказчику — срок закрыт;
    уточняет тяжесть дефекта (причина обязательна)."""
    ui.login("l.egorova")
    assert ui.nav()["Качество"] is True
    c = _claim(ui, "Рекламация № 2")
    assert (c["scope"], c["scope_label"], c["deadline"], c["deadline_alert"]) == ("batch", "Партия (наряд)", "soon", True)
    assert c["can_respond"] and c["can_classify"] and c["can_set_due"]
    assert _qv(ui)["counts"]["due_alert"] >= 1
    ui.go("quality", c["id"])
    rf = _popup(ui, "claimRespondForm")
    _call(rf, "openFor")
    ui.wait_idle()
    assert rf.property("canConfirm") is False                      # нет текста ответа
    _popup(ui, "respondSummary").setProperty("text", "Таблички будут закреплены при сервисном визите 20.11")
    ui.wait_idle()
    assert rf.property("canConfirm") is True
    _confirm(ui, rf)
    c = _claim(ui, "Рекламация № 2")
    assert c["deadline"] == "answered" and [r["kind_label"] for r in c["responses"]] == ["Промежуточный ответ"]
    cf = _popup(ui, "claimClassifyForm")
    _call(cf, "openFor")
    ui.wait_idle()
    _popup(ui, "classifySeverity").setProperty("current", "major")
    ui.wait_idle()
    assert cf.property("canConfirm") is False                      # причина изменения обязательна
    _popup(ui, "classifyReason").setProperty("text", "Без табличек шкафы на объекте путают — риск ошибки оператора")
    ui.wait_idle()
    _confirm(ui, cf)
    assert _claim(ui, "Рекламация № 2")["severity_label"] == "Значительный"
    assert qml_warnings == []


def test_rp_registers_batch_claim_without_serial(ui, qml_warnings):  # noqa: F811
    """РП (D-40): рекламация на партию (наряд) — без выбора экземпляра; заводской номер не нужен."""
    ui.login("a.smirnova")
    v = _qv(ui)
    assert [s["id"] for s in v["scopes"]] == ["instance", "batch", "project"] and v["batch_options"]
    ui.go("quality")
    f = _popup(ui, "claimForm")
    _call(f, "openFor")
    _popup(ui, "claimScope").setProperty("current", "batch")
    _popup(ui, "claimSource").setProperty("current", "internal")
    _popup(ui, "claimSeverity").setProperty("current", "major")
    _popup(ui, "claimDescription").setProperty("text", "На входном контроле партии — ослабленные клеммы")
    ui.wait_idle()
    assert f.property("canConfirm") is True                        # внутренняя рекламация — без срока ответа
    _confirm(ui, f)
    c = _claim(ui, "Рекламация № 3")
    assert (c["scope"], c["deadline"], c["customer"]) == ("batch", "none", False)
    assert qml_warnings == []
