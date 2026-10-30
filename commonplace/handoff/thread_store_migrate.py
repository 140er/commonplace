"""Verified v1 -> v2 row copy. Explicit utility; no production switch.

The source is opened read-only in a snapshot. The destination must be empty.
Any mismatch rolls back every copied row, keeping the original file intact.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .thread_store import ThreadStore, canonical_sha256
from .thread_store_v2 import ThreadStoreV2
from .thread_store_v2_schema import FILENAME


class MigrationMismatch(ValueError):
    """The source's rows or the copied content fail lossless verification."""


def migrate_v1(source: str, destination: str | None = None) -> int:
    source_path = Path(source).resolve()
    target = Path(destination) if destination is not None else source_path.with_name(FILENAME)
    if target.resolve() == source_path:
        raise ValueError("Migration needs a separate v2 file")
    old = ThreadStore.__new__(ThreadStore)
    old._lock = threading.RLock()
    old._conn = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True, isolation_level=None)
    old._conn.row_factory = sqlite3.Row
    try:
        old._conn.execute("BEGIN")
        new = ThreadStoreV2(str(target))
        try:
            return _copy(old, new)
        finally:
            new.close()
    finally:
        old.close()


def _copy(old, new):
    with new._lock, new._conn:
        new._conn.execute("BEGIN IMMEDIATE")
        for table in (
            "threads",
            "line",
            "message",
            "block",
            "blob",
            "dict",
            "chunk",
            "handoff_docs",
            "fact_extraction",
        ):
            if new._conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise ValueError("Migration destination must be empty")
        threads = old._conn.execute("SELECT * FROM threads ORDER BY rowid").fetchall()
        for row in threads:
            names = list(row.keys())
            new._conn.execute(
                f"INSERT INTO threads ({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
                tuple(row),
            )
        for row in threads:
            items = old.messages(row["id"])
            chunks = old.chunks(row["id"])
            if (
                not items
                or len(items) != row["message_count"]
                or len(chunks) != row["chunk_count"]
                or any(item.seq != seq for seq, item in enumerate(items))
                or any(chunk.index != idx for idx, chunk in enumerate(chunks))
                or canonical_sha256(items) != row["sha256"]
            ):
                raise MigrationMismatch("Source thread metadata or digest mismatch")
            new._write_messages(row["id"], items)
            try:
                new._write_chunks(row["id"], chunks)
            except (ValueError, IndexError) as error:
                raise MigrationMismatch("Copied chunk text mismatch") from error
            rebuilt = new.messages(row["id"])
            if rebuilt != items:
                raise MigrationMismatch("Copied message fields mismatch")
            if new.chunks(row["id"]) != chunks:
                raise MigrationMismatch("Copied chunk boundaries or text mismatch")
            if canonical_sha256(rebuilt) != row["sha256"]:
                raise MigrationMismatch("Copied thread digest mismatch")
        for table in ("handoff_docs", "fact_extraction"):
            rows = old._conn.execute(f"SELECT * FROM {table}").fetchall()
            if rows:
                placeholders = ",".join("?" for _ in rows[0])
                new._conn.executemany(
                    f"INSERT INTO {table} VALUES ({placeholders})", map(tuple, rows)
                )
            copied = new._conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            original = old._conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            if list(map(tuple, copied)) != list(map(tuple, original)):
                raise MigrationMismatch("Copied auxiliary rows mismatch")
        return len(threads)
