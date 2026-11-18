"""Качество, расширение D-40 (ответ владельца на A-46): роль ОТК (отдел технического контроля), рекламации без
заводского номера (на партию — наряд — или на проект), классификация дефекта, срок ответа заказчику, ответы
заказчику и напоминания о сроке.

Каждый тест закрепляет правило из server/quality.py (правила — vector.core.quality); запускается на SQLite и на
встроенном PostgreSQL. Кто есть кто — как в test_quality: gip1 — ГИП, rp1 — РП, eng1 — участник (инженер), head1 —
руководитель отдела, tech1/tech2 — технологи, prod1 — производство; otk1/otk2 — ОТК (вне команды проекта).
"""
from dataclasses import dataclass
from datetime import timedelta

import pytest

from conftest import new_id
from test_lifecycle import closed_wo, register, today  # noqa: F401
from test_production import approved_tp, pd, pull, release, tp_draft  # noqa: F401
from test_stock import st  # noqa: F401
from test_tasks_documents import audit, make_doc, proj, upload  # noqa: F401
from vector.core import quality as qa
from vector.server import db


@dataclass
class Q:
    """Набор теста: закрытый наряд на 2 шт. (зарегистрирован один экземпляр), экземпляр, сеансы ОТК."""
    wo: dict
    inst: dict
    otk: object
    otk2: object


@pytest.fixture
def q(world, pd):  # noqa: F811
    """Наряд на 2 изделия закрыт, зарегистрирован экземпляр № 001 (второй — без номера); два сотрудника ОТК."""
    wo = closed_wo(world, pd, qty=2)
    inst = register(pd.st.proj.rp, wo, "2026", "001")["result"]["product_instances"][0]
    world.clock.advance(seconds=61)          # ограничение частоты входа
    for login in ("otk1", "otk2"):
        world.make_user(login, "otk")
    world.clock.advance(seconds=61)
    return Q(wo, inst, world.login("otk1"), world.login("otk2"))


def reg(s, **kw):
    """claim.register от имени s: по умолчанию — рекламация заказчика «значительный», срок ответа — через 10 дней."""
    p = {"id": new_id(), "received_date": "2026-01-10", "source": "customer", "description": "Не включается вводной автомат",
         "severity": "major", **kw}
    if p["source"] == "customer" and "response_due" not in p:
        p["response_due"] = "2026-01-20"
    return s.cmd("claim.register", {k: v for k, v in p.items() if v is not None})


def kinds(s):
    """Виды уведомлений пользователя (pull)."""
    return [n["kind"] for n in pull(s)["notifications"]]


# ====================================================================== ОТК
def test_otk_registers_leads_and_checks_claims_but_does_not_decide_changes(world, pd, q):  # noqa: F811
    u = pd.st.proj
    # ОТК видит качество, изделия и наряды всех проектов — без роли в проекте и без денег (calc/цены — нет).
    p = pull(q.otk)
    assert [x["id"] for x in p["product_instances"]] == [q.inst["id"]]
    assert [x["id"] for x in p["work_orders"]] == [q.wo["id"]]
    assert p["projects"] == []                                     # карточка проекта (и расчёты) ОТК не нужна
    assert [(x["code"], x["can_claim"], x["can_ecr"]) for x in p["quality_projects"]] == [("П-100", True, True)]
    assert sorted(p["quality_otk"]) == sorted([q.otk.user_id, q.otk2.user_id])
    assert [b["id"] for b in p["claim_batches"]] == [q.wo["id"]] and p["claim_batches"][0]["instances"] == 1
    # Регистрирует рекламацию; новость приходит РП, ГИП и другому сотруднику ОТК.
    c = reg(q.otk, instance_id=q.inst["id"])["result"]["claim"]
    assert (c["claim_no"], c["scope"], c["severity_label"], c["deadline"]) == (1, "instance", "Значительный", "overdue")
    assert "claim_new" in kinds(u.rp) and "claim_new" in kinds(q.otk2) and "claim_new" not in kinds(q.otk)
    # Ответственный за анализ и исполнитель действия — может быть сотрудник ОТК (не участник проекта).
    c = u.rp.ok("claim.start_analysis", {"id": c["id"], "responsible_id": q.otk.user_id}, expected_version=1)["claim"]
    a = {"id": new_id(), "kind": "corrective", "description": "Заменить автомат", "owner_id": q.otk.user_id,
         "due_date": "2099-01-01"}
    c = q.otk.ok("claim.plan", {"id": c["id"], "cause": "Брак автомата", "category": "purchased", "actions": [a]},
                 expected_version=c["version"])["claim"]
    c = q.otk.ok("claim.action_done", {"id": c["id"], "action_id": a["id"], "result": "Заменён"})["claim"]
    c = u.rp.ok("claim.submit_check", {"id": c["id"]}, expected_version=c["version"])["claim"]
    assert "claim_check" in kinds(q.otk2)                           # проверяющим — и ОТК
    # Правило «другой человек» действует и для ОТК: otk1 составлял план и выполнял — не проверяет; otk2 — может.
    assert q.otk.cmd("claim.check", {"id": c["id"], "effective": True, "evidence": "Испытан"},
                     expected_version=c["version"])["code"] == "checker_not_independent"
    c = q.otk2.ok("claim.check", {"id": c["id"], "effective": True, "evidence": "Испытан, протокол № 5"},
                  expected_version=c["version"])["claim"]
    assert c["status"] == "closed" and c["checks"][0]["authority"].startswith("global_role:otk")
    # Запрос на изменение ОТК подаёт, но решение — за ГИП проекта; утверждение извещения — тоже.
    r = q.otk.ok("ecr.submit", {"id": new_id(), "project_id": u.pid, "title": "Входной контроль автоматов",
                                "reason": "Рекламация № 1", "proposal": "Добавить операцию контроля", "claim_id": c["id"]})
    e = u.rp.ok("ecr.assess", {"id": r["change_request"]["id"], "impact": "Техпроцесс"}, expected_version=1)["change_request"]
    assert q.otk2.cmd("ecr.decide", {"id": e["id"], "decision": "accept", "reason": "Да"},
                      expected_version=e["version"])["status"] == "denied"
    # Производство и инженер вне ОТК рекламации на партию не регистрируют.
    assert reg(pd.prod, scope="batch", work_order_id=q.wo["id"])["status"] == "denied"


# ====================================================================== охват: партия и проект
def test_claims_without_serial_on_batch_or_project(world, pd, q):  # noqa: F811
    u = pd.st.proj
    # Партия — по наряду с завершённым изготовлением; невыполненный наряд — отказ.
    open_wo = release(pd, approved_tp(pd), qty=1)["result"]["work_order"]
    assert reg(u.rp, scope="batch", work_order_id=open_wo["id"])["code"] == "batch_not_made"
    assert reg(u.rp, scope="batch")["code"] == "invalid_id"         # наряд не указан
    assert reg(u.rp, scope="lot", work_order_id=q.wo["id"])["code"] == "invalid_scope"
    c = reg(u.rp, scope="batch", work_order_id=q.wo["id"], source="internal", severity="minor")["result"]["claim"]
    assert (c["instance_id"], c["work_order_id"], c["scope_label"]) == (None, q.wo["id"], "Партия (наряд)")
    assert c["instance_label"] == f"Партия: наряд № {q.wo['doc_no']} — АТХ, 2 шт."
    assert [i["label"] for i in c["batch_instances"]] == ["АТХ № 001 (серия 2026)"]   # номера известны не у всех
    assert (c["response_due"], c["deadline"]) == (None, "none")      # внутренней рекламации срок ответа не нужен
    # Проект — без номера изделия; проект берётся из запроса, чужой экземпляр/наряд не подмешать.
    c2 = reg(u.rp, scope="project", project_id=u.pid, response_due=today(world, 10))["result"]["claim"]
    assert c2["instance_label"] == "Проект П-100 — без номера изделия" and c2["kit_id"] is None
    assert reg(u.rp, instance_id=q.inst["id"], project_id=new_id())["code"] == "project_mismatch"
    # Запрос на изменение по рекламации на партию — как обычно.
    r = u.eng.ok("ecr.submit", {"id": new_id(), "project_id": u.pid, "title": "Изменить схему", "reason": "Партия",
                                "proposal": "Новая схема", "claim_id": c["id"]})
    assert r["change_request"]["claim_no"] == c["claim_no"]
    # В журнале — охват и наряд.
    h = [x for x in pull(u.rp)["claims"] if x["id"] == c["id"]][0]["history"][0]
    assert (h["decision"], h["details"]["scope"], h["details"]["work_order_id"]) == ("registered", "batch", q.wo["id"])


# ====================================================================== классификация
def test_classification_with_reason_and_critical_notice(world, pd, q):  # noqa: F811
    u = pd.st.proj
    c = reg(u.rp, instance_id=q.inst["id"], response_due=today(world, 10))["result"]["claim"]
    assert reg(u.rp, instance_id=q.inst["id"], category="xx")["code"] == "invalid_category"
    v = c["version"]
    assert u.eng.cmd("claim.classify", {"id": c["id"], "severity": "critical", "reason": "x"}, expected_version=v)["status"] == "denied"
    assert u.rp.cmd("claim.classify", {"id": c["id"], "severity": "critical"}, expected_version=v)["code"] == "required"
    assert u.rp.cmd("claim.classify", {"id": c["id"], "severity": "major", "reason": "Так"}, expected_version=v)["code"] == "no_changes"
    c = u.rp.ok("claim.classify", {"id": c["id"], "severity": "critical", "category": "manufacturing",
                                   "reason": "Перегрев клемм — опасно"}, expected_version=v)["claim"]
    assert (c["severity_label"], c["category_label"]) == ("Критический", "Производственный (изготовление, сборка)")
    assert "claim_critical" in kinds(u.gip) and "claim_critical" in kinds(q.otk)
    h = c["history"][-1]
    assert (h["decision"], h["details"]["previous"]["severity"], h["details"]["reason"]) == ("classified", "major",
                                                                                             "Перегрев клемм — опасно")
    # Категорию можно сменить, но не убрать; повторный «критический» уведомление не дублирует.
    assert u.rp.cmd("claim.classify", {"id": c["id"], "severity": "minor", "reason": "Уточнили"},
                    expected_version=c["version"])["code"] == "category_required"
    # Если категория уже есть — первый план её не требует.
    c = u.rp.ok("claim.start_analysis", {"id": c["id"], "responsible_id": u.eng.user_id}, expected_version=c["version"])["claim"]
    a = {"id": new_id(), "kind": "corrective", "description": "Обжать клеммы", "owner_id": u.eng.user_id, "due_date": "2099-01-01"}
    c = u.rp.ok("claim.plan", {"id": c["id"], "cause": "Не обжаты клеммы", "actions": [a]}, expected_version=c["version"])["claim"]
    assert c["category"] == "manufacturing"


# ====================================================================== сроки и ответы заказчику
def test_response_deadline_reminders_and_responses(world, pd, q):  # noqa: F811
    u = pd.st.proj
    c = reg(u.rp, instance_id=q.inst["id"], received_date=today(world), response_due=today(world, 2))["result"]["claim"]
    assert c["deadline"] == "soon" and c["answered"] is False
    # Напоминание «срок подходит» — при обновлении, один раз; тем, кто ведёт рекламацию (не инженеру).
    assert kinds(u.rp).count("claim_due_soon") == 1 and kinds(u.rp).count("claim_due_soon") == 1
    assert "claim_due_soon" in kinds(q.otk) and "claim_due_soon" not in kinds(u.eng)
    # Срок прошёл (рекламация получена 5 дней назад, ответить надо было вчера) — напоминание «срок прошёл».
    c = reg(u.rp, instance_id=q.inst["id"], received_date=today(world, -5), response_due=today(world, -1))["result"]["claim"]
    assert c["deadline"] == "overdue"
    assert kinds(u.gip).count("claim_overdue") == 1 and kinds(u.gip).count("claim_overdue") == 1
    # Ответ: только при связи, с версией, не в будущем, не раньше получения.
    v = c["version"]
    resp = {"id": c["id"], "kind": "interim", "response_date": today(world), "summary": "Приняли в работу, анализ до 30.01",
            "reference": "Исх. № 12"}
    assert u.eng.cmd("claim.respond", resp, expected_version=v)["status"] == "denied"
    assert u.rp.cmd("claim.respond", {**resp, "response_date": today(world, 3)}, expected_version=v)["code"] == "date_in_future"
    assert u.rp.cmd("claim.respond", {**resp, "response_date": "2020-01-01"}, expected_version=v)["code"] == "response_before_received"
    assert u.rp.cmd("claim.respond", {**resp, "kind": "x"}, expected_version=v)["code"] == "invalid_kind"
    world.clock.advance(seconds=5)
    c = q.otk.ok("claim.respond", resp, expected_version=v)["claim"]
    assert (c["deadline"], c["answered"], c["responses"][0]["kind_label"]) == ("answered", True, "Промежуточный ответ")
    # Новый срок (окончательный ответ) — с причиной, не в прошлом; отсчёт заново, напоминание — снова.
    v = c["version"]
    assert u.rp.cmd("claim.set_due", {"id": c["id"], "response_due": today(world, 1)}, expected_version=v)["code"] == "required"
    assert u.rp.cmd("claim.set_due", {"id": c["id"], "response_due": today(world, -3), "reason": "x"},
                    expected_version=v)["code"] == "due_in_past"
    world.clock.advance(seconds=5)
    c = u.rp.ok("claim.set_due", {"id": c["id"], "response_due": today(world, 1), "reason": "Окончательный ответ — по письму № 13"},
                expected_version=v)["claim"]
    assert (c["deadline"], c["answered"]) == ("soon", False)
    assert kinds(u.rp).count("claim_due_soon") == 2                 # новый срок — новое напоминание
    world.clock.advance(seconds=5)
    c = u.rp.ok("claim.respond", {**resp, "kind": "final", "summary": "Причина устранена, акт № 3"},
                expected_version=c["version"])["claim"]
    assert c["deadline"] == "answered" and [r["seq"] for r in c["responses"]] == [1, 2]
    assert [h["decision"] for h in c["history"]][-3:] == ["responded", "due_set", "responded"]
    # Ответы неизменяемы; внутренней рекламации срок и ответ не нужны.
    with world.svc.engine.begin() as conn:
        with pytest.raises(Exception):
            conn.execute(db.claim_responses.delete())
    ci = reg(u.rp, instance_id=q.inst["id"], source="internal")["result"]["claim"]
    assert u.rp.cmd("claim.respond", {**resp, "id": ci["id"]}, expected_version=ci["version"])["code"] == "not_customer_claim"
    assert u.rp.cmd("claim.set_due", {"id": ci["id"], "response_due": today(world, 5), "reason": "x"},
                    expected_version=ci["version"])["code"] == "not_customer_claim"


def test_core_deadline_and_classification_rules():
    assert qa.deadline_state(None, False, "2026-01-10") == "none"
    assert qa.deadline_state("2026-01-10", True, "2026-02-01") == "answered"
    assert [qa.deadline_state("2026-01-10", False, d) for d in ("2026-01-05", "2026-01-08", "2026-01-10", "2026-01-11")] == \
        ["ok", "soon", "today", "overdue"]
    with pytest.raises(qa.QualityError) as e:
        qa.check_due("2026-01-10", "2027-03-01")
    assert e.value.code == "due_too_far"
    assert qa.parse_classification({"severity": "minor"}, require_category=False) == {"severity": "minor", "category": None}
    with pytest.raises(qa.QualityError) as e:
        qa.parse_classification({"severity": "minor"}, require_category=True)
    assert e.value.code == "category_required"
    assert set(qa.DEADLINE_LABELS_RU) == set(qa.DEADLINE_TONES)
    assert timedelta(days=qa.DUE_SOON_DAYS) == timedelta(days=2)
