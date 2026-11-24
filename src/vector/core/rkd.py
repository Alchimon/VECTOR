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

import io
import json
import re
import zipfile
from typing import Any, Optional

from vector.core.delivery import canon

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


# ---------------------------------------------------------------- профили шифров и статусы выдачи (D-62)
# Схему шифра и словарь статусов выдачи задаёт ЗАКАЗЧИК/проект, а не код программы (blueprint 52, §2.2):
# не зашивать один шаблон и не предполагать, что иностранная строка совпадает с ГОСТ-шифром.
MAX_ISSUE_STATUSES = 50
MAX_ISSUE_CODE = 40
MAX_ISSUE_LABEL = 200


def validate_label_pattern(pattern: Optional[str]) -> Optional[str]:
    """Регулярное выражение шифра ревизии: None — без проверки формата; строка должна компилироваться."""
    if pattern is None:
        return None
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Шаблон шифра — не регулярное выражение: {e}.")
    return pattern


def label_matches(pattern: Optional[str], label: str) -> bool:
    """Шифр соответствует шаблону профиля (полное совпадение); без шаблона — любой непустой."""
    return True if pattern is None else re.fullmatch(pattern, label.strip()) is not None


def validate_issue_statuses(raw: Any) -> list[dict]:
    """Словарь статусов выдачи профиля → [{code, label_ru}] (нормализованный) или ValueError с русским текстом.

    Коды непусты, уникальны без учёта регистра, ≤ MAX_ISSUE_CODE; подпись ≤ MAX_ISSUE_LABEL. Пустой список
    допустим: профиль без словаря → статусы выдачи не используются (set_issue откажет no_issue_profile).
    """
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ISSUE_STATUSES:
        raise ValueError(f"Статусы выдачи — список до {MAX_ISSUE_STATUSES} записей.")
    out, seen = [], set()
    for i, x in enumerate(raw, start=1):
        if not isinstance(x, dict):
            raise ValueError(f"Статус выдачи № {i}: нужен объект {{code, label_ru}}.")
        code = str(x.get("code") or "").strip()
        label = str(x.get("label_ru") or "").strip()
        if not code or len(code) > MAX_ISSUE_CODE:
            raise ValueError(f"Статус выдачи № {i}: код обязателен (до {MAX_ISSUE_CODE} знаков).")
        if not label or len(label) > MAX_ISSUE_LABEL:
            raise ValueError(f"Статус выдачи «{code}»: укажите расшифровку (до {MAX_ISSUE_LABEL} знаков).")
        if code.lower() in seen:
            raise ValueError(f"Статус выдачи «{code}» повторяется — коды уникальны.")
        seen.add(code.lower())
        out.append({"code": code, "label_ru": label})
    return out


def issue_status_of(statuses: list[dict], code: str) -> Optional[dict]:
    """Запись словаря по коду (без учёта регистра) или None."""
    norm = code.strip().lower()
    return next((s for s in statuses if s["code"].lower() == norm), None)


# ---------------------------------------------------------------- ZIP-пакет трансмиттала (D-63)
# Пакет выдачи собирается из уже сохранённых неизменяемых версий файлов, а не из меняющегося сетевого пути
# (blueprint 52, §2.2). Байты пакета ДЕТЕРМИНИРОВАНЫ: одинаковый состав и содержимое дают одинаковый SHA-256
# (фиксированная дата записей, нулевая система создания, сортировка имён, манифест — канонический JSON).
PACKAGE_FORMAT = "vector-transmittal-package/1"
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def build_package(manifest: dict, files: list[tuple[str, bytes]]) -> bytes:
    """Собрать ZIP пакета трансмиттала: manifest.json (канонический JSON) + файлы по arcname.

    manifest — словарь с метаданными выдачи (в него уже включён format=PACKAGE_FORMAT вызывающим).
    files — [(arcname, data)]; arcname должны быть уникальны (вызывающий нумерует каталоги позиций).
    Даты записей фиксированы (_ZIP_DATE), имена сортируются — повторная сборка тех же входов даёт
    байт-в-байт тот же пакет, и его хэш в записи трансмиттала доказуем.
    """
    names = [n for n, _ in files]
    if len(set(names)) != len(names):
        raise ValueError("Пути файлов пакета должны быть уникальны.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted([("manifest.json", canon(manifest).encode("utf-8")), *files],
                                 key=lambda x: x[0]):
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o600 << 16
            z.writestr(info, data)
    return buf.getvalue()


def read_package(data: bytes) -> tuple[dict, dict[str, bytes]]:
    """Разобрать пакет трансмиттала: (манифест, {arcname: содержимое}). Проверяет формат и целостность ZIP."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if z.testzip() is not None:
            raise ValueError("ZIP-пакет повреждён (контрольная сумма записи не совпала).")
        names = set(z.namelist())
        if "manifest.json" not in names:
            raise ValueError("В пакете нет manifest.json.")
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        if manifest.get("format") != PACKAGE_FORMAT:
            raise ValueError(f"Неизвестный формат пакета: {manifest.get('format')!r}.")
        return manifest, {n: z.read(n) for n in names if n != "manifest.json"}
