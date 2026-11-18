"""Рекламации, CAPA, запросы и извещения об изменении — правила, общие для сервера и клиента
(этап 6, сценарий С5 шаг 10, сценарий 10 «Сервис/качество»; decision log D-39).

Сервер применяет эти правила при выполнении команды, клиент — чтобы проверить ввод до отправки и показать
черновик офлайн так же, как его потом увидит сервер (одна правда на обеих сторонах).

Термины (расшифровки — client/hints.py, GLOSSARY):

* рекламация (claim) — претензия к экземпляру изделия: от заказчика или внутренняя (обнаружили сами);
* CAPA (корректирующие и предупреждающие действия) — план по рекламации: корректирующее действие устраняет
  причину у этой рекламации, предупреждающее — не даёт ей повториться на других изделиях;
* проверка эффективности — отдельная запись: помогли ли действия. Делает её ДРУГОЙ человек — не тот, кто
  составлял план, выполнял действия или отправлял на проверку (независимый проверяющий, 03 § 4);
* ECR (запрос на изменение, engineering change request) — предложение изменить документацию или техпроцесс;
* ECO (извещение об изменении, engineering change order) — утверждённое решение: какие ТОЧНЫЕ версии затронуты
  (документ — номер версии, техпроцесс — редакция) и на какие версии они заменены.

Жизненные циклы (03 § 4):

    рекламация: registered → analysis → action → effectiveness_check → closed
                (проверка «не помогло» возвращает в action — нужны новые действия)
    ECR:        submitted → assessed → accepted | rejected
    ECO:        draft → approved → implemented → verified

Расширение D-40 (ответ владельца на A-46):

* рекламация бывает без заводского номера — на ПАРТИЮ (наряд: все экземпляры одной серии, выпущенные по нему) или
  на ПРОЕКТ в целом (CLAIM_SCOPES_RU); точная связь с экземпляром — только когда номер известен;
* классификация дефекта: тяжесть (SEVERITY_RU) указывается при регистрации, категория — откуда дефект
  (CATEGORY_RU) — когда найдена причина (при первом плане CAPA) или раньше, если уже ясно;
* срок ответа заказчику (response_due): обязателен для рекламации от заказчика; «ответ дан», когда после
  установки срока записан ответ заказчику (claim.respond); состояние срока — deadline_state;
* роль «Технический контроль (ОТК — отдел технического контроля)» ведёт рекламации наравне с РП и ГИП и проверяет
  эффективность (права — contracts/permissions.slice1.json).

Закрытие рекламации НЕ доказывает устранение причины (02 сценарий 10): оно значит лишь, что независимый
проверяющий признал выполненные действия эффективными на момент проверки. Это сказано в карточке (CLOSURE_NOTE).

Ошибки — QualityError с машинным кодом и русским сообщением (как LifecycleError в vector.core.lifecycle).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Optional

from .lifecycle import LifecycleError, parse_date

# ---------------------------------------------------------------- статусы и подписи
CLAIM_STATUS_LABELS_RU = {"registered": "Зарегистрирована", "analysis": "Анализ причины", "action": "Выполнение действий",
                          "effectiveness_check": "Проверка эффективности", "closed": "Закрыта"}
CLAIM_SOURCES_RU = {"customer": "От заказчика", "internal": "Внутренняя"}
ACTION_KINDS_RU = {"corrective": "Корректирующее", "preventive": "Предупреждающее"}
ACTION_STATUS_RU = {"open": "Не выполнено", "done": "Выполнено"}
ECR_STATUS_LABELS_RU = {"submitted": "Подан", "assessed": "Оценён", "accepted": "Принят", "rejected": "Отклонён"}
ECO_STATUS_LABELS_RU = {"draft": "Черновик", "approved": "Утверждено", "implemented": "Внедрено", "verified": "Проверено"}
ECO_ITEM_KINDS_RU = {"document": "Документ", "tech_process": "Техпроцесс"}
# D-40: охват рекламации, классификация дефекта, ответы заказчику.
CLAIM_SCOPES_RU = {"instance": "Экземпляр (заводской номер)", "batch": "Партия (наряд)", "project": "Проект (без номера изделия)"}
SEVERITY_RU = {"critical": "Критический", "major": "Значительный", "minor": "Незначительный"}
SEVERITY_HINTS_RU = {
    "critical": "Опасен для людей или оборудования либо изделие не работает — нужно немедленное решение.",
    "major": "Изделие работает с ограничениями или не выполняет часть функций.",
    "minor": "Не влияет на работу: внешний вид, маркировка, комплектность документации.",
}
CATEGORY_RU = {"design": "Конструкторский (ошибка в документации)", "manufacturing": "Производственный (изготовление, сборка)",
               "purchased": "Покупные комплектующие", "software": "Программное обеспечение",
               "logistics": "Транспортировка и хранение", "operation": "Нарушение условий эксплуатации", "other": "Другое"}
RESPONSE_KINDS_RU = {"interim": "Промежуточный ответ", "final": "Окончательный ответ"}
DEADLINE_LABELS_RU = {"none": "Срок не задан", "ok": "В срок", "soon": "Срок подходит", "today": "Срок сегодня",
                      "overdue": "Срок прошёл", "answered": "Ответ дан"}
DEADLINE_TONES = {"none": "neutral", "ok": "info", "soon": "warning", "today": "warning", "overdue": "danger", "answered": "success"}
DUE_SOON_DAYS = 2        # за сколько дней до срока ответа он считается «подходит» (напоминание)
MAX_RESPONSE_DAYS = 366  # срок ответа — не дальше года от даты получения (защита от опечатки в годе)

CLOSURE_NOTE = ("Закрытие рекламации не доказывает устранение причины: оно значит только, что независимый проверяющий "
                "признал выполненные действия эффективными на момент проверки.")

MAX_TEXT = 4000          # символов в описании, причине, предложении
MAX_TITLE = 300
MAX_ACTIONS = 50         # действий CAPA за один раз
MAX_ITEMS = 50           # затронутых версий в одном извещении


class QualityError(LifecycleError):
    """Нарушение правила рекламации, CAPA, ECR или ECO: code — машинный, message_ru — для пользователя."""


def _text(v: Any, field: str, what: str, max_len: int, required: bool = True) -> str:
    """Текст без пробелов по краям; обязательный — непустой; длина ограничена."""
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise QualityError("invalid_type", "Ожидается текст.", {"field": field})
    v = v.strip()
    if required and not v:
        raise QualityError("required", f"Укажите {what}.", {"field": field})
    if len(v) > max_len:
        raise QualityError("too_long", f"Не длиннее {max_len} символов.", {"field": field})
    return v


def _date(v: Any, field: str, what: str, required: bool = True) -> Optional[str]:
    """Дата ГГГГ-ММ-ДД (правило vector.core.lifecycle.parse_date) с ошибкой QualityError."""
    try:
        return parse_date(v, field, what, required)
    except LifecycleError as e:
        raise QualityError(e.code, e.message_ru, e.details)


def _id(v: Any, field: str) -> str:
    """Идентификатор (UUID строкой); формат проверяет сервер (validation.req_uuid), здесь — что он есть."""
    if not isinstance(v, str) or not v.strip():
        raise QualityError("required", "Не указан идентификатор.", {"field": field})
    return v.strip()


# ---------------------------------------------------------------- рекламация
def parse_claim(p: dict) -> dict:
    """Регистрация рекламации: охват (экземпляр / партия / проект) и его объект, дата получения, источник
    (заказчик / внутренняя), описание, ссылка на письмо, тяжесть дефекта, категория (если уже ясна), срок ответа
    заказчику (для рекламации от заказчика — обязателен).

    Охват по умолчанию — «экземпляр» (так регистрировались рекламации до D-40, в т. ч. команды из очереди старого
    клиента). Тяжесть обязательна: без неё нельзя понять, насколько срочно реагировать (программа её не выдумывает).
    """
    src = p.get("source")
    if src not in CLAIM_SOURCES_RU:
        raise QualityError("invalid_source", "Источник рекламации — «От заказчика» или «Внутренняя».", {"field": "source"})
    scope = p.get("scope") or "instance"
    if scope not in CLAIM_SCOPES_RU:
        raise QualityError("invalid_scope", "Охват рекламации — экземпляр, партия (наряд) или проект.", {"field": "scope"})
    need = {"instance": ("instance_id", "экземпляр изделия"), "batch": ("work_order_id", "наряд (партию)"),
            "project": ("project_id", "проект")}[scope]
    if not isinstance(p.get(need[0]), str) or not p.get(need[0]).strip():
        raise QualityError("required", f"Укажите {need[1]}.", {"field": need[0]})
    received = _date(p.get("received_date"), "received_date", "дату получения рекламации")
    cls = parse_classification(p, require_category=False)
    due = _date(p.get("response_due"), "response_due", "срок ответа заказчику", required=src == "customer")
    if due:
        check_due(received, due)
    return {"scope": scope, "received_date": received, "source": src,
            "description": _text(p.get("description"), "description", "суть рекламации (что не так)", MAX_TEXT),
            "reference": _text(p.get("reference"), "reference", "", MAX_TITLE, required=False),
            **cls, "response_due": due}


def parse_classification(p: dict, require_category: bool) -> dict:
    """Классификация дефекта: тяжесть (обязательна) и категория — откуда дефект (обязательна, когда причина
    найдена: require_category)."""
    sev = p.get("severity")
    if sev not in SEVERITY_RU:
        raise QualityError("severity_required", "Укажите тяжесть дефекта: критический, значительный или незначительный.",
                           {"field": "severity"})
    cat = p.get("category") or None
    if cat is not None and cat not in CATEGORY_RU:
        raise QualityError("invalid_category", "Категория дефекта — из списка.", {"field": "category"})
    if require_category and cat is None:
        raise QualityError("category_required", "Укажите категорию дефекта — откуда он (по найденной причине).",
                           {"field": "category"})
    return {"severity": sev, "category": cat}


def check_due(received: str, due: str) -> None:
    """Срок ответа заказчику — не раньше даты получения и не дальше MAX_RESPONSE_DAYS дней от неё."""
    r, d = date.fromisoformat(received), date.fromisoformat(due)
    if d < r:
        raise QualityError("due_before_received", "Срок ответа не может быть раньше даты получения рекламации.",
                           {"field": "response_due"})
    if (d - r).days > MAX_RESPONSE_DAYS:
        raise QualityError("due_too_far", f"Срок ответа — не дальше {MAX_RESPONSE_DAYS} дней от даты получения. "
                           "Проверьте год.", {"field": "response_due"})


def deadline_state(due: Optional[str], answered: bool, today: str) -> str:
    """Состояние срока ответа заказчику: none — срок не задан; answered — ответ после установки срока записан;
    overdue — срок прошёл; today — сегодня; soon — осталось не больше DUE_SOON_DAYS дней; ok — в срок.

    today — дата по часам сервера (клиент передаёт дату последнего обновления, а без связи — свою).
    """
    if not due:
        return "none"
    if answered:
        return "answered"
    left = (date.fromisoformat(due) - date.fromisoformat(today)).days
    if left < 0:
        return "overdue"
    if left == 0:
        return "today"
    return "soon" if left <= DUE_SOON_DAYS else "ok"


def parse_response(p: dict) -> dict:
    """Ответ заказчику: вид (промежуточный / окончательный), дата отправки, что ответили, номер письма."""
    kind = p.get("kind")
    if kind not in RESPONSE_KINDS_RU:
        raise QualityError("invalid_kind", "Вид ответа — промежуточный или окончательный.", {"field": "kind"})
    return {"kind": kind, "response_date": _date(p.get("response_date"), "response_date", "дату отправки ответа"),
            "summary": _text(p.get("summary"), "summary", "что ответили заказчику", MAX_TEXT),
            "reference": _text(p.get("reference"), "reference", "", MAX_TITLE, required=False)}


def parse_actions(items: Any) -> list[dict]:
    """План CAPA: [{id, kind, description, owner_id, due_date}] — хотя бы одно действие, id без повторов."""
    if not isinstance(items, list) or not items:
        raise QualityError("required", "Добавьте хотя бы одно действие.", {"field": "actions"})
    if len(items) > MAX_ACTIONS:
        raise QualityError("too_many", f"Не больше {MAX_ACTIONS} действий за один раз.", {"field": "actions"})
    out, problems = [], []
    for n, a in enumerate(items, 1):
        if not isinstance(a, dict) or not set(a) <= {"id", "kind", "description", "owner_id", "due_date"}:
            raise QualityError("invalid_type", "Действие — {id, kind, description, owner_id, due_date}.", {"field": "actions"})
        try:
            if a.get("kind") not in ACTION_KINDS_RU:
                raise QualityError("invalid_kind", "Вид — корректирующее или предупреждающее.", {"field": "kind"})
            out.append({"id": _id(a.get("id"), "id"), "kind": a["kind"],
                        "description": _text(a.get("description"), "description", "что сделать", 2000),
                        "owner_id": _id(a.get("owner_id"), "owner_id"),
                        "due_date": _date(a.get("due_date"), "due_date", "срок")})
        except QualityError as e:
            problems.append({"row": n, "field": e.details.get("field"), "message_ru": e.message_ru})
    if problems:
        raise QualityError("actions_invalid", f"В плане действий ошибки: {len(problems)}. Исправьте отмеченные строки.",
                           {"field": "actions", "problems": problems})
    if len({a["id"] for a in out}) != len(out):
        raise QualityError("id_duplicate", "Идентификаторы действий повторяются.", {"field": "actions"})
    return out


def has_corrective(actions: Iterable[dict]) -> bool:
    """Есть ли в плане корректирующее действие (без него причина не устраняется — план не принимается)."""
    return any(a.get("kind") == "corrective" for a in actions)


def checker_conflicts(checker_id: str, *, planners: Iterable[str], doers: Iterable[str], submitter: Optional[str]) -> list[str]:
    """Почему этот человек НЕ может проверять эффективность (пусто — может): он составлял план, выполнял действия
    или отправлял на проверку. Независимость проверяющего — 03 § 4 («отдельная запись, независимый проверяющий»).
    """
    out = []
    if checker_id in set(planners):
        out.append("вы составляли план действий")
    if checker_id in set(doers):
        out.append("вы выполняли действия")
    if submitter and checker_id == submitter:
        out.append("вы отправили рекламацию на проверку")
    return out


def parse_check(p: dict) -> dict:
    """Проверка эффективности: помогло ли (да / нет) и на чём основан вывод (обязательно)."""
    eff = p.get("effective")
    if not isinstance(eff, bool):
        raise QualityError("required", "Укажите вывод: действия эффективны или нет.", {"field": "effective"})
    return {"effective": eff, "evidence": _text(p.get("evidence"), "evidence",
                                                "на чём основан вывод (осмотр, испытание, отзыв заказчика)", MAX_TEXT)}


# ---------------------------------------------------------------- ECR
def parse_ecr(p: dict) -> dict:
    """Запрос на изменение: заголовок, причина (почему менять), предложение (что поменять)."""
    return {"title": _text(p.get("title"), "title", "заголовок запроса", MAX_TITLE),
            "reason": _text(p.get("reason"), "reason", "причину изменения", MAX_TEXT),
            "proposal": _text(p.get("proposal"), "proposal", "что предлагается изменить", MAX_TEXT)}


# ---------------------------------------------------------------- ECO
def parse_eco_items(items: Any) -> list[dict]:
    """Затронутые версии извещения: документ — {kind:"document", document_id, from_version}; техпроцесс —
    {kind:"tech_process", tp_id} (редакция определяется ТП). Хотя бы одна строка, без повторов.
    """
    if not isinstance(items, list) or not items:
        raise QualityError("required", "Укажите хотя бы одну затронутую версию (документ или техпроцесс).", {"field": "items"})
    if len(items) > MAX_ITEMS:
        raise QualityError("too_many", f"Не больше {MAX_ITEMS} строк.", {"field": "items"})
    out = []
    for it in items:
        if not isinstance(it, dict):
            raise QualityError("invalid_type", "Строка извещения — объект.", {"field": "items"})
        kind = it.get("kind")
        if kind == "document":
            if set(it) - {"kind", "document_id", "from_version"}:
                raise QualityError("invalid_type", "Документ — {kind, document_id, from_version}.", {"field": "items"})
            v = it.get("from_version")
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise QualityError("invalid_version", "Версия документа — целое число от 1.", {"field": "items"})
            out.append({"kind": kind, "document_id": _id(it.get("document_id"), "items"), "from_version": v})
        elif kind == "tech_process":
            if set(it) - {"kind", "tp_id"}:
                raise QualityError("invalid_type", "Техпроцесс — {kind, tp_id}.", {"field": "items"})
            out.append({"kind": kind, "tp_id": _id(it.get("tp_id"), "items")})
        else:
            raise QualityError("invalid_kind", "Строка извещения — документ или техпроцесс.", {"field": "items"})
    keys = [item_key(x) for x in out]
    if len(set(keys)) != len(keys):
        raise QualityError("item_duplicate", "Одна и та же версия указана дважды.", {"field": "items"})
    return out


def item_key(it: dict) -> tuple:
    """Ключ строки извещения для поиска повторов: (вид, объект)."""
    return (it["kind"], it.get("document_id") or it.get("tp_id"))


def parse_eco_results(items: list[dict], results: Any) -> list[dict]:
    """Итог внедрения: для КАЖДОЙ строки извещения — новая версия: документ — {index, to_version} (больше прежней),
    техпроцесс — {index, to_tp_id}. Существование и «новее» проверяет сервер по данным.
    """
    if not isinstance(results, list) or len(results) != len(items):
        raise QualityError("results_incomplete", "Укажите новую версию для каждой строки извещения.", {"field": "results"})
    out: dict[int, dict] = {}
    for r in results:
        if not isinstance(r, dict) or not isinstance(r.get("index"), int) or not 0 <= r["index"] < len(items):
            raise QualityError("invalid_type", "Итог — {index, to_version | to_tp_id}.", {"field": "results"})
        it = items[r["index"]]
        if it["kind"] == "document":
            v = r.get("to_version")
            if set(r) - {"index", "to_version"} or not isinstance(v, int) or isinstance(v, bool):
                raise QualityError("invalid_version", "Новая версия документа — целое число.", {"field": "results"})
            if v <= it["from_version"]:
                raise QualityError("version_not_newer", f"Новая версия документа должна быть больше {it['from_version']}.",
                                   {"field": "results", "index": r["index"]})
            out[r["index"]] = {"index": r["index"], "to_version": v}
        else:
            if set(r) - {"index", "to_tp_id"}:
                raise QualityError("invalid_type", "Новая редакция техпроцесса — {index, to_tp_id}.", {"field": "results"})
            out[r["index"]] = {"index": r["index"], "to_tp_id": _id(r.get("to_tp_id"), "results")}
    if len(out) != len(items):
        raise QualityError("results_incomplete", "Укажите новую версию для каждой строки извещения.", {"field": "results"})
    return [out[i] for i in range(len(items))]
