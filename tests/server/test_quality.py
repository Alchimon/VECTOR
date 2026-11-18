"""Качество: рекламации → CAPA → проверка эффективности; запрос (ECR) → извещение (ECO) об изменении
(этап 6, сценарий С5 шаг 10; D-39).

Каждый тест закрепляет правило из server/quality.py (правила — vector.core.quality); запускается на SQLite и на
встроенном PostgreSQL. Кто есть кто — как в test_lifecycle: gip1 — ГИП, rp1 — РП, eng1 — участник (инженер),
lead1 — ведущий инженер (сначала вне команды проекта), head1 — руководитель отдела, tech1/tech2 — технологи,
prod1 — производство.
"""
import json

import pytest

from conftest import new_id
from test_lifecycle import closed_wo, register, today  # noqa: F401
from test_production import approved_tp, pd, pull, tp_draft  # noqa: F401
from test_stock import st  # noqa: F401
from test_tasks_documents import audit, make_doc, proj, upload  # noqa: F401
from vector.core import quality as qa
from vector.server import db


@pytest.fixture
def inst(world, pd):  # noqa: F811
    """Экземпляр изделия по закрытому наряду на 1 шт. (передан заказчику не обязательно)."""
    wo = closed_wo(world, pd, qty=1)
    return register(pd.st.proj.rp, wo, "2026", "001")["result"]["product_instances"][0]


def claim_of(s, inst, **kw):  # noqa: F811
    """claim.register от имени s (по умолчанию — рекламация заказчика с сегодняшней датой)."""
    p = {"id": new_id(), "instance_id": inst["id"], "received_date": kw.pop("received_date", None) or "2026-01-10",
         "source": "customer", "description": "Не включается вводной автомат", **kw}
    return s.cmd("claim.register", p)


def act(owner, kind="corrective", due="2099-01-01", what="Заменить автомат"):
    """Строка плана CAPA."""
    return {"id": new_id(), "kind": kind, "description": what, "owner_id": owner.user_id, "due_date": due}


# ====================================================================== рекламация и CAPA
def test_claim_cycle_rights_states_and_independent_effectiveness_check(world, pd, inst):  # noqa: F811
    u = pd.st.proj
    # Права (матрица 04): РП, ГИП проекта; резерв — руководитель отдела. Инженер, технолог, производство — нет.
    for s in (u.eng, pd.tech, pd.prod, u.lead):
        assert claim_of(s, inst)["status"] == "denied", s
    assert claim_of(u.rp, inst, source="x")["code"] == "invalid_source"
    assert claim_of(u.rp, inst, received_date=today(world, 5))["code"] == "date_in_future"
    assert claim_of(u.rp, inst, description=" ")["code"] == "required"
    c = u.rp.ok("claim.register", {"id": new_id(), "instance_id": inst["id"], "received_date": today(world),
                                   "source": "customer", "description": "Не включается вводной автомат",
                                   "reference": "Письмо № 17"})["claim"]
    assert (c["claim_no"], c["status"], c["instance_label"], c["reference"]) == (1, "registered", "АТХ № 001 (серия 2026)", "Письмо № 17")
    assert [n["kind"] for n in pull(u.gip)["notifications"]].count("claim_new") == 1

    # Анализ: ответственный — участник проекта.
    assert u.rp.cmd("claim.start_analysis", {"id": c["id"], "responsible_id": u.lead.user_id},
                    expected_version=1)["code"] == "not_project_member"
    c = u.rp.ok("claim.start_analysis", {"id": c["id"], "responsible_id": u.eng.user_id}, expected_version=1)["claim"]
    assert (c["status"], c["responsible_id"]) == ("analysis", u.eng.user_id)
    assert any(n["kind"] == "claim_assigned" for n in pull(u.eng)["notifications"])
    # Повтор из старого состояния — видимый конфликт (а не молчаливое применение).
    assert u.rp.cmd("claim.start_analysis", {"id": c["id"], "responsible_id": u.eng.user_id},
                    expected_version=2)["code"] == "claim_state"

    # План: причина обязательна, нужно корректирующее действие, исполнители — участники проекта, срок не прошёл.
    v = c["version"]
    assert u.rp.cmd("claim.plan", {"id": c["id"], "actions": [act(u.eng)]}, expected_version=v)["code"] == "required"
    assert u.rp.cmd("claim.plan", {"id": c["id"], "cause": "Брак партии автоматов", "actions": [act(u.eng, "preventive")]},
                    expected_version=v)["code"] == "no_corrective"
    assert u.rp.cmd("claim.plan", {"id": c["id"], "cause": "Брак", "actions": [act(u.lead)]},
                    expected_version=v)["code"] == "not_project_member"
    assert u.rp.cmd("claim.plan", {"id": c["id"], "cause": "Брак", "actions": [act(u.eng, due="2020-01-01")]},
                    expected_version=v)["code"] == "due_in_past"
    r = u.rp.cmd("claim.plan", {"id": c["id"], "cause": "Брак", "actions": [act(u.eng, kind="x"), act(u.eng, due="")]},
                 expected_version=v)
    assert r["code"] == "actions_invalid" and [p["row"] for p in r["details"]["problems"]] == [1, 2]
    a1, a2 = act(u.eng), act(u.gip, "preventive", what="Входной контроль автоматов")
    c = u.rp.ok("claim.plan", {"id": c["id"], "cause": "Брак партии автоматов", "actions": [a1, a2]}, expected_version=v)["claim"]
    assert (c["status"], c["cause"], [a["kind"] for a in c["actions"]]) == ("action", "Брак партии автоматов",
                                                                           ["corrective", "preventive"])
    assert any(n["kind"] == "capa_assigned" for n in pull(u.eng)["notifications"])

    # Отметить выполненным — исполнитель или тот, кто ведёт рекламацию; посторонний — нет.
    assert u.rp.cmd("claim.submit_check", {"id": c["id"]}, expected_version=c["version"])["code"] == "actions_open"
    assert pd.prod.cmd("claim.action_done", {"id": c["id"], "action_id": a1["id"], "result": "x"})["status"] == "denied"
    assert u.eng.cmd("claim.action_done", {"id": c["id"], "action_id": a2["id"], "result": "x"})["code"] == "not_action_owner"
    u.eng.ok("claim.action_done", {"id": c["id"], "action_id": a1["id"], "result": "Автомат заменён"})
    assert u.eng.cmd("claim.action_done", {"id": c["id"], "action_id": a1["id"], "result": "ещё"})["code"] == "action_done"
    c = u.gip.ok("claim.action_done", {"id": c["id"], "action_id": a2["id"], "result": "Введён входной контроль"})["claim"]
    assert c["doers"] == sorted([u.eng.user_id, u.gip.user_id]) and c["planners"] == [u.rp.user_id]
    c = u.rp.ok("claim.submit_check", {"id": c["id"]}, expected_version=c["version"])["claim"]
    assert (c["status"], c["submitted_by"]) == ("effectiveness_check", u.rp.user_id)
    # Уведомление о проверке — тем, кто может проверять (руководителю отдела), не участвовавшим (ГИП выполнял).
    assert any(n["kind"] == "claim_check" for n in pull(pd.head)["notifications"])
    assert not any(n["kind"] == "claim_check" for n in pull(u.gip)["notifications"])

    # Проверка эффективности — независимый: не составлял план (РП), не выполнял (ГИП), не отправлял (РП).
    chk = {"id": c["id"], "effective": True, "evidence": "Осмотр на объекте"}
    for s, why in ((u.rp, "вы составляли план действий"), (u.gip, "вы выполняли действия")):
        r = s.cmd("claim.check", chk, expected_version=c["version"])
        assert r["status"] == "denied" and r["code"] == "checker_not_independent" and why in r["details"]["reasons"]
    assert u.eng.cmd("claim.check", chk, expected_version=c["version"])["status"] == "denied"     # нет права
    assert pd.head.cmd("claim.check", {**chk, "evidence": ""}, expected_version=c["version"])["code"] == "required"
    # «Не помогло» — запись проверки и возврат к действиям.
    c = pd.head.ok("claim.check", {**chk, "effective": False, "evidence": "Отказ повторился через неделю"},
                   expected_version=c["version"])["claim"]
    assert (c["status"], c["submitted_by"], [x["effective"] for x in c["checks"]]) == ("action", None, [False])
    assert c["closure_note"] is None
    # Новые действия, выполнение, отправка ГИП — и проверка тем же руководителем отдела (он не участвовал).
    a3 = act(u.eng, what="Заменить партию автоматов у поставщика")
    c = u.rp.ok("claim.plan", {"id": c["id"], "actions": [a3]}, expected_version=c["version"])["claim"]
    assert [a["seq"] for a in c["actions"]] == [1, 2, 3] and c["cause"] == "Брак партии автоматов"
    u.eng.ok("claim.action_done", {"id": c["id"], "action_id": a3["id"], "result": "Партия заменена"})
    c = pull(u.rp)["claims"][0]
    c = u.gip.ok("claim.submit_check", {"id": c["id"]}, expected_version=c["version"])["claim"]
    c = pd.head.ok("claim.check", chk, expected_version=c["version"])["claim"]
    assert (c["status"], [x["effective"] for x in c["checks"]]) == ("closed", [False, True])
    # Закрытие не доказывает устранение причины — это сказано в карточке.
    assert c["closure_note"] == qa.CLOSURE_NOTE
    assert [h["decision"] for h in c["history"]] == ["registered", "analysis_started", "planned", "action_done", "action_done",
                                                     "submitted_check", "check_not_effective", "actions_added", "action_done",
                                                     "submitted_check", "check_effective"]
    assert any(n["kind"] == "claim_checked" for n in pull(u.rp)["notifications"])
    # Закрытая рекламация не принимает действий; записи проверки и журнала неизменяемы.
    assert u.rp.cmd("claim.plan", {"id": c["id"], "actions": [act(u.eng)]}, expected_version=c["version"])["code"] == "claim_state"
    with world.svc.engine.begin() as conn:
        with pytest.raises(Exception):
            conn.execute(db.effectiveness_checks.update().values(effective=True))
    with world.svc.engine.begin() as conn:
        with pytest.raises(Exception):
            conn.execute(db.quality_decisions.delete())
    ev = json.loads([a for a in audit(world, "claim.check") if a["result"] == "accepted"][-1]["details_json"])
    assert ev["event"] == "ClaimClosed"


def test_claim_dept_head_reserve_and_history_query(world, pd, inst):  # noqa: F811
    # Руководитель отдела — резерв: регистрирует и ведёт рекламацию без роли в проекте (A-37).
    c = claim_of(pd.head, inst)["result"]["claim"]
    assert c["claim_no"] == 1 and c["created_by"] == pd.head.user_id
    h = pd.st.proj.eng.query("history", object_type="claim", object_id=c["id"])
    assert h["status"] == "accepted" and h["result"][0]["command"] == "claim.register"
    assert pd.prod.query("history", object_type="claim", object_id=c["id"])["status"] == "denied"
    # Технолог видит рекламации (изменения касаются техпроцессов), производство — нет.
    assert [x["id"] for x in pull(pd.tech)["claims"]] == [c["id"]]
    assert pull(pd.prod)["claims"] == []


# ====================================================================== ECR и ECO
def _doc(world, proj, code="АТХ-СХ"):  # noqa: F811
    """Документ проекта с одной версией (для строки извещения)."""
    did = make_doc(proj, code)
    up = upload(proj.eng, proj.pid, b"%PDF eco doc v1 " + code.encode())
    proj.eng.ok("document.add_version", {"id": did, "sha256": up["sha256"], "file_name": f"{code}.pdf"}, expected_version=1)
    return did


def ecr_of(s, proj, **kw):  # noqa: F811
    """ecr.submit от имени s."""
    return s.cmd("ecr.submit", {"id": new_id(), "project_id": proj.pid, "title": "Заменить тип автомата",
                                "reason": "Рекламация: отказ автомата", "proposal": "Автомат другой серии", **kw})


def test_ecr_rights_assessment_and_four_eyes_decision(world, pd, inst):  # noqa: F811
    u = pd.st.proj
    c = claim_of(u.rp, inst)["result"]["claim"]
    # Подаёт любой участник проекта; ведущий инженер вне команды, технолог, производство — нет.
    for s in (u.lead, pd.tech, pd.prod):
        assert ecr_of(s, u)["status"] == "denied", s
    assert ecr_of(u.eng, u, title=" ")["code"] == "required"
    assert ecr_of(u.eng, u, claim_id=new_id())["code"] == "claim_invalid"
    r = u.eng.ok("ecr.submit", {"id": new_id(), "project_id": u.pid, "title": "Заменить тип автомата", "reason": "Отказ",
                                "proposal": "Другая серия", "kit_id": u.kit, "claim_id": c["id"]})["change_request"]
    assert (r["ecr_no"], r["status"], r["claim_no"], r["kit_code"]) == (1, "submitted", 1, "АТХ")
    assert any(n["kind"] == "ecr_new" for n in pull(u.gip)["notifications"])
    # Связь видна и в рекламации.
    assert pull(u.rp)["claims"][0]["change_requests"][0]["ecr_no"] == 1
    # Оценка — РП или ГИП; решение — только ГИП и только после оценки.
    assert u.gip.cmd("ecr.decide", {"id": r["id"], "decision": "accept", "reason": "Да"}, expected_version=1)["code"] == "ecr_state"
    assert u.eng.cmd("ecr.assess", {"id": r["id"], "impact": "x"}, expected_version=1)["status"] == "denied"
    r = u.rp.ok("ecr.assess", {"id": r["id"], "impact": "Схема Э3, ТП сборки; 2 дня"}, expected_version=1)["change_request"]
    assert (r["status"], r["assessed_by"]) == ("assessed", u.rp.user_id)
    assert any(n["kind"] == "ecr_assessed" for n in pull(u.gip)["notifications"])
    assert u.rp.cmd("ecr.decide", {"id": r["id"], "decision": "accept", "reason": "Да"}, expected_version=2)["status"] == "denied"
    assert u.gip.cmd("ecr.decide", {"id": r["id"], "decision": "maybe", "reason": "Да"}, expected_version=2)["code"] == "invalid_decision"
    r = u.gip.ok("ecr.decide", {"id": r["id"], "decision": "accept", "reason": "Причина подтверждена"},
                 expected_version=2)["change_request"]
    assert (r["status"], r["decided_by"]) == ("accepted", u.gip.user_id)
    assert any(n["kind"] == "ecr_decided" for n in pull(u.eng)["notifications"])
    # «Четыре глаза»: ГИП не решает по своему запросу.
    own = ecr_of(u.gip, u)["result"]["change_request"]
    u.rp.ok("ecr.assess", {"id": own["id"], "impact": "Мало"}, expected_version=1)
    assert u.gip.cmd("ecr.decide", {"id": own["id"], "decision": "reject", "reason": "Нет"}, expected_version=2)["code"] == "four_eyes"


def _accepted_ecr(u):
    """Принятый запрос (подал инженер, оценил РП, принял ГИП)."""
    r = ecr_of(u.eng, u)["result"]["change_request"]
    u.rp.ok("ecr.assess", {"id": r["id"], "impact": "Схема и ТП"}, expected_version=1)
    return u.gip.ok("ecr.decide", {"id": r["id"], "decision": "accept", "reason": "Да"}, expected_version=2)["change_request"]


def test_eco_exact_versions_four_eyes_implementation_and_verification(world, pd, inst):  # noqa: F811
    u = pd.st.proj
    did = _doc(world, u)
    tp1 = pull(u.rp)["tech_processes"][0]          # редакция 1 — по ней собран экземпляр
    ecr = _accepted_ecr(u)
    pending = ecr_of(u.eng, u)["result"]["change_request"]
    items = [{"kind": "document", "document_id": did, "from_version": 1}, {"kind": "tech_process", "tp_id": tp1["id"]}]
    base = {"ecr_id": ecr["id"], "description": "Автомат другой серии: схема и техпроцесс сборки", "items": items}
    # Только по принятому запросу; составляют РП или ГИП; версии — точные и существующие, без повторов.
    assert u.rp.cmd("eco.create", {**base, "id": new_id(), "ecr_id": pending["id"]})["code"] == "ecr_not_accepted"
    assert u.eng.cmd("eco.create", {**base, "id": new_id()})["status"] == "denied"
    assert u.rp.cmd("eco.create", {**base, "id": new_id(), "items": [{**items[0], "from_version": 3}]})["code"] == "version_not_found"
    assert u.rp.cmd("eco.create", {**base, "id": new_id(), "items": [items[0], items[0]]})["code"] == "item_duplicate"
    assert u.rp.cmd("eco.create", {**base, "id": new_id(), "items": []})["code"] == "required"
    o = u.rp.ok("eco.create", {**base, "id": new_id()})["change_order"]
    assert (o["eco_no"], o["status"], o["title"], o["ecr_no"]) == (1, "draft", ecr["title"], ecr["ecr_no"])
    assert o["items"][0]["code"] == "АТХ-СХ" and len(o["items"][0]["sha256"]) == 64 and o["items"][1]["revision_no"] == 1
    # Связь с экземплярами: собран по затронутой редакции ТП.
    assert [(i["label"], i["why"]) for i in o["instances"]] == [("АТХ № 001 (серия 2026)", ["собран по техпроцессу редакции 1"])]
    assert any(n["kind"] == "eco_approval" for n in pull(u.gip)["notifications"])
    r = u.rp.cmd("eco.create", {**base, "id": new_id()})
    assert r["status"] == "conflict" and r["code"] == "eco_exists"
    o = u.rp.ok("eco.save", {"id": o["id"], "description": "Уточнено: серия С32"}, expected_version=1)["change_order"]
    assert u.rp.cmd("eco.save", {"id": o["id"], "description": "Уточнено: серия С32"}, expected_version=2)["result"]["unchanged"]
    assert u.rp.cmd("eco.approve", {"id": o["id"]}, expected_version=2)["status"] == "denied"
    # «Четыре глаза»: ГИП не утверждает извещение, которое составлял или правил сам.
    own = u.gip.ok("eco.create", {**base, "id": new_id(), "ecr_id": _accepted_ecr(u)["id"]})["change_order"]
    assert u.gip.cmd("eco.approve", {"id": own["id"]}, expected_version=1)["code"] == "four_eyes"
    o = u.gip.ok("eco.approve", {"id": o["id"]}, expected_version=2)["change_order"]
    assert (o["status"], o["approved_by"]) == ("approved", u.gip.user_id)
    ev = json.loads([a for a in audit(world, "eco.approve") if a["result"] == "accepted"][-1]["details_json"])
    assert ev["event"] == "ECOReleased"
    # Утверждённое не правится.
    assert u.rp.cmd("eco.save", {"id": o["id"], "description": "x"}, expected_version=3)["code"] == "eco_state"

    # Внедрение: новая версия документа существует и новее; новая редакция ТП того же изделия, утверждена.
    v = o["version"]
    res = lambda dv, tp: {"id": o["id"], "results": [{"index": 0, "to_version": dv}, {"index": 1, "to_tp_id": tp}]}  # noqa: E731
    assert u.rp.cmd("eco.implement", {"id": o["id"], "results": [{"index": 0, "to_version": 2}]},
                    expected_version=v)["code"] == "results_incomplete"
    assert u.rp.cmd("eco.implement", res(1, tp1["id"]), expected_version=v)["code"] == "version_not_newer"
    assert u.rp.cmd("eco.implement", res(2, tp1["id"]), expected_version=v)["code"] == "version_not_found"
    up = upload(u.eng, u.pid, b"%PDF eco doc v2")
    u.eng.ok("document.add_version", {"id": did, "sha256": up["sha256"], "file_name": "АТХ-СХ-2.pdf"}, expected_version=2)
    assert u.rp.cmd("eco.implement", res(2, tp1["id"]), expected_version=v)["code"] == "tp_not_newer"
    tp2 = tp_draft(pd)["result"]["tech_process"]
    assert u.rp.cmd("eco.implement", res(2, tp2["id"]), expected_version=v)["code"] == "tp_not_approved"
    tp2 = pd.tech2.ok("tp.approve", {"id": tp2["id"]}, expected_version=tp2["version"])["tech_process"]
    o = u.rp.ok("eco.implement", res(2, tp2["id"]), expected_version=v)["change_order"]
    assert o["status"] == "implemented" and o["results"][0]["sha256"] == up["sha256"] and o["results"][1]["revision_no"] == 2
    # Проверка внедрения — не тот, кто внедрял; заметка обязательна; руководитель отдела — резерв.
    assert u.rp.cmd("eco.verify", {"id": o["id"], "note": "Проверено"}, expected_version=o["version"])["code"] == "verifier_not_independent"
    assert u.gip.cmd("eco.verify", {"id": o["id"], "note": ""}, expected_version=o["version"])["code"] == "required"
    o = pd.head.ok("eco.verify", {"id": o["id"], "note": "Схема выдана на участок, ТП ред. 2 применяется"},
                   expected_version=o["version"])["change_order"]
    assert (o["status"], o["verified_by"]) == ("verified", pd.head.user_id)
    assert [h["decision"] for h in o["history"]] == ["created", "saved", "approved", "implemented", "verified"]
    assert any(n["kind"] == "eco_verified" for n in pull(u.rp)["notifications"])
    # Запрос показывает своё извещение; справочник для строк извещения — только тем, кто составляет.
    assert pull(u.eng)["change_requests"][0]["change_order"]["eco_no"] == 1 and o["eco_no"] == 1
    assert {x["kind"] for x in pull(u.rp)["quality_refs"]} == {"document", "tech_process"}
    assert pull(u.eng)["quality_refs"] == []


def test_quality_rules_core():
    """Правила vector.core.quality без сервера (их же использует клиент)."""
    with pytest.raises(qa.QualityError) as e:
        qa.parse_claim({"source": "customer", "received_date": "2026-01-01", "description": ""})
    assert e.value.code == "required"
    assert qa.checker_conflicts("a", planners=["a"], doers=["b"], submitter="a") == ["вы составляли план действий",
                                                                                 "вы отправили рекламацию на проверку"]
    assert qa.checker_conflicts("c", planners=["a"], doers=["b"], submitter="a") == []
    assert qa.has_corrective([{"kind": "preventive"}]) is False
    items = qa.parse_eco_items([{"kind": "document", "document_id": "d", "from_version": 2}])
    with pytest.raises(qa.QualityError) as e:
        qa.parse_eco_results(items, [{"index": 0, "to_version": 2}])
    assert e.value.code == "version_not_newer"
    assert qa.parse_check({"effective": False, "evidence": "Отказ"}) == {"effective": False, "evidence": "Отказ"}


def test_quality_tables_added_in_v18():
    from vector.server import migrate as mg
    assert [t.name for t in mg.ADDED_IN[18]] == ["claims", "claim_actions", "effectiveness_checks", "change_requests",
                                                 "change_orders", "quality_decisions"]
    assert db.SCHEMA_VERSION == 18 and 17 in mg.STEPS
