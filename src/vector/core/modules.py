"""Модули платформы и лицензии (этап 8, экосистема; decision log D-80, предположение A-64).

Видение владельца (2026-11-25): одна установка ВЕКТОРА закрывает все потребности компании — сегодняшние разделы
(проекты, документы, расчёты, склад, закупки, производство, качество, люди) — это МОДУЛИ платформы, и к ним
добавятся собственные инженерные и офисные программы (схемотехника — аналог EPLAN, САПР — аналог AutoCAD/КОМПАС,
текстовый редактор, таблицы, презентации) и смежные. Продажа — помодульно: подписка или бессрочная лицензия,
с поддержкой или без (docs/blueprint/53-ecosystem.md).

Отсюда технические требования к этому файлу:

* **Модули — записи реестра, а не отдельные дистрибутивы.** Один установщик; модуль включается лицензией.
  Общее ядро (вход, оргструктура, права, файлы, синхронизация, уведомления) — всегда, без лицензии.
* **Лицензия проверяется офлайн.** Сервер стоит на площадке клиента, интернета может не быть никогда: лицензия —
  JSON-файл с подписью Ed25519, публичный ключ вендора встроен в программу; проверка — локально, без сети.
* **Истёкшая подписка не отнимает данные.** Данные — собственность клиента: после окончания подписки модуль
  переходит в режим «только чтение» (смотреть, печатать и выгружать можно всегда), а льготный период даёт время
  оплатить продление без остановки работы. Блокировать производство клиента из-за счёта — недопустимо.
* **Защита — от честных.** Исполняемый код находится у клиента: подпись не остановит злоумышленника с отладчиком,
  и не должна мешать легальному пользователю. Юридическая защита — договор; техническая — подписанный файл,
  чтобы случайное «скопировали папку» не превращалось в неучтённые рабочие места.

Формат файла лицензии (UTF-8, без BOM):

    {
      "payload": { "license_id", "customer", "issued_at", "kind", "valid_until", "support_until",
                   "modules", "seats", "grace_days", "key_id" },
      "signature": "<base64 Ed25519 по каноническому JSON payload>"
    }

Канонический JSON: json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False). Подписывает
вендор (tools/make_license.py — для стенда; боевой ключ хранится у вендора, НЕ в репозитории). ``key_id`` позволяет
сменить ключ, не перевыпуская старые лицензии.

Алгоритм подписи — сменный (D-86, ФСТЭК/КИИ — 54-compliance-fstek.md § 3.1): поле ``alg`` в payload, реестр
верификаторов ``register_verifier``. Встроенный — ``ed25519``. Для заказчиков с КИИ вендор выпускает лицензию в
профиле ГОСТ Р 34.10-2012, а проверку выполняет СЕРТИФИЦИРОВАННЫЙ СКЗИ площадки (КриптоПро CSP / ViPNet) —
свою реализацию ГОСТ мы не пишем (без сертификата она юридического веса не имеет). Лицензия с алгоритмом без
зарегистрированного верификатора отклоняется честно (``unsupported_alg``), а не «проходит молча».
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

# ---------------------------------------------------------------- реестр модулей

@dataclass(frozen=True)
class Module:
    """Модуль платформы: id стабилен (идёт в лицензии и права), stage — где модуль находится."""
    id: str
    title_ru: str
    stage: str                     # current — в продукте; planned — в планах экосистемы
    note_ru: str = ""


# Ядро всегда доступно и в лицензию не входит: вход, оргструктура, права, файлы, синхронизация, уведомления,
# справка. Без ядра ничего не работает, поэтому продавать его отдельно бессмысленно.
MODULES: tuple[Module, ...] = (
    Module("pdm", "Проекты, документы и расчёты", "current",
           "заказчики, тендеры, ТКП, комплекты, задачи, документы, расчёты, РКД"),
    Module("supply", "Склад и закупка", "current", "остатки, резервы, потребности, заказы, поставки, пополнение"),
    Module("production", "Производство", "current", "ТП, наряды, нормы, готовность сборки, производственный календарь"),
    Module("quality", "Качество", "current", "изменения ECR/ECO, рекламации, несоответствия"),
    Module("service", "Экземпляры и сервис", "current", "серийные номера, паспорта, передача, эксплуатация"),
    Module("hr", "Люди и время", "current", "рабочее время, замещения, переработки, доставка проектов"),
    Module("analytics", "Отчёты и итоги", "current", "итоги проектов, сводка, консолидация"),
    Module("integrations", "Интеграции", "current", "1С, почта, CAD-адаптеры — каждый адаптер выключаем"),
    # Экосистема (D-80): собственные программы вместо внешних. Порядок — предложение, решение за владельцем.
    Module("sheets", "ВЕКТОР Меркурий", "current",
           "таблицы и спецификации, аналог Excel: форматы .vsheetx/.xlsx, формулы, обмен таблицами (Ф1.1); серверные документы (Ф1.3); редактор — Ф1.4"),
    Module("text", "ВЕКТОР Венера", "current",
           "документы, аналог Word: форматы .vdocx/.docx, шаблоны с полями из платформы (Ф1.2–Ф1.3); редактор — Ф1.4"),
    Module("schema", "ВЕКТОР МАРС", "planned", "электротехнический проект, аналог EPLAN: схемы Э3 по ЕСКД, перечень элементов — в спецификацию"),
    Module("cad", "ВЕКТОР Юпитер", "planned", "черчение и CAD, аналог AutoCAD/КОМПАС: 2D ЕСКД, просмотр 3D (STEP)"),
    Module("slides", "ВЕКТОР Презентации", "planned", "аналог PowerPoint (в перспективе — часть ВЕКТОР Венера)"),
)

MODULE_BY_ID = {m.id: m for m in MODULES}

# Команда каталога → модуль (по префиксу команды). Чего нет в таблице — платформа: вход, учётные записи,
# оргструктура, права, конфигурация, уведомления, замещения — доступны всегда и в лицензию не входят. Тест
# test_command_module_covers_catalog следит, что каждая команда каталога попадает в известный модуль или
# осознанно остаётся платформой.
_COMMAND_PREFIX_MODULE = {
    # Проекты, документы и расчёты. office_doc. → text по умолчанию (set_template/render — всегда «Текст»;
    # create(kind=sheet) гейтится на «Таблицы» внутри транзакции — префикс параметра не видит; D-94).
    "calc.": "pdm", "checklist.": "pdm", "comment.": "pdm", "contract.": "pdm", "customer.": "pdm",
    "doc_template.": "pdm", "docprofile.": "pdm", "docrev.": "pdm", "document.": "pdm", "kit.": "pdm",
    "office_doc.": "text",
    "opportunity.": "pdm", "project.": "pdm", "risk.": "pdm", "task.": "pdm", "tender.": "pdm", "tkp.": "pdm",
    "transmittal.": "pdm", "workstream.": "pdm",
    # Склад и закупка
    "analog.": "supply", "item.": "supply", "item_alias.": "supply", "need.": "supply", "po.": "supply",
    "proposal.": "supply", "stock.": "supply", "supplier.": "supply", "unit_conversion.": "supply",
    "warehouse.": "supply",
    # Производство
    "batch.": "production", "calendar.": "production", "norm.": "production", "tp.": "production", "wo.": "production",
    # Качество
    "claim.": "quality", "ecr.": "quality", "eco.": "quality",
    # Экземпляры и сервис (включая ПНР, приёмо-сдаточные испытания и документы поставки заказчику)
    "delivery_doc.": "service", "fat.": "service", "instance.": "service", "passport.": "service", "pnr.": "service",
    # Люди и время
    "hr.": "hr", "overtime.": "hr", "report.": "hr", "schedule.": "hr", "workday.": "hr",
    # Платформа (не лицензируется): account., admin., config., delegation., notification., org.
}


def is_config_change(command: str) -> bool:
    """Команда меняет административную конфигурацию (учётки, права, оргструктура, оповещения) — для отчёта
    службы безопасности «изменения конфигурации за период» (54-compliance-fstek.md § 3.2, ФСТЭК). Платформенные
    префиксы — те, что в карте отсутствуют (account/admin/config/…), иначе попадали бы и бизнес-команды."""
    return command_module(command) is None and (command.split(".", 1)[0] + ".") not in _COMMAND_PREFIX_MODULE \
        and "." in command


def command_module(command: str) -> Optional[str]:
    """Модуль, которому принадлежит команда каталога, или None — платформа (доступна всегда)."""
    return _COMMAND_PREFIX_MODULE.get(command.split(".", 1)[0] + ".")
CURRENT_MODULE_IDS = frozenset(m.id for m in MODULES if m.stage == "current")
PLANNED_MODULE_IDS = frozenset(m.id for m in MODULES if m.stage == "planned")

# ---------------------------------------------------------------- ошибки

class LicenseError(Exception):
    """Лицензия не читается или не проходит проверку. code стабилен — на него опираются сервер и тесты."""

    def __init__(self, code: str, message_ru: str, details: Optional[dict[str, Any]] = None):
        super().__init__(f"{code}: {message_ru}")
        self.code = code
        self.message_ru = message_ru
        self.details = details or {}


# ---------------------------------------------------------------- лицензия

LICENSE_KINDS = ("subscription", "perpetual")
KIND_LABELS_RU = {"subscription": "Подписка", "perpetual": "Бессрочная"}
DEFAULT_GRACE_DAYS = 14

# Состояния модуля для клиента/сервера (label_ru — для интерфейса).
MODULE_STATES = ("active", "grace", "readonly", "not_licensed", "planned")
MODULE_STATE_LABELS_RU = {
    "active": "Доступен",
    "grace": "Льготный период",
    "readonly": "Только чтение (подписка истекла)",
    "not_licensed": "Не входит в лицензию",
    "planned": "В разработке",
}


# ---------------------------------------------------------------- верификаторы подписи (D-86)

Verifier = Any  # Callable[[payload_bytes: bytes, signature: bytes, public_key_pem: bytes], bool]


def _verify_ed25519(payload: bytes, signature: bytes, public_key_pem: bytes) -> bool:
    """Встроенный верификатор: Ed25519 (cryptography). Невалидная подпись/ключ → False (не исключение)."""
    from cryptography.hazmat.primitives import serialization
    try:
        key = serialization.load_pem_public_key(public_key_pem)
        key.verify(signature, payload)
        return True
    except Exception:
        return False


_VERIFIERS: dict[str, Verifier] = {"ed25519": _verify_ed25519}


def register_verifier(alg: str, fn: Verifier) -> None:
    """Зарегистрировать верификатор алгоритма подписи (например, профиль ГОСТ через СКЗИ площадки — адаптер
    вызывает внешний провайдер и НЕ входит в поставку). Переопределение встроенного ed25519 запрещено."""
    if alg == "ed25519" or not alg or len(alg) > 64:
        raise LicenseError("bad_schema", f"Недопустимый идентификатор алгоритма: {alg!r}")
    _VERIFIERS[alg] = fn


@dataclass(frozen=True)
class License:
    """Разобранная и проверенная подписью лицензия (содержимое payload)."""
    license_id: str
    customer: str
    issued_at: date
    kind: str                                   # subscription | perpetual
    valid_until: Optional[date]                 # обязателен для подписки; у бессрочной None
    support_until: Optional[date]               # обновления и поддержка — до этой даты (у «без поддержки» = None)
    modules: frozenset[str]                     # платные модули; ядро всегда доступно
    seats: Optional[int]                        # именных мест; None — без ограничения (площадка)
    grace_days: int = DEFAULT_GRACE_DAYS
    key_id: str = "dev"
    alg: str = "ed25519"                          # алгоритм подписи (D-86); профиль ГОСТ — через register_verifier


def _parse_date(value: Any, field_name: str) -> date:
    """Дата ГГГГ-ММ-ДД из лицензии; любая другая форма — ошибка схемы."""
    if not isinstance(value, str):
        raise LicenseError("bad_schema", f"Поле лицензии «{field_name}» должно быть датой ГГГГ-ММ-ДД.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise LicenseError("bad_schema", f"Поле лицензии «{field_name}»: не дата «{value}».") from None


def _opt_date(payload: dict, field_name: str) -> Optional[date]:
    v = payload.get(field_name)
    return None if v is None else _parse_date(v, field_name)


def _req_str(payload: dict, field_name: str, limit: int = 300) -> str:
    v = payload.get(field_name)
    if not isinstance(v, str) or not v.strip() or len(v) > limit:
        raise LicenseError("bad_schema", f"Поле лицензии «{field_name}» обязательно и не длиннее {limit} знаков.")
    return v.strip()


def parse_license_payload(payload: Any) -> License:
    """Проверка СОДЕРЖИМОГО лицензии (после проверки подписи). Неизвестные поля игнорируются — старые клиенты
    не ломаются о новые поля; неизвестные модули — тоже игнорируются (лицензия «на будущее» не опасна)."""
    if not isinstance(payload, dict):
        raise LicenseError("bad_schema", "Содержимое лицензии — не объект.")
    kind = payload.get("kind")
    if kind not in LICENSE_KINDS:
        raise LicenseError("bad_schema", "Вид лицензии должен быть subscription или perpetual.",
                           {"field": "kind"})
    valid_until = _opt_date(payload, "valid_until")
    if kind == "subscription" and valid_until is None:
        raise LicenseError("bad_schema", "У подписки должна быть дата окончания (valid_until).",
                           {"field": "valid_until"})
    raw_modules = payload.get("modules")
    if not isinstance(raw_modules, list) or not all(isinstance(m, str) for m in raw_modules):
        raise LicenseError("bad_schema", "Поле «modules» — список идентификаторов модулей.")
    seats = payload.get("seats")
    if seats is not None and (not isinstance(seats, int) or isinstance(seats, bool) or seats < 1):
        raise LicenseError("bad_schema", "Поле «seats» — целое число мест от 1.", {"field": "seats"})
    grace = payload.get("grace_days", DEFAULT_GRACE_DAYS)
    if not isinstance(grace, int) or isinstance(grace, bool) or not 0 <= grace <= 90:
        raise LicenseError("bad_schema", "Поле «grace_days» — целое от 0 до 90.", {"field": "grace_days"})
    support_until = _opt_date(payload, "support_until")
    return License(
        license_id=_req_str(payload, "license_id", 64),
        customer=_req_str(payload, "customer", 300),
        issued_at=_parse_date(payload.get("issued_at"), "issued_at"),
        kind=kind, valid_until=valid_until, support_until=support_until,
        modules=frozenset(m for m in raw_modules if m in MODULE_BY_ID),
        seats=seats, grace_days=grace,
        key_id=_req_str(payload, "key_id", 32) if payload.get("key_id") is not None else "dev",
        alg=_req_str(payload, "alg", 64) if payload.get("alg") is not None else "ed25519",
    )


def canonical_payload(payload: dict) -> bytes:
    """Каноническая форма содержимого лицензии — то, что реально подписано."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def load_license(text: str, public_key: Any) -> License:
    """Разбор файла лицензии: JSON → схема (узнать alg) → подпись верификатором алгоритма → объект. Без сети.

    public_key — PEM публичного ключа вендора (bytes/str) или готовый ключ Ed25519 (обратная совместимость
    со стендом). Верификатор получает PEM: профиль ГОСТ решает СКЗИ площадки, а не наш код."""
    try:
        doc = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        raise LicenseError("bad_json", "Файл лицензии не читается (нужен JSON в UTF-8).") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("payload"), dict):
        raise LicenseError("bad_schema", "В файле лицензии должен быть объект «payload».")
    sig_raw = doc.get("signature")
    if not isinstance(sig_raw, str):
        raise LicenseError("bad_schema", "В файле лицензии должна быть строка «signature».")
    try:
        signature = base64.b64decode(sig_raw, validate=True)
    except (binascii.Error, ValueError):
        raise LicenseError("bad_schema", "Подпись лицензии — не base64.") from None
    lic = parse_license_payload(doc["payload"])
    verifier = _VERIFIERS.get(lic.alg)
    if verifier is None:
        raise LicenseError("unsupported_alg",
                           f"Алгоритм подписи лицензии «{lic.alg}» этой установкой не поддерживается. "
                           "Для профиля ГОСТ нужен сертифицированный СКЗИ — обратитесь к поставщику.")
    if isinstance(public_key, str):
        public_key = public_key.encode("utf-8")
    if not isinstance(public_key, bytes):
        # Обратная совместимость: готовый объект ключа Ed25519 → PEM.
        from cryptography.hazmat.primitives import serialization
        public_key = public_key.public_bytes(serialization.Encoding.PEM,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)
    if not verifier(canonical_payload(doc["payload"]), signature, public_key):
        raise LicenseError("bad_signature",
                           "Подпись лицензии не прошла проверку — файл изменён или выдан не вендором.")
    return lic


# ---------------------------------------------------------------- состояние

@dataclass(frozen=True)
class ModuleState:
    """Состояние одного модуля на дату: state из MODULE_STATES + пояснения для интерфейса."""
    module_id: str
    state: str
    label_ru: str
    detail_ru: str = ""


@dataclass(frozen=True)
class LicenseStatus:
    """Итог проверки: режим установки, состояние модулей, поддержка. None-лицензия = демо (всё доступно)."""
    mode: str                                   # licensed | demo
    license: Optional[License]
    modules: dict[str, ModuleState] = field(default_factory=dict)
    support_label_ru: str = ""
    customer: str = ""


def evaluate(lic: Optional[License], today: date) -> LicenseStatus:
    """Состояние модулей на дату. Правила:

    * нет лицензии — режим демо/разработки: все выпущенные модули доступны (пилот и стенд не требуют файла);
    * модуль из лицензии: бессрочная — всегда active; подписка — active до valid_until, затем льготный период
      (grace, всё работает, виден срок), затем readonly — читать/печатать/выгружать можно, изменять нельзя;
    * модуль не из лицензии — not_licensed; модуль из плана экосистемы — planned (его ещё нет в продукте).
    """
    def module_state(m: Module) -> ModuleState:
        if m.stage == "planned":
            return ModuleState(m.id, "planned", MODULE_STATE_LABELS_RU["planned"], m.note_ru)
        if lic is None:
            return ModuleState(m.id, "active", "Доступен", "Режим без лицензии (демо/стенд).")
        if m.id not in lic.modules:
            return ModuleState(m.id, "not_licensed", MODULE_STATE_LABELS_RU["not_licensed"])
        if lic.kind == "perpetual":
            return ModuleState(m.id, "active", MODULE_STATE_LABELS_RU["active"])
        vu = lic.valid_until
        if vu is None or today <= vu:          # None у подписки не бывает (parse_license_payload), не падаем
            return ModuleState(m.id, "active", MODULE_STATE_LABELS_RU["active"])
        grace_end = vu + timedelta(days=lic.grace_days)
        if today <= grace_end:
            return ModuleState(m.id, "grace", MODULE_STATE_LABELS_RU["grace"],
                               f"Подписка истекла {vu.isoformat()}; до {grace_end} модуль работает, "
                               "затем — режим «только чтение». Данные сохраняются в любом случае.")
        return ModuleState(m.id, "readonly", MODULE_STATE_LABELS_RU["readonly"],
                           "Смотреть, печатать и выгружать можно всегда; изменения — после продления подписки.")

    states = {m.id: module_state(m) for m in MODULES}
    if lic is None:
        return LicenseStatus("demo", None, states, "Без файла лицензии — демо/стенд; поддержка по договорённости.")
    if lic.support_until is None:
        support = "Поддержка не включена"
    elif today <= lic.support_until:
        support = f"Поддержка до {lic.support_until.isoformat()}"
    else:
        support = f"Поддержка истекла {lic.support_until.isoformat()} (обновления — после продления)"
    return LicenseStatus("licensed", lic, states, support, lic.customer)


def license_block(status: LicenseStatus) -> dict:
    """Статус лицензии для передачи клиенту (pull): режим, заказчик, вид, сроки, состояние каждого модуля.
    Клиент прячет разделы недоступных модулей и показывает блок «О программе»; ворота — на сервере."""
    lic = status.license
    return {
        "mode": status.mode,
        "customer": status.customer,
        "support_label_ru": status.support_label_ru,
        "kind": lic.kind if lic else "",
        "kind_label_ru": KIND_LABELS_RU[lic.kind] if lic else "",
        "valid_until": lic.valid_until.isoformat() if lic and lic.valid_until else "",
        "license_id": lic.license_id if lic else "",
        "modules": [{"id": st.module_id, "title_ru": MODULE_BY_ID[st.module_id].title_ru,
                     "state": st.state, "label_ru": st.label_ru, "detail_ru": st.detail_ru}
                    for st in (status.modules[m.id] for m in MODULES)],
    }
