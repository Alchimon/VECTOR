"""Страж эталонного ядра (spikes/reference-core): конструктор Server проверяет fail-closed, что каждая команда
каталога либо исполнена в спайке, либо явно объявлена вне его области (OUT_OF_SPIKE_SCOPE).

Сам спайк — исполняемая спецификация среза 1 на stdlib; его полный набор (36 тестов) — отдельным прогоном
`python -m unittest` в spikes/reference-core/tests (см. CONTRIBUTING § 5). Этот тест ловит главную гниль —
молчаливый разъезд каталога и спайка: без него проверка сгнила незамеченной на ~120 команд (D-20…D-76).
"""
from __future__ import annotations

import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[2] / "spikes" / "reference-core"


def test_spike_catalog_and_handlers_consistent(tmp_path):
    """Каталог команд ↔ обработчики спайка согласованы (иначе конструктор Server отказывает)."""
    sys.path.insert(0, str(SPIKE))
    try:
        from vector_ref.server import Server  # noqa: PLC0415 — путь спайка не в пакете продукта
        srv = Server(str(tmp_path / "spike.db"))
        assert srv.handlers, "у спайка должен быть хотя бы один обработчик"
    finally:
        sys.path.remove(str(SPIKE))
