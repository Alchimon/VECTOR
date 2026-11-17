"""Клиент: представление замещений, «замещает» в истории согласования, контроль расчёта, сверка в кэше (D-34)."""
from vector.client import delegation_view as dv
from vector.client import purchasing_view as pv
from vector.client.backend import ROLE_LABELS, _plain_amount
from vector.client.sync import _po_same

ME, BOSS, BOB = "u-me", "u-boss", "u-bob"
PEOPLE = {ME: "Я Сотрудник", BOSS: "Орлова Елена", BOB: "Боб Замещающий"}


def _d(i, frm, to, role="po_approver", state="active", created=None, **kw):
    return {"id": i, "from_user_id": frm, "to_user_id": to, "role": role, "valid_from": "2026-11-16",
            "valid_until": "2026-11-30", "reason": "Отпуск", "created_by": created or frm, "state": state,
            "state_label": "Действует", **kw}


def test_role_labels_mark_delegated_roles():
    me = {"roles": ["accountant", "po_approver"], "own_roles": ["accountant"],
          "delegated_roles": [{"role": "po_approver", "valid_until": "2026-11-30", "from_user_id": BOSS}]}
    assert dv.role_labels(me, ROLE_LABELS) == ["Бухгалтерия", "Согласование закупок (замещение до 30.11.2026)"]
    # Старый кэш без own_roles — роли как свои.
    assert dv.role_labels({"roles": ["accountant"]}, ROLE_LABELS) == ["Бухгалтерия"]


def test_delegations_view_splits_and_hints():
    rows = [_d(1, ME, BOB), _d(2, BOB, ME, role="warehouse"), _d(3, BOSS, BOB, created=BOSS, state="ended"),
            _d(4, BOB, BOSS, role="rp", created=ME)]
    me = {"user_id": ME, "roles": ["admin", "po_approver"], "own_roles": ["admin", "po_approver"]}
    cov = [{"role": "po_approver", "state": "single", "holders": [ME], "deputies": [], "delegable": True},
           {"role": "admin", "state": "single", "holders": [ME], "deputies": [], "delegable": False},
           {"role": "rp", "state": "none", "holders": [], "deputies": [], "delegable": True},
           {"role": "gip", "state": "ok", "holders": [BOSS, BOB], "deputies": [], "delegable": True}]
    v = dv.delegations_view(rows, cov, PEOPLE, me, ROLE_LABELS, True, False)
    assert [r["id"] for r in v["given"]] == [1] and [r["id"] for r in v["received"]] == [2]
    assert [r["id"] for r in v["others"]] == [3, 4]
    # Отменить: своё (носитель/замещающий/оформивший) — да; чужое завершённое — нет.
    assert [r["can_revoke"] for r in v["given"] + v["received"] + v["others"]] == [True, True, False, True]
    assert v["given"][0]["period"] == "16.11.2026 — 30.11.2026" and v["given"][0]["tone"] == "success"
    # Передать можно свои роли, кроме «Администратор»; себя в списке замещающих нет.
    assert [r["id"] for r in v["role_options"]] == ["po_approver"]
    assert ME not in [p["id"] for p in v["people_options"]]
    # Покрытие: сначала «никого», «ок» не показывается; администратор — «выдайте второму».
    assert [c["role"] for c in v["coverage"]] == ["rp", "admin", "po_approver"] and v["warnings"] == 3
    assert "выдайте её второму" in v["coverage"][1]["text"]
    off = dv.delegations_view(rows, [], PEOPLE, me, ROLE_LABELS, False, True)
    assert (off["can_create"], off["can_manage"], off["manage"]) == (False, False, True)
    assert not any(r["can_revoke"] for r in off["given"] + off["others"])


def test_approval_events_show_deputy_and_invoice_reason():
    ev = [{"kind": "submit", "actor_id": ME, "at": "2026-11-16T09:00:00Z", "comment": None,
           "invoice_state": "mismatch", "invoice_reason": "НДС от итога", "diff_gross": "0.0000", "diff_vat": "0.0100"},
          {"kind": "approve", "actor_id": BOB, "at": "2026-11-16T10:00:00Z", "comment": "Ок", "on_behalf_of": BOSS}]
    a, b = pv._approval_events(ev, PEOPLE)
    assert a["invoice"] == "Расхождение со счётом поставщика: НДС от итога (разница НДС 0,01)" and a["invoice_warn"]
    assert b["label"].startswith("Согласован · Боб Замещающий (замещает Орлова Елена) · ")


def test_control_view_and_amount_normalization():
    from vector.core import purchasing as pu
    import uuid
    ls, _ = pu.parse_lines([{"line_id": str(uuid.uuid4()), "item_id": str(uuid.uuid4()), "qty": "10",
                             "unit_price": "122", "vat_rate": "22"}], "RUB")
    c = pv.control_view(ls, "included", "1220.00", "219.99")
    assert (c["invoice_state"], c["needs_reason"], c["has_problem"]) == ("mismatch", True, True)
    inv = next(x for x in c["checks"] if x["code"] == "invoice")
    assert (inv["tone"], inv["icon"]) == ("danger", "circle-x")
    assert pv.invoice_problem("1 220,50") == "" and "два знака" in pv.invoice_problem("1.005")
    assert (_plain_amount(" 1 220,50 "), _plain_amount(""), _plain_amount(None)) == ("1220.50", None, None)


def test_po_same_compares_invoice_amounts_as_numbers():
    base = {"supplier_id": "s", "currency": "RUB", "lines": [], "invoice_gross": "1220.0000"}
    assert _po_same(base, {**base, "invoice_gross": "1220.00"})
    assert not _po_same(base, {**base, "invoice_gross": "1220.01"})
    assert not _po_same(base, {**base, "invoice_gross": None})



def test_authority_label_for_delegated_role():
    """«Основание доступа» в истории: замещение — по-русски, без внутренних кодов (self слоту не нужен)."""
    from vector.client.backend import Backend
    fn = Backend.__dict__["authorityLabel"]
    assert fn(None, "delegated_role:po_approver#grant:9#delegation:3") == \
        "роль «Согласование закупок» по замещению №3 (выдача носителя №9)"
    assert fn(None, "global_role:rp#grant:2") == "роль «РП» (выдача №2)"
