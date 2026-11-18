"""Качество на клиенте (Class A, SQLite; D-39): рекламация и запрос на изменение — офлайн через очередь, решения —
только онлайн; черновик извещения — очередь с цепочкой версий и без пустых сохранений; представление quality_view
(флаги «другой человек» с причиной, варианты внедрения), проверка форм без сервера.
"""
import sys
from pathlib import Path

import pytest

# Стенд производства и экземпляр — из серверных тестов (каталог tests/server добавляется в путь импорта).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from conftest import new_id  # noqa: E402
from test_lifecycle import closed_wo, register  # noqa: E402
from test_offline_sync import BACKENDS, FAST_KDF, SERVER, mk  # noqa: E402,F401  (фикстура mk и общие константы)
from test_production import pd  # noqa: E402,F401  (фикстура)
from test_stock import st  # noqa: E402,F401  (фикстура)
from test_tasks_documents import proj  # noqa: E402,F401  (фикстура)
from vector.client import quality_view as qv  # noqa: E402

_ = (BACKENDS, FAST_KDF, SERVER)


@pytest.fixture
def made(world, pd, mk):  # noqa: F811
    """Экземпляр изделия (наряд на 1 шт.) и клиент РП проекта (rp1) после первого pull. Возвращает (экземпляр, клиент)."""
    wo = closed_wo(world, pd, qty=1)
    inst = register(pd.st.proj.rp, wo, "2026", "001")["result"]["product_instances"][0]
    world.clock.advance(seconds=61)       # ограничение частоты входа
    c = mk("rp1")
    c.pull()
    return inst, c


def view(c):
    """Представление раздела «Качество» из кэша клиента c."""
    return qv.quality_view(c.list_local("claim"), c.list_local("change_request"), c.list_local("change_order"),
                           c.list_local("product_instance"), c.get_meta("quality_refs") or [], c.list_local("project"),
                           c.list_local("kit"), c.get_meta("assignments") or [], c.get_meta("project_deputies") or [],
                           {p["id"]: p["display_name"] for p in c.get_meta("people") or []},
                           c.get_meta("me") or c.get_meta("session") or {}, c.t.online, c.get_meta("last_pull") or "")


def test_claim_offline_register_online_decisions_and_independent_check(world, pd, made):  # noqa: F811
    inst, c = made
    u = pd.st.proj
    v = view(c)
    assert v["sees"] and v["may_claim"] and [o["id"] for o in v["instance_options"]] == [inst["id"]]
    # Регистрация без связи — в очередь; в списке видна как «не отправлена».
    c.t.online = False
    cid = new_id()
    r = c.execute("claim.register", {"id": cid, "instance_id": inst["id"], "received_date": "2026-01-10",
                                     "source": "customer", "description": "Не включается вводной автомат",
                                     "severity": "major", "response_due": "2026-02-10"})
    assert r.status == "queued"
    row = view(c)["claims"][0]
    assert row["pending"] and row["label"] == "Рекламация (не отправлена)" and not row["can_start"]
    # Решения без связи не ставятся в очередь (при неотправленной очереди — «сначала отправьте очередь»).
    r = c.execute("claim.start_analysis", {"id": cid, "responsible_id": u.eng.user_id}, 1)
    assert r.status in ("refused_offline", "blocked") and len(c.queue()) == 1
    c.t.online = True
    assert c.sync().sent == 1
    row = view(c)["claims"][0]
    assert (row["label"], row["status_label"], row["can_start"]) == ("Рекламация № 1", "Зарегистрирована", True)
    assert row["instance_label"] == "АТХ № 001 (серия 2026)" and row["source_label"] == "От заказчика"
    assert [t["label"] for t in view(c)["teams"][row["project_id"]]] == ["ENG1", "GIP1", "RP1"]   # команда проекта

    c.execute("claim.start_analysis", {"id": cid, "responsible_id": u.eng.user_id}, row["version"])
    c.pull()
    row = view(c)["claims"][0]
    assert row["can_plan"] and row["first_plan"] and row["responsible"]
    aid = new_id()
    r = c.execute("claim.plan", {"id": cid, "cause": "Брак партии автоматов", "category": "purchased",
                                 "actions": [{"id": aid, "kind": "corrective", "description": "Заменить автомат",
                                              "owner_id": u.eng.user_id, "due_date": "2099-01-01"}]}, row["version"])
    assert r.status == "accepted", r
    c.pull()
    row = view(c)["claims"][0]
    assert row["status"] == "action" and not row["can_submit"] and row["submit_block"] == "Не выполнено действий: 1."
    a = row["actions"][0]
    assert a["can_done"] and a["owner"] and a["due_label"] and a["icon"] == "wrench"   # РП ведёт рекламацию
    u.eng.ok("claim.action_done", {"id": cid, "action_id": aid, "result": "Автомат заменён"})
    c.pull()
    row = view(c)["claims"][0]
    assert row["can_submit"] and row["actions"][0]["done"] and "Автомат заменён" == row["actions"][0]["result"]
    c.execute("claim.submit_check", {"id": cid}, row["version"])
    c.pull()
    row = view(c)["claims"][0]
    # РП составлял план и отправлял на проверку — проверять не может, и видит почему.
    assert row["may_check"] and not row["can_check"]
    assert "вы составляли план действий" in row["check_block"] and "вы отправили" in row["check_block"]
    assert [h["label"] for h in row["history"]] == ["Зарегистрирована", "Начат анализ причины", "Утверждён план действий",
                                                    "Действие выполнено", "Отправлена на проверку"]


def test_ecr_offline_eco_draft_chain_and_implement_options(world, pd, made, mk):  # noqa: F811
    inst, c = made
    u = pd.st.proj
    # Запрос на изменение — офлайн от инженера (участник проекта).
    world.clock.advance(seconds=61)
    ce = mk("eng1")
    ce.pull()
    assert view(ce)["may_ecr"] and not view(ce)["may_claim"]
    ce.t.online = False
    rid = new_id()
    pid = view(ce)["ecr_projects"][0]["id"]
    assert ce.execute("ecr.submit", {"id": rid, "project_id": pid, "title": "Герметичный разъём", "reason": "Влага",
                                     "proposal": "Заменить разъём"}).status == "queued"
    ce.t.online = True
    assert ce.sync().sent == 1
    c.pull()
    r = view(c)["ecrs"][0]
    assert r["can_assess"] and not r["may_decide"]                     # РП оценивает, решает ГИП
    c.execute("ecr.assess", {"id": rid, "impact": "Схема; один экземпляр"}, r["version"])
    e = u.gip.ok("ecr.decide", {"id": rid, "decision": "accept", "reason": "Нужно"}, expected_version=2)["change_request"]
    assert e["status"] == "accepted"
    c.pull()
    r = view(c)["ecrs"][0]
    assert r["can_eco"] and r["status_label"] == "Принят"
    refs = view(c)["ref_options"]
    assert refs["tech_process"], refs                                   # утверждённый ТП проекта
    tp = refs["tech_process"][0]
    # Черновик извещения — офлайн: создание, затем правка с цепочкой версий; без изменений — не в очередь.
    c.t.online = False
    oid = new_id()
    body = {"id": oid, "ecr_id": rid, "title": "", "description": "Заменить разъём",
            "items": [{"kind": "tech_process", "tp_id": tp["id"]}]}
    body.pop("title")
    assert c.save_eco_draft(body).status == "queued"
    o = view(c)["ecos"][0]
    assert o["pending"] and o["items"][0]["label"].startswith("Техпроцесс АТХ, редакция")   # подпись из справочника
    assert c.save_eco_draft({**body, "description": "Заменить разъём на герметичный"}).status == "queued"
    q = c.db.execute("SELECT command, expected_version FROM outbox WHERE state='pending' ORDER BY seq").fetchall()
    assert [tuple(x) for x in q] == [("eco.create", None), ("eco.save", 1)]
    assert c.save_eco_draft({**body, "description": "Заменить разъём на герметичный"}).response == {"unchanged": True}
    c.t.online = True
    assert c.sync().sent == 2
    o = view(c)["ecos"][0]
    assert (o["label"], o["status_label"], o["can_edit"]) == ("Извещение № 1", "Черновик", True)
    assert not o["may_approve"]                                         # РП не утверждает
    # ГИП не правил — может утвердить; составитель-ГИП не смог бы (approve_block).
    world.clock.advance(seconds=61)
    cg = mk("gip1")
    cg.pull()
    og = view(cg)["ecos"][0]
    assert og["may_approve"] and og["can_approve"] and og["approve_block"] == ""
    assert cg.execute("eco.approve", {"id": oid}, og["version"]).status == "accepted"
    c.pull()
    o = view(c)["ecos"][0]
    assert o["can_implement"] and o["items"][0]["tp_options"] == []        # новой утверждённой редакции нет
    assert "технолог" in o["items"][0]["hint"]
    assert [i["label"] for i in o["instances"]] == ["АТХ № 001 (серия 2026)"]   # собран по затронутой редакции


def test_quality_check_rules_without_server():
    assert qv.quality_check("claim", {"received_date": "2026-01-10", "source": "customer", "description": "x",
                                      "instance_id": "i", "severity": "minor", "response_due": "2026-01-20"})["ok"]
    r = qv.quality_check("claim", {"scope": "batch", "received_date": "2026-01-10", "source": "internal", "description": "x",
                                   "severity": "minor"})
    assert (r["ok"], r["field"]) == (False, "work_order_id")
    r = qv.quality_check("claim", {"received_date": "10.01.2026", "source": "customer", "description": "x", "instance_id": "i"})
    assert not r["ok"] and r["field"] == "received_date"
    acts = [{"id": new_id(), "kind": "preventive", "description": "Входной контроль", "owner_id": new_id(), "due_date": "2099-01-01"}]
    r = qv.quality_check("actions", {"first": True, "cause": "Брак", "actions": acts})
    assert not r["ok"] and "корректирующее" in r["message"]
    r = qv.quality_check("actions", {"first": False, "actions": acts + [{"id": new_id(), "kind": "x", "description": "",
                                                                         "owner_id": new_id(), "due_date": ""}]})
    assert not r["ok"] and list(r["rows"]) == ["1"]
    assert not qv.quality_check("ecr", {"title": "", "reason": "x", "proposal": "y"})["ok"]
    assert not qv.quality_check("eco", {"description": "x", "items": []})["ok"]
    assert qv.quality_check("eco", {"description": "x", "items": [{"kind": "tech_process", "tp_id": new_id()}]})["ok"]


def test_otk_per_project_team_and_flags_without_server():
    """D-41 (ответ владельца на A-47, п. 4): ОТК — по проектам. В список ответственных/исполнителей попадают только
    участники проекта (ОТК помечен «(ОТК)», если назначен в ЭТОТ проект); вне своих проектов роль ОТК не даёт
    кнопок; старый формат quality_otk (общий список, D-40) клиент тоже понимает."""
    people = {"u1": "Иванов И. И.", "o1": "Егорова Л. В.", "o2": "Петров П. П."}
    assignments = [{"project_id": "P1", "user_id": "u1"}, {"project_id": "P1", "user_id": "o1"},
                   {"project_id": "P2", "user_id": "u1"}]
    otk = {"P1": ["o1"], "P2": ["o2"]}
    assert qv._team("P1", assignments, [], people, otk) == [{"id": "o1", "label": "Егорова Л. В. (ОТК)"},
                                                            {"id": "u1", "label": "Иванов И. И."}]
    # o2 — ОТК второго проекта, но не его участник по назначению → в выборе нет (сервер ответит not_project_member).
    assert [x["id"] for x in qv._team("P2", assignments, [], people, otk)] == ["u1"]
    assert qv._otk_of(["o1", "o2"], "P1") == {"o1", "o2"} and qv._otk_of(None, "P1") == set()
    # Роль ОТК действует, только если у сотрудника есть роль в проекте (назначение или замещение).
    assert qv._here({"otk"}, {"participant"}) == {"otk"} and qv._here({"otk", "dept_head"}, set()) == {"dept_head"}
    c = {"id": "c1", "status": "registered", "claim_no": 1, "project_id": "P1", "source": "internal", "severity": "minor"}
    assert qv._claim_row(c, "o1", {"participant"}, {"otk"}, True, people, "—")["manage"] is True
    assert qv._claim_row(c, "o2", set(), {"otk"}, True, people, "—")["manage"] is False
