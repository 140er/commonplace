"""Verified palace copies for explicit store transitions.

The caller owns the writer lease. Destinations are fresh sibling directories;
this module never publishes them or changes the source's backend selection.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import numpy as np

from .backends import ChromaBackend, CompactBackend, PalaceRef
from .backends.compact import KEYWORD_ONLY
from .migrate import _carry_over_palace_files

logger = logging.getLogger(__name__)

BACKENDS = {"chroma": ChromaBackend, "compact": CompactBackend}
INCLUDE = ["documents", "metadatas", "embeddings"]


class CopyMismatch(ValueError):
    """A record, collection or vector failed the lossless copy check."""


class RollbackEmbedderMismatch(ValueError):
    """Configured rollback embedding would change the stored vector space."""


def collection_names(path, backend):
    filename = "compact.sqlite3" if backend == "compact" else "chroma.sqlite3"
    database = Path(path).resolve() / filename
    if not database.exists():
        return []
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        return [row[0] for row in conn.execute("SELECT name FROM collections ORDER BY name")]


def open_collection(backend, path, name, *, create=False, vectors_only=False):
    path = str(Path(path).resolve())
    return backend.get_collection(
        palace=PalaceRef(id=path, local_path=path),
        collection_name=name,
        create=create,
        options={"read_only": not create, "_migration_vectors": vectors_only},
    )


def verify_records(original, copied, *, quantized, reembedded=False):
    if set(original.ids) != set(copied.ids) or len(original.ids) != len(copied.ids):
        raise CopyMismatch("Copied record ids mismatch")
    positions = {record_id: i for i, record_id in enumerate(copied.ids)}
    for i, record_id in enumerate(original.ids):
        j = positions[record_id]
        expected_meta = dict(original.metadatas[i] or {})
        if reembedded and expected_meta.get(KEYWORD_ONLY) is True:
            expected_meta.pop(KEYWORD_ONLY)
        if (original.documents[i], expected_meta) != (
            copied.documents[j],
            copied.metadatas[j] or {},
        ):
            raise CopyMismatch("Copied document or metadata mismatch")
        left, right = original.embeddings[i], copied.embeddings[j]
        if reembedded and (original.metadatas[i] or {}).get(KEYWORD_ONLY) is True:
            if right is None or not np.isfinite(right).all():
                raise CopyMismatch("Re-embedded vector missing or invalid")
            continue
        if left is None or right is None:
            if left is not None or right is not None:
                raise CopyMismatch("Copied vector presence mismatch")
            continue
        left, right = np.asarray(left, dtype=np.float32), np.asarray(right, dtype=np.float32)
        if left.shape != right.shape or not np.isfinite(left).all() or not np.isfinite(right).all():
            raise CopyMismatch("Copied vector shape or values mismatch")
        # int8 rounds to half a scale; float32 arithmetic contributes a few ulps.
        scale = max(
            float(np.max(np.abs(left))) / 127, float(np.nextafter(np.float32(0), np.float32(1)))
        )
        float_error = float(np.max(np.abs(left))) * 4 * np.finfo(np.float32).eps
        tolerance = (scale / 2 if quantized else 0) + float_error
        if not np.all(np.abs(left - right) <= tolerance):
            raise CopyMismatch("Copied vector exceeds quantisation error")


def verify_palaces(source, destination, source_backend, target_backend, *, batch_size=256):
    old, new = BACKENDS[source_backend](), BACKENDS[target_backend]()
    try:
        names = collection_names(source, source_backend)
        if names != collection_names(destination, target_backend):
            raise CopyMismatch("Copied collection names mismatch")
        for name in names:
            src = open_collection(old, source, name, vectors_only=True)
            dst = open_collection(new, destination, name, vectors_only=True)
            if src.count() != dst.count():
                raise CopyMismatch("Copied collection count mismatch")
            for offset in range(0, src.count(), batch_size):
                batch = src.get(limit=batch_size, offset=offset, include=INCLUDE)
                verify_records(
                    batch,
                    dst.get(ids=batch.ids, include=INCLUDE),
                    quantized=target_backend == "compact",
                    reembedded=target_backend == "chroma",
                )
            left, right = src.get_stored_embedder_identity(), dst.get_stored_embedder_identity()
            if left != right and (
                left is None
                or right is None
                or left.model_name != right.model_name
                or left.dimension
                and right.dimension
                and left.dimension != right.dimension
            ):
                raise CopyMismatch("Copied embedder identity mismatch")
    finally:
        old.close()
        new.close()


def palace_preflight(source, source_backend, target_backend, *, max_records=100_000):
    """Inspect formats, counts and rollback cost without opening a Chroma client."""
    from .backends import detect_backends_for_path

    source = Path(source).resolve()
    detected = set(detect_backends_for_path(str(source)))
    if detected and detected != {source_backend}:
        raise CopyMismatch("Source palace backend disagrees with workspace store")
    filename = "compact.sqlite3" if source_backend == "compact" else "chroma.sqlite3"
    database = source / filename
    if not database.exists():
        return 0
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        if source_backend == "compact":
            counts = conn.execute(
                "SELECT collection_id,count(*) FROM documents GROUP BY collection_id"
            )
        else:
            counts = conn.execute(
                "SELECT c.id,count(e.id) FROM collections c LEFT JOIN segments s ON s.collection=c.id AND s.scope='METADATA' LEFT JOIN embeddings e ON e.segment_id=s.id GROUP BY c.id"
            )
        if target_backend == "compact" and any(row[1] > max_records for row in counts):
            raise ValueError("Collection exceeds compact migration size limit")
        keywords = 0
        if source_backend == "compact" and target_backend == "chroma":
            keywords = conn.execute(
                "SELECT count(*) FROM documents WHERE json_extract(metadata_json,?)=1",
                ("$." + KEYWORD_ONLY,),
            ).fetchone()[0]
            if keywords:
                logger.warning(
                    "Rollback re-embedding count=%d estimated_seconds=%.1f",
                    keywords,
                    keywords * 0.05,
                )
        return keywords


def collection_dimension(path, name):
    database = Path(path).resolve() / "compact.sqlite3"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        row = conn.execute("SELECT dimension FROM collections WHERE name=?", (name,)).fetchone()
    return row[0] if row else None


def copy_palace(
    source, destination, source_backend, target_backend, *, max_records=100_000, embed=None
):
    """Copy all collections and sidecars, then read back every record and vector."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Palace copy requires a sibling directory")
    if destination.exists():
        raise ValueError("Palace copy destination must be new")
    keyword_count = palace_preflight(
        source, source_backend, target_backend, max_records=max_records
    )
    old, new = BACKENDS[source_backend](), BACKENDS[target_backend]()
    try:
        names = collection_names(source, source_backend)
        if keyword_count and embed is None:
            raise ValueError("Rollback requires the workspace embedder for keyword-only records")
        # Check all sizes before creating any destination files.
        for name in names:
            if (
                target_backend == "compact"
                and open_collection(old, source, name, vectors_only=True).count() > max_records
            ):
                raise ValueError("Collection exceeds compact migration size limit")
        destination.mkdir(mode=0o700, parents=True)
        for name in names:
            src = open_collection(old, source, name, vectors_only=True)
            dst = open_collection(new, destination, name, create=True, vectors_only=True)
            for offset in range(0, src.count(), 256):
                batch = src.get(limit=256, offset=offset, include=INCLUDE)
                if target_backend == "chroma":
                    indices = [
                        i
                        for i, meta in enumerate(batch.metadatas)
                        if (meta or {}).get(KEYWORD_ONLY) is True
                    ]
                    if indices:
                        try:
                            vectors = embed(
                                [batch.documents[i] for i in indices],
                                [batch.metadatas[i] for i in indices],
                                identity=src.get_stored_embedder_identity(),
                                dimension=collection_dimension(source, name),
                            )
                        except RollbackEmbedderMismatch:
                            raise
                        except Exception:
                            raise ValueError(
                                "Rollback refused: workspace embedder unavailable"
                            ) from None
                        if len(vectors) != len(indices):
                            raise ValueError(
                                "Rollback refused: workspace embedder returned an invalid batch"
                            )
                        embeddings = list(batch.embeddings)
                        metadatas = [dict(meta) for meta in batch.metadatas]
                        for i, vector in zip(indices, vectors):
                            embeddings[i] = vector
                            metadatas[i].pop(KEYWORD_ONLY)
                        batch = replace(batch, embeddings=embeddings, metadatas=metadatas)
                dst.add(
                    ids=batch.ids,
                    documents=batch.documents,
                    metadatas=[meta or {} for meta in batch.metadatas],
                    embeddings=batch.embeddings,
                )
                # Re-embedded rollback vectors have a concrete expected value
                # here, before the final source comparison accounts for the
                # deliberate keyword-only policy change.
                verify_records(
                    batch,
                    dst.get(ids=batch.ids, include=INCLUDE),
                    quantized=target_backend == "compact",
                )
            identity = src.get_stored_embedder_identity()
            if identity is not None:
                dst.set_embedder_identity(identity)
    finally:
        old.close()
        new.close()
    # Preserve the original sidecar bytes (including legacy unknown dimensions)
    # instead of replacing them with newly serialized identity metadata.
    from .backends._sidecar import EMBEDDER_SIDECAR_FILENAME

    identity_file = destination / EMBEDDER_SIDECAR_FILENAME
    if (source / EMBEDDER_SIDECAR_FILENAME).exists() and identity_file.exists():
        identity_file.unlink()
    _carry_over_palace_files(str(source), str(destination), exclude_storage=source_backend)
    verify_palaces(source, destination, source_backend, target_backend)
    verify_sidecars(source, destination, source_backend)


def _verify_sqlite(left, right):
    """Compare typed values, including NULs that SQLite quote()/iterdump truncate."""
    left.execute("BEGIN")
    right.execute("BEGIN")
    schema = "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    expected = left.execute(schema).fetchall()
    if expected != right.execute(schema).fetchall():
        raise CopyMismatch("Copied SQLite schema mismatch")
    for kind, name, _, _ in expected:
        if kind != "table":
            continue
        identifier = '"' + name.replace('"', '""') + '"'
        query = "SELECT * FROM " + identifier
        try:
            src = left.execute(query + " ORDER BY rowid")
            dst = right.execute(query + " ORDER BY rowid")
        except sqlite3.OperationalError as error:
            if "no such column: rowid" not in str(error):
                raise
            # WITHOUT ROWID tables scan in primary-key order.
            src, dst = left.execute(query), right.execute(query)
        while True:
            a, b = src.fetchmany(256), dst.fetchmany(256)
            if a != b:
                raise CopyMismatch("Copied SQLite sidecar values mismatch")
            if not a:
                break


def verify_sidecars(source, destination, source_backend=None, *, skip=()):
    """Check copied sidecars, comparing SQLite contents including committed WAL."""
    import hashlib
    import os

    from .backups import _uncopyable_reason
    from .migrate import _is_chroma_storage

    def file_hash(path):
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            for data in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(data)
        return checksum.digest()

    def _sqlite_file(path):
        with path.open("rb") as stream:
            return stream.read(16) == b"SQLite format 3\0"

    source = Path(source).resolve()

    def check(src, dst):
        if src.relative_to(source).as_posix() in skip:
            return
        if src.name.endswith(("-wal", "-shm", "-journal")) and any(
            src.with_name(src.name[: -len(suffix)]).is_file()
            for suffix in ("-wal", "-shm", "-journal")
            if src.name.endswith(suffix)
        ):
            return
        if _uncopyable_reason(str(src), follow_symlinks=False) is not None:
            return
        if src.is_symlink():
            if not dst.is_symlink() or os.readlink(src) != os.readlink(dst):
                raise CopyMismatch("Copied sidecar link mismatch")
        elif src.is_dir():
            if not dst.is_dir():
                raise CopyMismatch("Copied sidecar directory missing")
            for child in src.iterdir():
                check(child, dst / child.name)
        elif src.is_file() and _sqlite_file(src):
            with (
                closing(sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)) as left,
                closing(sqlite3.connect(dst.as_uri() + "?mode=ro", uri=True)) as right,
            ):
                _verify_sqlite(left, right)
        elif file_hash(src) != file_hash(dst):
            raise CopyMismatch("Copied sidecar bytes mismatch")

    for src in Path(source).resolve().iterdir():
        if (
            (source_backend == "chroma" and _is_chroma_storage(src.name, str(src)))
            or (source_backend == "compact" and src.name.startswith("compact.sqlite3"))
            or src.name.endswith((".sqlite3-wal", ".sqlite3-shm", ".sqlite3-journal"))
        ):
            continue
        check(src, Path(destination).resolve() / src.name)
