"""Клиент: замещение ролей в проекте и уведомления (D-35, ответы владельца на A-40) — чистые представления."""
from datetime import datetime, timedelta, timezone

from vector.client import delegation_view as dv
from vector.client import notification_view as nv
from vector.client.backend import ROLE_LABELS
from vector.client.sync import ORDER_FREE, STOCK_META

ME, GIP, BOB = "u-me", "u-gip", "u-bob"
PEOPLE = {ME: "Я Сотрудник", GIP: "Волков Павел", BOB: "Лебедев Максим"}
P1, P2 = "p-1", "p-2"
PROJECTS = {P1: "П-2026-014", P2: "П-2026-002"}
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def test_role_key_roundtrip_and_scoped_label():
    """Ключ пункта формы раскладывается обратно; подпись роли в проекте — «ГИП в проекте …»."""
    assert dv.role_key("gip", P1) == "gip@p-1" and dv.split_role_key("gip@p-1") == ("gip", P1)
    assert dv.role_key("po_approver") == "po_approver" and dv.split_role_key("po_approver") == ("po_approver", "")
    assert dv.scoped_label("gip", "П-2026-014", ROLE_LABELS) == "ГИП в проекте П-2026-014"
    assert dv.scoped_label("participant", "П-2026-014", ROLE_LABELS) == "Участник в проекте П-2026-014"
    assert dv.scoped_label("procurement", None, ROLE_LABELS) == "Закупка"


def test_role_labels_include_project_substitutions():
    """В профиле видно, какие роли в проектах получены замещением и до какого числа."""
    me = {"roles": ["engineer"], "own_roles": ["engineer"],
          "delegated_project_roles": [{"role": "gip", "project_id": P1, "project_code": "П-2026-014",
                                       "valid_until": "2026-11-22", "from_user_id": GIP}]}
    assert dv.role_labels(me, ROLE_LABELS) == ["Инженер", "ГИП в проекте П-2026-014 (замещение до 22.11.2026)"]


def test_delegations_view_project_roles_in_form_cards_and_coverage():
    """Форма предлагает мои назначения в проектах; карточка и покрытие подписаны проектом; руководителю — все."""
    me = {"user_id": GIP, "roles": ["gip"], "own_roles": ["gip"]}
    asg = [{"user_id": GIP, "project_id": P1, "project_role": "gip"},
           {"user_id": GIP, "project_id": P2, "project_role": "participant"},
           {"user_id": BOB, "project_id": P1, "project_role": "participant"}]
    rows = [{"id": 7, "from_user_id": GIP, "to_user_id": BOB, "role": "gip", "project_id": P1,
             "project_code": "П-2026-014", "valid_from": "2026-11-16", "valid_until": "2026-11-22",
             "reason": "Командировка", "created_by": GIP, "state": "active", "state_label": "Действует"}]
    cov = [{"role": "rp", "scope": "project", "project_id": P1, "project_code": "П-2026-014", "state": "none",
            "holders": [], "deputies": [], "delegable": True},
           {"role": "gip", "scope": "project", "project_id": P2, "project_code": "П-2026-002", "state": "single",
            "holders": [ME], "deputies": [], "delegable": True}]
    v = dv.delegations_view(rows, cov, PEOPLE, me, ROLE_LABELS, True, False, asg, PROJECTS)
    # Глобальная «ГИП» (своя) и обе мои роли в проектах — по коду проекта; чужое назначение (BOB) не предлагается.
    assert [o["id"] for o in v["role_options"]] == ["gip", "participant@p-2", "gip@p-1"]
    assert v["role_options"][2]["label"] == "ГИП в проекте П-2026-014"
    assert v["given"][0]["role_label"] == "ГИП в проекте П-2026-014"
    # Покрытие: подпись с проектом и ключ для кнопки «Оформить замещение».
    assert [(c["label"], c["role_key"]) for c in v["coverage"]] == [
        ("РП в проекте П-2026-014", "rp@p-1"), ("ГИП в проекте П-2026-002", "gip@p-2")]
    # За сотрудника: все глобальные роли (кроме администратора) и ГИП/РП/участник каждого проекта.
    ids = [o["id"] for o in v["all_role_options"]]
    assert "admin" not in ids and {"gip@p-1", "rp@p-1", "participant@p-1", "gip@p-2"} <= set(ids)
    # Без назначений и проектов (старый вызов) — как раньше.
    old = dv.delegations_view(rows, [], PEOPLE, me, ROLE_LABELS, True, False)
    assert [o["id"] for o in old["role_options"]] == ["gip"]


def _n(i, kind, minutes_ago, read=False, otype="purchase_order", oid="po-1"):
    at = (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    return {"id": i, "kind": kind, "kind_label": kind, "title": f"Заголовок {i}", "body": "Текст",
            "object_type": otype, "object_id": oid, "actor_id": BOB, "created_at": at,
            "read_at": at if read else None}


def test_notifications_view_marks_unread_and_targets():
    items = [_n(3, "po_submitted", 5), _n(2, "delegation_created", 60 * 26, otype="delegation", oid="7"),
             _n(1, "po_approved", 60 * 24 * 3, read=True)]
    v = nv.notifications_view(items, PEOPLE, True, NOW, timezone.utc)
    assert v["unread"] == 2 and v["unread_ids"] == [3, 2] and v["can_mark"] is True
    a, b, c = v["items"]
    assert (a["icon"], a["tone"], a["page"], a["actor_name"]) == ("shield-check", "warning", "procurement", "Лебедев Максим")
    assert (b["page"], b["unread"], c["unread"]) == ("delegations", True, False)
    assert a["when"] == "сегодня, 11:55" and b["when"] == "вчера, 10:00" and c["when"] == "13.11.2026, 12:00"
    # Офлайн — список виден, отметить нельзя; всё прочитано — тоже нечего отмечать.
    assert nv.notifications_view(items, PEOPLE, False, NOW)["can_mark"] is False
    assert nv.notifications_view([items[2]], PEOPLE, True, NOW)["can_mark"] is False
    # Неизвестный вид и объект — нейтрально, «Открыть» не показывается.
    odd = nv.notifications_view([{**items[0], "kind": "x", "object_type": None}], PEOPLE, True, NOW)["items"][0]
    assert (odd["icon"], odd["tone"], odd["page"]) == ("bell", "neutral", "")


def test_when_label_edge_cases():
    assert nv.when_label(None, NOW) == "" and nv.when_label("не дата", NOW) == "не дата"
    # Время без зоны считается UTC; в местной зоне (UTC+3) — уже следующий день.
    msk = timezone(timedelta(hours=3))
    assert nv.when_label("2026-11-15T22:30:00", NOW, msk) == "сегодня, 01:30"


def test_toast_for_new_notifications_only():
    """Подсказка — только о непрочитанных, пришедших после последнего показа."""
    items = [_n(5, "po_submitted", 1), _n(4, "po_returned", 2, read=True), _n(3, "po_submitted", 3)]
    assert [n["id"] for n in nv.new_since(items, 3)] == [5]
    assert nv.toast_text(nv.new_since(items, 3)) == "Уведомление: Заголовок 5"
    assert nv.toast_text(nv.new_since(items, 0)).startswith("Новых уведомлений: 2")
    assert nv.toast_text([]) == ""


def test_cache_and_queue_settings():
    """Уведомления и замещающие хранятся в кэше; «прочитано» не ждёт отправки очереди."""
    assert {"notifications", "project_deputies"} <= set(STOCK_META)
    assert "notification.read" in ORDER_FREE and "account.change_password" in ORDER_FREE


def test_authority_label_for_delegated_project_role():
    from vector.client.backend import Backend
    fn = Backend.__dict__["authorityLabel"]
    assert fn(None, "delegated_project_role:gip#assignment:12#delegation:4") == \
        "«ГИП проекта» по замещению №4 (назначение носителя №12)"
