"""Резервное копирование: шифрование, подлинность, целостность, восстановление со сверкой, переносимость между СУБД."""
import hashlib
import os
import struct

import pytest
import sqlalchemy as sa

from conftest import FakeClock, World, make_url, new_id
from vector.server import backup as bk
from vector.server import db
from vector.server.service import Service

PASS = "очень-длинная-фраза-для-копий"
FILE_CONTENT = bytes(range(256)) * 4500      # ~1.1 МБ: два блока file_chunks, все значения байтов


def _document_id(svc, token):
    return svc.handle({"type": "query", "token": token, "query": "pull"})["result"]["documents"][0]["id"]


def _populate(world):
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    rp.ok("tkp.create", {"id": new_id(), "tender_id": tender, "label": "ТКП", "amount": "1234567.8901", "currency": "RUB"})
    pid = new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "P-1", "title": "РКД"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp"})
    kit, did = new_id(), new_id()
    rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "АТХ", "title": "Автоматика"})
    # Документ с файлом больше одного блока хранилища и задача: файлы входят в ту же копию, что и метаданные.
    rp.ok("document.create", {"id": did, "kit_id": kit, "code": "АТХ-1", "title": "Схема"})
    up = world.svc.put_file(rp.token, pid, FILE_CONTENT, hashlib.sha256(FILE_CONTENT).hexdigest())
    assert up["status"] == "accepted", up
    rp.ok("document.add_version", {"id": did, "sha256": up["sha256"], "file_name": "схема.pdf"}, expected_version=1)
    rp.ok("task.create", {"id": new_id(), "kit_id": kit, "title": "Проверить схему", "document_id": did})
    return pid


@pytest.fixture
def keyfile(tmp_path):
    p = tmp_path / "backup.key"
    bk.generate_key_file(p)
    return p


def test_roundtrip_same_dbms(world, tmp_path, keyfile, backend, clock):
    _populate(world)
    out = tmp_path / "a.vbak"
    rep = bk.create_backup(world.svc.engine, out, bk.KeySource(key_file=keyfile))
    assert rep.counts["audit_log"] > 10 and not (tmp_path / "a.vbak.partial").exists()
    assert oct(keyfile.stat().st_mode & 0o777) == "0o600"
    assert bk.verify_backup(out, bk.KeySource(key_file=keyfile)).ok

    target = db.make_engine(make_url(backend, tmp_path))
    rr = bk.restore_backup(out, bk.KeySource(key_file=keyfile), target)
    assert rr.verified, rr.problems
    assert rr.counts == rep.counts
    assert db.check_schema(target) == db.SCHEMA_VERSION
    # Восстановленная система работает: старые пароли, новые операции, append-only, последовательности.
    svc2 = Service(target, clock=clock, sec=world.svc.sec)
    r = svc2.handle({"type": "login", "login": "rp1", "password": "correct-horse-battery", "device_id": "d"})
    assert r["status"] == "accepted"
    pull = svc2.handle({"type": "query", "token": r["token"], "query": "pull"})["result"]
    assert pull["tkp"][0]["amount"] == "1234567.8901"
    assert len(pull["tasks"]) == 1 and pull["document_versions"][0]["size_bytes"] == len(FILE_CONTENT)
    # Файл версии документа восстановлен побайтно и проходит проверку SHA-256 при скачивании.
    resp, content = svc2.get_file(r["token"], _document_id(svc2, r["token"]), 1)
    assert resp["status"] == "accepted" and content == FILE_CONTENT
    a = svc2.handle({"type": "login", "login": "admin", "password": "correct-horse-battery", "device_id": "d"})
    g = svc2.handle({"type": "command", "token": a["token"], "operation_id": new_id(), "command": "admin.grant_role",
                     "payload": {"user_id": world.users["eng1"], "role": "rp", "reason": "после восстановления"}})
    assert g["status"] == "accepted" and g["result"]["grant_id"] > max(world.grants.values())
    with pytest.raises(sa.exc.DBAPIError):
        with target.begin() as c:
            c.exec_driver_sql("DELETE FROM audit_log")
    target.dispose()


@pytest.mark.postgres
@pytest.mark.parametrize("src,dst", [("postgres", "sqlite"), ("sqlite", "postgres")])
def test_cross_dbms_restore(tmp_path, keyfile, src, dst):
    clock = FakeClock()
    from conftest import make_world_engine
    eng = make_world_engine(src, tmp_path, clock)
    world = World(Service(eng, clock=clock), clock)
    _populate(world)
    out = tmp_path / "x.vbak"
    rep = bk.create_backup(eng, out, bk.KeySource(passphrase=PASS))
    target = db.make_engine(make_url(dst, tmp_path))
    rr = bk.restore_backup(out, bk.KeySource(passphrase=PASS), target)
    assert rr.verified, rr.problems
    assert rr.counts == rep.counts
    # Файл из PostgreSQL (bytea) читается из SQLite (BLOB) и наоборот — побайтно.
    svc2 = Service(target, clock=clock, sec=world.svc.sec)
    r = svc2.handle({"type": "login", "login": "rp1", "password": "correct-horse-battery", "device_id": "d"})
    resp, content = svc2.get_file(r["token"], _document_id(svc2, r["token"]), 1)
    assert content == FILE_CONTENT, resp
    eng.dispose(); target.dispose()


def test_restore_refuses_non_empty_target(world, tmp_path, keyfile):
    out = tmp_path / "a.vbak"
    bk.create_backup(world.svc.engine, out, bk.KeySource(key_file=keyfile))
    with pytest.raises(bk.BackupError, match="не пуста"):
        bk.restore_backup(out, bk.KeySource(key_file=keyfile), world.svc.engine)


def test_no_overwrite_and_no_unencrypted(world, tmp_path, keyfile):
    out = tmp_path / "a.vbak"
    bk.create_backup(world.svc.engine, out, bk.KeySource(key_file=keyfile))
    with pytest.raises(bk.BackupError, match="уже существует"):
        bk.create_backup(world.svc.engine, out, bk.KeySource(key_file=keyfile))
    with pytest.raises(bk.BackupError, match="Незашифрованные"):
        bk.create_backup(world.svc.engine, tmp_path / "b.vbak", bk.KeySource())
    with pytest.raises(bk.BackupError, match="16"):
        bk.create_backup(world.svc.engine, tmp_path / "c.vbak", bk.KeySource(passphrase="short"))


def test_plaintext_not_visible_in_file(world, tmp_path, keyfile):
    _populate(world)
    out = tmp_path / "a.vbak"
    bk.create_backup(world.svc.engine, out, bk.KeySource(key_file=keyfile))
    raw = out.read_bytes()
    for needle in ("АО Заказчик".encode(), b"argon2id", b"1234567.8901", b"rp1"):
        assert needle not in raw


def test_wrong_key_rejected(world, tmp_path, keyfile):
    out = tmp_path / "a.vbak"
    bk.create_backup(world.svc.engine, out, bk.KeySource(passphrase=PASS))
    with pytest.raises(bk.BackupError, match="Проверка подлинности"):
        bk.verify_backup(out, bk.KeySource(passphrase=PASS + "!"))
    other = tmp_path / "other.key"
    bk.generate_key_file(other)
    out2 = tmp_path / "b.vbak"
    bk.create_backup(world.svc.engine, out2, bk.KeySource(key_file=keyfile))
    with pytest.raises(bk.BackupError, match="не подходит"):
        bk.verify_backup(out2, bk.KeySource(key_file=other))


def _header_end(raw: bytes) -> int:
    (hlen,) = struct.unpack(">I", raw[10:14])
    return 14 + hlen


@pytest.mark.parametrize("tamper", ["flip_byte", "truncate", "drop_last_chunk", "header", "append"])
def test_tampering_detected(world, tmp_path, keyfile, tamper):
    _populate(world)
    out = tmp_path / "a.vbak"
    bk.create_backup(world.svc.engine, out, bk.KeySource(key_file=keyfile))
    raw = bytearray(out.read_bytes())
    he = _header_end(raw)
    if tamper == "flip_byte":
        raw[he + 40] ^= 0x01
    elif tamper == "truncate":
        raw = raw[:-7]
    elif tamper == "drop_last_chunk":
        # единственный/последний блок удалён целиком -> нет завершающего блока
        raw = raw[:he]
    elif tamper == "header":
        raw[he - 3] = ord("x") if raw[he - 3] != ord("x") else ord("y")
    elif tamper == "append":
        raw += raw[he:]
    out.write_bytes(bytes(raw))
    with pytest.raises((bk.BackupError, ValueError)):
        bk.verify_backup(out, bk.KeySource(key_file=keyfile))


def test_failed_restore_leaves_db_unstartable(world, tmp_path, keyfile, monkeypatch, backend):
    """Если сверка после восстановления не сошлась, сервер с этой БД не запускается (fail-closed)."""
    _populate(world)
    out = tmp_path / "a.vbak"
    bk.create_backup(world.svc.engine, out, bk.KeySource(key_file=keyfile))
    real = bk.TableDigest.hexdigest
    calls = {"n": 0}

    def flaky(self):
        calls["n"] += 1
        return "0" * 64 if calls["n"] > len(db.BACKUP_TABLE_ORDER) else real(self)  # 1-й проход — проверка копии, 2-й — сверка БД
    monkeypatch.setattr(bk.TableDigest, "hexdigest", flaky)
    target = db.make_engine(make_url(backend, tmp_path))
    rr = bk.restore_backup(out, bk.KeySource(key_file=keyfile), target)
    assert not rr.verified
    with pytest.raises(RuntimeError, match="fail-closed"):
        db.check_schema(target)
    target.dispose()


def test_prune_keeps_newest(tmp_path):
    for i in range(5):
        p = tmp_path / f"b{i}.vbak"
        p.write_bytes(b"x")
        os.utime(p, (1000 + i, 1000 + i))
    (tmp_path / "keep.txt").write_text("не трогать")
    removed = bk.prune_backups(tmp_path, 2)
    assert sorted(p.name for p in removed) == ["b0.vbak", "b1.vbak", "b2.vbak"]
    assert (tmp_path / "keep.txt").exists()
    with pytest.raises(bk.BackupError):
        bk.prune_backups(tmp_path, 0)


def test_restore_limits_batch_by_bytes(world, tmp_path, keyfile, backend):
    """Пачка вставки ограничена и по объёму: блоки файлов (до 1 МБ каждый) не копятся сотнями в памяти."""
    _populate(world)
    out = tmp_path / "b.vbak"
    rep = bk.create_backup(world.svc.engine, out, bk.KeySource(key_file=keyfile))
    target = db.make_engine(make_url(backend, tmp_path))
    chunk_inserts = []

    @sa.event.listens_for(target, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO FILE_CHUNKS"):
            chunk_inserts.append(len(parameters) if executemany else 1)

    # Предел 1 байт: каждый блок файла вставляется отдельной пачкой, хотя по числу строк влез бы в одну.
    rr = bk.restore_backup(out, bk.KeySource(key_file=keyfile), target, batch=500, batch_bytes=1)
    assert rr.verified, rr.problems
    assert rep.counts["file_chunks"] >= 2
    assert chunk_inserts == [1] * rep.counts["file_chunks"]
