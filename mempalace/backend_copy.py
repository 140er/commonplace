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


def copy_palace(
    source, destination, source_backend, target_backend, *, max_records=100_000, embed=None
):
    """Copy all collections and sidecars, then read back every record and vector."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Palace copy requires a sibling directory")
    if destination.exists():
        raise ValueError("Palace copy destination must be new")
    old, new = BACKENDS[source_backend](), BACKENDS[target_backend]()
    try:
        names = collection_names(source, source_backend)
        keyword_count = 0
        if target_backend == "chroma":
            for name in names:
                src = open_collection(old, source, name, vectors_only=True)
                keyword_count += len(src.get(where={KEYWORD_ONLY: True}, include=[]).ids)
            if keyword_count:
                logger.warning(
                    "Rollback re-embedding count=%d estimated_seconds=%.1f",
                    keyword_count,
                    keyword_count * 0.05,
                )
                if embed is None:
                    raise ValueError(
                        "Rollback requires the workspace embedder for keyword-only records"
                    )
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
                            )
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


def verify_sidecars(source, destination, source_backend):
    """Check copied sidecars, comparing SQLite contents including committed WAL."""
    import hashlib
    import os
    from itertools import zip_longest

    from .backups import _uncopyable_reason
    from .migrate import _is_chroma_storage

    def file_hash(path):
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            for data in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(data)
        return checksum.digest()

    def check(src, dst):
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
        elif src.name.endswith(".sqlite3"):
            with (
                closing(sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)) as left,
                closing(sqlite3.connect(dst.as_uri() + "?mode=ro", uri=True)) as right,
            ):
                if any(a != b for a, b in zip_longest(left.iterdump(), right.iterdump())):
                    raise CopyMismatch("Copied SQLite sidecar mismatch")
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
