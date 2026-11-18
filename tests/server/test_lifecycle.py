"""Экземпляры изделий, паспорт, передача заказчику, сервис и замены (этап 6, сценарий С5 шаги 7–9; D-38).

Каждый тест закрепляет правило из server/lifecycle.py (правила — vector.core.lifecycle); запускается на SQLite и на
встроенном PostgreSQL. Кто есть кто — как в test_production: gip1 — ГИП, rp1 — РП, eng1 — участник (инженер),
lead1 — ведущий инженер (сначала вне команды проекта), head1 — руководитель отдела, tech1/tech2 — технологи,
prod1/prod2 — производство.
"""
import json
from datetime import timedelta

import pytest

from conftest import new_id
from test_production import _finish_all, approved_tp, ops_of, pd, pull, release  # noqa: F401
from test_stock import issue, receive, st  # noqa: F401
from test_tasks_documents import audit, make_doc, proj, upload  # noqa: F401
from vector.core import lifecycle as lc
from vector.server import db


def closed_wo(world, pd, qty=2):  # noqa: F811
    """Закрытый наряд на qty изделий: обе операции выполнены, кабель выдан со склада на наряд."""
    res = release(pd, approved_tp(pd), qty=qty)["result"]
    wo, ops = res["work_order"], ops_of(res)
    _finish_all(world, pd, ops)
    receive(pd.st.wh, pd.st, "100")
    assert issue(pd.st.wh, pd.st, str(int(10.5 * qty + 0.5)), work_order_id=wo["id"])["status"] == "accepted"
    wo = pull(pd.st.proj.rp)["work_orders"][-1]
    return pd.st.proj.rp.ok("wo.close", {"id": wo["id"]}, expected_version=wo["version"])["work_order"]


def register(s, wo, series, *serials):
    """instance.register от имени s: серия и заводские номера (новые id экземпляров)."""
    return s.cmd("instance.register", {"work_order_id": wo["id"], "series": series,
                                       "items": [{"id": new_id(), "serial_no": x} for x in serials]})


def today(world, days=0):
    """Дата «сегодня» по часам сервера (+ days) строкой ГГГГ-ММ-ДД."""
    return (world.clock.now() + timedelta(days=days)).date().isoformat()


@pytest.fixture
def wo(world, pd):  # noqa: F811
    """Закрытый наряд на 2 изделия."""
    return closed_wo(world, pd)


# ====================================================================== регистрация экземпляров (шаг 7)
def test_register_only_closed_wo_within_qty_and_by_rp(world, pd, wo):  # noqa: F811
    u = pd.st.proj
    # Права: РП проекта (резерв — руководитель отдела); ГИП, инженер, технолог, производство — нет.
    for s in (u.gip, u.eng, pd.tech, pd.prod):
        assert register(s, wo, "2026", "001")["status"] == "denied"
    # Только закрытый наряд.
    open_wo = release(pd, pull(u.rp)["tech_processes"][0], qty=1)["result"]["work_order"]
    assert register(u.rp, open_wo, "2026", "001")["code"] == "wo_not_closed"
    # Ввод: серия обязательна, номера — допустимые символы, без повторов в запросе.
    assert register(u.rp, wo, " ", "001")["code"] == "required"
    assert register(u.rp, wo, "2026", "00\"1")["code"] == "bad_chars"
    r = register(u.rp, wo, "2026", "А-1", "а-1")
    assert r["code"] == "serial_duplicate" and r["details"]["serials"] == ["а-1"]
    # Не больше количества наряда (2): три номера сразу — отказ, ни один не создан.
    r = register(u.rp, wo, "2026", "001", "002", "003")
    assert r["code"] == "qty_exceeded" and r["details"] == {"field": "items", "qty": 2, "registered": 0}
    one = register(u.rp, wo, "2026", "А-1")["result"]["product_instances"]
    assert [(i["serial_no"], i["series"], i["status"], i["wo_doc_no"]) for i in one] == [("А-1", "2026", "registered", wo["doc_no"])]
    # Номер уникален в области «изделие + серия» без учёта регистра…
    r = register(u.rp, wo, "2026", "а-1")
    assert r["code"] == "serial_taken" and r["details"]["serials"] == ["А-1"]
    # …но тот же номер в другой серии того же изделия допустим (не глобальная уникальность).
    two = register(pd.head, wo, "2027", "А-1")["result"]["product_instances"]
    assert two[0]["series"] == "2027"
    assert register(u.rp, wo, "2028", "005")["code"] == "qty_exceeded"
    ev = json.loads([a for a in audit(world, "instance.register") if a["result"] == "accepted"][-1]["details_json"])
    assert ev["event"] == "InstanceRegistered" and ev["snapshot_hash"] == wo["snapshot_hash"]


def test_instance_composition_exact_links_and_immutable(world, pd, wo):  # noqa: F811
    i = register(pd.st.proj.rp, wo, "2026", "001")["result"]["product_instances"][0]
    c = i["composition"]
    # Точные связи: проект, изделие, наряд и SHA-256 его снимка, ТП (редакция и хэш), версия расчёта.
    assert (c["project"]["id"], c["kit"]["id"], c["work_order"]["id"]) == (pd.st.proj.pid, pd.st.proj.kit, wo["id"])
    assert c["work_order"]["snapshot_hash"] == wo["snapshot_hash"] and c["work_order"]["spec_hash"] == wo["spec_hash"]
    assert c["tp"]["revision_no"] == 1 and len(c["tp"]["content_hash"]) == 64
    assert c["spec"]["calc_version_id"] == pd.spec_id
    # Состав ОДНОГО изделия — количество на единицу из снимка (без деления и без цен).
    lines = {ln["line_id"]: ln for ln in c["lines"]}
    assert lines[pd.line_cable]["qty_per_unit"] == "10.5000" and lines[pd.line_cable]["qty"] == "21.0000"
    assert "unit_price" not in lines[pd.line_work]
    assert i["composition_ok"] is True and i["composition_hash"] == lc.canon_hash(c)
    assert [o["state"] for o in i["operations"]] == ["done", "done"]
    with world.svc.engine.begin() as conn:
        with pytest.raises(Exception):
            conn.execute(db.instance_compositions.update().values(content_hash="0" * 64))


# ====================================================================== паспорт (шаг 8)
def _doc_with_version(world, proj, code="АТХ-ПС"):  # noqa: F811
    """Документ проекта с одной версией файла (для ссылки из паспорта)."""
    did = make_doc(proj, code)
    up = upload(proj.eng, proj.pid, b"%PDF passport doc " + code.encode())
    proj.eng.ok("document.add_version", {"id": did, "sha256": up["sha256"], "file_name": f"{code}.pdf"}, expected_version=1)
    return did, up["sha256"]


def test_passport_draft_rights_template_and_exact_documents(world, pd, wo):  # noqa: F811
    u = pd.st.proj
    inst = register(u.rp, wo, "2026", "001")["result"]["product_instances"][0]
    did, sha = _doc_with_version(world, u)
    base = {"instance_id": inst["id"], "fields": {"tech_data": "Питание 230 В"}}
    # Матрица 04: черновик паспорта — РП проекта или ведущий инженер. Ведущий инженер вне команды проекта — нет
    # (in_project); инженер-участник, ГИП, технолог — нет.
    for s in (u.lead, u.eng, u.gip, pd.tech):
        assert s.cmd("passport.create", {"id": new_id(), **base})["status"] == "denied", s
    u.rp.ok("project.assign", {"project_id": u.pid, "user_id": world.users["lead1"], "project_role": "participant",
                                      "reason": "Паспорт"})
    # Проверки ввода: неизвестный раздел, испытания (все замечания), документ чужой версии.
    assert u.rp.cmd("passport.create", {"id": new_id(), "instance_id": inst["id"], "fields": {"x": "1"}})["code"] == "unknown_section"
    r = u.rp.cmd("passport.create", {"id": new_id(), "instance_id": inst["id"],
                                     "tests": [{"name": "", "result": "pass", "date": "2026-11-10"},
                                               {"name": "Изоляция", "result": "ok", "date": "2026-13-01"}]})
    assert r["code"] == "tests_invalid" and [p["line"] for p in r["details"]["problems"]] == [0, 1]
    assert u.rp.cmd("passport.create", {"id": new_id(), "instance_id": inst["id"],
                                        "documents": [{"document_id": did, "version_no": 2}]})["code"] == "document_version_invalid"
    tests = [{"name": "Проверка изоляции", "result": "pass", "date": "2026-11-10", "protocol": "Протокол № 5"}]
    pid = new_id()
    p = u.lead.ok("passport.create", {"id": pid, **base, "tests": tests,
                                      "documents": [{"document_id": did, "version_no": 1}]})["passport"]
    t = lc.template(*lc.CURRENT_TEMPLATE)
    assert (p["template_code"], p["template_version"], p["template_hash"], p["template_ok"]) == ("passport", 1, lc.template_hash(t), True)
    assert p["fields"]["tech_data"] == "Питание 230 В" and p["fields"]["acceptance"] == ""
    assert p["documents"] == [{"document_id": did, "code": "АТХ-ПС", "title": "Схема функциональная", "version_no": 1,
                               "file_name": "АТХ-ПС.pdf", "sha256": sha}]
    assert p["tests"][0]["result"] == "pass" and len(p["content_hash"]) == 64
    # Один паспорт на экземпляр: второй — конфликт с текущим паспортом.
    r = u.rp.cmd("passport.create", {"id": new_id(), "instance_id": inst["id"]})
    assert r["status"] == "conflict" and r["details"]["current"]["id"] == pid
    # Сохранение: без изменений — unchanged; чужая версия — конфликт; переданные части заменяются целиком.
    assert u.rp.ok("passport.save", {"id": pid, "fields": p["fields"]}, expected_version=1).get("unchanged") is True
    p2 = u.rp.ok("passport.save", {"id": pid, "fields": {**p["fields"], "acceptance": "Изделие соответствует"}},
                 expected_version=1)["passport"]
    assert (p2["version"], p2["tests"], p2["documents"][0]["version_no"]) == (2, p["tests"], 1)
    assert p2["content_hash"] != p["content_hash"]
    assert u.lead.cmd("passport.save", {"id": pid, "tests": []}, expected_version=1)["code"] == "version_conflict"
    # Документ из другого проекта — отказ.
    assert u.rp.cmd("passport.save", {"id": pid, "documents": [{"document_id": new_id(), "version_no": 1}]},
                    expected_version=2)["code"] == "document_invalid"
    assert json.loads([a for a in audit(world, "passport.save") if a["result"] == "accepted"][-1]["details_json"]) == {"event": "PassportSaved", "changed": ["fields"],
                                                                             "content_hash": p2["content_hash"]}


def test_draft_export_only_rp_marked_logged_and_escaped(world, pd, wo):  # noqa: F811
    u = pd.st.proj
    inst = register(u.rp, wo, "2026", "001")["result"]["product_instances"][0]
    pid = new_id()
    u.rp.ok("passport.create", {"id": pid, "instance_id": inst["id"],
                                "fields": {"tech_data": "<script>alert(1)</script>", "acceptance": "Годен"}})
    # Черновой экспорт — только РП проекта (ГИП, руководитель отдела, ведущий инженер — нет).
    u.rp.ok("project.assign", {"project_id": u.pid, "user_id": world.users["lead1"], "project_role": "participant",
                                      "reason": "Паспорт"})
    for s in (u.gip, pd.head, u.lead):
        assert s.cmd("passport.export_draft", {"id": pid}, expected_version=1)["status"] == "denied"
    e = u.rp.ok("passport.export_draft", {"id": pid}, expected_version=1)
    x = e["export"]
    assert (x["export_no"], x["label"]) == (1, "Черновик, не сертификат")
    page = x["html"]
    assert page.count("ЧЕРНОВИК, НЕ СЕРТИФИКАТ") >= 3          # заголовок окна, полоса, водяной знак, колонтитул
    assert "не является сертификатом" in page and x["content_hash"] in page and inst["composition_hash"] in page
    assert "<script>" not in page and "&lt;script&gt;" in page   # ввод пользователя экранирован
    assert x["file_name"].startswith("Паспорт_черновик_") and x["file_name"].endswith("_экспорт1.html")
    # Каждый экспорт — в журнале (номер растёт), версия паспорта при этом не меняется.
    e2 = u.rp.ok("passport.export_draft", {"id": pid}, expected_version=1)
    assert e2["export"]["export_no"] == 2 and e2["passport"]["version"] == 1
    assert [(z["export_no"], z["content_hash"]) for z in e2["passport"]["exports"]] == [(1, x["content_hash"]), (2, x["content_hash"])]
    a = json.loads(audit(world, "passport.export_draft")[-1]["details_json"])
    assert a["event"] == "PassportDraftExported" and a["label"] == "Черновик, не сертификат"
    with world.svc.engine.begin() as conn:
        with pytest.raises(Exception):
            conn.execute(db.passport_exports.delete())


# ====================================================================== передача, сервис, замены (шаг 9)
def test_handover_service_and_replacement_history(world, pd, wo):  # noqa: F811
    u = pd.st.proj
    inst = register(u.rp, wo, "2026", "001")["result"]["product_instances"][0]
    iid = inst["id"]
    svc_p = {"id": iid, "kind": "inspection", "date": today(world), "description": "Осмотр после монтажа"}
    # Сервисные события — после передачи заказчику.
    assert u.rp.cmd("instance.service", svc_p)["code"] == "instance_state"
    ho = {"id": iid, "date": today(world), "recipient": "Иванов И. И., главный инженер", "document": "Акт № 7"}
    assert u.gip.cmd("instance.handover", ho, expected_version=1)["status"] == "denied"
    assert u.rp.cmd("instance.handover", {**ho, "date": today(world, 5)}, expected_version=1)["code"] == "date_in_future"
    assert u.rp.cmd("instance.handover", {**ho, "recipient": " "}, expected_version=1)["code"] == "required"
    h = u.rp.ok("instance.handover", ho, expected_version=1)["product_instance"]
    assert (h["status"], h["version"], h["events"][0]["kind"]) == ("handed_over", 2, "handover")
    assert h["events"][0]["details"]["customer"]["name"] == "АО Заказчик"
    assert u.rp.cmd("instance.handover", ho, expected_version=2)["status"] == "conflict"
    # Сервис: вид — из списка; руководитель отдела — резерв (отсутствие РП не останавливает работу, A-37).
    assert u.rp.cmd("instance.service", {**svc_p, "kind": "magic"})["code"] == "invalid_kind"
    s = pd.head.ok("instance.service", svc_p)["product_instance"]
    assert s["events"][1]["summary"] == "Осмотр: Осмотр после монтажа"
    # Замена: строка состава — только из состава экземпляра; состав при регистрации не меняется.
    rep = {"id": iid, "date": today(world), "removed": "Модуль ввода", "removed_serial": "M-1", "installed": "Модуль ввода",
           "installed_serial": "M-2", "reason": "Отказ канала 3"}
    assert u.rp.cmd("instance.replace", {**rep, "line_id": "нет-такой"})["code"] == "line_not_found"
    assert u.rp.cmd("instance.replace", {**rep, "qty": "0"})["code"] == "qty_not_positive"
    r = u.rp.ok("instance.replace", {**rep, "line_id": pd.line_cable})["product_instance"]
    assert [e["seq"] for e in r["events"]] == [1, 2, 3] and r["events"][2]["kind"] == "replacement"
    assert r["events"][2]["summary"] == "Замена: Модуль ввода № M-1 → Модуль ввода № M-2 (Отказ канала 3)"
    assert r["composition_hash"] == inst["composition_hash"]
    with world.svc.engine.begin() as conn:
        with pytest.raises(Exception):
            conn.execute(db.instance_events.update().values(kind="service"))
    # История объекта из журнала аудита — по праву чтения экземпляра.
    hist = u.rp.query("history", object_type="product_instance", object_id=iid)
    assert hist["status"] == "accepted" and len(hist["result"]) == 4
    assert pd.prod.query("history", object_type="product_instance", object_id=iid)["status"] == "denied"


def test_pull_visibility_and_registration_candidates(world, pd, wo):  # noqa: F811
    u = pd.st.proj
    _doc_with_version(world, u)
    register(u.rp, wo, "2026", "001")
    rp = pull(u.rp)
    assert [(w["doc_no"], w["qty"], w["registered"], w["left"]) for w in rp["instance_work_orders"]] == [(wo["doc_no"], 2, 1, 1)]
    assert [d["code"] for d in rp["passport_documents"]] == ["АТХ-ПС"]
    assert len(rp["product_instances"]) == 1
    # Технолог видит экземпляры (без кандидатов на регистрацию и документов паспорта); производство — ничего.
    t = pull(pd.tech)
    assert len(t["product_instances"]) == 1 and t["instance_work_orders"] == [] and t["passport_documents"] == []
    p = pull(pd.prod)
    assert p["product_instances"] == [] and p["passports"] == []


def test_core_rules_serials_dates_and_template():
    assert lc.parse_serials("2026", "001\n\n 002 \n") == ("2026", ["001", "002"])
    with pytest.raises(lc.LifecycleError) as e:
        lc.parse_serials("2026", [])
    assert e.value.code == "required"
    assert lc.parse_date("2026-02-28", "d", "дату") == "2026-02-28"
    with pytest.raises(lc.LifecycleError):
        lc.parse_date("28.02.2026", "d", "дату")
    t = lc.template(*lc.CURRENT_TEMPLATE)
    assert lc.text_keys(t)[:2] == ["tech_data", "resources"]
    assert lc.passport_problems(t, {}, []) == ["Не заполнен раздел «Основные технические данные».",
                                              "Не заполнен раздел «Ресурсы, сроки службы и хранения, гарантии изготовителя».",
                                              "Не заполнен раздел «Свидетельство о приёмке».", "Нет ни одного испытания."]
    with pytest.raises(lc.LifecycleError):
        lc.template("passport", 99)


def test_lifecycle_tables_added_in_v17():
    from vector.server import migrate as mg
    assert [t.name for t in mg.ADDED_IN[17]] == ["product_instances", "instance_compositions", "instance_events",
                                                 "passports", "passport_exports"]
    assert db.SCHEMA_VERSION >= 17 and 16 in mg.STEPS
