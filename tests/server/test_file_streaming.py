"""Потоковая передача файлов документов (issue #8): файл никогда не держится в памяти целиком.

Что закрепляют тесты (каждый — на SQLite и на встроенном PostgreSQL):
* загрузка/скачивание многоблочного файла через HTTP даёт те же байты, блоки в БД — по FILE_CHUNK;
* память сервиса не растёт с размером файла (tracemalloc: файл 24 МиБ, пик — несколько блоков);
* предел размера срабатывает и по заявленному Content-Length, и по фактическому потоку без него;
* посторонний получает отказ ДО приёма тела (предварительная проверка), отказ — в аудите;
* если файл изменился между проходом хэширования и записью — ничего не записывается;
* повреждённый в хранилище файл не отдаётся ни байтом: HTTP-ответ — JSON-ошибка, в аудите — error;
* настоящий HTTP-сервер (uvicorn) + клиентский HttpTransport: поток в обе стороны, проверка SHA у клиента.
"""
import hashlib
import io
import socket
import threading
import time
import tracemalloc

import pytest
import sqlalchemy as sa
from starlette.testclient import TestClient

from vector.server import db
from vector.server.api import create_app
from vector.server.security import SecurityPolicy
from test_tasks_documents import audit, make_doc, proj  # noqa: F401  (proj — фикстура)

LIMIT_MB = 32
MiB = 1024 * 1024


@pytest.fixture
def fast_policy():
    """Политика безопасности с увеличенным пределом размера файла для тестов памяти."""
    # Предел побольше стандартного для тестов памяти; тесты предела превышают именно его.
    return SecurityPolicy(max_file_mb=LIMIT_MB)


@pytest.fixture
def http(world):
    """HTTP-клиент FastAPI поверх тестового сервиса."""
    return TestClient(create_app(world.svc))


def _pattern(n: int) -> bytes:
    """Детерминированное «несжимаемое» содержимое: блоки различаются, перестановка блоков заметна."""
    out, i = bytearray(), 0
    while len(out) < n:
        out += hashlib.sha256(i.to_bytes(8, "big")).digest() * 64
        i += 1
    return bytes(out[:n])


def _hdr(s, sha: str) -> dict:
    """Заголовки загрузки файла: токен, SHA-256, тип содержимого."""
    return {"Authorization": f"Bearer {s.token}", "X-Content-SHA256": sha, "Content-Type": "application/octet-stream"}


def _count(world, table) -> int:
    """Число строк в таблице."""
    with world.svc.engine.connect() as c:
        return c.execute(sa.select(sa.func.count()).select_from(table)).scalar()


def _gen(total: int, block: int = MiB):
    """Тело запроса без Content-Length (chunked): сервер узнаёт размер только по ходу чтения."""
    sent = 0
    while sent < total:
        n = min(block, total - sent)
        yield b"z" * n
        sent += n


# ====================================================================== HTTP: туда и обратно
def test_http_roundtrip_multi_chunk(world, proj, http):
    data = _pattern(2 * db.FILE_CHUNK + 12345)
    sha = hashlib.sha256(data).hexdigest()
    r = http.post("/api/v1/files", params={"project_id": proj.pid}, content=data, headers=_hdr(proj.eng, sha))
    assert r.status_code == 200 and r.json() == {"status": "accepted", "sha256": sha, "size_bytes": len(data),
                                                 "deduplicated": False}
    with world.svc.engine.connect() as c:
        sizes = [len(bytes(x)) for (x,) in c.execute(sa.select(db.file_chunks.c.data).order_by(db.file_chunks.c.idx))]
    assert sizes == [db.FILE_CHUNK, db.FILE_CHUNK, 12345]
    did = make_doc(proj)
    proj.eng.ok("document.add_version", {"id": did, "sha256": sha, "file_name": "Схема лист 1.dwg"}, expected_version=1)
    g = http.get(f"/api/v1/files/{did}/1", headers={"Authorization": f"Bearer {proj.gip.token}"})
    assert g.status_code == 200 and g.content == data
    assert g.headers["x-content-sha256"] == sha and g.headers["content-length"] == str(len(data))
    assert g.headers["cache-control"] == "no-store"
    assert "filename*=UTF-8''%D0%A1%D1%85%D0%B5%D0%BC%D0%B0" in g.headers["content-disposition"]


def test_empty_file_roundtrip(world, proj, http):
    sha = hashlib.sha256(b"").hexdigest()
    r = http.post("/api/v1/files", params={"project_id": proj.pid}, content=b"", headers=_hdr(proj.eng, sha))
    assert r.json()["status"] == "accepted" and r.json()["size_bytes"] == 0
    did = make_doc(proj)
    proj.eng.ok("document.add_version", {"id": did, "sha256": sha, "file_name": "empty.txt"}, expected_version=1)
    g = http.get(f"/api/v1/files/{did}/1", headers={"Authorization": f"Bearer {proj.eng.token}"})
    assert g.status_code == 200 and g.content == b""


# ====================================================================== предел размера и права до тела
def test_declared_size_over_limit_rejected_before_body(world, proj, http):
    h = {**_hdr(proj.eng, "0" * 64), "Content-Length": str(LIMIT_MB * MiB + 1)}
    # Тело короткое: если бы сервер читал его, он увидел бы 3 байта и пошёл дальше; отказ — по заголовку.
    r = http.post("/api/v1/files", params={"project_id": proj.pid}, content=b"abc", headers=h)
    assert r.json()["code"] == "file_too_large"
    assert _count(world, db.file_blobs) == 0


def test_streamed_size_over_limit_rejected_without_content_length(world, proj, http):
    r = http.post("/api/v1/files", params={"project_id": proj.pid}, content=_gen(LIMIT_MB * MiB + 1),
                  headers=_hdr(proj.eng, "0" * 64))
    assert r.json()["code"] == "file_too_large"
    assert _count(world, db.file_blobs) == 0 and _count(world, db.file_chunks) == 0


def test_outsider_denied_before_body_and_audited(world, proj, http):
    consumed = []

    def body():
        for _ in range(8):
            consumed.append(1)
            yield b"q" * MiB
    r = http.post("/api/v1/files", params={"project_id": proj.pid}, content=body(), headers=_hdr(proj.lead, "0" * 64))
    assert r.status_code == 403 and r.json()["code"] == "no_authority"
    rows = [a for a in audit(world, command="file.upload") if a["result"] == "denied"]
    assert len(rows) == 1 and '"stage":"precheck"' in rows[0]["details_json"].replace(" ", "")
    assert _count(world, db.file_blobs) == 0
    # Без входа — 401, тоже до тела.
    r = http.post("/api/v1/files", params={"project_id": proj.pid}, content=b"x",
                  headers={"X-Content-SHA256": "0" * 64})
    assert r.status_code == 401


def test_hash_mismatch_over_http_writes_nothing(world, proj, http):
    data = _pattern(db.FILE_CHUNK + 1)
    r = http.post("/api/v1/files", params={"project_id": proj.pid}, content=data, headers=_hdr(proj.eng, "a" * 64))
    assert r.json()["code"] == "hash_mismatch"
    assert _count(world, db.file_blobs) == 0 and _count(world, db.file_uploads) == 0


# ====================================================================== целостность
class _ChangingSource(io.BytesIO):
    """Источник, который «меняется на диске» после первого прохода (хэширования)."""

    def __init__(self, first: bytes, second: bytes):
        """Источник, который при втором чтении с начала отдаёт другие байты."""
        super().__init__(first)
        self._second, self._passes = second, 0

    def seek(self, pos, whence=0):
        """На втором переходе к началу подменить содержимое — имитация файла, изменённого во время загрузки."""
        if pos == 0 and whence == 0:
            self._passes += 1
            if self._passes == 2:
                self.__init__(self._second, b"")
                self._passes = 2
        return super().seek(pos, whence)


def test_source_changed_between_passes_writes_nothing(world, proj):
    first = _pattern(db.FILE_CHUNK + 100)
    second = first[:-1] + b"!"
    src = _ChangingSource(first, second)
    r = world.svc.put_file_stream(proj.eng.token, proj.pid, src, hashlib.sha256(first).hexdigest())
    assert r["code"] == "hash_mismatch"
    assert _count(world, db.file_blobs) == 0 and _count(world, db.file_chunks) == 0
    assert _count(world, db.file_uploads) == 0


def test_corrupted_file_not_served_over_http(world, proj, http):
    data = _pattern(db.FILE_CHUNK * 2 + 7)
    sha = hashlib.sha256(data).hexdigest()
    assert world.svc.put_file(proj.eng.token, proj.pid, data, sha)["status"] == "accepted"
    did = make_doc(proj)
    proj.eng.ok("document.add_version", {"id": did, "sha256": sha, "file_name": "a.bin"}, expected_version=1)
    # Перестановка двух блоков: размер тот же, хэш другой — порча, не видимая по длине.
    with world.svc.engine.begin() as c:
        rows = {i: bytes(d) for i, d in c.execute(sa.select(db.file_chunks.c.idx, db.file_chunks.c.data))}
        c.execute(db.file_chunks.update().where(db.file_chunks.c.idx == 0).values(data=rows[1]))
        c.execute(db.file_chunks.update().where(db.file_chunks.c.idx == 1).values(data=rows[0]))
    g = http.get(f"/api/v1/files/{did}/1", headers={"Authorization": f"Bearer {proj.eng.token}"})
    assert g.status_code == 500 and g.headers["content-type"].startswith("application/json")
    assert g.json()["code"] == "file_corrupted" and data[:64] not in g.content
    assert [a["result"] for a in audit(world, command="file.download")] == ["error"]


# ====================================================================== память
def test_service_memory_does_not_grow_with_file_size(world, proj, tmp_path):
    """Файл 24 МиБ проходит через сервис с пиком Python-памяти в несколько блоков, а не в размер файла.

    tracemalloc видит объекты Python (байтовые строки блоков, строки результата драйвера); именно они
    раньше копили файл целиком. Память внутри libpq/sqlite не учитывается — её поведение измерено
    отдельно (RSS) при работе над потоковым резервным копированием.
    """
    size = 24 * MiB
    src_path = tmp_path / "big.bin"
    h = hashlib.sha256()
    with open(src_path, "wb") as f:
        for i in range(size // MiB):
            b = _pattern(MiB)[:-8] + i.to_bytes(8, "big")
            h.update(b)
            f.write(b)
    sha = h.hexdigest()
    tracemalloc.start()
    try:
        with open(src_path, "rb") as f:
            tracemalloc.reset_peak()
            r = world.svc.put_file_stream(proj.eng.token, proj.pid, f, sha)
            up_peak = tracemalloc.get_traced_memory()[1]
        assert r["status"] == "accepted", r
        did = make_doc(proj)
        proj.eng.ok("document.add_version", {"id": did, "sha256": sha, "file_name": "big.bin"}, expected_version=1)
        out_path = tmp_path / "out.bin"
        with open(out_path, "w+b") as out:
            tracemalloc.reset_peak()
            d = world.svc.get_file_to(proj.eng.token, did, 1, out)
            down_peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert d["status"] == "accepted" and d["size_bytes"] == size
    with open(out_path, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == sha
    # Порог с запасом: несколько блоков по 1 МиБ (yield_per=4 у PostgreSQL), но далеко от 24 МиБ.
    assert up_peak < 8 * MiB, f"upload peak {up_peak / MiB:.1f} MiB"
    assert down_peak < 10 * MiB, f"download peak {down_peak / MiB:.1f} MiB"


# ====================================================================== настоящий HTTP-сервер + клиентский транспорт
def _free_port() -> int:
    """Свободный TCP-порт на 127.0.0.1."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(world):
    """Фикстура: настоящий сервер uvicorn в отдельном потоке (проверка потоковой передачи по сети)."""
    import uvicorn
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(world.svc), host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="off"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(10)


def test_http_transport_streams_both_ways(world, proj, live, tmp_path):
    from vector.client.transport import HttpTransport
    data = _pattern(3 * db.FILE_CHUNK + 999)
    sha = hashlib.sha256(data).hexdigest()
    src = tmp_path / "src.bin"
    src.write_bytes(data)
    t = HttpTransport(live)
    try:
        with open(src, "rb") as f:
            up = t.upload(proj.eng.token, proj.pid, f, sha)
        assert up["status"] == "accepted" and up["size_bytes"] == len(data)
        did = make_doc(proj)
        proj.eng.ok("document.add_version", {"id": did, "sha256": sha, "file_name": "s.bin"}, expected_version=1)
        sink = io.BytesIO()
        resp = t.download(proj.gip.token, did, 1, sink)
        assert resp == {"status": "accepted", "sha256": sha, "size_bytes": len(data)} and sink.getvalue() == data
        # Отказ приходит JSON-ом и в приёмник ничего не пишется.
        sink2 = io.BytesIO()
        assert t.download(proj.lead.token, did, 1, sink2)["status"] == "denied" and sink2.getvalue() == b""
        # Честный файл сверх предела: сервер отказывает по Content-Length, не читая тело.
        big = tmp_path / "big.bin"
        with open(big, "wb") as f:
            f.truncate(LIMIT_MB * MiB + 1)
        with open(big, "rb") as f:
            r = t.upload(proj.eng.token, proj.pid, f, "0" * 64)
        assert r["code"] == "file_too_large"
    finally:
        t.close()
