"""Bounded operational figures; no backend imports, index loads or worker calls."""

from __future__ import annotations

import os
import sqlite3
import stat
import struct
import sys
import time
import uuid
from pathlib import Path

FIELDS = ("drawers", "embedding_model", "dimension", "vector_count", "hnsw_capacity", "tombstones")
READ_SECONDS = 0.100
# Chroma 1.5.9 Cargo.lock: hnswlib 6868102bde454dc761136e1994490133a6a026bb,
# hnswalg.h HEADER_FIELDS: int, six size_t, int, uint32, three size_t,
# double, size_t. Native little-endian, 64-bit size_t only; no alignment padding.
HEADER = struct.Struct("<i6QiI3QdQ")


def unknown(reason):
    return {key: {"value": None, "reason": reason} for key in FIELDS}


def _header(path):
    if sys.byteorder != "little" or struct.calcsize("P") != 8:
        return None, "unsupported_header_format"
    # O_NONBLOCK prevents a corrupt FIFO from blocking a request. fstat checks
    # the opened descriptor, not a racy pathname. Never read more than 100 bytes.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size != HEADER.size:
            return None, "invalid_header_length"
        raw = os.read(fd, HEADER.size)
        if len(raw) != HEADER.size:
            return None, "invalid_header_length"
        values = HEADER.unpack(raw)
        version, offset, capacity, elements = values[:4]
        if version != 1:
            return None, "unsupported_header_format"
        if offset != 0 or elements > capacity or capacity == 0:
            return None, "invalid_header_counts"
        return capacity, None
    finally:
        os.close(fd)


def read(palace, *, backend="chroma", clock=time.monotonic):
    """Read one workspace within 100 ms of SQLite work; unknown never means zero.

    SQLite rows include pending HNSW changes, so header elements minus SQL rows
    cannot prove tombstones. The MemPalace embedder identity is a JSON sidecar,
    not an authoritative SQLite model field; neither source is guessed here.
    """
    result = unknown("unavailable")
    if backend != "chroma":
        return unknown("unsupported_backend")
    deadline = clock() + READ_SECONDS

    def remaining():
        if clock() >= deadline:
            raise TimeoutError

    conn = None
    try:
        path = Path(palace) / "chroma.sqlite3"
        if not path.is_file():
            return unknown("missing_database")
        # mode=ro sees current WAL commits; immutable=1 would silently ignore
        # them. Never request WAL mode or initialize/migrate a database.
        if Path(str(path) + "-wal").exists() and not Path(str(path) + "-shm").is_file():
            return unknown("wal_unavailable")
        remaining()
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.010)
        conn.execute("PRAGMA query_only=ON")
        conn.set_progress_handler(lambda: int(clock() >= deadline), 100)
        conn.execute("BEGIN")
        row = conn.execute(
            "SELECT id,dimension FROM collections WHERE name=? LIMIT 2",
            ("mempalace_drawers",),
        ).fetchall()
        if len(row) != 1:
            return unknown("missing_collection" if not row else "ambiguous_collection")
        collection, dimension = row[0]
        result["dimension"] = (
            {"value": dimension, "reason": None}
            if type(dimension) is int and dimension > 0
            else {"value": None, "reason": "dimension_not_recorded"}
        )
        result["embedding_model"] = {"value": None, "reason": "model_not_in_sqlite"}
        result["tombstones"] = {"value": None, "reason": "unsynchronized_sources"}
        # Chroma has no collection index on segments. Walk at most eight
        # entries in primary-key order, then refuse larger catalogs.
        catalog = conn.execute(
            "SELECT id,scope,collection FROM segments ORDER BY id LIMIT 9"
        ).fetchall()
        if len(catalog) > 8:
            return unknown("segment_catalog_limit")
        segments = [(sid, scope) for sid, scope, cid in catalog if cid == collection]
        metadata = [sid for sid, scope in segments if scope == "METADATA"]
        vectors = [sid for sid, scope in segments if scope == "VECTOR"]
        if len(metadata) == 1 and len(segments) <= 2:
            # Chroma UNIQUE(segment_id,embedding_id) is a covering index. No
            # content/metadata rows are read; the progress handler bounds count.
            n = conn.execute(
                "SELECT count(*) FROM embeddings WHERE segment_id=?", (metadata[0],)
            ).fetchone()[0]
            result["drawers"] = {"value": n, "reason": None}
            result["vector_count"] = {"value": n, "reason": None}
        else:
            for key in ("drawers", "vector_count"):
                result[key] = {"value": None, "reason": "unknown_metadata_segment"}
        remaining()
        # Release the SQL read transaction before file work. No app lock taken.
        conn.close()
        conn = None
        if len(vectors) != 1 or str(uuid.UUID(vectors[0])) != vectors[0]:
            result["hnsw_capacity"] = {"value": None, "reason": "unknown_vector_segment"}
        else:
            try:
                capacity, reason = _header(Path(palace) / vectors[0] / "header.bin")
                result["hnsw_capacity"] = {"value": capacity, "reason": reason}
            except OSError:
                result["hnsw_capacity"] = {"value": None, "reason": "header_unavailable"}
        remaining()
        return result
    except TimeoutError:
        return unknown("read_timeout")
    except sqlite3.Error:
        return unknown("read_timeout" if clock() >= deadline else "invalid_database")
    except (OSError, ValueError):
        return unknown("unavailable")
    finally:
        if conn is not None:
            conn.close()


def workspace(gateway, workspace_id):
    try:
        directory = gateway.supervisor.workspace_dir(workspace_id)
        # A store override may select compact even with a retired Chroma file.
        # Only stat it: allowed figure sources cannot establish its contents.
        if (directory / "workspace.json").exists():
            return unknown("backend_not_established")
        return read(directory / "palace")
    except (OSError, ValueError):
        return unknown("workspace_unavailable")
