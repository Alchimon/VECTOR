"""UI-тесты (Class A): настоящий QML в offscreen-режиме, сервер — in-process (LoopbackTransport).

На Linux без GPU нужен LD_LIBRARY_PATH со стабами (tools/qt_offscreen_stubs.py); переменные Qt выставляются здесь,
до создания QGuiApplication. Это не проверка Windows/DPI/шрифтов ОС — такие проверки относятся к Class C.
"""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

pytest.importorskip("PySide6.QtQuick")


@pytest.fixture
def ui():
    """Фикстура: стенд интерфейса (светлая тема) на один тест; закрывается после теста."""
    from ui_harness import Harness
    h = Harness("light")
    # Большинство UI-тестов написано на классическую оболочку, а с D-100 умолчание продукта — интерфейс 2.0.
    # Поэтому стенд явно переключаем в классику; тесты 2.0 (test_shell2.py) выставляют режим сами.
    h.backend.uiMode = "classic"
    h.wait_idle()
    yield h
    h.close()
