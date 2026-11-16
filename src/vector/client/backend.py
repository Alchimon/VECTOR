"""Мост QML ↔ офлайн-ядро.

Все обращения к SyncClient идут через ОДИН рабочий поток (последовательно): сеть и диск не блокируют интерфейс,
а локальная БД используется из одного потока. Результаты возвращаются сигналами в поток интерфейса.
Права в интерфейсе — только подсказка (скрыть явно недоступное); решение всегда принимает сервер.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Property, QObject, QSettings, QTimer, Signal, Slot

from vector import __version__
from vector.core.contracts import Contracts

from .sync import SyncClient
from .transport import HttpTransport
from .vault import KeyringStore, MemoryStore, VaultError

log = logging.getLogger("vector.client")

ROLE_LABELS = {"admin": "Администратор", "gip": "ГИП", "rp": "РП", "dept_head": "Руководитель отдела",
               "lead_engineer": "Ведущий инженер", "engineer": "Инженер", "designer": "Конструктор", "technologist": "Технолог",
               "production": "Производство", "warehouse": "Склад", "procurement": "Закупка", "accountant": "Бухгалтерия"}
FIELD_LABELS = {"name": "Наименование", "short_name": "Краткое наименование", "inn": "ИНН", "kpp": "КПП", "note": "Примечание",
                "contact": "Контакт", "title": "Название", "deadline": "Срок подачи", "customer_id": "Заказчик", "tender_id": "Тендер",
                "amount": "Сумма", "currency": "Валюта", "number": "Номер", "code": "Шифр", "status": "Статус", "reason": "Основание",
                "channel": "Канал", "sent_at": "Дата отправки", "responsible_id": "Ответственный", "valid_until": "Действует до"}
PROJECT_ROLE_LABELS = {"gip": "ГИП проекта", "rp": "РП проекта", "participant": "Участник"}


def default_data_dir() -> Path:
    if platform.system() == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "VECTOR" / "profiles"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "vector" / "profiles"


def _ro(attr: str, typ, notify):
    return Property(typ, lambda self: getattr(self, attr), notify=notify)


def _lst(kind: str, notify):
    return Property("QVariantList", lambda self: self._decorate(kind), notify=notify)


class Backend(QObject):
    changed = Signal()
    dataChanged = Signal()
    toast = Signal(str, str)                 # kind (success|info|warning|error), text
    commandDone = Signal(str, "QVariant")    # request tag, result dict
    queryDone = Signal(str, "QVariant")
    _deliver = Signal(int, object)

    def __init__(self, client_factory: Optional[Callable[[str, str], SyncClient]] = None, settings: Optional[QSettings] = None,
                 auto_timers: bool = True):
        super().__init__()
        self.contracts = Contracts.load()
        self._settings = settings or QSettings("VECTOR", "VECTOR-desktop")
        self._factory = client_factory or self._default_factory
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vector-sync")
        self._callbacks: dict[int, Callable[[Any], None]] = {}
        self._seq = 0
        self._deliver.connect(self._on_deliver)
        self.client: Optional[SyncClient] = None
        self._screen = "login"
        self._busy = False
        self._online = False
        self._session_mode = "none"
        self._pending = 0
        self._problems = 0
        self._me: dict = {}
        self._data: dict[str, list] = {"customer": [], "tender": [], "tkp": [], "project": [], "kit": []}
        self._people: dict[str, str] = {}
        self._last_pull = ""
        self._theme = self._settings.value("ui/theme", "light")
        self._login_error = ""
        self._queue: list = []
        self._assignments: list = []
        self._perm_rev = 0
        if auto_timers:
            self._timer = QTimer(self)
            self._timer.setInterval(20000)
            self._timer.timeout.connect(self.backgroundSync)
            self._timer.start()

    # ------------------------------------------------------------ инфраструктура
    def _default_factory(self, server_url: str, ca_file: str) -> SyncClient:
        try:
            store = KeyringStore()
        except VaultError:
            if os.environ.get("VECTOR_DEV_INSECURE_STORE") == "1":
                store = MemoryStore()  # только разработка/скриншоты: офлайн-профиль не переживёт перезапуск
            else:
                raise
        device = self._settings.value("device/id")
        if not device:
            device = str(uuid.uuid4())
            self._settings.setValue("device/id", device)
        return SyncClient(HttpTransport(server_url, ca_file or None), server_url, default_data_dir(), store, device, self.contracts)

    def _run(self, fn: Callable[[], Any], done: Callable[[Any], None]):
        self._seq += 1
        n = self._seq
        self._callbacks[n] = done
        self._set_busy(True)

        def job():
            try:
                res = fn()
            except VaultError as e:
                res = {"status": "error", "message_ru": e.message_ru}
            except Exception as e:  # не роняем интерфейс; подробности — в журнал
                log.exception("client job failed")
                res = {"status": "error", "message_ru": f"Ошибка клиента: {type(e).__name__}. Изменения в очереди сохранены."}
            self._deliver.emit(n, res)
        self._exec.submit(job)

    @Slot(int, object)
    def _on_deliver(self, n, res):
        cb = self._callbacks.pop(n, None)
        if not self._callbacks:
            self._set_busy(False)
        if cb:
            cb(res)

    def _set_busy(self, v):
        if self._busy != v:
            self._busy = v
            self.changed.emit()

    def shutdown(self):
        self._exec.shutdown(wait=True)

    # ------------------------------------------------------------ свойства
    screen = _ro("_screen", str, changed)
    busy = _ro("_busy", bool, changed)
    online = _ro("_online", bool, changed)
    sessionMode = _ro("_session_mode", str, changed)
    pendingCount = _ro("_pending", int, changed)
    problemCount = _ro("_problems", int, changed)
    lastPull = _ro("_last_pull", str, changed)
    loginError = _ro("_login_error", str, changed)

    @Property(str, notify=changed)
    def theme(self):
        return self._theme

    @theme.setter
    def theme(self, v):
        if v in ("light", "dark") and v != self._theme:
            self._theme = v
            self._settings.setValue("ui/theme", v)
            self.changed.emit()

    @Property(str, constant=True)
    def version(self):
        return __version__

    @Property(str, notify=changed)
    def savedServer(self):
        return self._settings.value("server/url", "")

    @Property(str, notify=changed)
    def savedLogin(self):
        return self._settings.value("server/login", "")

    @Property(str, notify=changed)
    def savedCa(self):
        return self._settings.value("server/ca", "")

    @Property("QVariantMap", notify=changed)
    def me(self):
        roles = self._me.get("roles", [])
        return {**self._me, "role_labels": [ROLE_LABELS.get(r, r) for r in roles],
                "initials": "".join(w[:1] for w in (self._me.get("display_name") or "?").split()[:2]).upper()}

    customers = _lst("customer", dataChanged)
    tenders = _lst("tender", dataChanged)
    tkps = _lst("tkp", dataChanged)
    projects = _lst("project", dataChanged)
    kits = _lst("kit", dataChanged)

    @Property("QVariantList", notify=dataChanged)
    def people(self):
        return [{"id": k, "display_name": v} for k, v in sorted(self._people.items(), key=lambda kv: kv[1])]

    @Property("QVariantList", notify=dataChanged)
    def assignments(self):
        return [{**a, "user_name": self._people.get(a["user_id"], "—"), "role_label": PROJECT_ROLE_LABELS.get(a["project_role"], a["project_role"])}
                for a in self._assignments]

    @Property("QVariantList", constant=True)
    def roleOptions(self):
        return [{"id": r, "label": ROLE_LABELS.get(r, r)} for r in self.contracts.permissions["roles"]]

    @Property("QVariantList", constant=True)
    def projectRoleOptions(self):
        return [{"id": r, "label": PROJECT_ROLE_LABELS.get(r, r)} for r in self.contracts.permissions["project_roles"]]

    def _decorate(self, kind):
        wf = self.contracts.workflows
        out = []
        for o in self._data[kind]:
            d = dict(o)
            d["pending"] = bool(o.get("_pending"))
            if kind == "tender":
                d["status_label"] = wf["tender"]["labels_ru"].get(o.get("status"), o.get("status") or "—")
                d["customer_name"] = self._name("customer", o.get("customer_id"))
                d["owner_name"] = self._people.get(o.get("owner_user_id") or "", "")
                d["next"] = [{"id": s, "label": wf["tender"]["labels_ru"][s], "reason": s in wf["tender"]["reason_required_for"]}
                             for s in wf["tender"]["transitions"].get(o.get("status"), [])]
                d["can_start_rkd"] = o.get("status") in wf["project"]["start_rkd_allowed_from_tender_status"] and \
                    not any(p.get("tender_id") == o["id"] for p in self._data["project"])
                d["project_id"] = next((p["id"] for p in self._data["project"] if p.get("tender_id") == o["id"]), "")
            if kind == "tkp":
                d["status_label"] = wf["tkp"]["labels_ru"].get(o.get("status"), o.get("status") or "—")
                d["amount_label"] = format_money(o.get("amount"), o.get("currency"))
            if kind == "project":
                d["status_label"] = wf["project"]["labels_ru"].get(o.get("status"), o.get("status") or "—")
                d["tender_title"] = self._name("tender", o.get("tender_id"), "title")
                d["kit_count"] = sum(1 for k in self._data["kit"] if k.get("project_id") == o["id"])
            out.append(d)
        key = {"customer": "name", "tender": "title", "tkp": "label", "project": "code", "kit": "code"}[kind]
        return sorted(out, key=lambda d: (str(d.get(key) or "")).lower())

    def _name(self, kind, oid, field="name"):
        return next((o.get(field) for o in self._data[kind] if o["id"] == oid), "")

    # ------------------------------------------------------------ права-подсказки
    @Property(int, notify=changed)
    def permRevision(self):
        return self._perm_rev

    @Slot(str, result=str)
    def commandLabel(self, command: str) -> str:
        meta = self.contracts.catalog["commands"].get(command) or {}
        return meta.get("label_ru") or command

    @Slot(str, result=str)
    def authorityLabel(self, authority: str) -> str:
        """global_role:rp#grant:2 → «роль РП (выдача №2)»; без внутренних кодов в интерфейсе."""
        if not authority:
            return "—"
        if authority == "self":
            return "собственная учётная запись"
        m = re.fullmatch(r"(global_role|project_role):([a-z_]+)#(grant|assignment):(\w+)", authority)
        if not m:
            return authority
        kind, role, _src, ref = m.groups()
        if kind == "global_role":
            return f"роль «{ROLE_LABELS.get(role, role)}» (выдача №{ref})"
        return f"«{PROJECT_ROLE_LABELS.get(role, role)}» (назначение №{ref})"

    @Slot(str, int, result=bool)
    def hint(self, action: str, _revision: int = 0) -> bool:
        """Подсказка интерфейса по глобальным ролям. Правила с ролями проекта — true (решит сервер)."""
        rule = self.contracts.permissions["rules"].get(action)
        if not rule:
            return False
        if "same_as" in rule:
            return self.hint(rule["same_as"], 0)
        roles = set(self._me.get("roles", []))
        for cond in rule.get("any_of", []):
            if cond.get("authenticated") or "project_role" in cond or roles & set(cond.get("global_role", [])):
                return True
        return False

    # ------------------------------------------------------------ вход
    @Slot(str, str, str, str)
    def login(self, server_url: str, login: str, password: str, ca_file: str):
        server_url, login = server_url.strip(), login.strip()
        if not server_url or not login or not password:
            self._login_error = "Укажите адрес сервера, логин и пароль."
            self.changed.emit()
            return
        self._login_error = ""
        try:
            if self.client is None or self.client.server_url != server_url:
                self.client = self._factory(server_url, ca_file.strip())
        except (ValueError, VaultError) as e:
            self._login_error = getattr(e, "message_ru", str(e))
            self.changed.emit()
            return
        self._settings.setValue("server/url", server_url)
        self._settings.setValue("server/login", login)
        self._settings.setValue("server/ca", ca_file.strip())

        def done(r):
            st = r.status if hasattr(r, "status") else r.get("status")
            msg = r.message_ru if hasattr(r, "message_ru") else r.get("message_ru", "")
            if st == "accepted":
                self._session_mode, self._online = "online", True
                self._screen = "change_password" if self.client.must_change_password else "main"
                if self._screen == "main":
                    self._refresh_after(lambda: None, pull=True)
                if self.client.orphaned_cache:
                    self.toast.emit("warning", "Пароль был сброшен администратором: прежний локальный кэш недоступен и отложен "
                                               "(не удалён). Несинхронизированные изменения из него не отправлены.")
            elif st == "offline_unlocked":
                self._session_mode, self._online, self._screen = "offline", False, "main"
                self._refresh_after(lambda: None, pull=False)
                self.toast.emit("warning", msg)
            else:
                self._login_error = msg or "Не удалось войти."
            self.changed.emit()
        self._run(lambda: self.client.login(login, password), done)

    @Slot(str, str)
    def changePassword(self, current: str, new: str):
        def done(r):
            if r.status == "accepted":
                self.toast.emit("success", "Пароль изменён. Другие сеансы этого пользователя завершены.")
                self._screen = "main"
                self._refresh_after(lambda: None, pull=True)
            else:
                self.toast.emit("error", r.message_ru or "Пароль не изменён.")
            self.changed.emit()
        self._run(lambda: self.client.change_password(current, new), done)

    @Slot(str)
    def reconnect(self, password: str):
        def done(r):
            if r.status == "accepted":
                self._session_mode, self._online = "online", True
                self.toast.emit("success", "Связь с сервером восстановлена. Отправляем очередь…")
                self.syncNow()
            else:
                self.toast.emit("error", r.message_ru or "Не удалось подключиться.")
            self.changed.emit()
        self._run(lambda: self.client.relogin_online(password), done)

    @Slot()
    def logout(self):
        def done(_):
            self._screen, self._session_mode, self._me = "login", "none", {}
            self._data = {k: [] for k in self._data}
            self.changed.emit()
            self.dataChanged.emit()
        self._run(lambda: self.client.logout() if self.client else None, done)

    # ------------------------------------------------------------ синхронизация
    def _snapshot(self, pull: bool):
        c = self.client
        rep = None
        if pull and c.token:
            rep = c.sync()
        data = {k: c.list_local(k) for k in self._data}
        return {"data": data, "me": c.get_meta("me") or c.get_meta("session") or {}, "people": c.get_meta("people") or [], "assignments": c.get_meta("assignments") or [],
                "pending": c.pending_count(), "problems": c.problem_count(), "last_pull": c.get_meta("last_pull") or "",
                "online": c.t.online and c.token is not None, "token": c.token is not None, "rep": rep.__dict__ if rep else None}

    def _apply_snapshot(self, s):
        if not isinstance(s, dict) or "data" not in s:
            if isinstance(s, dict) and s.get("message_ru"):
                self.toast.emit("error", s["message_ru"])
            return
        self._data = s["data"]
        if s["me"] != self._me:
            self._perm_rev += 1
        self._me = s["me"]
        self._people = {p["id"]: p["display_name"] for p in s["people"]}
        self._assignments = s.get("assignments", [])
        self._pending, self._problems, self._last_pull = s["pending"], s["problems"], s["last_pull"]
        self._online = s["online"]
        if not s["token"] and self._session_mode == "online":
            self._session_mode = "offline"
            self.toast.emit("warning", "Сеанс с сервером завершён. Работа продолжается локально; войдите снова, чтобы отправить очередь.")
        self.changed.emit()
        self.dataChanged.emit()

    def _refresh_after(self, then, pull: bool):
        def done(s):
            self._apply_snapshot(s)
            then()
        self._run(lambda: self._snapshot(pull), done)

    @Slot()
    def syncNow(self):
        if not self.client or self._screen != "main":
            return

        def done(s):
            self._apply_snapshot(s)
            rep = (s or {}).get("rep") if isinstance(s, dict) else None
            if rep:
                if rep.get("stop_status") in ("conflict", "denied", "rejected"):
                    self.toast.emit("warning", "Синхронизация остановлена: есть изменение, требующее вашего решения (раздел «Синхронизация»).")
                elif rep.get("offline"):
                    self.toast.emit("info", "Нет связи с сервером. Изменения сохранены в очереди.")
                elif rep.get("accepted"):
                    self.toast.emit("success", f"Отправлено изменений: {rep['accepted']}.")
        self._run(lambda: self._snapshot(True), done)

    @Slot()
    def backgroundSync(self):
        if self.client and self._screen == "main" and not self._busy and self.client.token:
            self._run(lambda: self._snapshot(True), self._apply_snapshot)
        elif self.client and self._screen == "main" and not self._busy and self._session_mode == "offline":
            def probe():
                return {"ping": self.client.t.ping()}

            def done(r):
                was = self._online
                self._online = bool(r.get("ping"))
                if self._online and not was:
                    self.toast.emit("info", "Сервер снова доступен. Введите пароль, чтобы отправить очередь.")
                self.changed.emit()
            self._run(probe, done)

    # ------------------------------------------------------------ команды
    @Slot(str, str, "QVariantMap", int)
    def execute(self, tag: str, command: str, payload: dict, expected_version: int):
        ev = expected_version if expected_version > 0 else None
        # "" из пустого поля формы = «не указано» (ключ не передаётся); null = явная очистка значения при изменении.
        payload = {k: v for k, v in dict(payload).items() if v != ""}

        def job():
            r = self.client.execute(command, payload, ev)
            return {"result": r.__dict__, "snap": self._snapshot(False)}

        def done(res):
            if "result" not in res:
                self.toast.emit("error", res.get("message_ru", "Ошибка"))
                self.commandDone.emit(tag, res)
                return
            r = res["result"]
            self._apply_snapshot(res["snap"])
            st = r["status"]
            if st == "accepted":
                self.toast.emit("success", "Готово: " + self.contracts.label(command).lower() + ".")
            elif st == "queued":
                self.toast.emit("info", r["message_ru"])
            elif st == "conflict":
                self.toast.emit("warning", r["message_ru"])
            elif st in ("refused_offline", "blocked", "unknown_outcome"):
                self.toast.emit("warning", r["message_ru"])
            else:
                self.toast.emit("error", r.get("message_ru") or "Действие не выполнено.")
            self.commandDone.emit(tag, {"status": st, "code": (r.get("response") or {}).get("code", ""),
                                        "message_ru": r.get("message_ru", ""), "operation_id": r.get("operation_id") or "",
                                        "details": (r.get("response") or {}).get("details") or {},
                                        "result": (r.get("response") or {}).get("result") or {}})
        self._run(job, done)

    @Slot(result=str)
    def newId(self) -> str:
        return str(uuid.uuid4())

    @Slot(str, result=str)
    def moneyProblem(self, text: str) -> str:
        """Предварительная проверка поля суммы (сервер всё равно проверит)."""
        t = text.strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
        if not t:
            return ""
        try:
            d = Decimal(t)
        except InvalidOperation:
            return "Только цифры, не более 4 знаков после запятой."
        if not d.is_finite() or "e" in t.lower():
            return "Только цифры, не более 4 знаков после запятой."
        if -d.as_tuple().exponent > 4:
            return "Не более 4 знаков после запятой."
        if abs(d) >= Decimal("100000000000000"):
            return "Слишком большая сумма."
        return ""

    @Slot(str, result=str)
    def normalizeMoney(self, text: str) -> str:
        return text.strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")

    # ------------------------------------------------------------ очередь и конфликты
    @Property("QVariantList", notify=dataChanged)
    def queueItems(self):
        return self._queue

    @Slot()
    def loadQueue(self):
        def job():
            return {"queue": self.client.queue(), "problems": self.client.problems()}

        def done(r):
            probs = {p["operation_id"]: p for p in r.get("problems", [])}
            items = []
            for q in r.get("queue", []):
                p = probs.get(q["operation_id"], {})
                diff = []
                cur = p.get("server_current") or {}
                for k, v in (p.get("payload") or {}).items():
                    if k == "id":
                        continue
                    diff.append({"field": k, "field_label": FIELD_LABELS.get(k, k), "mine": "" if v is None else str(v), "server": "" if cur.get(k) is None else str(cur.get(k)),
                                 "differs": cur.get(k) != v})
                items.append({**q, "message_ru": p.get("message_ru", ""), "code": p.get("code", ""), "diff": diff,
                              "server_version": cur.get("version", 0)})
            self._queue = items
            self.dataChanged.emit()
        self._run(job, done)

    @Slot(str)
    def discard(self, op_id: str):
        self._run(lambda: (self.client.discard(op_id), self._snapshot(False))[1],
                  lambda s: (self._apply_snapshot(s), self.loadQueue(), self.toast.emit("info", "Ваше изменение отменено; показана версия сервера.")))

    @Slot(str)
    def reapply(self, op_id: str):
        def job():
            r = self.client.reapply_on_current(op_id)
            return {"r": r.__dict__, "snap": self._snapshot(False)}

        def done(res):
            self._apply_snapshot(res.get("snap"))
            self.loadQueue()
            r = res.get("r", {})
            self.toast.emit("success" if r.get("status") == "accepted" else "warning",
                            "Изменение применено поверх текущей версии." if r.get("status") == "accepted" else r.get("message_ru", ""))
        self._run(job, done)

    # ------------------------------------------------------------ запросы
    @Slot(str, str, str)
    def history(self, tag: str, object_type: str, object_id: str):
        self._run(lambda: self.client.query("history", object_type=object_type, object_id=object_id), lambda r: self.queryDone.emit(tag, r))

    @Slot(str)
    def loadUsers(self, tag: str):
        def done(r):
            if r.get("status") == "accepted":
                for u in r["result"]:
                    u["role_labels"] = [ROLE_LABELS.get(g["role"], g["role"]) for g in u["grants"] if g["active"]]
                    for g in u["grants"]:
                        g["label"] = ROLE_LABELS.get(g["role"], g["role"])
            self.queryDone.emit(tag, r)
        self._run(lambda: self.client.query("users"), done)


def format_money(amount: Optional[str], currency: Optional[str]) -> str:
    """Отображение суммы: десятичная строка → группы разрядов; неизвестно ≠ 0."""
    if amount is None:
        return "Сумма не указана"
    d = Decimal(amount)
    sign = "−" if d < 0 else ""
    q = abs(d)
    whole, _, frac = format(q, "f").partition(".")
    frac = frac.rstrip("0")
    if len(frac) < 2:
        frac = frac.ljust(2, "0")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return f"{sign}{' '.join(groups) or '0'},{frac} {currency or ''}".strip()
