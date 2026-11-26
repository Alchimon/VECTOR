"""Бизнес-ревизии документов РКД: проверка РП, замечания/ответы, трансмитталы (этап 3 blueprint 52, D-56).

Каждый тест закрепляет правило из server/rkd.py (чистые правила — vector.core.rkd, контракт — workflows docrev);
запускается на SQLite и на встроенном PostgreSQL. Кто есть кто: rp1 — РП проекта, gip1 — ГИП, eng1 — инженер
(участник), rp2 — второй РП (вне проекта, если не назначен).
"""
import hashlib

import pytest
import sqlalchemy as sa

from conftest import new_id
from vector.server import db


def pull(s):
    r = s.query("pull")
    assert r["status"] == "accepted", r
    return r["result"]


@pytest.fixture
def pd(world):
    """Проект в работе: РП, ГИП и инженер в команде; комплект и документ созданы."""
    rp = world.login("rp1")
    _cust, tender, _ = world.won_tender(rp)
    pid, kit, did = new_id(), new_id(), new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-2026-910", "title": "Шкафы АСУ ТП"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"],
                                      "project_role": "rp", "reason": "приказ"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["gip1"],
                                      "project_role": "gip", "reason": "приказ"})
    rp.ok("project.assign", {"project_id": pid, "user_id": world.users["eng1"],
                             "project_role": "participant", "reason": "работы"})
    rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Автоматизация"})
    eng = world.login("eng1")
    eng.ok("document.create", {"id": did, "kit_id": kit, "code": "АТХ-001", "title": "Схема функциональная"})
    return {"world": world, "pid": pid, "kit": kit, "doc": did, "rp": rp, "gip": world.login("gip1"), "eng": eng}


def upload(s, pid, data: bytes):
    """Загрузить байты файла в проект."""
    return s.svc.put_file(s.token, pid, data, hashlib.sha256(data).hexdigest(), s.addr)


def add_version(pd, payload: bytes, name="АТХ-001.pdf"):
    """Инженер загружает файл и добавляет версию документа; возвращает id версии."""
    eng, did = pd["eng"], pd["doc"]
    doc = [d for d in pull(eng)["documents"] if d["id"] == did][0]
    up = upload(eng, pd["pid"], payload)
    r = eng.ok("document.add_version", {"id": did, "sha256": up["sha256"], "file_name": name},
               expected_version=doc["version"])
    return r["document_version"]["id"]


def make_rev(pd, label="01", payload=None):
    """Создать ревизию документа от точной версии файла."""
    ver = add_version(pd, payload or f"файл ревизии {label}".encode())
    r = pd["eng"].ok("docrev.create", {"id": new_id(), "document_id": pd["doc"], "label": label,
                                       "doc_version_id": ver})
    return r["revision"]


def trans(s, rev, to, reason=None):
    p = {"id": rev["id"], "to": to}
    if reason:
        p["reason"] = reason
    return s.cmd("docrev.transition", p, expected_version=rev["version"])


def rev_of(pd, rev_id):
    return [r for r in pull(pd["eng"])["document_revisions"] if r["id"] == rev_id][0]


def ready_rev(pd, label="01"):
    """Ревизия, разрешённая к отправке: в работе → на проверке → разрешена."""
    rev = make_rev(pd, label)
    rev = trans(pd["eng"], rev, "ready_for_rp")["result"]["revision"]
    rev = trans(pd["rp"], rev, "ready_to_transmit")["result"]["revision"]
    return rev


# ====================================================================== создание ревизии
def test_revision_create_rules(pd):
    eng, rp = pd["eng"], pd["rp"]
    ver = add_version(pd, b"pdf v1")
    # Права: второй РП вне проекта ревизию не создаёт.
    rp2 = pd["world"].login("rp2")
    assert rp2.cmd("docrev.create", {"id": new_id(), "document_id": pd["doc"], "label": "01",
                                     "doc_version_id": ver})["status"] == "denied"
    # Версия файла должна принадлежать этому документу.
    assert eng.cmd("docrev.create", {"id": new_id(), "document_id": pd["doc"], "label": "01",
                                     "doc_version_id": 999999})["code"] == "version_not_found"
    rev = eng.ok("docrev.create", {"id": new_id(), "document_id": pd["doc"], "label": "01",
                                   "doc_version_id": ver})["revision"]
    assert (rev["seq"], rev["status"], rev["status_label"]) == (1, "in_work", "В работе")
    # Шифр уникален в документе без учёта регистра; вторая рабочая ревизия — отказ.
    ver2 = add_version(pd, b"pdf v2")
    assert eng.cmd("docrev.create", {"id": new_id(), "document_id": pd["doc"], "label": " 01 ",
                                     "doc_version_id": ver2})["code"] == "revision_label_taken"
    assert eng.cmd("docrev.create", {"id": new_id(), "document_id": pd["doc"], "label": "02",
                                     "doc_version_id": ver2})["code"] == "revision_in_progress"
    # Событие в журнале проекта.
    ev = [e for e in pull(rp)["project_events"] if e["kind"] == "revision_created"]
    assert len(ev) == 1 and "01" in ev[0]["summary"]


# ====================================================================== проверка: возврат не повышает ревизию
def test_review_cycle_return_keeps_label_and_writes_review(pd):
    eng, rp = pd["eng"], pd["rp"]
    rev = make_rev(pd)
    # Подача на проверку; РП получил уведомление.
    rev = trans(eng, rev, "ready_for_rp")["result"]["revision"]
    assert any(n["kind"] == "docrev_submitted" for n in pull(rp)["notifications"])
    # Инженер не вправе возвращать/разрешать — решения проверки только у РП (D-56 п. 1).
    assert trans(eng, rev, "returned_to_engineer", reason="x")["status"] == "denied"
    assert trans(eng, rev, "ready_to_transmit")["status"] == "denied"
    # Возврат — только с причиной; ревизия и файл не меняются.
    assert trans(rp, rev, "returned_to_engineer")["code"] == "reason_required"
    rev = trans(rp, rev, "returned_to_engineer", reason="штамп не по профилю")["result"]["revision"]
    assert (rev["status"], rev["label"], rev["seq"]) == ("returned_to_engineer", "01", 1)
    assert any(n["kind"] == "docrev_review" for n in pull(eng)["notifications"])
    reviews = [r for r in pull(eng)["document_reviews"] if r["revision_id"] == rev["id"]]
    assert [(r["cycle_no"], r["decision"], r["reason"]) for r in reviews] == \
        [(1, "returned", "штамп не по профилю")]
    # Повторный цикл: в работу → на проверку → разрешена; цикл проверки — № 2.
    rev = trans(eng, rev, "in_work")["result"]["revision"]
    rev = trans(eng, rev, "ready_for_rp")["result"]["revision"]
    rev = trans(rp, rev, "ready_to_transmit")["result"]["revision"]
    assert rev["status"] == "ready_to_transmit"
    reviews = [r for r in pull(eng)["document_reviews"] if r["revision_id"] == rev["id"]]
    assert [r["cycle_no"] for r in reviews] == [1, 2] and reviews[1]["decision"] == "accepted"
    # Отзыв разрешения на отправку — с причиной.
    assert trans(rp, rev, "in_work")["code"] == "reason_required"
    rev = trans(rp, rev, "in_work", reason="заказчик попросил повременить")["result"]["revision"]
    assert rev["status"] == "in_work"


def test_system_only_transitions_rejected(pd):
    rev = ready_rev(pd)
    # «Отправлена» ставит только трансмиттал; «замечания заказчика» — только регистрация замечания.
    assert trans(pd["rp"], rev, "sent")["code"] == "use_system_command"
    # ревизия отправлена трансмитталом:
    pd["rp"].ok("transmittal.create", {"id": new_id(), "project_id": pd["pid"], "recipient": "Заказчик",
                                           "channel": "email", "items": [rev["id"]]})["transmittal"]
    rev = rev_of(pd, rev["id"])
    assert rev["status"] == "sent"
    assert trans(pd["rp"], rev, "customer_comments")["code"] == "use_system_command"


# ====================================================================== трансмиттал
def test_transmittal_atomic_and_receipt_once(pd, engine):
    rp, eng = pd["rp"], pd["eng"]
    rev1 = ready_rev(pd, "01")
    # Вторая ревизия ещё в работе — в пакет нельзя.
    rev2 = make_rev(pd, "02", payload=b"pdf rev2")
    # Дубликат и «чужая» ревизия отклоняются.
    assert rp.cmd("transmittal.create", {"id": new_id(), "project_id": pd["pid"], "recipient": "З",
                                         "channel": "email", "items": [rev1["id"], rev1["id"]]}
                  )["code"] == "bad_value"
    assert rp.cmd("transmittal.create", {"id": new_id(), "project_id": pd["pid"], "recipient": "З",
                                         "channel": "email", "items": [rev1["id"], rev2["id"]]}
                  )["code"] == "revision_not_ready"
    assert rp.cmd("transmittal.create", {"id": new_id(), "project_id": pd["pid"], "recipient": "З",
                                         "channel": "fax", "items": [rev1["id"]]})["code"] == "bad_value"
    # Инженер пакет не формирует — только РП (D-56 п. 1).
    assert eng.cmd("transmittal.create", {"id": new_id(), "project_id": pd["pid"], "recipient": "З",
                                          "channel": "email", "items": [rev1["id"]]})["status"] == "denied"
    t = rp.ok("transmittal.create", {"id": new_id(), "project_id": pd["pid"], "recipient": "ООО Заказчик",
                                     "channel": "edo", "note": "на согласование",
                                     "items": [rev1["id"]]})["transmittal"]
    assert (t["seq"], t["status"], t["channel_label"]) == (1, "sent", "Система электронного документооборота")
    rev1 = rev_of(pd, rev1["id"])
    assert rev1["status"] == "sent"
    # Состав пакета неизменяем и виден в pull; автор ревизии уведомлён.
    data = pull(eng)
    assert [i["revision_id"] for i in data["transmittal_items"] if i["transmittal_id"] == t["id"]] == [rev1["id"]]
    assert any(n["kind"] == "transmittal_sent" for n in data["notifications"])
    # Подтверждение доставки — один раз.
    t = rp.ok("transmittal.register_receipt", {"id": t["id"], "external_ref": "квитанция ЭДО № 77"},
              expected_version=1)["transmittal"]
    assert t["status"] == "delivered" and t["delivered_at"]
    assert rp.cmd("transmittal.register_receipt", {"id": t["id"], "external_ref": "ещё раз"},
                  expected_version=2)["code"] == "already_delivered"
    # Состав пакета неизменяем (триггер БД).
    with engine.connect() as conn:
        with pytest.raises(sa.exc.DBAPIError):
            with conn.begin():
                conn.execute(sa.delete(db.transmittal_items))


# ====================================================================== замечания заказчика и доработка
def test_customer_comments_gate_and_next_revision(pd, engine):
    rp, eng = pd["rp"], pd["eng"]
    rev = ready_rev(pd, "01")
    rp.ok("transmittal.create", {"id": new_id(), "project_id": pd["pid"], "recipient": "Заказчик",
                                 "channel": "email", "items": [rev["id"]]})
    rev = rev_of(pd, rev["id"])
    # Замечание заказчика — только с основанием и только на отправленную ревизию; инженер его не регистрирует.
    cid = new_id()
    assert eng.cmd("comment.add", {"id": cid, "revision_id": rev["id"], "source": "customer",
                                   "text": "п. 3 не по ТЗ", "external_ref": "письмо 55"})["status"] == "denied"
    assert rp.cmd("comment.add", {"id": cid, "revision_id": rev["id"], "source": "customer",
                                  "text": "п. 3 не по ТЗ"})["code"] == "field_required"
    r = rp.ok("comment.add", {"id": cid, "revision_id": rev["id"], "source": "customer",
                              "text": "п. 3 не по ТЗ", "external_ref": "письмо № 55 от 16.11.2026"})
    rev = r["revision"]
    assert rev["status"] == "customer_comments"
    c2 = new_id()
    rp.ok("comment.add", {"id": c2, "revision_id": rev["id"], "source": "customer",
                          "text": "уточнить кабельный журнал", "external_ref": "письмо № 55 от 16.11.2026"})
    # Пока есть открытые замечания — ни в доработку, ни в «согласована».
    assert trans(eng, rev, "revision_in_work")["code"] == "comments_open"
    assert trans(rp, rev, "approved", reason="письмо № 60")["code"] == "comments_open"
    # Ответ с доказательством закрывает замечание; повторный ответ — отказ.
    proof = upload(eng, pd["pid"], "исправленный фрагмент".encode())
    cm = [c for c in pull(eng)["document_comments"] if c["id"] == cid][0]
    closed = eng.ok("comment.respond", {"id": cid, "text": "исправлено, см. файл", "sha256": proof["sha256"]},
                    expected_version=cm["version"])["comment"]
    assert closed["status"] == "closed" and closed["closed_by"] == pd["world"].users["eng1"]
    assert eng.cmd("comment.respond", {"id": cid, "text": "ещё раз"},
                   expected_version=closed["version"])["code"] == "comment_closed"
    # Чужой файл (загруженный не отвечающим) доказательством быть не может.
    other = upload(rp, pd["pid"], "чужой файл".encode())
    assert eng.cmd("comment.respond", {"id": c2, "text": "ответ", "sha256": other["sha256"]},
                   expected_version=1)["code"] == "file_not_uploaded"
    eng.ok("comment.respond", {"id": c2, "text": "журнал уточнён"}, expected_version=1)
    # Все замечания закрыты — инженер берёт в доработку, новая ревизия создаётся отдельно.
    rev = trans(eng, rev_of(pd, rev["id"]), "revision_in_work")["result"]["revision"]
    rev2 = make_rev(pd, "02", payload=b"pdf rev2 fixed")
    assert rev2["seq"] == 2 and rev2["label"] == "02"
    # Ответы на замечания неизменяемы и видны в pull.
    data = pull(eng)
    resp = [r for r in data["document_comment_responses"] if r["comment_id"] == cid]
    assert len(resp) == 1 and resp[0]["sha256"] == proof["sha256"]
    with engine.connect() as conn:
        with pytest.raises(sa.exc.DBAPIError):
            with conn.begin():
                conn.execute(sa.delete(db.document_comment_responses))


def test_approved_only_with_customer_basis(pd):
    rp, eng = pd["rp"], pd["eng"]
    rev = ready_rev(pd, "01")
    # «Согласована заказчиком» до отправки невозможна (нет такого перехода), после отправки — только с основанием.
    assert trans(rp, rev, "approved", reason="письмо")["code"] == "transition_not_allowed"
    rp.ok("transmittal.create", {"id": new_id(), "project_id": pd["pid"], "recipient": "Заказчик",
                                 "channel": "hand", "items": [rev["id"]]})
    rev = rev_of(pd, rev["id"])
    assert trans(rp, rev, "approved")["code"] == "reason_required"
    assert trans(eng, rev, "approved", reason="письмо № 61 от 20.11.2026")["status"] == "denied"
    rev = trans(rp, rev, "approved", reason="письмо № 61 от 20.11.2026")["result"]["revision"]
    assert rev["status"] == "approved"
    ev = [e for e in pull(rp)["project_events"] if e["object_id"] == rev["id"]]
    assert any(e["kind"] == "revision_status" and "Согласована" in e["summary"] for e in ev)


# ====================================================================== внутренние замечания, аудит, неизменяемость
def test_internal_comments_review_context_and_immutability(pd, engine):
    rp, eng = pd["rp"], pd["eng"]
    rev = make_rev(pd)
    # Внутреннее замечание — только в ходе проверки; «в работе» — отказ.
    cid = new_id()
    assert rp.cmd("comment.add", {"id": cid, "revision_id": rev["id"], "source": "internal",
                                  "text": "проверить шифр"})["code"] == "revision_not_in_review"
    rev = trans(eng, rev, "ready_for_rp")["result"]["revision"]
    rp.ok("comment.add", {"id": cid, "revision_id": rev["id"], "source": "internal",
                          "text": "проверить шифр тока", "page": "лист 2"})
    cm = [c for c in pull(eng)["document_comments"] if c["id"] == cid][0]
    assert cm["status"] == "open" and cm["page"] == "лист 2"
    # Аудит: команды записаны с основанием полномочий.
    h = rp.query("history", object_type="document_revision", object_id=rev["id"])
    assert h["status"] == "accepted"
    assert {r["command"] for r in h["result"]} >= {"docrev.create", "docrev.transition"}
    # Решения проверки неизменяемы (триггер БД).
    rev = trans(rp, rev, "returned_to_engineer", reason="доработать")["result"]["revision"]
    with engine.connect() as conn:
        with pytest.raises(sa.exc.DBAPIError):
            with conn.begin():
                conn.execute(sa.update(db.document_reviews).values(reason="подмена"))
