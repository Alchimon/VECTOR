"""Шаблоны чек-листов ПНР/FAT (этап 6 blueprint 52, D-66/D-67): настраиваемые разделы и градации замечаний.

Каждый тест закрепляет правило из server/delivery.py и vector.core.delivery; запускается на SQLite и на встроенном
PostgreSQL. Кто есть кто: head1 — руководитель отдела (в оргструктуре — начальник сервисной службы, A-58),
rp1 — РП проекта (права на шаблоны нет).
"""
import pytest

from conftest import new_id


def pull(s):
    r = s.query("pull")
    assert r["status"] == "accepted", r
    return r["result"]


SECTIONS = [{"code": "power", "title": "Питание и заземление"}, {"code": "io", "title": "Проверка входов/выходов"}]
SEVERITIES = [{"code": "n", "label": "Несущественное", "blocking": False},
              {"code": "b", "label": "Блокирующее", "blocking": True}]


@pytest.fixture
def head(world):
    return world.login("head1")


def test_create_and_pull(head):
    tid = new_id()
    r = head.ok("checklist.create", {"id": tid, "kind": "fat", "name": "FAT шкафов АСУ",
                                     "sections": SECTIONS, "severities": SEVERITIES})
    cl = r["checklist"]
    assert cl["kind"] == "fat" and cl["kind_label"].startswith("FAT")
    assert [s["code"] for s in cl["sections"]] == ["power", "io"]
    assert cl["severities"][1]["blocking"] is True and cl["active"] and cl["version"] == 1
    # Шаблон приехал в pull и виден всем (читают все аутентифицированные).
    rows = pull(head)["checklist_templates"]
    assert any(x["id"] == tid and x["name"] == "FAT шкафов АСУ" for x in rows)


def test_default_severities_when_omitted(head):
    r = head.ok("checklist.create", {"id": new_id(), "kind": "pnr", "name": "ПНР базовый",
                                     "sections": SECTIONS})
    sev = r["checklist"]["severities"]
    assert [s["code"] for s in sev] == ["minor", "major", "critical"]
    assert sev[2]["blocking"] is True          # блокирующая градация есть всегда


def test_validation_errors(head):
    # Пустые разделы, повтор кода, градации без блокирующей — отказы с понятным текстом.
    r = head.cmd("checklist.create", {"id": new_id(), "kind": "fat", "name": "x", "sections": []})
    assert r["status"] == "rejected" and r["code"] == "bad_checklist"
    r = head.cmd("checklist.create", {"id": new_id(), "kind": "fat", "name": "x",
                                      "sections": [{"code": "a", "title": "А"}, {"code": "a", "title": "Б"}]})
    assert r["status"] == "rejected" and "повторяется" in r["message_ru"]
    r = head.cmd("checklist.create", {"id": new_id(), "kind": "fat", "name": "x", "sections": SECTIONS,
                                      "severities": [{"code": "n", "label": "Мелочь", "blocking": False}]})
    assert r["status"] == "rejected" and "блокирующей" in r["message_ru"]
    r = head.cmd("checklist.create", {"id": new_id(), "kind": "audit", "name": "x", "sections": SECTIONS})
    assert r["status"] == "rejected" and r["code"] == "bad_value"


def test_update_and_archive_versioned(head):
    tid = new_id()
    cl = head.ok("checklist.create", {"id": tid, "kind": "pnr", "name": "ПНР шкафов",
                                      "sections": SECTIONS})["checklist"]
    r = head.ok("checklist.update", {"id": tid, "name": "ПНР шкафов (расширенный)",
                                     "sections": SECTIONS + [{"code": "net", "title": "Сети связи"}]},
                expected_version=cl["version"])
    cl = r["checklist"]
    assert cl["version"] == 2 and len(cl["sections"]) == 3 and cl["name"].endswith("расширенный)")
    # Конфликт версий — отказ.
    r = head.cmd("checklist.update", {"id": tid, "name": "Ещё раз"}, expected_version=1)
    assert r["status"] == "conflict"
    # Нет изменений — отказ.
    r = head.cmd("checklist.update", {"id": tid, "name": cl["name"]}, expected_version=2)
    assert r["status"] == "rejected" and r["code"] == "no_changes"
    # Отключить и вернуть.
    cl = head.ok("checklist.archive", {"id": tid}, expected_version=2)["checklist"]
    assert cl["active"] is False and cl["version"] == 3
    r = head.cmd("checklist.archive", {"id": tid}, expected_version=3)
    assert r["status"] == "rejected" and r["code"] == "no_changes"
    cl = head.ok("checklist.unarchive", {"id": tid}, expected_version=3)["checklist"]
    assert cl["active"] is True


def test_manage_right_only_dept_head(world, head):
    # РП проекта шаблоны не настраивает (только читает); руководитель отдела — настраивает.
    rp = world.login("rp1")
    r = rp.cmd("checklist.create", {"id": new_id(), "kind": "fat", "name": "x", "sections": SECTIONS})
    assert r["status"] == "denied"
    tid = new_id()
    head.ok("checklist.create", {"id": tid, "kind": "fat", "name": "x", "sections": SECTIONS})
    rows = pull(rp)["checklist_templates"]
    assert any(x["id"] == tid for x in rows)               # читать может каждый
    r = rp.cmd("checklist.archive", {"id": tid}, expected_version=1)
    assert r["status"] == "denied"


def test_idempotent_replay(head):
    tid = new_id()
    p = {"id": tid, "kind": "fat", "name": "FAT", "sections": SECTIONS}
    op = new_id()
    r1 = head.cmd("checklist.create", p, op_id=op)
    assert r1["status"] == "accepted", r1
    r2 = head.cmd("checklist.create", p, op_id=op)          # повтор той же операции — тот же ответ, без дубля
    assert r2["status"] == "accepted" and r2["result"]["checklist"]["id"] == tid
    rows = [x for x in pull(head)["checklist_templates"] if x["id"] == tid]
    assert len(rows) == 1
