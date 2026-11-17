"""Настройки компании (07-configuration, D-26, A-26) и настройка «пересопоставление при резервах».

Что проверяется:
* каталог настроек из контрактов; не заданная настройка = самое строгое значение (strictest);
* публикует только администратор, только при связи, с причиной и версией; новая версия неизменяема;
* ошибки: неизвестная настройка, чужая область, недопустимое значение, «без изменений», конфликт версий;
* откат — публикация прежнего значения (история сохраняется);
* procurement.rematch_reserved_line: forbid → отказ; release_active → действующие резервы снимаются с причиной,
  но только у пользователя с правом stock.release (настройка не расширяет права); выданное — отказ всегда.
"""
import json

import pytest
import sqlalchemy as sa

from conftest import new_id
from test_need_matching import match, nd  # noqa: F401  (фикстура nd)
from test_stock import receive, st  # noqa: F401  (фикстура st)
from test_tasks_documents import audit, proj  # noqa: F401  (фикстура proj)
from vector.server import db

KEY = "procurement.rematch_reserved_line"


def publish(s, value, version, reason="Решение руководства", **kw):
    """config.publish для настройки пересопоставления."""
    return s.cmd("config.publish", {"key": KEY, "value": value, "reason": reason, **kw}, expected_version=version)


def setting(s):
    """Настройка из pull пользователя s."""
    return [c for c in s.query("pull")["result"]["config"] if c["key"] == KEY][0]


def test_unset_setting_is_strictest_and_visible_to_all(world, st):
    for s in (st.wh, st.pr, st.proj.gip, world.admin):
        c = setting(s)
        assert (c["value"], c["is_set"], c["version"], c["strictest"]) == ("forbid", False, 0, "forbid")
        assert {v["value"] for v in c["values"]} == {"forbid", "release_active"}
    assert world.svc.contracts.catalog["commands"]["config.publish"]["offline"] == "online_required"


def test_only_admin_publishes_with_reason_and_version(world, st):
    for s in (st.wh, st.pr, st.proj.gip, st.proj.rp):
        assert publish(s, "release_active", 0)["status"] == "denied"
    assert publish(world.admin, "release_active", 0, reason="")["code"] == "field_required"
    assert world.admin.cmd("config.publish", {"key": KEY, "value": "forbid", "reason": "x"})["code"] == "expected_version_required"
    assert world.admin.cmd("config.publish", {"key": "x.y", "value": "forbid", "reason": "x"},
                           expected_version=0)["code"] == "unknown_setting"
    assert publish(world.admin, "release_active", 0, scope_type="project")["code"] == "bad_scope"
    assert publish(world.admin, "всё можно", 0)["code"] == "bad_value"
    assert publish(world.admin, "release_active", 3)["status"] == "conflict"
    r = publish(world.admin, "release_active", 0, reason="Склад и закупка договорились")
    assert r["status"] == "accepted", r
    c = r["result"]["config"]
    assert (c["value"], c["is_set"], c["version"], c["reason"]) == ("release_active", True, 1,
                                                                    "Склад и закупка договорились")
    assert c["set_by"] and c["set_at"]
    assert publish(world.admin, "release_active", 1)["code"] == "no_changes"
    assert publish(world.admin, "forbid", 0)["status"] == "conflict"      # опоздавший администратор
    # Откат = публикация прежнего значения; явно заданное «forbid» отличается от «не задано».
    back = publish(world.admin, "forbid", 1, reason="Откат")["result"]["config"]
    assert (back["value"], back["is_set"], back["version"]) == ("forbid", True, 2)
    log = [json.loads(x["details_json"]) for x in audit(world, otype="config", oid=KEY)]
    assert log[0]["previous"] is None and log[0]["previous_effective"] == "forbid"
    assert (log[1]["value"], log[1]["previous"], log[1]["reason"]) == ("forbid", "release_active", "Откат")
    # Версии неизменяемы: подменить или удалить историю нельзя.
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as cn:
            cn.execute(db.config_versions.update().values(reason="подмена"))
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as cn:
            cn.execute(db.config_versions.delete())


def _reserved_line(st, need, cab, qty="1"):
    """Строка 2 (шкаф) сопоставлена с cab, под неё резерв qty. Возвращает (потребность, резерв)."""
    n = match(st.pr, need, 2, cab, reason="Первое решение")["result"]["need"]
    receive(st.wh, st, "5", item=cab)
    res = st.wh.ok("stock.reserve", {"id": new_id(), "warehouse_id": st.w, "item_id": cab, "project_id": st.proj.pid,
                                     "qty": qty, "reason": "Под шкаф", "need_id": n["id"],
                                     "need_line_id": n["lines"][2]["line_id"]})["reservation"]
    return n, res


def test_rematch_release_active_releases_only_with_release_right(world, nd):
    st, need, cab, rel = nd
    n, res = _reserved_line(st, need, cab)
    publish(world.admin, "release_active", 0)
    # Закупщик без права stock.release: настройка не расширяет права.
    r = match(st.pr, n, 2, rel, reason="Ошиблись: это реле")
    assert r["code"] == "need_line_reserved" and "права" in r["message_ru"]
    # Пользователь с обеими ролями (склад + закупка) — резерв снимается в той же транзакции.
    uid = world.make_user("wp1", "warehouse")
    world.admin.ok("admin.grant_role", {"user_id": uid, "role": "procurement", "reason": "test"})
    ok = match(world.login("wp1"), n, 2, rel, reason="Ошиблись: это реле")
    assert ok["status"] == "accepted", ok
    rows = [x for x in st.wh.query("pull")["result"]["stock_reservations"] if x["id"] == res["id"]]
    assert rows[0]["status"] == "released" and "Ошиблись: это реле" in rows[0]["close_reason"]
    a = [json.loads(x["details_json"]) for x in audit(world, otype="procurement_need", oid=need["id"])][-1]
    assert a["released_reservations"] == [{"id": res["id"], "released": "1.0000"}]
    ra = [json.loads(x["details_json"]) for x in audit(world, otype="stock_reservation", oid=res["id"])][-1]
    assert ra["by_rematch"] is True and ra["status"] == "released"


def test_rematch_forbid_default_and_consumed_always_refused(world, nd):
    st, need, cab, rel = nd
    n, res = _reserved_line(st, need, cab)
    r = match(st.pr, n, 2, rel)
    assert r["code"] == "need_line_reserved" and r["details"]["setting"] == "forbid"
    # Частично выдано со склада по резерву — пересопоставление отклоняется при любой настройке.
    st.wh.ok("stock.issue", {"id": new_id(), "warehouse_id": st.w, "item_id": cab, "qty": "1", "reason": "Монтаж",
                             "reservation_id": res["id"]})
    publish(world.admin, "release_active", 0)
    r = match(st.pr, n, 2, rel)
    assert r["code"] == "need_line_consumed" and r["details"]["consumed"] == "1.0000"
