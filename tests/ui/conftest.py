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
    from ui_harness import Harness
    h = Harness("light")
    yield h
    h.close()
