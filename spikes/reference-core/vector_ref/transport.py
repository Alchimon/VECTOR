"""Транспорт клиент↔сервер с JSON-границей и инъекцией сбоев.

Эталон проходит через сериализацию JSON (как реальный API), чтобы деньги и типы
проверялись на границе. Целевой протокол (gRPC/HTTP-JSON поверх TLS) — решение spike S2.
"""
from __future__ import annotations

import json


class Offline(Exception):
    """Нет соединения: запрос не дошёл до сервера."""


class ResponseLost(Exception):
    """Запрос дошёл и мог быть выполнен, но ответ потерян (исход неизвестен клиенту)."""


class LoopbackTransport:
    def __init__(self, server):
        self._server = server
        self.online = True
        self.drop_next_response = False
        self.sent: list[dict] = []

    def send(self, request: dict) -> dict:
        if not self.online:
            raise Offline()
        raw = json.dumps(request, ensure_ascii=False)
        self.sent.append(json.loads(raw))
        response_raw = self._server.handle_json(raw)
        if self.drop_next_response:
            self.drop_next_response = False
            raise ResponseLost()
        return json.loads(response_raw)
