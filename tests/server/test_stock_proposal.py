# Предложения перемещения между проектами и совместное решение «склад + закупка» (D-78, С4 шаги 3–4).
#
# Правила, которые закрепляют тесты (server/stock.py, команды proposal.propose/withdraw/decide):
# предложение — не резерв, не заказ и не движение; решение одно. accept (только склад) перебрасывает резервы
# проекта-донора старшие-сначала (частичное снятие уменьшает резерв), недостающее берёт из свободного и в той же
# транзакции создаёт резерв проекту-получателю с проверкой строки потребности; order — только закупка;
# reject — любая из сторон с причиной; отзывает предложение только автор и только до решения (в очереди).
import uuid

from conftest import new_id
from test_stock import bal, receive, reserve, st  # noqa: F401
from test_tasks_documents import audit, proj  # noqa: F401


def _pull(s):
    return s.query("pull")["result"]


def _find_prop(snap, pid):
    """Предложение pid из снимка pull или None."""
    return next((x for x in snap["transfer_proposals"] if x["id"] == pid), None)


def propose(s, st, qty, project_from=None, pid=None, **kw):
    """Предложить перемещение qty позиции фикстуры со склада фикстуры в проект фикстуры (статус не проверяется)."""
    p = {"id": pid or new_id(), "warehouse_id": st.w, "item_id": st.item, "to_project_id": st.proj.pid,
         "qty": qty, "reason": "У нас не хватает до конца недели", **kw}
    if project_from:
        p["from_project_id"] = project_from
    return s.cmd("proposal.propose", p)


def _donor_project(world, code="П-200"):
    """Второй проект в работе (донор): ГИП — head1."""
    rp = world.login("rp2")
    _, tender, _ = world.won_tender(rp)
    pid = new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": code, "title": "Проект-донор"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["head1"],
                                      "project_role": "gip", "reason": "Приказ"})
    return pid


# ====================================================================== путь шага 3–4: предложение → accept
def test_propose_accept_redistributes_reservations(world, st, proj):  # noqa: F811
    donor = _donor_project(world)
    receive(st.wh, st, "10")
    reserve(st.wh, st, "3", project_id=donor)
    reserve(st.wh, st, "2", project_id=donor)
    pid = new_id()
    prop = st.pr.ok("proposal.propose", {"id": pid, "warehouse_id": st.w, "item_id": st.item,
                                         "to_project_id": proj.pid, "from_project_id": donor, "qty": "4",
                                         "reason": "У донора лежит в резерве, нам нужно сейчас"})["proposal"]
    assert (prop["status"], prop["version"], prop["from_project_id"], prop["to_project_id"]) == \
        ("proposed", 1, donor, proj.pid)
    # Предложение видно складу и закупке в pull; остатки не изменились (это не движение).
    assert _find_prop(_pull(st.wh), pid)["status"] == "proposed"
    assert bal(st.wh, st) == ("10.0000", "5.0000", "5.0000")
    # Решение склада: accept — старший резерв донора (3) снят целиком, второй уменьшен на 1 (частичное снятие
    # уменьшает резерв, а не закрывает его), свободное не понадобилось → резерв получателя 4.
    r = st.wh.ok("proposal.decide", {"id": pid, "decision": "accept"}, expected_version=prop["version"])
    assert r["proposal"]["status"] == "accepted"
    res = r["reservation"]
    assert (res["project_id"], res["qty"], res["status"]) == (proj.pid, "4.0000", "active")
    assert r["balance"] == {"warehouse_id": st.w, "item_id": st.item,
                            "on_hand": "10.0000", "reserved": "5.0000", "available": "5.0000"}
    snap = _pull(st.wh)
    active_by_project: dict[str, float] = {}
    for x in snap["stock_reservations"]:
        if x["status"] == "active":
            active_by_project[x["project_id"]] = active_by_project.get(x["project_id"], 0.0) + float(x["remaining"])
    assert active_by_project == {donor: 1.0, proj.pid: 4.0}
    released = [x for x in snap["stock_reservations"] if x["status"] == "released"]
    assert len(released) == 1 and "Переброшено" in released[0]["close_reason"]
    # История предложения доступна по праву потребности (need.read).
    h = st.pr.query("history", object_type="stock_transfer_proposal", object_id=pid)
    assert h["status"] == "accepted"
    assert [e["command"] for e in h["result"]] == ["proposal.propose", "proposal.decide"]


def test_accept_from_free_stock_only(world, st, proj):  # noqa: F811
    receive(st.wh, st, "8")
    reserve(st.wh, st, "5", project_id=proj.pid)          # резерв самого получателя не трогаем
    pid = new_id()
    st.pr.ok("proposal.propose", {"id": pid, "warehouse_id": st.w, "item_id": st.item, "to_project_id": proj.pid,
                                  "qty": "3", "reason": "Из свободного остатка"})
    r = st.wh.ok("proposal.decide", {"id": pid, "decision": "accept"}, expected_version=1)
    assert r["reservation"]["project_id"] == proj.pid and r["reservation"]["qty"] == "3.0000"
    assert r["balance"]["reserved"] == "8.0000" and r["balance"]["available"] == "0.0000"


def test_accept_checks_balance(world, st, proj):  # noqa: F811
    donor = _donor_project(world)
    receive(st.wh, st, "4")
    reserve(st.wh, st, "2", project_id=donor)
    # Резерв донора 2 + свободное 2 < 5: accept отклоняет (отказ не меняет версию), предложение остаётся открытым.
    pid = new_id()
    st.pr.ok("proposal.propose", {"id": pid, "warehouse_id": st.w, "item_id": st.item, "to_project_id": proj.pid,
                                  "from_project_id": donor, "qty": "5", "reason": "Попытка сверх остатка"})
    r = st.wh.cmd("proposal.decide", {"id": pid, "decision": "accept"}, expected_version=1)
    assert r["status"] == "rejected" and r["code"] == "insufficient_available"
    assert _find_prop(_pull(st.wh), pid)["status"] == "proposed"


# ====================================================================== матрица решений, отказ, уведомления
def test_decide_matrix_reject_and_notifications(world, st, proj):  # noqa: F811
    donor = _donor_project(world)
    receive(st.wh, st, "8")
    reserve(st.wh, st, "5", project_id=donor)
    # order — только закупка; складу — отказ decision_not_for_role.
    p1 = new_id()
    st.pr.ok("proposal.propose", {"id": p1, "warehouse_id": st.w, "item_id": st.item, "to_project_id": proj.pid,
                                  "from_project_id": donor, "qty": "2", "reason": "Нужно"})
    r = st.wh.cmd("proposal.decide", {"id": p1, "decision": "order"}, expected_version=1)
    assert r["status"] == "rejected" and r["code"] == "decision_not_for_role"
    r = st.pr.ok("proposal.decide", {"id": p1, "decision": "order", "note": "Закупим до пятницы"}, expected_version=1)
    assert r["proposal"]["status"] == "order"
    # reject — любая из сторон, причина обязательна.
    p2 = new_id()
    proj.gip.ok("proposal.propose", {"id": p2, "warehouse_id": st.w, "item_id": st.item,
                                     "to_project_id": proj.pid, "from_project_id": donor, "qty": "2",
                                     "reason": "Нужно и нам"})
    r = st.pr.cmd("proposal.decide", {"id": p2, "decision": "reject"}, expected_version=1)
    assert r["status"] == "rejected" and r["code"] == "field_required"
    r = st.pr.ok("proposal.decide", {"id": p2, "decision": "reject", "note": "Донору самим нужно"},
                 expected_version=1)
    assert r["proposal"]["status"] == "rejected"
    # Уведомления: склад узнал о предложении и о решении; автор (ГИП получателя) — о решении.
    kinds_wh = [n["kind"] for n in _pull(st.wh)["notifications"]]
    assert "proposal_new" in kinds_wh and "proposal_decided" in kinds_wh
    kinds_gip = [n["kind"] for n in _pull(proj.gip)["notifications"]]
    assert "proposal_decided" in kinds_gip
    # Решённое предложение повторно не решается; аудит записан.
    r = st.wh.cmd("proposal.decide", {"id": p2, "decision": "accept"}, expected_version=r["proposal"]["version"])
    assert r["status"] == "rejected" and r["code"] == "proposal_state"
    assert audit(world, "proposal.decide", "stock_transfer_proposal", p2)
    assert audit(world, "proposal.propose", "stock_transfer_proposal", p1)


# ====================================================================== отзыв автором
def test_withdraw_by_author_only(world, st, proj):  # noqa: F811
    donor = _donor_project(world)
    pid = new_id()
    st.pr.ok("proposal.propose", {"id": pid, "warehouse_id": st.w, "item_id": st.item, "to_project_id": proj.pid,
                                  "from_project_id": donor, "qty": "1", "reason": "Нужно"})
    # Не автор отозвать не может (даже участник проекта-получателя).
    r = proj.gip.cmd("proposal.withdraw", {"id": pid, "reason": "Не моё"}, expected_version=1)
    assert r["status"] == "rejected" and r["code"] == "not_author"
    r = st.pr.ok("proposal.withdraw", {"id": pid, "reason": "Ошибся количеством"}, expected_version=1)
    assert r["proposal"]["status"] == "withdrawn" and r["proposal"]["decision_note"] == "Ошибся количеством"
    # Отозванное решать нельзя.
    r = st.wh.cmd("proposal.decide", {"id": pid, "decision": "accept"},
                  expected_version=r["proposal"]["version"])
    assert r["status"] == "rejected" and r["code"] == "proposal_state"


# ====================================================================== валидация, права, видимость, идемпотентность
def test_validation_permissions_visibility_idempotency(world, st, proj):  # noqa: F811
    donor = _donor_project(world)
    # Донор == получатель; несуществующий проект; лишний ключ.
    r = propose(st.pr, st, "1", project_from=proj.pid)
    assert r["status"] == "rejected" and r["code"] == "same_project"
    r = propose(st.pr, st, "1", to_project_id=str(uuid.uuid4()))
    assert r["status"] == "rejected"
    r = st.pr.cmd("proposal.propose", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item,
                                       "to_project_id": proj.pid, "qty": "1", "reason": "x", "unexpected": 1})
    assert r["status"] == "rejected" and r["code"] == "unknown_fields"
    # ГИП донора (head1) предлагать в чужой проект не может (нет project_role в получателе и он не закупка),
    # но ВИДИТ предложение — оно затрагивает его проект-донор (D-78).
    head = world.login("head1")
    r = propose(head, st, "1", project_from=donor)
    assert r["status"] == "denied"
    pid = new_id()
    st.pr.ok("proposal.propose", {"id": pid, "warehouse_id": st.w, "item_id": st.item, "to_project_id": proj.pid,
                                  "from_project_id": donor, "qty": "1", "reason": "Нужно"})
    assert _find_prop(_pull(head), pid) is not None
    # Совсем посторонний (lead1 — без роли в обоих проектах) предложение не видит.
    # Пауза в минуту: вход нового пользователя не должен упираться в ограничение попыток входа.
    world.clock.advance(seconds=61)
    assert _find_prop(_pull(world.login("lead1")), pid) is None
    # Участник проекта-получателя (eng1 — participant) предлагать может и видит предложение.
    proj.eng.ok("proposal.propose", {"id": new_id(), "warehouse_id": st.w, "item_id": st.item,
                                     "to_project_id": proj.pid, "qty": "1", "reason": "Инженеру нужно"})
    assert _find_prop(_pull(proj.eng), pid) is not None
    # Идемпотентность: тот же operation_id — тот же результат; тот же id объекта с новой операцией — конфликт.
    p = {"id": new_id(), "warehouse_id": st.w, "item_id": st.item, "to_project_id": proj.pid, "qty": "1",
         "reason": "Повтор"}
    op = new_id()
    r1 = st.pr.cmd("proposal.propose", p, op_id=op)
    r2 = st.pr.cmd("proposal.propose", p, op_id=op)
    assert r1["status"] == r2["status"] == "accepted"
    assert r1["result"]["proposal"]["id"] == r2["result"]["proposal"]["id"]
    r3 = st.pr.cmd("proposal.propose", {**p, "qty": "2"})
    assert (r3["status"], r3["code"]) == ("rejected", "id_exists")
