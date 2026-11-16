"""Транспорт клиент↔сервер.

Разделение ошибок принципиально для протокола синхронизации:
* Offline      — запрос точно НЕ дошёл (нет соединения, отказ TCP/TLS до отправки);
* ResponseLost — запрос мог дойти и выполниться, ответ потерян: исход неизвестен,
                 повтор допускается только с тем же operation_id (сервер вернёт сохранённый исход).
Ошибка проверки TLS-сертификата — НЕ «офлайн»: это возможная атака/неверная настройка, сообщаем явно.
"""
from __future__ import annotations

import json
import ssl
from typing import Optional

import httpx


class Offline(Exception):
    """Нет соединения: запрос не дошёл до сервера."""


class ResponseLost(Exception):
    """Запрос дошёл и мог быть выполнен, но ответ потерян (исход неизвестен клиенту)."""


class TlsError(Exception):
    """Сертификат сервера не прошёл проверку."""


_ROUTES = {"login": "/api/v1/auth/login", "logout": "/api/v1/auth/logout", "command": "/api/v1/command", "query": "/api/v1/query"}


class HttpTransport:
    def __init__(self, base_url: str, ca_file: Optional[str] = None, timeout: float = 15.0):
        if not base_url.startswith("https://") and not base_url.startswith("http://127.0.0.1") and not base_url.startswith("http://localhost"):
            raise ValueError("Адрес сервера должен начинаться с https:// (открытый HTTP допустим только для localhost в режиме разработки).")
        self.base_url = base_url.rstrip("/")
        verify: ssl.SSLContext | bool = ssl.create_default_context(cafile=ca_file) if ca_file else True
        self._http = httpx.Client(base_url=self.base_url, verify=verify, timeout=httpx.Timeout(timeout, connect=5.0),
                                  headers={"User-Agent": "VECTOR-desktop"}, follow_redirects=False)
        self.online = True

    def close(self):
        self._http.close()

    def send(self, request: dict) -> dict:
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

    def ping(self) -> bool:
        try:
            ok = self._http.get("/api/v1/health", timeout=3.0).status_code == 200
        except httpx.HTTPError:
            ok = False
        self.online = ok
        return ok


def _is_cert_error(e: Exception) -> bool:
    c: BaseException | None = e
    while c is not None:
        if isinstance(c, ssl.SSLCertVerificationError):
            return True
        c = c.__cause__ or c.__context__
    return "CERTIFICATE_VERIFY_FAILED" in str(e)


class LoopbackTransport:
    """Для автотестов: in-process сервис с JSON-границей и инъекцией сбоев."""

    def __init__(self, service, addr: str = "127.0.0.1"):
        self._svc, self._addr = service, addr
        self.online = True
        self.drop_next_response = False
        self.sent: list[dict] = []

    def send(self, request: dict) -> dict:
        if not self.online:
            raise Offline()
        raw = json.loads(json.dumps(request, ensure_ascii=False))
        self.sent.append(raw)
        resp = json.loads(json.dumps(self._svc.handle(raw, self._addr), ensure_ascii=False))
        if self.drop_next_response:
            self.drop_next_response = False
            raise ResponseLost()
        return resp

    def ping(self) -> bool:
        return self.online

    def close(self):
        pass
