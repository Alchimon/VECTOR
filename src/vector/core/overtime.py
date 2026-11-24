"""ТК РФ-контур сверхурочной работы и работы в выходные/праздники (D-74, blueprint 51).

Что добавлено к базовому учёту заявок (D-52/D-54/D-55):

* **Основания** — закрытый справочник по статьям 99 и 113 ТК РФ. Свободного основания нет: каждая заявка
  ссылается на код справочника; статья указана в подписи, чтобы решение руководителя было осознанным.
* **Кадровые категории** — часть работников привлекать НЕЛЬЗЯ всегда (ст. 99 ч. 4; ст. 259, 268), часть — только
  с письменного согласия и после разъяснения права отказаться (ст. 99 ч. 5; ст. 113 ч. 7). Категории назначает
  администратор (`hr.category_set`); перечень зафиксирован в коде и меняется только версией программы —
  состав подтверждает кадровая служба (blueprint 51, «Неопределённости перед production»).
* **Лимит «4 часа за два дня подряд»** (ст. 99 ч. 6) — дополняет суточный и годовой лимиты D-55.

Согласие и разъяснение права отказа — внешние документы (бумага), программа фиксирует факт их получения
(отметки заявителя/руководителя) и не подменяет их. Запрещающие категории проверяются при подаче заявки и
повторно при решении руководителя — изменение категории после подачи не может «протащить» запрещённую заявку.
Это не юридическая консультация: применимую редакцию ТК РФ и локальный порядок подтверждает работодатель.
"""
from __future__ import annotations

from typing import Iterable, Optional

from vector.core.lifecycle import LifecycleError


class OvertimeError(LifecycleError):
    """Нарушение ТК-контура сверхурочных: code — машинный, message_ru — для пользователя."""


# ---------------------------------------------------------------- основания (ст. 99, 113 ТК РФ)
# consent=True — основание допускает привлечение только с письменного согласия работника;
# consent=False — чрезвычайные основания части 3, согласие по закону не требуется.
GROUNDS: tuple[dict, ...] = (
    {"code": "finish_urgent", "kinds": ("overtime",), "consent": True,
     "label_ru": "Закончить начатую работу: её незавершение грозит ущербом или жизни людям",
     "article": "ст. 99 ч. 2 п. 1 ТК РФ"},
    {"code": "repair_mechanisms", "kinds": ("overtime",), "consent": True,
     "label_ru": "Ремонт/восстановление механизмов или сооружений: неисправность останавливает работу многих",
     "article": "ст. 99 ч. 2 п. 2 ТК РФ"},
    {"code": "continuous_no_show", "kinds": ("overtime",), "consent": True,
     "label_ru": "Неявка сменщика при непрерывной работе (до прибытия замены)",
     "article": "ст. 99 ч. 2 п. 3 ТК РФ"},
    {"code": "consent_employee", "kinds": ("weekend", "holiday"), "consent": True,
     "label_ru": "Работа в выходной или праздник по согласию работника",
     "article": "ст. 113 ч. 2 ТК РФ"},
    {"code": "disaster_prevention", "kinds": ("overtime", "weekend", "holiday"), "consent": False,
     "label_ru": "Предотвращение катастрофы/аварии или устранение их последствий",
     "article": "ст. 99 ч. 3 п. 1, ст. 113 ч. 3 п. 1 ТК РФ"},
    {"code": "public_infra", "kinds": ("overtime", "weekend", "holiday"), "consent": False,
     "label_ru": "Устранение сбоев водо-, тепло-, электроснабжения, транспорта или связи",
     "article": "ст. 99 ч. 3 п. 2, ст. 113 ч. 3 п. 2 ТК РФ"},
    {"code": "emergency_state", "kinds": ("overtime", "weekend", "holiday"), "consent": False,
     "label_ru": "Чрезвычайное/военное положение, угроза бедствия",
     "article": "ст. 99 ч. 3 п. 3, ст. 113 ч. 3 п. 3 ТК РФ"},
)
GROUNDS_BY_CODE = {g["code"]: g for g in GROUNDS}


# ---------------------------------------------------------------- кадровые категории (ст. 99 ч. 4–5, 113 ч. 7, 259, 268)
# rule: "forbidden" — привлекать нельзя никогда; "consent" — только с письменного согласия работника
# и после разъяснения права отказаться (и при отсутствии медицинских противопоказаний — внешний документ).
CATEGORIES: tuple[dict, ...] = (
    {"code": "minor", "label_ru": "Несовершеннолетний работник", "rule": "forbidden",
     "article": "ст. 99 ч. 4, ст. 268 ТК РФ"},
    {"code": "pregnant", "label_ru": "Беременная работница", "rule": "forbidden",
     "article": "ст. 99 ч. 4, ст. 259 ТК РФ"},
    {"code": "disabled", "label_ru": "Работник-инвалид", "rule": "consent",
     "article": "ст. 99 ч. 5, ст. 113 ч. 7 ТК РФ"},
    {"code": "parent_child_under3", "label_ru": "Работник с ребёнком до трёх лет", "rule": "consent",
     "article": "ст. 99 ч. 5, ст. 113 ч. 7, ст. 259 ТК РФ"},
)
CATEGORIES_BY_CODE = {c["code"]: c for c in CATEGORIES}

# Лимиты (ст. 99 ч. 6): не более 4 часов в течение двух дней подряд и 120 часов в год.
DAY_LIMIT_MIN = 4 * 60
TWO_DAY_LIMIT_MIN = 4 * 60
YEAR_LIMIT_MIN = 120 * 60


def parse_categories(raw) -> list[str]:
    """Список кодов категорий из JSON-снимка/команды: уникальные, известные, в порядке справочника."""
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)) or any(not isinstance(x, str) for x in raw):
        raise OvertimeError("invalid_categories", "Кадровые категории — список кодов справочника.",
                            {"field": "categories"})
    unknown = [x for x in raw if x not in CATEGORIES_BY_CODE]
    if unknown:
        raise OvertimeError("unknown_category", "Неизвестная кадровая категория; список меняется только версией программы.",
                            {"field": "categories", "unknown": sorted(set(unknown))})
    order = {c["code"]: i for i, c in enumerate(CATEGORIES)}
    return sorted(set(raw), key=order.get)


def validate_request(kind: str, ground_code: Optional[str], category_codes: Iterable[str],
                     consent_confirmed: bool, refusal_right_notified: bool) -> None:
    """Проверить заявку по ТК-контуру. kind — overtime/weekend/holiday; категории — заявителя.

    Порядок правил: основание → запрещающие категории → согласия. Каждое нарушение — OvertimeError с кодом;
    вызывающая сторона (сервер) превращает его в отказ с пояснением пользователю.
    """
    ground = GROUNDS_BY_CODE.get(ground_code or "")
    if ground is None:
        raise OvertimeError("ground_required", "Выберите основание привлечения из справочника (статьи 99/113 ТК РФ).",
                            {"field": "ground"})
    if kind not in ground["kinds"]:
        raise OvertimeError("ground_kind_mismatch", "Это основание не подходит для выбранного вида работы.",
                            {"field": "ground", "ground": ground["code"], "kind": kind})
    cats = parse_categories(list(category_codes))
    for code in cats:
        cat = CATEGORIES_BY_CODE[code]
        if cat["rule"] == "forbidden":
            raise OvertimeError("category_forbidden",
                                f"Категория «{cat['label_ru']}»: привлечение запрещено ({cat['article']}).",
                                {"category": code})
    needs_consent = bool(ground["consent"]) or any(CATEGORIES_BY_CODE[c]["rule"] == "consent" for c in cats)
    if needs_consent and not consent_confirmed:
        raise OvertimeError("consent_required",
                            "Нужно письменное согласие работника — отметьте, что оно получено.",
                            {"field": "consent_confirmed", "ground": ground["code"]})
    needs_notice = any(CATEGORIES_BY_CODE[c]["rule"] == "consent" for c in cats)
    if needs_notice and not refusal_right_notified:
        raise OvertimeError("refusal_notice_required",
                            "Работнику должно быть разъяснено право отказаться от привлечения — отметьте это.",
                            {"field": "refusal_right_notified"})


def requirements(kind: str, ground_code: Optional[str], category_codes: Iterable[str]) -> dict:
    """Что форма должна потребовать для выбранного вида/основания/категорий (для клиента и тестов)."""
    ground = GROUNDS_BY_CODE.get(ground_code or "")
    cats = parse_categories(list(category_codes))
    forbidden = [c for c in cats if CATEGORIES_BY_CODE[c]["rule"] == "forbidden"]
    consent_cats = [c for c in cats if CATEGORIES_BY_CODE[c]["rule"] == "consent"]
    return {"ground_known": ground is not None,
            "ground_allowed": bool(ground) and kind in ground["kinds"],
            "forbidden": forbidden,
            "consent_required": bool(ground and ground["consent"]) or bool(consent_cats),
            "refusal_notice_required": bool(consent_cats)}


def grounds_for(kind: str) -> list[dict]:
    """Основания, допустимые для вида работы, — для формы заявки."""
    return [dict(g) for g in GROUNDS if kind in g["kinds"]]


def category_labels(codes: Iterable[str]) -> list[str]:
    """Подписи категорий для интерфейса; неизвестные коды не показываются (устаревший снимок)."""
    if not isinstance(codes, (list, tuple)):
        return []
    return [CATEGORIES_BY_CODE[c]["label_ru"] for c in codes if c in CATEGORIES_BY_CODE]
