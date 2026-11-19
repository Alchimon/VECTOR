"""Последняя работа за компьютером (D-49, A-53 п. 1): фильтр событий запоминает только ВРЕМЯ ввода в окне ВЕКТОРа,
не чаще раза в 5 секунд; события идут дальше без изменений; не на Windows системного времени ввода нет."""
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QEvent, QObject

from vector.client import activity

UTC = timezone.utc


def test_input_watcher_remembers_time_only_and_throttles(monkeypatch):
    # Приложение Qt не создаётся: фильтр вызывается напрямую. QCoreApplication здесь сломал бы UI-тесты дальше в том
    # же процессе — им нужно приложение с графикой (QApplication), а второе создать нельзя.
    now = [datetime(2026, 11, 16, 19, 40, tzinfo=UTC)]
    w = activity.InputWatcher(lambda: now[0])
    target = QObject()
    assert w.last is None
    mono = [1000.0]
    monkeypatch.setattr(activity.time, "monotonic", lambda: mono[0])
    assert w.eventFilter(target, QEvent(QEvent.Type.KeyPress)) is False   # событие не «съедается»
    assert w.last == now[0]
    now[0] += timedelta(seconds=3)
    mono[0] += 3
    w.eventFilter(target, QEvent(QEvent.Type.MouseMove))
    assert w.last == now[0] - timedelta(seconds=3)                         # чаще раза в 5 секунд — не обновляется
    now[0] += timedelta(seconds=3)
    mono[0] += 3
    w.eventFilter(target, QEvent(QEvent.Type.MouseMove))
    assert w.last == now[0]
    before = w.last
    now[0] += timedelta(minutes=1)
    mono[0] += 60
    w.eventFilter(target, QEvent(QEvent.Type.Paint))                       # не ввод — не работа
    assert w.last == before


def test_last_activity_takes_latest_and_caps_at_now(monkeypatch):
    now = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
    w = activity.InputWatcher(lambda: now)
    w.last = now - timedelta(minutes=20)
    monkeypatch.setattr(activity, "system_last_input", lambda n: n - timedelta(minutes=5))   # как Windows
    assert activity.last_activity(w, now) == now - timedelta(minutes=5)
    monkeypatch.setattr(activity, "system_last_input", lambda n: None)                        # не Windows
    assert activity.last_activity(w, now) == now - timedelta(minutes=20)
    w.last = now + timedelta(minutes=1)                                                       # часы перевели назад
    assert activity.last_activity(w, now) == now
    assert activity.last_activity(None, now) is None


def test_system_last_input_off_windows(monkeypatch):
    monkeypatch.setattr(activity.sys, "platform", "linux")
    assert activity.system_last_input(datetime(2026, 11, 16, tzinfo=UTC)) is None
