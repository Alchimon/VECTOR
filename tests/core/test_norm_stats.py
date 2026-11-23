"""Ядро: агрегированная статистика норм трудоёмкости (этап 7 blueprint 52, D-72).

norm_stats группирует завершённые операции нарядов по (наименование, рабочий центр) и считает диапазон
фактических минут НА ОПЕРАЦИЮ: размер выборки, мин/медиана/макс. Это ориентир для ручного решения — программа
не выставляет норму и не строит рейтинг исполнителей (агрегат без персональных данных).
"""
from vector.core.production import norm_stats


def _op(name, wc, seconds, finished=True):
    """Завершённая операция наряда (как строка wo_operations): name, work_center, fact_seconds, finished_at."""
    return {"name": name, "work_center": wc, "fact_seconds": seconds,
            "finished_at": "2026-11-16T10:00:00+00:00" if finished else None}


def test_norm_stats_groups_ranges_and_skips_unfinished():
    ops = [_op("Сборка", "Участок 1", 3600), _op("Сборка", "Участок 1", 5400), _op("Сборка", "Участок 1", 7200),
           _op("Сборка", "Участок 1", 0, finished=False),          # не завершена — не в выборке
           _op("Сборка", "Участок 1", 9999, finished=False),
           _op("Сборка", None, 1800),                               # без рабочего центра — отдельная группа
           _op("Сборка", "Участок 1", 0)]                           # нулевой факт — не считается
    stats = norm_stats(ops)
    assert stats == [{"op_name": "Сборка", "work_center": "", "sample_size": 1,
                      "min_min": 30.0, "median_min": 30.0, "max_min": 30.0},
                     {"op_name": "Сборка", "work_center": "Участок 1", "sample_size": 3,
                      "min_min": 60.0, "median_min": 90.0, "max_min": 120.0}]


def test_norm_stats_median_of_even_sample_and_rounding():
    # Чётная выборка: медиана — среднее двух центральных; округление до десятых минуты.
    stats = norm_stats([_op("Обжим", "", 590), _op("Обжим", "", 611)])
    assert stats[0]["sample_size"] == 2
    assert stats[0]["min_min"] == round(590 / 60, 1) and stats[0]["max_min"] == round(611 / 60, 1)
    assert stats[0]["median_min"] == round((590 + 611) / 2 / 60, 1)
    assert norm_stats([]) == []
