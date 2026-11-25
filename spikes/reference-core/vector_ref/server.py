"""Серверный application service эталонного ядра (срез 1).

Свойства, которые проверяют тесты:
* единственная граница записи — handle()/handle_json(); клиент не видит БД;
* аутентификация по токену сеанса; роль из запроса игнорируется;
* авторизация, проверка состояния/версии, эффект, журнал и запись результата операции —
  в ОДНОЙ транзакции BEGIN IMMEDIATE (атомарно; при сбое — откат всего);
* идемпотентность по operation_id: повтор возвращает сохранённый результат,
  тот же id с другим содержимым отклоняется;
* оптимистичная конкуренция: expected_version, конфликт без молчаливой перезаписи;
* деньги — Decimal-строки, хранение в десятитысячных, None ≠ 0.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from . import money
from .authz import Authority, Policy, active_grants, grant_is_active
from .contracts import Contracts
from .errors import CommandOutcome, Conflict, Denied, Rejected, Unauthenticated
from .schema import SERVER_SCHEMA

SESSION_TTL = timedelta(hours=12)  # параметр эталона, не политика продукта (см. открытые вопросы)
PBKDF2_ITER = 200_000
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CUR_RE = re.compile(r"^[A-Z]{3}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INN_RE = re.compile(r"^(\d{10}|\d{12})$")


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def _hash_pwd(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITER)


def _canon(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Ctx:
    def __init__(self, conn, user_id, device_id, operation_id, now, command):
        self.conn: sqlite3.Connection = conn
        self.user_id: str = user_id
        self.device_id: str = device_id
        self.operation_id: str = operation_id
        self.now: str = now
        self.command: str = command
        self.audit: list[dict] = []

    def log(self, object_type, object_id, authority: Optional[Authority], before, after, details=None):
        self.audit.append(dict(object_type=object_type, object_id=object_id,
                               authority=authority.describe() if authority else None,
                               version_before=before, version_after=after, details=details or {}))


# ---------------------------------------------------------------- валидация полей
def req_str(p: dict, key: str, max_len: int = 200) -> str:
    v = p.get(key)
    if not isinstance(v, str) or not v.strip():
        raise Rejected("field_required", f"Поле «{key}» обязательно.", {"field": key})
    if len(v) > max_len:
        raise Rejected("field_too_long", f"Поле «{key}» длиннее {max_len} символов.", {"field": key})
    return v.strip()


def opt_str(p: dict, key: str, max_len: int = 2000) -> Optional[str]:
    v = p.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise Rejected("field_type", f"Поле «{key}» должно быть строкой.", {"field": key})
    if len(v) > max_len:
        raise Rejected("field_too_long", f"Поле «{key}» длиннее {max_len} символов.", {"field": key})
    v = v.strip()
    return v or None


def req_uuid(p: dict, key: str) -> str:
    v = p.get(key)
    if not isinstance(v, str) or not _UUID_RE.match(v):
        raise Rejected("invalid_id", f"Поле «{key}» должно быть UUID в нижнем регистре.", {"field": key})
    return v


def opt_date(p: dict, key: str) -> Optional[str]:
    v = p.get(key)
    if v is None:
        return None
    if not isinstance(v, str) or not _DATE_RE.match(v):
        raise Rejected("invalid_date", f"Поле «{key}»: ожидается дата ГГГГ-ММ-ДД.", {"field": key})
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise Rejected("invalid_date", f"Поле «{key}»: несуществующая дата.", {"field": key})
    return v


def only_keys(p: dict, allowed: set[str]) -> None:
    extra = set(p) - allowed
    if extra:
        raise Rejected("unknown_fields", "Неизвестные поля: " + ", ".join(sorted(extra)), {"fields": sorted(extra)})


# Команды вне области эталонного ядра (реализованы и проверены продуктом, src/vector/server):
#   учётные записи (каталог v2), задачи/документы с файлами (каталог v3, tests/server/test_tasks_documents.py),
#   расчёт комплекта и фиксация (каталог v4, tests/server/test_calc.py).
OUT_OF_SPIKE_SCOPE = frozenset({
    "account.change_password", "admin.reset_password", "admin.set_user_active",
    "task.create", "task.update", "task.transition",
    "document.create", "document.update", "document.add_version",
    "office_doc.create", "office_doc.set_template", "templates.render",
    "calc.save_draft", "calc.freeze",
    "warehouse.create", "warehouse.update", "item.create", "item.update",
    "stock.receive", "stock.issue", "stock.reverse", "stock.reserve", "stock.release", "need.generate", "need.match_line", "config.publish",
    "stock.adjust", "stock.adjust_sign", "stock.adjust_cancel",
    "proposal.propose", "proposal.withdraw", "proposal.decide",
    "stock.replen_rule_set", "stock.replen_rule_update", "stock.replen_confirm", "stock.replen_dismiss",
    "item_alias.create", "item_alias.update", "unit_conversion.create", "unit_conversion.update",

    # Этапы 2–7 продукта (D-20…D-76): склад/закупка, производство, качество, оргструктура, рабочее время,
    # сверхурочные по ТК РФ, сквозные процессы проекта (blueprint 52) — вне области спайка среза 1.
    "analog.decide", "analog.propose",
    "batch.create", "batch.transition", "batch.update",
    "calendar.day_set", "calendar.import",
    "checklist.archive", "checklist.create", "checklist.unarchive", "checklist.update",
    "claim.action_done", "claim.check", "claim.classify", "claim.plan", "claim.register", "claim.respond", "claim.set_due", "claim.start_analysis", "claim.submit_check",
    "comment.add", "comment.respond",
    "contract.create", "contract.update",
    "delegation.create", "delegation.revoke",
    "delivery_doc.create", "delivery_doc.update",
    "doc_template.create", "doc_template.set_active", "doc_template.update",
    "docprofile.activate", "docprofile.create", "docprofile.preview", "docprofile.update",
    "docrev.create", "docrev.set_issue", "docrev.transition",
    "eco.approve", "eco.create", "eco.implement", "eco.save", "eco.verify",
    "ecr.assess", "ecr.decide", "ecr.submit",
    "fat.add_finding", "fat.close_finding", "fat.create", "fat.finish",
    "hr.category_set",
    "instance.handover", "instance.register", "instance.replace", "instance.service",
    "norm.decide", "norm.propose",
    "notification.read",
    "opportunity.convert_to_tender", "opportunity.create", "opportunity.transition", "opportunity.update",
    "org.member_set", "org.unit_archive", "org.unit_create", "org.unit_restore", "org.unit_update",
    "overtime.decide", "overtime.period_close", "overtime.period_set", "overtime.request",
    "passport.create", "passport.export_draft", "passport.save",
    "pgroup.create", "pgroup.update",
    "pnr.create", "pnr.set_status",
    "po.approve", "po.cancel", "po.close", "po.create", "po.place", "po.receive", "po.return", "po.submit", "po.update", "po.withdraw",
    "project.transition",
    "report.create", "report.reopen", "report.review", "report.save", "report.submit",
    "risk.accept", "risk.resolve",
    "schedule.assign", "schedule.save",
    "stock.transfer", "stock.transfer_accept", "stock.transfer_cancel", "stock.transfer_lost",
    "supplier.create", "supplier.update",
    "tp.approve", "tp.create", "tp.update",
    "transmittal.create", "transmittal.register_receipt",
    "wo.assign", "wo.cancel", "wo.close", "wo.op_event", "wo.release", "wo.substitute",
    "workday.end_decide", "workday.end_request", "workday.mark", "workday.start_decide", "workday.start_request",
    "workstream.set_status", "workstream.update",
})


class Server:
    def __init__(self, db_path: str, contracts: Optional[Contracts] = None, clock=None):
        self.db_path = db_path
        self.contracts = contracts or Contracts.load()
        self.policy = Policy(self.contracts.permissions)
        self.wf = self.contracts.workflows
        self.clock = clock or SystemClock()
        self._fault_after_effect: Optional[str] = None  # для теста отката
        with self._connect() as conn:
            conn.executescript(SERVER_SCHEMA)
        self.handlers: dict[str, Callable[[Ctx, dict, Optional[int]], dict]] = {
            "customer.create": self._customer_create,
            "customer.update": self._customer_update,
            "tender.create": self._tender_create,
            "tender.update": self._tender_update,
            "tender.transition": self._tender_transition,
            "tkp.create": self._tkp_create,
            "tkp.update": self._tkp_update,
            "tkp.approve": self._tkp_approve,
            "tkp.reopen": self._tkp_reopen,
            "tkp.record_sent": self._tkp_record_sent,
            "project.start_rkd": self._project_start_rkd,
            "project.assign": self._project_assign,
            "project.revoke_assignment": self._project_revoke_assignment,
            "kit.create": self._kit_create,
            "kit.update": self._kit_update,
            "admin.create_user": self._admin_create_user,
            "admin.grant_role": self._admin_grant_role,
            "admin.revoke_role": self._admin_revoke_role,
            "admin.revoke_sessions": self._admin_revoke_sessions,
        }
        # Эталонное ядро — исполняемая спецификация среза 1 (бизнес-инварианты). Команды учётных записей добавлены
        # в каталог v2 продуктом (src/vector/server) и проверяются его тестами; здесь они ЯВНО вне области.
        # Любое иное расхождение каталога и обработчиков — по-прежнему отказ запуска (fail-closed).
        missing = (set(self.contracts.catalog["commands"]) - OUT_OF_SPIKE_SCOPE) ^ set(self.handlers)
        if missing:
            raise RuntimeError(f"Каталог команд и обработчики расходятся: {sorted(missing)}")

    # ------------------------------------------------------------ инфраструктура
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _now(self) -> str:
        return utc_iso(self.clock.now())

    def bootstrap_admin(self, login: str, password: str) -> str:
        """Однократная инициализация: только если пользователей ещё нет (см. F-10 в blueprint)."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                conn.execute("ROLLBACK")
                raise Rejected("already_bootstrapped", "Система уже инициализирована; повторный bootstrap запрещён.")
            now = self._now()
            uid = str(uuid.uuid4())
            salt = os.urandom(16)
            conn.execute("INSERT INTO users VALUES (?,?,?,?,?,1,?)", (uid, login, "Администратор", salt, _hash_pwd(password, salt), now))
            gid = conn.execute("INSERT INTO role_grants(user_id,role,scope_type,valid_from,granted_by,reason) VALUES (?,?,?,?,?,?)",
                               (uid, "admin", "company", now, None, "bootstrap")).lastrowid
            self._write_audit(conn, now, None, None, None, "system.bootstrap", "user", uid, f"bootstrap#grant:{gid}", None, 1, "accepted", {"login": login})
            conn.execute("COMMIT")
            return uid
        finally:
            conn.close()

    def _write_audit(self, conn, at, actor, device, op_id, command, otype, oid, authority, vb, va, result, details):
        conn.execute(
            "INSERT INTO audit_log(at,actor_user_id,device_id,operation_id,command,object_type,object_id,authority,version_before,version_after,result,details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (at, actor, device, op_id, command, otype, oid, authority, vb, va, result, _canon(details)))

    def handle_json(self, raw: str) -> str:
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            return _canon({"status": "rejected", "code": "bad_json", "message_ru": "Некорректный запрос."})
        return _canon(self.handle(req))

    def handle(self, req: dict) -> dict:
        t = req.get("type")
        if t == "login":
            return self._login(req)
        if t == "command":
            return self._command(req)
        if t == "query":
            return self._query(req)
        return {"status": "rejected", "code": "bad_type", "message_ru": "Неизвестный тип запроса."}

    # ------------------------------------------------------------ аутентификация
    def _login(self, req: dict) -> dict:
        login, password, device = req.get("login"), req.get("password"), req.get("device_id")
        if not all(isinstance(x, str) and x for x in (login, password, device)):
            return {"status": "rejected", "code": "bad_login_request", "message_ru": "Укажите логин, пароль и идентификатор устройства."}
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id, pwd_salt, pwd_hash, active FROM users WHERE login=?", (login,)).fetchone()
            now_dt = self.clock.now()
            now = utc_iso(now_dt)
            ok = bool(row) and row[3] == 1 and hmac.compare_digest(_hash_pwd(password, row[1]), row[2])
            if not ok:
                # Пароль в журнал не пишется; логин — да (для расследования перебора).
                self._write_audit(conn, now, None, device, None, "auth.login", "user", row[0] if row else None, None, None, None, "denied", {"login": login})
                conn.execute("COMMIT")
                return {"status": "unauthenticated", "code": "bad_credentials", "message_ru": "Неверный логин или пароль, либо учётная запись отключена."}
            token = secrets.token_urlsafe(32)
            th = hashlib.sha256(token.encode()).hexdigest()
            conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?,NULL)", (th, row[0], device, now, utc_iso(now_dt + SESSION_TTL)))
            self._write_audit(conn, now, row[0], device, None, "auth.login", "user", row[0], None, None, None, "accepted", {})
            conn.execute("COMMIT")
            return {"status": "accepted", "token": token, "user_id": row[0], "expires_at": utc_iso(now_dt + SESSION_TTL)}
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _auth(self, conn, token, now) -> tuple[str, str]:
        if not isinstance(token, str) or not token:
            raise Unauthenticated("no_token", "Требуется вход в систему.")
        th = hashlib.sha256(token.encode()).hexdigest()
        row = conn.execute(
            """SELECT s.user_id, s.device_id FROM sessions s JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.active=1""",
            (th, now)).fetchone()
        if not row:
            raise Unauthenticated("session_invalid", "Сеанс истёк или завершён. Войдите снова; локальная очередь сохранится.")
        return row[0], row[1]

    # ------------------------------------------------------------ команды
    def _command(self, req: dict) -> dict:
        op_id = req.get("operation_id")
        command = req.get("command")
        payload = req.get("payload")
        expected_version = req.get("expected_version")
        if not isinstance(op_id, str) or not _UUID_RE.match(op_id):
            return {"status": "rejected", "code": "bad_operation_id", "message_ru": "operation_id должен быть UUID."}
        if command not in self.handlers:
            return {"status": "rejected", "code": "unknown_command", "message_ru": f"Неизвестная команда «{command}»."}
        if not isinstance(payload, dict):
            return {"status": "rejected", "code": "bad_payload", "message_ru": "payload должен быть объектом."}
        if expected_version is not None and (isinstance(expected_version, bool) or not isinstance(expected_version, int)):
            return {"status": "rejected", "code": "bad_expected_version", "message_ru": "expected_version должен быть целым числом."}
        # Роль/права, присланные клиентом, игнорируются намеренно: их нет в хэше и они не читаются.
        request_hash = hashlib.sha256(_canon({"c": command, "p": payload, "v": expected_version}).encode()).hexdigest()

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = self._now()
            prev = conn.execute("SELECT request_hash, response_json, user_id FROM operations WHERE operation_id=?", (op_id,)).fetchone()
            try:
                user_id, device_id = self._auth(conn, req.get("token"), now)
            except Unauthenticated as e:
                conn.execute("ROLLBACK")
                return {"status": e.status, "code": e.code, "message_ru": e.message_ru}
            if prev:
                conn.execute("ROLLBACK")
                if prev[0] != request_hash or prev[2] != user_id:
                    return {"status": "rejected", "code": "operation_id_reused",
                            "message_ru": "Этот operation_id уже использован для другой операции. Повтор с изменёнными данными запрещён."}
                stored = json.loads(prev[1])
                stored["replayed"] = True
                return stored

            ctx = Ctx(conn, user_id, device_id, op_id, now, command)
            cmd_meta = self.contracts.catalog["commands"][command]
            conn.execute("SAVEPOINT effect")
            try:
                if cmd_meta.get("requires_expected_version") and expected_version is None:
                    raise Rejected("expected_version_required", "Для изменения нужна версия, на которой основана правка (expected_version).")
                result = self.handlers[command](ctx, payload, expected_version)
                response = {"status": "accepted", "operation_id": op_id, "result": result}
                for a in ctx.audit:
                    self._write_audit(conn, now, user_id, device_id, op_id, command, a["object_type"], a["object_id"],
                                      a["authority"], a["version_before"], a["version_after"], "accepted", a["details"])
                if self._fault_after_effect == command:
                    raise RuntimeError("injected fault after effect, before commit")
            except CommandOutcome as e:
                # Откатываем частичные эффекты внутри той же транзакции (без окна для гонки),
                # затем фиксируем только исход + журнал отказа.
                conn.execute("ROLLBACK TO SAVEPOINT effect")
                response = {"status": e.status, "operation_id": op_id, "code": e.code, "message_ru": e.message_ru, "details": e.details}
                self._write_audit(conn, now, user_id, device_id, op_id, command, None, None, None, None, None, e.status,
                                  {"code": e.code, **({"details": e.details} if e.details else {})})
            conn.execute("RELEASE SAVEPOINT effect")
            conn.execute("INSERT INTO operations VALUES (?,?,?,?,?,?,?,?)",
                         (op_id, user_id, device_id, command, request_hash, response["status"], _canon(response), now))
            conn.execute("COMMIT")
            return response
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            # Внутренняя ошибка: исход НЕ записан, клиент может повторить тот же operation_id.
            return {"status": "error", "operation_id": op_id, "code": "internal_error",
                    "message_ru": "Внутренняя ошибка сервера. Изменение не применено; повтор безопасен."}
        finally:
            conn.close()

    # ------------------------------------------------------------ helpers
    def _get(self, conn, table: str, oid: str) -> Optional[dict]:
        cur = conn.execute(f"SELECT * FROM {table} WHERE id=?", (oid,))
        row = cur.fetchone()
        if not row:
            return None
        return {d[0]: v for d, v in zip(cur.description, row)}

    def _require(self, conn, table, oid, what_ru) -> dict:
        obj = self._get(conn, table, oid)
        if obj is None:
            raise Rejected("not_found", f"{what_ru} не найден(а).", {"id": oid})
        return obj

    @staticmethod
    def _check_version(obj: dict, expected: Optional[int], view: Callable[[dict], dict]):
        if expected is not None and obj["version"] != expected:
            raise Conflict("version_conflict",
                           f"Объект изменён другим пользователем: ваша версия {expected}, на сервере {obj['version']}. "
                           "Изменение не применено; сравните и решите, повторить ли его.",
                           {"expected_version": expected, "current": view(obj)})

    def _ensure_new_id(self, conn, table, oid):
        if self._get(conn, table, oid) is not None:
            raise Rejected("id_exists", "Объект с таким идентификатором уже существует.", {"id": oid})

    # ------------------------------------------------------------ представления (JSON)
    @staticmethod
    def v_customer(o): return {k: o[k] for k in ("id", "name", "inn", "note", "version", "updated_at")}

    @staticmethod
    def v_tender(o): return {k: o[k] for k in ("id", "customer_id", "title", "submission_deadline", "owner_user_id", "status", "status_reason", "version", "updated_at")}

    @staticmethod
    def v_tkp(o):
        d = {k: o[k] for k in ("id", "tender_id", "label", "revision", "currency", "note", "status", "sent_at", "sent_channel", "version", "last_edited_by", "updated_at")}
        d["amount"] = money.to_json(money.from_minor(o["amount_minor"]))
        return d

    @staticmethod
    def v_project(o): return {k: o[k] for k in ("id", "code", "title", "tender_id", "status", "version", "updated_at")}

    @staticmethod
    def v_kit(o): return {k: o[k] for k in ("id", "project_id", "code", "title", "version", "updated_at")}

    # ------------------------------------------------------------ заказчики
    def _customer_create(self, ctx: Ctx, p: dict, ev):
        only_keys(p, {"id", "name", "inn", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "customer.create", ctx.now)
        cid = req_uuid(p, "id")
        name = req_str(p, "name")
        inn = opt_str(p, "inn", 12)
        if inn is not None and not _INN_RE.match(inn):
            raise Rejected("invalid_inn", "ИНН должен содержать 10 или 12 цифр (контрольная сумма в срезе 1 не проверяется).")
        self._ensure_new_id(ctx.conn, "customers", cid)
        ctx.conn.execute("INSERT INTO customers VALUES (?,?,?,?,1,?,?,?,?)",
                         (cid, name, inn, opt_str(p, "note"), ctx.user_id, ctx.now, ctx.user_id, ctx.now))
        ctx.log("customer", cid, auth, None, 1, {"name": name})
        return {"customer": self.v_customer(self._get(ctx.conn, "customers", cid))}

    def _customer_update(self, ctx, p, ev):
        only_keys(p, {"id", "name", "inn", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "customer.update", ctx.now)
        o = self._require(ctx.conn, "customers", req_uuid(p, "id"), "Заказчик")
        self._check_version(o, ev, self.v_customer)
        changes = {}
        if "name" in p:
            changes["name"] = req_str(p, "name")
        if "inn" in p:
            inn = opt_str(p, "inn", 12)
            if inn is not None and not _INN_RE.match(inn):
                raise Rejected("invalid_inn", "ИНН должен содержать 10 или 12 цифр.")
            changes["inn"] = inn
        if "note" in p:
            changes["note"] = opt_str(p, "note")
        if not changes:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in changes}
        sets = ", ".join(f"{k}=?" for k in changes)
        ctx.conn.execute(f"UPDATE customers SET {sets}, version=version+1, updated_by=?, updated_at=? WHERE id=?",
                         (*changes.values(), ctx.user_id, ctx.now, o["id"]))
        ctx.log("customer", o["id"], auth, o["version"], o["version"] + 1, {"before": before, "after": changes})
        return {"customer": self.v_customer(self._get(ctx.conn, "customers", o["id"]))}

    # ------------------------------------------------------------ тендеры
    def _tender_create(self, ctx, p, ev):
        only_keys(p, {"id", "customer_id", "title", "submission_deadline", "owner_user_id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tender.create", ctx.now)
        tid = req_uuid(p, "id")
        self._require(ctx.conn, "customers", req_uuid(p, "customer_id"), "Заказчик")
        owner = p.get("owner_user_id") or ctx.user_id  # A-11: владелец по умолчанию — создатель (виден и меняется)
        if not self._get(ctx.conn, "users", owner):
            raise Rejected("owner_not_found", "Ответственный не найден.")
        self._ensure_new_id(ctx.conn, "tenders", tid)
        title = req_str(p, "title")
        ctx.conn.execute("INSERT INTO tenders VALUES (?,?,?,?,?,?,NULL,1,?,?,?,?)",
                         (tid, p["customer_id"], title, opt_date(p, "submission_deadline"), owner,
                          self.wf["tender"]["initial"], ctx.user_id, ctx.now, ctx.user_id, ctx.now))
        ctx.log("tender", tid, auth, None, 1, {"title": title, "customer_id": p["customer_id"]})
        return {"tender": self.v_tender(self._get(ctx.conn, "tenders", tid))}

    def _tender_update(self, ctx, p, ev):
        only_keys(p, {"id", "title", "submission_deadline", "owner_user_id"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tender.update", ctx.now)
        o = self._require(ctx.conn, "tenders", req_uuid(p, "id"), "Тендер")
        self._check_version(o, ev, self.v_tender)
        if o["status"] in self.wf["tender"]["terminal"]:
            raise Rejected("tender_closed", "Тендер в конечном статусе — изменение запрещено.")
        changes = {}
        if "title" in p:
            changes["title"] = req_str(p, "title")
        if "submission_deadline" in p:
            changes["submission_deadline"] = opt_date(p, "submission_deadline")
        if "owner_user_id" in p:
            if not self._get(ctx.conn, "users", p["owner_user_id"] or ""):
                raise Rejected("owner_not_found", "Ответственный не найден.")
            changes["owner_user_id"] = p["owner_user_id"]
        if not changes:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in changes}
        sets = ", ".join(f"{k}=?" for k in changes)
        ctx.conn.execute(f"UPDATE tenders SET {sets}, version=version+1, updated_by=?, updated_at=? WHERE id=?",
                         (*changes.values(), ctx.user_id, ctx.now, o["id"]))
        ctx.log("tender", o["id"], auth, o["version"], o["version"] + 1, {"before": before, "after": changes})
        return {"tender": self.v_tender(self._get(ctx.conn, "tenders", o["id"]))}

    def _tender_transition(self, ctx, p, ev):
        only_keys(p, {"id", "to", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tender.transition", ctx.now)
        o = self._require(ctx.conn, "tenders", req_uuid(p, "id"), "Тендер")
        self._check_version(o, ev, self.v_tender)
        to = p.get("to")
        allowed = self.wf["tender"]["transitions"].get(o["status"], [])
        if to not in allowed:
            raise Rejected("transition_not_allowed", f"Переход «{o['status']}» → «{to}» не разрешён. Допустимо: {', '.join(allowed) or 'нет'}.",
                           {"from": o["status"], "to": to, "allowed": allowed})
        reason = opt_str(p, "reason")
        if to in self.wf["tender"]["reason_required_for"] and not reason:
            raise Rejected("reason_required", "Для этого перехода укажите причину.")
        ctx.conn.execute("UPDATE tenders SET status=?, status_reason=?, version=version+1, updated_by=?, updated_at=? WHERE id=?",
                         (to, reason, ctx.user_id, ctx.now, o["id"]))
        ctx.log("tender", o["id"], auth, o["version"], o["version"] + 1, {"from": o["status"], "to": to, "reason": reason})
        # Явно: победа в тендере НЕ создаёт проект. Проект — только командой project.start_rkd.
        return {"tender": self.v_tender(self._get(ctx.conn, "tenders", o["id"]))}

    # ------------------------------------------------------------ ТКП
    @staticmethod
    def _parse_amount(p: dict) -> tuple[Optional[int], Optional[str]]:
        try:
            amount = money.parse_optional(p.get("amount"))
        except money.MoneyError as e:
            raise Rejected(f"amount_{e.code}", e.message_ru, {"field": "amount"})
        cur = p.get("currency")
        if cur is not None and (not isinstance(cur, str) or not _CUR_RE.match(cur)):
            raise Rejected("invalid_currency", "Валюта — трёхбуквенный код ISO 4217 (например, указанный пользователем). Значение по умолчанию не подставляется.")
        if amount is not None and cur is None:
            raise Rejected("currency_required", "Для суммы обязательно укажите валюту — значение по умолчанию не подставляется.")
        return (money.to_minor(amount) if amount is not None else None), cur

    @staticmethod
    def _tkp_hash(o: dict) -> str:
        return hashlib.sha256(_canon({k: o[k] for k in ("id", "tender_id", "label", "revision", "amount_minor", "currency", "note")}).encode()).hexdigest()

    def _tkp_create(self, ctx, p, ev):
        only_keys(p, {"id", "tender_id", "label", "amount", "currency", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.create", ctx.now)
        kid = req_uuid(p, "id")
        t = self._require(ctx.conn, "tenders", req_uuid(p, "tender_id"), "Тендер")
        if t["status"] in ("lost", "cancelled"):
            raise Rejected("tender_closed", "Тендер закрыт (проигран или отменён) — новое ТКП не создаётся.")
        label = req_str(p, "label", 64)
        amount_minor, cur = self._parse_amount(p)
        self._ensure_new_id(ctx.conn, "tkp", kid)
        ctx.conn.execute("INSERT INTO tkp VALUES (?,?,?,1,?,?,?,'draft',NULL,NULL,1,?,?,?,?)",
                         (kid, t["id"], label, amount_minor, cur, opt_str(p, "note"), ctx.user_id, ctx.now, ctx.user_id, ctx.now))
        ctx.log("tkp", kid, auth, None, 1, {"label": label, "amount": p.get("amount"), "currency": cur})
        return {"tkp": self.v_tkp(self._get(ctx.conn, "tkp", kid))}

    def _tkp_update(self, ctx, p, ev):
        only_keys(p, {"id", "label", "amount", "currency", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.update", ctx.now)
        o = self._require(ctx.conn, "tkp", req_uuid(p, "id"), "ТКП")
        self._check_version(o, ev, self.v_tkp)
        if o["status"] != "draft":
            raise Rejected("tkp_not_draft", "Изменять можно только черновик ТКП. Чтобы изменить согласованное — сначала явно верните его в черновик.")
        changes: dict[str, Any] = {}
        if "label" in p:
            changes["label"] = req_str(p, "label", 64)
        if "amount" in p or "currency" in p:
            merged = {"amount": p.get("amount", money.to_json(money.from_minor(o["amount_minor"]))),
                      "currency": p.get("currency", o["currency"])}
            changes["amount_minor"], changes["currency"] = self._parse_amount(merged)
        if "note" in p:
            changes["note"] = opt_str(p, "note")
        if not changes:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in changes}
        sets = ", ".join(f"{k}=?" for k in changes)
        ctx.conn.execute(f"UPDATE tkp SET {sets}, revision=revision+1, version=version+1, last_edited_by=?, updated_at=? WHERE id=?",
                         (*changes.values(), ctx.user_id, ctx.now, o["id"]))
        ctx.log("tkp", o["id"], auth, o["version"], o["version"] + 1, {"before": before, "after": changes, "revision": o["revision"] + 1})
        return {"tkp": self.v_tkp(self._get(ctx.conn, "tkp", o["id"]))}

    def _tkp_approve(self, ctx, p, ev):
        only_keys(p, {"id", "comment"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.approve", ctx.now)
        o = self._require(ctx.conn, "tkp", req_uuid(p, "id"), "ТКП")
        self._check_version(o, ev, self.v_tkp)
        if o["status"] != "draft":
            raise Rejected("tkp_not_draft", "Согласовать можно только черновик.")
        if o["last_edited_by"] == ctx.user_id:
            raise Denied("self_approval", "Автор последней правки не может согласовать ТКП сам (разделение обязанностей, A-04).")
        h = self._tkp_hash(o)
        ctx.conn.execute("INSERT INTO tkp_approvals(tkp_id,tkp_revision,content_hash,approver_id,grant_id,approved_at) VALUES (?,?,?,?,?,?)",
                         (o["id"], o["revision"], h, ctx.user_id, auth.ref_id, ctx.now))
        ctx.conn.execute("UPDATE tkp SET status='approved', version=version+1, updated_at=? WHERE id=?", (ctx.now, o["id"]))
        ctx.log("tkp", o["id"], auth, o["version"], o["version"] + 1,
                {"approved_revision": o["revision"], "content_hash": h, "comment": opt_str(p, "comment")})
        return {"tkp": self.v_tkp(self._get(ctx.conn, "tkp", o["id"]))}

    def _tkp_reopen(self, ctx, p, ev):
        only_keys(p, {"id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.reopen", ctx.now)
        o = self._require(ctx.conn, "tkp", req_uuid(p, "id"), "ТКП")
        self._check_version(o, ev, self.v_tkp)
        if o["status"] != "approved":
            raise Rejected("tkp_not_approved", "Вернуть в черновик можно только согласованное ТКП.")
        reason = req_str(p, "reason", 500)
        ctx.conn.execute("UPDATE tkp_approvals SET superseded_at=?, superseded_reason=? WHERE tkp_id=? AND superseded_at IS NULL",
                         (ctx.now, "reopen: " + reason, o["id"]))
        ctx.conn.execute("UPDATE tkp SET status='draft', version=version+1, updated_at=? WHERE id=?", (ctx.now, o["id"]))
        ctx.log("tkp", o["id"], auth, o["version"], o["version"] + 1, {"reason": reason, "approval": "superseded"})
        return {"tkp": self.v_tkp(self._get(ctx.conn, "tkp", o["id"]))}

    def _tkp_record_sent(self, ctx, p, ev):
        only_keys(p, {"id", "sent_on", "channel", "note"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "tkp.record_sent", ctx.now)
        o = self._require(ctx.conn, "tkp", req_uuid(p, "id"), "ТКП")
        self._check_version(o, ev, self.v_tkp)
        if o["status"] != "approved":
            raise Rejected("tkp_not_approved", "Зарегистрировать отправку можно только для согласованного ТКП.")
        appr = ctx.conn.execute("SELECT id, tkp_revision, content_hash, approver_id, grant_id FROM tkp_approvals WHERE tkp_id=? AND superseded_at IS NULL ORDER BY id DESC LIMIT 1",
                                (o["id"],)).fetchone()
        approve_roles = self.policy.roles_for("tkp.approve", "global_role")
        # Проверка согласования в момент эффекта: тот же снимок + та же выдача роли (generation) ещё действует.
        if (not appr or appr[1] != o["revision"] or appr[2] != self._tkp_hash(o)
                or not grant_is_active(ctx.conn, appr[4], appr[3], approve_roles, ctx.now)):
            raise Rejected("approval_invalid",
                           "Согласование недействительно: изменилось содержимое, или полномочие согласующего отозвано/истекло. "
                           "Повторная выдача роли не восстанавливает прежнее согласование — нужно новое.",
                           {"approval_id": appr[0] if appr else None})
        sent_on = opt_date(p, "sent_on")
        if not sent_on:
            raise Rejected("field_required", "Укажите дату фактической отправки.")
        channel = opt_str(p, "channel", 100)
        ctx.conn.execute("UPDATE tkp SET status='sent_recorded', sent_at=?, sent_channel=?, version=version+1, updated_at=? WHERE id=?",
                         (sent_on, channel, ctx.now, o["id"]))
        ctx.log("tkp", o["id"], auth, o["version"], o["version"] + 1,
                {"sent_on": sent_on, "channel": channel, "approval_id": appr[0],
                 "meaning": "Зарегистрирован факт отправки со слов пользователя; доставка и получение не доказаны."})
        return {"tkp": self.v_tkp(self._get(ctx.conn, "tkp", o["id"]))}

    # ------------------------------------------------------------ проекты
    def _project_start_rkd(self, ctx, p, ev):
        only_keys(p, {"id", "tender_id", "code", "title"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "project.start_rkd", ctx.now)
        pid = req_uuid(p, "id")
        t = self._require(ctx.conn, "tenders", req_uuid(p, "tender_id"), "Тендер")
        allowed = self.wf["project"]["start_rkd_allowed_from_tender_status"]
        if t["status"] not in allowed:
            raise Rejected("tender_status_not_allowed",
                           f"Начать РКД можно из статуса тендера: {', '.join(allowed)}. Сейчас: {t['status']}.",
                           {"tender_status": t["status"]})
        if self.wf["project"]["one_project_per_tender"]:
            ex = ctx.conn.execute("SELECT id, code FROM projects WHERE tender_id=?", (t["id"],)).fetchone()
            if ex:
                raise Rejected("project_exists_for_tender", f"По этому тендеру уже начат проект {ex[1]}.", {"project_id": ex[0]})
        code = req_str(p, "code", 64)
        if ctx.conn.execute("SELECT 1 FROM projects WHERE code=?", (code,)).fetchone():
            raise Rejected("project_code_taken", "Код проекта уже используется (уникальность в пределах компании, A-10).")
        self._ensure_new_id(ctx.conn, "projects", pid)
        title = req_str(p, "title")
        ctx.conn.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,1,?,?,?,?)",
                         (pid, code, title, t["id"], t["version"], self.wf["project"]["initial"], ctx.user_id, ctx.now, ctx.user_id, ctx.now))
        ctx.log("project", pid, auth, None, 1, {"tender_id": t["id"], "tender_version": t["version"], "code": code,
                                               "note": "ГИП не назначен — назначает администратор."})
        return {"project": self.v_project(self._get(ctx.conn, "projects", pid)), "gip_assigned": False}

    def _project_assign(self, ctx, p, ev):
        only_keys(p, {"project_id", "user_id", "project_role", "valid_until", "reason"})
        pr = self._require(ctx.conn, "projects", req_uuid(p, "project_id"), "Проект")
        role = p.get("project_role")
        if role not in self.contracts.permissions["project_roles"]:
            raise Rejected("bad_project_role", "Роль в проекте: gip, rp или participant.")
        auth = self.policy.check(ctx.conn, ctx.user_id, f"project.assign_{role}", ctx.now, project_id=pr["id"])
        target = self._get(ctx.conn, "users", p.get("user_id") or "")
        if not target or target["active"] != 1:
            raise Rejected("user_not_found", "Пользователь не найден или отключён.")
        valid_until = p.get("valid_until")
        if valid_until is not None and (not isinstance(valid_until, str) or valid_until <= ctx.now):
            raise Rejected("bad_valid_until", "Срок назначения должен быть в будущем (ISO-8601 UTC).")
        if role == "gip" and ctx.conn.execute(
                "SELECT 1 FROM project_assignments WHERE project_id=? AND project_role='gip' AND revoked_at IS NULL AND (valid_until IS NULL OR valid_until>?)",
                (pr["id"], ctx.now)).fetchone():
            raise Rejected("gip_already_assigned", "ГИП уже назначен. Сначала явно отзовите текущее назначение.")
        if ctx.conn.execute(
                "SELECT 1 FROM project_assignments WHERE project_id=? AND user_id=? AND project_role=? AND revoked_at IS NULL AND (valid_until IS NULL OR valid_until>?)",
                (pr["id"], target["id"], role, ctx.now)).fetchone():
            raise Rejected("already_assigned", "Такое действующее назначение уже есть.")
        aid = ctx.conn.execute(
            "INSERT INTO project_assignments(project_id,user_id,project_role,valid_from,valid_until,assigned_by) VALUES (?,?,?,?,?,?)",
            (pr["id"], target["id"], role, ctx.now, valid_until, ctx.user_id)).lastrowid
        ctx.log("assignment", str(aid), auth, None, 1, {"project_id": pr["id"], "user_id": target["id"], "project_role": role,
                                                       "valid_until": valid_until, "reason": opt_str(p, "reason")})
        return {"assignment_id": aid}

    def _project_revoke_assignment(self, ctx, p, ev):
        only_keys(p, {"assignment_id", "reason"})
        aid = p.get("assignment_id")
        row = ctx.conn.execute("SELECT project_id, project_role, revoked_at, user_id FROM project_assignments WHERE id=?",
                               (aid if isinstance(aid, int) and not isinstance(aid, bool) else -1,)).fetchone()
        if not row:
            raise Rejected("not_found", "Назначение не найдено.")
        auth = self.policy.check(ctx.conn, ctx.user_id, f"project.assign_{row[1]}", ctx.now, project_id=row[0])
        if row[2]:
            raise Rejected("already_revoked", "Назначение уже отозвано.")
        reason = req_str(p, "reason", 500)
        ctx.conn.execute("UPDATE project_assignments SET revoked_at=?, revoked_reason=? WHERE id=?", (ctx.now, reason, aid))
        ctx.log("assignment", str(aid), auth, 1, 2, {"revoked": True, "reason": reason, "user_id": row[3], "project_role": row[1]})
        return {"assignment_id": aid, "revoked": True}

    # ------------------------------------------------------------ комплекты
    def _kit_create(self, ctx, p, ev):
        only_keys(p, {"id", "project_id", "code", "title"})
        pr = self._require(ctx.conn, "projects", req_uuid(p, "project_id"), "Проект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "kit.create", ctx.now, project_id=pr["id"])
        if pr["status"] != "rkd_in_progress":
            raise Rejected("project_state", "Комплекты добавляются только в проект в статусе «Разработка РКД».")
        kid = req_uuid(p, "id")
        code = req_str(p, "code", 64)
        if ctx.conn.execute("SELECT 1 FROM kits WHERE project_id=? AND code=?", (pr["id"], code)).fetchone():
            raise Rejected("kit_code_taken", "Комплект с таким кодом уже есть в этом проекте.")
        self._ensure_new_id(ctx.conn, "kits", kid)
        title = req_str(p, "title")
        ctx.conn.execute("INSERT INTO kits VALUES (?,?,?,?,1,?,?,?,?)", (kid, pr["id"], code, title, ctx.user_id, ctx.now, ctx.user_id, ctx.now))
        ctx.log("kit", kid, auth, None, 1, {"project_id": pr["id"], "code": code})
        return {"kit": self.v_kit(self._get(ctx.conn, "kits", kid))}

    def _kit_update(self, ctx, p, ev):
        only_keys(p, {"id", "code", "title"})
        o = self._require(ctx.conn, "kits", req_uuid(p, "id"), "Комплект")
        auth = self.policy.check(ctx.conn, ctx.user_id, "kit.update", ctx.now, project_id=o["project_id"])
        self._check_version(o, ev, self.v_kit)
        changes = {}
        if "code" in p:
            changes["code"] = req_str(p, "code", 64)
            if changes["code"] != o["code"] and ctx.conn.execute("SELECT 1 FROM kits WHERE project_id=? AND code=?", (o["project_id"], changes["code"])).fetchone():
                raise Rejected("kit_code_taken", "Комплект с таким кодом уже есть в этом проекте.")
        if "title" in p:
            changes["title"] = req_str(p, "title")
        if not changes:
            raise Rejected("no_changes", "Нет изменений.")
        before = {k: o[k] for k in changes}
        sets = ", ".join(f"{k}=?" for k in changes)
        ctx.conn.execute(f"UPDATE kits SET {sets}, version=version+1, updated_by=?, updated_at=? WHERE id=?", (*changes.values(), ctx.user_id, ctx.now, o["id"]))
        ctx.log("kit", o["id"], auth, o["version"], o["version"] + 1, {"before": before, "after": changes})
        return {"kit": self.v_kit(self._get(ctx.conn, "kits", o["id"]))}

    # ------------------------------------------------------------ администрирование
    def _admin_create_user(self, ctx, p, ev):
        only_keys(p, {"login", "display_name", "initial_password"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.create_user", ctx.now)
        login = req_str(p, "login", 64)
        pwd = p.get("initial_password")
        if not isinstance(pwd, str) or len(pwd) < 12:
            raise Rejected("weak_password", "Начальный пароль — не короче 12 символов (политику паролей утверждает ИБ).")
        if ctx.conn.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
            raise Rejected("login_taken", "Логин занят.")
        uid = str(uuid.uuid4())
        salt = os.urandom(16)
        ctx.conn.execute("INSERT INTO users VALUES (?,?,?,?,?,1,?)", (uid, login, req_str(p, "display_name"), salt, _hash_pwd(pwd, salt), ctx.now))
        ctx.log("user", uid, auth, None, 1, {"login": login})  # пароль в журнал не попадает
        return {"user_id": uid}

    def _admin_grant_role(self, ctx, p, ev):
        only_keys(p, {"user_id", "role", "valid_until", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.grant_role", ctx.now)
        if p.get("role") not in self.contracts.permissions["roles"]:
            raise Rejected("bad_role", "Неизвестная роль доступа.")
        if not self._get(ctx.conn, "users", p.get("user_id") or ""):
            raise Rejected("user_not_found", "Пользователь не найден.")
        vu = p.get("valid_until")
        if vu is not None and (not isinstance(vu, str) or vu <= ctx.now):
            raise Rejected("bad_valid_until", "Срок роли должен быть в будущем (ISO-8601 UTC).")
        reason = req_str(p, "reason", 500)
        gid = ctx.conn.execute("INSERT INTO role_grants(user_id,role,scope_type,valid_from,valid_until,granted_by,reason) VALUES (?,?,'company',?,?,?,?)",
                               (p["user_id"], p["role"], ctx.now, vu, ctx.user_id, reason)).lastrowid
        ctx.log("role_grant", str(gid), auth, None, 1, {"user_id": p["user_id"], "role": p["role"], "valid_until": vu, "reason": reason})
        return {"grant_id": gid}

    def _admin_revoke_role(self, ctx, p, ev):
        only_keys(p, {"grant_id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.revoke_role", ctx.now)
        gid = p.get("grant_id")
        row = ctx.conn.execute("SELECT revoked_at, user_id, role FROM role_grants WHERE id=?",
                               (gid if isinstance(gid, int) and not isinstance(gid, bool) else -1,)).fetchone()
        if not row:
            raise Rejected("not_found", "Выдача роли не найдена.")
        if row[0]:
            raise Rejected("already_revoked", "Роль уже отозвана.")
        reason = req_str(p, "reason", 500)
        ctx.conn.execute("UPDATE role_grants SET revoked_at=? WHERE id=?", (ctx.now, gid))
        ctx.log("role_grant", str(gid), auth, 1, 2, {"revoked": True, "user_id": row[1], "role": row[2], "reason": reason})
        return {"grant_id": gid, "revoked": True}

    def _admin_revoke_sessions(self, ctx, p, ev):
        only_keys(p, {"user_id", "reason"})
        auth = self.policy.check(ctx.conn, ctx.user_id, "admin.revoke_sessions", ctx.now)
        n = ctx.conn.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (ctx.now, p.get("user_id"))).rowcount
        ctx.log("session", p.get("user_id"), auth, None, None, {"revoked_sessions": n, "reason": req_str(p, "reason", 500)})
        return {"revoked_sessions": n}

    # ------------------------------------------------------------ запросы
    def _query(self, req: dict) -> dict:
        conn = self._connect()
        try:
            conn.execute("BEGIN")  # согласованный снимок чтения
            now = self._now()
            try:
                user_id, _ = self._auth(conn, req.get("token"), now)
            except Unauthenticated as e:
                return {"status": e.status, "code": e.code, "message_ru": e.message_ru}
            q = req.get("query")
            if q == "pull":
                return {"status": "accepted", "result": self._pull(conn, user_id, now)}
            if q == "history":
                return self._history(conn, user_id, now, req.get("object_type"), req.get("object_id"))
            return {"status": "rejected", "code": "unknown_query", "message_ru": "Неизвестный запрос."}
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()

    def _rows(self, conn, sql, args=()):
        cur = conn.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _pull(self, conn, user_id, now) -> dict:
        can = lambda action, pid=None: self.policy.can(conn, user_id, action, now, pid)  # noqa: E731
        out: dict[str, Any] = {"server_time": now, "customers": [], "tenders": [], "tkp": [], "projects": [], "kits": []}
        if can("customer.read"):
            out["customers"] = [self.v_customer(o) for o in self._rows(conn, "SELECT * FROM customers ORDER BY name")]
        if can("tender.read"):
            out["tenders"] = [self.v_tender(o) for o in self._rows(conn, "SELECT * FROM tenders ORDER BY created_at")]
        if can("tkp.read"):
            out["tkp"] = [self.v_tkp(o) for o in self._rows(conn, "SELECT * FROM tkp ORDER BY created_at")]
        for pr in self._rows(conn, "SELECT * FROM projects ORDER BY code"):
            if can("project.read", pr["id"]):
                out["projects"].append(self.v_project(pr))
                out["kits"].extend(self.v_kit(k) for k in self._rows(conn, "SELECT * FROM kits WHERE project_id=? ORDER BY code", (pr["id"],)))
        out["me"] = {
            "user_id": user_id,
            "roles": [r for _, r in active_grants(conn, user_id, now)],
            "note_ru": "Роли в кэше клиента служат только подсказкой интерфейса; решение принимает сервер в момент эффекта.",
        }
        return out

    def _history(self, conn, user_id, now, otype, oid) -> dict:
        read_action = {"customer": "customer.read", "tender": "tender.read", "tkp": "tkp.read", "project": "project.read", "kit": "kit.read"}.get(otype)
        if not read_action or not isinstance(oid, str):
            return {"status": "rejected", "code": "bad_history_request", "message_ru": "Укажите тип и id объекта."}
        pid = None
        if otype == "project":
            pid = oid
        elif otype == "kit":
            k = self._get(conn, "kits", oid)
            pid = k["project_id"] if k else None
        if not self.policy.can(conn, user_id, read_action, now, pid):
            return {"status": "denied", "code": "no_authority", "message_ru": "Нет права на просмотр истории этого объекта."}
        rows = self._rows(conn, "SELECT at, actor_user_id, operation_id, command, authority, version_before, version_after, result, details_json FROM audit_log WHERE object_type=? AND object_id=? ORDER BY id", (otype, oid))
        for r in rows:
            r["details"] = json.loads(r.pop("details_json"))
        return {"status": "accepted", "result": rows}
