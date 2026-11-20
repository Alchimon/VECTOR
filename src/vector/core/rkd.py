"""Бизнес-ревизии документов РКД: проверка, замечания, трансмитталы (этап 3 blueprint 52).

Чистые правила без БД и сети — ими пользуются и сервер (server/rkd.py), и клиент (подписи, подсказки).
Источник допустимых статусов и переходов — контракт contracts/workflows.slice1.json (раздел docrev);
этот модуль только читает его и добавляет то, что в JSON не выразить: переходы, доступные только
системным командам (трансмиттал, регистрация замечания заказчика), и правила циклов проверки.

Ключевые инварианты (blueprint 52, разделы 2.2 и 3):

* внешний шифр ревизии («REV A», «01») задаёт инженер; он уникален в документе и НЕ повышается при возврате —
  следующая загрузка файла = следующая ревизия (seq+1);
* ревизия ссылается на точную неизменяемую версию файла (document_versions), отправляется только пакетом
  (трансмитталом), а не по сетевому пути;
* «Согласована заказчиком» — только по документу заказчика (в причине — номер письма/протокола, D-56 п. 2);
* замечания привязаны к точной ревизии и закрываются ответом/доказательством; молчаливый перенос замечаний
  на новую ревизию запрещён — переход к доработке/согласованию возможен, только когда все замечания закрыты.
"""
from __future__ import annotations

from typing import Optional

# Переходы, которые выполняет не docrev.transition, а системная команда (см. workflows docrev.system_only):
#   sent — только трансмитталом (transmittal.create); customer_comments — только comment.add(source=customer).
SYSTEM_ONLY_TARGETS: tuple[str, ...] = ("sent", "customer_comments")

# Откуда пришло замечание: внутреннее (проверка РП) или от заказчика (по отправленной ревизии, с основанием).
COMMENT_SOURCES: dict[str, str] = {"internal": "Внутреннее", "customer": "Заказчик"}

# Канал отправки трансмиттала (ручная регистрация; автоотправка — адаптер этапа 8, создаёт ту же запись).
TRANSMITTAL_CHANNELS: dict[str, str] = {
    "email": "Электронная почта",
    "edo": "Система электронного документооборота",
    "platform": "Портал заказчика",
    "hand": "Лично / курьер",
}

# Решение проверки ревизии (document_reviews.decision).
REVIEW_DECISIONS: dict[str, str] = {"accepted": "Проверка пройдена", "returned": "Возвращена на доработку"}

# Решения проверки — только РП проекта (D-56 п. 1): переходы с этими целями требуют права docrev.review.
REVIEW_TARGETS: tuple[str, ...] = ("returned_to_engineer", "ready_to_transmit", "approved")


def revision_workflow(workflows: dict) -> dict:
    """Контракт переходов бизнес-ревизии (workflows.docrev)."""
    return workflows["docrev"]


def can_transition(workflows: dict, from_status: str, to: str) -> bool:
    """Переход from_status → to описан в контракте (без учёта прав и системных ограничений)."""
    return to in revision_workflow(workflows)["transitions"].get(from_status, ())


def needs_reason(workflows: dict, from_status: str, to: str) -> bool:
    """Причина обязательна: цели из reason_required_for (возврат на доработку, согласование заказчиком)
    и отзыв разрешения на отправку (ready_to_transmit → in_work) — он всегда объясняется."""
    return to in revision_workflow(workflows).get("reason_required_for", ()) \
        or (from_status, to) == ("ready_to_transmit", "in_work")


def review_decision_for(to: str) -> Optional[str]:
    """Решение проверки, которое фиксирует переход: возврат → returned, разрешение отправки → accepted;
    остальные переходы записи проверки не создают."""
    return {"returned_to_engineer": "returned", "ready_to_transmit": "accepted"}.get(to)


def label_free(existing_labels: list[str], label: str) -> bool:
    """Шифр ревизии уникален в документе (без учёта регистра и пробелов по краям)."""
    norm = label.strip().lower()
    return all(norm != x.strip().lower() for x in existing_labels)


def open_blockers(statuses: list[str]) -> bool:
    """Есть ли незакрытые замечания: статусы всех замечаний ревизии; доработка/согласование — только при пустом
    списке открытых."""
    return any(s == "open" for s in statuses)
