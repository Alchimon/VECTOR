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
import tempfile
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .db import FILE_CHUNK
from .service import Service

# Файлы до этого размера временно держатся в памяти, крупнее — во временном файле на диске.
FILE_SPOOL_IN_MEMORY = 1024 * 1024
# Порция записи входящего потока во временный файл (меньше вызовов пула потоков).
FILE_SPOOL_WRITE = 1024 * 1024

# Предел тела JSON-запроса (1 МБ). Файлы идут отдельным маршрутом /api/v1/files со своим пределом (max_file_mb).
MAX_BODY = 1 << 20
log = logging.getLogger("vector.api")

# Статус ответа сервиса → код HTTP. Клиент опирается на поле status в теле; код HTTP нужен прокси и мониторингу.
_STATUS_HTTP = {"accepted": 200, "rejected": 422, "denied": 403, "conflict": 409, "unauthenticated": 401, "error": 500}


def create_app(service: Service) -> Starlette:
    """Собрать HTTP-приложение (Starlette) поверх сервиса бизнес-правил.

    Все маршруты — тонкая обёртка: прочитать запрос (с ограничением размера), сложить «конверт» {type, token, …} и
    передать его в Service.handle в пуле потоков. Решения о правах, проверках и записи принимает только сервис;
    HTTP-слой лишь переводит статус ответа в код HTTP (_STATUS_HTTP).

    Маршруты: /api/v1/auth/login|logout, /api/v1/command (изменения), /api/v1/query (чтение, в т. ч. pull),
    /api/v1/files (файлы версий документов), /api/v1/health (жив ли сервер), /api/v1/about (версия, схема).
    """
    async def body_json(request: Request) -> dict | None:
        """Прочитать тело запроса как JSON-объект не больше MAX_BODY (1 МБ).

        Заявленный размер проверяется до чтения, фактический — по мере чтения (клиент может соврать в Content-
        Length). Любая ошибка — None, вызывающий отвечает «некорректный запрос» без подробностей.
        """
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
        """Токен сеанса из заголовка Authorization: Bearer … (в URL токен не принимается — он попал бы в журналы
        прокси).
        """
        h = request.headers.get("authorization", "")
        return h[7:].strip() if h.lower().startswith("bearer ") else None

    def reply(resp: dict) -> Response:
        """Ответ JSON с кодом HTTP по статусу (accepted → 200, conflict → 409 …) и запретом кэширования (данные
        персональные).
        """
        return JSONResponse(resp, status_code=_STATUS_HTTP.get(resp.get("status"), 200),
                            headers={"Cache-Control": "no-store"})

    def bad() -> Response:
        """Стандартный ответ на нечитаемый/слишком большой запрос — без подробностей разбора."""
        return reply({"status": "rejected", "code": "bad_request", "message_ru": "Некорректный запрос (формат или размер)."})

    def addr(request: Request) -> str | None:
        """IP-адрес клиента — для ограничения частоты входа и журнала аудита."""
        return request.client.host if request.client else None

    async def run(request: Request, envelope: dict) -> Response:
        """Выполнить конверт в сервисе и вернуть HTTP-ответ."""
        # Сервис синхронный (транзакции БД) — выполняем в пуле потоков, не блокируя цикл событий.
        from starlette.concurrency import run_in_threadpool
        return reply(await run_in_threadpool(service.handle, envelope, addr(request)))

    async def login(request: Request):
        """POST /api/v1/auth/login {login, password, device_id} → токен сеанса или отказ."""
        b = await body_json(request)
        if b is None:
            return bad()
        return await run(request, {"type": "login", "login": b.get("login"), "password": b.get("password"), "device_id": b.get("device_id")})

    async def logout(request: Request):
        """POST /api/v1/auth/logout — завершить текущий сеанс (токен становится недействительным)."""
        return await run(request, {"type": "logout", "token": bearer(request)})

    async def command(request: Request):
        """POST /api/v1/command {operation_id, command, payload, expected_version} — выполнить команду изменения.

        operation_id создаёт клиент один раз на операцию: повтор с тем же id (после обрыва связи) вернёт сохранённый
        ответ, а не выполнит действие второй раз.
        """
        b = await body_json(request)
        if b is None:
            return bad()
        return await run(request, {"type": "command", "token": bearer(request), "operation_id": b.get("operation_id"),
                                   "command": b.get("command"), "payload": b.get("payload"), "expected_version": b.get("expected_version")})

    async def query(request: Request):
        """POST /api/v1/query {query, …} — запросы чтения (pull, история объекта, версии документов…)."""
        b = await body_json(request)
        if b is None:
            return bad()
        return await run(request, {"type": "query", "token": bearer(request), **{k: v for k, v in b.items() if k != "token"}})

    async def file_upload(request: Request):
        """POST /api/v1/files?project_id=<uuid>  +  X-Content-SHA256: <hex>  +  тело — байты файла.

        Поток, а не буфер (issue #8): тело пишется во временный файл блоками, память сервера не зависит
        от размера файла. Порядок защиты:
          1) заявленный Content-Length больше предела → отказ сразу, тело не читается;
          2) вход и право в проекте проверяются ДО чтения тела — посторонний не может занять диск;
          3) при чтении считается фактический размер: превышение → чтение прекращается, временный файл удаляется;
          4) окончательные проверки (хэш, право, квитанция, аудит) — в одной транзакции в put_file_stream.
        Временный файл — в системном каталоге временных файлов (меняется переменной окружения TMPDIR / TEMP);
        он анонимный (на Linux удаляется из каталога сразу при создании) и закрывается в finally.
        """
        from starlette.concurrency import run_in_threadpool
        limit = service.sec.max_file_mb * 1024 * 1024
        cl = request.headers.get("content-length")
        if cl is not None and not cl.isdigit():
            return bad()
        token, project_id, sha = bearer(request), request.query_params.get("project_id"), request.headers.get("x-content-sha256")
        pre = await run_in_threadpool(service.precheck_upload, token, project_id, int(cl) if cl is not None else None,
                                      addr(request))
        if pre is not None:
            return reply(pre)
        spool = tempfile.SpooledTemporaryFile(max_size=FILE_SPOOL_IN_MEMORY)
        try:
            size, pending = 0, bytearray()
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    return reply({"status": "rejected", "code": "file_too_large",
                                  "message_ru": f"Файл больше допустимого размера ({service.sec.max_file_mb} МБ)."})
                pending += chunk
                if len(pending) >= FILE_SPOOL_WRITE:
                    # Запись на диск — блокирующая операция; уводим её из цикла событий крупными порциями.
                    await run_in_threadpool(spool.write, bytes(pending))
                    pending.clear()
            if pending:
                await run_in_threadpool(spool.write, bytes(pending))
            return reply(await run_in_threadpool(service.put_file_stream, token, project_id, spool, sha, addr(request)))
        finally:
            spool.close()

    async def file_download(request: Request):
        """GET /api/v1/files/{document_id}/{version_no} → байты; имя файла в Content-Disposition (RFC 6266/5987).

        Fail-closed и без файла целиком в памяти (issue #8): сервис собирает файл из блоков БД во временный
        файл, проверяя SHA-256 и размер; только после успешной проверки начинается отдача клиенту
        (StreamingResponse, блоками). Повреждённый файл не отдаётся ни байтом — клиент получает JSON-ошибку.
        """
        from starlette.concurrency import run_in_threadpool
        try:
            version_no = int(request.path_params["version_no"])
        except ValueError:
            return bad()
        spool = tempfile.SpooledTemporaryFile(max_size=FILE_SPOOL_IN_MEMORY)
        try:
            resp = await run_in_threadpool(service.get_file_to, bearer(request), request.path_params["document_id"],
                                           version_no, spool, addr(request))
        except BaseException:
            spool.close()
            raise
        if resp.get("status") != "accepted":
            spool.close()
            return reply(resp)
        spool.seek(0)

        def blocks():
            """Отдавать проверенный временный файл клиенту блоками по FILE_CHUNK."""
            # Синхронный генератор: Starlette выполняет его в пуле потоков. finally закрывает временный файл
            # и при обрыве связи клиентом (генератор закрывается сборщиком мусора).
            try:
                while True:
                    b = spool.read(FILE_CHUNK)
                    if not b:
                        break
                    yield b
            finally:
                spool.close()

        return StreamingResponse(blocks(), media_type="application/octet-stream", headers={
            "Cache-Control": "no-store",
            "Content-Length": str(resp["size_bytes"]),
            "X-Content-SHA256": resp["sha256"],
            # filename* — UTF-8 (кириллица в именах файлов); filename — запасной ASCII-вариант.
            "Content-Disposition": f"attachment; filename=\"document.bin\"; filename*=UTF-8''{quote(resp['file_name'])}",
        })

    async def transmittal_package_download(request: Request):
        """GET /api/v1/transmittals/{transmittal_id}/package → ZIP пакета выдачи (D-63).

        Тот же контур, что и у файлов документов: сервис собирает пакет из блоков БД во временный файл,
        пересчитывает SHA-256 и размер и только потом отдаёт; повреждённый пакет не отдаётся ни байтом.
        """
        from starlette.concurrency import run_in_threadpool
        spool = tempfile.SpooledTemporaryFile(max_size=FILE_SPOOL_IN_MEMORY)
        try:
            resp = await run_in_threadpool(service.get_transmittal_package_to, bearer(request),
                                           request.path_params["transmittal_id"], spool, addr(request))
        except BaseException:
            spool.close()
            raise
        if resp.get("status") != "accepted":
            spool.close()
            return reply(resp)
        spool.seek(0)

        def blocks():
            """Отдавать проверенный временный файл клиенту блоками по FILE_CHUNK."""
            try:
                while True:
                    b = spool.read(FILE_CHUNK)
                    if not b:
                        break
                    yield b
            finally:
                spool.close()

        return StreamingResponse(blocks(), media_type="application/zip", headers={
            "Cache-Control": "no-store",
            "Content-Length": str(resp["size_bytes"]),
            "X-Content-SHA256": resp["sha256"],
            "Content-Disposition": f"attachment; filename=\"package.zip\"; filename*=UTF-8''{quote(resp['file_name'])}",
        })

    async def health(request: Request):
        """GET /api/v1/health — проверка «сервер отвечает» для мониторинга и скриптов стенда (без входа, без данных).
        """
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    async def about(request: Request):
        """GET /api/v1/about — версия программы и схемы БД (без секретов) для окна «О программе» и диагностики."""
        return JSONResponse(service.about())

    return Starlette(routes=[
        Route("/api/v1/auth/login", login, methods=["POST"]),
        Route("/api/v1/auth/logout", logout, methods=["POST"]),
        Route("/api/v1/command", command, methods=["POST"]),
        Route("/api/v1/query", query, methods=["POST"]),
        Route("/api/v1/files", file_upload, methods=["POST"]),
        Route("/api/v1/files/{document_id}/{version_no}", file_download, methods=["GET"]),
        Route("/api/v1/transmittals/{transmittal_id}/package", transmittal_package_download, methods=["GET"]),
        Route("/api/v1/health", health, methods=["GET"]),
        Route("/api/v1/about", about, methods=["GET"]),
    ])
