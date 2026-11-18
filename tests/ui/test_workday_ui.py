"""UI-проверки таймера рабочего дня (D-46, ответы на A-49 пп. 2–4): настоящий QML, сервер in-process, демо-данные.

Демо (vector.server.demo._seed_workday): d.kuznetsov начал день 3 ч назад, был перерыв, и его запрос «фактическое
начало на 1 ч раньше» ждёт решения e.orlova (руководитель «Инженерной дирекции», над «Отделом АСУ ТП»); m.lebedev
вчера отработал полный день, сегодня таймер не запускал — ему показывается окно «Начните рабочий день».

В тестах окно «Начните рабочий день» само не открывается (Backend(auto_timers=False)), поэтому тест окна включает его
явно — setWorkdayPromptAuto(True) до входа.
"""
import pytest
from PySide6.QtCore import QMetaObject

from tests.ui.test_purchasing_ui import _call, _popup, _v, qml_warnings  # noqa: F401

pytestmark = pytest.mark.ui


def _wd(ui) -> dict:
    """Текущая модель таймера (backend.workdayOverview) как dict."""
    return _v(ui.backend, "workdayOverview")


def test_prompt_later_then_start_pause_resume_from_sidebar(ui, qml_warnings):  # noqa: F811
    """m.lebedev: после входа — окно «Начните рабочий день»; «Позже» закрывает и больше не открывает; затем день
    начинается кнопкой боковой панели, перерыв и продолжение меняют состояние таймера (A-49 п. 3 — перерыв по кнопке)."""
    ui.backend.setWorkdayPromptAuto(True)
    ui.login("m.lebedev")
    prompt = _popup(ui, "workdayPrompt")
    assert prompt.property("visible") is True
    assert prompt.property("cancelText") == "Позже"
    assert _wd(ui)["prompt"] is True and _wd(ui)["can_start"] is True
    QMetaObject.invokeMethod(prompt, "dismissed")
    prompt.close()
    ui.wait_idle()
    assert _wd(ui)["prompt"] is False
    ui.backend.syncNow()                                    # новый снимок данных — окно не возвращается
    ui.wait_idle()
    assert prompt.property("visible") is False             # «Позже» — сегодня окно больше не показывается
    # Карточка таймера в боковой панели — компактная, с кнопкой «Начать рабочий день».
    assert ui.find("workdayWidget").isVisible()
    start = ui.find("btnWorkdayStartMini")
    assert start.isVisible()
    ui.click(start)
    ui.wait_idle()
    wd = _wd(ui)
    assert (wd["status"], wd["running"], wd["can_pause"]) == ("working", True, True)
    assert [e["event"] for e in wd["events"]] == ["start"]
    ui.click(ui.find("btnWorkdayPauseMini"))
    ui.wait_idle()
    assert _wd(ui)["status"] == "paused" and _wd(ui)["running"] is False
    ui.click(ui.find("btnWorkdayResumeMini"))
    ui.wait_idle()
    wd = _wd(ui)
    assert wd["status"] == "working" and [e["event"] for e in wd["events"]] == ["start", "pause", "resume"]
    assert wd["break_label"] != "нет"                       # перерыв посчитан только потому, что нажали кнопку
    assert qml_warnings == []


def test_prompt_state_not_carried_to_next_user(ui, qml_warnings):  # noqa: F811
    """Окно «Начните рабочий день» — по своему состоянию: m.lebedev не начал день и вышел, не ответив; следующий
    пользователь (e.orlova, у неё сегодня тоже не начат, но автопоказ выключен) окна не видит — состояние сброшено."""
    ui.backend.setWorkdayPromptAuto(True)
    ui.login("m.lebedev")
    prompt = _popup(ui, "workdayPrompt")
    assert prompt.property("visible") is True
    prompt.close()
    ui.backend.setWorkdayPromptAuto(False)
    ui.backend.logout()
    ui.wait_idle()
    assert _wd(ui)["prompt"] is False
    ui.login("e.orlova")
    assert _popup(ui, "workdayPrompt").property("visible") is False
    assert qml_warnings == []


def test_started_day_shows_card_and_pending_request(ui, qml_warnings):  # noqa: F811
    """d.kuznetsov: день идёт — окна нет даже с включённым автопоказом; в «Рабочем времени» карточка таймера с
    началом и перерывами, запрос на фактическое начало «на согласовании», повторный запрос недоступен."""
    ui.backend.setWorkdayPromptAuto(True)
    ui.login("d.kuznetsov")
    assert _popup(ui, "workdayPrompt").property("visible") is False
    ui.go("worktime")
    card = ui.find("workdayCard")
    assert card is not None and card.isVisible()
    wd = _wd(ui)
    assert wd["status"] == "working" and wd["worked_seconds"] > 0
    assert wd["pending_request"]["status"] == "pending"
    assert wd["can_request"] is False and wd["request_block"] == "Запрос уже на согласовании у руководителя."
    assert ui.find("workdayPendingRequest").isVisible()
    assert ui.find("btnWorkdayEarlier").property("enabled") is False
    assert ui.find("workdayClock").property("text").count(":") == 2      # ЧЧ:ММ:СС
    assert qml_warnings == []


def test_head_approves_actual_start(ui, qml_warnings):  # noqa: F811
    """e.orlova: «Запросы на начало дня (1)» → «Согласовать» → форма решения → запрос согласован; затем d.kuznetsov
    видит, что таймер посчитал день с фактического начала (A-49 п. 4)."""
    ui.login("e.orlova")
    ui.go("worktime")
    wd = _wd(ui)
    assert wd["to_decide"] == 1
    req = next(r for r in wd["review"] if r["user_name"] == "Кузнецов Дмитрий")
    assert req["can_decide"] is True and "объекте заказчика" in req["reason"]
    btn = ui.find("btnWorkdayRequests")
    assert btn.isVisible() and btn.property("text") == "Запросы на начало дня (1)"
    form = _popup(ui, "workdayDecideForm")
    _call(form, "openFor", req, "approve")
    ui.wait_idle()
    assert form.property("visible") is True and form.property("canConfirm") is True
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert not form.property("visible"), form.property("error")
    done = next(r for r in _wd(ui)["review"] if r["id"] == req["id"])
    assert done["status"] == "approved" and _wd(ui)["to_decide"] == 0
    ui.backend.logout()
    ui.wait_idle()
    ui.login("d.kuznetsov")
    wd = _wd(ui)
    assert wd["approved"] is True and wd["approved_label"].startswith("Фактическое начало ")
    assert wd["pending_request"] == {}
    assert qml_warnings == []


def test_reject_needs_reason(ui, qml_warnings):  # noqa: F811
    """Отклонить без причины нельзя: кнопка формы неактивна, пока поле «Причина отказа» пустое."""
    ui.login("e.orlova")
    ui.go("worktime")
    req = _wd(ui)["review"][0]
    form = _popup(ui, "workdayDecideForm")
    _call(form, "openFor", req, "reject")
    ui.wait_idle()
    assert form.property("canConfirm") is False
    _popup(ui, "workdayDecideComment").setProperty("text", "В это время вас не было на объекте")
    ui.wait_idle()
    assert form.property("canConfirm") is True
    form.close()
    assert qml_warnings == []
