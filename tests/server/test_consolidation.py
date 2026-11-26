"""Консолидация закупок и аналоги (этап 4 blueprint 52): пакеты, распределения, покрытие, предложения аналогов.

Каждый тест закрепляет правило из server/consolidation.py (чистые правила — vector.core.consolidation);
запускается на SQLite и на встроенном PostgreSQL. Кто есть кто: pr1 — закупка, wh1 — склад, rp1/gip1 — проект.
"""
from decimal import Decimal

import pytest
import sqlalchemy as sa

from conftest import new_id
from test_purchasing import draft, line, supplier  # noqa: F401
from test_stock import _freeze, calc_line, st  # noqa: F401
from test_tasks_documents import proj  # noqa: F401
from vector.server import db


def pull(s):
    r = s.query("pull")
    assert r["status"] == "accepted", r
    return r["result"]


def make_need(st, qty="120"):  # noqa: F811
    """Потребность по новому комплекту: строка кабеля (сопоставлена) + строка без номенклатуры."""
    kit = new_id()
    st.proj.rp.ok("kit.create", {"id": kit, "project_id": st.proj.pid, "code": f"К-{kit[:4]}", "title": "Комплект"})
    ver = _freeze_for(st.proj, kit, [calc_line("Кабель", "КВВГ-4x1.5", "м", qty), calc_line("Шкаф", "ШК-1", "шт", "1")])
    return st.pr.ok("need.generate", {"id": new_id(), "calc_version_id": ver["id"]})["need"]


def _freeze_for(proj, kit, lines):
    """Сохранить черновик расчёта комплекта kit и зафиксировать его ГИП; вернуть версию расчёта."""
    proj.gip.ok("calc.save_draft", {"id": kit, "lines": lines, "currency": "RUB"}, expected_version=0)
    return proj.gip.ok("calc.freeze", {"id": kit, "reason": "Выпуск"}, expected_version=1)["calc_version"]


def group(st, code="КАБ"):  # noqa: F811
    """Группа закупки «кабель»."""
    return st.pr.ok("pgroup.create", {"id": new_id(), "code": code, "name": "Кабель и провод"})["group"]


def make_batch(st, allocs, **kw):  # noqa: F811
    p = {"id": new_id(), "title": "Пакет", "allocations": allocs, **kw}
    return st.pr.cmd("batch.create", p)


def alloc(need, idx, qty):
    ln = need["lines"][idx]
    return {"need_id": need["id"], "need_line_id": ln["line_id"], "qty": qty}


# ====================================================================== группы закупки
def test_group_crud_and_rights(world, st):  # noqa: F811
    # Права: группу ведёт закупка; склад и ГИП — нет.
    assert st.wh.cmd("pgroup.create", {"id": new_id(), "code": "X", "name": "X"})["status"] == "denied"
    assert st.proj.gip.cmd("pgroup.create", {"id": new_id(), "code": "X", "name": "X"})["status"] == "denied"
    g = group(st)
    assert st.pr.cmd("pgroup.create", {"id": new_id(), "code": "КАБ", "name": "Дубль"})["code"] == "group_code_taken"
    g = st.pr.ok("pgroup.update", {"id": g["id"], "name": "Кабельная продукция"}, expected_version=1)["group"]
    assert g["name"] == "Кабельная продукция"
    g = st.pr.ok("pgroup.update", {"id": g["id"], "active": False}, expected_version=2)["group"]
    assert g["active"] is False
    # Отключённая группа не принимает новые пакеты.
    need = make_need(st)
    r = make_batch(st, [alloc(need, 0, "10")], group_id=g["id"])
    assert r["code"] == "group_inactive"
    # Читают группы склад и закупка (stock.read), ГИП проекта — нет.
    assert len(pull(st.wh)["procurement_groups"]) == 1
    assert pull(st.proj.gip)["procurement_groups"] == []


# ====================================================================== пакет: создание и покрытие
def test_batch_consolidates_and_coverage_excludes_double_ordering(world, st):  # noqa: F811
    need = make_need(st, "120")
    g = group(st)
    # Несопоставленную строку (нет номенклатуры) в пакет нельзя; дубликат строки и ноль — тоже.
    assert make_batch(st, [alloc(need, 1, "1")])["code"] == "line_not_matched"
    assert make_batch(st, [alloc(need, 0, "10"), alloc(need, 0, "5")])["code"] == "bad_allocations"
    assert make_batch(st, [alloc(need, 0, "0")])["code"] == "bad_allocations"
    assert make_batch(st, [])["code"] == "bad_allocations"
    # Права: пакет формирует закупка.
    assert st.proj.gip.cmd("batch.create", {"id": new_id(), "title": "X", "allocations": [alloc(need, 0, "1")]}
                           )["status"] == "denied"
    b = make_batch(st, [alloc(need, 0, "100")], group_id=g["id"], planned_on="2026-12-01")
    assert b["status"] == "accepted"
    batch = b["result"]["batch"]
    assert (batch["batch_no"], batch["status"], batch["status_label"]) == (1, "open", "Формируется")
    # Строка пакета — одна на позицию+единицу, распределение связано со строкой потребности.
    data = pull(st.pr)
    ln = [x for x in data["procurement_batch_lines"] if x["batch_id"] == batch["id"]]
    al = [x for x in data["procurement_allocations"] if x["batch_line_id"] == ln[0]["id"]]
    assert (ln[0]["item_id"], ln[0]["unit"]) == (st.item, "м") and al[0]["qty"] == "100.0000"
    # Осталось 20: второй пакет на 30 — отказ, на 20 — можно.
    assert make_batch(st, [alloc(need, 0, "30")])["code"] == "coverage_exceeded"
    b2 = make_batch(st, [alloc(need, 0, "20")])
    assert b2["status"] == "accepted"
    assert make_batch(st, [alloc(need, 0, "1")])["code"] == "coverage_exceeded"
    # Отмена пакета освобождает покрытие; отмена — с причиной.
    b2v = b2["result"]["batch"]
    assert st.pr.cmd("batch.transition", {"id": b2v["id"], "to": "cancelled"},
                     expected_version=1)["code"] == "reason_required"
    st.pr.ok("batch.transition", {"id": b2v["id"], "to": "cancelled", "reason": "заказчик урезал объём"},
             expected_version=1)
    assert make_batch(st, [alloc(need, 0, "20")])["status"] == "accepted"
    # Черновик заказа поставщику тоже занимает покрытие: 100 в пакете + 20 в черновике — больше ничего.
    st2_need = make_need(st, "50")           # другая потребность того же кабеля (второй расчёт)
    sup = supplier(st.pr)
    draft(st, sup, [line(st.item, "40", need_id=st2_need["id"], need_line_id=st2_need["lines"][0]["line_id"])])
    assert make_batch(st, [alloc(st2_need, 0, "20")])["code"] == "coverage_exceeded"
    assert make_batch(st, [alloc(st2_need, 0, "10")])["status"] == "accepted"


def test_batch_update_recheck_and_transitions(world, st):  # noqa: F811
    need = make_need(st, "100")
    b = make_batch(st, [alloc(need, 0, "60")])["result"]["batch"]
    # Правка состава в «Формируется»: пересборка распределений в той же транзакции.
    b = st.pr.ok("batch.update", {"id": b["id"], "allocations": [alloc(need, 0, "70")], "title": "Пакет кабеля"},
                 expected_version=b["version"])["batch"]
    al = [x for x in pull(st.pr)["procurement_allocations"]
          if x["batch_line_id"] in [ln["id"] for ln in pull(st.pr)["procurement_batch_lines"]
                                    if ln["batch_id"] == b["id"]]]
    assert al[0]["qty"] == "70.0000"
    # Сформировать; пустой пакет нельзя; после формирования правка запрещена.
    assert st.pr.cmd("batch.transition", {"id": b["id"], "to": "consolidated"},
                     expected_version=b["version"])["status"] == "accepted"
    b = [x for x in pull(st.pr)["procurement_batches"] if x["id"] == b["id"]][0]
    assert st.pr.cmd("batch.update", {"id": b["id"], "title": "поздно"},
                     expected_version=b["version"])["code"] == "batch_not_open"
    # Возврат в работу — с причиной; повторное формирование перепроверяет покрытие.
    assert st.pr.cmd("batch.transition", {"id": b["id"], "to": "open"},
                     expected_version=b["version"])["code"] == "reason_required"
    b = st.pr.ok("batch.transition", {"id": b["id"], "to": "open", "reason": "добавить позицию"},
                 expected_version=b["version"])["batch"]
    # Пока пакет вернули, закупка разместила черновик заказа на 50 по той же строке: формирование 70 откажет.
    sup = supplier(st.pr)
    draft(st, sup, [line(st.item, "50", need_id=need["id"], need_line_id=need["lines"][0]["line_id"])])
    r = st.pr.cmd("batch.transition", {"id": b["id"], "to": "consolidated"}, expected_version=b["version"])
    assert r["code"] == "coverage_exceeded"
    # Закрытие — только из «Сформирован».
    assert st.pr.cmd("batch.transition", {"id": b["id"], "to": "closed"},
                     expected_version=b["version"])["code"] == "transition_not_allowed"


def test_batch_visibility_for_project_roles(world, st):  # noqa: F811
    need = make_need(st, "10")
    b = make_batch(st, [alloc(need, 0, "10")])["result"]["batch"]
    # ГИП проекта видит пакет со своей потребностью; склад видит все пакеты; чужой проект — нет.
    assert [x["id"] for x in pull(st.proj.gip)["procurement_batches"]] == [b["id"]]
    assert [x["id"] for x in pull(st.wh)["procurement_batches"]] == [b["id"]]
    rp2 = world.login("rp2")
    assert pull(rp2)["procurement_batches"] == []
    # Формирование уведомляет руководство затронутого проекта.
    st.pr.ok("batch.transition", {"id": b["id"], "to": "consolidated"}, expected_version=1)
    assert any(n["kind"] == "batch_consolidated" for n in pull(st.proj.gip)["notifications"])


# ====================================================================== аналоги
def test_analog_propose_decide_flow(world, st):  # noqa: F811
    wh, pr, gip = st.wh, st.pr, st.proj.gip
    item2 = new_id()
    wh.ok("item.create", {"id": item2, "code": "КВВГнг-4x1.5", "name": "Кабель КВВГнг 4×1,5", "unit": "м"})
    # Предложение: сам себе и дубликат пары — отказ; склад предложить не может.
    assert pr.cmd("analog.propose", {"id": new_id(), "item_id": st.item, "analog_item_id": st.item}
                  )["code"] == "bad_value"
    assert wh.cmd("analog.propose", {"id": new_id(), "item_id": st.item, "analog_item_id": item2}
                  )["status"] == "denied"
    a = pr.ok("analog.propose", {"id": new_id(), "item_id": st.item, "analog_item_id": item2,
                                 "note": "тот же кабель, не распространяющий горение"})["analog"]
    assert a["status"] == "proposed"
    assert pr.cmd("analog.propose", {"id": new_id(), "item_id": st.item, "analog_item_id": item2}
                  )["code"] == "analog_exists"
    # Решение — ГИП/ведущий инженер; закупка не решает; отклонение — с причиной; решение одноразовое.
    assert pr.cmd("analog.decide", {"id": a["id"], "decision": "approve"},
                  expected_version=1)["status"] == "denied"
    assert gip.cmd("analog.decide", {"id": a["id"], "decision": "reject"},
                   expected_version=1)["code"] == "reason_required"
    a = gip.ok("analog.decide", {"id": a["id"], "decision": "approve"}, expected_version=1)["analog"]
    assert a["status"] == "approved" and a["decided_by"] == world.users["gip1"]
    assert gip.cmd("analog.decide", {"id": a["id"], "decision": "reject", "reason": "x"},
                   expected_version=2)["code"] == "already_decided"
    # Предложивший получил уведомление; аналоги видны закупке и инженерам.
    assert any(n["kind"] == "analog_decided" for n in pull(pr)["notifications"])
    assert len(pull(gip)["item_analogs"]) == 1 and len(pull(wh)["item_analogs"]) == 1


# ====================================================================== миграция неизменяемости не требуется: правки только в «Формируется»
def test_batch_allocations_unique_per_batch(backend, tmp_path, clock):
    """Одна строка потребности не входит в один пакет дважды — уникальный ключ БД (под ковриком серверной проверки)."""
    from conftest import make_url
    eng = db.make_engine(make_url(backend, tmp_path))
    db.create_schema(eng, str(new_id()), clock.now())
    from conftest import World
    from vector.server.service import Service
    from vector.server.security import SecurityPolicy
    w = World(Service(eng, clock=clock, sec=SecurityPolicy()), clock)
    rp = w.login("rp1")
    _c, tender, _ = w.won_tender(rp)
    pid, kit = new_id(), new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-1", "title": "П"})
    w.admin.ok("project.assign", {"project_id": pid, "user_id": w.users["gip1"], "project_role": "gip", "reason": "п"})
    w.admin.ok("project.assign", {"project_id": pid, "user_id": w.users["rp1"], "project_role": "rp", "reason": "п"})
    rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "К"})
    w.make_user("wh9", "warehouse")
    w.make_user("pr9", "procurement")
    wh, pr = w.login("wh9"), w.login("pr9")
    item = new_id()
    wh.ok("item.create", {"id": item, "code": "КВВГ-4x1.5", "name": "Кабель", "unit": "м"})
    gip = w.login("gip1")
    gip.ok("calc.save_draft", {"id": kit, "lines": [calc_line("Кабель", "КВВГ-4x1.5", "м", "10")],
                               "currency": "RUB"}, expected_version=0)
    ver = gip.ok("calc.freeze", {"id": kit, "reason": "Выпуск"}, expected_version=1)["calc_version"]
    need = pr.ok("need.generate", {"id": new_id(), "calc_version_id": ver["id"]})["need"]
    b = pr.ok("batch.create", {"id": new_id(), "title": "П", "allocations": [
        {"need_id": need["id"], "need_line_id": need["lines"][0]["line_id"], "qty": "5"}]})["batch"]
    bl = [x for x in pull(pr)["procurement_batch_lines"] if x["batch_id"] == b["id"]][0]
    with eng.connect() as conn:
        with pytest.raises(sa.exc.DBAPIError):
            with conn.begin():
                conn.execute(db.procurement_allocations.insert().values(
                    id=new_id(), batch_line_id=bl["id"], need_id=need["id"],
                    need_line_id=need["lines"][0]["line_id"], qty=Decimal("1")))
