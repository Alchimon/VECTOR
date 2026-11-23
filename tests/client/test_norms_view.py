"""Представление норм трудоёмкости (этап 7 blueprint 52, D-72) — чистые функции, без сервера и Qt.

norm_proposals_view(proposals, me, online): подписи диапазона/статуса, кто предложил и кто решил, флаги
действий (предложить — технолог/РП/руководитель отдела при связи; решить — руководитель отдела/ведущий
инженер, не автор, при связи). Порядок: сначала ждущие решения, внутри — новые сверху.
"""
from vector.client.production_view import norm_proposals_view

ME_TECH = {"user_id": "u-tech", "roles": ["lead_engineer"]}
ME_HEAD = {"user_id": "u-head", "roles": ["dept_head"]}
ME_PROD = {"user_id": "u-prod", "roles": ["production"]}


def _prop(pid, status="proposed", author="u-tech", when="2026-11-16T09:00:00+00:00", **kw):
    """Предложение нормы как из pull (v_norm_proposal): диапазон — десятичные строки из БД."""
    return {"id": pid, "op_name": "Резка кабеля", "work_center": "Участок 1", "sample_size": 5,
            "min_min": "30.0000", "median_min": "45.0000", "max_min": "60.5000", "proposed_min": "45",
            "comment": kw.pop("comment", "По замерам ноября"), "status": status, "decision_reason": None,
            "created_by": author, "created_by_name": "Иванова А. И.", "created_at": when,
            "decided_by_name": None, "decided_at": None, "version": 1, **kw}


def test_norm_proposals_view_labels_order_and_flags():
    # Ждущее решение выше решённого; внутри группы — новые сверху; решать может не автор при связи.
    decided = _prop("p2", status="approved", when="2026-11-15T09:00:00+00:00",
                    decided_by_name="Петров Б. Б.", decided_at="2026-11-15T18:00:00+00:00")
    v = norm_proposals_view([decided, _prop("p1", when="2026-11-16T10:00:00+00:00"),
                             _prop("p3", when="2026-11-16T08:00:00+00:00")], ME_TECH, online=True)
    assert [r["id"] for r in v["rows"]] == ["p1", "p3", "p2"]
    r1 = v["rows"][0]
    assert r1["range_label"] == "факт: 30–60,5 мин, медиана 45 · выборка 5"
    assert r1["proposed_label"] == "45 мин" and r1["status_label"] == "предложена"
    # Ведущий инженер — автор p1: сам не решает; руководитель отдела — решает.
    assert r1["can_decide"] is False
    vh = norm_proposals_view([_prop("p1")], ME_HEAD, True)
    assert vh["rows"][0]["can_decide"] is True and vh["can_propose"] is True
    # Офлайн — действия недоступны; исполнителю предлагать нельзя.
    assert norm_proposals_view([_prop("p1")], ME_HEAD, False)["rows"][0]["can_decide"] is False
    vp = norm_proposals_view([_prop("p1")], ME_PROD, True)
    assert vp["can_propose"] is False and vp["rows"][0]["can_decide"] is False


def test_norm_proposals_view_decision_labels():
    # Отклонённое: подпись решения с кем/когда и причиной; утверждённое — без причины.
    rej = _prop("p1", status="rejected", decided_by_name="Петров Б. Б.",
                decided_at="2026-11-16T12:00:00+00:00", decision_reason="Проверить на следующем наряде")
    v = norm_proposals_view([rej], ME_TECH, True)["rows"][0]
    assert v["status_tone"] == "danger" and v["decision_reason"] == "Проверить на следующем наряде"
    assert "Петров Б. Б." in v["decision_label"] and v["can_decide"] is False
    assert norm_proposals_view([], ME_TECH, True) == {"rows": [], "can_propose": True}
