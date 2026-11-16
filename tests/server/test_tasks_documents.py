"""Задачи и документы с версиями (завершение среза 1): права, статусы, офлайн-устойчивость, файлы, аудит.

Каждый тест закрепляет одно правило из work.py; запускается на SQLite и на встроенном PostgreSQL.
Пользователи стенда (conftest.World): rp1 — РП, gip1 — ГИП, eng1 — инженер, lead1 — ведущий инженер, rp2 — второй РП.
"""
import hashlib
from dataclasses import dataclass

import pytest
import sqlalchemy as sa

from conftest import new_id
from vector.server import db


@dataclass
class Proj:
    """Проект с командой: ГИП gip1, РП rp1, участник eng1; один комплект. lead1 — вне команды."""
    pid: str
    kit: str
    rp: object
    gip: object
    eng: object
    lead: object


@pytest.fixture
def proj(world):
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    pid, kit = new_id(), new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-100", "title": "Проект"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["gip1"], "project_role": "gip", "reason": "Приказ"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp", "reason": "Приказ"})
    rp.ok("project.assign", {"project_id": pid, "user_id": world.users["eng1"], "project_role": "participant", "reason": "Работы"})
    rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Автоматизация"})
    return Proj(pid, kit, rp, world.login("gip1"), world.login("eng1"), world.login("lead1"))


def audit(world, command=None, otype=None, oid=None):
    t = db.audit_log
    q = sa.select(t).order_by(t.c.id)
    if command:
        q = q.where(t.c.command == command)
    if otype:
        q = q.where(t.c.object_type == otype, t.c.object_id == oid)
    with world.svc.engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def upload(s, pid, data: bytes, sha=None):
    return s.svc.put_file(s.token, pid, data, sha or hashlib.sha256(data).hexdigest(), s.addr)


# ====================================================================== задачи
def test_task_lifecycle_by_assignee_and_rp(world, proj):
    tid = new_id()
    t = proj.rp.ok("task.create", {"id": tid, "kit_id": proj.kit, "title": "Схема автоматизации",
                                   "assignee_user_id": world.users["eng1"], "due_date": "2026-12-20"})["task"]
    assert (t["status"], t["version"], t["due_date"], t["project_id"]) == ("open", 1, "2026-12-20", proj.pid)
    # Исполнитель берёт в работу и выполняет СВОЮ задачу.
    proj.eng.ok("task.transition", {"id": tid, "to": "in_progress"}, expected_version=1)
    t = proj.eng.ok("task.transition", {"id": tid, "to": "done"}, expected_version=2)["task"]
    assert t["status"] == "done"
    # Повторное открытие — только ГИП/РП и только с причиной.
    r = proj.eng.cmd("task.transition", {"id": tid, "to": "open", "reason": "ещё правки"}, expected_version=3)
    assert r["status"] == "denied"
    r = proj.rp.cmd("task.transition", {"id": tid, "to": "open"}, expected_version=3)
    assert r["code"] == "reason_required"
    t = proj.rp.ok("task.transition", {"id": tid, "to": "open", "reason": "Замечание заказчика"}, expected_version=3)["task"]
    assert (t["status"], t["status_reason"], t["version"]) == ("open", "Замечание заказчика", 4)
    kinds = [(a["command"], a["result"]) for a in audit(world, otype="task", oid=tid)]
    assert kinds == [("task.create", "accepted"), ("task.transition", "accepted"), ("task.transition", "accepted"),
                     ("task.transition", "accepted")]


def test_assignee_cannot_cancel_and_stranger_cannot_touch(world, proj):
    tid = new_id()
    proj.rp.ok("task.create", {"id": tid, "kit_id": proj.kit, "title": "Т", "assignee_user_id": world.users["eng1"]})
    r = proj.eng.cmd("task.transition", {"id": tid, "to": "cancelled", "reason": "не хочу"}, expected_version=1)
    assert (r["status"], r["code"]) == ("denied", "own_transition_not_allowed")
    # Ведущий инженер вне команды проекта: ни создать, ни двигать.
    assert proj.lead.cmd("task.create", {"id": new_id(), "kit_id": proj.kit, "title": "x"})["status"] == "denied"
    assert proj.lead.cmd("task.transition", {"id": tid, "to": "in_progress"}, expected_version=1)["status"] == "denied"
    # Участник, который НЕ исполнитель, двигать чужую задачу не может.
    other = new_id()
    proj.rp.ok("task.create", {"id": other, "kit_id": proj.kit, "title": "Чужая"})
    assert proj.eng.cmd("task.transition", {"id": other, "to": "in_progress"}, expected_version=1)["status"] == "denied"


def test_assignee_must_be_project_member_checked_at_effect_time(world, proj):
    r = proj.rp.cmd("task.create", {"id": new_id(), "kit_id": proj.kit, "title": "Т", "assignee_user_id": world.users["lead1"]})
    assert r["code"] == "assignee_not_in_project"
    # Назначение исполнителя отозвали — его «свой» переход больше не проходит (проверка в момент эффекта).
    tid = new_id()
    proj.rp.ok("task.create", {"id": tid, "kit_id": proj.kit, "title": "Т", "assignee_user_id": world.users["eng1"]})
    aid = [a for a in proj.rp.query("pull")["result"]["assignments"] if a["user_id"] == world.users["eng1"]][0]["id"]
    proj.rp.ok("project.revoke_assignment", {"assignment_id": aid, "reason": "Перевод"})
    assert proj.eng.cmd("task.transition", {"id": tid, "to": "in_progress"}, expected_version=1)["status"] == "denied"


def test_task_update_conflict_and_closed_task(world, proj):
    tid = new_id()
    proj.rp.ok("task.create", {"id": tid, "kit_id": proj.kit, "title": "Т"})
    proj.gip.ok("task.update", {"id": tid, "title": "Т (уточнено ГИП)"}, expected_version=1)
    r = proj.rp.cmd("task.update", {"id": tid, "title": "Т (РП, офлайн)"}, expected_version=1)
    assert r["status"] == "conflict" and r["details"]["current"]["title"] == "Т (уточнено ГИП)"
    # null очищает необязательное поле; пустой набор изменений — отказ.
    proj.rp.ok("task.update", {"id": tid, "due_date": "2026-12-01"}, expected_version=2)
    t = proj.rp.ok("task.update", {"id": tid, "due_date": None}, expected_version=3)["task"]
    assert t["due_date"] is None
    assert proj.rp.cmd("task.update", {"id": tid, "due_date": None}, expected_version=4)["code"] == "no_changes"
    proj.rp.ok("task.transition", {"id": tid, "to": "cancelled", "reason": "Дубль"}, expected_version=4)
    assert proj.rp.cmd("task.update", {"id": tid, "title": "x"}, expected_version=5)["code"] == "task_closed"


def test_task_replay_is_idempotent(world, proj):
    op, tid = new_id(), new_id()
    payload = {"id": tid, "kit_id": proj.kit, "title": "Офлайн-задача"}
    first = proj.rp.cmd("task.create", payload, op_id=op)
    again = proj.rp.cmd("task.create", payload, op_id=op)
    assert first["status"] == "accepted" and again.get("replayed") is True
    with world.svc.engine.connect() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(db.tasks)).scalar() == 1


# ====================================================================== документы и файлы
def make_doc(proj, code="АТХ-001"):
    did = new_id()
    proj.eng.ok("document.create", {"id": did, "kit_id": proj.kit, "code": code, "title": "Схема функциональная"})
    return did


def test_document_versions_are_immutable_and_numbered(world, proj):
    did = make_doc(proj)
    assert proj.eng.cmd("document.create", {"id": new_id(), "kit_id": proj.kit, "code": "АТХ-001", "title": "x"})["code"] == "document_code_taken"
    v1 = b"%PDF-1.7 schema v1"
    up = upload(proj.eng, proj.pid, v1)
    assert up["status"] == "accepted" and up["size_bytes"] == len(v1) and up["deduplicated"] is False
    r = proj.eng.ok("document.add_version", {"id": did, "sha256": up["sha256"], "file_name": "АТХ-001 схема.pdf",
                                             "comment": "Первая выдача"}, expected_version=1)
    assert r["document"]["current_version_no"] == 1 and r["document_version"]["version_no"] == 1
    up2 = upload(proj.eng, proj.pid, b"%PDF-1.7 schema v2")
    r = proj.eng.ok("document.add_version", {"id": did, "sha256": up2["sha256"], "file_name": "АТХ-001 схема.pdf"},
                    expected_version=2)
    assert r["document_version"]["version_no"] == 2
    # Старая версия сохранилась и скачивается без изменений.
    resp, content = world.svc.get_file(proj.gip.token, did, 1)
    assert resp["status"] == "accepted" and content == v1 and resp["file_name"] == "АТХ-001 схема.pdf"
    # Изменить или удалить версию нельзя даже напрямую в БД (триггер).
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as c:
            c.execute(db.document_versions.update().values(comment="подмена"))
    with pytest.raises(sa.exc.DBAPIError):
        with world.svc.engine.begin() as c:
            c.execute(db.document_versions.delete())


def test_concurrent_new_versions_conflict_not_overwrite(world, proj):
    did = make_doc(proj)
    a, b = upload(proj.eng, proj.pid, b"engineer file"), upload(proj.rp, proj.pid, b"rp file")
    proj.eng.ok("document.add_version", {"id": did, "sha256": a["sha256"], "file_name": "a.dwg"}, expected_version=1)
    r = proj.rp.cmd("document.add_version", {"id": did, "sha256": b["sha256"], "file_name": "b.dwg"}, expected_version=1)
    assert r["status"] == "conflict" and r["details"]["current"]["current_version_no"] == 1


def test_file_hash_capability_is_not_enough(world, proj):
    """Знать хэш чужого файла недостаточно: версию можно добавить только с файлом, который загрузил ты сам."""
    did = make_doc(proj)
    secret = b"confidential drawing"
    up = upload(proj.eng, proj.pid, secret)
    r = proj.rp.cmd("document.add_version", {"id": did, "sha256": up["sha256"], "file_name": "x.pdf"}, expected_version=1)
    assert r["code"] == "file_not_uploaded"
    # Тот же ответ, если файла нет вовсе — существование не раскрывается.
    r2 = proj.rp.cmd("document.add_version", {"id": did, "sha256": "0" * 64, "file_name": "x.pdf"}, expected_version=1)
    assert r2["code"] == "file_not_uploaded" and r2["message_ru"] == r["message_ru"]
    # Загрузив те же байты сам, РП получает квитанцию (содержимое не дублируется).
    assert upload(proj.rp, proj.pid, secret)["deduplicated"] is True
    proj.rp.ok("document.add_version", {"id": did, "sha256": up["sha256"], "file_name": "x.pdf"}, expected_version=1)
    with world.svc.engine.connect() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(db.file_blobs)).scalar() == 1


def test_upload_rules(world, proj, svc):
    data = b"payload"
    assert upload(proj.eng, proj.pid, data, sha="f" * 64)["code"] == "hash_mismatch"
    assert upload(proj.lead, proj.pid, data)["status"] == "denied"          # вне команды проекта
    assert upload(proj.eng, proj.pid, data, sha="nothex")["code"] == "invalid_sha256"
    big = b"x" * (svc.sec.max_file_mb * 1024 * 1024 + 1)
    assert upload(proj.eng, proj.pid, big)["code"] == "file_too_large"
    assert world.svc.put_file("bad-token", proj.pid, data, hashlib.sha256(data).hexdigest())["status"] == "unauthenticated"
    denied = [a for a in audit(world, command="file.upload") if a["result"] == "denied"]
    assert len(denied) == 1


def test_bad_file_names_rejected(world, proj):
    did = make_doc(proj)
    up = upload(proj.eng, proj.pid, b"data")
    for name in ("..\\..\\boot.ini", "a/b.pdf", "con:x", "name.", "\x01.pdf"):
        r = proj.eng.cmd("document.add_version", {"id": did, "sha256": up["sha256"], "file_name": name}, expected_version=1)
        assert r["code"] == "invalid_file_name", name


def test_download_rights_and_corruption_detected(world, proj):
    did = make_doc(proj)
    content = b"A" * (db.FILE_CHUNK + 10)   # больше одного блока: проверяем сборку из частей
    up = upload(proj.eng, proj.pid, content)
    proj.eng.ok("document.add_version", {"id": did, "sha256": up["sha256"], "file_name": "big.bin"}, expected_version=1)
    resp, got = world.svc.get_file(proj.eng.token, did, 1)
    assert got == content
    # Посторонний (вне проекта) и несуществующий документ — одинаковый отказ.
    r1, b1 = world.svc.get_file(proj.lead.token, did, 1)
    r2, b2 = world.svc.get_file(proj.lead.token, new_id(), 1)
    assert (r1["status"], b1, r1["message_ru"]) == ("denied", None, r2["message_ru"]) and b2 is None
    # Порча содержимого в хранилище обнаруживается при скачивании; файл не отдаётся.
    with world.svc.engine.begin() as c:
        c.execute(db.file_chunks.update().where(db.file_chunks.c.idx == 1).values(data=b"B" * 10))
    r, b = world.svc.get_file(proj.eng.token, did, 1)
    assert (r["code"], b) == ("file_corrupted", None)
    results = [a["result"] for a in audit(world, command="file.download")]
    assert results == ["accepted", "denied", "denied", "error"]
    # В журнале видно основание доступа (назначение участника), а у отказов основания нет.
    rows = audit(world, command="file.download")
    assert rows[0]["authority"].startswith("project_role:participant#assignment:")
    assert rows[1]["authority"] is None and rows[2]["authority"] is None
    ups = audit(world, command="file.upload")
    assert ups and all(u["authority"] and u["authority"].startswith("project_role:participant") for u in ups)


def test_pull_and_history_include_tasks_and_documents(world, proj):
    did = make_doc(proj)
    tid = new_id()
    proj.rp.ok("task.create", {"id": tid, "kit_id": proj.kit, "title": "Замечание к схеме", "document_id": did})
    up = upload(proj.eng, proj.pid, b"v1")
    proj.eng.ok("document.add_version", {"id": did, "sha256": up["sha256"], "file_name": "v1.pdf"}, expected_version=1)
    res = proj.eng.query("pull")["result"]
    assert [t["id"] for t in res["tasks"]] == [tid] and res["tasks"][0]["document_id"] == did
    assert [d["id"] for d in res["documents"]] == [did]
    assert [(v["document_id"], v["version_no"]) for v in res["document_versions"]] == [(did, 1)]
    assert "data" not in res["document_versions"][0]       # содержимое файлов в pull не передаётся
    outsider = proj.lead.query("pull")["result"]
    assert outsider["tasks"] == [] and outsider["documents"] == [] and outsider["document_versions"] == []
    h = proj.eng.query("history", object_type="document", object_id=did)
    assert h["status"] == "accepted" and [x["command"] for x in h["result"]] == ["document.create", "document.add_version"]
    assert proj.lead.query("history", object_type="task", object_id=tid)["status"] == "denied"


def test_task_document_link_must_be_same_project(world, proj):
    rp = proj.rp
    _, tender2, _ = world.won_tender(rp)
    pid2, kit2 = new_id(), new_id()
    rp.ok("project.start_rkd", {"id": pid2, "tender_id": tender2, "code": "П-200", "title": "Другой"})
    world.admin.ok("project.assign", {"project_id": pid2, "user_id": world.users["rp1"], "project_role": "rp", "reason": "Приказ"})
    rp.ok("kit.create", {"id": kit2, "project_id": pid2, "code": "ЭМ", "title": "Электрика"})
    did = make_doc(proj)
    r = rp.cmd("task.create", {"id": new_id(), "kit_id": kit2, "title": "x", "document_id": did})
    assert r["code"] == "document_not_in_project"
