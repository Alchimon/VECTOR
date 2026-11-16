"""Зашифрованное логическое резервное копирование и восстановление (формат .vbak v1).

Свойства (проверяются тестами tests/server/test_backup.py):
* консистентный снимок: экспорт в одной транзакции чтения;
* шифрование AES-256-GCM поблочно; nonce = префикс(8) || счётчик(4); AAD = SHA-256(заголовка) || счётчик || флаг последнего блока;
  -> подмена/перестановка/удаление/обрезка блоков и изменение заголовка обнаруживаются;
* ключ: файл ключа (32 случайных байта, base64) или парольная фраза -> scrypt (n=2^15, r=8, p=1);
  ключ/фраза не принимаются из командной строки (видны в списке процессов);
* целостность данных: число строк и SHA-256 (независимый от порядка и СУБД) по каждой таблице;
* восстановление только в ПУСТУЮ БД; до успешной сверки в schema_meta стоит restore_in_progress —
  сервер с такой БД не запустится (fail-closed);
* переносимость: резервная копия PostgreSQL восстанавливается в MS SQL/SQLite и наоборот.

Это дополнение, а не замена штатных средств СУБД (pg_dump/PITR, BACKUP DATABASE) — см. docs/ops/backup-restore.md.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import secrets
import struct
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import IO, Any, Iterator, Optional

import sqlalchemy as sa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from vector import __version__

from . import db

MAGIC = b"VECTORBK"
FORMAT_VERSION = 1
CHUNK = 1 << 20
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


class BackupError(Exception):
    pass


# ---------------------------------------------------------------- ключи
@dataclass(frozen=True)
class KeySource:
    key_file: Optional[Path] = None
    passphrase: Optional[str] = None

    def derive(self, kdf: dict) -> bytes:
        if kdf["kdf"] == "keyfile":
            if not self.key_file:
                raise BackupError("Копия зашифрована файлом ключа — укажите --key-file.")
            raw = base64.b64decode(Path(self.key_file).read_text().strip())
            if len(raw) != 32:
                raise BackupError("Файл ключа повреждён: ожидается 32 байта в base64.")
            fp = hashlib.sha256(raw).hexdigest()[:16]
            if fp != kdf["key_fingerprint"]:
                raise BackupError("Файл ключа не подходит к этой резервной копии (отпечаток не совпадает).")
            return raw
        if kdf["kdf"] == "scrypt":
            if not self.passphrase:
                raise BackupError("Копия зашифрована парольной фразой — задайте её в VECTOR_BACKUP_PASSPHRASE или введите интерактивно.")
            return hashlib.scrypt(self.passphrase.encode("utf-8"), salt=base64.b64decode(kdf["salt"]),
                                  n=kdf["n"], r=kdf["r"], p=kdf["p"], maxmem=256 * 1024 * 1024, dklen=32)
        raise BackupError(f"Неизвестный способ получения ключа: {kdf['kdf']}")

    def new_kdf(self) -> tuple[dict, bytes]:
        if self.key_file:
            raw = base64.b64decode(Path(self.key_file).read_text().strip())
            if len(raw) != 32:
                raise BackupError("Файл ключа повреждён: ожидается 32 байта в base64.")
            return {"kdf": "keyfile", "key_fingerprint": hashlib.sha256(raw).hexdigest()[:16]}, raw
        if self.passphrase:
            if len(self.passphrase) < 16:
                raise BackupError("Парольная фраза для резервных копий — не короче 16 символов.")
            kdf = {"kdf": "scrypt", "salt": base64.b64encode(os.urandom(16)).decode(), **SCRYPT}
            return kdf, self.derive(kdf)
        raise BackupError("Не задан ключ шифрования: --key-file или VECTOR_BACKUP_PASSPHRASE. Незашифрованные копии не создаются.")


def generate_key_file(path: Path) -> str:
    if path.exists():
        raise BackupError(f"Файл {path} уже существует — перезапись ключа запрещена.")
    raw = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(base64.b64encode(raw).decode() + "\n")
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------- кодирование значений
def _enc(v: Any) -> Any:
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, Decimal):
        return {"$d": format(v, "f")}
    if isinstance(v, datetime):
        return {"$ts": v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
    if isinstance(v, date):
        return {"$date": v.isoformat()}
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$b64": base64.b64encode(bytes(v)).decode()}
    raise BackupError(f"Неподдерживаемый тип значения: {type(v).__name__}")


def _dec(v: Any) -> Any:
    if isinstance(v, dict):
        if "$d" in v:
            return Decimal(v["$d"])
        if "$ts" in v:
            return datetime.strptime(v["$ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        if "$date" in v:
            return date.fromisoformat(v["$date"])
        if "$b64" in v:
            return base64.b64decode(v["$b64"])
    return v


def _row_digest(values: list) -> str:
    return hashlib.sha256(json.dumps([_enc(x) for x in values], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class TableDigest:
    """Хэш, не зависящий от порядка строк и от СУБД: SHA-256 отсортированных хэшей строк."""

    def __init__(self):
        self.rows: list[str] = []

    def add(self, values: list) -> None:
        self.rows.append(_row_digest(values))

    def hexdigest(self) -> str:
        h = hashlib.sha256()
        for r in sorted(self.rows):
            h.update(r.encode())
        return h.hexdigest()


# ---------------------------------------------------------------- поток шифрования
class _Writer:
    def __init__(self, out: IO[bytes], key: bytes, header: dict):
        self.out, self.aes = out, AESGCM(key)
        self.prefix = os.urandom(8)
        header = {**header, "nonce_prefix": base64.b64encode(self.prefix).decode(), "chunk_size": CHUNK}
        hb = json.dumps(header, ensure_ascii=False, sort_keys=True).encode()
        out.write(MAGIC + struct.pack(">HI", FORMAT_VERSION, len(hb)) + hb)
        self.hhash = hashlib.sha256(hb).digest()
        self.buf, self.counter = bytearray(), 0

    def write(self, data: bytes) -> None:
        self.buf += data
        while len(self.buf) > CHUNK:
            self._emit(bytes(self.buf[:CHUNK]), False)
            del self.buf[:CHUNK]

    def _emit(self, chunk: bytes, final: bool) -> None:
        nonce = self.prefix + struct.pack(">I", self.counter)
        aad = self.hhash + struct.pack(">QB", self.counter, 1 if final else 0)
        ct = self.aes.encrypt(nonce, chunk, aad)
        self.out.write(struct.pack(">I", len(ct)) + ct)
        self.counter += 1
        if self.counter >= 2 ** 32:
            raise BackupError("Слишком большая резервная копия для одного файла.")

    def close(self) -> None:
        self._emit(bytes(self.buf), True)
        self.buf.clear()


def read_header(f: IO[bytes]) -> tuple[dict, bytes]:
    if f.read(8) != MAGIC:
        raise BackupError("Это не файл резервной копии ВЕКТОР.")
    ver, hlen = struct.unpack(">HI", f.read(6))
    if ver != FORMAT_VERSION:
        raise BackupError(f"Версия формата {ver} не поддерживается этой версией программы.")
    if hlen > 1 << 16:
        raise BackupError("Повреждённый заголовок.")
    hb = f.read(hlen)
    return json.loads(hb), hb


def _decrypt_stream(f: IO[bytes], keys: KeySource) -> tuple[dict, bytes]:
    header, hb = read_header(f)
    key = keys.derive(header["kdf"])
    aes, prefix, hhash = AESGCM(key), base64.b64decode(header["nonce_prefix"]), hashlib.sha256(hb).digest()
    out, counter, final_seen = bytearray(), 0, False
    while True:
        lb = f.read(4)
        if not lb:
            break
        if final_seen:
            raise BackupError("После последнего блока есть лишние данные — файл изменён.")
        (n,) = struct.unpack(">I", lb)
        if n > CHUNK + 16:
            raise BackupError("Повреждённый блок.")
        ct = f.read(n)
        if len(ct) != n:
            raise BackupError("Файл обрезан.")
        nonce = prefix + struct.pack(">I", counter)
        try:
            pt = aes.decrypt(nonce, ct, hhash + struct.pack(">QB", counter, 1))
            final_seen = True
        except Exception:
            try:
                pt = aes.decrypt(nonce, ct, hhash + struct.pack(">QB", counter, 0))
            except Exception:
                raise BackupError("Проверка подлинности не пройдена: неверный ключ или файл повреждён/изменён.")
        out += pt
        counter += 1
    if not final_seen:
        raise BackupError("Нет завершающего блока — файл обрезан.")
    return header, bytes(out)


# ---------------------------------------------------------------- резервное копирование
@dataclass
class BackupReport:
    path: Path
    counts: dict[str, int]
    hashes: dict[str, str]
    size: int
    header: dict = field(default_factory=dict)


def _snapshot_conn(engine: sa.Engine):
    name = engine.dialect.name
    if name == "postgresql":
        return engine.connect().execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
    return engine.connect()  # MS SQL: SERIALIZABLE (или SNAPSHOT, если включён в БД); SQLite: BEGIN IMMEDIATE


def create_backup(engine: sa.Engine, out_path: Path, keys: KeySource) -> BackupReport:
    out_path = Path(out_path)
    if out_path.exists():
        raise BackupError(f"Файл {out_path} уже существует — перезапись запрещена.")
    kdf, key = keys.new_kdf()
    tmp = out_path.with_name(out_path.name + ".partial")
    counts: dict[str, int] = {}
    hashes: dict[str, str] = {}
    try:
        with _snapshot_conn(engine) as conn, conn.begin():
            meta = {r.key: r.value for r in conn.execute(sa.select(db.schema_meta))}
            if meta.get("restore_in_progress"):
                raise BackupError("БД в состоянии незавершённого восстановления — копирование запрещено.")
            header = {"product": "VECTOR", "app_version": __version__, "schema_version": int(meta["schema_version"]),
                      "installation_id": meta.get("installation_id"), "source_dialect": engine.dialect.name,
                      "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                      "cipher": "AES-256-GCM", "compression": "gzip", "kdf": kdf, "tables": db.BACKUP_TABLE_ORDER}
            with open(tmp, "xb") as raw:
                w = _Writer(raw, key, header)
                gz = gzip.GzipFile(fileobj=_Sink(w), mode="wb", mtime=0)
                for name in db.BACKUP_TABLE_ORDER:
                    t = db.metadata.tables[name]
                    cols = [c.name for c in t.columns]
                    gz.write(_line({"type": "table", "name": name, "columns": cols}))
                    dg, n = TableDigest(), 0
                    for row in conn.execute(sa.select(t)):
                        vals = list(row)
                        dg.add(vals)
                        gz.write(_line({"type": "row", "v": [_enc(x) for x in vals]}))
                        n += 1
                    counts[name], hashes[name] = n, dg.hexdigest()
                gz.write(_line({"type": "end", "counts": counts, "hashes": hashes}))
                gz.close()
                w.close()
                raw.flush()
                os.fsync(raw.fileno())
        os.replace(tmp, out_path)  # атомарно: частичный файл никогда не выглядит готовой копией
        _fsync_dir(out_path.parent)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
    return BackupReport(out_path, counts, hashes, out_path.stat().st_size, header)


class _Sink(io.RawIOBase):
    def __init__(self, w: _Writer):
        self.w = w

    def writable(self):
        return True

    def write(self, b):
        self.w.write(bytes(b))
        return len(b)


def _line(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return  # Windows: каталоги не открываются для fsync
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------- чтение и проверка
def _records(path: Path, keys: KeySource) -> tuple[dict, Iterator[dict]]:
    with open(path, "rb") as f:
        header, plain = _decrypt_stream(f, keys)
    data = gzip.decompress(plain)

    def it():
        for line in data.splitlines():
            if line:
                yield json.loads(line)
    return header, it()


@dataclass
class VerifyReport:
    header: dict
    counts: dict[str, int]
    ok: bool
    problems: list[str]


def verify_backup(path: Path, keys: KeySource) -> VerifyReport:
    header, recs = _records(path, keys)
    problems: list[str] = []
    current, dg, counts, hashes, end = None, None, {}, {}, None
    for r in recs:
        if r["type"] == "table":
            if current:
                counts[current], hashes[current] = n, dg.hexdigest()
            current, dg, n = r["name"], TableDigest(), 0
        elif r["type"] == "row":
            dg.add([_dec(x) for x in r["v"]])
            n += 1
        elif r["type"] == "end":
            if current:
                counts[current], hashes[current] = n, dg.hexdigest()
            end = r
    if end is None:
        problems.append("Нет итоговой записи манифеста.")
    else:
        for t in header["tables"]:
            if end["counts"].get(t) != counts.get(t):
                problems.append(f"{t}: число строк {counts.get(t)} ≠ {end['counts'].get(t)} в манифесте.")
            if end["hashes"].get(t) != hashes.get(t):
                problems.append(f"{t}: контрольная сумма не совпадает.")
    return VerifyReport(header, counts, not problems, problems)


# ---------------------------------------------------------------- восстановление
@dataclass
class RestoreReport:
    counts: dict[str, int]
    verified: bool
    problems: list[str]


def restore_backup(path: Path, keys: KeySource, target: sa.Engine, batch: int = 500,
                   batch_bytes: int = 16 * 1024 * 1024) -> RestoreReport:
    """Восстановить копию в ПУСТУЮ БД и сверить результат с дайджестами копии.

    Строки вставляются пачками: до `batch` строк ИЛИ до ~`batch_bytes` двоичных данных — что наступит раньше.
    Второе ограничение важно для file_chunks (строка = блок файла до 1 МБ): без него пачка из 500 строк
    занимала бы ~500 МБ памяти. Остающееся ограничение — расшифровка копии целиком в память (issue #8).
    """
    vr = verify_backup(path, keys)
    if not vr.ok:
        raise BackupError("Резервная копия не прошла проверку: " + "; ".join(vr.problems))
    header, recs = _records(path, keys)
    if header["schema_version"] != db.SCHEMA_VERSION:
        raise BackupError(f"Копия со схемой v{header['schema_version']}, программа ожидает v{db.SCHEMA_VERSION}. "
                          "Восстановите той же версией программы, затем выполните миграцию.")
    with target.connect() as c:
        if sa.inspect(c).get_table_names():
            raise BackupError("Целевая БД не пуста. Восстановление выполняется только в пустую БД (защита от перезаписи).")
    db.metadata.create_all(target)
    with target.begin() as conn:
        conn.execute(db.schema_meta.insert().values(key="restore_in_progress", value=header["created_at"]))

    current, cols, buf, end = None, None, [], None
    buf_bytes = 0   # объём двоичных значений в текущей пачке (строки/числа не считаем — они малы)

    def flush(conn):
        nonlocal buf_bytes
        if buf:
            t = db.metadata.tables[current]
            if current == "schema_meta":
                rows = [r for r in buf if r["key"] != "restore_in_progress"]
            else:
                rows = list(buf)
            if rows:
                ident = conn.dialect.name == "mssql" and any(c.autoincrement is True for c in t.primary_key.columns)
                if ident:
                    conn.exec_driver_sql(f"SET IDENTITY_INSERT {t.name} ON")
                conn.execute(t.insert(), rows)
                if ident:
                    conn.exec_driver_sql(f"SET IDENTITY_INSERT {t.name} OFF")
            buf.clear()
        buf_bytes = 0

    with target.begin() as conn:
        for r in recs:
            if r["type"] == "table":
                flush(conn)
                current, cols = r["name"], r["columns"]
            elif r["type"] == "row":
                vals = [_dec(x) for x in r["v"]]
                buf.append(dict(zip(cols, vals)))
                buf_bytes += sum(len(v) for v in vals if isinstance(v, (bytes, bytearray, memoryview)))
                if len(buf) >= batch or buf_bytes >= batch_bytes:
                    flush(conn)
            elif r["type"] == "end":
                flush(conn)
                end = r
        _reset_sequences(conn)

    # Сверка: пересчитываем из целевой БД теми же правилами.
    problems: list[str] = []
    counts: dict[str, int] = {}
    with target.connect() as conn:
        for name in header["tables"]:
            t = db.metadata.tables[name]
            dg, n = TableDigest(), 0
            for row in conn.execute(sa.select(t)):
                vals = list(row)
                if name == "schema_meta" and vals[0] == "restore_in_progress":
                    continue
                dg.add(vals)
                n += 1
            counts[name] = n
            if n != end["counts"][name]:
                problems.append(f"{name}: восстановлено {n} строк из {end['counts'][name]}.")
            elif dg.hexdigest() != end["hashes"][name]:
                problems.append(f"{name}: контрольная сумма после восстановления не совпадает.")
    if not problems:
        with target.begin() as conn:
            conn.execute(db.schema_meta.delete().where(db.schema_meta.c.key == "restore_in_progress"))
    return RestoreReport(counts, not problems, problems)


def _reset_sequences(conn) -> None:
    if conn.dialect.name != "postgresql":
        return  # SQLite — по max(rowid); MS SQL — IDENTITY сдвигается при явной вставке большего значения
    for t in db.metadata.sorted_tables:
        pk = list(t.primary_key.columns)
        if len(pk) == 1 and pk[0].autoincrement is True:
            conn.execute(sa.text(f"SELECT setval(pg_get_serial_sequence('{t.name}', '{pk[0].name}'), "
                                 f"COALESCE((SELECT MAX({pk[0].name}) FROM {t.name}), 0) + 1, false)"))


def prune_backups(directory: Path, keep: int) -> list[Path]:
    """Удаляет самые старые .vbak сверх keep. Не трогает файлы других форматов."""
    if keep < 1:
        raise BackupError("Нужно хранить хотя бы одну копию.")
    files = sorted(Path(directory).glob("*.vbak"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = files[keep:]
    for p in removed:
        p.unlink()
    return removed
