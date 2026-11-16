"""Офлайн-ядро клиента: зашифрованный локальный кэш, очередь операций, синхронизация, конфликты.

Перенос эталона spikes/reference-core/vector_ref/client.py (поведение закреплено тестами) с изменениями:
* локальные данные зашифрованы (vault.py), отдельный профиль на пару сервер+пользователь;
* вход: онлайн; при отсутствии связи — офлайн-разблокировка профиля (просмотр кэша и очередь queued-команд);
* «online_required» не зависит от флага online: попытка отправки, Offline -> отказ без постановки в очередь.

Правила (из контракта, не менять без Decision Log):
* класс команды берётся из contracts/command-catalog (queued / online_required);
* queued: устойчивый operation_id создаётся ОДИН раз и переиспользуется при всех повторах;
* синхронизация строго по порядку; на конфликте/отказе — стоп (причинность сохраняется);
* конфликт не сливается автоматически: «отменить своё» или «повторить поверх текущей версии» (новая операция);
* потерян ответ — операция остаётся в очереди с тем же operation_id.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from vector.core.contracts import Contracts

from .transport import Offline, ResponseLost, TlsError
from .vault import DeviceSecretStore, OpenVault, Vault, VaultError, profile_dir, server_dir

LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
  object_type TEXT NOT NULL,
  id          TEXT NOT NULL,
  data_enc    TEXT NOT NULL,
  server_enc  TEXT,
  server_version INTEGER,
  pending     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (object_type, id)
) STRICT;
CREATE TABLE IF NOT EXISTS outbox (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  operation_id TEXT NOT NULL UNIQUE,
  command      TEXT NOT NULL,
  payload_enc  TEXT NOT NULL,
  expected_version INTEGER,
  object_type  TEXT,
  object_id    TEXT,
  created_at   TEXT NOT NULL,
  attempts     INTEGER NOT NULL DEFAULT 0,
  state        TEXT NOT NULL CHECK (state IN ('pending','accepted','conflict','rejected','discarded','superseded')),
  response_enc TEXT
) STRICT;
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v_enc TEXT NOT NULL) STRICT;
"""

OBJECT_TYPES = (("customer", "customers"), ("tender", "tenders"), ("tkp", "tkp"), ("project", "projects"), ("kit", "kits"))


@dataclass
class LocalResult:
    status: str   # queued | accepted | refused_offline | conflict | rejected | denied | unauthenticated | unknown_outcome | blocked | error | offline_unlocked
    operation_id: Optional[str] = None
    message_ru: str = ""
    response: dict = field(default_factory=dict)


@dataclass
class SyncReport:
    sent: int = 0
    accepted: int = 0
    replayed: int = 0
    stopped_on: Optional[str] = None
    stop_status: Optional[str] = None
    offline: bool = False
    unknown_outcome: bool = False
    pulled: bool = False
    message_ru: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SyncClient:
    def __init__(self, transport, server_url: str, base_dir: Path, store: DeviceSecretStore, device_id: str,
                 contracts: Optional[Contracts] = None, clock=None, offline_unlock_max_days: int = 7, kdf: dict | None = None):
        self.t = transport
        self.server_url = server_url
        self.base_dir = Path(base_dir)
        self.store = store
        self.device_id = device_id
        self.contracts = contracts or Contracts.load()
        self.clock = clock
        self.max_days = offline_unlock_max_days
        self.kdf = kdf
        self.token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.login_name: Optional[str] = None
        self.must_change_password = False
        self.db: Optional[sqlite3.Connection] = None
        self.vault: Optional[Vault] = None
        self.ov: Optional[OpenVault] = None
        self.orphaned_cache: Optional[Path] = None

    def now(self) -> datetime:
        return self.clock.now() if self.clock else _now()

    # ------------------------------------------------------------ профили
    def _accounts_file(self) -> Path:
        return server_dir(self.base_dir, self.server_url) / "accounts.json"

    def _remember_account(self, login: str, user_id: str):
        f = self._accounts_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(f.read_text()) if f.exists() else {}
        data[login.lower()] = user_id
        f.write_text(json.dumps(data))

    def _known_user(self, login: str) -> Optional[str]:
        f = self._accounts_file()
        return json.loads(f.read_text()).get(login.lower()) if f.exists() else None

    def _open_db(self, ov: OpenVault):
        if self.db:
            self.db.close()
        self.ov = ov
        self.db = sqlite3.connect(str(ov.path / "local.db"), isolation_level=None, check_same_thread=False)
        self.db.executescript(LOCAL_SCHEMA)
        self.db.execute("PRAGMA secure_delete=ON")
        # Проверка ключа: служебная запись расшифровывается — иначе база чужая/повреждена.
        row = self.db.execute("SELECT v_enc FROM meta WHERE k='check'").fetchone()
        if row:
            self._open("meta", "check", row[0])
        else:
            self.db.execute("INSERT INTO meta VALUES ('check', ?)", (self._seal("meta", "check", "ok"),))

    def _seal(self, table, key, text):
        return self.ov.seal(table, key, text)

    def _open(self, table, key, sealed):
        return self.ov.open(table, key, sealed)

    def _set_meta(self, k: str, v: Any):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, self._seal("meta", k, json.dumps(v, ensure_ascii=False))))

    def get_meta(self, k: str) -> Any:
        row = self.db.execute("SELECT v_enc FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(self._open("meta", k, row[0])) if row else None

    # ------------------------------------------------------------ сеанс
    def login(self, login: str, password: str) -> LocalResult:
        try:
            r = self.t.send({"type": "login", "login": login, "password": password, "device_id": self.device_id})
        except TlsError as e:
            return LocalResult("error", message_ru=str(e))
        except (Offline, ResponseLost):
            return self._offline_unlock(login, password)
        if r.get("status") != "accepted":
            return LocalResult(r.get("status", "error"), message_ru=r.get("message_ru", ""), response=r)
        if self.user_id and self.user_id != r["user_id"]:
            self.logout()
        self.token, self.user_id, self.login_name = r["token"], r["user_id"], r["login"]
        self.must_change_password = bool(r.get("must_change_password"))
        self._remember_account(login, r["user_id"])
        self.vault = Vault(profile_dir(self.base_dir, self.server_url, r["user_id"]), self.store, self.max_days)
        ov = self.vault.unlock_after_online_login(password, self.now(), self.kdf)
        self.orphaned_cache = self.vault.orphaned
        self._open_db(ov)
        self._set_meta("session", {"user_id": r["user_id"], "login": r["login"], "display_name": r.get("display_name")})
        return LocalResult("accepted", response=r)

    def _offline_unlock(self, login: str, password: str) -> LocalResult:
        uid = self._known_user(login)
        if not uid:
            return LocalResult("refused_offline", message_ru="Нет связи с сервером, а офлайн-профиля этого пользователя на компьютере нет. "
                                                             "Первый вход требует связи с сервером.")
        vault = Vault(profile_dir(self.base_dir, self.server_url, uid), self.store, self.max_days)
        try:
            ov = vault.unlock_offline(password, self.now())
        except VaultError as e:
            return LocalResult("refused_offline", message_ru=e.message_ru, response={"code": e.code})
        self.vault, self.user_id, self.login_name, self.token = vault, uid, login, None
        self._open_db(ov)
        return LocalResult("offline_unlocked", message_ru="Нет связи с сервером. Открыт локальный кэш: просмотр и черновые изменения "
                                                          "уйдут на сервер после входа при появлении связи.")

    def relogin_online(self, password: str) -> LocalResult:
        """После офлайн-режима: получить сеанс тем же пользователем, не теряя очередь."""
        if not self.login_name:
            return LocalResult("rejected", message_ru="Нет активного профиля.")
        return self.login(self.login_name, password)

    def change_password(self, current: str, new: str) -> LocalResult:
        r = self.execute("account.change_password", {"current_password": current, "new_password": new})
        if r.status == "accepted":
            self.must_change_password = False
            self.vault.rewrap(self.ov, new, self.now(), self.kdf)
        return r

    def logout(self):
        if self.token:
            try:
                self.t.send({"type": "logout", "token": self.token})
            except (Offline, ResponseLost, TlsError):
                pass
        self.token = None
        if self.db:
            self.db.close()
        self.db, self.ov, self.user_id = None, None, None

    @property
    def online_session(self) -> bool:
        return self.token is not None

    # ------------------------------------------------------------ команды
    def execute(self, command: str, payload: dict, expected_version: Optional[int] = None) -> LocalResult:
        if self.db is None:
            return LocalResult("unauthenticated", message_ru="Войдите в систему.")
        cls = self.contracts.offline_class(command)
        label = self.contracts.label(command)
        if cls == "online_required":
            if not self.token:
                return LocalResult("refused_offline", message_ru=f"«{label}» требует актуальной проверки сервера (права, статус, версия). "
                                                                 "Сейчас нет сеанса с сервером — действие не выполнено и не поставлено в очередь.")
            if self.pending_count() and command != "account.change_password":
                rep = self.sync()
                if self.pending_count():
                    return LocalResult("blocked", message_ru="Сначала синхронизируйте или разрешите ожидающие изменения в очереди.",
                                       response={"sync": rep.__dict__})
            op_id = str(uuid.uuid4())
            try:
                r = self._send_command(op_id, command, payload, expected_version)
            except Offline:
                return LocalResult("refused_offline", op_id, f"Связь пропала до отправки — «{label}» не выполнено.")
            except TlsError as e:
                return LocalResult("error", op_id, str(e))
            except ResponseLost:
                if command == "account.change_password":
                    return LocalResult("unknown_outcome", op_id, "Ответ сервера не получен — неизвестно, сменён ли пароль. "
                                                                 "Попробуйте войти с новым паролем, затем со старым.")
                self._remember_online_unknown(op_id, command, payload, expected_version)
                return LocalResult("unknown_outcome", op_id, "Ответ сервера не получен: результат неизвестен. Нажмите «Проверить» — "
                                                             "система спросит сервер по тому же номеру операции, не выполняя действие дважды.")
            if r.get("status") == "unauthenticated":
                self.token = None if r.get("code") != "password_change_required" else self.token
            self._apply_response(r)
            return LocalResult(r["status"], op_id, r.get("message_ru", ""), r)

        op_id = str(uuid.uuid4())
        otype = self.contracts.catalog["commands"][command]["object"]
        oid = payload.get("id")
        self.db.execute("BEGIN")
        self.db.execute("INSERT INTO outbox(operation_id,command,payload_enc,expected_version,object_type,object_id,created_at,state) "
                        "VALUES (?,?,?,?,?,?,?, 'pending')",
                        (op_id, command, self._seal("outbox", op_id, json.dumps(payload, ensure_ascii=False)), expected_version, otype, oid,
                         self.now().isoformat()))
        self._overlay(otype, oid, payload)
        self.db.execute("COMMIT")
        res = LocalResult("queued", op_id, "Сохранено на этом компьютере и поставлено в очередь. Сервер ещё не подтвердил изменение.")
        if self.token:
            self.sync()
            st, resp = self._op_state(op_id)
            if st == "accepted":
                return LocalResult("accepted", op_id, "", resp)
            if st in ("conflict", "rejected"):
                return LocalResult(resp["status"], op_id, resp.get("message_ru", ""), resp)
        return res

    def retry_online(self, op_id: str) -> LocalResult:
        row = self.db.execute("SELECT command, payload_enc, expected_version FROM outbox WHERE operation_id=? AND state='pending'", (op_id,)).fetchone()
        if not row:
            return LocalResult("rejected", op_id, "Операция не найдена в очереди.")
        payload = json.loads(self._open("outbox", op_id, row[1]))
        try:
            r = self._send_command(op_id, row[0], payload, row[2])
        except (Offline, ResponseLost, TlsError):
            return LocalResult("unknown_outcome", op_id, "Ответ всё ещё не получен.")
        if r.get("status") in ("unauthenticated", "error"):
            return LocalResult(r["status"], op_id, r.get("message_ru", ""), r)
        self._finish(op_id, row[0], r)
        return LocalResult(r["status"], op_id, r.get("message_ru", ""), r)

    # ------------------------------------------------------------ синхронизация
    def sync(self) -> SyncReport:
        rep = SyncReport()
        if self.db is None or not self.token:
            rep.stop_status = "unauthenticated"
            rep.message_ru = "Нет сеанса с сервером: очередь сохранена и будет отправлена после входа."
            return rep
        while True:
            row = self.db.execute("SELECT operation_id, command, payload_enc, expected_version FROM outbox WHERE state='pending' ORDER BY seq LIMIT 1").fetchone()
            if not row:
                break
            op_id, command, penc, ev = row
            payload = json.loads(self._open("outbox", op_id, penc))
            self.db.execute("UPDATE outbox SET attempts=attempts+1 WHERE operation_id=?", (op_id,))
            try:
                r = self._send_command(op_id, command, payload, ev)
            except (Offline, TlsError):
                rep.offline = True
                rep.message_ru = "Нет связи с сервером. Изменения сохранены в очереди."
                return rep
            except ResponseLost:
                rep.unknown_outcome, rep.stopped_on = True, op_id
                rep.message_ru = "Ответ сервера не получен. Повторная отправка безопасна (тот же номер операции)."
                return rep
            rep.sent += 1
            if r.get("status") in ("unauthenticated", "error"):
                rep.stopped_on, rep.stop_status = op_id, r.get("status")
                rep.message_ru = r.get("message_ru", "")
                if r.get("status") == "unauthenticated":
                    self.token = None
                return rep
            self._finish(op_id, command, r)
            if r["status"] == "accepted":
                rep.accepted += 1
                rep.replayed += 1 if r.get("replayed") else 0
                continue
            rep.stopped_on, rep.stop_status, rep.message_ru = op_id, r["status"], r.get("message_ru", "")
            return rep
        rep.pulled = self.pull()
        return rep

    def pull(self) -> bool:
        if not self.token:
            return False
        try:
            r = self.t.send({"type": "query", "query": "pull", "token": self.token})
        except (Offline, ResponseLost, TlsError):
            return False
        if r.get("status") != "accepted":
            if r.get("status") == "unauthenticated":
                self.token = None
            return False
        res = r["result"]
        pending_keys = {(a, b) for a, b in self.db.execute("SELECT object_type, object_id FROM outbox WHERE state IN ('pending','conflict','rejected')")}
        self.db.execute("BEGIN")
        self.db.execute("DELETE FROM cache WHERE pending=0")  # права могли сузиться — кэш заменяется видимым сервером
        for otype, key in OBJECT_TYPES:
            for obj in res.get(key, []):
                if (otype, obj["id"]) in pending_keys:
                    self._set_server_state(otype, obj)
                    continue
                j = json.dumps(obj, ensure_ascii=False)
                self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?,0)",
                                (otype, obj["id"], self._seal("cache", f"{otype}/{obj['id']}/d", j),
                                 self._seal("cache", f"{otype}/{obj['id']}/s", j), obj["version"]))
        self._set_meta("last_pull", res["server_time"])
        self._set_meta("me", res["me"])
        self._set_meta("people", res.get("people", []))
        self._set_meta("assignments", res.get("assignments", []))
        self.db.execute("COMMIT")
        return True

    def query(self, name: str, **kw) -> dict:
        if not self.token:
            return {"status": "refused_offline", "message_ru": "Нужна связь с сервером."}
        try:
            return self.t.send({"type": "query", "query": name, "token": self.token, **kw})
        except (Offline, ResponseLost):
            return {"status": "refused_offline", "message_ru": "Нет связи с сервером."}
        except TlsError as e:
            return {"status": "error", "message_ru": str(e)}

    # ------------------------------------------------------------ конфликты и отказы
    def problems(self) -> list[dict]:
        out = []
        for op_id, command, state, penc, renc in self.db.execute(
                "SELECT operation_id, command, state, payload_enc, response_enc FROM outbox WHERE state IN ('conflict','rejected') ORDER BY seq"):
            r = json.loads(self._open("outbox", op_id + "/r", renc))
            out.append({"operation_id": op_id, "command": command, "label_ru": self.contracts.label(command), "state": state,
                        "payload": json.loads(self._open("outbox", op_id, penc)), "status": r.get("status"), "code": r.get("code"),
                        "message_ru": r.get("message_ru"), "server_current": (r.get("details") or {}).get("current")})
        return out

    def queue(self) -> list[dict]:
        out = []
        for op_id, command, state, attempts, created, otype, oid in self.db.execute(
                "SELECT operation_id, command, state, attempts, created_at, object_type, object_id FROM outbox "
                "WHERE state IN ('pending','conflict','rejected') ORDER BY seq"):
            out.append({"operation_id": op_id, "command": command, "label_ru": self.contracts.label(command), "state": state,
                        "attempts": attempts, "created_at": created, "object_type": otype, "object_id": oid})
        return out

    def discard(self, op_id: str) -> None:
        row = self.db.execute("SELECT object_type, object_id, state FROM outbox WHERE operation_id=?", (op_id,)).fetchone()
        if not row or row[2] not in ("conflict", "rejected", "pending"):
            raise ValueError("Нечего отменять")
        self.db.execute("UPDATE outbox SET state='discarded' WHERE operation_id=?", (op_id,))
        self._refresh_pending_flag(row[0], row[1])
        self.pull()

    def reapply_on_current(self, op_id: str) -> LocalResult:
        row = self.db.execute("SELECT command, payload_enc, response_enc, state FROM outbox WHERE operation_id=?", (op_id,)).fetchone()
        if not row or row[3] != "conflict":
            raise ValueError("Повтор поверх доступен только для конфликта")
        current = json.loads(self._open("outbox", op_id + "/r", row[2]))["details"]["current"]
        self.db.execute("UPDATE outbox SET state='superseded' WHERE operation_id=?", (op_id,))
        return self.execute(row[0], json.loads(self._open("outbox", op_id, row[1])), expected_version=current["version"])

    # ------------------------------------------------------------ чтение кэша
    def list_local(self, object_type: str) -> list[dict]:
        out = []
        for oid, denc, pending, sv in self.db.execute("SELECT id, data_enc, pending, server_version FROM cache WHERE object_type=? ORDER BY id", (object_type,)):
            d = json.loads(self._open("cache", f"{object_type}/{oid}/d", denc))
            d["_pending"] = bool(pending)
            d["_server_version"] = sv
            out.append(d)
        return out

    def get_local(self, object_type: str, oid: str) -> Optional[dict]:
        return next((d for d in self.list_local(object_type) if d["id"] == oid), None)

    def pending_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM outbox WHERE state='pending'").fetchone()[0] if self.db else 0

    def problem_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM outbox WHERE state IN ('conflict','rejected')").fetchone()[0] if self.db else 0

    # ------------------------------------------------------------ внутреннее
    def _op_state(self, op_id):
        st, renc = self.db.execute("SELECT state, response_enc FROM outbox WHERE operation_id=?", (op_id,)).fetchone()
        return st, (json.loads(self._open("outbox", op_id + "/r", renc)) if renc else {})

    def _send_command(self, op_id, command, payload, ev) -> dict:
        return self.t.send({"type": "command", "token": self.token, "operation_id": op_id, "command": command,
                            "payload": payload, "expected_version": ev})

    def _remember_online_unknown(self, op_id, command, payload, ev):
        otype = self.contracts.catalog["commands"][command]["object"]
        self.db.execute("INSERT INTO outbox(operation_id,command,payload_enc,expected_version,object_type,object_id,created_at,state) "
                        "VALUES (?,?,?,?,?,?,?,'pending')",
                        (op_id, command, self._seal("outbox", op_id, json.dumps(payload, ensure_ascii=False)), ev, otype, payload.get("id"),
                         self.now().isoformat()))

    def _finish(self, op_id, command, r):
        state = {"accepted": "accepted", "conflict": "conflict"}.get(r["status"], "rejected")
        row = self.db.execute("SELECT object_type, object_id FROM outbox WHERE operation_id=?", (op_id,)).fetchone()
        self.db.execute("BEGIN")
        self.db.execute("UPDATE outbox SET state=?, response_enc=? WHERE operation_id=?",
                        (state, self._seal("outbox", op_id + "/r", json.dumps(r, ensure_ascii=False)), op_id))
        if state == "accepted":
            self._apply_response(r)
        elif state == "conflict" and (r.get("details") or {}).get("current") and row:
            self._set_server_state(row[0], r["details"]["current"])
        self.db.execute("COMMIT")
        if row:
            self._refresh_pending_flag(row[0], row[1])

    def _open_problems(self, otype, oid) -> int:
        return self.db.execute("SELECT COUNT(*) FROM outbox WHERE object_type=? AND object_id=? AND state IN ('pending','conflict','rejected')",
                               (otype, oid)).fetchone()[0]

    def _set_server_state(self, otype, obj):
        j = json.dumps(obj, ensure_ascii=False)
        se = self._seal("cache", f"{otype}/{obj['id']}/s", j)
        if self.db.execute("UPDATE cache SET server_enc=?, server_version=? WHERE object_type=? AND id=?", (se, obj["version"], otype, obj["id"])).rowcount == 0:
            self.db.execute("INSERT INTO cache VALUES (?,?,?,?,?,0)", (otype, obj["id"], self._seal("cache", f"{otype}/{obj['id']}/d", j), se, obj["version"]))

    def _apply_response(self, r):
        if r.get("status") != "accepted":
            return
        for otype, _ in OBJECT_TYPES:
            obj = (r.get("result") or {}).get(otype)
            if not obj:
                continue
            self._set_server_state(otype, obj)
            if not self._open_problems(otype, obj["id"]):
                j = json.dumps(obj, ensure_ascii=False)
                self.db.execute("UPDATE cache SET data_enc=?, pending=0 WHERE object_type=? AND id=?",
                                (self._seal("cache", f"{otype}/{obj['id']}/d", j), otype, obj["id"]))

    def _overlay(self, otype, oid, payload):
        if not oid:
            return
        row = self.db.execute("SELECT data_enc FROM cache WHERE object_type=? AND id=?", (otype, oid)).fetchone()
        if row:
            data = json.loads(self._open("cache", f"{otype}/{oid}/d", row[0]))
            data.update(payload)
            self.db.execute("UPDATE cache SET data_enc=?, pending=1 WHERE object_type=? AND id=?",
                            (self._seal("cache", f"{otype}/{oid}/d", json.dumps(data, ensure_ascii=False)), otype, oid))
        else:
            self.db.execute("INSERT INTO cache VALUES (?,?,?,NULL,NULL,1)",
                            (otype, oid, self._seal("cache", f"{otype}/{oid}/d", json.dumps(payload, ensure_ascii=False))))

    def _refresh_pending_flag(self, otype, oid):
        if not oid or self._open_problems(otype, oid):
            return
        row = self.db.execute("SELECT server_enc FROM cache WHERE object_type=? AND id=?", (otype, oid)).fetchone()
        if not row:
            return
        if row[0] is None:
            self.db.execute("DELETE FROM cache WHERE object_type=? AND id=?", (otype, oid))
        else:
            j = self._open("cache", f"{otype}/{oid}/s", row[0])
            self.db.execute("UPDATE cache SET data_enc=?, pending=0 WHERE object_type=? AND id=?",
                            (self._seal("cache", f"{otype}/{oid}/d", j), otype, oid))
