"""Мост QML ↔ офлайн-ядро.

Все обращения к SyncClient идут через ОДИН рабочий поток (последовательно): сеть и диск не блокируют интерфейс,
а локальная БД используется из одного потока. Результаты возвращаются сигналами в поток интерфейса.
Права в интерфейсе — только подсказка (скрыть явно недоступное); решение всегда принимает сервер.
"""
from __future__ import annotations

import logging
import os
import platform
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Property, QObject, QSettings, QTimer, QUrl, Signal, Slot

from vector import __version__
from vector.core import stock
from vector.core.contracts import Contracts

from . import (calc_view, delegation_view, hints, lifecycle_view, notification_view, production_view, purchasing_view,
               quality_view, stock_view)
from .sync import STOCK_META, SyncClient
from .transport import HttpTransport
from .vault import KeyringStore, MemoryStore, VaultError

log = logging.getLogger("vector.client")

# Русские названия глобальных ролей (коды — из contracts/permissions). Внутренние коды пользователю не показываются.
ROLE_LABELS = {"admin": "Администратор", "gip": "ГИП", "rp": "РП", "dept_head": "Руководитель отдела",
               "lead_engineer": "Ведущий инженер", "engineer": "Инженер", "designer": "Конструктор", "technologist": "Технолог",
               "production": "Производство", "warehouse": "Склад", "procurement": "Закупка", "accountant": "Бухгалтерия",
               "po_approver": "Согласование закупок",
               "otk": "Технический контроль (ОТК)"}
# Русские названия полей команд — для экрана конфликта «моё / на сервере».
FIELD_LABELS = {"name": "Наименование", "short_name": "Краткое наименование", "inn": "ИНН", "kpp": "КПП", "note": "Примечание",
                "contact": "Контакт", "title": "Название", "deadline": "Срок подачи", "customer_id": "Заказчик", "tender_id": "Тендер",
                "amount": "Сумма", "currency": "Валюта", "number": "Номер", "code": "Шифр", "status": "Статус", "reason": "Основание",
                "channel": "Канал", "sent_at": "Дата отправки", "responsible_id": "Ответственный", "valid_until": "Действует до",
                "lines": "Позиции расчёта"}
# Русские названия ролей в проекте (назначения).
PROJECT_ROLE_LABELS = {"gip": "ГИП проекта", "rp": "РП проекта", "participant": "Участник"}


def default_data_dir() -> Path:
    r"""Каталог профилей клиента: в Windows — %LOCALAPPDATA%\VECTOR\profiles (локально для пользователя, не в
    перемещаемом профиле), в Linux — ~/.local/share/vector/profiles.
    """
    if platform.system() == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "VECTOR" / "profiles"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "vector" / "profiles"


def qml_safe(value: Any) -> Any:
    """Подготовить данные для передачи в QML: убрать None из словарей и списков (рекурсивно).

    Зачем: PySide6 6.12 при преобразовании dict → QVariantMap теряет одну ссылку на None за каждое
    поле со значением None (проверено: sys.getrefcount(None) убывает при каждом чтении свойства).
    При частых обновлениях счётчик None доходит до нуля и процесс аварийно завершается
    («Fatal Python error: none_dealloc»). Это ошибка привязки, а не нашего кода, но защищаться
    приходится нам.

    Семантика не теряется: в QML отсутствующий ключ даёт undefined, и интерфейс трактует его так же,
    как null, — «значение не задано». Различия «ноль / пусто / неизвестно» мы показываем через
    подготовленные Python-подписи (amount_label и т. п.), а не через null в QML.
    """
    if isinstance(value, dict):
        return {k: qml_safe(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [qml_safe(v) for v in value if v is not None]
    return value


def _clean_lines(lines) -> list[dict]:
    """Позиции расчёта из формы QML → payload: пустые поля = «не задано» (None), числа — строками как введены.

    Числа из полей ввода не превращаются во float: «1 234,5» → «1234.5» (пробелы убираются, запятая → точка),
    дальше строгая проверка vector.core.calc (не больше 4 знаков после запятой, без округления).
    """
    out = []
    for raw in lines or []:
        ln = {}
        for k in ("line_id", "code", "name", "unit", "qty", "unit_price", "note"):
            v = raw.get(k) if isinstance(raw, dict) else None
            if isinstance(v, str):
                v = v.strip()
                if k in ("qty", "unit_price"):
                    v = v.replace(" ", "").replace("\u00a0", "").replace(",", ".")
            if v in ("", None):
                v = None
            if v is not None or k in ("name", "unit", "qty", "unit_price"):
                ln[k] = v
        out.append(ln)
    return out


def _int_or_none(v: Any) -> Optional[int]:
    """Целое из поля формы или QML-числа: 5, 5.0, «5», « 5 » → 5; иначе (пусто, 5.5, «abc», True) — None."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    t = str(v).strip()
    return int(t) if re.fullmatch(r"-?\d{1,9}", t) else None


def _plain(v: Any) -> Any:
    """Данные формы QML → обычные dict / list / str / int (рекурсивно). Целые числа из JavaScript приходят как float
    (2.0) — превращаются в int, иначе сервер отклонит «номер версии — целое число»; дробные остаются float.
    """
    if hasattr(v, "toVariant"):
        v = v.toVariant()
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _plain_amount(v) -> Optional[str]:
    """Сумма из поля формы в виде для сервера: без пробелов, запятая → точка; пусто → None (не указано)."""
    if v is None:
        return None
    t = str(v).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    return t or None


def _clean_po_lines(lines) -> list[dict]:
    """Строки заказа поставщику из формы QML → payload (D-32).

    Как у расчёта: числа не превращаются во float («1 234,5» → «1234.5»); пустая цена = «неизвестна» (None), а
    не ноль; пустые ссылки на потребность не передаются. Строгая проверка — vector.core.purchasing.
    """
    out = []
    for raw in lines or []:
        raw = raw if isinstance(raw, dict) else {}
        ln = {}
        for k in ("line_id", "item_id", "qty", "unit_price", "need_id", "need_line_id", "note", "vat_rate"):
            v = raw.get(k)
            if isinstance(v, str):
                v = v.strip()
                if k in ("qty", "unit_price"):
                    v = v.replace(" ", "").replace("\u00a0", "").replace(",", ".")
            v = None if v in ("", None) else v
            if v is not None or k in ("qty", "unit_price"):
                ln[k] = v
        out.append(ln)
    return out


def _conflict_value(field: str, value, obj) -> str:
    """Значение поля для экрана конфликта. Позиции расчёта — кратко (число позиций и итог), а не сырой список."""
    if value is None:
        return ""
    if field == "lines" and isinstance(value, list) and any(isinstance(x, dict) and "item_id" in x for x in value):
        # Строки заказа поставщику (D-32): кратко — число строк (цены и итог видны в самом заказе).
        return f"{len(value)} стр. заказа"
    if field == "lines" and isinstance(value, list):
        pv = calc_view.preview(_clean_lines(value), (obj or {}).get("currency"))
        return f"{len(value)} поз. · {pv['total_label']}"
    return str(value)


def _ro(attr: str, typ, notify):
    """Фабрика свойства QML «только чтение» из атрибута объекта (на уровне модуля — так требует PySide6)."""
    return Property(typ, lambda self: getattr(self, attr), notify=notify)


def _lst(kind: str, notify):
    """Фабрика свойства-списка QML: объекты кэша типа kind с подписями для интерфейса (_decorate)."""
    return Property("QVariantList", lambda self: qml_safe(self._decorate(kind)), notify=notify)


class Backend(QObject):
    """Объект backend, доступный во всём QML: состояние сеанса, данные для экранов и слоты-действия.

    Сигналы: changed — сеанс, экран, флаги; dataChanged — данные; toast(kind, text) — уведомление; commandDone /
    queryDone(tag, result) — ответ на действие формы (tag — её метка). Все обращения к SyncClient выполняются в
    одном фоновом потоке (_run), а результаты возвращаются в поток интерфейса сигналом _deliver.
    """
    changed = Signal()
    dataChanged = Signal()
    toast = Signal(str, str)                 # kind (success|info|warning|error), text
    commandDone = Signal(str, "QVariant")    # request tag, result dict
    queryDone = Signal(str, "QVariant")
    _deliver = Signal(int, object)

    def __init__(self, client_factory: Optional[Callable[[str, str], SyncClient]] = None, settings: Optional[QSettings] = None,
                 auto_timers: bool = True):
        """client_factory и settings подменяются в тестах; auto_timers=False отключает фоновую синхронизацию (в боевом
        режиме — каждые 20 с).
        """
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
        self._data: dict[str, list] = {"customer": [], "tender": [], "tkp": [], "project": [], "kit": [], "task": [], "document": [],
                                       "calc_draft": [], "warehouse": [], "stock_item": [], "supplier": [], "purchase_order": [],
                                       "supplier_contract": [], "tech_process": [], "work_order": [], "wo_operation": [],
                                       "product_instance": [], "passport": [],
                                       "claim": [], "change_request": [], "change_order": []}
        # Фильтр вкладки «Заказы поставщикам» (active / approval / draft / placed / closed / all) — состояние экрана.
        self._po_filter = "active"
        # Наибольший id уведомления, о котором уже сказали (None — ещё не было снимка после входа, D-35).
        self._notify_seen: Optional[int] = None
        # Склад: снимок остатков/резервов/журнала/потребностей на момент последнего обновления (meta кэша).
        self._stock: dict = {}
        # Метаданные версий документов (номер, имя файла, размер, SHA-256) из зашифрованного кэша клиента.
        # Сами файлы в кэш не попадают: их скачивают по явной команде пользователя.
        self._doc_versions: list = []
        # Расчёт: зафиксированные версии комплектов и ревизии проектного итога (неизменяемые, из meta кэша).
        self._calc_versions: list = []
        self._calc_revisions: list = []
        self._data_rev = 0
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
        """Создать SyncClient для адреса сервера: секрет устройства в хранилище Windows (без него — отказ; переменная
        VECTOR_DEV_INSECURE_STORE=1 только для разработки), постоянный id устройства из настроек, HTTPS-транспорт.
        """
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
        """Выполнить fn в рабочем потоке, а done(результат) — в потоке интерфейса. Пока есть незавершённые задания,
        busy=true. Исключение не роняет программу: пользователь видит сообщение, подробности уходят в журнал.
        """
        self._seq += 1
        n = self._seq
        self._callbacks[n] = done
        self._set_busy(True)

        def job():
            """Тело задания в рабочем потоке: вызвать fn, перехватить ошибки, отправить результат сигналом."""
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
        """Принять результат задания в потоке интерфейса и вызвать его обработчик."""
        cb = self._callbacks.pop(n, None)
        if not self._callbacks:
            self._set_busy(False)
        if cb:
            cb(res)

    def _set_busy(self, v):
        """Изменить флаг «идёт работа» (индикатор в шапке)."""
        if self._busy != v:
            self._busy = v
            self.changed.emit()

    # Все данные для QML уходят через qml_safe (см. её описание): сигналы — тоже.
    def _command_done(self, tag: str, result: Any) -> None:
        """Сигнал commandDone с данными, безопасными для QML."""
        self.commandDone.emit(tag, qml_safe(result))

    def _query_done(self, tag: str, result: Any) -> None:
        """Сигнал queryDone с данными, безопасными для QML."""
        self.queryDone.emit(tag, qml_safe(result))

    def shutdown(self):
        """Дождаться завершения рабочего потока при выходе из программы."""
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
        """Тема интерфейса: «light» или «dark»; сохраняется в настройках пользователя."""
        return self._theme

    @theme.setter
    def theme(self, v):
        """Сменить тему (light/dark): сохраняется в настройках клиента, интерфейс перекрашивается сразу."""
        if v in ("light", "dark") and v != self._theme:
            self._theme = v
            self._settings.setValue("ui/theme", v)
            self.changed.emit()

    @Property(str, constant=True)
    def version(self):
        """Версия программы (для окна «О программе» и журнала)."""
        return __version__

    # Поля входа можно заполнить заранее переменными окружения VECTOR_SERVER_URL и VECTOR_CA_FILE — так делает
    # ярлык тестового стенда (deploy/test-stand). Переменная важнее сохранённого значения: ярлык стенда — источник
    # истины о его адресе и сертификате. Это только подстановка в форму: пароль так не передаётся никогда.
    @Property(str, notify=changed)
    def savedServer(self):
        """Адрес сервера для формы входа."""
        return os.environ.get("VECTOR_SERVER_URL") or self._settings.value("server/url", "")

    @Property(str, notify=changed)
    def savedLogin(self):
        """Последний логин для формы входа."""
        return self._settings.value("server/login", "")

    @Property(str, notify=changed)
    def savedCa(self):
        """Путь к файлу корневого сертификата для формы входа."""
        return os.environ.get("VECTOR_CA_FILE") or self._settings.value("server/ca", "")

    @Property("QVariantMap", notify=changed)
    def me(self):
        """Текущий пользователь: id, имя, глобальные роли с русскими подписями (полученные замещением — с пометкой
        «замещение до …», D-34), инициалы для аватара."""
        return qml_safe({**self._me, "role_labels": delegation_view.role_labels(self._me, ROLE_LABELS),
                "initials": "".join(w[:1] for w in (self._me.get("display_name") or "?").split()[:2]).upper()})

    customers = _lst("customer", dataChanged)
    tenders = _lst("tender", dataChanged)
    tkps = _lst("tkp", dataChanged)
    projects = _lst("project", dataChanged)
    kits = _lst("kit", dataChanged)
    tasks = _lst("task", dataChanged)
    documents = _lst("document", dataChanged)

    @Property("QVariantList", notify=dataChanged)
    def people(self):
        """Люди (id и имя), отсортированные по имени, — для выпадающих списков исполнителей."""
        return [{"id": k, "display_name": v} for k, v in sorted(self._people.items(), key=lambda kv: kv[1])]

    @Property("QVariantList", notify=dataChanged)
    def assignments(self):
        """Назначения в проектах с именами людей и русскими названиями проектных ролей."""
        return qml_safe([{**a, "user_name": self._people.get(a["user_id"], "—"), "role_label": PROJECT_ROLE_LABELS.get(a["project_role"], a["project_role"])}
                         for a in self._assignments])

    @Property("QVariantList", constant=True)
    def roleOptions(self):
        """Глобальные роли из контракта прав с русскими подписями (для админки). Сокращения в списке выбора
        расшифрованы (D-37): «ГИП — главный инженер проекта»."""
        return [{"id": r, "label": hints.with_expansion(ROLE_LABELS.get(r, r))} for r in self.contracts.permissions["roles"]]

    @Property("QVariantList", constant=True)
    def projectRoleOptions(self):
        """Роли в проекте (ГИП, РП, участник) из контракта прав с подписями; сокращения расшифрованы (D-37)."""
        return [{"id": r, "label": hints.with_expansion(PROJECT_ROLE_LABELS.get(r, r))}
                for r in self.contracts.permissions["project_roles"]]

    def _decorate(self, kind):
        """Объекты кэша типа kind + всё, что нужно экрану: подписи статусов из контракта workflows, допустимые
        переходы, имена связанных объектов и людей, суммы в русском формате, признак «не отправлено».
        """
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
            if kind == "kit":
                # Счётчики для карточки комплекта: «открытые» = ещё не выполненные и не отменённые задачи.
                ks = [t for t in self._data["task"] if t.get("kit_id") == o["id"]]
                d["task_count"] = len(ks)
                d["open_task_count"] = sum(1 for t in ks if t.get("status") in ("open", "in_progress"))
                d["doc_count"] = sum(1 for x in self._data["document"] if x.get("kit_id") == o["id"])
                vs = [v["version_no"] for v in self._calc_versions if v.get("kit_id") == o["id"]]
                dr = next((x for x in self._data["calc_draft"] if x["id"] == o["id"]), None)
                d["calc_label"] = (f"расчёт v{max(vs)}" if vs else "расчёт не зафиксирован") + \
                    (" · есть правки" if dr and vs and (dr.get("_pending") or dr.get("version", 0) >
                                                        max(v["draft_version"] for v in self._calc_versions if v.get("kit_id") == o["id"])) else "")
            if kind == "task":
                d.update(self._decorate_task(o))
            if kind == "document":
                d.update(self._decorate_document(o))
            out.append(d)
        if kind == "task":
            # Сначала то, что требует работы (в работе → открытые → выполненные → отменённые), внутри — по названию.
            order = {"in_progress": 0, "open": 1, "done": 2, "cancelled": 3}
            return sorted(out, key=lambda d: (order.get(d.get("status"), 9), str(d.get("title") or "").lower()))
        key = {"customer": "name", "tender": "title", "tkp": "label", "project": "code", "kit": "code", "document": "code",
               "warehouse": "code", "stock_item": "code", "supplier": "code",
               # Порядок заказов задаёт purchasing_view.orders_view (по номеру); здесь — любой стабильный.
               "purchase_order": "id", "supplier_contract": "number"}[kind]
        return sorted(out, key=lambda d: (str(d.get(key) or "")).lower())

    def _decorate_task(self, o: dict) -> dict:
        """Поля задачи для интерфейса: подписи статуса и исполнителя, доступные переходы.

        Переходы — ПОДСКАЗКА, а не право: ГИП/РП (по глобальной роли) видят все переходы из workflows,
        исполнитель — только «свои» (own_transitions, A-13). Роли в проекте клиенту известны не всегда,
        поэтому при сомнении кнопка показывается, а окончательное решение принимает сервер
        (отказ придёт понятным сообщением и попадёт в аудит).
        """
        wf = self.contracts.workflows["task"]
        st = o.get("status")
        mine = bool(o.get("assignee_user_id")) and o.get("assignee_user_id") == self._me.get("user_id")
        if self.hint("task.transition", 0) or self._has_project_role(o.get("project_id"), ("gip", "rp")):
            allowed = wf["transitions"].get(st, [])
        elif mine:
            allowed = wf["own_transitions"].get(st, [])
        else:
            allowed = []
        nxt = []
        for to in allowed:
            reopen = st == "done" and to == "open"
            need_reason = to in wf["reason_required_for"] or (reopen and "reopen" in wf["reason_required_for"])
            label = {"in_progress": "Взять в работу", "done": "Выполнено", "cancelled": "Отменить",
                     "open": "Открыть заново" if reopen else "Вернуть в открытые"}.get(to, wf["labels_ru"].get(to, to))
            nxt.append({"id": to, "label": label, "reason": need_reason})
        doc = next((x for x in self._data["document"] if x["id"] == o.get("document_id")), None)
        return {"status_label": wf["labels_ru"].get(st, st or "—"),
                "assignee_name": self._people.get(o.get("assignee_user_id") or "", "") if o.get("assignee_user_id") else "",
                "mine": mine, "next": nxt, "terminal": st in wf["terminal"],
                "document_code": doc["code"] if doc else ""}

    def _decorate_document(self, o: dict) -> dict:
        """Поля документа: список версий из кэша (новые сверху) и подпись текущей версии."""
        vs = sorted((v for v in self._doc_versions if v.get("document_id") == o["id"]),
                    key=lambda v: v["version_no"], reverse=True)
        vs = [{**v, "size_label": format_size(v.get("size_bytes")), "author_name": self._people.get(v.get("created_by") or "", ""),
               "created_label": (v.get("created_at") or "")[:16].replace("T", " ")} for v in vs]
        cur = o.get("current_version_no") or 0
        return {"versions": vs, "current_label": f"версия {cur}" if cur else "файла ещё нет"}

    def _has_project_role(self, project_id, roles) -> bool:
        """Есть ли у меня действующее назначение в проекте с одной из ролей (по кэшу назначений)."""
        uid = self._me.get("user_id")
        return any(a.get("project_id") == project_id and a.get("user_id") == uid and a.get("project_role") in roles
                   for a in self._assignments)

    def _name(self, kind, oid, field="name"):
        """Поле (по умолчанию name) объекта типа kind по id из кэша или пустая строка."""
        return next((o.get(field) for o in self._data[kind] if o["id"] == oid), "")

    # ------------------------------------------------------------ расчёт комплекта и проектный итог (С3)
    @Property(int, notify=dataChanged)
    def dataRevision(self):
        """Счётчик обновлений данных: QML передаёт его в слоты-вычисления, чтобы привязки пересчитывались."""
        return self._data_rev

    @Slot(str, int, int, result="QVariantMap")
    def kitCalc(self, kit_id: str, compare_no: int = 0, _rev: int = 0) -> dict:
        """Вкладка «Расчёт»: черновик (с неотправленными правками), версии, сравнение, подсказки прав."""
        kit = next((k for k in self._data["kit"] if k["id"] == kit_id), None)
        if kit is None:
            return {}
        draft = next((x for x in self._data["calc_draft"] if x["id"] == kit_id), None)
        v = calc_view.kit_calc(kit, draft, self._calc_versions, self._people, compare_no)
        pid = kit.get("project_id")
        # Подсказки (решает сервер): фиксирует только ГИП проекта; черновик — участники проекта.
        v["can_freeze"] = self._has_project_role(pid, ("gip",))
        v["can_edit"] = self._has_project_role(pid, ("gip", "rp", "participant")) or not self._assignments_known(pid)
        v["can_see_revisions"] = self._has_project_role(pid, ("gip", "rp")) or "accountant" in self._me.get("roles", [])
        return qml_safe(v)

    @Slot("QVariantList", str, result="QVariantMap")
    def calcPreview(self, lines, currency: str) -> dict:
        """Проверка рабочей копии позиций в форме (без сервера): суммы, замечания по строкам, итог."""
        lines = lines.toVariant() if hasattr(lines, "toVariant") else lines
        return qml_safe(calc_view.preview(_clean_lines(lines), currency.strip().upper() or None))

    @Slot(str, int, result="QVariantMap")
    def projectCalc(self, project_id: str, _rev: int = 0) -> dict:
        """Проектный итог (ревизии) для карточки проекта. _rev — dataRevision, чтобы привязка QML пересчитывалась.
        """
        return qml_safe(calc_view.project_calc(project_id, self._calc_revisions, self._people))

    @Property("QVariantList", notify=dataChanged)
    def financeOverview(self):
        """Итоги проектов (официальные — только из зафиксированных версий): для ГИП/РП и бухгалтерии."""
        return qml_safe(calc_view.finance_overview(self._calc_revisions, self._people))

    # ------------------------------------------------------------ склад и закупка (С4)
    warehouses = _lst("warehouse", dataChanged)
    stockItems = _lst("stock_item", dataChanged)

    @Property("QVariantMap", notify=dataChanged)
    def stockOverview(self):
        """Экран «Склад»: остатки по позициям и складам, резервы, журнал — снимок «на момент …»."""
        st = self._stock
        return qml_safe(stock_view.overview(
            self._decorate("warehouse"), self._decorate("stock_item"), st.get("stock_balances") or [],
            st.get("stock_reservations") or [], st.get("stock_movements") or [], st.get("stock_projects") or [],
            self._people, self._last_pull, self._online and self._session_mode == "online", self._me.get("roles", []),
            st.get("stock_transfers") or [], st.get("config") or []))

    @Property("QVariantMap", notify=dataChanged)
    def needsOverview(self):
        """Экран «Закупка»: потребности из зафиксированных версий и их покрытие складом."""
        st = self._stock
        return qml_safe(stock_view.needs_view(
            st.get("needs") or [], st.get("need_candidates") or [], self._decorate("stock_item"), self._last_pull,
            self._online and self._session_mode == "online", self._me.get("roles", []),
            st.get("unit_conversions") or [], st.get("config") or []))

    @Property("QVariantMap", notify=dataChanged)
    def directoryOverview(self):
        """Вкладка «Справочник» раздела «Склад»: другие обозначения позиций и пересчёт единиц (D-25, A-27)."""
        st = self._stock
        return qml_safe(stock_view.directory_view(
            st.get("item_aliases") or [], st.get("unit_conversions") or [], self._decorate("stock_item"),
            self._me.get("roles", []), self._online and self._session_mode == "online"))

    @Property("QVariantMap", notify=dataChanged)
    def settingsOverview(self):
        """Настройки компании для раздела «Администрирование» (D-26, A-26): значение, кто/когда/почему задал."""
        return qml_safe(stock_view.settings_view(self._stock.get("config") or [], self._me.get("roles", []),
                                                 self._online and self._session_mode == "online"))

    # ------------------------------------------------------------ заказы поставщикам и поставщики (D-32, D-33)
    @Property("QVariantMap", notify=dataChanged)
    def ordersOverview(self):
        """Вкладка «Заказы поставщикам»: черновики (в т. ч. не отправленные), согласование, размещённые,
        «поставлено / ждём», НДС раздельно (D-33)."""
        return qml_safe(purchasing_view.orders_view(
            self._decorate("purchase_order"), self._decorate("supplier"), self._decorate("stock_item"),
            self._stock.get("needs") or [], self._me.get("roles", []), self._online and self._session_mode == "online",
            self._last_pull, self._po_filter, self._decorate("supplier_contract"), self._people,
            self._me.get("user_id") or ""))

    @Property("QVariantMap", notify=dataChanged)
    def delegationsOverview(self):
        """Раздел «Замещения» (D-34): мои замещения, все — для руководителя отдела и администратора, покрытие ролей."""
        return qml_safe(delegation_view.delegations_view(
            self._stock.get("delegations") or [], self._stock.get("role_coverage") or [], self._people, self._me,
            ROLE_LABELS, self._online and self._session_mode == "online", self.hint("delegation.manage", 0),
            self._assignments, {p["id"]: p.get("code") or "" for p in self._data.get("project", [])}))

    @Property("QVariantList", notify=dataChanged)
    def projectDeputies(self):
        """Кто сейчас замещает в проектах (D-35) — для списка команды проекта: имя, за кого, роль, до какого числа."""
        return qml_safe([{**d, "user_name": self._people.get(d["user_id"], "—"),
                          "for_name": self._people.get(d["for_user_id"], "—"),
                          "role_label": PROJECT_ROLE_LABELS.get(d["project_role"], d["project_role"]),
                          "until_label": delegation_view._day(d.get("valid_until"))}
                         for d in self._stock.get("project_deputies") or []])

    # ------------------------------------------------------------ уведомления (D-35)
    @Property("QVariantMap", notify=dataChanged)
    def notificationsOverview(self):
        """Раздел «Уведомления»: свои уведомления (новые сверху), число непрочитанных, можно ли отметить."""
        return qml_safe(notification_view.notifications_view(
            self._stock.get("notifications") or [], self._people, self._online and self._session_mode == "online",
            datetime.now(timezone.utc)))

    @Slot(str, "QVariantList")
    def markNotificationsRead(self, tag: str, ids):
        """Отметить прочитанными уведомления ids (только при связи). Числа из QML приходят как float — приводим."""
        clean = [int(i) for i in (ids.toVariant() if hasattr(ids, "toVariant") else ids) or []]
        if clean:
            self.execute(tag, "notification.read", {"ids": clean}, 0)

    @Slot(str)
    def markAllNotificationsRead(self, tag: str):
        """«Прочитать все» (только при связи)."""
        self.execute(tag, "notification.read", {"all": True}, 0)

    @Slot(str, result=str)
    def orderFilterFor(self, order_id: str) -> str:
        """Фильтр списка заказов, в котором виден заказ order_id (для перехода из уведомления): закрытые и
        отменённые — «Архив», остальные — «В работе»."""
        o = next((x for x in self._data.get("purchase_order", []) if x.get("id") == order_id), None)
        return "closed" if o and o.get("status") in ("closed", "cancelled") else "active"

    def _announce_notifications(self):
        """После снимка: сообщить всплывающей подсказкой о новых непрочитанных уведомлениях (не о тех, что были к
        моменту входа — их показывает бейдж «Уведомления»)."""
        items = self._stock.get("notifications") or []
        top = max([int(n.get("id") or 0) for n in items] or [0])
        if self._notify_seen is None:
            self._notify_seen = top
            return
        text = notification_view.toast_text(notification_view.new_since(items, self._notify_seen))
        self._notify_seen = max(self._notify_seen, top)
        if text:
            self.toast.emit("info", text)

    # ------------------------------------------------------------ технология и производство (D-36)
    @Property("QVariantMap", notify=dataChanged)
    def productionOverview(self):
        """Раздел «Производство»: мои операции (исполнителю, офлайн), наряды, техпроцессы — снимок кэша."""
        st = self._stock
        return qml_safe(production_view.production_view(
            self._data.get("tech_process", []), st.get("tech_specs") or [], self._data.get("work_order", []),
            self._data.get("wo_operation", []), st.get("production_staff") or [], self._data.get("project", []),
            self._data.get("kit", []), self._assignments, st.get("project_deputies") or [], self._people, self._me,
            self._online and self._session_mode == "online", self._last_pull, prod_kits=st.get("production_kits") or []))

    @Slot(int, str, str, result=str)
    def opTimer(self, fact_seconds: int, running_since: str, state: str) -> str:
        """Табло таймера операции на «сейчас» (QML вызывает раз в секунду, пока операция в работе)."""
        return production_view.op_timer_label(fact_seconds, running_since, state)

    @Slot(str, "QVariantMap")
    def saveTpDraft(self, tag: str, payload):
        """«Сохранить ТП»: через очередь (можно без связи); создание или правка и expected_version — решает
        SyncClient.save_tp_draft. Числа из QML приходят как float — номер версии расчёта приводится к целому."""
        p = dict(payload.toVariant() if hasattr(payload, "toVariant") else payload)
        p["title"] = (p.get("title") or "").strip()
        p["note"] = (p.get("note") or "").strip() or None
        p["spec_version_id"] = _int_or_none(p.get("spec_version_id"))
        p["operations"] = production_view.clean_operations(
            [dict(o.toVariant() if hasattr(o, "toVariant") else o) for o in p.get("operations") or []])

        def job():
            """Сохранить черновик ТП в рабочем потоке и снять снимок данных."""
            r = self.client.save_tp_draft(p)
            return {"result": r.__dict__, "snap": self._snapshot(False)}
        self._run(job, lambda res: self._finish_command(tag, "tp.update", res))

    @Slot("QVariantList", result="QVariantMap")
    def tpOpsCheck(self, rows) -> dict:
        """Проверка операций в форме ТП (без сервера, правила ядра): замечания по строкам и норма на изделие."""
        rows = rows.toVariant() if hasattr(rows, "toVariant") else rows
        return qml_safe(production_view.operations_problems([dict(r) for r in rows or []]))

    @Slot(str, str, str, str, str)
    def opEvent(self, tag: str, op_id: str, kind: str, qty_done: str, note: str):
        """Кнопка исполнителя (старт / пауза / продолжить / завершить / проблема): событие с временем этого
        компьютера — в очередь (можно без связи, D-36). qty_done — «сколько изделий выполнено» при завершении
        (пусто — весь наряд)."""
        q = _int_or_none(qty_done) if (qty_done or "").strip() else None
        if (qty_done or "").strip() and q is None:
            self._command_done(tag, {"status": "rejected", "message_ru": "«Выполнено» — целое число изделий."})
            return

        def job():
            """Поставить событие в очередь (и сразу отправить, если есть связь) и снять снимок данных."""
            r = self.client.op_event(op_id, kind, q, (note or "").strip() or None)
            return {"result": r.__dict__, "snap": self._snapshot(False)}
        self._run(job, lambda res: self._finish_command(tag, "wo.op_event", res))

    @Slot(str, str, str, "QVariantMap")
    def releaseWo(self, tag: str, tp_id: str, qty: str, assignees):
        """«Выпустить наряд» (только при связи): ТП, количество изделий (целое), исполнители по номерам операций."""
        n = _int_or_none(qty)
        if n is None:
            self._command_done(tag, {"status": "rejected", "message_ru": "Количество изделий — целое число."})
            return
        a = dict(assignees.toVariant() if hasattr(assignees, "toVariant") else assignees)
        clean = {str(_int_or_none(k) if _int_or_none(k) is not None else k): v for k, v in a.items() if v}
        self.execute(tag, "wo.release", {"id": self.newId(), "tp_id": tp_id, "qty": n, "assignees": clean}, 0)

    # ------------------------------------------------------------ экземпляры изделий и паспорт (D-38)
    @Property("QVariantMap", notify=dataChanged)
    def lifecycleOverview(self):
        """Раздел «Изделия и паспорта»: экземпляры с точными связями, паспорт-черновик, история эксплуатации."""
        st = self._stock
        return qml_safe(lifecycle_view.lifecycle_view(
            self._data.get("product_instance", []), self._data.get("passport", []), st.get("instance_work_orders") or [],
            st.get("passport_documents") or [], self._assignments, st.get("project_deputies") or [], self._people, self._me,
            self._online and self._session_mode == "online", self._last_pull))

    @Slot(str, str, str, str)
    def registerInstances(self, tag: str, work_order_id: str, series: str, serials: str):
        """«Зарегистрировать экземпляры» (только при связи): серия и заводские номера (по одному на строке).
        Ввод проверяется здесь теми же правилами ядра, что и на сервере, — ошибку видно сразу."""
        from vector.core import lifecycle as lc
        try:
            s, nums = lc.parse_serials(series, serials)
        except lc.LifecycleError as e:
            self._command_done(tag, {"status": "rejected", "message_ru": e.message_ru})
            return
        self.execute(tag, "instance.register", {"work_order_id": work_order_id, "series": s,
                                                "items": [{"id": self.newId(), "serial_no": n} for n in nums]}, 0)

    @Slot("QVariantMap", "QVariantList", result="QVariantMap")
    def passportCheck(self, fields, tests) -> dict:
        """Проверка формы паспорта без сервера: замечания по строкам испытаний."""
        f = dict(fields.toVariant() if hasattr(fields, "toVariant") else fields or {})
        t = [dict(x.toVariant() if hasattr(x, "toVariant") else x) for x in (tests.toVariant() if hasattr(tests, "toVariant") else tests) or []]
        return qml_safe(lifecycle_view.passport_check(f, t))

    @Slot(str, "QVariantMap")
    def savePassport(self, tag: str, payload):
        """«Сохранить паспорт»: через очередь (можно без связи); создание или правка и expected_version — решает
        SyncClient.save_passport_draft. payload — {id, instance_id, fields, tests, documents}."""
        p = dict(payload.toVariant() if hasattr(payload, "toVariant") else payload)
        body = lifecycle_view.passport_payload(dict(p.get("fields") or {}),
                                               [dict(x) for x in p.get("tests") or []], [dict(x) for x in p.get("documents") or []])
        data = {"id": p["id"], "instance_id": p.get("instance_id"), **body}

        def job():
            """Сохранить черновик паспорта в рабочем потоке и снять снимок данных."""
            r = self.client.save_passport_draft(data)
            return {"result": r.__dict__, "snap": self._snapshot(False)}
        self._run(job, lambda res: self._finish_command(tag, "passport.save", res))

    @Slot(str, str, int, str)
    def exportPassport(self, tag: str, passport_id: str, version: int, dest_url: str):
        """«Черновой экспорт» паспорта (только при связи, только РП проекта): сервер журналирует экспорт и отдаёт HTML
        с пометкой «Черновик, не сертификат»; файл сохраняется в выбранное место."""
        dest = Path(QUrl(dest_url).toLocalFile() if "://" in dest_url else dest_url)

        def job():
            """Экспорт в рабочем потоке: команда, затем запись файла (только если сервер принял)."""
            r = self.client.execute("passport.export_draft", {"id": passport_id}, int(version) or None)
            if r.status == "accepted":
                x = (r.response.get("result") or {}).get("export") or {}
                try:
                    dest.write_text(x.get("html") or "", encoding="utf-8")
                except OSError as e:
                    return {"status": "error", "message_ru": f"Экспорт выполнен (№ {x.get('export_no')}), но файл не записан: {e}."}
            return {"result": r.__dict__, "snap": self._snapshot(False)}

        self._run(job, lambda res: self._file_done(tag, res, f"Черновик паспорта сохранён: {dest.name}. Это не сертификат."))

    # ------------------------------------------------------------ качество: рекламации, CAPA, ECR, ECO (D-39)
    # Команды качества идут через общий execute (tag, команда, payload, версия); здесь — данные раздела, проверка форм
    # без сервера и сохранение черновика извещения (очередь с цепочкой версий).
    @Property("QVariantMap", notify=dataChanged)
    def qualityOverview(self):
        """Раздел «Качество»: рекламации и CAPA, запросы и извещения об изменении, варианты для форм."""
        st = self._stock
        return qml_safe(quality_view.quality_view(
            self._data.get("claim", []), self._data.get("change_request", []), self._data.get("change_order", []),
            self._data.get("product_instance", []), st.get("quality_refs") or [], self._data.get("project", []),
            self._data.get("kit", []), self._assignments, st.get("project_deputies") or [], self._people, self._me,
            self._online and self._session_mode == "online", self._last_pull,
            # D-40: проекты качества, сотрудники ОТК (отдел технического контроля), наряды для рекламации на партию.
            st.get("quality_projects") or [], st.get("quality_otk") or {}, st.get("claim_batches") or []))

    @Slot(str, "QVariantMap", result="QVariantMap")
    def qualityCheck(self, kind: str, payload) -> dict:
        """Проверка формы качества без сервера (те же правила ядра): kind — claim / actions / classify / due / respond /
        ecr / check / eco."""
        p = payload.toVariant() if hasattr(payload, "toVariant") else payload
        return qml_safe(quality_view.quality_check(kind, _plain(p)))

    # Команды качества, которые форма может отправить через qualityCommand (остальные — отдельными слотами).
    _QUALITY_COMMANDS = frozenset({"claim.register", "claim.start_analysis", "claim.plan", "claim.action_done",
                                   "claim.submit_check", "claim.check", "ecr.submit", "ecr.assess", "ecr.decide",
                                   "eco.approve", "eco.implement", "eco.verify",
                                   # D-40: классификация дефекта, срок и ответ заказчику.
                                   "claim.classify", "claim.set_due", "claim.respond"})

    @Slot(str, str, "QVariantMap", int)
    def qualityCommand(self, tag: str, command: str, payload, version: int):
        """Команда качества из формы: данные приводятся к обычным типам (_plain — целые остаются целыми), новые
        объекты получают id здесь; дальше — общий execute (очередь или онлайн по каталогу команд)."""
        if command not in self._QUALITY_COMMANDS:
            self._command_done(tag, {"status": "rejected", "message_ru": "Неизвестная команда качества."})
            return
        p = _plain(payload)
        if command in ("claim.register", "ecr.submit") and not p.get("id"):
            p["id"] = self.newId()
        if command == "claim.register":
            # Пустые необязательные поля формы не отправляются (охват определяет, какой объект нужен; категория и
            # срок ответа могут быть не заданы).
            p = {k: v for k, v in p.items() if not (k in ("instance_id", "work_order_id", "project_id", "category",
                                                           "response_due", "reference") and v in ("", None))}
        if command == "claim.classify" and not p.get("category"):
            p.pop("category", None)
        if command == "claim.plan":
            p["actions"] = [{**a, "id": a.get("id") or self.newId()} for a in p.get("actions") or []]
        self.execute(tag, command, p, int(version))

    @Slot(str, "QVariantMap")
    def saveEco(self, tag: str, payload):
        """«Сохранить извещение»: через очередь (можно без связи); создание или правка — решает
        SyncClient.save_eco_draft. payload — {id, ecr_id, title, description, items}."""
        p = _plain(payload.toVariant() if hasattr(payload, "toVariant") else payload)
        items = []
        for it in p.get("items") or []:
            if it.get("kind") == "document":
                items.append({"kind": "document", "document_id": str(it.get("document_id")), "from_version": _int_or_none(it.get("from_version")) or 0})
            elif it.get("kind") == "tech_process":
                items.append({"kind": "tech_process", "tp_id": str(it.get("tp_id"))})
        data = {"id": p["id"], "ecr_id": p.get("ecr_id"), "title": (p.get("title") or "").strip(),
                "description": (p.get("description") or "").strip(), "items": items}
        if not data["title"]:
            data.pop("title")

        def job():
            """Сохранить черновик извещения в рабочем потоке и снять снимок данных."""
            r = self.client.save_eco_draft(data)
            return {"result": r.__dict__, "snap": self._snapshot(False)}
        self._run(job, lambda res: self._finish_command(tag, "eco.save", res))

    @Property("QVariantMap", notify=dataChanged)
    def suppliersOverview(self):
        """Вкладка «Поставщики»: справочник с реквизитами и договорами (ведёт закупка, A-34)."""
        return qml_safe(purchasing_view.suppliers_view(self._decorate("supplier"), self._me.get("roles", []),
                                                       self._decorate("supplier_contract")))

    @Slot(str)
    def setOrdersFilter(self, flt: str):
        """Сменить фильтр списка заказов (active / approval / draft / placed / closed / all)."""
        self._po_filter = flt if flt in ("active", "approval", "draft", "placed", "closed", "all") else "active"
        self.dataChanged.emit()

    @Slot("QVariantList", str, str, str, str, result="QVariantMap")
    def poPreview(self, lines, currency: str, vat_mode: str = "", invoice_gross: str = "", invoice_vat: str = "") -> dict:
        """Проверка строк черновика заказа в форме (без сервера): суммы и НДС строк, замечания, итоги, чего не
        хватает для согласования; с D-34 — контроль расчёта и сверка с суммами из счёта поставщика."""
        lines = lines.toVariant() if hasattr(lines, "toVariant") else lines
        return qml_safe(purchasing_view.preview(_clean_po_lines(lines), currency.strip().upper() or None,
                                                self._decorate("stock_item"), vat_mode or None,
                                                _plain_amount(invoice_gross), _plain_amount(invoice_vat)))

    @Slot(str, str, result=str)
    def invoiceProblem(self, text: str, field: str) -> str:
        """Проверка суммы из счёта поставщика в форме: неотрицательная, в рублях и копейках (D-34)."""
        return purchasing_view.invoice_problem(text, field)

    @Slot(str, result=str)
    def innProblem(self, text: str) -> str:
        """Проверка ИНН в форме поставщика (10/12 цифр с контрольной суммой; пусто — можно)."""
        return purchasing_view.inn_problem(text)

    @Slot("QVariantMap", str, result="QVariantMap")
    def requisitesProblems(self, fields, inn: str) -> dict:
        """Проверка реквизитов в форме поставщика (КПП, ОГРН, БИК, счета — те же правила, что на сервере):
        {поле: замечание}; пустой словарь — замечаний нет."""
        f = fields.toVariant() if hasattr(fields, "toVariant") else fields
        return purchasing_view.requisites_problems(dict(f or {}), inn)

    @Slot(str, "QVariantMap")
    def savePoDraft(self, tag: str, payload):
        """«Сохранить черновик заказа»: через очередь (можно без связи). Создание или правка и expected_version —
        решает SyncClient.save_po_draft.
        """
        p = dict(payload.toVariant() if hasattr(payload, "toVariant") else payload)
        p["lines"] = _clean_po_lines(p.get("lines") or [])
        # Пустые поля формы → None («не указано»); договор и режим цен (D-33) — так же.
        for k in ("currency", "supplier_ref", "expected_on", "note", "contract_id", "vat_mode"):
            v = (p.get(k) or "").strip() if isinstance(p.get(k), str) else p.get(k)
            p[k] = (v.upper() if k == "currency" else v) or None
        # Суммы из счёта поставщика (D-34): «1 220,50» → «1220.50»; пусто — не введены (сверки нет).
        for k in ("invoice_gross", "invoice_vat"):
            if k in p:
                p[k] = _plain_amount(p.get(k))

        def job():
            """Сохранить черновик в рабочем потоке и снять снимок данных."""
            r = self.client.save_po_draft(p)
            return {"result": r.__dict__, "snap": self._snapshot(False)}
        self._run(job, lambda res: self._finish_command(tag, "po.update", res))

    @Slot(str, result=str)
    def qtyProblem(self, text: str) -> str:
        """Проверка количества в форме теми же правилами, что и на сервере (> 0, ≤ 4 знаков, без float)."""
        t = text.strip().replace(",", ".").replace(" ", "").replace("\u00a0", "")
        if not t:
            return ""
        try:
            stock.parse_qty(t)
            return ""
        except stock.StockError as e:
            return e.message_ru

    @Slot(str, str, str, result="QVariant")
    def transferShortage(self, shipped: str, accepted_text: str, unit: str):
        """Недостача в форме приёмки перемещения (D-30): считается в Decimal (не в JavaScript-числах QML)."""
        return qml_safe(stock_view.shortage(shipped, accepted_text, unit))

    @Slot(str, result=str)
    def normalizeQty(self, text: str) -> str:
        """Количество из поля ввода → строка для команды: без пробелов, запятая → точка (без float)."""
        return text.strip().replace(",", ".").replace(" ", "").replace("\u00a0", "")

    @Slot(str, str, str, str, "QVariantList")
    def saveCalcDraft(self, tag: str, kit_id: str, currency: str, note: str, lines):
        """«Сохранить расчёт»: через очередь (можно без связи). expected_version считает SyncClient."""
        lines = _clean_lines(lines.toVariant() if hasattr(lines, "toVariant") else lines)
        cur = currency.strip().upper() or None

        def job():
            """Сохранить черновик в рабочем потоке и снять снимок данных."""
            r = self.client.save_calc_draft(kit_id, cur, note.strip() or None, lines)
            return {"result": r.__dict__, "snap": self._snapshot(False)}
        self._run(job, lambda res: self._finish_command(tag, "calc.save_draft", res))

    def _assignments_known(self, project_id) -> bool:
        """Известны ли клиенту назначения проекта (если нет — подсказки прав не скрывают действия, решит сервер)."""
        return any(a.get("project_id") == project_id for a in self._assignments)

    # ------------------------------------------------------------ права-подсказки
    @Property(int, notify=changed)
    def permRevision(self):
        """Счётчик смены прав текущего пользователя: QML передаёт его в hint(), чтобы кнопки обновлялись после смены
        ролей.
        """
        return self._perm_rev

    # События журнала, которые не являются командами каталога (файловые маршруты, вход и т. п.).
    _EVENT_LABELS = {"file.upload": "Загрузка файла", "file.download": "Скачивание файла"}

    @Slot(str, result=str)
    def commandLabel(self, command: str) -> str:
        """Русское название команды или события журнала (для истории и очереди)."""
        meta = self.contracts.catalog["commands"].get(command) or {}
        return meta.get("label_ru") or self._EVENT_LABELS.get(command) or command

    @Slot(str, result=str)
    def authorityLabel(self, authority: str) -> str:
        """global_role:rp#grant:2 → «роль РП (выдача №2)»; delegated_role:… / delegated_project_role:… → «… по
        замещению №N»; без внутренних кодов."""
        if not authority:
            return "—"
        if authority == "self":
            return "собственная учётная запись"
        # Замещение (D-34): роль носителя, полученная на срок.
        d = re.fullmatch(r"delegated_role:([a-z_]+)#grant:(\w+)#delegation:(\w+)", authority)
        if d:
            role, grant, deleg = d.groups()
            return f"роль «{ROLE_LABELS.get(role, role)}» по замещению №{deleg} (выдача носителя №{grant})"
        # Роль в проекте по замещению (D-35): назначение носителя в этом проекте.
        d = re.fullmatch(r"delegated_project_role:([a-z_]+)#assignment:(\w+)#delegation:(\w+)", authority)
        if d:
            role, aid, deleg = d.groups()
            return f"«{PROJECT_ROLE_LABELS.get(role, role)}» по замещению №{deleg} (назначение носителя №{aid})"
        m = re.fullmatch(r"(global_role|project_role):([a-z_]+)#(grant|assignment):(\w+)", authority)
        if not m:
            return authority
        kind, role, _src, ref = m.groups()
        if kind == "global_role":
            return f"роль «{ROLE_LABELS.get(role, role)}» (выдача №{ref})"
        return f"«{PROJECT_ROLE_LABELS.get(role, role)}» (назначение №{ref})"

    @Slot(str, result=str)
    def tip(self, text: str) -> str:
        """Всплывающая подсказка для элемента интерфейса с текстом ``text`` (D-37): что делает кнопка, пункт меню,
        вкладка или что значит статус, плюс расшифровка всех сокращений. Пусто — подсказывать нечего.
        Тексты — в hints.py (один источник для клиента, окна «Сокращения и подсказки» и руководства).
        """
        return hints.tip(text or "")

    @Property("QVariantList", constant=True)
    def glossary(self) -> list:
        """Справочник сокращений для окна «Сокращения и подсказки» (F1): [{abbr, full, note}] по алфавиту."""
        return hints.glossary_rows()

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
        """Вход из формы: проверить поля, создать клиент для сервера (или переиспользовать), запомнить
        адрес/логин/сертификат (не пароль) и войти в рабочем потоке.
        """
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
            """Итог входа: онлайн → главный экран (или смена пароля) и обновление данных; офлайн → главный экран в
            офлайн-режиме с предупреждением; иначе — текст ошибки на форме. Если пароль сбрасывал администратор,
            прежний кэш отложен — пользователь предупреждается.
            """
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
        """Смена пароля (обязательная при первом входе или по желанию)."""
        def done(r):
            """Итог смены пароля: переход на главный экран или уведомление об ошибке."""
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
        """Перейти из офлайн-режима в онлайн тем же пользователем (ввод пароля) и сразу отправить очередь."""
        def done(r):
            """Итог переподключения."""
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
        """Выход: закрыть профиль и стереть все данные из памяти интерфейса (на экране входа ничего не остаётся)."""
        def done(_):
            """Сбросить состояние интерфейса после выхода."""
            self._screen, self._session_mode, self._me = "login", "none", {}
            self._notify_seen = None
            self._data = {k: [] for k in self._data}
            # Снимки из кэша тоже убираются из памяти: после выхода на экране входа не должно оставаться данных.
            self._stock, self._calc_versions, self._calc_revisions, self._doc_versions = {}, [], [], []
            self.changed.emit()
            self.dataChanged.emit()
        self._run(lambda: self.client.logout() if self.client else None, done)

    # ------------------------------------------------------------ синхронизация
    def _snapshot(self, pull: bool):
        """Собрать в рабочем потоке всё, что показывает интерфейс (объекты кэша, списки meta, склад, люди, счётчики
        очереди), при pull=True — предварительно синхронизировать.
        """
        c = self.client
        rep = None
        if pull and c.token:
            rep = c.sync()
        data = {k: c.list_local(k) for k in self._data}
        return {"data": data, "doc_versions": c.get_meta("document_versions") or [],
                "calc_versions": c.get_meta("calc_versions") or [], "calc_revisions": c.get_meta("calc_revisions") or [],
                "stock": {k: c.get_meta(k) for k in STOCK_META}, "me": c.get_meta("me") or c.get_meta("session") or {}, "people": c.get_meta("people") or [], "assignments": c.get_meta("assignments") or [],
                "pending": c.pending_count(), "problems": c.problem_count(), "last_pull": c.get_meta("last_pull") or "",
                "online": c.t.online and c.token is not None, "token": c.token is not None, "rep": rep.__dict__ if rep else None}

    def _apply_snapshot(self, s):
        """Применить снимок в потоке интерфейса и разослать changed/dataChanged. Если сеанс с сервером пропал — перейти
        в офлайн-режим с уведомлением.
        """
        if not isinstance(s, dict) or "data" not in s:
            if isinstance(s, dict) and s.get("message_ru"):
                self.toast.emit("error", s["message_ru"])
            return
        self._data = s["data"]
        self._doc_versions = s.get("doc_versions", [])
        self._calc_versions = s.get("calc_versions", [])
        self._calc_revisions = s.get("calc_revisions", [])
        self._stock = s.get("stock") or {}
        self._data_rev += 1
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
        self._announce_notifications()
        self.changed.emit()
        self.dataChanged.emit()

    def _refresh_after(self, then, pull: bool):
        """Обновить снимок (с синхронизацией или без) и затем вызвать then()."""
        def done(s):
            """Применить снимок и продолжить."""
            self._apply_snapshot(s)
            then()
        self._run(lambda: self._snapshot(pull), done)

    @Slot()
    def syncNow(self):
        """Кнопка «Синхронизировать»: отправить очередь, получить свежие данные, сообщить итог."""
        if not self.client or self._screen != "main":
            return

        def done(s):
            """Уведомление по итогу: остановка на конфликте, нет связи или число отправленных изменений."""
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
        """Фоновая синхронизация по таймеру: при сеансе — тихая синхронизация; в офлайн-режиме — только проверка,
        доступен ли сервер (пароль для входа спросит пользователь).
        """
        if self.client and self._screen == "main" and not self._busy and self.client.token:
            self._run(lambda: self._snapshot(True), self._apply_snapshot)
        elif self.client and self._screen == "main" and not self._busy and self._session_mode == "offline":
            def probe():
                """Проверить доступность сервера."""
                return {"ping": self.client.t.ping()}

            def done(r):
                """Обновить индикатор связи; при появлении сервера — подсказать войти."""
                was = self._online
                self._online = bool(r.get("ping"))
                if self._online and not was:
                    self.toast.emit("info", "Сервер снова доступен. Введите пароль, чтобы отправить очередь.")
                self.changed.emit()
            self._run(probe, done)

    # ------------------------------------------------------------ команды
    @Slot(str, str, "QVariantMap", int)
    def execute(self, tag: str, command: str, payload: dict, expected_version: int):
        """Выполнить команду из формы QML: tag — метка формы, payload — поля (пустая строка = поле не передаётся, null
        = очистить), expected_version > 0 — ожидаемая версия объекта.
        """
        ev = expected_version if expected_version > 0 else None
        # "" из пустого поля формы = «не указано» (ключ не передаётся); null = явная очистка значения при изменении.
        payload = {k: v for k, v in dict(payload).items() if v != ""}

        def job():
            """Выполнить команду в рабочем потоке; после складских команд сразу перечитать снимок с сервера."""
            r = self.client.execute(command, payload, ev)
            if r.status == "accepted" and command.split(".")[0] in self._STOCK_PREFIXES:
                # Проводка/резерв/потребность меняют производные данные, которые клиент сам не пересчитывает
                # (покрытие строк потребности, «доступно» по позициям под резервами других проектов). Сразу
                # перечитываем снимок с сервера; если связь пропала — остаётся локально применённый ответ,
                # а экран по-прежнему честно показывает «на момент …».
                self.client.pull()
            return {"result": r.__dict__, "snap": self._snapshot(False)}
        self._run(job, lambda res: self._finish_command(tag, command, res))

    # Команды, после которых обновляется складской снимок (см. execute).
    @Slot(str, str, str, str, int)
    def publishSetting(self, tag: str, key: str, value: str, reason: str, version: int):
        """Опубликовать настройку компании (config.publish, только admin и при связи; A-26).

        Отдельный слот, потому что у ещё не заданной настройки ожидаемая версия = 0, а общий execute трактует
        0 как «версии нет». version — номер действующей версии, которую видел администратор.
        """
        def job():
            """Публикация в рабочем потоке и сразу pull: настройки приходят только снимком."""
            r = self.client.execute("config.publish", {"key": key, "value": value, "reason": reason}, int(version))
            if r.status == "accepted":
                self.client.pull()
            return {"result": r.__dict__, "snap": self._snapshot(False)}
        self._run(job, lambda res: self._finish_command(tag, "config.publish", res))

    # Справочник и настройки тоже приходят только с pull — после их команд снимок перечитывается.
    # Замещение (D-34) меняет роли — после него перечитываются и роли (me), и права в интерфейсе; после «прочитано»
    # (D-35) — список уведомлений и бейдж.
    # Наряды (D-36): после выпуска, замены, закрытия — свежие материалы и статусы с сервера.
    _STOCK_PREFIXES = frozenset({"stock", "need", "item_alias", "unit_conversion", "config", "po", "delegation",
                                 "notification", "wo", "tp"})

    def _finish_command(self, tag: str, command: str, res: dict):
        """Общий разбор результата команды: снимок данных, уведомление, сигнал commandDone для формы."""
        if "result" not in res:
            self.toast.emit("error", res.get("message_ru", "Ошибка"))
            self._command_done(tag, res)
            return
        r = res["result"]
        self._apply_snapshot(res["snap"])
        st = r["status"]
        if st == "accepted" and (r.get("response") or {}).get("unchanged"):
            self.toast.emit("info", r.get("message_ru") or "Изменений нет.")
        elif st == "accepted":
            self.toast.emit("success", "Готово: " + self.contracts.label(command).lower() + ".")
        elif st == "queued":
            self.toast.emit("info", r["message_ru"])
        elif st == "conflict":
            self.toast.emit("warning", r["message_ru"])
        elif st in ("refused_offline", "blocked", "unknown_outcome"):
            self.toast.emit("warning", r["message_ru"])
        else:
            self.toast.emit("error", r.get("message_ru") or "Действие не выполнено.")
        self._command_done(tag, {"status": st, "code": (r.get("response") or {}).get("code", ""),
                                    "message_ru": r.get("message_ru", ""), "operation_id": r.get("operation_id") or "",
                                    "details": (r.get("response") or {}).get("details") or {},
                                    "result": (r.get("response") or {}).get("result") or {}})

    # ------------------------------------------------------------ файлы документов
    # Ограничение клиента «на всякий случай»: заведомо неприемлемые размеры отсекаем до чтения файла.
    # Сам файл передаётся потоком (issue #8) и в память целиком не читается. Реальный предел задаёт сервер
    # (max_file_mb, A-16) — он же отказывает по заявленному размеру ещё до приёма тела.
    _CLIENT_FILE_CAP = 2048 * 1024 * 1024

    @Slot(str, str, str, str, int)
    def addDocumentVersion(self, tag: str, document_id: str, file_url: str, comment: str, expected_version: int):
        """Загрузить выбранный файл новой версией документа (только онлайн; в очередь не ставится)."""
        path = Path(QUrl(file_url).toLocalFile() if "://" in file_url else file_url)

        def job():
            """Проверить файл (существует, не больше предела клиента) и загрузить его потоком."""
            try:
                size = path.stat().st_size
            except OSError:
                return {"status": "error", "message_ru": "Файл не найден или недоступен для чтения."}
            if size > self._CLIENT_FILE_CAP:
                return {"status": "rejected", "message_ru": "Файл слишком большой для загрузки."}
            r = self.client.add_document_version(document_id, path, path.name, comment.strip() or None, expected_version)
            return {"result": r.__dict__, "snap": self._snapshot(False)}

        self._run(job, lambda res: self._file_done(tag, res, "Новая версия загружена и проверена."))

    @Slot(str, str, int, str)
    def downloadDocumentVersion(self, tag: str, document_id: str, version_no: int, dest_url: str):
        """Скачать файл версии в выбранное место; файл сохраняется только после сверки SHA-256."""
        dest = Path(QUrl(dest_url).toLocalFile() if "://" in dest_url else dest_url)

        def job():
            """Скачать версию в выбранный файл (сохранение — только после сверки SHA-256)."""
            r = self.client.download_document_version(document_id, version_no, dest)
            return {"result": r.__dict__, "snap": None}

        self._run(job, lambda res: self._file_done(tag, res, f"Файл сохранён и проверен: {dest.name}."))

    def _file_done(self, tag: str, res: dict, ok_text: str):
        """Общий итог файловых операций: тост + commandDone в том же формате, что и execute()."""
        if "result" not in res:
            self.toast.emit("error", res.get("message_ru", "Ошибка"))
            self._command_done(tag, {"status": res.get("status", "error"), "message_ru": res.get("message_ru", "")})
            return
        r = res["result"]
        if res.get("snap"):
            self._apply_snapshot(res["snap"])
        st = r["status"]
        if st == "accepted":
            self.toast.emit("success", ok_text)
        elif st in ("conflict", "refused_offline", "unknown_outcome"):
            self.toast.emit("warning", r.get("message_ru", ""))
        else:
            self.toast.emit("error", r.get("message_ru") or "Действие не выполнено.")
        self._command_done(tag, {"status": st, "message_ru": r.get("message_ru", ""),
                                    "code": (r.get("response") or {}).get("code", "")})

    @Slot(str, int, result=str)
    def suggestedFileName(self, document_id: str, version_no: int) -> str:
        """Имя файла версии для диалога «Сохранить как» (как его загрузил автор)."""
        v = next((v for v in self._doc_versions if v.get("document_id") == document_id and v.get("version_no") == version_no), None)
        return v.get("file_name", "") if v else ""

    @Slot(result=str)
    def newId(self) -> str:
        """Новый UUID для создаваемого объекта: id выдаёт клиент, чтобы объект, созданный офлайн, сохранил его на
        сервере.
        """
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
        """Сумма из поля ввода → строка для команды: без пробелов, запятая → точка (без float)."""
        return text.strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")

    # ------------------------------------------------------------ очередь и конфликты
    @Property("QVariantList", notify=dataChanged)
    def queueItems(self):
        """Очередь синхронизации для экрана «Синхронизация» (после loadQueue)."""
        return qml_safe(self._queue)

    @Slot()
    def loadQueue(self):
        """Загрузить очередь и проблемные операции; для конфликтов собрать сравнение «моё / на сервере» по полям."""
        def job():
            """Прочитать очередь и проблемы в рабочем потоке."""
            return {"queue": self.client.queue(), "problems": self.client.problems()}

        def done(r):
            """Построить строки экрана очереди с русскими названиями полей и признаком расхождения."""
            probs = {p["operation_id"]: p for p in r.get("problems", [])}
            items = []
            for q in r.get("queue", []):
                p = probs.get(q["operation_id"], {})
                diff = []
                cur = p.get("server_current") or {}
                for k, v in (p.get("payload") or {}).items():
                    if k == "id":
                        continue
                    diff.append({"field": k, "field_label": FIELD_LABELS.get(k, k), "mine": _conflict_value(k, v, p.get("payload")),
                                 "server": _conflict_value(k, cur.get(k), cur), "differs": cur.get(k) != v})
                items.append({**q, "message_ru": p.get("message_ru", ""), "code": p.get("code", ""), "diff": diff,
                              "server_version": cur.get("version", 0)})
            self._queue = items
            self.dataChanged.emit()
        self._run(job, done)

    @Slot(str)
    def discard(self, op_id: str):
        """«Отменить своё» для операции очереди; на экране остаётся версия сервера."""
        self._run(lambda: (self.client.discard(op_id), self._snapshot(False))[1],
                  lambda s: (self._apply_snapshot(s), self.loadQueue(), self.toast.emit("info", "Ваше изменение отменено; показана версия сервера.")))

    @Slot(str)
    def reapply(self, op_id: str):
        """«Повторить поверх текущей версии» для конфликта."""
        def job():
            """Повторить операцию в рабочем потоке и снять снимок."""
            r = self.client.reapply_on_current(op_id)
            return {"r": r.__dict__, "snap": self._snapshot(False)}

        def done(res):
            """Обновить экран и сообщить итог."""
            self._apply_snapshot(res.get("snap"))
            self.loadQueue()
            r = res.get("r", {})
            self.toast.emit("success" if r.get("status") == "accepted" else "warning",
                            "Изменение применено поверх текущей версии." if r.get("status") == "accepted" else r.get("message_ru", ""))
        self._run(job, done)

    # ------------------------------------------------------------ запросы
    @Slot(str, str, str)
    def history(self, tag: str, object_type: str, object_id: str):
        """Запросить с сервера историю объекта (журнал изменений) — только онлайн; ответ придёт в queryDone(tag)."""
        self._run(lambda: self.client.query("history", object_type=object_type, object_id=object_id), lambda r: self._query_done(tag, r))

    @Slot(str)
    def loadUsers(self, tag: str):
        """Запросить список пользователей для администрирования (только онлайн, право проверяет сервер)."""
        def done(r):
            """Добавить русские подписи ролей к выдачам и передать ответ форме."""
            if r.get("status") == "accepted":
                for u in r["result"]:
                    u["role_labels"] = [ROLE_LABELS.get(g["role"], g["role"]) for g in u["grants"] if g["active"]]
                    for g in u["grants"]:
                        g["label"] = ROLE_LABELS.get(g["role"], g["role"])
            self._query_done(tag, r)
        self._run(lambda: self.client.query("users"), done)


def format_size(n: Optional[int]) -> str:
    """Размер файла по-русски: 1 536 → «1,5 КБ». Неизвестный размер — «—», а не «0 Б»."""
    if n is None:
        return "—"
    units = ["Б", "КБ", "МБ", "ГБ"]
    v, i = float(n), 0
    while v >= 1024 and i < len(units) - 1:
        v, i = v / 1024, i + 1
    return (f"{int(v)} {units[i]}" if i == 0 else f"{v:.1f} {units[i]}".replace(".", ","))


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
