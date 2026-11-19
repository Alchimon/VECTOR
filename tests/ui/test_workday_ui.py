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


def test_prompt_insists_then_start_pause_resume_from_sidebar(ui, qml_warnings):  # noqa: F811
    """m.lebedev: после входа — окно «Начните рабочий день»; оно «настаивает» (D-47, A-51 пп. 3–4): «Позже» нет,
    крестика нет, закрыть нельзя — закрытое открывается снова; «Начать рабочий день» запускает таймер и закрывает
    окно; затем перерыв и продолжение кнопками боковой панели (A-49 п. 3 — перерыв по кнопке)."""
    ui.backend.setWorkdayPromptAuto(True)
    ui.login("m.lebedev")
    prompt = _popup(ui, "workdayPrompt")
    assert prompt.property("visible") is True
    assert prompt.property("insist") is True
    assert prompt.property("cancelText") == "Выйти из учётной записи"
    assert "Начало по графику" in prompt.property("subtitle")          # график «Смена наладчиков» отдела АСУ ТП
    assert _wd(ui)["prompt"] is True and _wd(ui)["can_start"] is True
    prompt.close()                                          # закрыть «мимо кнопок» нельзя: окно возвращается
    ui.wait_idle()
    assert prompt.property("visible") is True
    ui.backend.syncNow()                                    # и после нового снимка данных — тоже
    ui.wait_idle()
    assert prompt.property("visible") is True
    QMetaObject.invokeMethod(prompt, "confirmed")           # «Начать рабочий день»
    ui.wait_idle()
    assert prompt.property("visible") is False
    wd = _wd(ui)
    assert (wd["status"], wd["running"], wd["can_pause"], wd["prompt"]) == ("working", True, True, False)
    assert [e["event"] for e in wd["events"]] == ["start"]
    assert ui.find("workdayWidget").isVisible()
    ui.click(ui.find("btnWorkdayPauseMini"))
    ui.wait_idle()
    assert _wd(ui)["status"] == "paused" and _wd(ui)["running"] is False
    ui.click(ui.find("btnWorkdayResumeMini"))
    ui.wait_idle()
    wd = _wd(ui)
    assert wd["status"] == "working" and [e["event"] for e in wd["events"]] == ["start", "pause", "resume"]
    assert wd["break_label"] != "нет"                       # перерыв посчитан только потому, что нажали кнопку
    assert qml_warnings == []


def test_prompt_logout_and_state_not_carried_to_next_user(ui, qml_warnings):  # noqa: F811
    """Окно «Начните рабочий день» — «Выйти из учётной записи» (чужой компьютер) выходит, окно не возвращается;
    следующий пользователь (e.orlova, автопоказ выключен) окна не видит — состояние сброшено."""
    ui.backend.setWorkdayPromptAuto(True)
    ui.login("m.lebedev")
    prompt = _popup(ui, "workdayPrompt")
    assert prompt.property("visible") is True
    QMetaObject.invokeMethod(prompt, "dismissed")
    ui.wait_idle()
    assert ui.backend.property("screen") == "login"
    assert _wd(ui)["prompt"] is False
    ui.backend.setWorkdayPromptAuto(False)
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
    """e.orlova: «Запросы по рабочему дню (1)» → «Согласовать» → форма решения → запрос согласован; затем d.kuznetsov
    видит, что таймер посчитал день с фактического начала (A-49 п. 4)."""
    ui.login("e.orlova")
    ui.go("worktime")
    wd = _wd(ui)
    assert wd["to_decide"] == 1
    req = next(r for r in wd["review"] if r["user_name"] == "Кузнецов Дмитрий")
    assert req["can_decide"] is True and "объекте заказчика" in req["reason"]
    btn = ui.find("btnWorkdayRequests")
    assert btn.isVisible() and btn.property("text") == "Запросы по рабочему дню (1)"
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


class _Clock:
    """Подставные часы клиента (SyncClient.clock): «сейчас» = t — чтобы дойти до конца дня по графику без ожидания."""

    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t


def test_end_reminder_snooze_auto_finish_and_worked_longer(ui, qml_warnings):  # noqa: F811
    """D-47 (A-51 п. 5): d.kuznetsov забыл «Завершить». Конец дня по графику → окно «Рабочий день по графику
    закончился» (настаивает; поле «Закончил в» проверяется сразу); «Работаю дальше» — окно закрывается до следующего
    напоминания; без ответа 30 минут день завершается сам (без связи — в очередь); в «Рабочем времени» — «Я работал
    дольше»: запрос фактического окончания уходит руководителю."""
    from datetime import datetime, timedelta, timezone
    ui.backend.setWorkdayPromptAuto(True)
    ui.login("d.kuznetsov")
    c = ui.backend.client
    c.clock = _Clock(datetime.now(timezone.utc) + timedelta(hours=20))
    due = datetime.fromisoformat(c.workday_reminder()["due"])             # когда напомнить (конец по графику и т. п.)
    c.t.online = False                                                     # дальше — без связи: часы «в будущем»
    c.clock = _Clock(due + timedelta(minutes=1))
    ui.backend.workdayCheckReminder()
    ui.wait_idle()
    prompt = _popup(ui, "workdayEndPrompt")
    assert prompt.property("visible") is True and prompt.property("insist") is True
    assert prompt.property("cancelText") == "Работаю дальше"
    assert _wd(ui)["reminder"] is True and f"{due.astimezone():%H:%M}" in prompt.property("subtitle")
    fin = _popup(ui, "wdFinishAt")
    fin.setProperty("text", "99:99")
    ui.wait_idle()
    assert prompt.property("canConfirm") is False and fin.property("error")
    fin.setProperty("text", "")
    ui.wait_idle()
    assert prompt.property("canConfirm") is True
    QMetaObject.invokeMethod(prompt, "dismissed")                          # «Работаю дальше» — через час снова
    ui.wait_idle()
    assert prompt.property("visible") is False and _wd(ui)["reminder"] is False
    c.clock = _Clock(due + timedelta(minutes=95))                          # снова напоминание и 30 минут без ответа
    ui.backend.workdayCheckReminder()
    ui.wait_idle()
    ui.backend.workdayCheckReminder()
    ui.wait_idle()
    wd = _wd(ui)
    assert wd["auto_finished"] is True and wd["can_request_end"] is True, wd.get("end_label")
    assert "завершён автоматически" in wd["end_label"]
    assert any(q["command"] == "workday.mark" for q in c.queue())
    prompt_start = _popup(ui, "workdayPrompt")
    if prompt_start.property("visible"):                                   # на подставных часах уже новый день
        prompt_start.setProperty("insist", False)
        ui.backend.setWorkdayPromptAuto(False)
        prompt_start.close()
    ui.go("worktime")
    assert ui.find("workdayAutoFinished").isVisible()
    btn = ui.find("btnWorkdayEndLater")
    assert btn.isVisible() and btn.property("enabled") is True
    form = _popup(ui, "workdayEndForm")
    _call(form, "openFor")
    ui.wait_idle()
    assert form.property("visible") is True and form.property("canConfirm") is False   # причина пустая
    _popup(ui, "workdayEndReason").setProperty("text", "Доделывал схему на объекте")
    ui.wait_idle()
    assert form.property("canConfirm") is True, _popup(ui, "workdayEndTime").property("error")
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert not form.property("visible"), form.property("error")
    wd = _wd(ui)
    assert wd["can_request_end"] is False and wd["pending_end_request"]["kind"] == "end"
    assert ui.find("workdayPendingEnd").isVisible()
    assert [q["command"] for q in c.queue()][-1] == "workday.end_request"
    assert qml_warnings == []


def test_head_approves_actual_end(ui, qml_warnings):  # noqa: F811
    """Демо: i.belova забыла «Завершить», день завершён автоматически, она просит считать окончание на час позже.
    Решает тот же, кто проверяет её отчёты (A-51 п. 6), — v.zaitseva (руководитель коммерческой службы)."""
    ui.login("v.zaitseva")
    ui.go("worktime")
    wd = _wd(ui)
    req = next(r for r in wd["review"] if r["kind"] == "end")
    assert (req["user_name"], req["kind_label"], req["command"]) == ("Белова Ирина", "Окончание дня", "workday.end_decide")
    assert req["can_decide"] is True and "поставку" in req["reason"]
    btn = ui.find("btnWorkdayRequests")
    assert btn.isVisible() and btn.property("text") == "Запросы по рабочему дню (1)"
    form = _popup(ui, "workdayDecideForm")
    _call(form, "openFor", req, "approve")
    ui.wait_idle()
    assert "окончан" in form.property("title").lower()
    QMetaObject.invokeMethod(form, "confirmed")
    ui.wait_idle()
    assert not form.property("visible"), form.property("error")
    done = next(r for r in _wd(ui)["review"] if r["id"] == req["id"])
    assert done["status"] == "approved"
    ui.backend.logout()
    ui.wait_idle()
    ui.login("i.belova")
    wd = _wd(ui)
    assert wd["pending_end_request"] == {}
    assert wd["my_requests"][0]["status_label"] == "Согласовано"
    assert qml_warnings == []
