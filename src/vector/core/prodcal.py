"""Производственный календарь (этап 7, R-20; decision log D-48 — ответ владельца на A-52 п. 3).

Ответ владельца: «производственный календарь дело хорошее, особенно если он будет подтягиваться с нужных ресурсов на
каждый год». Календарь говорит, какие дни года **особые** по сравнению с обычной неделей «пн–пт рабочие, сб–вс
выходные»:

* ``holiday``  — нерабочий праздничный день или перенесённый выходной (1 января, 9 марта за 8 марта и т. п.);
* ``short``    — рабочий **сокращённый** день (предпраздничный: на 1 час короче, ст. 95 Трудового кодекса РФ); бывает
                 и в субботу, если она рабочая по переносу;
* ``workday``  — рабочий день, который обычно выходной (суббота по переносу);
* ``normal``   — только ручная правка администратора: «обычный день по графику» (снять праздник из загруженного
                 календаря, например, если компания в этот день работает по своему решению).

Остальные дни — по дням недели графика работы. Календарь учитывают графики с отметкой «учитывать производственный
календарь» (пятидневка — да; круглосуточная смена — обычно нет): в праздник окно «Начните рабочий день» не
появляется, в сокращённый день конец по графику — на час раньше, в рабочую субботу день — рабочий
(vector.core.schedule.window).

Откуда данные. Формат — XML сайта xmlcalendar.ru (открытые данные; зеркало — GitHub xmlcalendar/xmlcalendar.github.io):

    <calendar year="2026" lang="ru" date="2025.09.30" country="ru">
      <holidays><holiday id="1" title="Новогодние каникулы"/> …</holidays>
      <days>
        <day d="01.01" t="1" h="1"/>          t=1 — выходной (h — праздник, f — с какой даты перенесён)
        <day d="04.30" t="2"/>                t=2 — рабочий сокращённый (любой день недели)
        <day d="12.28" t="3"/>                t=3 — рабочий день (суббота / воскресенье)
      </days>
    </calendar>

* В программу встроены календари России на несколько лет (``prodcal_data/ru-ГГГГ.xml``) — работает «из коробки» и
  без интернета; новые годы приходят с обновлением программы.
* Администратор загружает календарь на год **из интернета** (программа администратора скачивает файл с xmlcalendar.ru
  или его копий — URLS; серверу интернет не нужен) или **из файла** — загруженный в базу год важнее встроенного.
* **Каждый год сам** (A-52 п. 3: «подтягиваться с нужных ресурсов на каждый год»): с октября, когда календарь на
  следующий год обычно опубликован, программа администратора раз в день пробует скачать и загрузить его без участия
  человека (needs_auto_years); не вышло (нет интернета, ещё не опубликован) — попробует завтра, а в разделе
  «Производственный календарь» видно, что год не загружен.
* Ручные правки дней (региональный праздник, решение компании) сохраняются и при повторной загрузке года.

XML из интернета — недоверенные данные: размер ограничен, DOCTYPE / ENTITY запрещены (защита от «XML-бомб»), каждый
день проверяется (дата внутри года, тип из списка, без повторов).

Чистые функции без БД и Qt — общие для сервера и клиента.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date
from importlib import resources
from typing import Any, Iterable, Optional

from .lifecycle import LifecycleError

KINDS = ("holiday", "short", "workday")            # из календаря (xmlcalendar t = 1, 2, 3)
MANUAL_KINDS = KINDS + ("normal",)                 # ручная правка администратора
KIND_LABELS_RU = {"holiday": "праздник / выходной", "short": "сокращённый на 1 час", "workday": "рабочий (перенос)",
                  "normal": "обычный (по графику)"}
KIND_TONES = {"holiday": "danger", "short": "warning", "workday": "primary", "normal": "neutral"}
SHORT_MINUTES = 60                                 # предпраздничный день короче на 1 час (ст. 95 ТК РФ)
MAX_XML = 64 * 1024                                # календарь на год — около 2 КБ; больше — не календарь
MAX_TITLE = 120
MIN_YEAR, MAX_YEAR = 2000, 2100
_T_KIND = {"1": "holiday", "2": "short", "3": "workday"}
# Откуда скачивать календарь на год ({year} — год) — по порядку, пока один не ответит: сайт xmlcalendar.ru, его копия
# на GitHub Pages и сами файлы в репозитории GitHub (ветка main).
URLS = ("https://xmlcalendar.ru/data/ru/{year}/calendar.xml",
        "https://xmlcalendar.github.io/data/ru/{year}/calendar.xml",
        "https://raw.githubusercontent.com/xmlcalendar/xmlcalendar.github.io/main/data/ru/{year}/calendar.xml")
SOURCE_RU = "xmlcalendar.ru"
# С какого месяца напоминать администратору загрузить календарь на следующий год (xmlcalendar.ru публикует его
# обычно в конце сентября, после постановления Правительства о переносе выходных).
REMIND_FROM_MONTH = 10


class CalendarError(LifecycleError):
    """Нарушение правила производственного календаря (код, сообщение по-русски, детали — поле формы)."""


def parse_year(v: Any, field: str = "year") -> int:
    """Год календаря: целое число 2000…2100."""
    if isinstance(v, bool) or not isinstance(v, int) or not MIN_YEAR <= v <= MAX_YEAR:
        raise CalendarError("invalid_year", f"Год — число от {MIN_YEAR} до {MAX_YEAR}.", {"field": field})
    return v


def parse_xml(text: Any, year: Optional[int] = None) -> dict[str, Any]:
    """Текст XML xmlcalendar.ru → {year, source_date («2025-09-30» или None), days: [{date, kind, title, moved_from}],
    counts: {holiday, short, workday}}. year — какой год ожидается (файл за другой год — ошибка: перепутали файл).

    Проверки: размер до MAX_XML, без DOCTYPE / ENTITY, корень <calendar year="…">, дни — «ММ.ДД» внутри года, тип 1 / 2
    / 3, без повторов, хотя бы один день (пустой календарь — скорее ошибка загрузки, чем год без праздников).
    """
    if not isinstance(text, str) or not text.strip():
        raise CalendarError("empty", "Файл календаря пустой.", {"field": "xml"})
    if len(text.encode("utf-8")) > MAX_XML:
        raise CalendarError("too_large", "Файл слишком большой для производственного календаря (больше 64 КБ) — "
                                         "проверьте, тот ли это файл.", {"field": "xml"})
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.IGNORECASE):
        raise CalendarError("unsafe_xml", "Файл календаря содержит объявления типа документа или сущностей — "
                                          "такие файлы не принимаются (защита от вредоносного XML).", {"field": "xml"})
    try:
        root = ET.fromstring(text.lstrip("\ufeff").strip())
    except ET.ParseError as e:
        raise CalendarError("invalid_xml", f"Это не XML производственного календаря: {e}.", {"field": "xml"})
    if root.tag != "calendar":
        raise CalendarError("invalid_xml", "Ожидается XML производственного календаря (корень <calendar>, формат "
                                           "xmlcalendar.ru).", {"field": "xml"})
    try:
        y = int(root.get("year") or "")
    except ValueError:
        raise CalendarError("invalid_xml", "В календаре не указан год (<calendar year=…>).", {"field": "xml"})
    parse_year(y, "xml")
    if year is not None and y != year:
        raise CalendarError("wrong_year", f"Файл — календарь на {y} год, а загружается {year}.", {"field": "xml"})
    country = (root.get("country") or "ru").lower()
    if country != "ru":
        raise CalendarError("wrong_country", f"Календарь страны «{country}», ожидается Россия (ru).", {"field": "xml"})
    src_date = None
    m = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", root.get("date") or "")
    if m:
        try:
            src_date = date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            src_date = None
    titles = {h.get("id"): (h.get("title") or "").strip()[:MAX_TITLE] for h in root.iter("holiday")}
    days: list[dict] = []
    seen: set[str] = set()
    for d in root.iter("day"):
        iso = _md(d.get("d"), y)
        kind = _T_KIND.get(d.get("t") or "")
        if iso is None or kind is None:
            raise CalendarError("invalid_day", f"Неверная запись дня: d=«{d.get('d')}», t=«{d.get('t')}».",
                                {"field": "xml"})
        if iso in seen:
            raise CalendarError("duplicate_day", f"День {iso} указан дважды.", {"field": "xml"})
        seen.add(iso)
        moved = d.get("f")
        days.append({"date": iso, "kind": kind, "title": titles.get(d.get("h")) or None,
                     # f="01.03" — выходной перенесён с этой даты (того же года, а для начала года — бывает и прошлого)
                     "moved_from": _md(moved, y) if moved else None})
    if not days:
        raise CalendarError("empty", f"В календаре на {y} год нет ни одного дня — проверьте файл.", {"field": "xml"})
    days.sort(key=lambda x: x["date"])
    return {"year": y, "source_date": src_date, "days": days, "counts": counts(days)}


def _md(v: Optional[str], year: int) -> Optional[str]:
    """«ММ.ДД» года year → «ГГГГ-ММ-ДД» или None (неверная дата)."""
    m = re.fullmatch(r"(\d{2})\.(\d{2})", v or "")
    if not m:
        return None
    try:
        return date(year, int(m[1]), int(m[2])).isoformat()
    except ValueError:
        return None


def counts(days: Iterable[dict]) -> dict[str, int]:
    """Сколько дней каждого типа (для предпросмотра загрузки и строки состояния)."""
    out = {k: 0 for k in MANUAL_KINDS}
    for d in days:
        if d.get("kind") in out:
            out[d["kind"]] += 1
    return out


def counts_label(c: dict[str, int]) -> str:
    """«праздничных и выходных по переносу: 14, сокращённых: 4, рабочих выходных: 1»."""
    parts = [f"праздничных и перенесённых выходных: {c.get('holiday', 0)}", f"сокращённых: {c.get('short', 0)}"]
    if c.get("workday"):
        parts.append(f"рабочих выходных: {c['workday']}")
    if c.get("normal"):
        parts.append(f"обычных по правке: {c['normal']}")
    return ", ".join(parts)


def check_day(p: dict) -> dict[str, Any]:
    """Ручная правка дня администратором: {date (ГГГГ-ММ-ДД), kind (holiday|short|workday|normal или пусто — убрать
    правку), title?} → нормализованные {date, kind | None, title | None}."""
    raw = p.get("date")
    try:
        d = date.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        d = None
    if d is None:
        raise CalendarError("invalid_date", "Дата — ГГГГ-ММ-ДД, например 2026-12-30.", {"field": "date"})
    parse_year(d.year, "date")
    kind = p.get("kind") or None
    if kind is not None and kind not in MANUAL_KINDS:
        raise CalendarError("invalid_kind", "Тип дня: праздник, сокращённый, рабочий (перенос) или обычный.",
                            {"field": "kind"})
    title = p.get("title")
    if title is not None and not isinstance(title, str):
        raise CalendarError("invalid_text", "Название — текстом.", {"field": "title"})
    title = (title or "").strip() or None
    if title and len(title) > MAX_TITLE:
        raise CalendarError("too_long", f"Название — не длиннее {MAX_TITLE} знаков.", {"field": "title"})
    return {"date": d.isoformat(), "kind": kind, "title": title}


# ---------------------------------------------------------------- встроенные календари
_BUILTIN_CACHE: dict[int, Optional[dict]] = {}


def builtin(year: int) -> Optional[dict]:
    """Встроенный в программу календарь России на год (prodcal_data/ru-ГГГГ.xml) или None — года нет в этой версии."""
    if year not in _BUILTIN_CACHE:
        try:
            text = resources.files("vector.core").joinpath(f"prodcal_data/ru-{year}.xml").read_text(encoding="utf-8")
            _BUILTIN_CACHE[year] = parse_xml(text, year)
        except (FileNotFoundError, OSError, CalendarError):
            _BUILTIN_CACHE[year] = None
    return _BUILTIN_CACHE[year]


def federal_holiday(day: date) -> Optional[dict]:
    """Федеральный нерабочий праздничный день из встроенной редакции российского календаря.

    Это самостоятельная классификация для отметки фактически отработанного праздника: она намеренно не использует
    настройки графика и никогда не участвует в расчёте окна смены. Не считаем региональные ручные дни федеральными.
    """
    data = builtin(day.year)
    if not data:
        return None
    return next((item for item in data["days"]
                 if item["date"] == day.isoformat() and item["kind"] == "holiday"), None)


def builtin_years() -> list[int]:
    """Годы, встроенные в эту версию программы."""
    out = []
    for f in resources.files("vector.core").joinpath("prodcal_data").iterdir():
        m = re.fullmatch(r"ru-(\d{4})\.xml", f.name)
        if m:
            out.append(int(m[1]))
    return sorted(out)


# ---------------------------------------------------------------- действующий календарь
def effective(years: Iterable[int], loaded: dict[int, dict], imported: Iterable[dict],
              manual: Iterable[dict]) -> dict[str, dict]:
    """Действующий календарь на годы years: {ГГГГ-ММ-ДД: {kind, title, origin (import|builtin|manual)}}.

    loaded — загруженные в базу годы ({год: {source, …}}); imported — дни загруженных годов; manual — ручные правки
    (важнее всего; kind = normal — день обычный по графику, такие в итог не попадают). Год не загружен — встроенный
    календарь программы (если он есть).
    """
    ys = set(years)
    out: dict[str, dict] = {}
    for y in sorted(ys):
        if y in loaded:
            src = [d for d in imported if str(d["date"])[:4] == str(y)]
            origin = "import"
        else:
            b = builtin(y)
            src = b["days"] if b else []
            origin = "builtin"
        for d in src:
            out[str(d["date"])] = {"kind": d["kind"], "title": d.get("title"), "origin": origin}
    for d in manual:
        iso = str(d["date"])
        if int(iso[:4]) not in ys:
            continue
        if d["kind"] == "normal":
            out.pop(iso, None)
        else:
            out[iso] = {"kind": d["kind"], "title": d.get("title"), "origin": "manual"}
    return out


def year_status(year: int, loaded: dict[int, dict]) -> dict[str, Any]:
    """Откуда календарь на год: {state (loaded|builtin|missing), label}."""
    if year in loaded:
        x = loaded[year]
        when = f", файл от {_ru_date(x.get('source_date'))}" if x.get("source_date") else ""
        return {"state": "loaded", "label": f"загружен ({x.get('source') or SOURCE_RU}{when})"}
    b = builtin(year)
    if b is not None:
        when = f", от {_ru_date(b.get('source_date'))}" if b.get("source_date") else ""
        return {"state": "builtin", "label": f"встроенный в программу ({SOURCE_RU}{when})"}
    return {"state": "missing", "label": "нет — только дни недели графика"}


def needs_auto_years(today: date, loaded: dict[int, dict]) -> list[int]:
    """Какие годы программе администратора скачать самой: текущий, если он не загружен в базу, и с октября —
    следующий (встроенный календарь тоже годится, но загруженный свежее: постановление о переносах выходных могло
    выйти после сборки программы)."""
    out = [today.year] if today.year not in loaded else []
    if today.month >= REMIND_FROM_MONTH and (today.year + 1) not in loaded:
        out.append(today.year + 1)
    return out


def _ru_date(iso: Optional[str]) -> str:
    """«2025-09-30» → «30.09.2025»."""
    try:
        return f"{date.fromisoformat(iso or ''):%d.%m.%Y}"
    except ValueError:
        return iso or ""


def day_note(info: Optional[dict]) -> str:
    """Подпись особого дня для экрана: «праздник: День России», «сокращённый на 1 час (предпраздничный)»."""
    if not info:
        return ""
    kind = info.get("kind")
    title = info.get("title")
    if kind == "holiday":
        return "праздник: " + title if title else "нерабочий день (праздник или перенос выходного)"
    if kind == "short":
        return "сокращённый день — на 1 час короче (предпраздничный)"
    if kind == "workday":
        return "рабочий день по переносу"
    return ""
