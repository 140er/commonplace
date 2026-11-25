"""Inert, append-only opaque storage with explicit Linux secure-open support."""

from __future__ import annotations

import ctypes
import os
import stat
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal, cast

from .records import (
    ErrorCode,
    PutReceipt,
    RecordKind,
    SealedEnvelope,
    SealedPage,
    SealedStoreError,
    ServerWriter,
    StoredSealedRecord,
    WriterTrust,
    validate_id,
    validate_kind,
)

_MAX_SEQUENCE = (1 << 63) - 1
_APPLICATION_ID = 0x5345414C
_DATABASE = "records.sqlite3"
_SIDE_FILES = tuple(_DATABASE + suffix for suffix in ("-journal", "-wal", "-shm"))
_BUSY_SECONDS = 1.0
_Value = int | str | bytes | None
_Row = tuple[_Value, ...]

_INFO_SQL = """CREATE TABLE sealed_info (
 singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
 schema_version INTEGER NOT NULL CHECK (schema_version = 1),
 workspace_id TEXT NOT NULL
) STRICT"""
_OBJECTS_SQL = """CREATE TABLE sealed_objects (
 sequence INTEGER PRIMARY KEY,
 format_version INTEGER NOT NULL CHECK (format_version = 1),
 kind TEXT NOT NULL CHECK (kind IN ('memory', 'printed-recovery')),
 record_id TEXT NOT NULL CHECK (length(record_id) = 32 AND record_id NOT GLOB '*[^0-9a-f]*'),
 key_id TEXT NOT NULL CHECK (length(key_id) = 32 AND key_id NOT GLOB '*[^0-9a-f]*'),
 nonce BLOB NOT NULL CHECK (length(nonce) = 12),
 ciphertext BLOB NOT NULL CHECK (
   (kind = 'memory' AND length(ciphertext) BETWEEN 16 AND 1048576)
   OR (kind = 'printed-recovery' AND length(ciphertext) = 48)),
 writer_user_id TEXT NOT NULL CHECK (length(writer_user_id) > 0),
 writer_connection_id TEXT NOT NULL,
 writer_trust TEXT NOT NULL CHECK (writer_trust IN ('trusted', 'standard', 'untrusted')),
 written_at TEXT NOT NULL,
 UNIQUE (kind, record_id),
 UNIQUE (kind, key_id, nonce)
) STRICT"""
_COLUMNS = (
    "sequence, format_version, kind, record_id, key_id, nonce, ciphertext, "
    "writer_user_id, writer_connection_id, writer_trust, written_at"
)


class _SQLiteFailure(Exception):
    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__("Native SQLite operation refused.")


@lru_cache(maxsize=1)
def _library() -> ctypes.CDLL:
    # CPython's connect() cannot request SQLITE_OPEN_NOFOLLOW. Use the public
    # SQLite C ABI, never CPython object layouts or a /proc descriptor pathname.
    if sys.platform != "linux" or not all(
        hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "pread")
    ):
        raise SealedStoreError("UNSAFE_PATH")
    try:
        library = ctypes.CDLL("libsqlite3.so.0")
        pointer, integer, text = ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p
        prototypes = {
            "sqlite3_libversion_number": ([], integer),
            "sqlite3_open_v2": ([text, ctypes.POINTER(pointer), integer, text], integer),
            "sqlite3_close": ([pointer], integer),
            "sqlite3_busy_timeout": ([pointer, integer], integer),
            "sqlite3_prepare_v2": (
                [pointer, text, integer, ctypes.POINTER(pointer), pointer],
                integer,
            ),
            "sqlite3_step": ([pointer], integer),
            "sqlite3_finalize": ([pointer], integer),
            "sqlite3_bind_int64": ([pointer, integer, ctypes.c_int64], integer),
            "sqlite3_bind_text": ([pointer, integer, text, integer, pointer], integer),
            "sqlite3_bind_blob": ([pointer, integer, pointer, integer, pointer], integer),
            "sqlite3_column_count": ([pointer], integer),
            "sqlite3_column_type": ([pointer, integer], integer),
            "sqlite3_column_int64": ([pointer, integer], ctypes.c_int64),
            "sqlite3_column_blob": ([pointer, integer], pointer),
            "sqlite3_column_bytes": ([pointer, integer], integer),
        }
        for name, (arguments, result) in prototypes.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
        if library.sqlite3_libversion_number() < 3_037_000:
            raise SealedStoreError("UNSAFE_PATH")
    except (OSError, AttributeError):
        raise SealedStoreError("UNSAFE_PATH") from None
    return library


class _Connection:
    def __init__(self, path: Path) -> None:
        self.library = _library()
        self.handle = ctypes.c_void_p()
        # READWRITE | FULLMUTEX | NOFOLLOW; CREATE is deliberately absent.
        result = self.library.sqlite3_open_v2(
            os.fsencode(path), ctypes.byref(self.handle), 0x01010002, b"unix"
        )
        if result:
            self.close()
            if result & 0xFF == 14:
                raise SealedStoreError("UNSAFE_PATH")
            raise _SQLiteFailure(result)
        self.library.sqlite3_busy_timeout(self.handle, int(_BUSY_SECONDS * 1000))

    def close(self) -> None:
        if self.handle:
            self.library.sqlite3_close(self.handle)
            self.handle = ctypes.c_void_p()

    def execute(self, sql: str, parameters: Sequence[_Value] = ()) -> list[_Row]:
        statement = ctypes.c_void_p()
        try:
            result = self.library.sqlite3_prepare_v2(
                self.handle, sql.encode("utf-8"), -1, ctypes.byref(statement), None
            )
            if result:
                raise _SQLiteFailure(result)
            for index, value in enumerate(parameters, 1):
                if type(value) is int:
                    result = self.library.sqlite3_bind_int64(statement, index, value)
                elif isinstance(value, str):
                    encoded = value.encode("utf-8")
                    result = self.library.sqlite3_bind_text(
                        statement, index, encoded, len(encoded), ctypes.c_void_p(-1)
                    )
                elif isinstance(value, bytes):
                    result = self.library.sqlite3_bind_blob(
                        statement, index, value, len(value), ctypes.c_void_p(-1)
                    )
                else:
                    raise SealedStoreError("INVALID_RECORD")
                if result:
                    raise _SQLiteFailure(result)
            rows: list[_Row] = []
            while True:
                result = self.library.sqlite3_step(statement)
                if result == 101:  # SQLITE_DONE
                    return rows
                if result != 100:  # SQLITE_ROW
                    raise _SQLiteFailure(result)
                row: list[_Value] = []
                for column in range(self.library.sqlite3_column_count(statement)):
                    kind = self.library.sqlite3_column_type(statement, column)
                    if kind == 1:
                        row.append(int(self.library.sqlite3_column_int64(statement, column)))
                    elif kind in (3, 4):
                        size = self.library.sqlite3_column_bytes(statement, column)
                        data = ctypes.string_at(
                            self.library.sqlite3_column_blob(statement, column), size
                        )
                        row.append(data.decode("utf-8") if kind == 3 else data)
                    elif kind == 5:
                        row.append(None)
                    else:
                        raise SealedStoreError("STORAGE_UNAVAILABLE")
                rows.append(tuple(row))
        finally:
            if statement:
                self.library.sqlite3_finalize(statement)


def _private_file(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
        raise SealedStoreError("UNSAFE_PATH")


def _same(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


@dataclass
class _Paths:
    path: Path
    chain: list[tuple[int, str, int]]
    database: int = -1
    created: bool = False

    @property
    def directory(self) -> int:
        return self.chain[-1][2]

    def verify(self) -> None:
        for parent, name, child in self.chain:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not _same(current, os.fstat(child)) or not stat.S_ISDIR(current.st_mode):
                raise SealedStoreError("UNSAFE_PATH")
        current = os.stat(_DATABASE, dir_fd=self.directory, follow_symlinks=False)
        _private_file(current)
        if not _same(current, os.fstat(self.database)):
            raise SealedStoreError("UNSAFE_PATH")
        self.check_side_files()

    def check_side_files(self, *, refuse_existing: bool = False) -> None:
        for name in _SIDE_FILES:
            try:
                info = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            _private_file(info)
            if refuse_existing:
                # Do not recover a hot journal before validating schema/binding.
                raise SealedStoreError("STORAGE_UNAVAILABLE")


@contextmanager
def _paths(data_dir: Path, workspace_id: str, *, create: bool) -> Iterator[_Paths]:
    _library()
    import fcntl

    if ".." in data_dir.parts:
        raise SealedStoreError("UNSAFE_PATH")
    target = Path(os.path.abspath(data_dir)) / "sealed" / workspace_id
    descriptors: list[int] = []
    paths = _Paths(target / _DATABASE, [])
    try:
        current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        descriptors.append(current)
        data_depth = len(Path(os.path.abspath(data_dir)).parts) - 1
        for depth, name in enumerate(target.parts[1:], 1):
            try:
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=current,
                )
            except FileNotFoundError:
                if not create:
                    raise SealedStoreError("NOT_INITIALIZED") from None
                try:
                    os.mkdir(name, mode=0o700, dir_fd=current)
                except FileExistsError:
                    pass
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=current,
                )
            descriptors.append(child)
            info = os.fstat(child)
            private = depth >= data_depth
            if info.st_uid not in (0, os.geteuid()) or (
                info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
            ):
                raise SealedStoreError("UNSAFE_PATH")
            if private and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise SealedStoreError("UNSAFE_PATH")
            paths.chain.append((current, name, child))
            current = child
        # Directory-inode flock serializes owned connections before opening a DB
        # descriptor. Closing another raw DB fd must not drop a live POSIX lock.
        deadline = time.monotonic() + _BUSY_SECONDS
        while True:
            try:
                fcntl.flock(current, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SealedStoreError("STORAGE_UNAVAILABLE") from None
                time.sleep(min(0.01, remaining))
        paths.check_side_files(refuse_existing=True)
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            paths.database = os.open(_DATABASE, flags, dir_fd=current)
        except FileNotFoundError:
            if not create:
                raise SealedStoreError("NOT_INITIALIZED") from None
            paths.database = os.open(
                _DATABASE, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=current
            )
            paths.created = True
        _private_file(os.fstat(paths.database))
        paths.verify()
        if not paths.created:
            header = os.pread(paths.database, 100, 0)
            if len(header) != 100 or header[:16] != b"SQLite format 3\x00":
                raise SealedStoreError("UNSUPPORTED_SCHEMA")
            if int.from_bytes(header[68:72], "big") != _APPLICATION_ID:
                raise SealedStoreError("UNSUPPORTED_SCHEMA")
            if header[18:20] != b"\x01\x01":
                raise SealedStoreError("UNSUPPORTED_SCHEMA")
        yield paths
    except OSError:
        raise SealedStoreError("UNSAFE_PATH") from None
    finally:
        if paths.database >= 0:
            os.close(paths.database)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _schema(connection: _Connection, workspace_id: str) -> None:
    entries = connection.execute(
        "SELECT name, sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY name"
    )
    if entries != [("sealed_info", _INFO_SQL), ("sealed_objects", _OBJECTS_SQL)]:
        raise SealedStoreError("UNSUPPORTED_SCHEMA")
    info = connection.execute("SELECT singleton, schema_version, workspace_id FROM sealed_info")
    if len(info) != 1 or info[0][:2] != (1, 1):
        raise SealedStoreError("UNSUPPORTED_SCHEMA")
    if info[0][2] != workspace_id:
        raise SealedStoreError("WORKSPACE_MISMATCH")


def _record(row: _Row, workspace_id: str) -> StoredSealedRecord:
    try:
        envelope = SealedEnvelope(
            cast(Literal[1], row[1]),
            cast(RecordKind, row[2]),
            cast(str, row[3]),
            cast(str, row[4]),
            cast(bytes, row[5]),
            cast(bytes, row[6]),
        )
        writer = ServerWriter(cast(str, row[7]), cast(str, row[8]), cast(WriterTrust, row[9]))
        stamp = cast(str, row[10])
        if type(row[0]) is not int or not 1 <= row[0] <= _MAX_SEQUENCE:
            raise ValueError
        if not isinstance(stamp, str) or not stamp.endswith("Z"):
            raise ValueError
        datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return StoredSealedRecord(workspace_id, row[0], envelope, writer, stamp)
    except (SealedStoreError, ValueError, TypeError, IndexError):
        raise SealedStoreError("STORAGE_UNAVAILABLE") from None


def _cursor(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SEQUENCE:
        raise SealedStoreError("INVALID_RECORD")
    return value


class SealedStore:
    def __init__(self, data_dir: Path, workspace_id: str, clock: Callable[[], datetime]) -> None:
        self._data_dir, self._workspace_id, self._clock = data_dir, workspace_id, clock
        self._closed = False

    @contextmanager
    def _session(self, *, create: bool = False) -> Iterator[_Connection]:
        if self._closed:
            raise SealedStoreError("STORAGE_UNAVAILABLE")
        try:
            with _paths(self._data_dir, self._workspace_id, create=create) as paths:
                connection = _Connection(paths.path)
                try:
                    paths.verify()
                    connection.execute("PRAGMA trusted_schema = OFF")
                    if paths.created:
                        connection.execute("BEGIN IMMEDIATE")
                        connection.execute(_INFO_SQL)
                        connection.execute(_OBJECTS_SQL)
                        connection.execute(
                            "INSERT INTO sealed_info VALUES (1, 1, ?)", (self._workspace_id,)
                        )
                        connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
                        paths.verify()
                        connection.execute("COMMIT")
                    _schema(connection, self._workspace_id)
                    yield connection
                    paths.verify()
                finally:
                    connection.close()  # Rolls an uncommitted transaction back.
        except _SQLiteFailure as exc:
            code: ErrorCode = "CONFLICT" if exc.code & 0xFF == 19 else "STORAGE_UNAVAILABLE"
            raise SealedStoreError(code) from None
        except (UnicodeError, OverflowError):
            raise SealedStoreError("STORAGE_UNAVAILABLE") from None

    def put(self, envelope: SealedEnvelope, *, writer: ServerWriter) -> PutReceipt:
        if not isinstance(envelope, SealedEnvelope) or not isinstance(writer, ServerWriter):
            raise SealedStoreError("INVALID_RECORD")
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                f"SELECT {_COLUMNS} FROM sealed_objects WHERE kind = ? AND record_id = ?",
                (envelope.kind, envelope.record_id),
            )
            if prior:
                record = _record(prior[0], self._workspace_id)
                identity = (writer.user_id, writer.connection_id)
                if record.envelope != envelope or identity != (
                    record.writer.user_id,
                    record.writer.connection_id,
                ):
                    raise SealedStoreError("CONFLICT")
                connection.execute("COMMIT")
                return PutReceipt(record, False)
            try:
                now = self._clock()
                if not isinstance(now, datetime) or now.utcoffset() is None:
                    raise ValueError
                stamp = (
                    now.astimezone(timezone.utc)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z")
                )
            except Exception:
                raise SealedStoreError("STORAGE_UNAVAILABLE") from None
            connection.execute(
                "INSERT INTO sealed_objects "
                "(format_version, kind, record_id, key_id, nonce, ciphertext, "
                "writer_user_id, writer_connection_id, writer_trust, written_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    envelope.format_version,
                    envelope.kind,
                    envelope.record_id,
                    envelope.key_id,
                    envelope.nonce,
                    envelope.ciphertext,
                    writer.user_id,
                    writer.connection_id,
                    writer.trust,
                    stamp,
                ),
            )
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM sealed_objects WHERE kind = ? AND record_id = ?",
                (envelope.kind, envelope.record_id),
            )
            record = _record(rows[0], self._workspace_id)
            connection.execute("COMMIT")
            return PutReceipt(record, True)

    def get(self, *, kind: RecordKind, record_id: str) -> StoredSealedRecord | None:
        validate_kind(kind)
        validate_id(record_id)
        with self._session() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM sealed_objects WHERE kind = ? AND record_id = ?",
                (kind, record_id),
            )
            return _record(rows[0], self._workspace_id) if rows else None

    def list(
        self, *, kind: RecordKind, after: int = 0, through: int | None = None, limit: int = 100
    ) -> SealedPage:
        validate_kind(kind)
        _cursor(after)
        if through is not None:
            _cursor(through)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise SealedStoreError("INVALID_RECORD")
        with self._session() as connection:
            high = cast(
                int,
                connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM sealed_objects")[0][0],
            )
            bound = high if through is None else through
            if after > bound or bound > high:
                raise SealedStoreError("INVALID_RECORD")
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM sealed_objects "
                "WHERE kind = ? AND sequence > ? AND sequence <= ? ORDER BY sequence LIMIT ?",
                (kind, after, bound, limit + 1),
            )
            records = tuple(_record(row, self._workspace_id) for row in rows[:limit])
            return SealedPage(records, bound, records[-1].sequence if len(rows) > limit else None)

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> SealedStore:
        if self._closed:
            raise SealedStoreError("STORAGE_UNAVAILABLE")
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def open_sealed_store(
    data_dir: Path, workspace_id: str, *, create: bool = False, clock: Callable[[], datetime] = _now
) -> SealedStore:
    if (
        type(workspace_id) is not str
        or not workspace_id.startswith("ws_")
        or not workspace_id[3:].isalnum()
    ):
        raise SealedStoreError("UNSAFE_PATH")
    store = SealedStore(Path(data_dir), workspace_id, clock)
    with store._session(create=create):
        pass
    return store
