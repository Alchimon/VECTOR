"""Экземпляры изделий и паспорт на клиенте (Class A, SQLite; D-38): регистрация и эксплуатация — только онлайн,
черновик паспорта — офлайн через очередь с цепочкой версий и без пустых сохранений, представление lifecycle_view.
"""
import json
import sys
from pathlib import Path

import pytest

# Стенд производства (закрытый наряд) — из серверных тестов: каталог tests/server добавляется в путь импорта, чтобы
# этот файл можно было запускать и отдельно от серверных тестов.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from conftest import new_id  # noqa: E402
from test_lifecycle import _doc_with_version, closed_wo  # noqa: E402
from test_offline_sync import BACKENDS, FAST_KDF, SERVER, mk  # noqa: E402,F401  (фикстура mk и общие константы)
from test_production import pd  # noqa: E402,F401  (фикстура)
from test_stock import st  # noqa: E402,F401  (фикстура)
from test_tasks_documents import proj  # noqa: E402,F401  (фикстура)
from vector.client import lifecycle_view as lv  # noqa: E402

_ = (BACKENDS, FAST_KDF, SERVER)


@pytest.fixture
def made(world, pd, mk):  # noqa: F811
    """Закрытый наряд на 2 изделия и клиент РП проекта (rp1) после первого pull. Возвращает (наряд, клиент)."""
    wo = closed_wo(world, pd)
    world.clock.advance(seconds=61)       # ограничение частоты входа
    c = mk("rp1")
    c.pull()
    return wo, c


def view(c):
    """Представление раздела «Изделия и паспорта» из кэша клиента c."""
    return lv.lifecycle_view(c.list_local("product_instance"), c.list_local("passport"), c.get_meta("instance_work_orders") or [],
                             c.get_meta("passport_documents") or [], c.get_meta("assignments") or [],
                             c.get_meta("project_deputies") or [], {p["id"]: p["display_name"] for p in c.get_meta("people") or []},
                             c.get_meta("me") or c.get_meta("session") or {}, c.t.online, c.get_meta("last_pull") or "")


def test_register_online_only_and_view(world, made):
    wo, c = made
    v = view(c)
    assert v["sees"] and v["may_register"] and v["can_register"] and v["items"] == []
    assert [o["left"] for o in v["register_options"]] == [2]
    c.t.online = False
    r = c.execute("instance.register", {"work_order_id": wo["id"], "series": "2026", "items": [{"id": new_id(), "serial_no": "001"}]})
    assert r.status == "refused_offline" and c.queue() == []
    c.t.online = True
    r = c.execute("instance.register", {"work_order_id": wo["id"], "series": "2026",
                                        "items": [{"id": new_id(), "serial_no": "001"}, {"id": new_id(), "serial_no": "002"}]})
    assert r.status == "accepted", r
    c.pull()
    v = view(c)
    assert [i["label"] for i in v["items"]] == ["АТХ № 001 (серия 2026)", "АТХ № 002 (серия 2026)"]
    assert v["register_options"] == [] and not v["can_register"]          # наряд исчерпан
    i = v["items"][0]
    assert i["composition_ok"] and i["can_handover"] and not i["can_service"] and i["can_passport"]
    keys = [x["k"] for x in i["links"]]
    assert "Снимок наряда, SHA-256" in keys and "Состав экземпляра, SHA-256" in keys
    cable = next(ln for ln in i["lines"] if ln["name"].startswith("Кабель"))
    assert cable["qty_label"] == "10,5 м"                                  # на одно изделие, не на наряд
    assert [o["state"] for o in i["operations"]] == ["done", "done"]


def test_passport_offline_chain_unchanged_sync_and_export(world, pd, made):  # noqa: F811
    wo, c = made
    inst = c.execute("instance.register", {"work_order_id": wo["id"], "series": "2026",
                                           "items": [{"id": new_id(), "serial_no": "001"}]}).response["result"]["product_instances"][0]
    did, sha = _doc_with_version(world, pd.st.proj)
    c.pull()
    assert [d["id"] for d in view(c)["doc_options"]] == [did]
    pid = new_id()
    fields = {"tech_data": "Питание 380 В", "resources": "", "acceptance": "Изделие соответствует"}
    tests = [{"name": "Сопротивление изоляции", "result": "pass", "date": "2026-11-16", "protocol": "П-1", "note": ""},
             {"name": "", "result": "pass", "date": "", "protocol": "", "note": ""}]          # пустая строка отбрасывается
    c.t.online = False
    body = lv.passport_payload(fields, tests, [{"document_id": did, "version_no": "1"}])
    assert len(body["tests"]) == 1 and body["documents"] == [{"document_id": did, "version_no": 1}]
    assert c.save_passport_draft({"id": pid, "instance_id": inst["id"], **body}).status == "queued"       # passport.create
    # Тот же текст — в очередь не ставится (сервер ответил бы «нет изменений», цепочка версий разошлась бы).
    assert c.save_passport_draft({"id": pid, "instance_id": inst["id"], **body}).response == {"unchanged": True}
    body2 = lv.passport_payload({**fields, "resources": "Гарантия 24 месяца"}, tests, [{"document_id": did, "version_no": 1}])
    assert c.save_passport_draft({"id": pid, "instance_id": inst["id"], **body2}).status == "queued"      # passport.save ev=1
    assert [(q["command"]) for q in c.queue()] == ["passport.create", "passport.save"]
    assert [r[0] for r in c.db.execute("SELECT expected_version FROM outbox ORDER BY seq")] == [None, 1]
    op_id, enc = c.db.execute("SELECT operation_id, payload_enc FROM outbox WHERE command='passport.save'").fetchone()
    assert "instance_id" not in json.loads(c._open("outbox", op_id, enc))   # экземпляр паспорта не меняется
    # Офлайн-паспорт уже виден в карточке (текущий шаблон, подпись документа — из справочника проекта).
    p = view(c)["items"][0]["passport"]
    assert p["pending"] and p["documents"][0]["label"].startswith("АТХ-ПС") and not p["can_export"]
    c.t.online = True
    rep = c.sync()
    assert (rep.accepted, rep.stopped_on) == (2, None)
    p = view(c)["items"][0]["passport"]
    assert (p["version"], p["pending"], p["template_ok"]) == (2, False, True)
    assert p["documents"][0]["sha256"] == sha and p["can_export"] and p["problems"] == []
    # Черновой экспорт — только онлайн: без связи отказ без очереди; при связи — HTML с пометкой «не сертификат».
    c.t.online = False
    assert c.execute("passport.export_draft", {"id": pid}, 2).status == "refused_offline" and c.queue() == []
    c.t.online = True
    r = c.execute("passport.export_draft", {"id": pid}, 2)
    assert r.status == "accepted", r
    x = r.response["result"]["export"]
    assert x["export_no"] == 1 and lv.lc.DRAFT_LABEL in x["html"]
    c.pull()
    assert len(view(c)["items"][0]["passport"]["exports"]) == 1


def test_passport_check_marks_bad_test_lines():
    tests = [{"name": "Изоляция", "result": "pass", "date": "2026-11-16", "protocol": "", "note": ""},
             {"name": "", "result": "pass", "date": "", "protocol": "", "note": ""},
             {"name": "Прогон", "result": "pass", "date": "16.11.2026", "protocol": "", "note": ""}]
    r = lv.passport_check({}, tests)
    assert not r["ok"] and list(r["by_line"]) == ["2"]                    # номер строки — как в форме (с пустой)
    assert lv.passport_check({}, tests[:2])["ok"]
