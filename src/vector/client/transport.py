"""Транспорт клиент↔сервер.

Разделение ошибок принципиально для протокола синхронизации:
* Offline      — запрос точно НЕ дошёл (нет соединения, отказ TCP/TLS до отправки);
* ResponseLost — запрос мог дойти и выполниться, ответ потерян: исход неизвестен,
                 повтор допускается только с тем же operation_id (сервер вернёт сохранённый исход).
Ошибка проверки TLS-сертификата — НЕ «офлайн»: это возможная атака/неверная настройка, сообщаем явно.
"""
from __future__ import annotations

import io
import json
import os
import ssl
from typing import Optional

import httpx


class Offline(Exception):
    """Нет соединения: запрос не дошёл до сервера."""


class ResponseLost(Exception):
    """Запрос дошёл и мог быть выполнен, но ответ потерян (исход неизвестен клиенту)."""


class TlsError(Exception):
    """Сертификат сервера не прошёл проверку."""


# Блок потоковой передачи файлов. В памяти клиента одновременно — порядка одного блока.
FILE_BLOCK = 1024 * 1024


def _as_reader(source):
    """bytes → BytesIO; файловый объект возвращается как есть."""
    return io.BytesIO(bytes(source)) if isinstance(source, (bytes, bytearray, memoryview)) else source


def _remaining(f) -> int:
    """Сколько байтов осталось прочитать из файлового объекта (позиция не меняется)."""
    try:
        return os.fstat(f.fileno()).st_size - f.tell()
    except (AttributeError, OSError, io.UnsupportedOperation):
        pos = f.tell()
        end = f.seek(0, io.SEEK_END)
        f.seek(pos)
        return end - pos


def _seekable(f):
    """Сервис читает источник дважды (хэш, затем запись): ему нужен файл с seek от начала."""
    if f.tell() == 0:
        return f
    return io.BytesIO(f.read())


# Тип запроса протокола → путь HTTP API сервера (api.py).
_ROUTES = {"login": "/api/v1/auth/login", "logout": "/api/v1/auth/logout", "command": "/api/v1/command", "query": "/api/v1/query"}


class HttpTransport:
    """Транспорт по HTTPS к серверу ВЕКТОР (httpx). Только https:// (http — лишь для localhost при разработке); свой
    корневой сертификат — ca_file; редиректы запрещены (не уводим токен на чужой адрес). Флаг online отражает
    последний исход связи — по нему интерфейс показывает «Офлайн».
    """
    def __init__(self, base_url: str, ca_file: Optional[str] = None, timeout: float = 15.0):
        """Проверить адрес сервера и создать HTTP-клиент: таймаут подключения 5 с, остальное — timeout (15 с по
        умолчанию).
        """
        if not base_url.startswith("https://") and not base_url.startswith("http://127.0.0.1") and not base_url.startswith("http://localhost"):
            raise ValueError("Адрес сервера должен начинаться с https:// (открытый HTTP допустим только для localhost в режиме разработки).")
        self.base_url = base_url.rstrip("/")
        verify: ssl.SSLContext | bool = ssl.create_default_context(cafile=ca_file) if ca_file else True
        self._http = httpx.Client(base_url=self.base_url, verify=verify, timeout=httpx.Timeout(timeout, connect=5.0),
                                  headers={"User-Agent": "VECTOR-desktop"}, follow_redirects=False)
        self.online = True

    def close(self):
        """Закрыть HTTP-клиент и его соединения."""
        self._http.close()

    def send(self, request: dict) -> dict:
        """Отправить запрос протокола (login / logout / command / query) как JSON и вернуть ответ сервера.

        Токен уходит в заголовке Authorization, а не в теле. Сбои классифицируются: нет связи до отправки → Offline
        (можно ставить в очередь); запрос ушёл, но ответа нет или он не JSON → ResponseLost (исход неизвестен,
        повтор только с тем же operation_id); ошибка сертификата → TlsError (это не «офлайн», а возможная атака).
        """
        t = request.get("type")
        body = {k: v for k, v in request.items() if k not in ("type", "token")}
        headers = {"Authorization": f"Bearer {request['token']}"} if request.get("token") else {}
        try:
            r = self._http.post(_ROUTES[t], content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                headers={**headers, "Content-Type": "application/json"})
        except httpx.ConnectError as e:
            if _is_cert_error(e):
                self.online = False
                raise TlsError("Сертификат сервера не прошёл проверку. Соединение прервано ради безопасности — обратитесь к администратору.") from e
            self.online = False
            raise Offline() from e
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            self.online = False
            raise Offline() from e
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError) as e:
            self.online = False
            raise ResponseLost() from e
        self.online = True
        try:
            data = r.json()
        except ValueError:
            raise ResponseLost()  # промежуточный узел вернул не-JSON: исход неизвестен
        if not isinstance(data, dict):
            raise ResponseLost()
        return data

    # ------------------------------------------------------------ файлы версий документов
    # Файлы идут отдельными маршрутами (сырые байты, не JSON). Контрольная сумма SHA-256 передаётся
    # в заголовке X-Content-SHA256 в обе стороны: сервер проверяет её при загрузке, клиент — при скачивании.
    # Таймаут чтения/записи увеличен: большой чертёж по медленному каналу не должен обрываться через 15 с.
    _FILE_TIMEOUT = httpx.Timeout(600.0, connect=5.0)

    def _guard(self, fn):
        """Те же правила классификации сбоев сети, что и в send(): нет связи / ответ потерян / сертификат."""
        try:
            r = fn()
        except httpx.ConnectError as e:
            self.online = False
            if _is_cert_error(e):
                raise TlsError("Сертификат сервера не прошёл проверку. Соединение прервано ради безопасности — обратитесь к администратору.") from e
            raise Offline() from e
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            self.online = False
            raise Offline() from e
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError) as e:
            self.online = False
            raise ResponseLost() from e
        self.online = True
        return r

    def upload(self, token: str, project_id: str, source, sha256: str, size: Optional[int] = None) -> dict:
        """Отправить содержимое файла потоком (issue #8).

        source — двоичный файловый объект (открытый файл, BytesIO) или bytes. Читается блоками по FILE_BLOCK,
        в памяти держится один блок. Content-Length передаём явно: сервер отказывает в слишком большом файле
        ещё до приёма тела.
        """
        src = _as_reader(source)
        if size is None:
            size = _remaining(src)

        def body():
            """Генератор тела запроса: файл блоками по FILE_BLOCK."""
            while True:
                b = src.read(FILE_BLOCK)
                if not b:
                    return
                yield b
        r = self._guard(lambda: self._http.post("/api/v1/files", params={"project_id": project_id}, content=body(),
                                                headers={"Authorization": f"Bearer {token}", "X-Content-SHA256": sha256,
                                                         "Content-Type": "application/octet-stream",
                                                         "Content-Length": str(size)},
                                                timeout=self._FILE_TIMEOUT))
        try:
            out = r.json()
        except ValueError:
            raise ResponseLost()
        return out if isinstance(out, dict) else {"status": "error", "message_ru": "Некорректный ответ сервера."}

    def download(self, token: str, document_id: str, version_no: int, sink) -> dict:
        """Скачать файл версии потоком в sink (двоичный файловый объект для записи). Возвращает ответ сервера.

        При успехе ответ {"status": "accepted", "sha256": <заголовок сервера>, "size_bytes": n}; байты уже в sink.
        Иначе — JSON-ответ сервера (отказ/ошибка), в sink ничего не пишется. Проверка SHA-256 — забота
        вызывающего (SyncClient): транспорт только доставляет байты. Обрыв посреди передачи → ResponseLost.
        """
        def fetch():
            """Потоковое скачивание: при 200 и application/octet-stream — байты в sink, иначе разбор JSON-ответа об
            ошибке.
            """
            with self._http.stream("GET", f"/api/v1/files/{document_id}/{int(version_no)}",
                                   headers={"Authorization": f"Bearer {token}"}, timeout=self._FILE_TIMEOUT) as r:
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/octet-stream"):
                    n = 0
                    for b in r.iter_bytes(FILE_BLOCK):
                        sink.write(b)
                        n += len(b)
                    return {"status": "accepted", "sha256": r.headers.get("x-content-sha256", ""), "size_bytes": n}
                r.read()   # ответ-ошибка небольшой (JSON); дочитываем, чтобы соединение вернулось в пул
                try:
                    out = r.json()
                except ValueError:
                    raise ResponseLost()
                return out if isinstance(out, dict) else {"status": "error", "message_ru": "Некорректный ответ сервера."}
        return self._guard(fetch)

    def ping(self) -> bool:
        """Проверка доступности сервера (/api/v1/health, 3 с); обновляет флаг online."""
        try:
            ok = self._http.get("/api/v1/health", timeout=3.0).status_code == 200
        except httpx.HTTPError:
            ok = False
        self.online = ok
        return ok


def _is_cert_error(e: Exception) -> bool:
    """Причина ошибки подключения — непрошедшая проверка TLS-сертификата? Просматривается вся цепочка исключений."""
    c: BaseException | None = e
    while c is not None:
        if isinstance(c, ssl.SSLCertVerificationError):
            return True
        c = c.__cause__ or c.__context__
    return "CERTIFICATE_VERIFY_FAILED" in str(e)


class LoopbackTransport:
    """Для автотестов: in-process сервис с JSON-границей и инъекцией сбоев."""

    def __init__(self, service, addr: str = "127.0.0.1"):
        """Подключить сервис напрямую. Флаги для тестов: online=False — имитация отсутствия сети; drop_next_response —
        запрос выполнен, ответ «потерян»; corrupt_next_download — порча байта при скачивании.
        """
        self._svc, self._addr = service, addr
        self.online = True
        self.drop_next_response = False
        self.corrupt_next_download = False
        self.sent: list[dict] = []

    def send(self, request: dict) -> dict:
        """Передать запрос в сервис через JSON-сериализацию (как по сети: никаких Python-объектов через границу) с
        учётом флагов сбоев.
        """
        if not self.online:
            raise Offline()
        raw = json.loads(json.dumps(request, ensure_ascii=False))
        self.sent.append(raw)
        resp = json.loads(json.dumps(self._svc.handle(raw, self._addr), ensure_ascii=False))
        if self.drop_next_response:
            self.drop_next_response = False
            raise ResponseLost()
        return resp

    def upload(self, token: str, project_id: str, source, sha256: str, size: Optional[int] = None) -> dict:
        """Загрузить файл в сервис напрямую (с той же проверкой SHA-256 на стороне сервиса)."""
        if not self.online:
            raise Offline()
        resp = self._svc.put_file_stream(token, project_id, _seekable(_as_reader(source)), sha256, self._addr)
        if self.drop_next_response:
            self.drop_next_response = False
            raise ResponseLost()
        return json.loads(json.dumps(resp, ensure_ascii=False))

    def download(self, token: str, document_id: str, version_no: int, sink) -> dict:
        """Скачать файл из сервиса; при corrupt_next_download один байт портится — клиент обязан это обнаружить."""
        if not self.online:
            raise Offline()
        # Сервис собирает проверенный файл во временный буфер (как HTTP-слой), затем «передаёт» его в sink.
        buf = io.BytesIO()
        resp = self._svc.get_file_to(token, document_id, version_no, buf, self._addr)
        if resp.get("status") == "accepted":
            content = buf.getvalue()
            if self.corrupt_next_download:
                # Инъекция порчи «в пути» для тестов: клиент обязан заметить несовпадение SHA-256.
                self.corrupt_next_download = False
                content = content[:-1] + bytes([content[-1] ^ 0xFF]) if content else b"x"
            sink.write(content)
            resp = {"status": "accepted", "sha256": resp["sha256"], "size_bytes": len(content)}
        return json.loads(json.dumps(resp, ensure_ascii=False))

    def ping(self) -> bool:
        """«Сервер доступен», если не включена имитация офлайна."""
        return self.online

    def close(self):
        """Нечего закрывать."""
        pass
