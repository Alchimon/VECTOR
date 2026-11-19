"""Последняя работа за компьютером (этап 7, R-20; decision log D-49 — ответ владельца на A-53 п. 1).

Владелец: «надо исключить тот факт, что человек заработался и не нажал, а он по факту работал». Поэтому
автоматическое «Завершить» забытого дня ждёт, пока за компьютером перестанут работать, и записывается временем
последней активности (правила — vector.core.workday.reminder_state / auto_finish_time).

Что считается активностью и что программа узнаёт:

* Windows — время последнего нажатия клавиатуры или мыши **в любой программе** этого сеанса Windows (функция
  GetLastInputInfo): человек может весь вечер работать в САПР, не трогая окно ВЕКТОРа;
* другие системы и запасной вариант — нажатия и движения мыши **в окне ВЕКТОРа** (фильтр событий Qt).

Программа узнаёт только **время** последнего нажатия — не какие клавиши, не в какой программе, не что на экране.
Время хранится в зашифрованном кэше этого компьютера (SyncClient.workday_note_activity) и уходит на сервер только
как время автоматического «Завершить».
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject

# Какие события окна считаются работой: клавиатура, кнопки и колесо мыши, движение мыши, касание экрана.
_INPUT_EVENTS = frozenset({QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                           QEvent.Type.Wheel, QEvent.Type.TouchBegin})
THROTTLE_SECONDS = 5.0               # движение мыши приходит сотнями событий — время берём не чаще раза в 5 секунд


def system_last_input(now: datetime) -> Optional[datetime]:
    """Время последнего нажатия клавиатуры или мыши в сеансе Windows (GetLastInputInfo); не Windows или функция
    недоступна — None. now — «сейчас» по часам программы (от него отсчитывается простой)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            """Структура Windows: размер и счётчик миллисекунд (GetTickCount) в момент последнего ввода."""
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        # Счётчик 32-битный и переполняется раз в 49,7 суток — разность берётся по модулю 2^32.
        idle_ms = (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return now - timedelta(milliseconds=idle_ms)
    except Exception:                                     # noqa: BLE001 — нет функции, другая сборка Windows
        return None


class InputWatcher(QObject):
    """Фильтр событий приложения: запоминает время последнего ввода в окне ВЕКТОРа (часы — clock, те же, что у
    таймера рабочего дня; в тестах — подставные). Само окно и события не меняет."""

    def __init__(self, clock: Callable[[], datetime], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._clock = clock
        self._mono = 0.0
        self.last: Optional[datetime] = None

    def eventFilter(self, obj, event) -> bool:            # noqa: N802 — имя метода задаёт Qt
        """Ввод — запомнить время (не чаще раза в THROTTLE_SECONDS); событие идёт дальше без изменений."""
        try:
            if event.type() in _INPUT_EVENTS:
                m = time.monotonic()
                if m - self._mono >= THROTTLE_SECONDS:
                    self._mono = m
                    self.last = self._clock()
        except Exception:                                 # noqa: BLE001 — фильтр не должен ломать окно
            pass
        return False


def last_activity(watcher: Optional[InputWatcher], now: datetime) -> Optional[datetime]:
    """Последняя работа за компьютером: позднее из «в любой программе Windows» и «в окне ВЕКТОРа»; не позже now."""
    found = [t for t in (system_last_input(now), watcher.last if watcher else None) if t is not None]
    return min(max(found), now) if found else None
