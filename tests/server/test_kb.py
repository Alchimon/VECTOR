"""База знаний и справочники компании (P3-3, blueprint 57).

Правила (server/kb.py, contracts permissions kb.*/vendor.*/equipment.*):
* статьи: scope company — ведущий инженер/руководитель отдела; scope project — участники ЭТОГО проекта;
  код уникален в своём scope; команды — офлайн-очередь; архив мягкий (active=false), версия растёт;
* vendors (общая база подрядчиков) — только онлайн, ведёт закупка (procurement);
* equipment (каталог оборудования) — только онлайн, ведут ведущий инженер и технолог;
* чтение: статьи компании — всем вошедшим, проекта — читающим проект; справочники — всем вошедшим.
"""
from conftest import new_id
from test_tasks_documents import proj  # noqa: F401  (проект в работе: ГИП gip1, РП rp1, участник eng1; lead1 вне команды)


def kb_of(s):
    """Статьи базы знаний из pull пользователя."""
    return s.query("pull")["result"]["kb_articles"]


def test_kb_company_scope_rights_and_crud(world):
    lead, head, eng = world.login("lead1"), world.login("head1"), world.login("eng1")
    # Стандарт компании пишет ведущий инженер (или руководитель отдела) — не рядовой инженер.
    r = eng.cmd("kb.create", {"id": new_id(), "scope": "company", "kind": "standard", "code": "СТО-01",
                              "title": "Сварка", "body_md": "Текст стандарта"})
    assert r["status"] == "denied"
    a = lead.ok("kb.create", {"id": new_id(), "scope": "company", "kind": "standard", "code": "СТО-01",
                              "title": "Сварка", "body_md": "Текст стандарта", "tags": ["Сварка", "  кип  "]})["kb_article"]
    assert (a["scope"], a["code"], a["tags"], a["active"], a["version"]) == ("company", "СТО-01", ["Сварка", "кип"], True, 1)
    # Код уникален в scope.
    r = head.cmd("kb.create", {"id": new_id(), "scope": "company", "kind": "note", "code": "СТО-01",
                               "title": "Дубль", "body_md": "…"})
    assert r["status"] == "rejected" and r["code"] == "code_taken"
    # Правка любым уполномоченным (коллективная база) с версией; без изменений — отказ.
    a2 = head.ok("kb.update", {"id": a["id"], "title": "Сварка. Общие требования"}, expected_version=1)["kb_article"]
    assert a2["version"] == 2 and a2["title"].endswith("требования")
    r = head.cmd("kb.update", {"id": a["id"], "title": a2["title"]}, expected_version=2)
    assert r["code"] == "no_changes"
    # Конфликт версий — честный 409 с актуальным состоянием.
    r = lead.cmd("kb.update", {"id": a["id"], "title": "X"}, expected_version=1)
    assert r["status"] == "conflict" and r["details"]["current"]["version"] == 2
    # Архив мягкий: статья остаётся в выдаче с пометкой.
    a3 = head.ok("kb.archive", {"id": a["id"], "active": False}, expected_version=2)["kb_article"]
    assert a3["active"] is False
    assert next(x for x in kb_of(eng) if x["id"] == a["id"])["active"] is False
    head.ok("kb.archive", {"id": a["id"], "active": True}, expected_version=3)


def test_kb_project_scope(world, proj):  # noqa: F811
    # Заметку проекта пишет его участник; посторонний (lead1) — отказ scope.
    n = proj.eng.ok("kb.create", {"id": new_id(), "scope": "project", "project_id": proj.pid, "kind": "howto",
                                  "code": "КАК-01", "title": "Как готовить трансмиттал", "body_md": "Шаги…"})["kb_article"]
    assert n["project_id"] == proj.pid
    r = proj.lead.cmd("kb.create", {"id": new_id(), "scope": "project", "project_id": proj.pid, "kind": "note",
                                    "code": "Ч-01", "title": "Чужая", "body_md": "…"})
    assert r["status"] == "rejected" and r["code"] == "kb_scope_denied"
    # Глобальная роль без назначения в проект тоже не даёт писать в проект (D-38-логика scope).
    assert world.login("lead1").query("pull")["result"]["kb_articles"] == []  # company-статей нет, чужой проект не виден
    assert any(x["id"] == n["id"] for x in kb_of(proj.rp))
    # Участник проекта НЕ может писать стандарты компании только по назначению (нужна глобальная роль).
    r = proj.eng.cmd("kb.create", {"id": new_id(), "scope": "company", "kind": "standard", "code": "СТО-02",
                                   "title": "X", "body_md": "…"})
    assert r["status"] == "denied"
    # project_id обязателен для scope=project и запрещён для company (CHECK: ровно одно из двух).
    r = proj.eng.cmd("kb.create", {"id": new_id(), "scope": "project", "kind": "note", "code": "Б-1",
                                   "title": "Без проекта", "body_md": "…"})
    assert r["status"] == "rejected"


def test_vendors_online_only_and_rights(world):
    # В стенде роли procurement нет у предсозданных — создаём пользователя закупки.
    uid = world.make_user("proc1", "procurement")
    proc = world.login("proc1")
    v = proc.ok("vendor.create", {"id": new_id(), "name": "ООО Монтажстрой", "kind": "contractor",
                                  "inn": "7723456789", "contacts": {"phone": "+7 900 000-00-00"},
                                  "rating_note": "Работали в 2025 — без нареканий"})["vendor"]
    assert (v["kind_label"], v["contacts"]["phone"], v["active"]) == ("Подрядчик", "+7 900 000-00-00", True)
    # Правит только закупка.
    r = world.login("eng1").cmd("vendor.update", {"id": v["id"], "name": "X"}, expected_version=1)
    assert r["status"] == "denied"
    # ИНН проверяется по формату.
    r = proc.cmd("vendor.create", {"id": new_id(), "name": "Y", "kind": "supplier", "inn": "123"})
    assert r["status"] == "rejected" and r["code"] == "invalid_inn"
    # Онлайн-only: в каталоге класс online_required (очередь не принимает — это проверяет клиент; сервер сам
    # не различает «онлайн/офлайн», контракт читаем из каталога).
    from vector.core.contracts import Contracts
    assert Contracts.load().offline_class("vendor.create") == "online_required"
    # Архив и возврат.
    v2 = proc.ok("vendor.archive", {"id": v["id"], "active": False}, expected_version=1)["vendor"]
    assert v2["active"] is False
    proc.ok("vendor.archive", {"id": v["id"], "active": True}, expected_version=2)
    # Читают все вошедшие.
    assert any(x["id"] == v["id"] for x in world.login("eng1").query("pull")["result"]["vendors"])


def test_equipment_catalog(world):
    lead, tech = world.login("lead1"), world.login("eng1")
    # Оборудование ведут ведущий инженер и технолог.
    r = tech_or_denied(world, tech)
    assert r == "denied"
    e = lead.ok("equipment.create", {"id": new_id(), "code": "ПЛК-100", "name": "Контроллер",
                                     "category": "КИПиА", "manufacturer": "ОВЕН",
                                     "params": {"di": 16, "do": 8}})["equipment"]
    assert (e["params"]["di"], e["kind"] if "kind" in e else "ok") == (16, "ok")
    # Код уникален.
    r = lead.cmd("equipment.create", {"id": new_id(), "code": "ПЛК-100", "name": "Дубль", "category": "КИПиА"})
    assert r["status"] == "rejected" and r["code"] == "code_taken"
    # Правка и архив с версиями.
    e2 = lead.ok("equipment.update", {"id": e["id"], "params": {"di": 16, "do": 8, "ai": 4}},
                 expected_version=1)["equipment"]
    assert e2["params"]["ai"] == 4 and e2["version"] == 2
    lead.ok("equipment.archive", {"id": e["id"], "active": False}, expected_version=2)
    assert next(x for x in tech.query("pull")["result"]["equipment"] if x["id"] == e["id"])["active"] is False


def tech_or_denied(world, tech):
    from conftest import new_id as nid
    return tech.cmd("equipment.create", {"id": nid(), "code": "X-1", "name": "X", "category": "C"})["status"]
