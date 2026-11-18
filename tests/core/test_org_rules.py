"""Правила оргструктуры без БД (D-44, ответ на A-48 п. 1): виды, коды, цикл и глубина дерева, обход для экрана,
маршрут проверки отчёта (свой руководитель → вышестоящий), архив только пустого подразделения."""
import pytest

from vector.core import org as og


def u(i, parent=None, head=None, kind="department", active=True, name=None, code=None):
    """Подразделение для тестов."""
    return {"id": i, "kind": kind, "name": name or i, "code": code, "parent_id": parent, "head_user_id": head,
            "active": active}


def tree():
    """Филиал F → структура S → отделы D1, D2; второй корень F2."""
    return {x["id"]: x for x in (u("F", kind="branch", head="boss", name="Филиал Юг"),
                                 u("S", "F", "dir", kind="structure", name="Инженерная дирекция"),
                                 u("D1", "S", "h1", name="Отдел АСУ ТП"), u("D2", "S", None, name="Конструкторский"),
                                 u("F2", kind="branch", name="Филиал Север"))}


def test_parse_fields():
    """Вид — из трёх; название обязательно; код без пробелов; должность необязательна."""
    assert og.parse_kind("branch") == "branch"
    with pytest.raises(og.OrgError) as e:
        og.parse_kind("division")
    assert e.value.code == "invalid_kind"
    assert og.parse_name("  Отдел  ") == "Отдел"
    with pytest.raises(og.OrgError):
        og.parse_name(" ")
    assert og.parse_code(None) is None and og.parse_code("ОАСУ-1") == "ОАСУ-1"
    with pytest.raises(og.OrgError) as e:
        og.parse_code("О АСУ")
    assert e.value.code == "invalid_code"
    assert og.parse_position("") is None and og.parse_position("Инженер") == "Инженер"
    with pytest.raises(og.OrgError):
        og.opt_id("abc", "parent_id")


def test_cycle_and_parent_checks():
    """Нельзя войти в себя или в своё подчинённое; родитель должен существовать и действовать."""
    t = tree()
    og.check_parent(t, "D2", "D1")            # отдел в отделе — можно (приложение универсальное)
    og.check_parent(t, "F2", "D1")            # и филиал внутрь — тоже, если нет круга
    for parent in ("S", "D1"):
        with pytest.raises(og.OrgError) as e:
            og.check_parent(t, "S" if parent == "S" else "F", parent)
        assert e.value.code == "cycle"
    with pytest.raises(og.OrgError) as e:
        og.check_parent(t, "D1", "nope")
    assert e.value.code == "parent_not_found"
    t["F2"]["active"] = False
    with pytest.raises(og.OrgError) as e:
        og.check_parent(t, "D1", "F2")
    assert e.value.code == "parent_archived"


def test_depth_limit():
    """Глубина дерева ограничена — в том числе с учётом подчинённых переносимого подразделения."""
    t = {f"U{i}": u(f"U{i}", f"U{i - 1}" if i else None) for i in range(og.MAX_DEPTH)}
    t["X"] = u("X")
    with pytest.raises(og.OrgError) as e:
        og.check_parent(t, "X", f"U{og.MAX_DEPTH - 1}")
    assert e.value.code == "too_deep"
    og.check_parent(t, "X", f"U{og.MAX_DEPTH - 2}")


def test_flatten_subtree_and_path():
    """Обход по названию с глубиной; поддерево; путь от корня; цикл в повреждённых данных не зацикливает."""
    t = tree()
    flat = og.flatten(t)
    assert [(x["id"], x["depth"]) for x in flat] == [("F2", 0), ("F", 0), ("S", 1), ("D2", 2), ("D1", 2)]
    assert [x["has_children"] for x in flat] == [False, True, True, False, False]
    assert set(og.subtree(t, "S")) == {"S", "D1", "D2"}
    assert og.path_label(t, "D1") == "Филиал Юг › Инженерная дирекция › Отдел АСУ ТП"
    assert og.path_label(t, None) == ""
    t["F"]["parent_id"] = "D1"                 # повреждённые данные: круг
    assert len(og.ancestors(t, "D1")) <= og.MAX_DEPTH + 1
    # Архивные скрываются по флагу, их подчинённые всплывают корнями.
    t = tree()
    t["S"]["active"] = False
    assert [x["id"] for x in og.flatten(t, include_archived=False)][:2] == ["F2", "F"]
    assert {"D1", "D2"} <= {x["id"] for x in og.flatten(t, include_archived=False)}


def test_review_route_goes_up_when_no_head():
    """Свой руководитель; нет его — вышестоящий; автор-руководитель — его руководитель; отключённый — пропуск."""
    t = tree()
    assert og.review_route(t, "D1", "eng") == {"unit_id": "D1", "head_user_id": "h1"}
    assert og.review_route(t, "D2", "eng") == {"unit_id": "S", "head_user_id": "dir"}
    assert og.review_route(t, "D1", "h1") == {"unit_id": "S", "head_user_id": "dir"}
    assert og.review_route(t, "D1", "eng", lambda x: x != "h1")["head_user_id"] == "dir"
    assert og.review_route(t, "F", "boss") is None
    assert og.review_route(t, None, "eng") is None
    assert og.chain_heads(t, "D1", "eng") == ["h1", "dir", "boss"]
    assert og.chain_heads(t, "D1", "dir") == ["h1", "boss"]
    assert og.headed_units(t, "dir") == ["S"]


def test_archive_only_empty():
    """В архив — только без действующих подчинённых и без сотрудников."""
    t = tree()
    with pytest.raises(og.OrgError) as e:
        og.check_archive(t, "S", 0)
    assert e.value.code == "has_children" and "Отдел АСУ ТП" in e.value.message_ru
    with pytest.raises(og.OrgError) as e:
        og.check_archive(t, "D1", 3)
    assert e.value.code == "has_members"
    og.check_archive(t, "D2", 0)
    assert og.unit_label(u("x", name="Отдел", code="ОТ")) == "Отдел (ОТ)" and og.unit_label(None) == "—"
    assert og.kind_of(t, t) == {"branch": 2, "structure": 1, "department": 2}
