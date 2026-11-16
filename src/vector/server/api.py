"""HTTP API сервера (транспорт и граница бизнес-правил; не пользовательский веб-интерфейс).

* Токен — только в заголовке Authorization: Bearer (не в URL, не в журналах доступа).
* Ограничение размера тела запроса; ответы — JSON без стеков и внутренних деталей.
* Деньги на входе — десятичные строки; число с плавающей точкой отклоняется доменом (float_forbidden).
* Файлы версий документов — отдельные маршруты /api/v1/files: тело — сырые байты (не JSON), предел размера
  из политики сервера, контрольная сумма SHA-256 в заголовке X-Content-SHA256 в обе стороны.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .service import Service

MAX_BODY = 1 << 20
log = logging.getLogger("vector.api")

_STATUS_HTTP = {"accepted": 200, "rejected": 422, "denied": 403, "conflict": 409, "unauthenticated": 401, "error": 500}


def create_app(service: Service) -> Starlette:
    async def body_json(request: Request) -> dict | None:
        cl = request.headers.get("content-length")
        if cl is not None and (not cl.isdigit() or int(cl) > MAX_BODY):
            return None
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > MAX_BODY:
                return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def bearer(request: Request) -> str | None:
        h = request.headers.get("authorization", "")
        return h[7:].strip() if h.lower().startswith("bearer ") else None

    def reply(resp: dict) -> Response:
        return JSONResponse(resp, status_code=_STATUS_HTTP.get(resp.get("status"), 200),
                            headers={"Cache-Control": "no-store"})

    def bad() -> Response:
        return reply({"status": "rejected", "code": "bad_request", "message_ru": "Некорректный запрос (формат или размер)."})

    def addr(request: Request) -> str | None:
        return request.client.host if request.client else None

    async def run(request: Request, envelope: dict) -> Response:
        # Сервис синхронный (транзакции БД) — выполняем в пуле потоков, не блокируя цикл событий.
        from starlette.concurrency import run_in_threadpool
        return reply(await run_in_threadpool(service.handle, envelope, addr(request)))

    async def login(request: Request):
        b = await body_json(request)
        if b is None:
            return bad()
        return await run(request, {"type": "login", "login": b.get("login"), "password": b.get("password"), "device_id": b.get("device_id")})

    async def logout(request: Request):
        return await run(request, {"type": "logout", "token": bearer(request)})

    async def command(request: Request):
        b = await body_json(request)
        if b is None:
            return bad()
        return await run(request, {"type": "command", "token": bearer(request), "operation_id": b.get("operation_id"),
                                   "command": b.get("command"), "payload": b.get("payload"), "expected_version": b.get("expected_version")})

    async def query(request: Request):
        b = await body_json(request)
        if b is None:
            return bad()
        return await run(request, {"type": "query", "token": bearer(request), **{k: v for k, v in b.items() if k != "token"}})

    async def file_upload(request: Request):
        """POST /api/v1/files?project_id=<uuid>  +  X-Content-SHA256: <hex>  +  тело — байты файла.

        Тело читается потоком с контролем размера: при превышении предела чтение прекращается сразу,
        а не после загрузки гигабайтов в память.
        """
        from starlette.concurrency import run_in_threadpool
        limit = service.sec.max_file_mb * 1024 * 1024
        cl = request.headers.get("content-length")
        if cl is not None and (not cl.isdigit() or int(cl) > limit):
            return reply({"status": "rejected", "code": "file_too_large",
                          "message_ru": f"Файл больше допустимого размера ({service.sec.max_file_mb} МБ)."})
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > limit:
                return reply({"status": "rejected", "code": "file_too_large",
                              "message_ru": f"Файл больше допустимого размера ({service.sec.max_file_mb} МБ)."})
        return reply(await run_in_threadpool(service.put_file, bearer(request), request.query_params.get("project_id"),
                                             bytes(buf), request.headers.get("x-content-sha256"), addr(request)))

    async def file_download(request: Request):
        """GET /api/v1/files/{document_id}/{version_no} → байты; имя файла в Content-Disposition (RFC 6266/5987)."""
        from starlette.concurrency import run_in_threadpool
        try:
            version_no = int(request.path_params["version_no"])
        except ValueError:
            return bad()
        resp, content = await run_in_threadpool(service.get_file, bearer(request), request.path_params["document_id"],
                                                version_no, addr(request))
        if content is None:
            return reply(resp)
        return Response(content, media_type="application/octet-stream", headers={
            "Cache-Control": "no-store",
            "X-Content-SHA256": resp["sha256"],
            # filename* — UTF-8 (кириллица в именах файлов); filename — запасной ASCII-вариант.
            "Content-Disposition": f"attachment; filename=\"document.bin\"; filename*=UTF-8''{quote(resp['file_name'])}",
        })

    async def health(request: Request):
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    async def about(request: Request):
        return JSONResponse(service.about())

    return Starlette(routes=[
        Route("/api/v1/auth/login", login, methods=["POST"]),
        Route("/api/v1/auth/logout", logout, methods=["POST"]),
        Route("/api/v1/command", command, methods=["POST"]),
        Route("/api/v1/query", query, methods=["POST"]),
        Route("/api/v1/files", file_upload, methods=["POST"]),
        Route("/api/v1/files/{document_id}/{version_no}", file_download, methods=["GET"]),
        Route("/api/v1/health", health, methods=["GET"]),
        Route("/api/v1/about", about, methods=["GET"]),
    ])
