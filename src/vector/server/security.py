"""Безопасность учётных записей (собственные учётные записи — решение владельца, D-16).

* Пароли — Argon2id (argon2-cffi), параметры библиотеки по умолчанию (RFC 9106 «second recommended»);
  при входе хэш прозрачно пересчитывается, если параметры устарели.
* Политика паролей и блокировки — параметры SecurityPolicy (значения по умолчанию — консервативные
  технические, требуют утверждения ИБ: OQ-18). Это не бизнес-пороги.
* Токен сеанса — 256 бит из secrets; в БД хранится только SHA-256.
* Ограничение частоты попыток входа — в памяти процесса (один процесс сервера; см. ops/deployment).
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_HASHER = PasswordHasher()
# Фиктивный хэш для выравнивания времени ответа при несуществующем логине (защита от перебора логинов).
_DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(16))

# Небольшой встроенный список самых распространённых паролей; на сервере можно подключить расширенный файл.
_COMMON = {
    "123456789012", "qwertyuiop12", "password1234", "пароль123456", "1q2w3e4r5t6y", "qwerty123456",
    "administrator", "admin1234567", "111111111111", "000000000000", "letmein12345", "welcome12345",
}


@dataclass(frozen=True)
class SecurityPolicy:
    """Технические параметры безопасности (секция [security] в server.toml).

    Значения по умолчанию консервативны, но это не утверждённая политика ИБ компании (OQ-18): их можно поменять в
    конфигурации, validate() не даст задать заведомо опасные значения.
    """
    password_min_length: int = 12
    lockout_threshold: int = 5          # неудачных попыток подряд
    lockout_minutes: int = 15
    session_idle_minutes: int = 120
    session_absolute_hours: int = 12
    login_rate_per_minute_per_addr: int = 20
    # Максимальный размер одного файла версии документа (технический предел, A-16; меняется в [security]).
    max_file_mb: int = 200

    def validate(self) -> None:
        """Отвергнуть недопустимые значения (пароль короче 8, нулевые сроки, размер файла вне 1…2048 МБ)."""
        if self.password_min_length < 8:
            raise ValueError("Минимальная длина пароля не может быть меньше 8")
        if self.lockout_threshold < 1 or self.lockout_minutes < 1:
            raise ValueError("Параметры блокировки должны быть положительными")
        if self.session_idle_minutes < 1 or self.session_absolute_hours < 1:
            raise ValueError("Сроки сеанса должны быть положительными")
        if not 1 <= self.max_file_mb <= 2048:
            raise ValueError("max_file_mb: допустимо от 1 до 2048 МБ")


def configure_hasher_for_tests() -> None:
    """ТОЛЬКО для автотестов: дешёвые параметры Argon2id (иначе тысячи хэшей идут минутами).
    Продуктовые параметры проверяет отдельный тест (test_security.py::test_production_hasher_parameters)."""
    global _HASHER, _DUMMY_HASH
    _HASHER = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    _DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(16))


def production_hasher_parameters() -> dict:
    """Параметры Argon2id в рабочем режиме — для теста, который проверяет, что они не ослаблены."""
    p = PasswordHasher()
    return {"type": p.type.name, "time_cost": p.time_cost, "memory_cost_kib": p.memory_cost, "parallelism": p.parallelism}


def hash_password(password: str) -> str:
    """Хэш пароля Argon2id (соль внутри хэша). Сам пароль нигде не сохраняется."""
    return _HASHER.hash(password)


def verify_password(stored_hash: str | None, password: str) -> tuple[bool, str | None]:
    """Возвращает (совпал?, новый_хэш_если_нужен_пересчёт)."""
    try:
        _HASHER.verify(stored_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    if stored_hash is None:
        return False, None
    return True, (_HASHER.hash(password) if _HASHER.check_needs_rehash(stored_hash) else None)


def password_problems(password: object, login: str, policy: SecurityPolicy) -> list[str]:
    """Почему пароль не подходит — список понятных причин (пустой список = подходит): длина, совпадение с логином,
    распространённый пароль, слишком однообразный.
    """
    if not isinstance(password, str):
        return ["Пароль должен быть строкой."]
    out = []
    if len(password) < policy.password_min_length:
        out.append(f"Пароль короче {policy.password_min_length} символов.")
    if len(password) > 256:
        out.append("Пароль длиннее 256 символов.")
    if password.lower() == login.lower() or login.lower() in password.lower():
        out.append("Пароль не должен содержать логин.")
    if password.lower() in _COMMON:
        out.append("Пароль входит в список распространённых.")
    if len(set(password)) < 5:
        out.append("Пароль слишком однообразен.")
    return out


def new_token() -> tuple[str, str]:
    """Новый токен сеанса: (токен для клиента, его SHA-256 для БД). Утечка таблицы сеансов не даёт войти — токенов в
    ней нет.
    """
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    """SHA-256 токена — по нему сеанс ищется в БД."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class RateLimiter:
    """Скользящее окно 60 с на ключ (адрес клиента)."""
    per_minute: int
    _hits: dict[str, deque] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self, key: str, now: float | None = None) -> bool:
        """Можно ли принять ещё одну попытку входа с этого адреса: не больше per_minute за последние 60 секунд.
        Работает в памяти одного процесса сервера.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            if len(self._hits) > 10000:  # защита памяти: убираем ключи без свежих попыток
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 60]:
                    del self._hits[k]
            return True
