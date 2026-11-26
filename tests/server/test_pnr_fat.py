# -*- coding: utf-8 -*-
"""ПНР и FAT-сессии (этап 6 blueprint 52, D-68, ответы владельца D-66).

Покрыто:
- запрос ПНР создаёт участник проекта; статус (и отмена с причиной) ведёт РП; отмена без причины отклонена;
- FAT создаётся по активному шаблону вида «fat» со снимком разделов/градаций; архивный шаблон отклонён;
- замечание: раздел и градация только из снимка сессии; блокирующий признак — снимком; к завершённому — нельзя;
- итог FAT: «неуспешен» требует причины; итог неизменяем (повторное испытание — новая сессия);
- ШЛЮЗ ОТГРУЗКИ: без финального FAT / при идущем / при неуспешном / при открытом блокирующем замечании —
  поток «Отгрузка» → «Отгружена» отклонён; после успешного финального FAT без блокирующих — разрешён;
  предварительный FAT на шлюз не влияет.
"""
from __future__ import annotations

import pytest

from conftest import new_id
from tests.server.test_delivery import make_project, pull, set_status, streams


def checklist(head, active=True):
    """Активный FAT-шаблон с блокирующей градацией (ведёт начальник сервисной службы — здесь head1, A-58)."""
    tid = new_id()
    head.ok("checklist.create", {"id": tid, "kind": "fat", "name": "FAT шкафов (типовой)",
                                 "sections": [{"code": "power", "title": "Питание и заземление"},
                                              {"code": "io", "title": "Проверка входов/выходов"}],
                                 "severities": [{"code": "minor", "label": "Несущественное", "blocking": False},
                                                {"code": "crit", "label": "Блокирующее", "blocking": True}]})
    if not active:
        head.ok("checklist.archive", {"id": tid})
    return tid


@pytest.fixture
def project(world):
    """Проект с РП rp1 и участником eng1 (participant) в команде."""
    rp = world.login("rp1")
    pid = make_project(world, rp)
    rp.ok("project.assign", {"project_id": pid, "user_id": world.users["eng1"],
                             "project_role": "participant", "reason": "приказ"})
    return pid


def pnr(pid, title="Наладка шкафа", kind="internal"):
    return {"id": new_id(), "project_id": pid, "kind": kind, "title": title,
            "need_power": True, "planned_on": "2026-12-01"}


# ====================================================================== запросы ПНР
def test_pnr_participant_creates_rp_moves(world, project):
    rp, eng = world.login("rp1"), world.login("eng1")
    r = eng.ok("pnr.create", pnr(project))
    assert r["pnr"]["status"] == "requested" and r["pnr"]["need_power"] is True
    assert r["pnr"]["kind_label"] == "Внутренняя наладка"
    pid = r["pnr"]["id"]
    # Статус ведёт РП проекта (у участника права нет).
    assert eng.cmd("pnr.set_status", {"id": pid, "to": "scheduled"}, expected_version=1)["code"] == "no_authority"
    ws = rp.ok("pnr.set_status", {"id": pid, "to": "scheduled"}, expected_version=1)["pnr"]
    assert ws["status"] == "scheduled"
    ws = rp.ok("pnr.set_status", {"id": pid, "to": "in_progress"}, expected_version=2)["pnr"]
    ws = rp.ok("pnr.set_status", {"id": pid, "to": "done"}, expected_version=3)["pnr"]
    assert ws["status"] == "done"
    # Из терминального статуса переходов нет.
    assert rp.cmd("pnr.set_status", {"id": pid, "to": "cancelled", "reason": "поздно"},
                  expected_version=4)["code"] == "transition_not_allowed"


def test_pnr_cancel_requires_reason(world, project):
    rp, eng = world.login("rp1"), world.login("eng1")
    r = eng.ok("pnr.create", pnr(project, "Выезд на площадку", "site"))
    assert rp.cmd("pnr.set_status", {"id": r["pnr"]["id"], "to": "cancelled"},
                  expected_version=1)["code"] == "reason_required"
    r2 = rp.ok("pnr.set_status", {"id": r["pnr"]["id"], "to": "cancelled",
                                  "reason": "Площадка заказчика не готова"}, expected_version=1)
    assert r2["pnr"]["status"] == "cancelled" and "не готова" in r2["pnr"]["status_reason"]


# ====================================================================== FAT-сессии и замечания
def test_fat_snapshot_and_template_rules(world, project):
    rp, head = world.login("rp1"), world.login("head1")
    tpl = checklist(head)
    fat = rp.ok("fat.create", {"id": new_id(), "project_id": project, "kind": "final",
                               "template_id": tpl})["fat"]
    assert fat["status"] == "in_progress" and fat["kind"] == "final"
    assert [s["code"] for s in fat["sections"]] == ["power", "io"]          # снимок разделов шаблона
    assert any(s["blocking"] for s in fat["severities"])                    # снимок градаций
    # Правка шаблона историю не переписывает: снимок сессии прежний.
    head.ok("checklist.update", {"id": tpl, "name": "FAT шкафов v2"}, expected_version=1)
    again = next(f for f in pull(rp)["fat_sessions"] if f["id"] == fat["id"])
    assert again["template_name"] == "FAT шкафов (типовой)"
    # Архивный шаблон не даёт новую сессию.
    assert rp.cmd("fat.create", {"id": new_id(), "project_id": project, "kind": "final",
                                 "template_id": checklist(head, active=False)})["code"] == "template_unusable"


def test_findings_from_snapshot_and_close(world, project):
    rp, eng, head = world.login("rp1"), world.login("eng1"), world.login("head1")
    fat = rp.ok("fat.create", {"id": new_id(), "project_id": project, "kind": "final",
                               "template_id": checklist(head)})["fat"]
    # Участник (исполнитель) добавляет замечание в идущее испытание; блокирующий признак — снимком градации.
    f = eng.ok("fat.add_finding", {"id": new_id(), "fat_id": fat["id"], "section_code": "power",
                                   "severity": "crit", "text": "Индикация аварии не срабатывает"})["finding"]
    assert f["status"] == "open" and f["blocking"] is True and f["severity_label"] == "Блокирующее"
    assert f["seq"] == 1
    # Чужой раздел или градация — отклонены (разделы/градации только из снимка сессии).
    assert eng.cmd("fat.add_finding", {"id": new_id(), "fat_id": fat["id"], "section_code": "XXX",
                                       "severity": "crit", "text": "?"})["code"] == "bad_value"
    assert eng.cmd("fat.add_finding", {"id": new_id(), "fat_id": fat["id"], "section_code": "power",
                                       "severity": "zzz", "text": "?"})["code"] == "bad_value"
    # Закрытие — РП, с причиной; повторное закрытие отклонено.
    assert eng.cmd("fat.close_finding", {"id": f["id"], "reason": "нет права"},
                   expected_version=1)["code"] == "no_authority"
    c = rp.ok("fat.close_finding", {"id": f["id"], "reason": "Заменён модуль индикации"},
              expected_version=1)["finding"]
    assert c["status"] == "closed" and c["closed_at"]
    assert rp.cmd("fat.close_finding", {"id": f["id"], "reason": "повторно"},
                  expected_version=2)["code"] == "finding_closed"
    # К завершённому испытанию замечания не добавляются.
    rp.ok("fat.finish", {"id": fat["id"], "result": "passed"}, expected_version=1)
    assert eng.cmd("fat.add_finding", {"id": new_id(), "fat_id": fat["id"], "section_code": "power",
                                       "severity": "minor", "text": "поздно"})["code"] == "fat_finished"


def test_finish_failed_requires_reason_and_result_is_final(world, project):
    rp, head = world.login("rp1"), world.login("head1")
    fat = rp.ok("fat.create", {"id": new_id(), "project_id": project, "kind": "final",
                               "template_id": checklist(head)})["fat"]
    assert rp.cmd("fat.finish", {"id": fat["id"], "result": "failed"},
                  expected_version=1)["code"] == "reason_required"
    done = rp.ok("fat.finish", {"id": fat["id"], "result": "failed", "reason": "Неполный комплект документов"},
                 expected_version=1)["fat"]
    assert done["status"] == "failed" and done["finished_at"] and done["finished_by"]
    # Итог неизменяем: повторное испытание — новой сессией.
    assert rp.cmd("fat.finish", {"id": fat["id"], "result": "passed"},
                  expected_version=2)["code"] == "transition_not_allowed"


# ====================================================================== шлюз отгрузки
def _ship_ready(rp, pid):
    """Поток «Отгрузка» проекта, доведённый до «Готова к отгрузке»."""
    ws = streams(pull(rp), pid)["shipping"]
    return set_status(rp, ws, "ready")["result"]["workstream"]


def _final_fat(rp, pid, tpl, note=None):
    p = {"id": new_id(), "project_id": pid, "kind": "final", "template_id": tpl}
    if note:
        p["note"] = note
    return rp.ok("fat.create", p)["fat"]


def test_shipping_gate(world, project):
    rp, eng, head = world.login("rp1"), world.login("eng1"), world.login("head1")
    tpl = checklist(head)
    ws = _ship_ready(rp, project)
    # 1. Нет финального FAT — отгрузка заблокирована.
    assert set_status(rp, ws, "shipped")["code"] == "gate_requires_fat"
    # 2. Предварительный FAT (даже успешный) на шлюз не влияет (ответ владельца п. 4).
    pre = rp.ok("fat.create", {"id": new_id(), "project_id": project, "kind": "preliminary",
                               "template_id": tpl})["fat"]
    rp.ok("fat.finish", {"id": pre["id"], "result": "passed"}, expected_version=1)
    assert set_status(rp, ws, "shipped")["code"] == "gate_requires_fat"
    # 3. Идущий финальный — заблокировано (испытание не завершено).
    fat = _final_fat(rp, project, tpl)
    assert set_status(rp, ws, "shipped")["code"] == "gate_requires_fat"
    # 4. Блокирующее замечание + «неуспешен» — заблокировано; закрытие замечания итог не переписывает.
    fnd = eng.ok("fat.add_finding", {"id": new_id(), "fat_id": fat["id"], "section_code": "power",
                                     "severity": "crit", "text": "Блокирующее замечание"})["finding"]
    rp.ok("fat.finish", {"id": fat["id"], "result": "failed", "reason": "есть блокирующие замечания"},
          expected_version=1)
    r = set_status(rp, ws, "shipped")
    assert r["code"] == "gate_requires_fat" and "блокирующ" in r["message_ru"].lower()
    rp.ok("fat.close_finding", {"id": fnd["id"], "reason": "устранено"}, expected_version=1)
    assert set_status(rp, ws, "shipped")["code"] == "gate_requires_fat"   # итог failed неизменяем
    # 5. Новое успешное финальное испытание с открытым НЕблокирующим замечанием — отгрузка разрешена.
    fat2 = _final_fat(rp, project, tpl)
    eng.ok("fat.add_finding", {"id": new_id(), "fat_id": fat2["id"], "section_code": "io",
                               "severity": "minor", "text": "Мелкая помарка на табличке"})
    rp.ok("fat.finish", {"id": fat2["id"], "result": "passed"}, expected_version=1)
    r = set_status(rp, ws, "shipped")
    assert r["status"] == "accepted", r
    assert r["result"]["workstream"]["status"] == "shipped"


def test_pull_exposes_pnr_and_fat(world, project):
    rp, eng, head = world.login("rp1"), world.login("eng1"), world.login("head1")
    eng.ok("pnr.create", pnr(project))
    fat = _final_fat(rp, project, checklist(head))
    eng.ok("fat.add_finding", {"id": new_id(), "fat_id": fat["id"], "section_code": "power",
                               "severity": "crit", "text": "замечание"})
    data = pull(rp)
    assert any(p["project_id"] == project for p in data["pnr_requests"])
    fats = [f for f in data["fat_sessions"] if f["project_id"] == project]
    assert fats and fats[0]["template_name"].startswith("FAT шкафов")
    assert any(f["fat_id"] == fat["id"] for f in data["fat_findings"])
    # События проекта фиксируют запрос ПНР и старт испытания.
    kinds = {e["kind"] for e in data["project_events"] if e["project_id"] == project}
    assert {"pnr_requested", "fat_started"} <= kinds
