"""HTTP API сервера (транспорт и граница бизнес-правил; не пользовательский веб-интерфейс).

* Токен — только в заголовке Authorization: Bearer (не в URL, не в журналах доступа).
* Ограничение размера тела запроса; ответы — JSON без стеков и внутренних деталей.
* Деньги на входе — десятичные строки; число с плавающей точкой отклоняется доменом (float_forbidden).
"""
from __future__ import annotations

import json
import logging

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

    async def health(request: Request):
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    async def about(request: Request):
        return JSONResponse(service.about())

    return Starlette(routes=[
        Route("/api/v1/auth/login", login, methods=["POST"]),
        Route("/api/v1/auth/logout", logout, methods=["POST"]),
        Route("/api/v1/command", command, methods=["POST"]),
        Route("/api/v1/query", query, methods=["POST"]),
        Route("/api/v1/health", health, methods=["GET"]),
        Route("/api/v1/about", about, methods=["GET"]),
    ])
