"""HTTP-граница: коды ответов, размер тела, токен только в заголовке, без утечки деталей."""
import pytest
from starlette.testclient import TestClient

from conftest import PWD, new_id
from vector.server.api import MAX_BODY, create_app


@pytest.fixture
def http(world):
    return TestClient(create_app(world.svc))


def _login(http, login="rp1"):
    r = http.post("/api/v1/auth/login", json={"login": login, "password": PWD, "device_id": "d1"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_health_and_about(http):
    assert http.get("/api/v1/health").json() == {"status": "ok"}
    a = http.get("/api/v1/about").json()
    assert a["product"] == "ВЕКТОР" and a["api"] == "v1"
    # Заголовок Server отключается на уровне uvicorn (server_header=False) — проверяется в test_cli_ops через настоящий TLS-сервер.
    assert http.get("/api/v1/health").headers["cache-control"] == "no-store"


def test_status_codes(http, world):
    assert http.post("/api/v1/auth/login", json={"login": "rp1", "password": "nope-nope-nope", "device_id": "d"}).status_code == 401
    h = _login(http)
    cid = new_id()
    r = http.post("/api/v1/command", headers=h, json={"operation_id": new_id(), "command": "customer.create", "payload": {"id": cid, "name": "ООО"}})
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert r.headers["cache-control"] == "no-store"
    r = http.post("/api/v1/command", headers=h, json={"operation_id": new_id(), "command": "customer.update",
                                                     "payload": {"id": cid, "name": "X"}, "expected_version": 7})
    assert r.status_code == 409 and r.json()["details"]["current"]["name"] == "ООО"
    eh = _login(http, "eng1")
    r = http.post("/api/v1/command", headers=eh, json={"operation_id": new_id(), "command": "customer.create", "payload": {"id": new_id(), "name": "X"}})
    assert r.status_code == 403
    r = http.post("/api/v1/command", json={"operation_id": new_id(), "command": "customer.create", "payload": {}})
    assert r.status_code == 401
    r = http.post("/api/v1/query", headers=h, json={"query": "pull"})
    assert r.status_code == 200 and r.json()["result"]["customers"][0]["name"] == "ООО"


def test_token_in_body_is_ignored(http):
    h = _login(http)
    tok = h["Authorization"][7:]
    r = http.post("/api/v1/query", json={"query": "pull", "token": tok})
    assert r.status_code == 401


def test_bad_json_and_oversized_body(http):
    h = _login(http)
    assert http.post("/api/v1/command", headers={**h, "content-type": "application/json"}, content=b"{not json").status_code == 422
    assert http.post("/api/v1/command", headers=h, json=[1, 2]).status_code == 422
    big = b'{"x":"' + b"a" * (MAX_BODY + 10) + b'"}'
    assert http.post("/api/v1/command", headers={**h, "content-type": "application/json"}, content=big).status_code == 422


def test_float_money_rejected_over_http(http, world):
    h = _login(http)
    _, tender, _ = world.won_tender(world.login("rp1"))
    r = http.post("/api/v1/command", headers=h, content=('{"operation_id":"%s","command":"tkp.create","payload":{"id":"%s","tender_id":"%s",'
                                                         '"label":"x","amount":1.5,"currency":"RUB"}}' % (new_id(), new_id(), tender)).encode(),
                  )
    assert r.json()["code"] == "amount_float_forbidden"


def test_internal_error_hides_details(http, world):
    h = _login(http)
    world.svc._fault_after_effect = "customer.create"
    r = http.post("/api/v1/command", headers=h, json={"operation_id": new_id(), "command": "customer.create", "payload": {"id": new_id(), "name": "X"}})
    assert r.status_code == 500
    assert "Traceback" not in r.text and "injected" not in r.text


def test_file_upload_and_download_over_http(http, world):
    """Файлы по HTTP: сырые байты, SHA-256 в заголовке в обе стороны, кириллица в имени, отказ при порче в пути."""
    import hashlib
    from urllib.parse import quote
    rp = world.login("rp1")
    _, tender, _ = world.won_tender(rp)
    pid, kit, did = new_id(), new_id(), new_id()
    rp.ok("project.start_rkd", {"id": pid, "tender_id": tender, "code": "П-1", "title": "П"})
    world.admin.ok("project.assign", {"project_id": pid, "user_id": world.users["rp1"], "project_role": "rp", "reason": "Приказ"})
    rp.ok("kit.create", {"id": kit, "project_id": pid, "code": "К", "title": "К"})
    rp.ok("document.create", {"id": did, "kit_id": kit, "code": "К-1", "title": "Документ"})
    h = _login(http)
    data = "Чертёж\n".encode() * 1000
    sha = hashlib.sha256(data).hexdigest()
    bad = http.post(f"/api/v1/files?project_id={pid}", content=data, headers={**h, "X-Content-SHA256": "0" * 64})
    assert bad.status_code == 422 and bad.json()["code"] == "hash_mismatch"
    up = http.post(f"/api/v1/files?project_id={pid}", content=data, headers={**h, "X-Content-SHA256": sha})
    assert up.status_code == 200 and up.json()["sha256"] == sha
    rp.ok("document.add_version", {"id": did, "sha256": sha, "file_name": "Схема К-1.dwg"}, expected_version=1)
    r = http.get(f"/api/v1/files/{did}/1", headers=h)
    assert r.status_code == 200 and r.content == data
    assert r.headers["x-content-sha256"] == sha and r.headers["cache-control"] == "no-store"
    assert quote("Схема К-1.dwg") in r.headers["content-disposition"]
    assert http.get(f"/api/v1/files/{did}/1").status_code == 401           # без токена
    assert http.get(f"/api/v1/files/{did}/9", headers=h).status_code == 422
    assert http.get(f"/api/v1/files/{did}/abc", headers=h).status_code == 422
