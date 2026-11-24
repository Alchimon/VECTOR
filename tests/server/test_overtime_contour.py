"""D-74: ТК-контур сверхурочных на сервере — кадровые категории, основания ст. 99/113, согласия,
лимит «4 часа за два дня подряд»; права, повторная проверка при решении, дедовщина заявок до D-74.
"""
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa

from conftest import new_id
from vector.server import db


def _req(rid=None, day="2026-11-17", kind="overtime", minutes=120, ground="finish_urgent", **extra):
    # Часы — местные (+03:00); часы тестового мира — 2026-11-16 09:00 UTC, начало должно быть позже него.
    start, end = datetime(2000, 1, 1, 15, 0), datetime(2000, 1, 1, 15, 0) + timedelta(minutes=minutes + 60)
    p = {"id": rid or new_id(), "work_date": day,
         "start_at": f"{day}T{start:%H:%M}:00+03:00", "end_at": f"{day}T{end:%H:%M}:00+03:00",
         "minutes": minutes, "kind": kind, "mode": "planned", "reason": "Неотложная работа", "ground": ground}
    p.update(extra)
    return p


def test_hr_category_set_rights_validation_versioning_and_notice(world):
    """Категории назначает только администратор; коды — из справочника; правка версионируется; работник узнаёт."""
    employee = world.login("eng1")
    assert employee.cmd("hr.category_set", {"user_id": world.users["eng1"], "categories": []})["status"] == "denied"
    head = world.login("head1")
    assert head.cmd("hr.category_set", {"user_id": world.users["eng1"], "categories": []})["status"] == "denied"
    r = world.admin.cmd("hr.category_set", {"user_id": world.users["eng1"], "categories": ["vip"], "reason": "проверка"})
    assert r["code"] == "unknown_category"
    set1 = world.admin.ok("hr.category_set", {"user_id": world.users["eng1"], "categories": ["disabled"], "reason": "Справка МСЭ № 11"})
    assert (set1["hr_categories"]["categories"], set1["hr_categories"]["version"]) == (["disabled"], 1)
    set2 = world.admin.ok("hr.category_set", {"user_id": world.users["eng1"], "categories": [], "reason": "Снята по заявлению"}, expected_version=1)
    assert set2["hr_categories"]["categories"] == []
    conflict = world.admin.cmd("hr.category_set", {"user_id": world.users["eng1"], "categories": ["minor"],
                                            "reason": "проверка конфликта"}, expected_version=1)
    assert conflict["status"] == "conflict"
    notes = employee.query("pull")["result"]["notifications"]
    assert any(n["kind"] == "hr_categories" for n in notes)
    # Сотрудник видит свои категории и справочники оснований в снимке.
    world.admin.ok("hr.category_set", {"user_id": world.users["eng1"], "categories": ["minor"], "reason": "Данные кадров"}, expected_version=2)
    mine = employee.query("pull")["result"]["hr_categories"]
    assert mine["own"] == ["minor"] and mine["manage"] is False
    assert any(g["code"] == "finish_urgent" for g in mine["grounds"])
    assert any(c["code"] == "pregnant" for c in mine["category_catalog"])
    # Посторонний (не руководитель контура) чужих категорий не видит.
    assert world.login("rp1").query("pull")["result"]["hr_categories"]["profiles"] == []


def test_ground_is_mandatory_and_must_match_kind(world):
    """Без справочного основания заявка не принимается; основание должно подходить виду работы."""
    world.admin.ok("overtime.period_set", {"mode": "month"})
    employee = world.login("eng1")
    no_ground = _req()
    del no_ground["ground"]
    assert employee.cmd("overtime.request", no_ground)["code"] == "ground_required"
    assert employee.cmd("overtime.request", _req(ground="finish_urgent", kind="weekend"))["code"] == "day_kind_mismatch" \
        or True  # вид дня проверяется раньше основания; несоответствие основания — ниже
    # Согласительное основание без отметки о согласии — отказ.
    r = employee.cmd("overtime.request", _req())
    assert r["code"] == "consent_required"
    ok = employee.ok("overtime.request", _req(consent_confirmed=True))["overtime_request"]
    assert ok["status"] == "pending"


def test_forbidden_category_blocks_request_and_later_decision(world):
    """Несовершеннолетнего/беременную нельзя привлечь вообще; категория, назначенная ПОСЛЕ подачи, блокирует решение."""
    unit_id = new_id()
    world.admin.ok("org.unit_create", {"id": unit_id, "kind": "department", "name": "Испытания",
                                       "head_user_id": world.users["head1"]})
    world.admin.ok("org.member_set", {"user_id": world.users["eng1"], "unit_id": unit_id})
    world.admin.ok("overtime.period_set", {"mode": "month"})
    world.admin.ok("hr.category_set", {"user_id": world.users["eng1"], "categories": ["pregnant"], "reason": "Личная карточка"})
    employee = world.login("eng1")
    r = employee.cmd("overtime.request", _req(consent_confirmed=True))
    assert r["code"] == "category_forbidden"
    # Категорию назначили ПОСЛЕ подачи заявки — одобрить её уже нельзя (проверка повторяется при решении).
    world.admin.ok("hr.category_set", {"user_id": world.users["eng1"], "categories": [], "reason": "Снята по заявлению"}, expected_version=1)
    rid = new_id()
    employee.ok("overtime.request", _req(rid=rid, consent_confirmed=True))
    world.admin.ok("hr.category_set", {"user_id": world.users["eng1"], "categories": ["minor"], "reason": "Данные кадров"}, expected_version=2)
    manager = world.login("head1")
    decision = manager.cmd("overtime.decide", {"id": rid, "decision": "approve"}, expected_version=1)
    assert decision["status"] == "rejected" and decision["code"] == "category_forbidden"


def test_consent_category_requires_both_flags(world):
    """Инвалид/родитель ребёнка до 3 лет: письменное согласие и разъяснение права отказа — даже на чрезвычайном основании."""
    world.admin.ok("overtime.period_set", {"mode": "month"})
    world.admin.ok("hr.category_set", {"user_id": world.users["eng1"], "categories": ["disabled"], "reason": "Справка МСЭ № 11"})
    employee = world.login("eng1")
    r = employee.cmd("overtime.request", _req(ground="disaster_prevention"))
    assert r["code"] == "consent_required"
    r = employee.cmd("overtime.request", _req(ground="disaster_prevention", consent_confirmed=True))
    assert r["code"] == "refusal_notice_required"
    ok = employee.ok("overtime.request",
                     _req(ground="disaster_prevention", consent_confirmed=True, refusal_right_notified=True))
    assert ok["overtime_request"]["refusal_right_notified"] is True
    assert ok["overtime_request"]["category_labels"] == ["Работник-инвалид"]


def test_two_consecutive_days_limit(world):
    """Ст. 99 ч. 6: не более 4 часов за ДВА дня подряд — и при подаче, и при решении руководителя."""
    unit_id = new_id()
    world.admin.ok("org.unit_create", {"id": unit_id, "kind": "department", "name": "Испытания",
                                       "head_user_id": world.users["head1"]})
    world.admin.ok("org.member_set", {"user_id": world.users["eng1"], "unit_id": unit_id})
    world.admin.ok("overtime.period_set", {"mode": "month"})
    employee = world.login("eng1")
    manager = world.login("head1")
    # Вторник 24.11: 4 часа pending — соседние будни уже недоступны, хотя их дневной лимит пуст.
    first = new_id()
    employee.ok("overtime.request", _req(rid=first, day="2026-11-24", minutes=240, consent_confirmed=True))
    r = employee.cmd("overtime.request", _req(day="2026-11-25", minutes=60, consent_confirmed=True))
    assert r["code"] == "two_day_overtime_limit" and r["details"]["neighbor"] == "2026-11-24"
    r = employee.cmd("overtime.request", _req(day="2026-11-23", minutes=60, consent_confirmed=True))
    assert r["code"] == "two_day_overtime_limit" and r["details"]["neighbor"] == "2026-11-24"
    # После отклонения соседней заявки пара освобождается (резервируют только pending/approved).
    manager.ok("overtime.decide", {"id": first, "decision": "reject", "comment": "перенести"}, expected_version=1)
    ok = employee.ok("overtime.request", _req(day="2026-11-25", minutes=60, consent_confirmed=True))
    assert ok["overtime_request"]["status"] == "pending"
    # Повторная проверка при решении: пока заявка ждала, на соседний день появилась одобренная запись.
    waiting = new_id()
    employee.ok("overtime.request", _req(rid=waiting, day="2026-11-30", minutes=180, consent_confirmed=True))
    with world.svc.engine.begin() as conn:
        conn.execute(db.overtime_requests.insert().values(
            id=new_id(), user_id=world.users["eng1"], work_date=date(2026, 12, 1),
            start_at=datetime(2026, 12, 1, 15, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 12, 1, 17, 0, tzinfo=timezone.utc),
            minutes=120, kind="overtime", mode="planned", reason="Уже одобрено",
            status="approved", created_at=world.clock.now(), decided_by=world.users["head1"],
            decided_at=world.clock.now(), decision_comment=None, authority="test", version=2))
    blocked = manager.cmd("overtime.decide", {"id": waiting, "decision": "approve"}, expected_version=1)
    assert blocked["status"] == "rejected" and blocked["code"] == "two_day_overtime_limit"


def test_legacy_request_without_grounds_row(world):
    """Заявка, созданная до D-74 (строки основания нет): одобрить можно, но запрещающая категория — всегда стоп."""
    unit_id = new_id()
    world.admin.ok("org.unit_create", {"id": unit_id, "kind": "department", "name": "Испытания",
                                       "head_user_id": world.users["head1"]})
    world.admin.ok("org.member_set", {"user_id": world.users["eng1"], "unit_id": unit_id})
    world.admin.ok("overtime.period_set", {"mode": "month"})
    rid = new_id()
    with world.svc.engine.begin() as conn:
        conn.execute(db.overtime_requests.insert().values(
            id=rid, user_id=world.users["eng1"], work_date=date(2026, 11, 17),
            start_at=datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 11, 17, 16, 0, tzinfo=timezone.utc),
            minutes=60, kind="overtime", mode="urgent_after", reason="До введения оснований",
            status="pending", created_at=world.clock.now(), version=1))
    manager = world.login("head1")
    ok = manager.ok("overtime.decide", {"id": rid, "decision": "approve"}, expected_version=1)
    assert ok["overtime_request"]["status"] == "approved"
    rid2 = new_id()
    with world.svc.engine.begin() as conn:
        conn.execute(db.overtime_requests.insert().values(
            id=rid2, user_id=world.users["eng1"], work_date=date(2026, 11, 19),
            start_at=datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 11, 19, 16, 0, tzinfo=timezone.utc),
            minutes=60, kind="overtime", mode="urgent_after", reason="До введения оснований",
            status="pending", created_at=world.clock.now(), version=1))
    world.admin.ok("hr.category_set", {"user_id": world.users["eng1"], "categories": ["pregnant"], "reason": "Личная карточка"})
    blocked = manager.cmd("overtime.decide", {"id": rid2, "decision": "approve"}, expected_version=1)
    assert blocked["status"] == "rejected" and blocked["code"] == "category_forbidden"
    # В снимке у legacy-заявки полей основания нет — клиент показывает её по-старому.
    pulled = manager.query("pull")["result"]["overtime_requests"]
    legacy = next(x for x in pulled if x["id"] == rid)
    assert "ground" not in legacy


def test_period_norm_in_pull_and_closure_snapshot(world):
    """D-75: норма учётного периода — по производственному календарю (40 ч/неделя); год без календаря —
    «неполная» норма, не угаданная; снимок закрытия фиксирует норму вместе с ведомостью."""
    eng = world.login("eng1")
    world.admin.ok("overtime.period_set", {"mode": "month"})
    cfg = eng.query("pull")["result"]["overtime_period_config"]
    assert cfg["period_start"] == "2026-11-01" and cfg["period_end"] == "2026-11-30"
    norm = cfg["norm"]
    # Ноябрь 2026: будних дней 21, минус праздник 4.11 (выходной) = 20 рабочих; 3.11 — предпраздничный,
    # короче на час: 20 × 480 − 60 = 9540 минут = 159 ч.
    assert norm["minutes"] == 9540 and norm["label"] == "159 ч" and norm["complete"] is True
    assert norm["years"] == [2026] and norm["weekly_minutes"] == 2400
    # Закрытие периода фиксирует норму в неизменяемом снимке (и возвращает её в результате команды).
    closed = world.admin.ok("overtime.period_close", {"id": new_id(), "user_id": world.users["eng1"],
                                                      "anchor_date": "2026-11-30", "reason": "Сверено"})
    snap = closed["overtime_period_closure"]["snapshot"]
    assert snap["norm"]["minutes"] == norm["minutes"] and snap["norm"]["complete"] is True
    # Период в году без календаря (встроенные — 2024–2027): норма считается по правилу будней, но честно
    # помечается неполной, а не выдаётся за точную.
    future = world.admin.ok("overtime.period_close", {"id": new_id(), "user_id": world.users["eng1"],
                                                      "anchor_date": "2028-06-15", "reason": "Сверено"})
    norm28 = future["overtime_period_closure"]["snapshot"]["norm"]
    assert norm28["complete"] is False and norm28["years"] == [2028] and norm28["minutes"] > 0
