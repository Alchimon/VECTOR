"""Офлайн-ядро клиента: зашифрованный локальный кэш, очередь операций, синхронизация, конфликты.

Перенос эталона spikes/reference-core/vector_ref/client.py (поведение закреплено тестами) с изменениями:
* локальные данные зашифрованы (vault.py), отдельный профиль на пару сервер+пользователь;
* вход: онлайн; при отсутствии связи — офлайн-разблокировка профиля (просмотр кэша и очередь queued-команд);
* «online_required» не зависит от флага online: попытка отправки, Offline -> отказ без постановки в очередь.

Правила (из контракта, не менять без Decision Log):
* класс команды берётся из contracts/command-catalog (queued / online_required);
* queued: устойчивый operation_id создаётся ОДИН раз и переиспользуется при всех повторах;
* синхронизация строго по порядку; на конфликте/отказе — стоп (причинность сохраняется);
* конфликт не сливается автоматически: «отменить своё» или «применить поверх текущей версии» (новая операция);
* потерян ответ — операция остаётся в очереди с тем же operation_id.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from vector.core.contracts import Contracts
from vector.core.redact import without_secrets

from .transport import FILE_BLOCK, Offline, ResponseLost, TlsError
from .vault import DeviceSecretStore, OpenVault, Vault, VaultError, profile_dir, server_dir

# Локальная БД профиля (SQLite на ПК пользователя). Все данные — в зашифрованных полях *_enc (vault.py).
# * cache  — объекты: data_enc — то, что видит пользователь (с его неотправленными правками),
#            server_enc — последняя известная серверная копия, pending=1 — есть неподтверждённые правки;
# * outbox — очередь операций в порядке seq; operation_id создаётся один раз и не меняется при повторах;
#            state: pending → accepted | conflict | rejected; конфликт/отказ решает пользователь:
#            discarded («отменить своё») или superseded («применить поверх» новой операцией);
# * meta   — служебное: сеанс, время снимка last_pull, люди, назначения, неизменяемые списки, склад.
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

# Типы объектов в зашифрованном кэше: (тип в каталоге команд, ключ в ответе pull / результате команды).
# Версии документов (document_versions) в кэш объектов не входят: они неизменяемы и не правятся с клиента,
# поэтому хранятся списком в зашифрованной meta (как назначения проекта).
# Черновик расчёта комплекта (calc_draft) — обычный объект кэша: id = id комплекта, правится офлайн через очередь.
# Зафиксированные версии расчёта и ревизии проектного итога неизменяемы — хранятся списками в meta.
# Склад: справочники (склады, номенклатура) правятся офлайн через очередь — обычные объекты кэша.
OBJECT_TYPES = (("customer", "customers"), ("tender", "tenders"), ("tkp", "tkp"), ("project", "projects"), ("kit", "kits"),
                ("task", "tasks"), ("document", "documents"), ("calc_draft", "calc_drafts"),
                ("warehouse", "warehouses"), ("stock_item", "stock_items"),
                # Закупка (D-32): поставщики и черновики заказов правятся офлайн через очередь; размещение, приём,
                # закрытие и отмена — онлайн-командами, их ответ (заказ) тоже кладётся сюда.
                ("supplier", "suppliers"), ("purchase_order", "purchase_orders"),
                # Договоры с поставщиками (D-33, A-39) — тоже справочник закупки, правится офлайн через очередь.
                ("supplier_contract", "supplier_contracts"),
                # Технология и производство (D-36): ТП (черновик правится офлайн), наряды и операции нарядов (события
                # исполнителя — офлайн через очередь; выпуск, назначение, замена, закрытие — онлайн-командами).
                ("tech_process", "tech_processes"), ("work_order", "work_orders"), ("wo_operation", "wo_operations"))
# Объекты из результата команды → список в meta (ключ результата, ключ meta); заменяются по id.
# Движения склада, резервы и потребности меняются ТОЛЬКО онлайн-командами (online_required): в кэше это
# снимок «на момент последнего обновления», офлайн он только читается.
META_LISTS = (("calc_version", "calc_versions"), ("calc_revision", "calc_revisions"),
              ("movement", "stock_movements"), ("reservation", "stock_reservations"), ("need", "needs"),
              ("transfer", "stock_transfers"))
# Данные из pull, которые хранятся в meta как есть (снимок; время снимка — last_pull): склад, потребности,
# справочник аналогов/пересчёта (D-25, A-27), настройки компании (config, D-26), замещения и покрытие ролей
# (D-34), замещающие в проектах и уведомления (D-35). Всё это меняется только онлайн-командами, офлайн — только
# читается.
STOCK_META = ("stock_balances", "stock_reservations", "stock_movements", "stock_movements_truncated", "needs",
              "stock_transfers", "stock_projects", "need_candidates", "item_aliases", "unit_conversions", "config",
              "delegations", "role_coverage", "project_deputies", "notifications",
              # D-36: состав изделий из зафиксированных версий расчёта (без цен) — для ТП; исполнители «Производства».
              "tech_specs", "production_kits", "production_staff")


# Онлайн-команды, которым не нужно сначала отправить очередь (их результат не зависит от неотправленных правок).
ORDER_FREE = frozenset({"account.change_password", "notification.read"})


def _calc_same(local: dict, payload: dict) -> bool:
    """Совпадает ли содержимое расчёта (валюта, примечание, позиции) — в канонической форме vector.core.calc."""
    from vector.core import calc
    try:
        a, ca = calc.parse_lines(local.get("lines") or [], local.get("currency"))
        b, cb = calc.parse_lines(payload["lines"], payload.get("currency"))
    except calc.CalcError:
        return False
    return (ca, (local.get("note") or None)) == (cb, payload.get("note")) and [x.to_json() for x in a] == [x.to_json() for x in b]


def _po_same(local: dict, payload: dict) -> bool:
    """Совпадает ли черновик заказа в кэше с сохраняемым: справочные поля и строки в канонической форме ядра."""
    from vector.core import purchasing as pu

    def canon(o):
        """Каноническая форма черновика (строки — через правила ядра; не разобрались — None, т. е. «не равно»)."""
        try:
            lines, cur = pu.parse_lines([{k: ln.get(k) for k in pu.LINE_KEYS if k in ln} for ln in o.get("lines") or []],
                                        o.get("currency"))
        except pu.PurchaseError:
            return None
        try:   # D-34: суммы по счёту — числом («1220.00» из формы = «1220.0000» с сервера)
            inv = tuple(None if o.get(k) in (None, "") else pu.check_invoice_amount(o.get(k), k)
                        for k in ("invoice_gross", "invoice_vat"))
        except pu.PurchaseError:
            return None
        # D-33: договор и режим цен (с НДС / без НДС) — тоже содержание черновика; ставка НДС — в строках.
        return (o.get("supplier_id"), cur, o.get("supplier_ref") or None, o.get("expected_on") or None,
                o.get("note") or None, o.get("contract_id") or None, o.get("vat_mode") or None, inv,
                [x.to_json() for x in lines])
    a = canon(local)
    return a is not None and a == canon(payload)


def _tp_same(local: dict, payload: dict) -> bool:
    """Совпадает ли черновик ТП в кэше с сохраняемым — по SHA-256 содержимого из ядра (как считает сервер)."""
    from vector.core import production as prod

    def h(o):
        """Хэш содержимого ТП (операции не разобрались — None, т. е. «не равно»)."""
        try:
            ops = prod.parse_operations(o.get("operations") or [])
        except prod.ProductionError:
            return None
        return prod.tp_content_hash((o.get("title") or "").strip(), o.get("spec_version_id"), ops, o.get("note") or None)
    a = h(local)
    return a is not None and a == h(payload)


@dataclass
class LocalResult:
    """Исход действия пользователя на клиенте — то, что видит интерфейс.

    Ключевые статусы: queued — сохранено локально, сервер ещё не подтвердил; accepted — сервер принял;
    refused_offline — действие требует сервера и НЕ выполнено (и не поставлено в очередь); unknown_outcome — запрос
    ушёл, ответа нет (проверка повтором с тем же operation_id); blocked — сначала разберите очередь; conflict /
    rejected / denied — ответ сервера; offline_unlocked — вход без связи.
    """
    status: str   # queued | accepted | refused_offline | conflict | rejected | denied | unauthenticated | unknown_outcome | blocked | error | offline_unlocked
    operation_id: Optional[str] = None
    message_ru: str = ""
    response: dict = field(default_factory=dict)


@dataclass
class SyncReport:
    """Итог прохода синхронизации: сколько отправлено и принято (replayed — сервер вернул сохранённый ранее исход), на
    какой операции и с каким статусом остановились, была ли связь, обновлён ли кэш (pulled), сообщение для
    пользователя.
    """
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
    """Текущее время в UTC (если в SyncClient не подставлены тестовые часы)."""
    return datetime.now(timezone.utc)


class SyncClient:
    """Клиентская часть синхронизации: сеанс, зашифрованный локальный кэш, очередь операций (outbox), отправка, приём
    снимка (pull), конфликты и файлы.

    Один экземпляр обслуживает одного вошедшего пользователя одного сервера. Используется из Backend (интерфейс) и
    напрямую в тестах tests/client.
    """
    def __init__(self, transport, server_url: str, base_dir: Path, store: DeviceSecretStore, device_id: str,
                 contracts: Optional[Contracts] = None, clock=None, offline_unlock_max_days: int = 7, kdf: dict | None = None):
        """Транспорт, адрес сервера, базовый каталог профилей, хранилище секрета устройства, id устройства. contracts —
        каталог команд (класс queued/online_required); clock и kdf подменяются в тестах; offline_unlock_max_days —
        предел офлайн-входа.
        """
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
        self.max_file_bytes: Optional[int] = None   # предел размера файла с сервера (из ответа входа)
        self.db: Optional[sqlite3.Connection] = None
        self.vault: Optional[Vault] = None
        self.ov: Optional[OpenVault] = None
        self.orphaned_cache: Optional[Path] = None

    def now(self) -> datetime:
        """Текущее время (тестовые часы, если заданы)."""
        return self.clock.now() if self.clock else _now()

    # ------------------------------------------------------------ профили
    def _accounts_file(self) -> Path:
        """Файл соответствия «логин → user_id» для сервера: нужен для офлайн-входа (найти профиль без связи). Пароли и
        токены в нём не хранятся.
        """
        return server_dir(self.base_dir, self.server_url) / "accounts.json"

    def _remember_account(self, login: str, user_id: str):
        """Запомнить user_id для логина после успешного онлайн-входа."""
        f = self._accounts_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(f.read_text()) if f.exists() else {}
        data[login.lower()] = user_id
        f.write_text(json.dumps(data))

    def _known_user(self, login: str) -> Optional[str]:
        """user_id ранее входившего на этом ПК пользователя или None."""
        f = self._accounts_file()
        return json.loads(f.read_text()).get(login.lower()) if f.exists() else None

    def _open_db(self, ov: OpenVault):
        """Открыть локальную БД профиля (SQLite), создать таблицы, включить secure_delete (удалённое затирается).
        Служебная запись «check» проверяет, что ключ подходит к этой базе: чужая или повреждённая база сразу даёт
        ошибку.
        """
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
        """Зашифровать значение для ячейки (таблица, ключ строки) ключом открытого хранилища."""
        return self.ov.seal(table, key, text)

    def _open(self, table, key, sealed):
        """Расшифровать значение ячейки; при подмене — VaultError local_data_corrupt."""
        return self.ov.open(table, key, sealed)

    def _set_meta(self, k: str, v: Any):
        """Записать служебное значение (JSON) в meta зашифрованным."""
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, self._seal("meta", k, json.dumps(v, ensure_ascii=False))))

    def get_meta(self, k: str) -> Any:
        """Прочитать служебное значение из meta (None, если нет)."""
        row = self.db.execute("SELECT v_enc FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(self._open("meta", k, row[0])) if row else None

    # ------------------------------------------------------------ сеанс
    def login(self, login: str, password: str) -> LocalResult:
        """Вход.

        При связи: запрос к серверу; при успехе — запомнить токен (только в памяти), предел размера файла, открыть
        или создать зашифрованный профиль под этим паролем. Смена пользователя закрывает прежний профиль. Нет связи
        → офлайн-разблокировка профиля. Ошибка сертификата → отказ (а не офлайн-режим).
        """
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
        # Предел размера файла с сервера (issue #9); None — сервер старой версии, тогда проверяет только он.
        mfb = r.get("max_file_bytes")
        self.max_file_bytes = mfb if isinstance(mfb, int) and not isinstance(mfb, bool) and mfb > 0 else None
        self._remember_account(login, r["user_id"])
        self.vault = Vault(profile_dir(self.base_dir, self.server_url, r["user_id"]), self.store, self.max_days)
        ov = self.vault.unlock_after_online_login(password, self.now(), self.kdf)
        self.orphaned_cache = self.vault.orphaned
        self._open_db(ov)
        self._set_meta("session", {"user_id": r["user_id"], "login": r["login"], "display_name": r.get("display_name")})
        return LocalResult("accepted", response=r)

    def _offline_unlock(self, login: str, password: str) -> LocalResult:
        """Вход без связи: найти профиль по логину и открыть его паролем последнего онлайн-входа. Сеанса с сервером нет
        (token=None): можно смотреть кэш и ставить queued-команды в очередь.
        """
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
        """Сменить пароль на сервере (только онлайн); при успехе перешифровать обёртку ключа профиля новым паролем —
        очередь и кэш сохраняются.
        """
        r = self.execute("account.change_password", {"current_password": current, "new_password": new})
        if r.status == "accepted":
            self.must_change_password = False
            self.vault.rewrap(self.ov, new, self.now(), self.kdf)
        return r

    def logout(self):
        """Выйти: сообщить серверу (если есть связь), забыть токен, закрыть локальную БД. Очередь остаётся на диске —
        она будет отправлена при следующем входе этого пользователя.
        """
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
        """Есть ли действующий сеанс с сервером (а не только открытый офлайн-кэш)."""
        return self.token is not None

    # ------------------------------------------------------------ команды
    def execute(self, command: str, payload: dict, expected_version: Optional[int] = None) -> LocalResult:
        """Выполнить команду пользователя по её классу из каталога команд.

        * online_required (проводки, фиксации, файлы, смена пароля): нужен сеанс; если в очереди есть
          неотправленное — сначала синхронизация (причинность), иначе blocked. Отправка с новым operation_id; без
          связи — refused_offline (не ставится в очередь); потерянный ответ — операция запоминается для проверки
          повтором.
        * queued (справочники, задачи, черновики): запись в outbox и наложение на локальный объект в одной
          транзакции; при наличии сеанса — сразу синхронизация, и пользователь получает исход сервера.
        """
        if self.db is None:
            return LocalResult("unauthenticated", message_ru="Войдите в систему.")
        cls = self.contracts.offline_class(command)
        label = self.contracts.label(command)
        if cls == "online_required":
            if not self.token:
                return LocalResult("refused_offline", message_ru=f"«{label}» требует актуальной проверки сервера (права, статус, версия). "
                                                                 "Сейчас нет сеанса с сервером — действие не выполнено и не поставлено в очередь.")
            # Смена пароля и «прочитано» (D-35) не зависят от порядка очереди — не ждут её.
            if self.pending_count() and command not in ORDER_FREE:
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
                return LocalResult("unknown_outcome", op_id, "Ответ сервера не получен: результат неизвестен. Нажмите «Синхронизировать» "
                                                             "(F5) — программа спросит сервер по тому же номеру операции, "
                                                             "не выполняя действие дважды.")
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

    def save_calc_draft(self, kit_id: str, currency: Optional[str], note: Optional[str], lines: list[dict]) -> LocalResult:
        """«Сохранить расчёт» комплекта: команда calc.save_draft через офлайн-очередь.

        expected_version вычисляется здесь, а не в интерфейсе, с учётом очереди:
        * в очереди нет неотправленных сохранений этого черновика → версия черновика с сервера (0 — его ещё нет);
        * есть (пользователь сохранял несколько раз без связи) → версия предыдущего сохранения + 1: каждое
          следующее сохранение основано на предыдущем, и после его приёма сервер будет именно на этой версии.
        Ошибка в этой цепочке (например, предыдущее сохранение не изменило содержимого) даёт только КОНФЛИКТ,
        который пользователь решает сам, — но никогда молчаливую перезапись чужих правок.
        """
        if self.db is None:
            return LocalResult("unauthenticated", message_ru="Войдите в систему.")
        prev = self.db.execute("SELECT expected_version FROM outbox WHERE command='calc.save_draft' AND object_id=? "
                               "AND state IN ('pending','conflict','rejected') ORDER BY seq DESC LIMIT 1", (kit_id,)).fetchone()
        if prev is not None:
            ev = (prev[0] or 0) + 1
        else:
            row = self.db.execute("SELECT server_version FROM cache WHERE object_type='calc_draft' AND id=?", (kit_id,)).fetchone()
            ev = (row[0] if row and row[0] is not None else 0)
        payload = {"id": kit_id, "currency": currency or None, "note": note or None, "lines": lines}
        # Ничего не изменилось относительно локальной копии → не ставим операцию в очередь: сервер такое
        # сохранение не считает новой версией, и следующая цепочка версий разошлась бы (ложный конфликт).
        local = self.get_local("calc_draft", kit_id)
        if local is not None and _calc_same(local, payload):
            return LocalResult("accepted", message_ru="Изменений нет — сохранять нечего.", response={"unchanged": True})
        return self.execute("calc.save_draft", payload, ev)

    def save_po_draft(self, payload: dict) -> LocalResult:
        """«Сохранить черновик заказа поставщику»: po.create (заказа ещё нет ни на сервере, ни в очереди) или
        po.update — через офлайн-очередь (D-32).

        expected_version — по той же цепочке, что у черновика расчёта: если в очереди уже есть неотправленные
        правки этого заказа, версия = версия предыдущей правки + 1 (создание идёт без версии → следующая правка
        основана на версии 1); иначе — версия серверной копии. Ошибка цепочки даёт только конфликт, не перезапись.
        Сохранение без изменений в очередь не ставится (сервер ответил бы «нет изменений» и цепочка разошлась бы).
        """
        if self.db is None:
            return LocalResult("unauthenticated", message_ru="Войдите в систему.")
        oid = payload["id"]
        row = self.db.execute("SELECT server_version FROM cache WHERE object_type='purchase_order' AND id=?", (oid,)).fetchone()
        prev = self.db.execute("SELECT expected_version FROM outbox WHERE command IN ('po.create','po.update') AND object_id=? "
                               "AND state IN ('pending','conflict','rejected') ORDER BY seq DESC LIMIT 1", (oid,)).fetchone()
        if prev is None and (row is None or row[0] is None):
            return self.execute("po.create", payload)
        local = self.get_local("purchase_order", oid) or {}
        if _po_same(local, payload):
            return LocalResult("accepted", message_ru="Изменений нет — сохранять нечего.", response={"unchanged": True})
        ev = (prev[0] or 0) + 1 if prev is not None else row[0]
        return self.execute("po.update", payload, ev)

    def save_tp_draft(self, payload: dict) -> LocalResult:
        """«Сохранить ТП (черновик)»: tp.create (ТП ещё нет ни на сервере, ни в очереди) или tp.update — через
        офлайн-очередь (D-36). Цепочка expected_version — как у черновика заказа (save_po_draft); сохранение без
        изменений в очередь не ставится.
        """
        if self.db is None:
            return LocalResult("unauthenticated", message_ru="Войдите в систему.")
        oid = payload["id"]
        row = self.db.execute("SELECT server_version FROM cache WHERE object_type='tech_process' AND id=?", (oid,)).fetchone()
        prev = self.db.execute("SELECT expected_version FROM outbox WHERE command IN ('tp.create','tp.update') AND object_id=? "
                               "AND state IN ('pending','conflict','rejected') ORDER BY seq DESC LIMIT 1", (oid,)).fetchone()
        if prev is None and (row is None or row[0] is None):
            return self.execute("tp.create", payload)
        local = self.get_local("tech_process", oid) or {}
        if _tp_same(local, payload):
            return LocalResult("accepted", message_ru="Изменений нет — сохранять нечего.", response={"unchanged": True})
        ev = (prev[0] or 0) + 1 if prev is not None else row[0]
        # tp.update не принимает kit_id: комплект ТП не меняется после создания.
        return self.execute("tp.update", {k: v for k, v in payload.items() if k != "kit_id"}, ev)

    def op_event(self, op_id: str, kind: str, qty_done: Optional[int] = None, note: Optional[str] = None) -> LocalResult:
        """Событие исполнителя по операции наряда (старт / пауза / продолжить / завершить / проблема) — офлайн (D-36).

        Время события — часы ЭТОГО компьютера в момент нажатия (A-44): так таймер честен и без связи. Сначала
        событие проверяется по локальной копии операции теми же правилами ядра, что на сервере: заведомо
        недопустимое («завершить» уже завершённую) в очередь не ставится — пользователь сразу видит причину. Принятое
        локально — в очередь; на сервере оно проверяется ещё раз (назначение, наряд не закрыт, порядок времени), и
        расхождение даёт видимый конфликт в разделе «Синхронизация».
        """
        from vector.core import production as prod
        if self.db is None:
            return LocalResult("unauthenticated", message_ru="Войдите в систему.")
        op = self.get_local("wo_operation", op_id)
        if op is None:
            return LocalResult("rejected", message_ru="Операция не найдена в данных этого компьютера — обновите данные.")
        dt = self.now()
        try:
            prod.apply_event(op, kind, dt, int(op.get("wo_qty") or 0), qty_done)
        except prod.ProductionError as e:
            return LocalResult("rejected", message_ru=e.message_ru, response={"code": e.code})
        payload: dict[str, Any] = {"id": op_id, "kind": kind, "device_time": dt.isoformat()}
        if qty_done is not None:
            payload["qty_done"] = qty_done
        if note:
            payload["note"] = note
        return self.execute("wo.op_event", payload)

    def retry_online(self, op_id: str) -> LocalResult:
        """Повторить операцию с неизвестным исходом с ТЕМ ЖЕ operation_id: сервер либо выполнит её впервые, либо вернёт
        сохранённый исход — дважды она не выполнится.
        """
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
        """Отправить очередь строго по порядку.

        На принятой операции — дальше; на конфликте или отказе — стоп (следующие операции могут зависеть от этой).
        Нет связи или ответ потерян — стоп, очередь сохранена. Сеанс истёк — стоп, токен забыт. После полного
        прохода — pull свежего снимка.
        """
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
        """Получить с сервера снимок всего, что пользователь вправе видеть, и заменить им кэш.

        Объекты без локальных правок заменяются целиком (если права сузились, лишнее исчезает). Для объектов с
        операциями в очереди обновляется только «серверная» копия — локальная правка пользователя не затирается.
        Списки (версии документов, расчёты, склад) кладутся в meta вместе с временем снимка last_pull.
        """
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
        self._set_meta("document_versions", res.get("document_versions", []))
        for _, key in META_LISTS:
            self._set_meta(key, res.get(key, []))
        for key in STOCK_META:
            self._set_meta(key, res.get(key, False if key.endswith("_truncated") else []))
        self.db.execute("COMMIT")
        return True

    def query(self, name: str, **kw) -> dict:
        """Произвольный запрос к серверу (история, отчёты) — только онлайн; без связи возвращает refused_offline."""
        if not self.token:
            return {"status": "refused_offline", "message_ru": "Нужна связь с сервером."}
        try:
            return self.t.send({"type": "query", "query": name, "token": self.token, **kw})
        except (Offline, ResponseLost):
            return {"status": "refused_offline", "message_ru": "Нет связи с сервером."}
        except TlsError as e:
            return {"status": "error", "message_ru": str(e)}

    # ------------------------------------------------------------ файлы версий документов
    # Файлы НЕ ставятся в офлайн-очередь (document.add_version — online_required): содержимое могло бы
    # пролежать в очереди днями, а при конфликте его нельзя «слить». Поэтому всё — только при живом сеансе.

    def document_versions(self, document_id: str) -> list[dict]:
        """Версии документа из локального кэша (метаданные: номер, имя, размер, SHA-256), по возрастанию номера."""
        return [v for v in (self.get_meta("document_versions") or []) if v["document_id"] == document_id]

    def add_document_version(self, document_id: str, data, file_name: str, comment: Optional[str],
                             expected_version: int) -> LocalResult:
        """Загрузить файл и создать новую версию документа — два шага, оба онлайн.

        data — путь к файлу (Path) или bytes. Файл с диска читается потоком дважды (SHA-256, затем отправка),
        целиком в память не загружается (issue #8). Если файл изменят между этими проходами, сервер
        увидит несовпадение хэша и отклонит загрузку — «смешанная» версия не появится.

        1) upload: байты + SHA-256, посчитанный здесь; сервер пересчитывает и выдаёт квитанцию загрузки.
           Повтор после обрыва безопасен: содержимое адресуется хэшем и не дублируется.
        2) document.add_version с тем же хэшем и ожидаемой версией карточки документа: если коллега успел
           добавить свою версию, вернётся конфликт — пользователь решает сам, ничего не перезаписывается.
        """
        if self.db is None:
            return LocalResult("unauthenticated", message_ru="Войдите в систему.")
        doc = self.get_local("document", document_id)
        if doc is None:
            return LocalResult("rejected", message_ru="Документ не найден в локальных данных. Обновите данные с сервера.")
        if not self.token:
            return LocalResult("refused_offline", message_ru="Новая версия документа (файл) добавляется только при связи с сервером. "
                                                             "Ничего не поставлено в очередь.")
        try:
            src = open(data, "rb") if isinstance(data, (str, os.PathLike)) else io.BytesIO(bytes(data))
        except OSError:
            return LocalResult("error", message_ru="Файл не найден или недоступен для чтения.")
        try:
            limit = self.max_file_bytes
            if limit is not None and _remaining_size(src) > limit:
                # Понятный отказ без отправки (issue #9). Без этой проверки сервер всё равно откажет, но на медленной
                # сети он может закрыть соединение раньше, чем клиент дошлёт тело, — и пользователь увидел бы
                # «исход неизвестен» вместо причины.
                src.close()
                return LocalResult("rejected", message_ru=f"Файл больше допустимого размера ({_fmt_mb(limit)} МБ). "
                                                          "Версия не добавлена.", response={"code": "file_too_large"})
            sha, size = _sha256_of(src)
            src.seek(0)
            up = self.t.upload(self.token, doc["project_id"], src, sha, size)
        except OSError:
            return LocalResult("error", message_ru="Не удалось прочитать файл. Версия не добавлена.")
        except Offline:
            return LocalResult("refused_offline", message_ru="Связь пропала до загрузки файла. Версия не добавлена.")
        except ResponseLost:
            return LocalResult("unknown_outcome", message_ru="Ответ сервера на загрузку не получен. Повторите — "
                                                             "повторная загрузка того же файла безопасна.")
        except TlsError as e:
            return LocalResult("error", message_ru=str(e))
        finally:
            src.close()
        if up.get("status") != "accepted":
            if up.get("status") == "unauthenticated":
                self.token = None
            return LocalResult(up.get("status", "error"), message_ru=up.get("message_ru", "Файл не загружен."), response=up)
        r = self.execute("document.add_version", {"id": document_id, "sha256": sha, "file_name": file_name,
                                                  **({"comment": comment} if comment else {})}, expected_version)
        if r.status == "accepted":
            v = (r.response.get("result") or {}).get("document_version")
            if v:
                versions = [x for x in (self.get_meta("document_versions") or []) if x["id"] != v["id"]] + [v]
                self._set_meta("document_versions", versions)
        return r

    def download_document_version(self, document_id: str, version_no: int, dest: Path) -> LocalResult:
        """Скачать файл версии в dest с проверкой целостности «из конца в конец».

        * SHA-256 полученных байтов сверяется с хэшем версии из кэша И с заголовком сервера:
          порча в пути или подмена промежуточным узлом обнаруживаются, файл не сохраняется.
        * Запись атомарная: сначала временный файл рядом, затем переименование — при сбое на диске
          не останется «половины чертежа» под нужным именем.
        """
        if not self.token:
            return LocalResult("refused_offline", message_ru="Скачивание файла доступно при связи с сервером.")
        meta = next((v for v in self.document_versions(document_id) if v["version_no"] == version_no), None)
        dest = Path(dest)
        tmp = dest.with_name(dest.name + ".vector-partial")
        # Байты пишутся сразу во временный файл рядом с целевым, хэш считается по ходу (issue #8):
        # в памяти — один блок, сколько бы ни весил чертёж. Целевое имя появляется только после сверки.
        saved = False
        try:
            with open(tmp, "wb") as f:
                sink = _HashingWriter(f)
                try:
                    resp = self.t.download(self.token, document_id, version_no, sink)
                except (Offline, ResponseLost):
                    return LocalResult("refused_offline", message_ru="Нет связи с сервером — файл не скачан.")
                except TlsError as e:
                    return LocalResult("error", message_ru=str(e))
                if resp.get("status") != "accepted":
                    if resp.get("status") == "unauthenticated":
                        self.token = None
                    return LocalResult(resp.get("status", "error"), message_ru=resp.get("message_ru", "Файл не получен."),
                                       response=resp)
                actual = sink.hexdigest()
                expected = {resp.get("sha256")} | ({meta["sha256"]} if meta else set())
                if expected != {actual}:
                    return LocalResult("error", message_ru="Файл повреждён при передаче (контрольная сумма не совпала) и не сохранён. "
                                                           "Повторите скачивание; если ошибка повторяется — сообщите администратору.")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dest)
            saved = True
            _fsync_dir(dest.parent)
        except OSError as e:
            return LocalResult("error", message_ru=f"Не удалось записать файл на диск: {e.strerror or e}.")
        finally:
            if not saved:
                # Недокачанный или непроверенный файл не оставляем: его можно было бы принять за настоящий.
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return LocalResult("accepted", message_ru=f"Файл сохранён: {dest.name}", response={"sha256": actual, "path": str(dest)})

    # ------------------------------------------------------------ конфликты и отказы
    def problems(self) -> list[dict]:
        """Операции очереди, отклонённые сервером или в конфликте: команда, отправленные данные, ответ сервера и его
        текущая версия объекта — для экрана «Требуют решения».
        """
        out = []
        for op_id, command, state, penc, renc in self.db.execute(
                "SELECT operation_id, command, state, payload_enc, response_enc FROM outbox WHERE state IN ('conflict','rejected') ORDER BY seq"):
            r = json.loads(self._open("outbox", op_id + "/r", renc))
            out.append({"operation_id": op_id, "command": command, "label_ru": self.contracts.label(command), "state": state,
                        "payload": json.loads(self._open("outbox", op_id, penc)), "status": r.get("status"), "code": r.get("code"),
                        "message_ru": r.get("message_ru"), "server_current": (r.get("details") or {}).get("current")})
        return out

    def queue(self) -> list[dict]:
        """Все незавершённые операции очереди (ожидают, конфликт, отказ) по порядку — для экрана очереди."""
        out = []
        for op_id, command, state, attempts, created, otype, oid in self.db.execute(
                "SELECT operation_id, command, state, attempts, created_at, object_type, object_id FROM outbox "
                "WHERE state IN ('pending','conflict','rejected') ORDER BY seq"):
            out.append({"operation_id": op_id, "command": command, "label_ru": self.contracts.label(command), "state": state,
                        "attempts": attempts, "created_at": created, "object_type": otype, "object_id": oid})
        return out

    def discard(self, op_id: str) -> None:
        """«Отменить своё»: операция помечается отменённой (не удаляется — остаётся след), локальный объект
        возвращается к серверной копии, затем pull.
        """
        row = self.db.execute("SELECT object_type, object_id, state FROM outbox WHERE operation_id=?", (op_id,)).fetchone()
        if not row or row[2] not in ("conflict", "rejected", "pending"):
            raise ValueError("Нечего отменять")
        self.db.execute("UPDATE outbox SET state='discarded' WHERE operation_id=?", (op_id,))
        self._refresh_pending_flag(row[0], row[1])
        self.pull()

    def reapply_on_current(self, op_id: str) -> LocalResult:
        """«Повторить поверх текущей версии»: прежняя операция помечается заменённой, те же данные отправляются новой
        операцией с версией объекта, которую сообщил сервер. Пользователь делает это сознательно, увидев изменения
        коллеги.
        """
        row = self.db.execute("SELECT command, payload_enc, response_enc, state FROM outbox WHERE operation_id=?", (op_id,)).fetchone()
        if not row or row[3] != "conflict":
            raise ValueError("Повтор поверх доступен только для конфликта")
        current = json.loads(self._open("outbox", op_id + "/r", row[2]))["details"]["current"]
        self.db.execute("UPDATE outbox SET state='superseded' WHERE operation_id=?", (op_id,))
        return self.execute(row[0], json.loads(self._open("outbox", op_id, row[1])), expected_version=current["version"])

    # ------------------------------------------------------------ чтение кэша
    def list_local(self, object_type: str) -> list[dict]:
        """Все объекты типа из кэша (с локальными правками), с пометками _pending (есть неподтверждённые изменения) и
        _server_version.
        """
        out = []
        for oid, denc, pending, sv in self.db.execute("SELECT id, data_enc, pending, server_version FROM cache WHERE object_type=? ORDER BY id", (object_type,)):
            d = json.loads(self._open("cache", f"{object_type}/{oid}/d", denc))
            d["_pending"] = bool(pending)
            d["_server_version"] = sv
            out.append(d)
        return out

    def get_local(self, object_type: str, oid: str) -> Optional[dict]:
        """Один объект из кэша или None."""
        return next((d for d in self.list_local(object_type) if d["id"] == oid), None)

    def pending_count(self) -> int:
        """Сколько операций ждут отправки."""
        return self.db.execute("SELECT COUNT(*) FROM outbox WHERE state='pending'").fetchone()[0] if self.db else 0

    def problem_count(self) -> int:
        """Сколько операций требуют решения пользователя (конфликт или отказ)."""
        return self.db.execute("SELECT COUNT(*) FROM outbox WHERE state IN ('conflict','rejected')").fetchone()[0] if self.db else 0

    # ------------------------------------------------------------ внутреннее
    def _op_state(self, op_id):
        """Состояние операции в очереди и сохранённый ответ сервера."""
        st, renc = self.db.execute("SELECT state, response_enc FROM outbox WHERE operation_id=?", (op_id,)).fetchone()
        return st, (json.loads(self._open("outbox", op_id + "/r", renc)) if renc else {})

    def _send_command(self, op_id, command, payload, ev) -> dict:
        """Отправить одну команду протокола с operation_id и ожидаемой версией."""
        return self.t.send({"type": "command", "token": self.token, "operation_id": op_id, "command": command,
                            "payload": payload, "expected_version": ev})

    def _remember_online_unknown(self, op_id, command, payload, ev):
        """Сохранить online_required-операцию с потерянным ответом в очередь как pending — чтобы её можно было
        проверить повтором с тем же operation_id.
        """
        otype = self.contracts.catalog["commands"][command]["object"]
        # Пароли (подпись акта, создание пользователя) не сохраняются даже в зашифрованной очереди: для проверки
        # повтором они не нужны — сервер не включает их в хэш запроса (vector.core.redact).
        payload = without_secrets(payload)
        self.db.execute("INSERT INTO outbox(operation_id,command,payload_enc,expected_version,object_type,object_id,created_at,state) "
                        "VALUES (?,?,?,?,?,?,?,'pending')",
                        (op_id, command, self._seal("outbox", op_id, json.dumps(payload, ensure_ascii=False)), ev, otype, payload.get("id"),
                         self.now().isoformat()))

    def _finish(self, op_id, command, r):
        """Записать исход операции: принята — применить результат к кэшу; конфликт — сохранить текущую серверную версию
        объекта (для сравнения). Затем пересчитать признак «есть локальные правки».
        """
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
        """Сколько незавершённых операций относится к объекту."""
        return self.db.execute("SELECT COUNT(*) FROM outbox WHERE object_type=? AND object_id=? AND state IN ('pending','conflict','rejected')",
                               (otype, oid)).fetchone()[0]

    def _set_server_state(self, otype, obj):
        """Обновить «серверную» копию объекта в кэше (или создать запись, если объекта ещё не было), не трогая
        локальную правку.
        """
        j = json.dumps(obj, ensure_ascii=False)
        se = self._seal("cache", f"{otype}/{obj['id']}/s", j)
        if self.db.execute("UPDATE cache SET server_enc=?, server_version=? WHERE object_type=? AND id=?", (se, obj["version"], otype, obj["id"])).rowcount == 0:
            self.db.execute("INSERT INTO cache VALUES (?,?,?,?,?,0)", (otype, obj["id"], self._seal("cache", f"{otype}/{obj['id']}/d", j), se, obj["version"]))

    def _apply_response(self, r):
        """Применить результат принятой команды к кэшу сразу, не дожидаясь pull: неизменяемые объекты — в списки meta;
        новый остаток — в снимок остатков; сторно помечает исходное движение; сформированная потребность убирается
        из кандидатов; изменённые объекты — в кэш (если по ним нет других операций в очереди).
        """
        if r.get("status") != "accepted":
            return
        # Неизменяемые объекты (версия расчёта, ревизия итога) — сразу в локальные списки, не дожидаясь pull:
        # пользователь видит результат фиксации и без связи после неё.
        for rkey, mkey in META_LISTS:
            obj = (r.get("result") or {}).get(rkey)
            if obj:
                items = [x for x in (self.get_meta(mkey) or []) if x.get("id") != obj["id"]]
                self._set_meta(mkey, items + [obj])
        res = r.get("result") or {}
        # Остаток после проводки/резерва — сразу в снимок (без ожидания pull); сторно помечает исходное движение.
        # Перемещение (D-29) возвращает списки: движения ноги и остатки затронутых складов — заменяем по ключу.
        bals = [res["balance"]] if isinstance(res.get("balance"), dict) else []
        bals += [b for b in (res.get("balances") or []) if isinstance(b, dict)]
        for bal in bals:
            if not bal.get("warehouse_id"):
                continue
            key = (bal["warehouse_id"], bal["item_id"])
            rows = [x for x in (self.get_meta("stock_balances") or []) if (x.get("warehouse_id"), x.get("item_id")) != key]
            self._set_meta("stock_balances", rows + [bal])
        new_mv = [m for m in (res.get("movements") or []) if isinstance(m, dict) and m.get("id")]
        if new_mv:
            ids = {m["id"] for m in new_mv}
            self._set_meta("stock_movements", [x for x in (self.get_meta("stock_movements") or []) if x.get("id") not in ids] + new_mv)
        mv = res.get("movement")
        if isinstance(mv, dict) and mv.get("reverses_id"):
            self._set_meta("stock_movements", [dict(x, reversed_by=mv["id"]) if x.get("id") == mv["reverses_id"] else x
                                               for x in (self.get_meta("stock_movements") or [])])
        need = res.get("need")
        if isinstance(need, dict):
            self._set_meta("need_candidates", [c for c in (self.get_meta("need_candidates") or [])
                                               if c.get("calc_version_id") != need.get("calc_version_id")])
        objs = []
        for otype, key in OBJECT_TYPES:
            obj = res.get(otype)
            if isinstance(obj, dict) and obj.get("id"):
                objs.append((otype, obj))
            # Списки объектов в результате (выпуск наряда возвращает его операции, D-36).
            if key != otype and isinstance(res.get(key), list):
                objs += [(otype, x) for x in res[key] if isinstance(x, dict) and x.get("id")]
        for otype, obj in objs:
            self._set_server_state(otype, obj)
            if not self._open_problems(otype, obj["id"]):
                j = json.dumps(obj, ensure_ascii=False)
                self.db.execute("UPDATE cache SET data_enc=?, pending=0 WHERE object_type=? AND id=?",
                                (self._seal("cache", f"{otype}/{obj['id']}/d", j), otype, obj["id"]))

    def _overlay(self, otype, oid, payload):
        """Наложить данные queued-команды на локальную копию объекта (или создать её), пометив «есть локальные правки».
        Так пользователь сразу видит своё изменение офлайн.
        """
        if not oid:
            return
        row = self.db.execute("SELECT data_enc FROM cache WHERE object_type=? AND id=?", (otype, oid)).fetchone()
        if row and otype == "wo_operation" and "kind" in payload:
            data = _op_event_overlay(json.loads(self._open("cache", f"{otype}/{oid}/d", row[0])), payload)
            self.db.execute("UPDATE cache SET data_enc=?, pending=1 WHERE object_type=? AND id=?",
                            (self._seal("cache", f"{otype}/{oid}/d", json.dumps(data, ensure_ascii=False)), otype, oid))
        elif row:
            data = json.loads(self._open("cache", f"{otype}/{oid}/d", row[0]))
            data.update(payload)
            self.db.execute("UPDATE cache SET data_enc=?, pending=1 WHERE object_type=? AND id=?",
                            (self._seal("cache", f"{otype}/{oid}/d", json.dumps(data, ensure_ascii=False)), otype, oid))
        else:
            self.db.execute("INSERT INTO cache VALUES (?,?,?,NULL,NULL,1)",
                            (otype, oid, self._seal("cache", f"{otype}/{oid}/d", json.dumps(payload, ensure_ascii=False))))

    def _refresh_pending_flag(self, otype, oid):
        """Если по объекту больше нет незавершённых операций — вернуть локальную копию к серверной (а созданный офлайн
        и не принятый объект — удалить из кэша).
        """
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


def _op_event_overlay(op: dict, payload: dict) -> dict:
    """Локальная копия операции после события исполнителя (офлайн): те же изменения, что посчитает сервер
    (vector.core.production.apply_event), плюс событие в списке «последние события» с пометкой «не отправлено».

    Ошибку правил здесь не поднимаем: событие уже проверено перед постановкой в очередь (SyncClient.op_event); если
    копия успела измениться (pull), событие просто добавляется в список — решит сервер.
    """
    from vector.core import production as prod
    data = dict(op)
    kind = payload["kind"]
    try:
        dt = prod.parse_device_time(payload["device_time"])
        ch = prod.apply_event(data, kind, dt, int(data.get("wo_qty") or 0), payload.get("qty_done"))
    except prod.ProductionError:
        ch = {}
    for k, v in ch.items():
        data[k] = v.isoformat() if isinstance(v, datetime) else v
    if "fact_seconds" in ch:
        data["fact_min"] = prod.fact_minutes(ch["fact_seconds"])
    if "state" in ch:
        data["state_label"] = prod.OP_STATE_LABELS_RU[ch["state"]]
    if kind == "problem":
        data["problem_count"] = int(data.get("problem_count") or 0) + 1
    data["events"] = list(data.get("events") or []) + [
        {"kind": kind, "kind_label": prod.EVENT_LABELS_RU.get(kind, kind), "device_time": payload["device_time"],
         "server_time": None, "user_id": None, "qty_done": ch.get("qty_done"), "note": payload.get("note"),
         "local": True}]
    return data


def _sha256_of(f) -> tuple[str, int]:
    """SHA-256 и размер файлового объекта, прочитанного блоками от текущей позиции до конца."""
    h, n = hashlib.sha256(), 0
    while True:
        b = f.read(FILE_BLOCK)
        if not b:
            return h.hexdigest(), n
        h.update(b)
        n += len(b)


class _HashingWriter:
    """Обёртка файла для записи: считает SHA-256 записанных байтов по ходу скачивания."""

    def __init__(self, f):
        """Обернуть файл и начать подсчёт SHA-256."""
        self._f, self._h = f, hashlib.sha256()

    def write(self, b) -> int:
        """Учесть байты в хэше и записать их в файл."""
        self._h.update(b)
        return self._f.write(b)

    def hexdigest(self) -> str:
        """SHA-256 всего записанного."""
        return self._h.hexdigest()


def _fsync_dir(path: Path) -> None:
    """Сбросить на диск запись каталога после переименования (POSIX), чтобы сохранение пережило отключение питания.

    На Windows каталог так открыть нельзя, а NTFS журналирует метаданные сама — там это пропускается.
    Ошибка здесь не делает файл недействительным (он уже проверен и на месте), поэтому она не поднимается.
    """
    if os.name != "posix":
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _remaining_size(f) -> int:
    """Размер файлового объекта от текущей позиции (без чтения содержимого)."""
    try:
        return os.fstat(f.fileno()).st_size - f.tell()
    except (AttributeError, OSError, io.UnsupportedOperation):
        pos = f.tell()
        end = f.seek(0, io.SEEK_END)
        f.seek(pos)
        return end - pos


def _fmt_mb(n: int) -> str:
    """Байты → «200» или «0,5» (МБ) для сообщений пользователю."""
    mb = n / (1024 * 1024)
    return str(int(mb)) if mb == int(mb) else f"{mb:.1f}".replace(".", ",")
