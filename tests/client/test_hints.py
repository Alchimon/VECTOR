"""Подсказки и расшифровка сокращений (D-37): словарь hints.py полон и согласован с интерфейсом.

Проверяется без Qt:
* у каждого сокращения, которое программа показывает пользователю (строки QML, тексты клиента и ядра, подписи
  статусов из контрактов, демо-данные тестового стенда), есть расшифровка в GLOSSARY — новое сокращение без
  расшифровки не пройдёт;
* у каждой кнопки, пункта меню, кнопки подтверждения формы, вкладки и заголовка секции с постоянным текстом есть
  собственное пояснение в HINTS (а не только расшифровка сокращений);
* tip() собирает пояснение и расшифровки, нормализует счётчики, не выдумывает подсказку для обычного слова.
"""
import ast
import json
import re
from pathlib import Path

from vector.client import hints

ROOT = Path(__file__).resolve().parents[2]
QML = ROOT / "src" / "vector" / "client" / "qml"
# Консольные утилиты администратора (не интерфейс программы): их сообщения описаны в руководстве администратора.
CONSOLE = {"cli.py", "config.py", "backup.py", "devcert.py", "migrate.py", "hints.py"}


def _qml_strings(path: Path):
    """Строковые литералы QML без комментариев (// до конца строки вне строки)."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("//"):
            continue
        for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', line):
            yield m.group(1)


def _py_strings(path: Path):
    """Строковые константы Python, кроме докстрингов (они для разработчика, а не для пользователя)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body \
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
            docs.add(id(n.body[0].value))
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n.value


def _json_strings(o):
    """Все строки контракта (подписи статусов и т. п.)."""
    if isinstance(o, dict):
        for v in o.values():
            yield from _json_strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _json_strings(v)
    elif isinstance(o, str):
        yield o


def _user_texts():
    """(источник, текст) — всё, что программа может показать пользователю."""
    for f in sorted(QML.glob("*.qml")):
        for s in _qml_strings(f):
            yield f.name, s
    for pkg in ("client", "core", "server"):
        for f in sorted((ROOT / "src" / "vector" / pkg).glob("*.py")):
            if f.name not in CONSOLE:
                for s in _py_strings(f):
                    yield f.name, s
    wf = json.loads((ROOT / "contracts" / "workflows.slice1.json").read_text(encoding="utf-8"))
    for s in _json_strings(wf):
        yield "workflows", s


def test_every_abbreviation_shown_to_user_is_expanded():
    missing = {}
    for src, s in _user_texts():
        if not re.search("[А-Яа-яЁё]", s):      # служебные строки (ключи, коды команд) — не для пользователя
            continue
        for a in hints.abbreviations_in(s):
            if a not in hints.GLOSSARY:
                missing.setdefault(a, f"{src}: {s[:80]}")
    assert not missing, "Нет расшифровки в hints.GLOSSARY: " + json.dumps(missing, ensure_ascii=False, indent=1)


def test_glossary_entries_are_complete_and_plain():
    for abbr, (full, note) in hints.GLOSSARY.items():
        assert full.strip(), abbr
        assert full == full.strip() and note == note.strip(), abbr
        assert not note.endswith("."), f"{abbr}: пояснение без точки в конце (точку ставит expand)"


def _static_labels():
    """(вид, текст) — постоянные тексты кнопок, пунктов меню, подтверждений форм, вкладок, секций и меток в QML."""
    for f in sorted(QML.glob("*.qml")):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r'\b(VButton|NavItem|SectionTitle)\s*\{[^{}]*?\btext:\s*"([^"]+)"', src):
            yield m.group(1), m.group(2)
        for m in re.finditer(r'confirmText:\s*"([^"]+)"', src):
            yield "FormDialog.confirmText", m.group(1)
        for m in re.finditer(r'\{\s*id:\s*"[a-z_]+",\s*label:\s*"([^"]+)"', src):
            yield "VSegmented", m.group(1)
    for lb in ("Мои операции", "Наряды", "Техпроцессы"):      # вкладки «Производства» (production_view.py)
        yield "VSegmented", lb


def test_every_button_nav_item_tab_and_section_has_its_own_hint():
    missing = sorted({(k, t) for k, t in _static_labels() if not hints.has_hint(t)})
    assert not missing, f"Нет пояснения в hints.HINTS: {missing}"


def test_tip_combines_hint_and_expansions_in_order_of_appearance():
    t = hints.tip("Тендеры и ТКП")
    first, *rest = t.split("\n")
    assert first == hints.HINTS["Тендеры и ТКП"]
    assert rest == ["ТКП — технико-коммерческое предложение. Предложение заказчику по тендеру: состав работ, сроки и цена"]
    assert hints.tip("ТП АТХ ред. 1").split("\n") == [hints.expand("ТП"), hints.expand("АТХ"), hints.expand("ред.")]


def test_tip_normalizes_counters_and_handles_plain_words():
    assert hints.tip("Мои операции (3)") == hints.tip("Мои операции")
    assert hints.tip("Комплекты (") == hints.tip("Комплекты")
    assert hints.tip("Просто слово") == ""
    assert hints.tip("") == "" and hints.tip("   ") == ""


def test_compound_abbreviations_are_not_split():
    assert hints.abbreviations_in("Формат даты: ГГГГ-ММ-ДД.") == ["ГГГГ-ММ-ДД"]
    assert hints.abbreviations_in("ед. изм.: шт") == ["ед. изм."]
    assert hints.abbreviations_in("КИПиА насосной станции") == ["КИПиА"]
    assert hints.abbreviations_in("SHA-256 снимка") == ["SHA-256"]
    # Название программы и заголовки групп меню — не сокращения.
    assert hints.abbreviations_in("ВЕКТОР · РАБОТА · СИСТЕМА") == []


def test_online_rules_in_hints_match_command_catalog():
    """Подсказка «Только при связи» / «Можно без связи» не противоречит каталогу команд."""
    cat = json.loads((ROOT / "contracts" / "command-catalog.slice1.json").read_text(encoding="utf-8"))["commands"]
    mode = {k: v["offline"] for k, v in cat.items()}
    pairs = {"Новый заказчик": "customer.create", "Новый тендер": "tender.create", "Сменить статус": "tender.transition",
             "Зафиксировать": "calc.freeze", "Сохранить расчёт": "calc.save_draft", "Новый ТП": "tp.create",
             "Утвердить": "tp.approve", "Выпустить наряд": "wo.release", "Начать": "wo.op_event",
             "Проблема": "wo.op_event", "Приход": "stock.receive", "Новый заказ": "po.create",
             "На согласование": "po.submit", "Передать мою роль": "delegation.create", "Прочитано": "notification.read",
             "Новая версия": "document.add_version", "Начать РКД": "project.start_rkd"}
    for label, cmd in pairs.items():
        cls = mode[cmd]
        assert cls in ("queued", "online_required"), (cmd, cls)
        h = hints.HINTS[label]
        if cls == "queued":
            assert "без связи" in h and "Только при связи" not in h, label
        else:
            assert "Только при связи" in h, label


def test_with_expansion_for_select_lists():
    assert hints.with_expansion("ГИП") == "ГИП (главный инженер проекта)"
    assert hints.with_expansion("РП проекта") == "РП проекта (руководитель проекта)"
    assert hints.with_expansion("Технолог") == "Технолог"


def test_glossary_rows_sorted_russian_first_without_case_duplicates():
    rows = hints.glossary_rows()
    abbrs = [r["abbr"] for r in rows]
    assert len({a.lower() for a in abbrs}) == len(abbrs)
    first_latin = next(i for i, a in enumerate(abbrs) if a[0].isascii())
    assert all(not a[0].isascii() for a in abbrs[:first_latin]) and all(a[0].isascii() for a in abbrs[first_latin:])
    assert {"ТКП", "РКД", "ГИП", "РП", "ТП", "НДС", "SHA-256"} <= set(abbrs)
