"""Application service сервера ВЕКТОР — единственная граница записи бизнес-данных.

Каждая команда — одна транзакция (SERIALIZABLE / BEGIN IMMEDIATE):
сеанс → права (в момент эффекта) → состояние/версия → эффект → журнал → результат операции.
Частичные эффекты при отказе откатываются SAVEPOINT; отказ и журнал отказа фиксируются.
Конфликт сериализации/дубль первичного ключа -> повтор всей транзакции (ограниченно).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from vector import __version__
from vector.core import money
from vector.core.contracts import Contracts
from vector.core.errors import CommandOutcome, Conflict, Denied, Rejected, Unauthenticated

from . import db, security
from .authz import Authority, Policy, active_grants, grant_is_active
from .work import WorkMixin
from .db import audit_log, customers, kits, operations, project_assignments, projects, role_grants, sessions, tenders, tkp, tkp_approvals, users

log = logging.getLogger("vector.server")

MAX_ATTEMPTS = 6


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


# Валидаторы и сериализация вынесены в validation.py (используются и модулем задач/документов);
# импортируются сюда под прежними именами, чтобы не менять существующий код и тесты.
from .validation import (_CUR_RE, _INN_RE, _LOGIN_RE, _UUID_RE, _json_default, canon, iso, only_keys,  # noqa: E402,F401
                         opt_date, opt_int, opt_str, parse_iso_utc, req_str, req_uuid)


class Ctx:
    def __init__(self, conn: Connection, user: dict, device_id: str, op_id: str, now: datetime, command: str,
                 session_hash: str, client_addr: Optional[str]):
        self.conn, self.user, self.user_id = conn, user, user["id"]
        self.device_id, self.operation_id, self.now, self.command = device_id, op_id, now, command
        self.session_hash, self.client_addr = session_hash, client_addr
        self.audit: list[dict] = []

    def log(self, otype, oid, authority: Optional[Authority], before, after, details=None):
        self.audit.append(dict(object_type=otype, object_id=str(oid) if oid is not None else None,
                               authority=authority.describe() if authority else None,
                               version_before=before, version_after=after, details=details or {}))


class Service(WorkMixin):
    """Сервис команд. Задачи, документы и файлы — в примеси WorkMixin (work.py)."""

    def __init__(self, engine: Engine, contracts: Optional[Contracts] = None, clock=None,
                 sec: Optional[security.SecurityPolicy] = None):
        self.engine = engine
        self.contracts = contracts or Contracts.load()
        self.policy = Policy(self.contracts.permissions)
        self.wf = self.contracts.workflows
        self.clock = clock or SystemClock()
        self.sec = sec or security.SecurityPolicy()
        self.sec.validate()
        self.rate = security.RateLimiter(self.sec.login_rate_per_minute_per_addr)
        # Подбор текущего пароля через украденный сеанс: не чаще lockout_threshold попыток в минуту на пользователя.
        self.pwd_rate = security.RateLimiter(self.sec.lockout_threshold)
        self._fault_after_effect: Optional[str] = None
        self.handlers: dict[str, Callable[[Ctx, dict, Optional[int]], dict]] = {
            "customer.create": self._customer_create, "customer.update": self._customer_update,
            "tender.create": self._tender_create, "tender.update": self._tender_update, "tender.transition": self._tender_transition,
            "tkp.create": self._tkp_create, "tkp.update": self._tkp_update, "tkp.approve": self._tkp_approve,
            "tkp.reopen": self._tkp_reopen, "tkp.record_sent": self._tkp_record_sent,
            "project.start_rkd": self._project_start_rkd, "project.assign": self._project_assign,
            "project.revoke_assignment": self._project_revoke_assignment,
            "kit.create": self._kit_create, "kit.update": self._kit_update,
            "account.change_password": self._account_change_password,
            "admin.create_user": self._admin_create_user, "admin.grant_role": self._admin_grant_role,
            "admin.revoke_role": self._admin_revoke_role, "admin.revoke_sessions": self._admin_revoke_sessions,
            "admin.reset_password": self._admin_reset_password, "admin.set_user_active": self._admin_set_user_active,
            # задачи и документы (work.py)
            "task.create": self._task_create, "task.update": self._task_update, "task.transition": self._task_transition,
            "document.create": self._document_create, "document.update": self._document_update,
            "document.add_version": self._document_add_version,
        }
        diff = set(self.contracts.catalog["commands"]) ^ set(self.handlers)
        if diff:
            raise RuntimeError(f"Каталог команд и обработчики расходятся: {sorted(diff)}")

    # ------------------------------------------------------------ инфраструктура
    def now(self) -> datetime:
        return self.clock.now()

    def _tx(self, fn: Callable[[Connection], Any]) -> Any:
        for attempt in range(MAX_ATTEMPTS):
            try:
                with self.engine.begin() as conn:
                    return fn(conn)
            except sa.exc.IntegrityError:
                # Например, параллельная вставка того же operation_id: повтор увидит сохранённый исход.
                if attempt == MAX_ATTEMPTS - 1:
                    raise
            except sa.exc.DBAPIError as e:
                if not db.is_retryable(e) or attempt == MAX_ATTEMPTS - 1:
                    raise
            time.sleep(0.01 * (2 ** attempt))
        raise RuntimeError("unreachable")

    def _audit(self, conn, at, actor, device, addr, op_id, command, otype, oid, authority, vb, va, result, details):
        conn.execute(audit_log.insert().values(
            at=at, actor_user_id=actor, device_id=device, client_addr=addr, operation_id=op_id, command=command,
            object_type=otype, object_id=oid, authority=authority, version_before=vb, version_after=va,
            result=result, details_json=canon(details)))

    @staticmethod
    def _get(conn, table, key, col="id") -> Optional[dict]:
        r = conn.execute(sa.select(table).where(table.c[col] == key)).first()
        return dict(r._mapping) if r else None

    def bootstrap_admin(self, login: str, password: str, display_name: str = "Администратор") -> str:
        """Однократно: только если учётных записей нет (F-10-подобная защита от повторного bootstrap)."""
        problems = security.password_problems(password, login, self.sec)
        if problems or not _LOGIN_RE.match(login):
            raise Rejected("weak_password", " ".join(problems) or "Недопустимый логин.")

        def tx(conn):
            if conn.execute(sa.select(sa.func.count()).select_from(users)).scalar():
                raise Rejected("already_bootstrapped", "Система уже инициализирована; повторный bootstrap запрещён.")
            now, uid = self.now(), str(uuid.uuid4())
            conn.execute(users.insert().values(id=uid, login=login, display_name=display_name,
                                               pwd_hash=security.hash_password(password), must_change_password=False,
                                               password_changed_at=now, failed_attempts=0, active=True, created_at=now))
            gid = conn.execute(role_grants.insert().values(user_id=uid, role="admin", scope_type="company", valid_from=now,
                                                           reason="bootstrap")).inserted_primary_key[0]
            self._audit(conn, now, None, None, None, None, "system.bootstrap", "user", uid, f"bootstrap#grant:{gid}", None, 1,
                        "accepted", {"login": login})
            return uid
        return self._tx(tx)

    # ------------------------------------------------------------ точка входа
    def handle(self, req: dict, client_addr: Optional[str] = None) -> dict:
        try:
            t = req.get("type") if isinstance(req, dict) else None
            if t == "login":
                return self._login(req, client_addr)
            if t == "logout":
                return self._logout(req)
            if t == "command":
                return self._command(req, client_addr)
            if t == "query":
                return self._query(req)
            return {"status": "rejected", "code": "bad_type", "message_ru": "Неизвестный тип запроса."}
        except Exception:
            log.exception("internal error")  # стек — только в журнале сервера; секретов в запросах журнал не содержит
            return {"status": "error", "code": "internal_error", "operation_id": req.get("operation_id") if isinstance(req, dict) else None,
                    "message_ru": "Внутренняя ошибка сервера. Изменение не применено; повтор безопасен."}

    # ------------------------------------------------------------ вход/выход
    def _login(self, req: dict, addr: Optional[str]) -> dict:
        login, password, device = req.get("login"), req.get("password"), req.get("device_id")
        if not all(isinstance(x, str) and x for x in (login, password, device)) or len(device) > 100 or len(login) > 64:
            return {"status": "rejected", "code": "bad_login_request", "message_ru": "Укажите логин, пароль и идентификатор устройства."}
        if not self.rate.allow(addr or "local", self.now().timestamp()):
            return {"status": "rejected", "code": "rate_limited", "message_ru": "Слишком много попыток входа. Подождите минуту."}
        generic = {"status": "unauthenticated", "code": "bad_credentials",
                   "message_ru": "Неверный логин или пароль, либо учётная запись отключена."}

        def tx(conn):
            now = self.now()
            u = self._get(conn, users, login, "login")
            ok, rehash = security.verify_password(u["pwd_hash"] if u else None, password)
            if u and u["locked_until"] and u["locked_until"] > now:
                self._audit(conn, now, u["id"], device, addr, None, "auth.login", "user", u["id"], None, None, None, "denied", {"reason": "locked"})
                return {"status": "unauthenticated", "code": "account_locked",
                        "message_ru": f"Учётная запись временно заблокирована после неудачных попыток. Повторите после {u['locked_until'].astimezone(timezone.utc):%H:%M} UTC или обратитесь к администратору.",
                        "locked_until": iso(u["locked_until"])}
            if not u or not ok or not u["active"]:
                if u and not ok:
                    fails = u["failed_attempts"] + 1
                    vals: dict[str, Any] = {"failed_attempts": fails}
                    if fails >= self.sec.lockout_threshold:
                        vals = {"failed_attempts": 0, "locked_until": now + timedelta(minutes=self.sec.lockout_minutes)}
                    conn.execute(users.update().where(users.c.id == u["id"]).values(**vals))
                self._audit(conn, now, u["id"] if u else None, device, addr, None, "auth.login", "user", u["id"] if u else None,
                            None, None, None, "denied", {"login": login, "reason": "inactive" if (u and ok) else "bad_credentials"})
                return generic
            vals = {"failed_attempts": 0, "locked_until": None}
            if rehash:
                vals["pwd_hash"] = rehash
            conn.execute(users.update().where(users.c.id == u["id"]).values(**vals))
            token, th = security.new_token()
            exp = now + timedelta(hours=self.sec.session_absolute_hours)
            conn.execute(sessions.insert().values(token_hash=th, user_id=u["id"], device_id=device, client_addr=addr,
                                                  issued_at=now, last_seen_at=now, expires_at=exp))
            self._audit(conn, now, u["id"], device, addr, None, "auth.login", "user", u["id"], None, None, None, "accepted", {})
            return {"status": "accepted", "token": token, "user_id": u["id"], "display_name": u["display_name"],
                    "login": u["login"], "must_change_password": bool(u["must_change_password"]), "expires_at": iso(exp),
                    "idle_timeout_minutes": self.sec.session_idle_minutes}
        return self._tx(tx)

    def _logout(self, req: dict) -> dict:
        th = security.token_hash(req.get("token") or "")

        def tx(conn):
            now = self.now()
            n = conn.execute(sessions.update().where(sessions.c.token_hash == th, sessions.c.revoked_at.is_(None))
                             .values(revoked_at=now, revoked_reason="logout")).rowcount
            return {"status": "accepted", "revoked": n}
        return self._tx(tx)

    def _auth(self, conn, token, now, allow_restricted=False) -> tuple[dict, str, str]:
        if not isinstance(token, str) or not token:
            raise Unauthenticated("no_token", "Требуется вход в систему.")
        th = security.token_hash(token)
        s = self._get(conn, sessions, th, "token_hash")
        if (not s or s["revoked_at"] is not None or s["expires_at"] <= now
                or now - s["last_seen_at"] > timedelta(minutes=self.sec.session_idle_minutes)):
            raise Unauthenticated("session_invalid", "Сеанс истёк или завершён. Войдите снова; локальная очередь изменений сохранится.")
        u = self._get(conn, users, s["user_id"])
        if not u or not u["active"]:
            raise Unauthenticated("session_invalid", "Учётная запись отключена.")
        if u["must_change_password"] and not allow_restricted:
            raise Unauthenticated("password_change_required", "Перед началом работы смените временный пароль.")
        conn.execute(sessions.update().where(sessions.c.token_hash == th).values(last_seen_at=now))
        return u, s["device_id"], th

    # ------------------------------------------------------------ команды
    def _command(self, req: dict, addr: Optional[str]) -> dict:
        op_id, command, payload, ev = req.get("operation_id"), req.get("command"), req.get("payload"), req.get("expected_version")
        if not isinstance(op_id, str) or not _UUID_RE.match(op_id):
            return {"status": "rejected", "code": "bad_operation_id", "message_ru": "operation_id должен быть UUID."}
        if command not in self.handlers:
            return {"status": "rejected", "code": "unknown_command", "message_ru": f"Неизвестная команда «{command}»."}
        if not isinstance(payload, dict):
            return {"status": "rejected", "code": "bad_payload", "message_ru": "payload должен быть объектом."}
        if ev is not None and (isinstance(ev, bool) or not isinstance(ev, int)):
            return {"status": "rejected", "code": "bad_expected_version", "message_ru": "expected_version должен быть целым числом."}
        # Секреты (пароли) не входят в хэш в открытом виде — только как часть SHA-256 всего запроса.
        request_hash = hashlib.sha256(canon({"c": command, "p": payload, "v": ev}).encode()).hexdigest()

        def tx(conn):
            now = self.now()
            prev = self._get(conn, operations, op_id, "operation_id")
            try:
                user, device, th = self._auth(conn, req.get("token"), now, allow_restricted=(command == "account.change_password"))
            except Unauthenticated as e:
                return {"status": e.status, "code": e.code, "message_ru": e.message_ru}
            if prev:
                if prev["request_hash"] != request_hash or prev["user_id"] != user["id"]:
                    return {"status": "rejected", "code": "operation_id_reused",
                            "message_ru": "Этот operation_id уже использован для другой операции. Повтор с изменёнными данными запрещён."}
                stored = json.loads(prev["response_json"])
                stored["replayed"] = True
                return stored
            ctx = Ctx(conn, user, device, op_id, now, command, th, addr)
            sp = conn.begin_nested()
            try:
                if self.contracts.catalog["commands"][command].get("requires_expected_version") and ev is None:
                    raise Rejected("expected_version_required", "Для изменения нужна версия, на которой основана правка (expected_version).")
                result = self.handlers[command](ctx, payload, ev)
                response = {"status": "accepted", "operation_id": op_id, "result": result}
                for a in ctx.audit:
                    self._audit(conn, now, user["id"], device, addr, op_id, command, a["object_type"], a["object_id"],
                                a["authority"], a["version_before"], a["version_after"], "accepted", a["details"])
                if self._fault_after_effect == command:
                    raise RuntimeError("injected fault after effect, before commit")
                sp.commit()
            except CommandOutcome as e:
                sp.rollback()
                response = {"status": e.status, "operation_id": op_id, "code": e.code, "message_ru": e.message_ru, "details": e.details}
                self._audit(conn, now, user["id"], device, addr, op_id, command, None, None, None, None, None, e.status,
                            {"code": e.code, **({"details": e.details} if e.details else {})})
            conn.execute(operations.insert().values(operation_id=op_id, user_id=user["id"], device_id=device, command=command,
                                                    request_hash=request_hash, status=response["status"],
                                                    response_json=canon(response), recorded_at=now))
            return response
        return self._tx(tx)

    # ------------------------------------------------------------ helpers
    def _require(self, conn, table, oid, what_ru) -> dict:
        o = self._get(conn, table, oid)
        if o is None:
            raise Rejected("not_found", f"{what_ru}: объект не найден.", {"id": oid})
        return o

    @staticmethod
    def _check_version(o: dict, expected: Optional[int], view):
        if expected is not None and o["version"] != expected:
            raise Conflict("version_conflict",
                           f"Объект изменён другим пользователем: ваша версия {expected}, на сервере {o['version']}. "
                           "Изменение не применено; сравните и решите, повторить ли его.",
                           {"expected_version": expected, "current": view(o)})

    def _ensure_new(self, conn, table, oid):
        if self._get(conn, table, oid) is not None:
            raise Rejected("id_exists", "Объект с таким идентификатором уже существует.", {"id": oid})

    def _update_versioned(self, ctx, table, o, values: dict):
        n = ctx.conn.execute(table.update().where(table.c.id == o["id"], table.c.version == o["version"])
                             .values(version=o["version"] + 1, **values)).rowcount
        if n != 1:  # дополнительная защита поверх SERIALIZABLE
            raise Conflict("version_conflict", "Объект изменён параллельно. Повторите после обновления.", {"current": None})
        return self._get(ctx.conn, table, o["id"])

    # ------------------------------------------------------------ представления
    @staticmethod
    def v_customer(o):
        return {"id": o["id"], "name": o["name"], "inn": o["inn"], "note": o["note"], "version": o["version"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_tender(o):
        return {"id": o["id"], "customer_id": o["customer_id"], "title": o["title"],
                "submission_deadline": o["submission_deadline"].isoformat() if o["submission_deadline"] else None,
                "owner_user_id": o["owner_user_id"], "status": o["status"], "status_reason": o["status_reason"],
                "version": o["version"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_tkp(o):
        return {"id": o["id"], "tender_id": o["tender_id"], "label": o["label"], "revision": o["revision"],
                "amount": money.to_json(o["amount"]), "currency": o["currency"], "note": o["note"], "status": o["status"],
                "sent_on": o["sent_on"].isoformat() if o["sent_on"] else None, "sent_channel": o["sent_channel"],
                "version": o["version"], "last_edited_by": o["last_edited_by"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_project(o):
        return {"id": o["id"], "code": o["code"], "title": o["title"], "tender_id": o["tender_id"], "status": o["status"],
                "version": o["version"], "updated_at": iso(o["updated_at"])}

    @staticmethod
    def v_kit(o):
        return {"id": o["id"], "project_id": o["project_id"], "code": o["code"], "title": o["title"], "version": o["version"],
                "updated_at": iso(o["updated_at"])}

    # ------------------------------------------------------------ заказчики
    @staticmethod
    def _inn(p):
        inn = opt_str(p, "inn", 12)
        if inn is not None and not _INN_RE.match(inn):
            raise Rejected("invalid_inn", "ИНН должен содержать 10 или 12 цифр (контрольная сумма пока не проверяется).")
        return inn

    def _customer_create(self, ctx, p, ev):
        only_keys(p, {"id", "name", "inn", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "customer.create", ctx.now)
        cid = req_uuid(p, "id")
        name = req_str(p, "name")
        inn = self._inn(p)
        self._ensure_new(ctx.conn, customers, cid)
        ctx.conn.execute(customers.insert().values(id=cid, name=name, inn=inn, note=opt_str(p, "note"), version=1,
                                                   created_by=ctx.user_id, created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("customer", cid, auth, None, 1, {"name": name})
        return {"customer": self.v_customer(self._get(ctx.conn, customers, cid))}

    def _customer_update(self, ctx, p, ev):
        only_keys(p, {"id", "name", "inn", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "customer.update", ctx.now)
        o = self._require(ctx.conn, customers, req_uuid(p, "id"), "Заказчик")
        self._check_version(o, ev, self.v_customer)
        ch: dict[str, Any] = {}
        if "name" in p:
            ch["name"] = req_str(p, "name")
        if "inn" in p:
            ch["inn"] = self._inn(p)
        if "note" in p:
            ch["note"] = opt_str(p, "note")
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in ch}
        n = self._update_versioned(ctx, customers, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("customer", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"customer": self.v_customer(n)}

    # ------------------------------------------------------------ тендеры
    def _tender_create(self, ctx, p, ev):
        only_keys(p, {"id", "customer_id", "title", "submission_deadline", "owner_user_id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tender.create", ctx.now)
        tid = req_uuid(p, "id")
        self._require(ctx.conn, customers, req_uuid(p, "customer_id"), "Заказчик")
        owner = p.get("owner_user_id") or ctx.user_id  # A-11
        if not isinstance(owner, str) or not self._get(ctx.conn, users, owner):
            raise Rejected("owner_not_found", "Ответственный не найден.")
        title = req_str(p, "title")
        self._ensure_new(ctx.conn, tenders, tid)
        ctx.conn.execute(tenders.insert().values(id=tid, customer_id=p["customer_id"], title=title,
                                                 submission_deadline=opt_date(p, "submission_deadline"), owner_user_id=owner,
                                                 status=self.wf["tender"]["initial"], version=1, created_by=ctx.user_id,
                                                 created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("tender", tid, auth, None, 1, {"title": title, "customer_id": p["customer_id"]})
        return {"tender": self.v_tender(self._get(ctx.conn, tenders, tid))}

    def _tender_update(self, ctx, p, ev):
        only_keys(p, {"id", "title", "submission_deadline", "owner_user_id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tender.update", ctx.now)
        o = self._require(ctx.conn, tenders, req_uuid(p, "id"), "Тендер")
        self._check_version(o, ev, self.v_tender)
        if o["status"] in self.wf["tender"]["terminal"]:
            raise Rejected("tender_closed", "Тендер в конечном статусе — изменение запрещено.")
        ch: dict[str, Any] = {}
        if "title" in p:
            ch["title"] = req_str(p, "title")
        if "submission_deadline" in p:
            ch["submission_deadline"] = opt_date(p, "submission_deadline")
        if "owner_user_id" in p:
            if not isinstance(p["owner_user_id"], str) or not self._get(ctx.conn, users, p["owner_user_id"]):
                raise Rejected("owner_not_found", "Ответственный не найден.")
            ch["owner_user_id"] = p["owner_user_id"]
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in ch}
        n = self._update_versioned(ctx, tenders, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("tender", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"tender": self.v_tender(n)}

    def _tender_transition(self, ctx, p, ev):
        only_keys(p, {"id", "to", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tender.transition", ctx.now)
        o = self._require(ctx.conn, tenders, req_uuid(p, "id"), "Тендер")
        self._check_version(o, ev, self.v_tender)
        to = p.get("to")
        allowed = self.wf["tender"]["transitions"].get(o["status"], [])
        if to not in allowed:
            raise Rejected("transition_not_allowed", f"Переход «{o['status']}» → «{to}» не разрешён. Допустимо: {', '.join(allowed) or 'нет'}.",
                           {"from": o["status"], "to": to, "allowed": allowed})
        reason = opt_str(p, "reason", 2000)
        if to in self.wf["tender"]["reason_required_for"] and not reason:
            raise Rejected("reason_required", "Для этого перехода укажите причину.")
        n = self._update_versioned(ctx, tenders, o, {"status": to, "status_reason": reason, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("tender", o["id"], auth, o["version"], n["version"], {"from": o["status"], "to": to, "reason": reason})
        # Победа в тендере НЕ создаёт проект: проект — только командой project.start_rkd.
        return {"tender": self.v_tender(n)}

    # ------------------------------------------------------------ ТКП
    @staticmethod
    def _amount(p: dict) -> tuple[Optional[Decimal], Optional[str]]:
        try:
            amount = money.parse_optional(p.get("amount"))
        except money.MoneyError as e:
            raise Rejected(f"amount_{e.code}", e.message_ru, {"field": "amount"})
        cur = p.get("currency")
        if cur is not None and (not isinstance(cur, str) or not _CUR_RE.match(cur)):
            raise Rejected("invalid_currency", "Валюта — трёхбуквенный код ISO 4217. Значение по умолчанию не подставляется.")
        if amount is not None and cur is None:
            raise Rejected("currency_required", "Для суммы укажите валюту — значение по умолчанию не подставляется.")
        return amount, cur

    @staticmethod
    def _tkp_hash(o: dict) -> str:
        return hashlib.sha256(canon({"id": o["id"], "tender_id": o["tender_id"], "label": o["label"], "revision": o["revision"],
                                     "amount": money.to_json(o["amount"]), "currency": o["currency"], "note": o["note"]}).encode()).hexdigest()

    def _tkp_create(self, ctx, p, ev):
        only_keys(p, {"id", "tender_id", "label", "amount", "currency", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.create", ctx.now)
        kid = req_uuid(p, "id")
        t = self._require(ctx.conn, tenders, req_uuid(p, "tender_id"), "Тендер")
        if t["status"] in ("lost", "cancelled"):
            raise Rejected("tender_closed", "Тендер закрыт (проигран или отменён) — новое ТКП не создаётся.")
        label = req_str(p, "label", 64)
        amount, cur = self._amount(p)
        self._ensure_new(ctx.conn, tkp, kid)
        ctx.conn.execute(tkp.insert().values(id=kid, tender_id=t["id"], label=label, revision=1, amount=amount, currency=cur,
                                             note=opt_str(p, "note"), status="draft", version=1, created_by=ctx.user_id,
                                             created_at=ctx.now, last_edited_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("tkp", kid, auth, None, 1, {"label": label, "amount": money.to_json(amount), "currency": cur})
        return {"tkp": self.v_tkp(self._get(ctx.conn, tkp, kid))}

    def _tkp_update(self, ctx, p, ev):
        only_keys(p, {"id", "label", "amount", "currency", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.update", ctx.now)
        o = self._require(ctx.conn, tkp, req_uuid(p, "id"), "ТКП")
        self._check_version(o, ev, self.v_tkp)
        if o["status"] != "draft":
            raise Rejected("tkp_not_draft", "Изменять можно только черновик ТКП. Чтобы изменить согласованное — сначала явно верните его в черновик.")
        ch: dict[str, Any] = {}
        if "label" in p:
            ch["label"] = req_str(p, "label", 64)
        if "amount" in p or "currency" in p:
            ch["amount"], ch["currency"] = self._amount({"amount": p.get("amount", money.to_json(o["amount"])),
                                                         "currency": p.get("currency", o["currency"])})
        if "note" in p:
            ch["note"] = opt_str(p, "note")
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: (money.to_json(o[k]) if k == "amount" else o[k]) for k in ch}
        n = self._update_versioned(ctx, tkp, o, {**ch, "revision": o["revision"] + 1, "last_edited_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("tkp", o["id"], auth, o["version"], n["version"],
                {"before": before, "after": {k: (money.to_json(v) if k == "amount" else v) for k, v in ch.items()}, "revision": n["revision"]})
        return {"tkp": self.v_tkp(n)}

    def _tkp_approve(self, ctx, p, ev):
        only_keys(p, {"id", "comment"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.approve", ctx.now)
        o = self._require(ctx.conn, tkp, req_uuid(p, "id"), "ТКП")
        self._check_version(o, ev, self.v_tkp)
        if o["status"] != "draft":
            raise Rejected("tkp_not_draft", "Согласовать можно только черновик.")
        if o["last_edited_by"] == ctx.user_id:
            raise Denied("self_approval", "Автор последней правки не может согласовать ТКП сам (разделение обязанностей).")
        h = self._tkp_hash(o)
        ctx.conn.execute(tkp_approvals.insert().values(tkp_id=o["id"], tkp_revision=o["revision"], content_hash=h,
                                                       approver_id=ctx.user_id, grant_id=auth.ref_id, approved_at=ctx.now))
        n = self._update_versioned(ctx, tkp, o, {"status": "approved", "updated_at": ctx.now})
        ctx.log("tkp", o["id"], auth, o["version"], n["version"], {"approved_revision": o["revision"], "content_hash": h,
                                                                  "comment": opt_str(p, "comment", 2000)})
        return {"tkp": self.v_tkp(n)}

    def _tkp_reopen(self, ctx, p, ev):
        only_keys(p, {"id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.reopen", ctx.now)
        o = self._require(ctx.conn, tkp, req_uuid(p, "id"), "ТКП")
        self._check_version(o, ev, self.v_tkp)
        if o["status"] != "approved":
            raise Rejected("tkp_not_approved", "Вернуть в черновик можно только согласованное ТКП.")
        reason = req_str(p, "reason", 500)
        ctx.conn.execute(tkp_approvals.update().where(tkp_approvals.c.tkp_id == o["id"], tkp_approvals.c.superseded_at.is_(None))
                         .values(superseded_at=ctx.now, superseded_reason="reopen: " + reason))
        n = self._update_versioned(ctx, tkp, o, {"status": "draft", "updated_at": ctx.now})
        ctx.log("tkp", o["id"], auth, o["version"], n["version"], {"reason": reason, "approval": "superseded"})
        return {"tkp": self.v_tkp(n)}

    def _tkp_record_sent(self, ctx, p, ev):
        only_keys(p, {"id", "sent_on", "channel", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.record_sent", ctx.now)
        o = self._require(ctx.conn, tkp, req_uuid(p, "id"), "ТКП")
        self._check_version(o, ev, self.v_tkp)
        if o["status"] != "approved":
            raise Rejected("tkp_not_approved", "Зарегистрировать отправку можно только для согласованного ТКП.")
        ap = ctx.conn.execute(sa.select(tkp_approvals).where(tkp_approvals.c.tkp_id == o["id"], tkp_approvals.c.superseded_at.is_(None))
                              .order_by(tkp_approvals.c.id.desc()).limit(1)).first()
        roles = self.policy.roles_for("tkp.approve", "global_role")
        if (not ap or ap.tkp_revision != o["revision"] or ap.content_hash != self._tkp_hash(o)
                or not grant_is_active(ctx.conn, ap.grant_id, ap.approver_id, roles, ctx.now)):
            raise Rejected("approval_invalid",
                           "Согласование недействительно: изменилось содержимое, или полномочие согласующего отозвано/истекло. "
                           "Повторная выдача роли не восстанавливает прежнее согласование — нужно новое.",
                           {"approval_id": ap.id if ap else None})
        sent_on = opt_date(p, "sent_on")
        if not sent_on:
            raise Rejected("field_required", "Укажите дату фактической отправки.")
        if sent_on > ctx.now.date():
            raise Rejected("date_in_future", "Дата отправки не может быть в будущем.")
        channel = opt_str(p, "channel", 100)
        n = self._update_versioned(ctx, tkp, o, {"status": "sent_recorded", "sent_on": sent_on, "sent_channel": channel, "updated_at": ctx.now})
        ctx.log("tkp", o["id"], auth, o["version"], n["version"],
                {"sent_on": sent_on.isoformat(), "channel": channel, "approval_id": ap.id,
                 "meaning": "Зарегистрирован факт отправки со слов пользователя; доставка и получение не доказаны."})
        return {"tkp": self.v_tkp(n)}

    # ------------------------------------------------------------ проекты
    def _project_start_rkd(self, ctx, p, ev):
        only_keys(p, {"id", "tender_id", "code", "title"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "project.start_rkd", ctx.now)
        pid = req_uuid(p, "id")
        t = self._require(ctx.conn, tenders, req_uuid(p, "tender_id"), "Тендер")
        allowed = self.wf["project"]["start_rkd_allowed_from_tender_status"]
        if t["status"] not in allowed:
            raise Rejected("tender_status_not_allowed", f"Начать РКД можно из статуса тендера: {', '.join(allowed)}. Сейчас: {t['status']}.",
                           {"tender_status": t["status"]})
        if self.wf["project"]["one_project_per_tender"]:
            ex = ctx.conn.execute(sa.select(projects.c.id, projects.c.code).where(projects.c.tender_id == t["id"])).first()
            if ex:
                raise Rejected("project_exists_for_tender", f"По этому тендеру уже начат проект {ex.code}.", {"project_id": ex.id})
        code = req_str(p, "code", 64)
        if ctx.conn.execute(sa.select(projects.c.id).where(projects.c.code == code)).first():
            raise Rejected("project_code_taken", "Код проекта уже используется (уникальность в пределах компании).")
        title = req_str(p, "title")
        self._ensure_new(ctx.conn, projects, pid)
        ctx.conn.execute(projects.insert().values(id=pid, code=code, title=title, tender_id=t["id"], tender_version_at_start=t["version"],
                                                  status=self.wf["project"]["initial"], version=1, created_by=ctx.user_id,
                                                  created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("project", pid, auth, None, 1, {"tender_id": t["id"], "tender_version": t["version"], "code": code,
                                               "note": "ГИП не назначен — назначает администратор."})
        return {"project": self.v_project(self._get(ctx.conn, projects, pid)), "gip_assigned": False}

    def _valid_until(self, ctx, p) -> Optional[datetime]:
        if p.get("valid_until") is None:
            return None
        vu = parse_iso_utc(p["valid_until"], "valid_until")
        if vu <= ctx.now:
            raise Rejected("bad_valid_until", "Срок должен быть в будущем.")
        return vu

    def _project_assign(self, ctx, p, ev):
        only_keys(p, {"project_id", "user_id", "project_role", "valid_until", "reason"})
        pr = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")
        role = p.get("project_role")
        if role not in self.contracts.permissions["project_roles"]:
            raise Rejected("bad_project_role", "Роль в проекте: gip, rp или participant.")
        auth = self.policy.check(ctx.conn, ctx.user_id, f"project.assign_{role}", ctx.now, project_id=pr["id"])
        target = self._get(ctx.conn, users, p.get("user_id")) if isinstance(p.get("user_id"), str) else None
        if not target or not target["active"]:
            raise Rejected("user_not_found", "Пользователь не найден или отключён.")
        vu = self._valid_until(ctx, p)
        act = sa.and_(project_assignments.c.project_id == pr["id"], project_assignments.c.revoked_at.is_(None),
                      sa.or_(project_assignments.c.valid_until.is_(None), project_assignments.c.valid_until > ctx.now))
        if role == "gip" and ctx.conn.execute(sa.select(project_assignments.c.id).where(act, project_assignments.c.project_role == "gip")).first():
            raise Rejected("gip_already_assigned", "ГИП уже назначен. Сначала явно отзовите текущее назначение.")
        if ctx.conn.execute(sa.select(project_assignments.c.id).where(act, project_assignments.c.user_id == target["id"],
                                                                      project_assignments.c.project_role == role)).first():
            raise Rejected("already_assigned", "Такое действующее назначение уже есть.")
        aid = ctx.conn.execute(project_assignments.insert().values(project_id=pr["id"], user_id=target["id"], project_role=role,
                                                                   valid_from=ctx.now, valid_until=vu, assigned_by=ctx.user_id)).inserted_primary_key[0]
        ctx.log("assignment", aid, auth, None, 1, {"project_id": pr["id"], "user_id": target["id"], "project_role": role,
                                                  "valid_until": iso(vu), "reason": opt_str(p, "reason", 500)})
        return {"assignment_id": aid}

    def _project_revoke_assignment(self, ctx, p, ev):
        only_keys(p, {"assignment_id", "reason"})
        aid = opt_int(p, "assignment_id")
        row = self._get(ctx.conn, project_assignments, aid) if aid is not None else None
        if not row:
            raise Rejected("not_found", "Назначение не найдено.")
        auth = self.policy.check(ctx.conn, ctx.user_id, f"project.assign_{row['project_role']}", ctx.now, project_id=row["project_id"])
        if row["revoked_at"]:
            raise Rejected("already_revoked", "Назначение уже отозвано.")
        reason = req_str(p, "reason", 500)
        ctx.conn.execute(project_assignments.update().where(project_assignments.c.id == aid).values(revoked_at=ctx.now, revoked_reason=reason))
        ctx.log("assignment", aid, auth, 1, 2, {"revoked": True, "reason": reason, "user_id": row["user_id"], "project_role": row["project_role"]})
        return {"assignment_id": aid, "revoked": True}

    # ------------------------------------------------------------ комплекты
    def _kit_create(self, ctx, p, ev):
        only_keys(p, {"id", "project_id", "code", "title"})
        pr = self._require(ctx.conn, projects, req_uuid(p, "project_id"), "Проект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "kit.create", ctx.now, project_id=pr["id"])
        if pr["status"] != "rkd_in_progress":
            raise Rejected("project_state", "Комплекты добавляются только в проект в статусе «Разработка РКД».")
        kid = req_uuid(p, "id")
        code = req_str(p, "code", 64)
        if ctx.conn.execute(sa.select(kits.c.id).where(kits.c.project_id == pr["id"], kits.c.code == code)).first():
            raise Rejected("kit_code_taken", "Комплект с таким кодом уже есть в этом проекте.")
        title = req_str(p, "title")
        self._ensure_new(ctx.conn, kits, kid)
        ctx.conn.execute(kits.insert().values(id=kid, project_id=pr["id"], code=code, title=title, version=1, created_by=ctx.user_id,
                                              created_at=ctx.now, updated_by=ctx.user_id, updated_at=ctx.now))
        ctx.log("kit", kid, auth, None, 1, {"project_id": pr["id"], "code": code})
        return {"kit": self.v_kit(self._get(ctx.conn, kits, kid))}

    def _kit_update(self, ctx, p, ev):
        only_keys(p, {"id", "code", "title"})
        o = self._require(ctx.conn, kits, req_uuid(p, "id"), "Комплект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "kit.update", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, self.v_kit)
        ch: dict[str, Any] = {}
        if "code" in p:
            ch["code"] = req_str(p, "code", 64)
            if ch["code"] != o["code"] and ctx.conn.execute(sa.select(kits.c.id).where(kits.c.project_id == o["project_id"], kits.c.code == ch["code"])).first():
                raise Rejected("kit_code_taken", "Комплект с таким кодом уже есть в этом проекте.")
        if "title" in p:
            ch["title"] = req_str(p, "title")
        if not ch:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in ch}
        n = self._update_versioned(ctx, kits, o, {**ch, "updated_by": ctx.user_id, "updated_at": ctx.now})
        ctx.log("kit", o["id"], auth, o["version"], n["version"], {"before": before, "after": ch})
        return {"kit": self.v_kit(n)}

    # ------------------------------------------------------------ учётные записи
    def _revoke_sessions(self, conn, user_id, now, reason, except_hash=None) -> int:
        q = sessions.update().where(sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None))
        if except_hash:
            q = q.where(sessions.c.token_hash != except_hash)
        return conn.execute(q.values(revoked_at=now, revoked_reason=reason)).rowcount

    def _check_password(self, pwd, login):
        problems = security.password_problems(pwd, login, self.sec)
        if problems:
            raise Rejected("weak_password", " ".join(problems))

    def _account_change_password(self, ctx, p, ev):
        only_keys(p, {"current_password", "new_password"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "account.change_password", ctx.now)
        if not self.pwd_rate.allow(ctx.user_id, ctx.now.timestamp()):
            raise Rejected("rate_limited", "Слишком много попыток смены пароля. Подождите минуту.")
        ok, _ = security.verify_password(ctx.user["pwd_hash"], p.get("current_password") or "")
        if not ok:
            raise Denied("bad_current_password", "Текущий пароль указан неверно.")
        new = p.get("new_password")
        self._check_password(new, ctx.user["login"])
        if security.verify_password(ctx.user["pwd_hash"], new)[0]:
            raise Rejected("same_password", "Новый пароль совпадает с текущим.")
        ctx.conn.execute(users.update().where(users.c.id == ctx.user_id).values(
            pwd_hash=security.hash_password(new), must_change_password=False, password_changed_at=ctx.now))
        n = self._revoke_sessions(ctx.conn, ctx.user_id, ctx.now, "password_changed", except_hash=ctx.session_hash)
        ctx.log("user", ctx.user_id, auth, None, None, {"password_changed": True, "other_sessions_revoked": n})
        return {"password_changed": True, "other_sessions_revoked": n}

    def _admin_create_user(self, ctx, p, ev):
        only_keys(p, {"login", "display_name", "initial_password"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.create_user", ctx.now)
        login = req_str(p, "login", 64)
        if not _LOGIN_RE.match(login):
            raise Rejected("bad_login", "Логин: 3–64 символа, латиница, цифры, точка, дефис, подчёркивание.")
        self._check_password(p.get("initial_password"), login)
        if self._get(ctx.conn, users, login, "login"):
            raise Rejected("login_taken", "Логин занят.")
        uid = str(uuid.uuid4())
        ctx.conn.execute(users.insert().values(id=uid, login=login, display_name=req_str(p, "display_name"),
                                               pwd_hash=security.hash_password(p["initial_password"]), must_change_password=True,
                                               password_changed_at=ctx.now, failed_attempts=0, active=True, created_at=ctx.now))
        ctx.log("user", uid, auth, None, 1, {"login": login, "must_change_password": True})
        return {"user_id": uid}

    def _admin_reset_password(self, ctx, p, ev):
        only_keys(p, {"user_id", "temporary_password", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.reset_password", ctx.now)
        u = self._get(ctx.conn, users, p.get("user_id")) if isinstance(p.get("user_id"), str) else None
        if not u:
            raise Rejected("user_not_found", "Пользователь не найден.")
        self._check_password(p.get("temporary_password"), u["login"])
        reason = req_str(p, "reason", 500)
        ctx.conn.execute(users.update().where(users.c.id == u["id"]).values(
            pwd_hash=security.hash_password(p["temporary_password"]), must_change_password=True, password_changed_at=ctx.now,
            failed_attempts=0, locked_until=None))
        n = self._revoke_sessions(ctx.conn, u["id"], ctx.now, "password_reset")
        ctx.log("user", u["id"], auth, None, None, {"password_reset": True, "sessions_revoked": n, "reason": reason})
        return {"user_id": u["id"], "sessions_revoked": n}

    def _active_admins(self, conn, now) -> set[str]:
        rows = conn.execute(sa.select(role_grants.c.user_id).join(users, users.c.id == role_grants.c.user_id).where(
            role_grants.c.role == "admin", role_grants.c.revoked_at.is_(None), role_grants.c.valid_from <= now,
            sa.or_(role_grants.c.valid_until.is_(None), role_grants.c.valid_until > now), users.c.active == sa.true()))
        return {r.user_id for r in rows}

    def _admin_set_user_active(self, ctx, p, ev):
        only_keys(p, {"user_id", "active", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.set_user_active", ctx.now)
        u = self._get(ctx.conn, users, p.get("user_id")) if isinstance(p.get("user_id"), str) else None
        if not u:
            raise Rejected("user_not_found", "Пользователь не найден.")
        active = p.get("active")
        if not isinstance(active, bool):
            raise Rejected("field_type", "Поле «active» — true или false.")
        reason = req_str(p, "reason", 500)
        if not active:
            if u["id"] == ctx.user_id:
                raise Rejected("cannot_disable_self", "Нельзя отключить собственную учётную запись.")
            if self._active_admins(ctx.conn, ctx.now) == {u["id"]}:
                raise Rejected("last_admin", "Нельзя отключить последнего действующего администратора.")
        ctx.conn.execute(users.update().where(users.c.id == u["id"]).values(active=active, failed_attempts=0, locked_until=None))
        n = 0 if active else self._revoke_sessions(ctx.conn, u["id"], ctx.now, "user_disabled")
        ctx.log("user", u["id"], auth, None, None, {"active": active, "sessions_revoked": n, "reason": reason})
        return {"user_id": u["id"], "active": active}

    def _admin_grant_role(self, ctx, p, ev):
        only_keys(p, {"user_id", "role", "valid_until", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.grant_role", ctx.now)
        if p.get("role") not in self.contracts.permissions["roles"]:
            raise Rejected("bad_role", "Неизвестная роль доступа.")
        if not isinstance(p.get("user_id"), str) or not self._get(ctx.conn, users, p["user_id"]):
            raise Rejected("user_not_found", "Пользователь не найден.")
        vu = self._valid_until(ctx, p)
        reason = req_str(p, "reason", 500)
        gid = ctx.conn.execute(role_grants.insert().values(user_id=p["user_id"], role=p["role"], scope_type="company", valid_from=ctx.now,
                                                           valid_until=vu, granted_by=ctx.user_id, reason=reason)).inserted_primary_key[0]
        ctx.log("role_grant", gid, auth, None, 1, {"user_id": p["user_id"], "role": p["role"], "valid_until": iso(vu), "reason": reason})
        return {"grant_id": gid}

    def _admin_revoke_role(self, ctx, p, ev):
        only_keys(p, {"grant_id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.revoke_role", ctx.now)
        gid = opt_int(p, "grant_id")
        row = self._get(ctx.conn, role_grants, gid) if gid is not None else None
        if not row:
            raise Rejected("not_found", "Выдача роли не найдена.")
        if row["revoked_at"]:
            raise Rejected("already_revoked", "Роль уже отозвана.")
        if row["role"] == "admin":
            others = ctx.conn.execute(sa.select(sa.func.count()).select_from(role_grants).join(users, users.c.id == role_grants.c.user_id).where(
                role_grants.c.role == "admin", role_grants.c.id != gid, role_grants.c.revoked_at.is_(None),
                sa.or_(role_grants.c.valid_until.is_(None), role_grants.c.valid_until > ctx.now), users.c.active == sa.true())).scalar()
            if not others:
                raise Rejected("last_admin", "Нельзя отозвать роль у последнего действующего администратора.")
        reason = req_str(p, "reason", 500)
        ctx.conn.execute(role_grants.update().where(role_grants.c.id == gid).values(revoked_at=ctx.now))
        ctx.log("role_grant", gid, auth, 1, 2, {"revoked": True, "user_id": row["user_id"], "role": row["role"], "reason": reason})
        return {"grant_id": gid, "revoked": True}

    def _admin_revoke_sessions(self, ctx, p, ev):
        only_keys(p, {"user_id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.revoke_sessions", ctx.now)
        u = self._get(ctx.conn, users, p.get("user_id")) if isinstance(p.get("user_id"), str) else None
        if not u:
            raise Rejected("user_not_found", "Пользователь не найден.")
        reason = req_str(p, "reason", 500)
        n = self._revoke_sessions(ctx.conn, u["id"], ctx.now, "admin: " + reason)
        ctx.log("session", u["id"], auth, None, None, {"revoked_sessions": n, "reason": reason})
        return {"revoked_sessions": n}

    # ------------------------------------------------------------ запросы
    def _query(self, req: dict) -> dict:
        def tx(conn):
            now = self.now()
            try:
                user, _, _ = self._auth(conn, req.get("token"), now)
            except Unauthenticated as e:
                return {"status": e.status, "code": e.code, "message_ru": e.message_ru}
            q = req.get("query")
            if q == "pull":
                return {"status": "accepted", "result": self._pull(conn, user, now)}
            if q == "history":
                return self._history(conn, user["id"], now, req.get("object_type"), req.get("object_id"))
            if q == "users":
                if not self.policy.can(conn, user["id"], "admin.list_users", now):
                    return {"status": "denied", "code": "no_authority", "message_ru": "Список учётных записей доступен администратору."}
                return {"status": "accepted", "result": self._users(conn, now)}
            return {"status": "rejected", "code": "unknown_query", "message_ru": "Неизвестный запрос."}
        return self._tx(tx)

    def _rows(self, conn, stmt) -> list[dict]:
        return [dict(r._mapping) for r in conn.execute(stmt)]

    def _pull(self, conn, user, now) -> dict:
        uid = user["id"]
        can = lambda action, pid=None: self.policy.can(conn, uid, action, now, pid)  # noqa: E731
        out: dict[str, Any] = {"server_time": iso(now), "customers": [], "tenders": [], "tkp": [], "projects": [], "kits": [], "assignments": [], "people": []}
        if can("customer.read"):
            out["customers"] = [self.v_customer(o) for o in self._rows(conn, sa.select(customers).order_by(customers.c.name))]
        if can("tender.read"):
            out["tenders"] = [self.v_tender(o) for o in self._rows(conn, sa.select(tenders).order_by(tenders.c.created_at))]
        if can("tkp.read"):
            out["tkp"] = [self.v_tkp(o) for o in self._rows(conn, sa.select(tkp).order_by(tkp.c.created_at))]
        readable: list[str] = []
        for pr in self._rows(conn, sa.select(projects).order_by(projects.c.code)):
            if can("project.read", pr["id"]):
                readable.append(pr["id"])
                out["projects"].append(self.v_project(pr))
                out["kits"].extend(self.v_kit(k) for k in self._rows(conn, sa.select(kits).where(kits.c.project_id == pr["id"]).order_by(kits.c.code)))
                out["assignments"].extend(
                    {"id": a["id"], "project_id": a["project_id"], "user_id": a["user_id"], "project_role": a["project_role"],
                     "valid_until": iso(a["valid_until"])}
                    for a in self._rows(conn, sa.select(project_assignments).where(
                        project_assignments.c.project_id == pr["id"], project_assignments.c.revoked_at.is_(None),
                        sa.or_(project_assignments.c.valid_until.is_(None), project_assignments.c.valid_until > now))
                        .order_by(project_assignments.c.id)))
        # Задачи, документы и метаданные версий (без содержимого файлов) — только по читаемым проектам.
        out.update(self._pull_work(conn, readable))
        if out["tenders"] or out["projects"]:
            # Минимальный справочник людей (имя для отображения ответственного) — без логинов и контактов.
            out["people"] = [{"id": r["id"], "display_name": r["display_name"]} for r in
                             self._rows(conn, sa.select(users.c.id, users.c.display_name).where(users.c.active == sa.true()))]
        out["me"] = {"user_id": uid, "display_name": user["display_name"], "login": user["login"],
                     "roles": [r for _, r in active_grants(conn, uid, now)],
                     "note_ru": "Роли в кэше клиента служат только подсказкой интерфейса; решение принимает сервер в момент эффекта."}
        return out

    def _users(self, conn, now) -> list[dict]:
        out = []
        for u in self._rows(conn, sa.select(users).order_by(users.c.login)):
            grants = self._rows(conn, sa.select(role_grants).where(role_grants.c.user_id == u["id"]).order_by(role_grants.c.id))
            out.append({"id": u["id"], "login": u["login"], "display_name": u["display_name"], "active": bool(u["active"]),
                        "must_change_password": bool(u["must_change_password"]),
                        "locked_until": iso(u["locked_until"]) if u["locked_until"] and u["locked_until"] > now else None,
                        "grants": [{"id": g["id"], "role": g["role"], "valid_until": iso(g["valid_until"]), "revoked_at": iso(g["revoked_at"]),
                                    "active": g["revoked_at"] is None and (g["valid_until"] is None or g["valid_until"] > now)} for g in grants]})
        return out

    def _history(self, conn, uid, now, otype, oid) -> dict:
        action = {"customer": "customer.read", "tender": "tender.read", "tkp": "tkp.read", "project": "project.read",
                  "kit": "kit.read", "task": "task.read", "document": "document.read"}.get(otype)
        if not action or not isinstance(oid, str):
            return {"status": "rejected", "code": "bad_history_request", "message_ru": "Укажите тип и id объекта."}
        pid = oid if otype == "project" else None
        if otype in ("kit", "task", "document"):
            # Объекты внутри проекта: право чтения определяется ролью в их проекте.
            table = {"kit": kits, "task": db.tasks, "document": db.documents}[otype]
            k = self._get(conn, table, oid)
            pid = k["project_id"] if k else None
        if not self.policy.can(conn, uid, action, now, pid):
            return {"status": "denied", "code": "no_authority", "message_ru": "Нет права на просмотр истории этого объекта."}
        names = {r["id"]: r["display_name"] for r in self._rows(conn, sa.select(users.c.id, users.c.display_name))}
        rows = self._rows(conn, sa.select(audit_log).where(audit_log.c.object_type == otype, audit_log.c.object_id == oid).order_by(audit_log.c.id))
        return {"status": "accepted", "result": [
            {"at": iso(r["at"]), "actor_user_id": r["actor_user_id"], "actor": names.get(r["actor_user_id"]), "operation_id": r["operation_id"],
             "command": r["command"], "authority": r["authority"], "version_before": r["version_before"], "version_after": r["version_after"],
             "result": r["result"], "details": json.loads(r["details_json"])} for r in rows]}

    def about(self) -> dict:
        return {"product": "ВЕКТОР", "product_en": "VECTOR", "version": __version__, "api": "v1", "schema_version": db.SCHEMA_VERSION}
