"""Справочник руководства (docs/manual/reference.md) совпадает с контрактами и подписями ролей клиента.

Если тест упал — после правки /contracts выполните: python tools/gen_manual_ref.py
"""
import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _tool():
    """Загрузить tools/gen_manual_ref.py как модуль (каталог tools — не пакет)."""
    spec = importlib.util.spec_from_file_location("gen_manual_ref", ROOT / "tools" / "gen_manual_ref.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_reference_is_up_to_date():
    mod = _tool()
    assert mod.OUT.read_text(encoding="utf-8") == mod.render(), "запустите python tools/gen_manual_ref.py"


def test_role_labels_match_client():
    # Подписи ролей в справочнике и в интерфейсе одинаковы (клиент не импортируем: он требует PySide6).
    tree = ast.parse((ROOT / "src" / "vector" / "client" / "backend.py").read_text(encoding="utf-8"))
    client = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in ("ROLE_LABELS", "PROJECT_ROLE_LABELS"):
            client[node.targets[0].id] = ast.literal_eval(node.value)
    mod = _tool()
    assert client["ROLE_LABELS"] == mod.ROLE_LABELS
    # В справочнике «Участник проекта» — полнее, чем короткая подпись «Участник» в интерфейсе; коды совпадают.
    assert set(client["PROJECT_ROLE_LABELS"]) == set(mod.PROJECT_ROLE_LABELS)
