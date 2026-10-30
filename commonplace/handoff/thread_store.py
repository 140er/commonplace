"""Lossless thread store with a full-text index. Owner: part T02.

One SQLite file per workspace (``<data>/workspaces/<id>/threads.sqlite3``,
mode 0600). Messages are stored whole, verbatim, one row per
``ThreadMessage`` (``raw`` included). Chunks are views over consecutive
messages (``chunk_messages``) indexed in FTS5 for ``search``.

Idempotent: storing the same ``source_thread_id`` again returns the existing
``StoredThread`` and writes nothing; storing different content under the
same source id raises ``ThreadConflict``. A crash mid-store leaves nothing
behind (one transaction). Never deletes.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import CHUNK_MAX_CHARS, THREADS_WING, RecallHit, StoredThread, ThreadMessage


class ThreadConflict(ValueError):
    """Same source thread id, different content."""


@dataclass(frozen=True)
class Chunk:
    index: int
    seq_start: int  # inclusive
    seq_end: int  # inclusive
    text: str  # "[<seq>] <role> <kind>[ <name>]: <text>" lines, verbatim text


def chunk_messages(messages: list, max_chars: int = CHUNK_MAX_CHARS) -> list[Chunk]:
    """Group whole messages up to ``max_chars``. One message longer than
    ``max_chars`` is split into consecutive pieces of that message only
    (same seq_start == seq_end), so every character is in exactly one chunk.

    The limit includes the line prefix and separators. A split piece repeats
    its message's prefix; a limit too small to hold that prefix and any text
    raises ``ValueError``. Whitespace and line breaks in text stay untouched.
    """
    if max_chars <= 0:
        raise ValueError("Chunk size must be positive")

    chunks = []
    lines = []
    length = 0
    seq_start = seq_end = 0

    def flush() -> None:
        nonlocal length
        if lines:
            chunks.append(Chunk(len(chunks), seq_start, seq_end, "\n".join(lines)))
            lines.clear()
            length = 0

    for message in messages:
        prefix = f"[{message.seq}] {message.role} {message.kind}"
        if message.name:
            prefix += f" {message.name}"
        prefix += ": "
        capacity = max_chars - len(prefix)
        if capacity < 0 or (capacity == 0 and message.text):
            raise ValueError("Chunk size is too small for the message prefix")

        line = prefix + message.text
        if len(line) > max_chars:
            flush()
            for offset in range(0, len(message.text), capacity):
                chunks.append(
                    Chunk(
                        len(chunks),
                        message.seq,
                        message.seq,
                        prefix + message.text[offset : offset + capacity],
                    )
                )
            continue

        if lines and length + 1 + len(line) > max_chars:
            flush()
        if not lines:
            seq_start = message.seq
        seq_end = message.seq
        length += len(line) + bool(lines)
        lines.append(line)

    flush()
    return chunks


def canonical_sha256(messages: list) -> str:
    """SHA-256 over the messages' (seq, role, kind, name, call_id, text, raw)."""
    canonical = [(m.seq, m.role, m.kind, m.name, m.call_id, m.text, m.raw) for m in messages]
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stored_thread(row: Optional[sqlite3.Row]) -> Optional[StoredThread]:
    if row is None:
        return None
    return StoredThread(
        id=row["id"],
        workspace_id=row["workspace_id"],
        source_thread_id=row["source_thread_id"],
        message_count=row["message_count"],
        chunk_count=row["chunk_count"],
        sha256=row["sha256"],
        wing=row["wing"],
        created_at=row["created_at"],
        created_by=row["created_by"],
    )


class ThreadStore:
    def __init__(self, path: str):
        self._lock = threading.RLock()
        if path != ":memory:":
            file_path = Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            # Create privately before SQLite can write any transcript data.
            descriptor = os.open(file_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                os.chmod(file_path, 0o600)
            finally:
                os.close(descriptor)

        self._conn = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None, timeout=30
        )
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    source_thread_id TEXT NOT NULL,
                    parent_stored_id TEXT NOT NULL DEFAULT '',
                    message_count INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    wing TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    UNIQUE(workspace_id, source_thread_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    seq INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    at TEXT NOT NULL,
                    text TEXT NOT NULL,
                    raw TEXT NOT NULL,
                    PRIMARY KEY(thread_id, seq)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    idx INTEGER NOT NULL,
                    seq_start INTEGER NOT NULL,
                    seq_end INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    PRIMARY KEY(thread_id, idx)
                );
                """
            )
            self._fts_enabled = self._init_fts()
        except BaseException:
            self._conn.close()
            raise

    def _init_fts(self) -> bool:
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
                    "USING fts5(thread_id UNINDEXED, idx UNINDEXED, text)"
                )
            except sqlite3.OperationalError as error:
                if "no such module: fts5" not in str(error).lower():
                    raise
                return False
            # Also handles reopening with FTS5 after stores made without it.
            self._conn.execute(
                """INSERT INTO chunks_fts(thread_id, idx, text)
                   SELECT thread_id, idx, text FROM chunks
                   WHERE (thread_id, idx) NOT IN
                         (SELECT thread_id, idx FROM chunks_fts)"""
            )
        return True

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def store(
        self,
        workspace_id: str,
        source_thread_id: str,
        messages: list[ThreadMessage],
        *,
        actor: str,
        parent_stored_id: str = "",
    ) -> StoredThread:
        messages = list(messages)
        if not messages:
            raise ValueError("Thread must contain at least one message")
        if any(message.seq != seq for seq, message in enumerate(messages)):
            raise ValueError("Message sequence must start at zero and be contiguous")
        digest = canonical_sha256(messages)

        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = _stored_thread(
                self._conn.execute(
                    "SELECT * FROM threads WHERE workspace_id=? AND source_thread_id=?",
                    (workspace_id, source_thread_id),
                ).fetchone()
            )
            if existing is not None:
                if existing.sha256 != digest:
                    raise ThreadConflict("Source thread already has different stored content")
                return existing

            chunks = chunk_messages(messages)
            stored = StoredThread(
                id="thr_" + secrets.token_hex(8),
                workspace_id=workspace_id,
                source_thread_id=source_thread_id,
                message_count=len(messages),
                chunk_count=len(chunks),
                sha256=digest,
                wing=THREADS_WING,
                created_at=datetime.now(timezone.utc).isoformat(),
                created_by=actor,
            )
            self._conn.execute(
                """INSERT INTO threads
                   (id, workspace_id, source_thread_id, parent_stored_id,
                    message_count, chunk_count, sha256, wing, created_at, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    stored.id,
                    workspace_id,
                    source_thread_id,
                    parent_stored_id,
                    stored.message_count,
                    stored.chunk_count,
                    digest,
                    stored.wing,
                    stored.created_at,
                    actor,
                ),
            )
            self._conn.executemany(
                """INSERT INTO messages
                   (thread_id, seq, role, kind, name, call_id, at, text, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        stored.id,
                        m.seq,
                        m.role,
                        m.kind,
                        m.name,
                        m.call_id,
                        m.at,
                        m.text,
                        m.raw,
                    )
                    for m in messages
                ],
            )
            self._conn.executemany(
                "INSERT INTO chunks(thread_id, idx, seq_start, seq_end, text) "
                "VALUES (?, ?, ?, ?, ?)",
                [(stored.id, c.index, c.seq_start, c.seq_end, c.text) for c in chunks],
            )
            if self._fts_enabled:
                self._conn.executemany(
                    "INSERT INTO chunks_fts(thread_id, idx, text) VALUES (?, ?, ?)",
                    [(stored.id, c.index, c.text) for c in chunks],
                )
            return stored

    def get(self, stored_id: str) -> Optional[StoredThread]:
        with self._lock:
            return _stored_thread(
                self._conn.execute("SELECT * FROM threads WHERE id=?", (stored_id,)).fetchone()
            )

    def by_source(self, workspace_id: str, source_thread_id: str) -> Optional[StoredThread]:
        with self._lock:
            return _stored_thread(
                self._conn.execute(
                    "SELECT * FROM threads WHERE workspace_id=? AND source_thread_id=?",
                    (workspace_id, source_thread_id),
                ).fetchone()
            )

    def parent_of(self, stored_id: str) -> Optional[str]:
        """The stored thread this one's agent was a successor of, if any."""
        with self._lock:
            row = self._conn.execute(
                "SELECT parent_stored_id FROM threads WHERE id=?", (stored_id,)
            ).fetchone()
            return row["parent_stored_id"] or None if row is not None else None

    def messages(
        self, stored_id: str, start: int = 0, end: Optional[int] = None
    ) -> list[ThreadMessage]:
        """Messages with ``start <= seq <= end`` (end None: to the last)."""
        if start < 0 or (end is not None and (end < 0 or end < start)):
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, role, kind, name, call_id, at, text, raw FROM messages "
                "WHERE thread_id=? AND seq>=? AND (? IS NULL OR seq<=?) ORDER BY seq",
                (stored_id, start, end, end),
            ).fetchall()
            return [ThreadMessage(**dict(row)) for row in rows]

    def chunks(self, stored_id: str) -> list[Chunk]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT idx, seq_start, seq_end, text FROM chunks WHERE thread_id=? ORDER BY idx",
                (stored_id,),
            ).fetchall()
            return [
                Chunk(row["idx"], row["seq_start"], row["seq_end"], row["text"]) for row in rows
            ]

    def search(self, stored_ids: list[str], query: str, limit: int = 8) -> list[RecallHit]:
        """BM25 over the chunks of ``stored_ids``. Any query text is safe
        (quoted for FTS5, never a syntax error). Best first. Scores are
        negated BM25 (larger is better); the LIKE fallback has tied scores.
        """
        tokens = query.split()
        if not tokens or not stored_ids or limit <= 0:
            return []
        stored_ids = list(dict.fromkeys(stored_ids))
        placeholders = ",".join("?" for _ in stored_ids)
        with self._lock:
            # FTS5's query parser treats NUL as an end marker even in a
            # quoted string, so such queries use the literal scan as well.
            if self._fts_enabled and "\x00" not in query:
                match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
                rows = self._conn.execute(
                    "SELECT c.thread_id, c.idx, c.seq_start, c.seq_end, c.text, "
                    "-bm25(chunks_fts) AS score FROM chunks_fts "
                    "JOIN chunks AS c ON c.thread_id=chunks_fts.thread_id AND c.idx=chunks_fts.idx "
                    f"WHERE chunks_fts MATCH ? AND c.thread_id IN ({placeholders}) "
                    "ORDER BY bm25(chunks_fts), c.thread_id, c.idx LIMIT ?",
                    (match, *stored_ids, limit),
                ).fetchall()
            else:
                # Escape LIKE wildcards rather than interpreting user text
                # as a pattern. Bound values also protect thread-id filters.
                patterns = [
                    "%" + token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                    for token in tokens
                ]
                matches = " OR ".join("text LIKE ? ESCAPE '\\'" for _ in patterns)
                rows = self._conn.execute(
                    "SELECT thread_id, idx, seq_start, seq_end, text, 0.0 AS score FROM chunks "
                    f"WHERE thread_id IN ({placeholders}) AND ({matches}) "
                    "ORDER BY thread_id, idx LIMIT ?",
                    (*stored_ids, *patterns, limit),
                ).fetchall()
            return [
                RecallHit(
                    stored_thread_id=row["thread_id"],
                    chunk_index=row["idx"],
                    seq_start=row["seq_start"],
                    seq_end=row["seq_end"],
                    score=row["score"],
                    text=row["text"],
                )
                for row in rows
            ]
