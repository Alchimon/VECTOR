"""Страж версии PySide6: сигналы и преобразование dict → QVariant не должны портить счётчики ссылок.

История (D-21): в PySide6 6.12.0 каждый Signal.emit() без получателей «терял» ссылку на True/False,
а чтение свойства со словарём, где есть None, — ссылку на None. Через несколько тысяч сигналов
интерпретатор аварийно завершался (Fatal Python error: bool_dealloc / none_dealloc) — для настольного
приложения, работающего весь день, это гарантированное падение.

Проверка выполняется в отдельном процессе: при регрессии он падает сам, а не роняет весь прогон pytest.
При обновлении PySide6 этот тест обязан пройти до поднятия верхней границы в pyproject.toml.
"""
import os
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("PySide6.QtCore")

PROBE = textwrap.dedent('''
    import sys
    from PySide6.QtCore import QCoreApplication, QObject, Property, Signal
    app = QCoreApplication([])

    class O(QObject):
        s = Signal()
        t = Signal(str, "QVariant")
        p = Property("QVariantList", lambda self: [{"a": None, "b": [None, {"c": None}], "d": True}], constant=True)

    o = O()
    o.t.connect(lambda a, b: None)
    checks = [("emit()", lambda: o.s.emit()),
              ("emit(str, map с None)", lambda: o.t.emit("x", {"a": None, "f": False})),
              ("чтение свойства с None", lambda: o.property("p"))]
    bad = []
    for name, fn in checks:
        for _ in range(100):
            fn()
        before = (sys.getrefcount(None), sys.getrefcount(True), sys.getrefcount(False))
        for _ in range(5000):
            fn()
        after = (sys.getrefcount(None), sys.getrefcount(True), sys.getrefcount(False))
        # Допускаем небольшой шум (кэши интерпретатора), но не линейную утечку ~1 ссылки на вызов.
        if any(abs(a - b) > 50 for a, b in zip(after, before)):
            bad.append(f"{name}: {before} -> {after}")
    print("BAD" if bad else "OK", *bad, sep="\\n")
''')


def test_signals_and_variant_conversion_keep_refcounts():
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    r = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, f"процесс упал (вероятно, ошибка PySide6): {r.stderr[-800:]}"
    assert r.stdout.strip().startswith("OK"), r.stdout
