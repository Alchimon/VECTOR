"""Экземпляры изделий, паспорт, передача заказчику, сервис и замены — правила, общие для сервера и клиента
(этап 6, сценарий С5 шаги 7–9; decision log D-38).

Сервер применяет эти правила при выполнении команды, клиент — чтобы проверить ввод до отправки и показать
черновик паспорта офлайн так же, как его потом увидит сервер (одна правда на обеих сторонах).

Что здесь:

* экземпляр изделия — изделие (комплект проекта) с заводским номером; номер уникален в области
  «изделие + серия», а не глобально (`parse_serials`);
* шаблон-мастер паспорта — версия шаблона с разделами (`PASSPORT_TEMPLATES`, `template_hash`). Паспорт хранит
  код, версию и SHA-256 шаблона, по которому он составлен: новая версия шаблона не меняет уже составленные паспорта;
* черновик паспорта — текстовые разделы, испытания, документы с ТОЧНЫМИ версиями (`parse_passport`,
  `passport_hash`);
* сервисные события и замены в эксплуатации (`parse_service`, `parse_replacement`) — неизменяемая история;
* черновой экспорт паспорта — HTML с пометкой «Черновик, не сертификат» на каждой странице (`render_draft_html`).
  Черновой экспорт — НЕ сертификат, НЕ свидетельство о приёмке и НЕ подписанный документ (02 С5 шаг 9).

Ошибки — LifecycleError с машинным кодом и русским сообщением; problems — список замечаний по полям (форма
показывает их у полей), conflict=True — «состояние уже другое» (сервер вернёт видимый конфликт).
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date
from typing import Any, Iterable, Optional

from . import money

# ---------------------------------------------------------------- статусы и подписи
INSTANCE_STATUS_LABELS_RU = {"registered": "Изготовлен", "handed_over": "Передан заказчику"}
EVENT_KIND_LABELS_RU = {"handover": "Передача заказчику", "service": "Сервисное событие", "replacement": "Замена"}
# Виды сервисных событий (история эксплуатации). «Отказ» — факт неисправности; разбор причины — рекламация (шаг 10).
SERVICE_KINDS_RU = {"commissioning": "Ввод в эксплуатацию", "inspection": "Осмотр", "maintenance": "Техническое обслуживание",
                    "repair": "Ремонт", "failure": "Отказ", "other": "Прочее"}
TEST_RESULTS_RU = {"pass": "Соответствует", "fail": "Не соответствует"}
PASSPORT_STATUS_LABELS_RU = {"draft": "Черновик"}
# Пометка чернового экспорта (04-permissions: «Черновик, не сертификат»).
DRAFT_LABEL = "Черновик, не сертификат"

MAX_SERIALS = 500        # заводских номеров за одну регистрацию
MAX_FIELD = 4000         # символов в текстовом разделе паспорта
MAX_TESTS = 100
MAX_DOCS = 100
# Заводской номер и серия: буквы, цифры, пробел и знаки - . / _ (без управляющих символов и кавычек — номер
# печатается на шильдике и в паспорте).
_SERIAL_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё \-./_]*$")


class LifecycleError(Exception):
    """Нарушение правила экземпляра или паспорта: code — машинный, message_ru — для пользователя."""
    def __init__(self, code: str, message_ru: str, details: Optional[dict] = None, conflict: bool = False):
        super().__init__(message_ru)
        self.code, self.message_ru, self.details, self.conflict = code, message_ru, details or {}, conflict


def canon_hash(obj: Any) -> str:
    """SHA-256 канонического JSON (ключи по алфавиту, без пробелов, UTF-8) — как у остальных хэшей ВЕКТОР."""
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- шаблон-мастер паспорта
# Разделы — по образцу паспорта изделия из эксплуатационных документов ЕСКД (ГОСТ 2.610); точный состав и
# формулировки утверждает владелец (A-46). kind: auto — раздел заполняет программа из точных связей
# (проект, наряд, состав, история); text — текст вводит составитель; tests / documents — списки.
PASSPORT_TEMPLATES: dict[tuple[str, int], dict] = {
    ("passport", 1): {
        "code": "passport", "version": 1, "title": "Паспорт изделия",
        "sections": [
            {"key": "general", "title": "Основные сведения об изделии", "kind": "auto",
             "hint": "Наименование, проект, наряд, заводской номер, серия, дата изготовления — из данных программы"},
            {"key": "tech_data", "title": "Основные технические данные", "kind": "text",
             "hint": "Характеристики изделия: питание, мощность, степень защиты, габариты"},
            {"key": "composition", "title": "Комплектность", "kind": "auto",
             "hint": "Состав одного изделия по снимку наряда с заменами и заменами в эксплуатации"},
            {"key": "resources", "title": "Ресурсы, сроки службы и хранения, гарантии изготовителя", "kind": "text",
             "hint": "Гарантийный срок и условия — так, как их утвердила компания; программа ничего не подставляет"},
            {"key": "preservation", "title": "Консервация", "kind": "text", "hint": "Дата и способ консервации, если была"},
            {"key": "packing", "title": "Свидетельство об упаковывании", "kind": "text",
             "hint": "Кто и когда упаковал изделие"},
            {"key": "acceptance", "title": "Свидетельство о приёмке", "kind": "text",
             "hint": "Текст свидетельства. В черновом экспорте он остаётся черновиком: подписи и печати программа не ставит"},
            {"key": "tests", "title": "Испытания", "kind": "tests",
             "hint": "Какие испытания проведены, результат, дата, протокол"},
            {"key": "documents", "title": "Документы", "kind": "documents",
             "hint": "Документы проекта с точными версиями (номер версии и SHA-256 файла)"},
            {"key": "movement", "title": "Движение изделия при эксплуатации", "kind": "auto",
             "hint": "Передача заказчику, сервисные события и замены — из истории экземпляра"},
            {"key": "notes", "title": "Заметки по эксплуатации и хранению", "kind": "text", "hint": "Необязательно"},
            {"key": "disposal", "title": "Сведения об утилизации", "kind": "text", "hint": "Необязательно"},
            {"key": "special", "title": "Особые отметки", "kind": "text", "hint": "Необязательно"},
        ]},
}
CURRENT_TEMPLATE = ("passport", 1)


def template(code: str, version: int) -> dict:
    """Шаблон паспорта по коду и версии (неизвестный — LifecycleError)."""
    t = PASSPORT_TEMPLATES.get((code, version))
    if t is None:
        raise LifecycleError("template_unknown", f"Шаблон паспорта «{code}» версии {version} неизвестен этой программе.")
    return t


def template_hash(t: dict) -> str:
    """SHA-256 шаблона — паспорт хранит его, чтобы было видно, по какому именно шаблону он составлен."""
    return canon_hash(t)


def text_keys(t: dict) -> list[str]:
    """Ключи текстовых разделов шаблона (их заполняет составитель)."""
    return [s["key"] for s in t["sections"] if s["kind"] == "text"]


# ---------------------------------------------------------------- заводские номера
def clean_code(v: Any, field: str, what: str, max_len: int) -> str:
    """Серия или заводской номер: строка, обрезка пробелов по краям, допустимые символы, длина."""
    if not isinstance(v, str) or not v.strip():
        raise LifecycleError("required", f"Укажите {what}.", {"field": field})
    s = " ".join(v.split())
    if len(s) > max_len:
        raise LifecycleError("too_long", f"{what[:1].upper() + what[1:]}: не длиннее {max_len} символов.", {"field": field})
    if not _SERIAL_RE.match(s):
        raise LifecycleError("bad_chars", f"{what[:1].upper() + what[1:]} «{s}»: допустимы буквы, цифры, пробел и знаки - . / _",
                             {"field": field})
    return s


def parse_serials(series: Any, serials: Any) -> tuple[str, list[str]]:
    """Серия и список заводских номеров для регистрации экземпляров.

    serials — список строк или текст (номер на строке). Пустые строки пропускаются. Повтор номера в одном запросе —
    ошибка с перечнем (уникальность в области «изделие + серия» проверяет ещё и база данных).
    """
    s = clean_code(series, "series", "серию", 40)
    if isinstance(serials, str):
        serials = serials.splitlines()
    if not isinstance(serials, list):
        raise LifecycleError("invalid_type", "Заводские номера — список.", {"field": "serials"})
    out = [clean_code(x, "serials", "заводской номер", 60) for x in serials if not (isinstance(x, str) and not x.strip())]
    if not out:
        raise LifecycleError("required", "Укажите хотя бы один заводской номер.", {"field": "serials"})
    if len(out) > MAX_SERIALS:
        raise LifecycleError("too_many", f"За один раз — не больше {MAX_SERIALS} номеров.", {"field": "serials"})
    seen, dup = set(), []
    for x in out:
        key = x.casefold()
        if key in seen and x not in dup:
            dup.append(x)
        seen.add(key)
    if dup:
        raise LifecycleError("serial_duplicate", "Номера повторяются в списке: " + ", ".join(dup) + ".",
                             {"field": "serials", "serials": dup})
    return s, out


def serial_label(series: str, serial_no: str) -> str:
    """Подпись экземпляра: «№ 001 (серия 2026)»."""
    return f"№ {serial_no} (серия {series})"


# ---------------------------------------------------------------- черновик паспорта
def _opt_text(v: Any, field: str, max_len: int) -> str:
    """Необязательный текст: None → «», строка — без пробелов по краям, длина ограничена."""
    if v is None:
        return ""
    if not isinstance(v, str):
        raise LifecycleError("invalid_type", "Ожидается текст.", {"field": field})
    v = v.strip()
    if len(v) > max_len:
        raise LifecycleError("too_long", f"Не длиннее {max_len} символов.", {"field": field})
    return v


def parse_date(v: Any, field: str, what: str, required: bool = True) -> Optional[str]:
    """Дата ГГГГ-ММ-ДД → та же строка (проверенная). Пусто и не обязательно — None."""
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise LifecycleError("required", f"Укажите {what} (ГГГГ-ММ-ДД).", {"field": field})
        return None
    if not isinstance(v, str):
        raise LifecycleError("invalid_date", f"{what[:1].upper() + what[1:]}: формат ГГГГ-ММ-ДД.", {"field": field})
    try:
        return date.fromisoformat(v.strip()).isoformat()
    except ValueError:
        raise LifecycleError("invalid_date", f"{what[:1].upper() + what[1:]}: формат ГГГГ-ММ-ДД.", {"field": field})


def parse_fields(t: dict, fields: Any) -> dict[str, str]:
    """Текстовые разделы паспорта: только ключи шаблона с kind=text; отсутствующие — пустые строки."""
    if fields is None:
        fields = {}
    if not isinstance(fields, dict):
        raise LifecycleError("invalid_type", "Разделы паспорта — объект {раздел: текст}.", {"field": "fields"})
    keys = text_keys(t)
    extra = sorted(set(fields) - set(keys))
    if extra:
        raise LifecycleError("unknown_section", "В шаблоне паспорта нет разделов: " + ", ".join(extra) + ".",
                             {"field": "fields", "sections": extra})
    return {k: _opt_text(fields.get(k), f"fields.{k}", MAX_FIELD) for k in keys}


def parse_tests(tests: Any) -> list[dict]:
    """Испытания: [{name, result: pass|fail, date, protocol?, note?}] — все замечания сразу (problems)."""
    if tests is None:
        return []
    if not isinstance(tests, list):
        raise LifecycleError("invalid_type", "Испытания — список.", {"field": "tests"})
    if len(tests) > MAX_TESTS:
        raise LifecycleError("too_many", f"Не больше {MAX_TESTS} испытаний.", {"field": "tests"})
    out, problems = [], []
    for i, x in enumerate(tests):
        if not isinstance(x, dict):
            problems.append({"line": i, "message_ru": "Строка испытания — объект."})
            continue
        try:
            name = _opt_text(x.get("name"), "name", 300)
            if not name:
                raise LifecycleError("required", "Укажите испытание.")
            res = x.get("result")
            if res not in TEST_RESULTS_RU:
                raise LifecycleError("invalid_result", "Результат — «Соответствует» или «Не соответствует».")
            d = parse_date(x.get("date"), "date", "дату испытания")
            out.append({"name": name, "result": res, "date": d, "protocol": _opt_text(x.get("protocol"), "protocol", 200),
                        "note": _opt_text(x.get("note"), "note", 1000)})
        except LifecycleError as e:
            problems.append({"line": i, "message_ru": e.message_ru})
    if problems:
        raise LifecycleError("tests_invalid", "Испытания: " + "; ".join(f"строка {p['line'] + 1} — {p['message_ru']}"
                                                                        for p in problems),
                             {"field": "tests", "problems": problems})
    return out


def parse_documents(docs: Any) -> list[dict]:
    """Документы паспорта: [{document_id, version_no}] — точная версия выбирается явно (не «текущая на момент
    синхронизации»). Повтор документа — ошибка. Существование версии проверяет сервер.
    """
    if docs is None:
        return []
    if not isinstance(docs, list):
        raise LifecycleError("invalid_type", "Документы — список.", {"field": "documents"})
    if len(docs) > MAX_DOCS:
        raise LifecycleError("too_many", f"Не больше {MAX_DOCS} документов.", {"field": "documents"})
    out, seen = [], set()
    for x in docs:
        if not isinstance(x, dict) or not isinstance(x.get("document_id"), str) or isinstance(x.get("version_no"), bool) \
                or not isinstance(x.get("version_no"), int) or x["version_no"] < 1:
            raise LifecycleError("invalid_document", "Документ паспорта — {document_id, version_no ≥ 1}.", {"field": "documents"})
        if x["document_id"] in seen:
            raise LifecycleError("document_duplicate", "Документ указан дважды.", {"field": "documents"})
        seen.add(x["document_id"])
        out.append({"document_id": x["document_id"], "version_no": x["version_no"]})
    return out


def passport_hash(*, template_code: str, template_version: int, template_sha: str, instance_id: str,
                  composition_hash: str, fields: dict, tests: list, documents: list) -> str:
    """SHA-256 содержимого паспорта: шаблон, экземпляр, его состав (хэш), разделы, испытания, документы с версиями
    и хэшами файлов. Записывается в журнал при черновом экспорте — видно, ЧТО именно выгружено.
    """
    return canon_hash({"template": [template_code, template_version, template_sha], "instance_id": instance_id,
                       "composition_hash": composition_hash, "fields": fields, "tests": tests,
                       "documents": [{k: d.get(k) for k in ("document_id", "version_no", "sha256")} for d in documents]})


def passport_problems(t: dict, fields: dict, tests: list) -> list[str]:
    """Что ещё не заполнено в черновике (подсказка составителю; сохранять черновик это не мешает)."""
    out = []
    for s in t["sections"]:
        if s["kind"] == "text" and s["key"] in ("tech_data", "resources", "acceptance") and not (fields.get(s["key"]) or "").strip():
            out.append(f"Не заполнен раздел «{s['title']}».")
    if not tests:
        out.append("Нет ни одного испытания.")
    elif any(x["result"] == "fail" for x in tests):
        out.append("Есть испытание с результатом «Не соответствует».")
    return out


# ---------------------------------------------------------------- передача, сервис, замены
def parse_handover(p: dict) -> dict:
    """Передача заказчику: дата, кто принял (ФИО, должность), документ (акт, накладная), примечание."""
    return {"date": parse_date(p.get("date"), "date", "дату передачи"),
            "recipient": _required(p.get("recipient"), "recipient", "кто принял у заказчика", 300),
            "document": _required(p.get("document"), "document", "документ передачи (акт, накладная)", 300),
            "note": _opt_text(p.get("note"), "note", 2000)}


def parse_service(p: dict) -> dict:
    """Сервисное событие: вид, дата, описание (что сделано / что случилось), документ (необязательно)."""
    kind = p.get("kind")
    if kind not in SERVICE_KINDS_RU:
        raise LifecycleError("invalid_kind", "Вид события — из списка.", {"field": "kind"})
    return {"kind": kind, "date": parse_date(p.get("date"), "date", "дату события"),
            "description": _required(p.get("description"), "description", "описание", 2000),
            "document": _opt_text(p.get("document"), "document", 300)}


def parse_replacement(p: dict) -> dict:
    """Замена в эксплуатации: что снято (наименование, номер), что установлено, количество, причина, дата."""
    qty = p.get("qty", "1")
    try:
        q = money.parse_decimal(qty)
    except money.MoneyError as e:
        raise LifecycleError(e.code, f"«Количество»: {e.message_ru}", {"field": "qty"})
    if q <= 0:
        raise LifecycleError("qty_not_positive", "«Количество» должно быть больше нуля.", {"field": "qty"})
    return {"date": parse_date(p.get("date"), "date", "дату замены"),
            "removed": _required(p.get("removed"), "removed", "что снято", 300),
            "removed_serial": _opt_text(p.get("removed_serial"), "removed_serial", 60),
            "installed": _required(p.get("installed"), "installed", "что установлено", 300),
            "installed_serial": _opt_text(p.get("installed_serial"), "installed_serial", 60),
            "line_id": _opt_text(p.get("line_id"), "line_id", 64) or None,
            "qty": money.to_json(q),
            "reason": _required(p.get("reason"), "reason", "причину замены", 2000)}


def _required(v: Any, field: str, what: str, max_len: int) -> str:
    """Обязательный текст."""
    s = _opt_text(v, field, max_len)
    if not s:
        raise LifecycleError("required", f"Укажите {what}.", {"field": field})
    return s


def event_summary(kind: str, d: dict) -> str:
    """Одна строка истории экземпляра для списка и паспорта."""
    if kind == "handover":
        return f"Передан заказчику: принял {d.get('recipient')}, {d.get('document')}"
    if kind == "service":
        return f"{SERVICE_KINDS_RU.get(d.get('kind'), d.get('kind'))}: {d.get('description')}"
    if kind == "replacement":
        rs = f" № {d['removed_serial']}" if d.get("removed_serial") else ""
        ins = f" № {d['installed_serial']}" if d.get("installed_serial") else ""
        return f"Замена: {d.get('removed')}{rs} → {d.get('installed')}{ins} ({d.get('reason')})"
    return kind


# ---------------------------------------------------------------- черновой экспорт
def _e(v: Any) -> str:
    """Экранирование HTML (всё, что ввёл пользователь, попадает в документ только экранированным)."""
    return html.escape("" if v is None else str(v), quote=True)


def _ru_date(iso_d: Optional[str]) -> str:
    """«2026-11-16» (или ISO с временем) → «16.11.2026»; пусто — «—»."""
    if not iso_d:
        return "—"
    s = str(iso_d)[:10]
    try:
        return date.fromisoformat(s).strftime("%d.%m.%Y")
    except ValueError:
        return s


def render_draft_html(*, passport: dict, instance: dict, export_no: int, exported_at: str, exported_by: str,
                      content_hash: str, company: str = "") -> str:
    """Черновой экспорт паспорта — самостоятельный HTML-файл (открывается в браузере, печатается).

    На каждой странице — пометка «Черновик, не сертификат» (водяной знак и колонтитул), в конце — номер экспорта,
    кто и когда выгрузил и SHA-256 содержимого. Подписей и печатей нет и не может быть: это черновик.
    passport/instance — представления сервера (v_passport / v_instance).
    """
    t = template(passport["template_code"], passport["template_version"])
    comp = instance.get("composition") or {}
    fields = passport.get("fields") or {}
    parts: list[str] = []

    def section(title: str, body: str) -> None:
        parts.append(f"<h2>{_e(title)}</h2>\n{body}")

    def para(text: str) -> str:
        return "".join(f"<p>{_e(x)}</p>" for x in text.splitlines() if x.strip()) or '<p class="empty">Не заполнено.</p>'

    for s in t["sections"]:
        k = s["key"]
        if k == "general":
            rows = [("Изделие", f"{instance.get('kit_code')} — {instance.get('kit_title') or ''}"),
                    ("Проект", instance.get("project_code")), ("Заводской номер", instance.get("serial_no")),
                    ("Серия", instance.get("series")),
                    ("Наряд", f"№ {(comp.get('work_order') or {}).get('doc_no')}"),
                    ("Дата изготовления (закрытия наряда)", _ru_date((comp.get("work_order") or {}).get("closed_at"))),
                    ("Техпроцесс", f"редакция {(comp.get('tp') or {}).get('revision_no')}"),
                    ("Спецификация", f"версия расчёта {(comp.get('spec') or {}).get('version_no')}")]
            if company:
                rows.insert(0, ("Изготовитель", company))
            section(s["title"], "<table>" + "".join(f"<tr><th>{_e(a)}</th><td>{_e(b)}</td></tr>" for a, b in rows) + "</table>")
        elif k == "composition":
            body = "<table><tr><th>Обозначение</th><th>Наименование</th><th>Количество на одно изделие</th></tr>"
            for ln in comp.get("lines") or []:
                q = f"{ln.get('qty_per_unit')} {ln.get('unit')}" if ln.get("qty_per_unit") else f"{ln.get('qty')} {ln.get('unit')} (на наряд)"
                mark = " (замена по наряду)" if ln.get("substituted") else ""
                body += f"<tr><td>{_e(ln.get('code') or '—')}</td><td>{_e(ln.get('name'))}{_e(mark)}</td><td>{_e(q)}</td></tr>"
            body += "</table>"
            reps = [e for e in instance.get("events") or [] if e.get("kind") == "replacement"]
            if reps:
                body += "<p><b>Замены в эксплуатации:</b></p><ul>" + "".join(
                    f"<li>{_e(_ru_date(e.get('date')))} — {_e(e.get('summary'))}</li>" for e in reps) + "</ul>"
            section(s["title"], body)
        elif k == "tests":
            tests = passport.get("tests") or []
            if not tests:
                section(s["title"], '<p class="empty">Испытания не указаны.</p>')
            else:
                section(s["title"], "<table><tr><th>Испытание</th><th>Результат</th><th>Дата</th><th>Протокол</th></tr>" + "".join(
                    f"<tr><td>{_e(x['name'])}{(' — ' + _e(x['note'])) if x.get('note') else ''}</td>"
                    f"<td>{_e(TEST_RESULTS_RU.get(x['result']))}</td><td>{_e(_ru_date(x['date']))}</td>"
                    f"<td>{_e(x.get('protocol') or '—')}</td></tr>" for x in tests) + "</table>")
        elif k == "documents":
            docs = passport.get("documents") or []
            if not docs:
                section(s["title"], '<p class="empty">Документы не указаны.</p>')
            else:
                section(s["title"], "<table><tr><th>Обозначение</th><th>Наименование</th><th>Версия</th><th>SHA-256 файла</th></tr>" + "".join(
                    f"<tr><td>{_e(d.get('code'))}</td><td>{_e(d.get('title'))}</td><td>{_e(d.get('version_no'))}</td>"
                    f"<td class=\"hash\">{_e(d.get('sha256'))}</td></tr>" for d in docs) + "</table>")
        elif k == "movement":
            evs = [e for e in instance.get("events") or [] if e.get("kind") in ("handover", "service")]
            section(s["title"], ("<ul>" + "".join(f"<li>{_e(_ru_date(e.get('date')))} — {_e(e.get('summary'))}</li>" for e in evs)
                                 + "</ul>") if evs else '<p class="empty">Записей нет.</p>')
        else:
            section(s["title"], para(fields.get(k) or ""))

    title = f"{t['title']} — {instance.get('kit_code')}, заводской № {instance.get('serial_no')}"
    css = ("@page{margin:18mm 16mm 22mm}body{font-family:'Segoe UI',Arial,sans-serif;font-size:11pt;color:#111;margin:0}"
           ".draft{position:fixed;top:0;left:0;right:0;background:#b42318;color:#fff;text-align:center;font-weight:bold;"
           "padding:4px;letter-spacing:1px}.foot{position:fixed;bottom:0;left:0;right:0;font-size:8pt;color:#b42318;"
           "text-align:center;border-top:1px solid #b42318;background:#fff;padding:2px}"
           ".wm{position:fixed;top:40%;left:0;right:0;text-align:center;font-size:64pt;color:rgba(180,35,24,.10);"
           "transform:rotate(-30deg);pointer-events:none;z-index:0}main{position:relative;z-index:1;padding:36px 8px 40px}"
           "h1{font-size:16pt}h2{font-size:12.5pt;margin-top:18px;border-bottom:1px solid #999}"
           "table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbb;padding:3px 6px;text-align:left;vertical-align:top}"
           "th{background:#f2f2f2}.hash{font-family:monospace;font-size:8pt;word-break:break-all}.empty{color:#777}"
           ".meta{font-size:9pt;color:#444;border:1px solid #b42318;padding:6px;margin-top:22px}")
    label = DRAFT_LABEL.upper()
    return ("<!DOCTYPE html>\n<html lang=\"ru\"><head><meta charset=\"utf-8\">"
            f"<title>{_e(label)} — {_e(title)}</title><style>{css}</style></head><body>"
            f"<div class=\"draft\">{_e(label)}</div><div class=\"wm\">{_e(label)}</div>"
            f"<div class=\"foot\">{_e(label)} · черновой экспорт № {export_no} · SHA-256 {_e(content_hash[:16])}…</div>"
            f"<main><h1>{_e(title)}</h1>"
            f"<p><b>{_e(DRAFT_LABEL)}.</b> Документ сформирован программой ВЕКТОР из черновика паспорта. Он не является "
            "сертификатом, свидетельством о приёмке или подписанным документом: подписей и печатей в нём нет.</p>"
            + "\n".join(parts) +
            f"<div class=\"meta\">Черновой экспорт № {export_no} · выгрузил: {_e(exported_by)} · {_e(exported_at)}<br>"
            f"Шаблон: {_e(t['title'])}, версия {t['version']} · SHA-256 содержимого: <span class=\"hash\">{_e(content_hash)}</span><br>"
            f"Состав изделия: SHA-256 <span class=\"hash\">{_e(instance.get('composition_hash'))}</span> · "
            f"снимок наряда: SHA-256 <span class=\"hash\">{_e((comp.get('work_order') or {}).get('snapshot_hash'))}</span></div>"
            "</main></body></html>\n")


def composition_lines(snapshot_lines: Iterable[dict], effective: Iterable[dict]) -> list[dict]:
    """Состав ОДНОГО изделия для карточки экземпляра: количество на единицу — из снимка наряда (точно, без деления);
    у строки, заменённой по наряду, количество известно только «на наряд» (замена задаётся на весь наряд).
    """
    per_unit = {ln["line_id"]: ln.get("qty_per_unit") for ln in snapshot_lines}
    out = []
    for ln in effective:
        sub = bool(ln.get("substituted"))
        out.append({"line_id": ln["line_id"], "code": ln.get("code"), "name": ln["name"], "unit": ln["unit"],
                    "qty_per_unit": None if sub else per_unit.get(ln["line_id"]), "qty": ln["qty"], "substituted": sub})
    return out
