"""Офлайн-ядро клиента против продуктового сервиса (Class A, SQLite): очередь, отказ online_required, потеря ответа,
дедупликация, конфликт, отзыв прав офлайн, шифрование локального кэша, офлайн-разблокировка."""
import json
import sqlite3
import uuid

import pytest
import sqlalchemy as sa

from conftest import NEW_PWD, PWD, new_id
from vector.client.sync import SyncClient
from vector.client.transport import LoopbackTransport
from vector.client.vault import MemoryStore
from vector.server import db

FAST_KDF = {"n": 2 ** 10, "r": 8, "p": 1}
SERVER = "https://vector.test"

BACKENDS = ["sqlite"]  # клиентское ядро не зависит от СУБД сервера; серверные инварианты — в tests/server на SQLite+PG


@pytest.fixture
def mk(world, tmp_path):
    """Фикстура-фабрика клиентов: make(login) создаёт SyncClient поверх LoopbackTransport с общим хранилищем секретов и
    входит.
    """
    store = MemoryStore()

    def make(login, base=None, password=PWD):
        """Создать клиент для логина (свой каталог профилей, если base не задан) и войти; вход обязан пройти."""
        c = SyncClient(LoopbackTransport(world.svc), SERVER, base or (tmp_path / f"profiles-{uuid.uuid4().hex[:6]}"), store,
                       device_id=f"dev-{login}", clock=world.clock, kdf=FAST_KDF)
        r = c.login(login, password)
        assert r.status == "accepted", r
        return c
    make.store = store
    return make


def count(world, table, where=None):
    """Число строк в серверной таблице (с условием where) — проверка «записано ровно один раз»."""
    t = db.metadata.tables[table]
    q = sa.select(sa.func.count()).select_from(t)
    if where is not None:
        q = q.where(where)
    with world.svc.engine.connect() as c:
        return c.execute(q).scalar()


def test_queue_offline_and_sync_later(world, mk):
    rp = mk("rp1")
    rp.t.online = False
    cust, tender = new_id(), new_id()
    assert rp.execute("customer.create", {"id": cust, "name": "Офлайн-заказчик"}).status == "queued"
    assert rp.execute("tender.create", {"id": tender, "customer_id": cust, "title": "Офлайн-тендер"}).status == "queued"
    assert rp.get_local("customer", cust)["_pending"] is True
    assert count(world, "customers") == 0
    r = rp.execute("tender.transition", {"id": tender, "to": "in_work"}, expected_version=1)
    assert r.status in ("refused_offline", "blocked")
    assert rp.pending_count() == 2
    rp.t.online = True
    rep = rp.sync()
    assert (rep.accepted, rep.stopped_on) == (2, None)
    t = rp.get_local("tender", tender)
    assert t["_pending"] is False and t["version"] == 1


def test_online_required_offline_not_queued(world, mk):
    rp = mk("rp1")
    _, tender, _ = world.won_tender(world.login("rp1"))
    rp.pull()
    rp.t.online = False
    r = rp.execute("project.start_rkd", {"id": new_id(), "tender_id": tender, "code": "P", "title": "t"})
    assert r.status == "refused_offline" and rp.pending_count() == 0


def test_lost_response_is_deduplicated(world, mk):
    rp = mk("rp1")
    rp.t.online = False
    rp.execute("customer.create", {"id": new_id(), "name": "Дубль?"})
    rp.t.online = True
    rp.t.drop_next_response = True
    assert rp.sync().unknown_outcome and rp.pending_count() == 1
    rep = rp.sync()
    assert (rep.accepted, rep.replayed) == (1, 1)
    assert count(world, "customers") == 1
    assert count(world, "audit_log", db.audit_log.c.command == "customer.create") == 1


def test_lost_response_on_online_required(world, mk):
    rp = mk("rp1")
    _, tender, _ = world.won_tender(world.login("rp1"))
    rp.t.drop_next_response = True
    r = rp.execute("project.start_rkd", {"id": new_id(), "tender_id": tender, "code": "P-U", "title": "t"})
    assert r.status == "unknown_outcome"
    r2 = rp.retry_online(r.operation_id)
    assert r2.status == "accepted" and r2.response.get("replayed")
    assert count(world, "projects") == 1


def test_conflict_is_not_silently_merged(world, mk):
    rp, head = mk("rp1"), mk("rp2")
    cust = new_id()
    rp.execute("customer.create", {"id": cust, "name": "Исходное"})
    head.pull()
    rp.t.online = False
    rp.execute("customer.update", {"id": cust, "name": "Правка РП (офлайн)"}, expected_version=1)
    assert head.execute("customer.update", {"id": cust, "note": "Правка коллеги"}, expected_version=1).status == "accepted"
    rp.t.online = True
    assert rp.sync().stop_status == "conflict"
    probs = rp.problems()
    assert len(probs) == 1 and probs[0]["server_current"]["version"] == 2 and probs[0]["server_current"]["note"] == "Правка коллеги"
    with world.svc.engine.connect() as c:
        assert tuple(c.execute(sa.select(db.customers.c.name, db.customers.c.version)).one()) == ("Исходное", 2)
    r = rp.reapply_on_current(probs[0]["operation_id"])
    assert r.status == "accepted" and r.operation_id != probs[0]["operation_id"]
    with world.svc.engine.connect() as c:
        assert tuple(c.execute(sa.select(db.customers.c.name, db.customers.c.note, db.customers.c.version)).one()) == \
            ("Правка РП (офлайн)", "Правка коллеги", 3)


def test_conflict_discard_restores_server_state_offline(world, mk):
    rp, other = mk("rp1"), mk("rp2")
    cust = new_id()
    rp.execute("customer.create", {"id": cust, "name": "Исходное"})
    rp.t.online = False
    rp.execute("customer.update", {"id": cust, "name": "Моя правка"}, expected_version=1)
    other.execute("customer.update", {"id": cust, "name": "Чужая правка"}, expected_version=1)
    rp.t.online = True
    rp.sync()
    op = rp.problems()[0]["operation_id"]
    rp.t.online = False
    rp.discard(op)
    local = rp.get_local("customer", cust)
    assert (local["name"], local["_pending"]) == ("Чужая правка", False)


def test_sync_stops_preserving_order(world, mk):
    rp, other = mk("rp1"), mk("rp2")
    cust = new_id()
    rp.execute("customer.create", {"id": cust, "name": "A"})
    rp.t.online = False
    rp.execute("customer.update", {"id": cust, "name": "B"}, expected_version=1)
    rp.execute("tender.create", {"id": new_id(), "customer_id": cust, "title": "после конфликта"})
    other.execute("customer.update", {"id": cust, "name": "C"}, expected_version=1)
    rp.t.online = True
    assert rp.sync().stop_status == "conflict"
    assert rp.pending_count() == 1
    rp.discard(rp.problems()[0]["operation_id"])
    assert rp.sync().accepted == 1


def test_rights_revoked_while_offline(world, mk):
    rp = mk("rp1")
    rp.t.online = False
    cid = new_id()
    rp.execute("customer.create", {"id": cid, "name": "Пока был офлайн"})
    world.admin.ok("admin.revoke_role", {"grant_id": world.grants["rp1"], "reason": "смена функции"})
    rp.t.online = True
    assert rp.sync().stop_status == "denied"
    assert rp.problems()[0]["code"] == "no_authority"
    rp.discard(rp.problems()[0]["operation_id"])
    assert rp.get_local("customer", cid) is None


def test_pull_respects_read_rights(world, mk):
    mk("rp1").execute("customer.create", {"id": new_id(), "name": "Секретный"})
    eng = mk("eng1")
    eng.pull()
    assert eng.list_local("customer") == []


def test_session_revoked_keeps_queue(world, mk):
    rp = mk("rp1")
    world.admin.ok("admin.revoke_sessions", {"user_id": world.users["rp1"], "reason": "t"})
    r = rp.execute("customer.create", {"id": new_id(), "name": "X"})
    assert r.status == "queued" and rp.pending_count() == 1 and rp.token is None
    assert rp.relogin_online(PWD).status == "accepted"
    assert rp.sync().accepted == 1


# ---------------------------------------------------------------- защита локальных данных
def test_local_db_is_encrypted(world, mk, tmp_path):
    base = tmp_path / "p"
    rp = mk("rp1", base=base)
    rp.t.online = False
    rp.execute("customer.create", {"id": new_id(), "name": "Секретное ООО Ромашка", "inn": "7701234567"})
    rp.db.close()
    raw = b"".join(p.read_bytes() for p in base.rglob("*") if p.is_file())
    for needle in ("Ромашка".encode(), b"7701234567", PWD.encode()):
        assert needle not in raw


def test_tampered_local_row_detected(world, mk, tmp_path):
    from vector.client.vault import VaultError
    rp = mk("rp1", base=tmp_path / "p")
    rp.t.online = False
    a, b = new_id(), new_id()
    rp.execute("customer.create", {"id": a, "name": "A"})
    rp.execute("customer.create", {"id": b, "name": "B"})
    # Перенос шифртекста между строками обнаруживается (AAD = ключ строки).
    enc_b = rp.db.execute("SELECT data_enc FROM cache WHERE id=?", (b,)).fetchone()[0]
    rp.db.execute("UPDATE cache SET data_enc=? WHERE id=?", (enc_b, a))
    with pytest.raises(VaultError):
        rp.list_local("customer")


def test_offline_unlock_and_later_sync(world, mk, tmp_path):
    base = tmp_path / "p"
    rp = mk("rp1", base=base)
    rp.logout()
    c = SyncClient(LoopbackTransport(world.svc), SERVER, base, mk.store, "dev-rp1", clock=world.clock, kdf=FAST_KDF)
    c.t.online = False
    assert c.login("rp1", "wrong-password-123").status == "refused_offline"
    r = c.login("rp1", PWD)
    assert r.status == "offline_unlocked" and c.token is None
    cid = new_id()
    assert c.execute("customer.create", {"id": cid, "name": "Сделано офлайн"}).status == "queued"
    assert c.execute("admin.create_user", {"login": "x"}).status == "refused_offline"
    c.t.online = True
    assert c.sync().stop_status == "unauthenticated"
    assert c.relogin_online(PWD).status == "accepted"
    assert c.sync().accepted == 1 and count(world, "customers") == 1


def test_offline_unlock_limits(world, mk, tmp_path):
    base = tmp_path / "p"
    mk("rp1", base=base).logout()
    other_device = MemoryStore()  # другая учётная запись Windows / другой компьютер
    c = SyncClient(LoopbackTransport(world.svc), SERVER, base, other_device, "dev", clock=world.clock, kdf=FAST_KDF)
    c.t.online = False
    assert c.login("rp1", PWD).response["code"] == "device_secret_missing"
    c = SyncClient(LoopbackTransport(world.svc), SERVER, base, mk.store, "dev", clock=world.clock, kdf=FAST_KDF)
    c.t.online = False
    assert c.login("unknown", PWD).status == "refused_offline"
    world.clock.advance(days=8)
    assert c.login("rp1", PWD).response["code"] == "offline_expired"


def test_profiles_are_isolated_per_user(world, mk, tmp_path):
    base = tmp_path / "p"
    rp = mk("rp1", base=base)
    rp.execute("customer.create", {"id": new_id(), "name": "Только для rp1"})
    rp.logout()
    eng = mk("eng1", base=base)
    assert eng.list_local("customer") == []


def test_password_change_rewraps_and_keeps_queue(world, mk, tmp_path):
    base = tmp_path / "p"
    rp = mk("rp1", base=base)
    assert rp.change_password(PWD, NEW_PWD).status == "accepted"
    rp.t.online = False
    rp.execute("customer.create", {"id": new_id(), "name": "В очереди"})
    rp.logout()
    c = SyncClient(LoopbackTransport(world.svc), SERVER, base, mk.store, "dev", clock=world.clock, kdf=FAST_KDF)
    c.t.online = False
    assert c.login("rp1", PWD).status == "refused_offline"           # старый пароль больше не открывает профиль
    assert c.login("rp1", NEW_PWD).status == "offline_unlocked"
    assert c.pending_count() == 1  # ключ данных тот же — очередь читается под новым паролем


def test_admin_reset_orphans_old_cache_without_deleting(world, mk, tmp_path):
    base = tmp_path / "p"
    rp = mk("rp1", base=base)
    rp.t.online = False
    rp.execute("customer.create", {"id": new_id(), "name": "Не отправлено"})
    rp.logout()
    world.admin.ok("admin.reset_password", {"user_id": world.users["rp1"], "temporary_password": "Временный-пароль-1", "reason": "забыл"})
    c = SyncClient(LoopbackTransport(world.svc), SERVER, base, mk.store, "dev", clock=world.clock, kdf=FAST_KDF)
    r = c.login("rp1", "Временный-пароль-1")
    assert r.status == "accepted" and c.must_change_password
    assert c.orphaned_cache is not None and c.orphaned_cache.exists()
    assert c.pending_count() == 0


def test_pull_delivers_only_visible_active_assignments(world, mk):
    s = world.login("rp1")
    _, tender, _ = world.won_tender(s)
    pid = new_id()
    s.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-1", "title": "Проект"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["gip1"], "project_role": "gip", "reason": "Приказ"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp", "reason": "Приказ"})
    a_eng = s.ok("project.assign", {"project_id": pid, "user_id": world.users["eng1"], "project_role": "participant", "reason": "Работы"})
    rp = mk("rp1")
    assert rp.pull()
    mine = {(a["user_id"], a["project_role"]) for a in rp.get_meta("assignments") if a["project_id"] == pid}
    assert (world.users["gip1"], "gip") in mine and (world.users["eng1"], "participant") in mine
    # Пользователь без доступа к проекту (инженер не в команде; A-06: глобальные gip/rp/dept_head читают все) назначений не получает.
    other = mk("lead1")
    assert other.pull()
    assert [a for a in other.get_meta("assignments") if a["project_id"] == pid] == []
    # Отозванное назначение исчезает из кэша при следующем pull.
    aid = a_eng["assignment_id"]
    s.ok("project.revoke_assignment", {"assignment_id": aid, "reason": "Перевод"})
    assert rp.pull()
    assert (world.users["eng1"], "participant") not in {(a["user_id"], a["project_role"]) for a in rp.get_meta("assignments")}
    # В открытом виде в локальной БД назначения не хранятся (meta шифруется).
    raw = sqlite3.connect(str(rp.ov.path / "local.db")).execute("SELECT v_enc FROM meta WHERE k='assignments'").fetchone()[0]
    assert world.users["gip1"].encode() not in (raw if isinstance(raw, bytes) else raw.encode())


# ====================================================================== задачи и документы (клиент)
@pytest.fixture
def team(world, mk):
    """Проект с командой (rp1 — РП проекта, eng1 — участник) и комплектом; клиенты rp1 и eng1."""
    s = world.login("rp1")
    _, tender, _ = world.won_tender(s)
    pid, kit = new_id(), new_id()
    s.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-7", "title": "Проект"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp", "reason": "Приказ"})
    s.ok("project.assign", {"project_id": pid, "user_id": world.users["eng1"], "project_role": "participant", "reason": "Работы"})
    s.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Автоматизация"})
    rp, eng = mk("rp1"), mk("eng1")
    rp.pull(); eng.pull()
    return pid, kit, rp, eng


def test_task_created_offline_syncs_later(world, team):
    pid, kit, rp, eng = team
    rp.t.online = False
    tid = new_id()
    assert rp.execute("task.create", {"id": tid, "kit_id": kit, "title": "Офлайн-задача",
                                      "assignee_user_id": world.users["eng1"]}).status == "queued"
    assert rp.get_local("task", tid)["_pending"] is True
    assert count(world, "tasks") == 0
    rp.t.online = True
    rep = rp.sync()
    assert rep.accepted == 1 and count(world, "tasks") == 1
    eng.pull()
    t = eng.get_local("task", tid)
    assert t["assignee_user_id"] == world.users["eng1"] and t["status"] == "open"
    # Исполнитель двигает свою задачу; это тоже работает через очередь.
    eng.t.online = False
    assert eng.execute("task.transition", {"id": tid, "to": "in_progress"}, expected_version=1).status == "queued"
    eng.t.online = True
    assert eng.sync().accepted == 1


def test_document_version_requires_connection_and_roundtrips_file(world, team, tmp_path):
    pid, kit, rp, eng = team
    did = new_id()
    assert eng.execute("document.create", {"id": did, "kit_id": kit, "code": "АТХ-1", "title": "Схема"}).status == "accepted"
    data = "Схема автоматизации, лист 1\n".encode() * 5000
    eng.t.online = False
    eng.token, saved = None, eng.token        # офлайн-сеанс: токена нет
    r = eng.add_document_version(did, data, "АТХ-1.pdf", None, expected_version=1)
    assert r.status == "refused_offline" and eng.pending_count() == 0
    eng.token = saved
    eng.t.online = True
    r = eng.add_document_version(did, data, "АТХ-1.pdf", "Первая выдача", expected_version=1)
    assert r.status == "accepted", r
    assert [v["version_no"] for v in eng.document_versions(did)] == [1]
    rp.pull()
    dest = tmp_path / "АТХ-1 (скачано).pdf"
    d = rp.download_document_version(did, 1, dest)
    assert d.status == "accepted" and dest.read_bytes() == data
    assert not list(tmp_path.glob("*.vector-partial"))


def test_corrupted_download_is_not_saved(world, team, tmp_path):
    pid, kit, rp, eng = team
    did = new_id()
    eng.execute("document.create", {"id": did, "kit_id": kit, "code": "АТХ-2", "title": "Схема"})
    assert eng.add_document_version(did, b"original bytes", "a.pdf", None, expected_version=1).status == "accepted"
    eng.t.corrupt_next_download = True
    dest = tmp_path / "a.pdf"
    r = eng.download_document_version(did, 1, dest)
    assert r.status == "error" and "повреждён" in r.message_ru
    assert not dest.exists() and not list(tmp_path.glob("*.vector-partial"))


def test_add_version_from_path_streams_file_object(world, team, tmp_path):
    """Файл с диска передаётся транспорту открытым файлом (поток), а не байтами в памяти (issue #8)."""
    pid, kit, rp, eng = team
    did = new_id()
    eng.execute("document.create", {"id": did, "kit_id": kit, "code": "АТХ-4", "title": "Схема"})
    src = tmp_path / "Схема АТХ-4.dwg"
    data = bytes(range(256)) * 9000       # ~2.2 МБ: больше двух блоков
    src.write_bytes(data)
    seen = []
    orig = eng.t.upload

    def spy(token, project_id, source, sha256, size=None):
        seen.append((type(source).__name__, size))
        return orig(token, project_id, source, sha256, size)
    eng.t.upload = spy
    r = eng.add_document_version(did, src, src.name, None, expected_version=1)
    assert r.status == "accepted", r
    assert seen == [("BufferedReader", len(data))]
    dest = tmp_path / "out.dwg"
    assert eng.download_document_version(did, 1, dest).status == "accepted" and dest.read_bytes() == data
    # Нечитаемый путь — понятная ошибка, ничего не отправлено.
    r = eng.add_document_version(did, tmp_path / "нет такого.dwg", "x.dwg", None, expected_version=2)
    assert r.status == "error" and len(seen) == 1


def test_interrupted_download_leaves_nothing(world, team, tmp_path):
    """Обрыв посреди скачивания: ни целевого файла, ни «.vector-partial» (недокачанное не примут за чертёж)."""
    pid, kit, rp, eng = team
    did = new_id()
    eng.execute("document.create", {"id": did, "kit_id": kit, "code": "АТХ-5", "title": "Схема"})
    assert eng.add_document_version(did, b"0123456789" * 1000, "a.pdf", None, expected_version=1).status == "accepted"
    from vector.client.transport import ResponseLost

    def broken(token, document_id, version_no, sink):
        sink.write(b"01234")
        raise ResponseLost()
    eng.t.download = broken
    dest = tmp_path / "a.pdf"
    dest.write_bytes(b"old file stays")   # уже существующий файл с тем же именем не портится
    r = eng.download_document_version(did, 1, dest)
    assert r.status == "refused_offline"
    assert dest.read_bytes() == b"old file stays" and not list(tmp_path.glob("*.vector-partial"))


def test_lost_replies_in_file_flow_lose_nothing_and_duplicate_nothing(world, team, tmp_path):
    """Надёжность без потерь: ответ потерян на загрузке И на создании версии → повторы дают ровно одну версию,
    одно содержимое в хранилище, и скачанные байты совпадают с исходными."""
    pid, kit, rp, eng = team
    did = new_id()
    eng.execute("document.create", {"id": did, "kit_id": kit, "code": "АТХ-6", "title": "Схема"})
    src = tmp_path / "АТХ-6.dwg"
    data = bytes(range(251)) * 9000                       # ~2.2 МБ, несколько блоков
    src.write_bytes(data)
    # 1) Сервер принял файл, но ответ на загрузку потерян.
    eng.t.drop_next_response = True
    r = eng.add_document_version(did, src, src.name, None, expected_version=1)
    assert r.status == "unknown_outcome" and "безопасна" in r.message_ru
    # 2) Повтор: загрузка дедуплицируется, но ответ на создание версии снова потерян.
    orig_send = eng.t.send

    def drop_add_version(request):
        if request.get("command") == "document.add_version":
            eng.t.send = orig_send
            eng.t.drop_next_response = True
        return orig_send(request)
    eng.t.send = drop_add_version
    r = eng.add_document_version(did, src, src.name, None, expected_version=1)
    assert r.status == "unknown_outcome" and r.operation_id
    # 3) Повтор той же операции (тот же operation_id): сервер возвращает сохранённый исход, не создаёт вторую версию.
    r2 = eng.retry_online(r.operation_id)
    assert r2.status == "accepted" and r2.response.get("replayed")
    with world.svc.engine.connect() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(db.document_versions)).scalar() == 1
        assert c.execute(sa.select(sa.func.count()).select_from(db.file_blobs)).scalar() == 1
    assert count(world, "audit_log", db.audit_log.c.command == "document.add_version") == 1
    rp.pull()
    dest = tmp_path / "скачано.dwg"
    assert rp.download_document_version(did, 1, dest).status == "accepted" and dest.read_bytes() == data


def test_file_over_server_limit_refused_before_sending(world, team, tmp_path):
    """Issue #9: предел приходит с сервера при входе; файл больше предела отклоняется понятно и без отправки."""
    pid, kit, rp, eng = team
    assert eng.max_file_bytes == world.svc.sec.max_file_mb * 1024 * 1024
    did = new_id()
    eng.execute("document.create", {"id": did, "kit_id": kit, "code": "АТХ-7", "title": "Схема"})
    calls = []
    orig = eng.t.upload
    eng.t.upload = lambda *a, **k: calls.append(1) or orig(*a, **k)
    eng.max_file_bytes = 512 * 1024                 # имитируем сервер с пределом 0,5 МБ, не создавая гигабайтный файл
    big = tmp_path / "big.dwg"
    big.write_bytes(b"x" * (512 * 1024 + 1))
    r = eng.add_document_version(did, big, big.name, None, expected_version=1)
    assert r.status == "rejected" and r.response["code"] == "file_too_large" and "0,5 МБ" in r.message_ru
    assert calls == []
    # Ровно на пределе — отправляется (граница включительная, как на сервере).
    ok = tmp_path / "ok.dwg"
    ok.write_bytes(b"y" * (512 * 1024))
    assert eng.add_document_version(did, ok, ok.name, None, expected_version=1).status == "accepted" and calls == [1]
    # Сервер старой версии без поля — клиент не проверяет сам, решает сервер.
    eng.max_file_bytes = None
    assert eng.add_document_version(did, big, big.name, None, expected_version=2).status == "accepted"


def test_concurrent_version_is_conflict_on_client(world, team):
    pid, kit, rp, eng = team
    did = new_id()
    eng.execute("document.create", {"id": did, "kit_id": kit, "code": "АТХ-3", "title": "Схема"})
    rp.pull()
    assert eng.add_document_version(did, b"engineer v", "e.pdf", None, expected_version=1).status == "accepted"
    r = rp.add_document_version(did, b"rp v", "r.pdf", None, expected_version=1)   # РП основывался на старой карточке
    assert r.status == "conflict"


def test_document_versions_cached_encrypted(world, team):
    pid, kit, rp, eng = team
    did = new_id()
    eng.execute("document.create", {"id": did, "kit_id": kit, "code": "СЕКРЕТ-1", "title": "Закрытый документ"})
    eng.add_document_version(did, b"x", "Секретный чертёж.dwg", None, expected_version=1)
    eng.pull()
    raw = sqlite3.connect(str(eng.ov.path / "local.db"))
    blob = b"".join((r[0] if isinstance(r[0], bytes) else r[0].encode()) for r in raw.execute("SELECT v_enc FROM meta"))
    blob += b"".join((r[0] if isinstance(r[0], bytes) else r[0].encode()) for r in raw.execute("SELECT data_enc FROM cache"))
    assert "Секретный".encode() not in blob and "СЕКРЕТ-1".encode() not in blob


# --------------------------------------------------------------------------- склад (С4)
def test_stock_refs_queue_offline_postings_refused_and_snapshot_updates(world, mk):
    """Справочники склада — в очередь офлайн; проводки — только онлайн (не ставятся в очередь);
    принятая проводка сразу обновляет локальный снимок остатков, сторно помечает исходное движение."""
    world.make_user("wh1", "warehouse")
    wh = mk("wh1")
    wh.t.online = False
    w, it = new_id(), new_id()
    assert wh.execute("warehouse.create", {"id": w, "code": "СК-1", "name": "Центральный"}).status == "queued"
    assert wh.execute("item.create", {"id": it, "code": "КАБ-1", "name": "Кабель", "unit": "м"}).status == "queued"
    assert wh.get_local("stock_item", it)["_pending"] is True
    r = wh.execute("stock.receive", {"id": new_id(), "warehouse_id": w, "item_id": it, "qty": "10", "source_doc": "ТН 1"})
    # «blocked» — проводка ссылается на ещё не отправленный склад; в любом случае в очередь она не попадает.
    assert r.status in ("refused_offline", "blocked") and wh.pending_count() == 2
    assert count(world, "stock_movements") == 0

    wh.t.online = True
    assert wh.sync().accepted == 2
    assert wh.get_local("stock_item", it)["_pending"] is False
    wh.t.online = False                       # склад уже на сервере — теперь отказ именно «нужна связь»
    r = wh.execute("stock.receive", {"id": new_id(), "warehouse_id": w, "item_id": it, "qty": "10", "source_doc": "ТН 1"})
    assert r.status == "refused_offline" and wh.pending_count() == 0
    wh.t.online = True
    m = new_id()
    r = wh.execute("stock.receive", {"id": m, "warehouse_id": w, "item_id": it, "qty": "12.5", "source_doc": "ТН 1"})
    assert r.status == "accepted", r
    bal = [b for b in wh.get_meta("stock_balances") if b["item_id"] == it]
    assert [(b["on_hand"], b["available"]) for b in bal] == [("12.5000", "12.5000")]
    assert [x["id"] for x in wh.get_meta("stock_movements")] == [m]
    # Сторно — тоже онлайн; исходное движение в снимке помечается reversed_by.
    s = new_id()
    assert wh.execute("stock.reverse", {"id": s, "movement_id": m, "reason": "ошибка"}).status == "accepted"
    mv = {x["id"]: x for x in wh.get_meta("stock_movements")}
    assert mv[m]["reversed_by"] == s and mv[s]["kind_label"] == "Сторно прихода"
    assert [b["on_hand"] for b in wh.get_meta("stock_balances") if b["item_id"] == it] == ["0.0000"]
    # Полный pull даёт тот же результат, что и локальное применение ответов.
    wh.pull()
    assert [b["on_hand"] for b in wh.get_meta("stock_balances") if b["item_id"] == it] == ["0.0000"]
    assert {x["id"] for x in wh.get_meta("stock_movements")} == {m, s}


def test_stock_snapshot_hidden_without_stock_role(world, mk):
    """Пользователь без складской роли не получает остатков и движений (пустые списки, а не ошибка)."""
    world.make_user("wh1", "warehouse")
    wh = mk("wh1")
    w, it = new_id(), new_id()
    wh.execute("warehouse.create", {"id": w, "code": "СК-1", "name": "Центральный"})
    wh.execute("item.create", {"id": it, "code": "КАБ-1", "name": "Кабель", "unit": "м"})
    assert wh.execute("stock.receive", {"id": new_id(), "warehouse_id": w, "item_id": it, "qty": "1", "source_doc": "ТН"}).status == "accepted"
    rp = mk("rp1")
    rp.pull()
    assert rp.get_meta("stock_balances") in (None, []) and rp.get_meta("stock_movements") in (None, [])
    assert rp.list_local("stock_item") == [] and rp.list_local("warehouse") == []


def test_lost_response_with_password_keeps_no_password_and_replays(world, mk):
    """Ответ на создание пользователя потерян: в очереди клиента пароля нет (даже в зашифрованном виде), а повтор
    тем же operation_id всё равно получает сохранённый исход — сервер не включает пароли в хэш запроса."""
    adm = mk("admin")
    adm.t.drop_next_response = True
    r = adm.execute("admin.create_user", {"login": "new.user", "display_name": "Новый", "initial_password": PWD + "-x"})
    assert r.status == "unknown_outcome"
    row = adm.db.execute("SELECT payload_enc FROM outbox WHERE operation_id=?", (r.operation_id,)).fetchone()
    stored = json.loads(adm._open("outbox", r.operation_id, row[0]))
    assert "initial_password" not in stored and stored["login"] == "new.user"
    r2 = adm.retry_online(r.operation_id)
    assert r2.status == "accepted" and r2.response.get("replayed")
    assert count(world, "users", db.users.c.login == "new.user") == 1
