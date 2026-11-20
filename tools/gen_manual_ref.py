"""Справочник руководства (docs/manual/reference.md) — генерируется из контрактов /contracts.

Зачем генерировать, а не писать руками: права, команды и статусы меняются вместе с кодом. Сгенерированный
справочник не может разойтись с тем, что на самом деле проверяет сервер, — тест tests/core/test_manual_reference.py
падает, если справочник не обновили после правки контрактов.

  python tools/gen_manual_ref.py          # пересоздать docs/manual/reference.md
  python tools/gen_manual_ref.py --check  # только проверить, что файл актуален (код 1 — устарел)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# Словарь сокращений клиента (D-37) — без Qt, импортируется напрямую: справочник и программа расшифровывают одинаково.
from vector.client import hints  # noqa: E402
CONTRACTS = ROOT / "contracts"
OUT = ROOT / "docs" / "manual" / "reference.md"

# Русские названия ролей — те же, что в клиенте (src/vector/client/backend.py, ROLE_LABELS).
# Дублируются здесь, чтобы инструмент не зависел от PySide6; совпадение проверяет тест.
ROLE_LABELS = {"admin": "Администратор", "gip": "ГИП", "rp": "РП", "dept_head": "Руководитель отдела",
               "lead_engineer": "Ведущий инженер", "engineer": "Инженер", "designer": "Конструктор",
               "technologist": "Технолог", "production": "Производство", "warehouse": "Склад",
               "procurement": "Закупка", "accountant": "Бухгалтерия",
               "po_approver": "Согласование закупок",
               "otk": "Технический контроль (ОТК)"}
PROJECT_ROLE_LABELS = {"gip": "ГИП проекта", "rp": "РП проекта", "participant": "Участник проекта"}
OFFLINE_LABELS = {"queued": "можно без связи (очередь)", "online_required": "только при связи",
                  "offline_full": "полностью офлайн"}
# Названия действий, которых нет в каталоге команд (чтение и уточнения прав внутри команды).
EXTRA_ACTION_LABELS = {
    "customer.read": "Просмотр заказчиков", "tender.read": "Просмотр тендеров", "tkp.read": "Просмотр ТКП",
    "project.read": "Просмотр проекта", "kit.read": "Просмотр комплектов", "task.read": "Просмотр задач",
    "document.read": "Просмотр документов и скачивание файлов", "calc.read": "Просмотр расчётов комплектов",
    "calc.read_revisions": "Просмотр проектного итога (ревизий)", "stock.read": "Просмотр остатков, резервов и журнала склада",
    "need.read": "Просмотр потребностей", "supplier.read": "Просмотр поставщиков и договоров",
    "po.read": "Просмотр заказов поставщикам", "po.read_prices": "Видеть цены, суммы и НДС заказов",
    "admin.list_users": "Список учётных записей",
    "project.assign_gip": "Назначить ГИП проекта", "project.assign_rp": "Назначить РП проекта",
    "project.assign_participant": "Назначить участника проекта",
    "task.transition_own": "Сменить статус своей задачи (исполнитель)"}
WORKFLOW_TITLES = {"tender": "Тендер", "tkp": "ТКП", "project": "Проект", "task": "Задача", "document": "Документ",
                   "stock_reservation": "Складской резерв", "stock_transfer": "Перемещение между складами",
                   "purchase_order": "Заказ поставщику", "purchase_order_approval": "Согласование заказа",
                   "tech_process": "Технологический процесс (ТП)", "work_order": "Наряд",
                   "wo_operation": "Операция наряда"}


def _load(name: str) -> dict:
    """Прочитать JSON-контракт из /contracts."""
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def _who(rule: dict, rules: dict) -> str:
    """Кто может выполнить действие — по правилу из permissions (any_of / same_as) — русским текстом."""
    if "same_as" in rule:
        return _who(rules[rule["same_as"]], rules)
    parts = []
    for cond in rule.get("any_of", []):
        if cond.get("authenticated"):
            parts.append("любой вошедший пользователь (только над своей учётной записью)")
        if "global_role" in cond:
            parts.append("роль: " + ", ".join(ROLE_LABELS.get(r, r) for r in cond["global_role"]))
        if "project_role" in cond:
            parts.append("в проекте: " + ", ".join(PROJECT_ROLE_LABELS.get(r, r) for r in cond["project_role"]))
    return "; ".join(parts) or "никто"


def render() -> str:
    """Собрать текст справочника из трёх контрактов."""
    cat, perm, wf = _load("command-catalog.slice1.json"), _load("permissions.slice1.json"), _load("workflows.slice1.json")
    rules = perm["rules"]
    out = ["# Справочник: роли, права, команды, статусы, сокращения",
           "",
           "> **Файл создан автоматически** командой `python tools/gen_manual_ref.py` из контрактов",
           f"> `{cat['contract']}`, `{perm['contract']}`, `{wf['contract']}`. Не правьте его вручную —",
           "> правьте контракты в `/contracts` и запустите генератор (тест проверяет актуальность).",
           "",
           "Права ниже — **действующие временные предположения** (журнал решений, A-03…A-24), которые владелец",
           "продукта может изменить. Интерфейс использует их только как подсказку (скрыть явно недоступное);",
           "решение всегда принимает сервер в момент действия.",
           "",
           "## 1. Роли доступа",
           "",
           "Роль доступа ≠ должность ≠ подразделение. Глобальные роли выдаёт администратор (раздел «Администрирование»);",
           "роли в проекте — назначения в карточке проекта. Администратор **не получает** бизнес-прав автоматически.",
           "",
           "| Код | Роль | Тип |",
           "|---|---|---|"]
    # Сокращения в названиях ролей расшифрованы (D-37), как в списках выбора программы.
    for r in perm["roles"]:
        out.append(f"| `{r}` | {hints.with_expansion(ROLE_LABELS.get(r, r))} | глобальная |")
    for r in perm["project_roles"]:
        out.append(f"| `{r}` | {hints.with_expansion(PROJECT_ROLE_LABELS.get(r, r))} | в проекте |")
    out += ["", "Общие правила:", ""]
    out += [f"* {n}" for n in perm.get("notes", [])]
    out += ["", "## 2. Кто что может (матрица прав)", "",
            "| Действие | Кто может | Основание |", "|---|---|---|"]
    labels = {**EXTRA_ACTION_LABELS, **{k: v["label_ru"] for k, v in cat["commands"].items()}}
    for action, rule in rules.items():
        title = labels.get(action, "")
        name = f"{title} (`{action}`)" if title else f"`{action}`"
        basis = rule.get("assumption", "") or (f"как `{rule['same_as']}`" if "same_as" in rule else "")
        note = rule.get("extra") or rule.get("note") or ""
        out.append(f"| {name} | {_who(rule, rules)}{(' — ' + note) if note else ''} | {basis} |")
    out += ["", "## 3. Команды и работа без связи", ""]
    for k, v in cat["offline_classes"].items():
        out.append(f"* **{OFFLINE_LABELS.get(k, k)}** (`{k}`) — {v}")
    out += ["", "| Команда | Действие | Без связи |", "|---|---|---|"]
    for k, v in cat["commands"].items():
        out.append(f"| `{k}` | {v['label_ru']} | {OFFLINE_LABELS.get(v['offline'], v['offline'])} |")
    out += ["", "Запросы:", "", "| Запрос | Назначение | Без связи |", "|---|---|---|"]
    for k, v in cat.get("queries", {}).items():
        out.append(f"| `{k}` | {v['label_ru']} | {OFFLINE_LABELS.get(v['offline'], v['offline'])} |")
    out += ["", "## 4. Статусы и переходы", ""]
    out += [f"* {n}" for n in ([wf["notes"]] if isinstance(wf.get("notes"), str) else wf.get("notes", []))]
    out.append("")
    tender_labels = wf["tender"]["labels_ru"]
    for name, w in wf.items():
        if not isinstance(w, dict):
            continue
        if "labels_ru" not in w:
            # Объект без статусов (документ): выводим его правила текстом.
            out += [f"### {WORKFLOW_TITLES.get(name, name)}", ""]
            if w.get("code_unique_within") == "project":
                out.append("Обозначение документа уникально в пределах проекта.")
            if w.get("versions"):
                out.append(w["versions"])
            out += ["", f"Основание: {w.get('assumption', '')}.", ""]
            continue
        lab = w["labels_ru"]
        out += [f"### {WORKFLOW_TITLES.get(name, name)}", ""]
        if w.get("initial"):
            out.append((f"Начальный статус: **{lab.get(w['initial'], w['initial'])}**. "
                        + ("Конечные: " + ", ".join(f"**{lab.get(t, t)}**" for t in w.get("terminal", [])) + "."
                           if w.get("terminal") else "")).rstrip())
            out.append("")
        if w.get("transitions"):
            out += ["| Из статуса | Можно перевести в |", "|---|---|"]
            for src, dsts in w["transitions"].items():
                out.append(f"| {lab.get(src, src)} | {', '.join(lab.get(d, d) for d in dsts) or '—'} |")
            out.append("")
        if w.get("own_transitions"):
            own = w["own_transitions"]
            items = own.items() if isinstance(own, dict) else []
            if items:
                out.append("Исполнитель может сам переводить свою задачу: "
                           + "; ".join(f"{lab.get(s, s)} → {', '.join(lab.get(d, d) for d in ds)}" for s, ds in items) + ".")
                out.append("")
        if w.get("reason_required_for"):
            # «reopen» — не статус, а возврат выполненной задачи в работу (Выполнена → Открыта).
            rr = [lab.get(s, "возврат в работу" if s == "reopen" else s) for s in w["reason_required_for"]]
            out.append("Причина обязательна для: " + ", ".join(rr) + ".")
            out.append("")
        if w.get("start_rkd_allowed_from_tender_status"):
            out.append("Проект создаётся явным действием «Начать РКД» только из тендера в статусе: "
                       + ", ".join(f"**{tender_labels.get(x, x)}**" for x in w["start_rkd_allowed_from_tender_status"])
                       + (". Один проект на тендер." if w.get("one_project_per_tender") else "."))
            out.append("")
        if w.get("assumption"):
            out += [f"Основание: {w['assumption']}.", ""]
    out += ["## 5. Сокращения", "",
            "Все сокращения, которые встречаются в программе (D-37). Тот же список — в программе: раздел",
            "«Сокращения и подсказки» (клавиша `F1`) и во всплывающих подсказках при наведении мыши. Источник —",
            "`src/vector/client/hints.py`; тест не даст показать в интерфейсе сокращение без расшифровки.", "",
            "| Сокращение | Полное название | Пояснение |", "|---|---|---|"]
    for row in hints.glossary_rows():
        out.append(f"| {row['abbr']} | {row['full']} | {row['note'] or '—'} |")
    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    """--check — сравнить с файлом; без ключей — записать файл."""
    text = render()
    if "--check" in sys.argv:
        ok = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("reference.md: OK" if ok else "reference.md устарел: запустите python tools/gen_manual_ref.py")
        raise SystemExit(0 if ok else 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"Записан {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
