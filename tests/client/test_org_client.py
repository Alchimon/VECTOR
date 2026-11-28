"""Клиент: представление раздела «Оргструктура» (D-44) — дерево с отступами, поиск с вышестоящими, карточка,
варианты для форм, «кто проверяет мои отчёты», сотрудники вне оргструктуры, журнал изменений."""
from datetime import datetime, timezone

import pytest

from vector.client import org_view as ov
from vector.client.sync import STOCK_META

PEOPLE = {"u-boss": "Орлова Елена", "u-eng": "Кузнецов Дмитрий", "u-lead": "Лебедев Максим", "u-new": "Соколов Никита"}


def _u(i, name, kind="department", parent=None, head=None, code=None, active=True, version=1):
    return {"id": i, "kind": kind, "name": name, "code": code, "parent_id": parent, "head_user_id": head,
            "active": active, "version": version}


UNITS = [_u("ho", "Головной офис", "branch", code="ГО"), _u("dir", "Инженерная дирекция", "structure", "ho", "u-boss"),
         _u("asu", "Отдел АСУ ТП", parent="dir", code="ОАСУ"), _u("old", "Старый отдел", parent="ho", active=False)]
MEMBERS = [{"user_id": "u-eng", "unit_id": "asu", "position": "Инженер"}, {"user_id": "u-lead", "unit_id": "asu", "position": None},
           {"user_id": "u-boss", "unit_id": "dir", "position": "Директор"}]
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def test_tree_rows_cards_and_options():
    """Строки по дереву с глубиной; карточка с путём и сотрудниками; архивные — в дереве, но не в вариантах."""
    v = ov.org_view(UNITS, MEMBERS, [], {"unit_id": "asu", "reviewer_id": "u-boss", "manage": True}, PEOPLE, True, NOW)
    assert [(r["name"], r["depth"]) for r in v["rows"]] == [("Головной офис", 0), ("Инженерная дирекция", 1),
                                                            ("Отдел АСУ ТП", 2), ("Старый отдел", 1)]
    assert v["rows"][1]["subtitle"] == "Структура · руководитель: Орлова Елена"
    assert v["rows"][2]["members"] == 2 and v["rows"][3]["active"] is False
    c = v["cards"]["asu"]
    assert c["path"] == "Головной офис › Инженерная дирекция › Отдел АСУ ТП" and c["head_name"] == "—"
    assert [m["name"] for m in c["members_list"]] == ["Кузнецов Дмитрий", "Лебедев Максим"]
    assert c["can_archive"] is False and v["cards"]["old"]["can_archive"] is False
    assert v["cards"]["dir"]["members_list"][0]["is_head"] is True
    assert [o["id"] for o in v["unit_options"]] == ["ho", "dir", "asu"]
    assert v["unit_options"][2]["label"] == "        Отдел АСУ ТП"
    assert v["parent_options"][0]["id"] == "" and v["head_options"][0]["label"] == "— не назначен —"
    assert [p["label"] for p in v["unplaced"]] == ["Соколов Никита"]
    assert v["counts_label"] == "Филиалы: 1, Структуры: 1, Отделы: 1"
    assert v["mine"]["text"] == "Вы: Головной офис › Инженерная дирекция › Отдел АСУ ТП. Дневные отчёты проверяет Орлова Елена."
    assert (v["manage"], v["can_manage"]) == (True, True)
    assert ov.org_view(UNITS, MEMBERS, [], {"manage": True}, PEOPLE, False, NOW)["can_manage"] is False


def test_search_keeps_ancestors_and_mine_texts():
    """Поиск по сотруднику показывает его отдел вместе с вышестоящими; «вне оргструктуры» — понятный текст."""
    v = ov.org_view(UNITS, MEMBERS, [], {}, PEOPLE, True, NOW, query="лебедев")
    assert [r["id"] for r in v["rows"]] == ["ho", "dir", "asu"]
    assert ov.org_view(UNITS, MEMBERS, [], {}, PEOPLE, True, NOW, query="нет такого")["rows"] == []
    assert "не включены" in v["mine"]["text"]
    v = ov.org_view(UNITS, MEMBERS, [], {"unit_id": "asu"}, PEOPLE, True, NOW)
    assert "Руководителя по цепочке нет" in v["mine"]["text"]
    assert ov.org_view([], [], [], {}, PEOPLE, True, NOW)["empty"] is True


def test_events_labels_and_cache_keys():
    """Журнал: подпись, объект, подразделение, кто и когда; данные оргструктуры хранятся в кэше снимком."""
    ev = [{"id": 2, "object_type": "member", "object_id": "u-eng", "event": "moved", "event_label": "Сотрудник переведён",
           "details": {"unit_id": "asu"}, "actor_id": "u-boss", "at": "2026-11-16T09:15:00+00:00"},
          {"id": 1, "object_type": "unit", "object_id": "asu", "event": "created", "event_label": "Создано подразделение",
           "details": {"kind": "department"}, "actor_id": "u-boss", "at": "2026-11-15T09:15:00+00:00"}]
    v = ov.org_view(UNITS, MEMBERS, ev, {}, PEOPLE, True, NOW)
    assert (v["events"][0]["object"], v["events"][0]["unit"], v["events"][0]["who"]) == ("Кузнецов Дмитрий", "Отдел АСУ ТП", "Орлова Елена")
    assert v["events"][0]["when"].startswith("сегодня") and v["events"][1]["object"] == "Отдел АСУ ТП"
    assert {"org_units", "org_members", "org_events", "org_me"} <= set(STOCK_META)


def test_authority_label_for_unit_head():
    """«Основание доступа» в истории отчёта (D-45): руководитель подразделения — с названием из кэша, без кодов."""
    pytest.importorskip("PySide6", reason="Backend — QObject, требует PySide6 (минимальная машина тестирования)")
    from types import SimpleNamespace

    from vector.client.backend import Backend
    fn = Backend.__dict__["authorityLabel"]
    me = SimpleNamespace(_stock={"org_units": [{"id": "11111111-2222-3333-4444-555555555555", "name": "Отдел АСУ ТП"}]})
    assert fn(me, "org_head#unit:11111111-2222-3333-4444-555555555555") == "руководитель подразделения «Отдел АСУ ТП»"
    assert fn(SimpleNamespace(_stock={}), "org_head#unit:abc") == "руководитель подразделения"
