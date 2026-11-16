"""Офлайн-клиент эталонного ядра: локальное хранилище, очередь, синхронизация, конфликты.

Модель поведения (для переноса в native desktop-клиент):
* класс команды берётся из contracts/command-catalog (offline_full / queued / online_required);
* queued: устойчивый operation_id создаётся ОДИН раз и переиспользуется при всех повторах;
  локально объект виден с пометкой «не синхронизировано»; это не подтверждённое действие;
* online_required офлайн: отказ с объяснением, в очередь НЕ ставится;
* синхронизация строго по порядку; на конфликте/отказе — стоп (причинность сохраняется),
  последующие операции ждут решения пользователя;
* конфликт не сливается автоматически: пользователь выбирает «отменить своё» или
  «повторить поверх текущей версии» (новая операция, новый operation_id);
* потерян ответ — операция остаётся в очереди с тем же operation_id; сервер вернёт
  сохранённый исход (дубликат не создаётся).

Ограничение spike: локальная БД не зашифрована, токен хранится в памяти. В продукте — DPAPI/
защищённое хранилище Windows и шифрование локальной БД (spike S3).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .contracts import Contracts
from .transport import Offline, ResponseLost

LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
  object_type TEXT NOT NULL,
  id          TEXT NOT NULL,
  data_json   TEXT NOT NULL,      -- то, что видит пользователь (сервер + локальные несинхронизированные правки)
  server_json TEXT,               -- последнее известное серверное состояние; NULL = сервер объект ещё не видел
  server_version INTEGER,
  pending     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (object_type, id)
) STRICT;
CREATE TABLE IF NOT EXISTS outbox (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  operation_id TEXT NOT NULL UNIQUE,
  command      TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  expected_version INTEGER,
  object_type  TEXT,
  object_id    TEXT,
  created_at   TEXT NOT NULL,
  attempts     INTEGER NOT NULL DEFAULT 0,
  state        TEXT NOT NULL CHECK (state IN ('pending','accepted','conflict','rejected','discarded','superseded')),
  last_response_json TEXT
) STRICT;
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL) STRICT;
"""

_ID_FIELD = {"customer": "id", "tender": "id", "tkp": "id", "kit": "id", "project": "id"}


@dataclass
class LocalResult:
    status: str                     # queued | accepted | refused_offline | conflict | rejected | denied | unauthenticated | unknown_outcome | blocked
    operation_id: Optional[str] = None
    message_ru: str = ""
    response: dict = field(default_factory=dict)


@dataclass
class SyncReport:
    sent: int = 0
    accepted: int = 0
    replayed: int = 0
    stopped_on: Optional[str] = None        # operation_id
    stop_status: Optional[str] = None
    offline: bool = False
    unknown_outcome: bool = False
    pulled: bool = False


class Client:
    def __init__(self, local_db_path: str, transport, device_id: str, contracts: Optional[Contracts] = None):
        self.db = sqlite3.connect(local_db_path, isolation_level=None)
        self.db.executescript(LOCAL_SCHEMA)
        self.t = transport
        self.device_id = device_id
        self.contracts = contracts or Contracts.load()
        self.token: Optional[str] = None
        self.user_id: Optional[str] = None

    # ------------------------------------------------------------ сеанс
    def login(self, login: str, password: str) -> LocalResult:
        try:
            r = self.t.send({"type": "login", "login": login, "password": password, "device_id": self.device_id})
        except (Offline, ResponseLost):
            return LocalResult("refused_offline", message_ru="Вход требует связи с сервером. Офлайн доступен просмотр кэша последнего пользователя этого устройства.")
        if r["status"] != "accepted":
            return LocalResult(r["status"], message_ru=r.get("message_ru", ""), response=r)
        self.token, self.user_id = r["token"], r["user_id"]
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('user_id', ?)", (self.user_id,))
        return LocalResult("accepted", response=r)

    # ------------------------------------------------------------ команды
    def execute(self, command: str, payload: dict, expected_version: Optional[int] = None) -> LocalResult:
        cls = self.contracts.offline_class(command)
        if cls == "online_required":
            if not self.t.online:
                return LocalResult("refused_offline", message_ru=(
                    f"«{self.contracts.catalog['commands'][command]['label_ru']}» требует актуальной проверки сервера "
                    "(права, статус, версия). Сейчас нет связи — действие не выполнено и не поставлено в очередь."))
            if self.pending_count():
                rep = self.sync()
                if self.pending_count():
                    return LocalResult("blocked", message_ru="Сначала нужно синхронизировать или разрешить ожидающие изменения в очереди.",
                                       response={"sync": rep.__dict__})
            op_id = str(uuid.uuid4())
            try:
                r = self._send_command(op_id, command, payload, expected_version)
            except Offline:
                return LocalResult("refused_offline", op_id, "Связь пропала до отправки — действие не выполнено.")
            except ResponseLost:
                # Исход неизвестен. Повтор возможен только с тем же operation_id (retry_online).
                self._remember_online_unknown(op_id, command, payload, expected_version)
                return LocalResult("unknown_outcome", op_id, "Ответ сервера не получен: результат неизвестен. "
                                   "Повторите проверку — система спросит сервер по тому же номеру операции, не выполняя действие дважды.")
            self._apply_response(command, payload, r)
            return LocalResult(r["status"], op_id, r.get("message_ru", ""), r)

        # queued
        op_id = str(uuid.uuid4())
        otype = self.contracts.catalog["commands"][command]["object"]
        oid = payload.get(_ID_FIELD.get(otype, "id"))
        self.db.execute("BEGIN")
        self.db.execute("INSERT INTO outbox(operation_id,command,payload_json,expected_version,object_type,object_id,created_at,state) VALUES (?,?,?,?,?,?,?, 'pending')",
                        (op_id, command, json.dumps(payload, ensure_ascii=False), expected_version, otype, oid, _now()))
        self._overlay(otype, oid, payload)
        self.db.execute("COMMIT")
        res = LocalResult("queued", op_id, "Сохранено локально и поставлено в очередь синхронизации. Это ещё не подтверждено сервером.")
        if self.t.online and self.token:
            self.sync()
            state = self.db.execute("SELECT state, last_response_json FROM outbox WHERE operation_id=?", (op_id,)).fetchone()
            if state[0] == "accepted":
                return LocalResult("accepted", op_id, "", json.loads(state[1]))
            if state[0] in ("conflict", "rejected"):
                r = json.loads(state[1])
                return LocalResult(r["status"], op_id, r.get("message_ru", ""), r)
        return res

    def retry_online(self, op_id: str) -> LocalResult:
        row = self.db.execute("SELECT command, payload_json, expected_version FROM outbox WHERE operation_id=? AND state='pending'", (op_id,)).fetchone()
        if not row:
            return LocalResult("rejected", op_id, "Операция не найдена в очереди.")
        try:
            r = self._send_command(op_id, row[0], json.loads(row[1]), row[2])
        except (Offline, ResponseLost):
            return LocalResult("unknown_outcome", op_id, "Ответ всё ещё не получен.")
        self._finish(op_id, row[0], json.loads(row[1]), r)
        return LocalResult(r["status"], op_id, r.get("message_ru", ""), r)

    # ------------------------------------------------------------ синхронизация
    def sync(self) -> SyncReport:
        rep = SyncReport()
        if not self.token:
            rep.stop_status = "unauthenticated"
            return rep
        while True:
            row = self.db.execute("SELECT operation_id, command, payload_json, expected_version FROM outbox WHERE state='pending' ORDER BY seq LIMIT 1").fetchone()
            if not row:
                break
            op_id, command, payload_json, ev = row
            payload = json.loads(payload_json)
            self.db.execute("UPDATE outbox SET attempts=attempts+1 WHERE operation_id=?", (op_id,))
            try:
                r = self._send_command(op_id, command, payload, ev)
            except Offline:
                rep.offline = True
                return rep
            except ResponseLost:
                rep.unknown_outcome = True
                rep.stopped_on = op_id
                return rep
            rep.sent += 1
            if r["status"] in ("unauthenticated", "error"):
                rep.stopped_on, rep.stop_status = op_id, r["status"]
                return rep  # операция остаётся pending: повтор безопасен
            self._finish(op_id, command, payload, r)
            if r["status"] == "accepted":
                rep.accepted += 1
                rep.replayed += 1 if r.get("replayed") else 0
                continue
            rep.stopped_on, rep.stop_status = op_id, r["status"]
            return rep
        rep.pulled = self.pull()
        return rep

    def pull(self) -> bool:
        try:
            r = self.t.send({"type": "query", "query": "pull", "token": self.token})
        except (Offline, ResponseLost):
            return False
        if r["status"] != "accepted":
            return False
        res = r["result"]
        pending_keys = {(a, b) for a, b in self.db.execute("SELECT object_type, object_id FROM outbox WHERE state IN ('pending','conflict','rejected')")}
        self.db.execute("BEGIN")
        # Удаляем из кэша всё не-ожидающее и перезаписываем видимым сервером (права могли сузиться).
        self.db.execute("DELETE FROM cache WHERE pending=0")
        for otype, key in (("customer", "customers"), ("tender", "tenders"), ("tkp", "tkp"), ("project", "projects"), ("kit", "kits")):
            for obj in res[key]:
                if (otype, obj["id"]) in pending_keys:
                    self._set_server_state(otype, obj)  # локальный черновик остаётся поверх
                    continue
                j = json.dumps(obj, ensure_ascii=False)
                self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?,0)", (otype, obj["id"], j, j, obj["version"]))
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('last_pull', ?)", (res["server_time"],))
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('me', ?)", (json.dumps(res["me"], ensure_ascii=False),))
        self.db.execute("COMMIT")
        return True

    # ------------------------------------------------------------ конфликты и отказы
    def problems(self) -> list[dict]:
        out = []
        for op_id, command, state, payload, resp in self.db.execute(
                "SELECT operation_id, command, state, payload_json, last_response_json FROM outbox WHERE state IN ('conflict','rejected') ORDER BY seq"):
            r = json.loads(resp)
            out.append({"operation_id": op_id, "command": command, "state": state, "payload": json.loads(payload),
                        "code": r.get("code"), "message_ru": r.get("message_ru"), "server_current": r.get("details", {}).get("current")})
        return out

    def discard(self, op_id: str) -> None:
        """«Отменить моё изменение»: локальная правка удаляется, кэш берётся с сервера."""
        row = self.db.execute("SELECT object_type, object_id, state FROM outbox WHERE operation_id=?", (op_id,)).fetchone()
        if not row or row[2] not in ("conflict", "rejected", "pending"):
            raise ValueError("Нечего отменять")
        self.db.execute("UPDATE outbox SET state='discarded' WHERE operation_id=?", (op_id,))
        self._refresh_pending_flag(row[0], row[1])
        if self.t.online:
            self.pull()

    def reapply_on_current(self, op_id: str) -> LocalResult:
        """«Повторить поверх текущей версии» после просмотра различий: НОВАЯ операция с новым id."""
        row = self.db.execute("SELECT command, payload_json, last_response_json, state FROM outbox WHERE operation_id=?", (op_id,)).fetchone()
        if not row or row[3] != "conflict":
            raise ValueError("Повтор поверх доступен только для конфликта")
        current = json.loads(row[2])["details"]["current"]
        self.db.execute("UPDATE outbox SET state='superseded' WHERE operation_id=?", (op_id,))
        return self.execute(row[0], json.loads(row[1]), expected_version=current["version"])

    # ------------------------------------------------------------ чтение кэша (офлайн)
    def list_local(self, object_type: str) -> list[dict]:
        out = []
        for data, pending, sv in self.db.execute("SELECT data_json, pending, server_version FROM cache WHERE object_type=? ORDER BY id", (object_type,)):
            d = json.loads(data)
            d["_sync"] = "не синхронизировано" if pending else "синхронизировано"
            d["_server_version"] = sv
            out.append(d)
        return out

    def get_local(self, object_type: str, oid: str) -> Optional[dict]:
        for d in self.list_local(object_type):
            if d["id"] == oid:
                return d
        return None

    def pending_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM outbox WHERE state='pending'").fetchone()[0]

    def outbox_states(self) -> list[tuple[str, str, int]]:
        return list(self.db.execute("SELECT command, state, attempts FROM outbox ORDER BY seq"))

    # ------------------------------------------------------------ внутреннее
    def _send_command(self, op_id, command, payload, ev) -> dict:
        return self.t.send({"type": "command", "token": self.token, "operation_id": op_id, "command": command,
                            "payload": payload, "expected_version": ev})

    def _remember_online_unknown(self, op_id, command, payload, ev):
        otype = self.contracts.catalog["commands"][command]["object"]
        self.db.execute("INSERT INTO outbox(operation_id,command,payload_json,expected_version,object_type,object_id,created_at,state) VALUES (?,?,?,?,?,?,?,'pending')",
                        (op_id, command, json.dumps(payload, ensure_ascii=False), ev, otype, payload.get("id"), _now()))

    def _finish(self, op_id, command, payload, r):
        state = {"accepted": "accepted", "conflict": "conflict"}.get(r["status"], "rejected")
        row = self.db.execute("SELECT object_type, object_id FROM outbox WHERE operation_id=?", (op_id,)).fetchone()
        self.db.execute("BEGIN")
        self.db.execute("UPDATE outbox SET state=?, last_response_json=? WHERE operation_id=?", (state, json.dumps(r, ensure_ascii=False), op_id))
        if state == "accepted":
            self._apply_response(command, payload, r)
        elif state == "conflict" and r.get("details", {}).get("current") and row:
            self._set_server_state(row[0], r["details"]["current"])
        self.db.execute("COMMIT")
        if row:
            self._refresh_pending_flag(row[0], row[1])

    def _open_problems(self, otype, oid) -> int:
        return self.db.execute("SELECT COUNT(*) FROM outbox WHERE object_type=? AND object_id=? AND state IN ('pending','conflict','rejected')",
                               (otype, oid)).fetchone()[0]

    def _set_server_state(self, otype, obj):
        j = json.dumps(obj, ensure_ascii=False)
        if self.db.execute("UPDATE cache SET server_json=?, server_version=? WHERE object_type=? AND id=?", (j, obj["version"], otype, obj["id"])).rowcount == 0:
            self.db.execute("INSERT INTO cache VALUES (?,?,?,?,?,0)", (otype, obj["id"], j, j, obj["version"]))

    def _apply_response(self, command, payload, r):
        if r.get("status") != "accepted":
            return
        for otype in ("customer", "tender", "tkp", "project", "kit"):
            obj = r.get("result", {}).get(otype)
            if not obj:
                continue
            self._set_server_state(otype, obj)
            if not self._open_problems(otype, obj["id"]):
                j = json.dumps(obj, ensure_ascii=False)
                self.db.execute("UPDATE cache SET data_json=?, pending=0 WHERE object_type=? AND id=?", (j, otype, obj["id"]))

    def _overlay(self, otype, oid, payload):
        if not oid:
            return
        row = self.db.execute("SELECT data_json FROM cache WHERE object_type=? AND id=?", (otype, oid)).fetchone()
        if row:
            data = json.loads(row[0])
            data.update(payload)
            self.db.execute("UPDATE cache SET data_json=?, pending=1 WHERE object_type=? AND id=?", (json.dumps(data, ensure_ascii=False), otype, oid))
        else:
            self.db.execute("INSERT INTO cache VALUES (?,?,?,NULL,NULL,1)", (otype, oid, json.dumps(payload, ensure_ascii=False)))

    def _refresh_pending_flag(self, otype, oid):
        if not oid or self._open_problems(otype, oid):
            return
        row = self.db.execute("SELECT server_json FROM cache WHERE object_type=? AND id=?", (otype, oid)).fetchone()
        if not row:
            return
        if row[0] is None:
            self.db.execute("DELETE FROM cache WHERE object_type=? AND id=?", (otype, oid))  # черновик, не принятый сервером
        else:
            self.db.execute("UPDATE cache SET data_json=server_json, pending=0 WHERE object_type=? AND id=?", (otype, oid))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
