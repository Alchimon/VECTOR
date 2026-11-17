"""Защита локальных данных клиента (закрывает P-4).

* Для каждой пары (сервер, пользователь) — отдельный каталог и отдельный случайный ключ данных (DEK, 256 бит).
* Чувствительные поля локальной БД (кэш объектов, очередь, ответы сервера) шифруются AES-256-GCM
  с AAD = таблица|ключ строки — подмена/перенос значений между строками обнаруживается.
* DEK хранится только в обёрнутом виде: KEK = HKDF(scrypt(пароль, соль) || секрет устройства).
  Секрет устройства лежит в защищённом хранилище ОС (Windows: Credential Manager/DPAPI через keyring),
  поэтому копия каталога профиля на другой машине без учётной записи Windows бесполезна,
  а без пароля пользователя — бесполезна и на этой машине.
* Офлайн-разблокировка: тот же пароль, что при последнем успешном онлайн-входе, и не дольше
  offline_unlock_max_days с момента этого входа (значение по умолчанию — техническое, OQ-18).
* Токен сеанса на диск не пишется (только память процесса).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
KEYRING_SERVICE = "VECTOR-desktop"


class VaultError(Exception):
    """Отказ открыть или создать локальное хранилище: code — для логики клиента, message_ru — для пользователя."""
    def __init__(self, code: str, message_ru: str):
        """Запомнить машинный код и русский текст."""
        super().__init__(message_ru)
        self.code, self.message_ru = code, message_ru


class DeviceSecretStore(Protocol):
    """Интерфейс хранилища секрета устройства: получить или записать строку по имени."""
    def get(self, name: str) -> Optional[str]: ...          # значение по имени или None
    def set(self, name: str, value: str) -> None: ...       # записать значение по имени


class KeyringStore:
    """Секрет устройства в хранилище ОС. Fail-closed: без защищённого хранилища клиент не хранит данные."""

    def __init__(self):
        """Подключить keyring и убедиться, что есть настоящее защищённое хранилище (в Windows — Credential Manager).
        Если нет — отказ no_secure_store: класть ключ в обычный файл нельзя.
        """
        import keyring
        from keyring.backends import fail
        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring) or getattr(kr, "priority", 0) <= 0:
            raise VaultError("no_secure_store", "Не найдено защищённое хранилище ОС для ключей (Windows Credential Manager). "
                                                "Локальный кэш не будет создан.")
        self._kr = keyring

    def get(self, name):
        """Прочитать секрет из хранилища ОС."""
        return self._kr.get_password(KEYRING_SERVICE, name)

    def set(self, name, value):
        """Записать секрет в хранилище ОС."""
        self._kr.set_password(KEYRING_SERVICE, name, value)


class MemoryStore:
    """Только для тестов."""

    def __init__(self):
        """Пустой словарь вместо хранилища ОС."""
        self.d: dict[str, str] = {}

    def get(self, name):
        """Значение по имени или None."""
        return self.d.get(name)

    def set(self, name, value):
        """Записать значение по имени."""
        self.d[name] = value


def server_dir(base: Path, server_url: str) -> Path:
    """Каталог данных одного сервера: имя — первые 16 символов SHA-256 от адреса (без слэша в конце, в нижнем
    регистре). Кэши разных серверов не смешиваются.
    """
    return Path(base) / hashlib.sha256(server_url.rstrip("/").lower().encode()).hexdigest()[:16]


def profile_dir(base: Path, server_url: str, user_id: str) -> Path:
    """Каталог профиля пользователя на сервере. user_id проверяется по формату UUID, чтобы им нельзя было выйти за
    пределы каталога (../).
    """
    if not re.fullmatch(r"[0-9a-f-]{36}", user_id):
        raise VaultError("bad_user", "Некорректный идентификатор пользователя.")
    return server_dir(base, server_url) / user_id


@dataclass
class OpenVault:
    """Открытое хранилище: ключ данных (DEK) в памяти и каталог профиля. Через него шифруются и расшифровываются поля
    локальной БД.
    """
    dek: bytes
    path: Path

    def seal(self, table: str, key: str, plaintext: str) -> str:
        """Зашифровать значение: AES-256-GCM, случайный nonce, AAD = «таблица|ключ». Результат — base64(nonce +
        шифротекст).
        """
        nonce = os.urandom(12)
        ct = AESGCM(self.dek).encrypt(nonce, plaintext.encode("utf-8"), f"{table}|{key}".encode())
        return base64.b64encode(nonce + ct).decode()

    def open(self, table: str, key: str, sealed: str) -> str:
        """Расшифровать значение; при неверном теге (данные изменены, перенесены между строками или повреждены) — отказ
        local_data_corrupt с подсказкой пользователю.
        """
        raw = base64.b64decode(sealed)
        try:
            return AESGCM(self.dek).decrypt(raw[:12], raw[12:], f"{table}|{key}".encode()).decode("utf-8")
        except Exception:
            raise VaultError("local_data_corrupt", "Локальные данные повреждены или изменены вне программы. "
                                                   "Очистите локальный кэш (несинхронизированные изменения будут потеряны).")


class Vault:
    """Локальное хранилище одного пользователя на одном сервере: файл vault.json с обёрнутым ключом данных и временем
    последнего онлайн-входа (для предела офлайн-работы).
    """
    def __init__(self, directory: Path, store: DeviceSecretStore, offline_unlock_max_days: int = 7):
        """Каталог профиля, хранилище секрета устройства и предел офлайн-работы в днях (по умолчанию 7 — техническое
        значение, OQ-18).
        """
        self.dir = Path(directory)
        self.store = store
        self.max_days = offline_unlock_max_days
        self.file = self.dir / "vault.json"
        self.orphaned: Optional[Path] = None

    def _device_secret(self, create: bool) -> Optional[bytes]:
        """Секрет устройства для этого профиля из хранилища ОС; при create=True создаётся (32 случайных байта), если
        его ещё нет.
        """
        name = "device:" + hashlib.sha256(str(self.dir).encode()).hexdigest()[:24]
        v = self.store.get(name)
        if v is None and create:
            v = base64.b64encode(secrets.token_bytes(32)).decode()
            self.store.set(name, v)
        return base64.b64decode(v) if v else None

    @staticmethod
    def _kek(password: str, salt: bytes, dev: bytes, kdf: dict) -> bytes:
        """Ключ обёртки (KEK): scrypt от пароля с солью, затем HKDF вместе с секретом устройства. Нужны оба: пароль
        пользователя И эта учётная запись Windows.
        """
        pk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=kdf["n"], r=kdf["r"], p=kdf["p"], maxmem=256 * 1024 * 1024, dklen=32)
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"VECTOR local vault v1").derive(pk + dev)

    def exists(self) -> bool:
        """Есть ли на этом ПК офлайн-профиль (vault.json)."""
        return self.file.exists()

    def unlock_after_online_login(self, password: str, now: datetime, kdf: dict | None = None) -> OpenVault:
        """После успешного онлайн-входа: открыть (или создать) хранилище и перевыпустить обёртку под текущий пароль."""
        self.dir.mkdir(parents=True, exist_ok=True)
        _restrict(self.dir)
        dev = self._device_secret(create=True)
        dek = None
        if self.file.exists():
            meta = json.loads(self.file.read_text())
            dek = self._try_unwrap(meta, password, dev)
            # Пароль сменён на сервере: старая обёртка не открывается. Старый кэш недоступен — начинаем заново
            # (несинхронизированные операции зашифрованы старым ключом и не могут быть прочитаны без старого пароля).
        self.orphaned: Optional[Path] = None
        if dek is None:
            dek = secrets.token_bytes(32)
            old = [f for f in self.dir.glob("local.db*") if ".orphaned-" not in f.name]
            if old:
                # Ничего не удаляем молча: прежний кэш (возможно, с несинхронизированными операциями) откладывается.
                stamp = now.strftime("%Y%m%dT%H%M%SZ")
                for f in old:
                    f.rename(f.with_name(f"{f.name}.orphaned-{stamp}"))
                self.orphaned = self.dir / f"local.db.orphaned-{stamp}"
        self._write(dek, password, dev, now, kdf or SCRYPT)
        return OpenVault(dek, self.dir)

    def rewrap(self, opened: OpenVault, new_password: str, now: datetime, kdf: dict | None = None) -> None:
        """После смены пароля в программе: тот же ключ данных под новым паролем — очередь сохраняется."""
        dev = self._device_secret(create=True)
        self._write(opened.dek, new_password, dev, now, kdf or SCRYPT)

    def unlock_offline(self, password: str, now: datetime) -> OpenVault:
        """Открыть хранилище без связи с сервером.

        Условия: профиль есть; с последнего онлайн-входа прошло не больше max_days; секрет устройства на месте;
        пароль подходит (тот, что был при последнем онлайн-входе). Каждое нарушение — свой понятный отказ.
        """
        if not self.file.exists():
            raise VaultError("no_offline_profile", "На этом компьютере нет офлайн-профиля этого пользователя. Нужен вход при связи с сервером.")
        meta = json.loads(self.file.read_text())
        last = datetime.fromisoformat(meta["last_online_login"])
        if now - last > timedelta(days=self.max_days):
            raise VaultError("offline_expired", f"Офлайн-работа доступна не дольше {self.max_days} дн. после последнего входа при связи. "
                                                "Подключитесь к сети компании.")
        dev = self._device_secret(create=False)
        if dev is None:
            raise VaultError("device_secret_missing", "Ключ устройства не найден (другая учётная запись Windows или профиль перенесён).")
        dek = self._try_unwrap(meta, password, dev)
        if dek is None:
            raise VaultError("bad_offline_password", "Неверный пароль для офлайн-профиля.")
        return OpenVault(dek, self.dir)

    def _try_unwrap(self, meta: dict, password: str, dev: Optional[bytes]) -> Optional[bytes]:
        """Попробовать развернуть ключ данных паролем и секретом устройства; None — не подошло."""
        if dev is None:
            return None
        kek = self._kek(password, base64.b64decode(meta["salt"]), dev, meta["kdf"])
        raw = base64.b64decode(meta["wrapped"])
        try:
            return AESGCM(kek).decrypt(raw[:12], raw[12:], b"dek")
        except Exception:
            return None

    def _write(self, dek: bytes, password: str, dev: bytes, now: datetime, kdf: dict):
        """Записать vault.json: новая соль, ключ данных, обёрнутый KEK, время входа. Запись атомарная (временный файл +
        replace), права только владельцу.
        """
        salt = os.urandom(16)
        kek = self._kek(password, salt, dev, kdf)
        nonce = os.urandom(12)
        meta = {"v": 1, "kdf": kdf, "salt": base64.b64encode(salt).decode(),
                "wrapped": base64.b64encode(nonce + AESGCM(kek).encrypt(nonce, dek, b"dek")).decode(),
                "last_online_login": now.astimezone(timezone.utc).isoformat()}
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta))
        _restrict(tmp)
        os.replace(tmp, self.file)


def _restrict(p: Path):
    """Права только владельцу (700 для каталога, 600 для файла) там, где ОС это поддерживает."""
    try:
        os.chmod(p, 0o700 if p.is_dir() else 0o600)
    except OSError:
        pass  # Windows: каталог %LOCALAPPDATA% уже доступен только владельцу


def constant_eq(a: str, b: str) -> bool:
    """Сравнение строк за постоянное время (не выдаёт по времени ответа, сколько символов совпало)."""
    return hmac.compare_digest(a.encode(), b.encode())
