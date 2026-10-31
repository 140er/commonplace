"""Offline, backup-gated and restartable workspace store cutovers.

One durable generation pointer publishes palace, threads, boards and settings
at once. Interrupted staging generations are never served. Old generations
remain available for comparison for at least fourteen days.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from mempalace.fsio import atomic_write_json, atomic_write_text, fsync_directory

from .backup import BackupError, _snapshot, _verify_offline, data_lock
from .workspace_store import (
    DEFAULT_MAX_RECORDS,
    POINTER,
    RECEIPT,
    SETTINGS,
    STATE,
    generation_path,
    migration_limit,
    read_json,
    resolve_workspace,
    settings_for,
    thread_path,
    worker_config,
    workspace_root,
)

RETENTION_SECONDS = 14 * 24 * 60 * 60
BACKUP_STDERR = ".store-backup.stderr.log"
_ADMIN_FILES = frozenset({POINTER, RECEIPT, STATE, BACKUP_STDERR})


def newest_write(directory):
    """Latest durable data mtime, excluding diagnostics and disposable model cache."""
    newest = 0

    def visit(path):
        nonlocal newest
        if (
            path.name in _ADMIN_FILES
            or path.name == "worker.log"
            or path.name == "chroma"
            and path.parent.name == ".cache"
        ):
            return
        if path.is_symlink():
            return
        if path.is_dir():
            for child in path.iterdir():
                visit(child)
        elif not path.name.endswith(("-shm", "-journal")):
            stat = path.stat()
            if not (path.name.endswith("-wal") and stat.st_size == 0):
                newest = max(newest, stat.st_mtime_ns)

    if Path(directory).exists():
        visit(Path(directory))
    return newest


def _retirement_digest(directory):
    """Detect added/changed data even within the filesystem's mtime resolution."""
    directory = Path(directory)
    checksum = hashlib.sha256()

    def visit(path):
        if (
            path.name in _ADMIN_FILES
            or path.name == "worker.log"
            or path.name == "chroma"
            and path.parent.name == ".cache"
        ):
            return
        if path.is_symlink():
            checksum.update(path.relative_to(directory).as_posix().encode("utf-8"))
            checksum.update(os.fsencode(os.readlink(path)))
        elif path.is_dir():
            for child in sorted(path.iterdir()):
                visit(child)
        elif path.is_file():
            if (
                path.name.endswith(("-shm", "-journal"))
                or path.name.endswith("-wal")
                and path.stat().st_size == 0
            ):
                return
            checksum.update(
                json.dumps([path.relative_to(directory).as_posix(), path.stat().st_size]).encode(
                    "utf-8"
                )
            )
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(block)

    if directory.exists():
        visit(directory)
    return checksum.hexdigest()


def _retired_changed(directory, item):
    return (
        newest_write(directory) > int(item["retired_at"] * 1_000_000_000)
        or not item.get("digest")
        or _retirement_digest(directory) != item["digest"]
    )


def _staging_paths(staging):
    return [
        staging.with_name(staging.name + suffix)
        for suffix in (
            "",
            ".source-palace",
            ".probe",
            ".rollback-probe",
            ".probe.source-palace",
            ".rollback-probe.source-palace",
        )
    ]


def _remove_staging(staging):
    for path in _staging_paths(staging):
        if path.is_symlink():
            raise ValueError("Refusing symlink in staging cleanup")
        if path.exists():
            shutil.rmtree(path)


def _cleanup_unpublished(root, pointer):
    protected = {
        root.name,
        pointer.get("active"),
        *(item["name"] for item in pointer.get("retained", [])),
    }
    pattern = re.compile(re.escape(root.name) + r"\.generation-[0-9a-f]{24}")
    for path in root.parent.iterdir():
        match = pattern.match(path.name)
        if not match:
            continue
        base = match.group()
        # Match only this command's generation and scratch names.
        staging = generation_path(root, base)
        if path not in _staging_paths(staging):
            continue
        if path.name == base and base in protected:
            continue
        if path.is_symlink():
            raise ValueError("Refusing symlink in staging cleanup")
        if path.is_dir():
            shutil.rmtree(path)


def _seal_retired(root, pointer):
    for item in pointer.get("retained", []):
        directory = generation_path(root, item["name"])
        if directory.name == pointer.get("active") or not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.parent == root and path.name in _ADMIN_FILES:
                continue
            if path.is_file() and not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)


def _preflight(source, target_store):
    from mempalace.backend_copy import palace_preflight

    from .handoff.thread_store_migrate import read_store, require_v1_representable

    source_store = settings_for(source).get("store", "v1")
    source_backend = "compact" if source_store == "compact" else "chroma"
    configured = read_json(source / "config" / "config.json").get("backend", source_backend)
    if str(configured).strip().lower() != source_backend:
        raise ValueError("Source backend configuration disagrees with workspace store")
    limit = migration_limit(source) if target_store == "compact" else DEFAULT_MAX_RECORDS
    palace_preflight(
        source / "palace",
        source_backend,
        "compact" if target_store == "compact" else "chroma",
        max_records=limit,
    )
    expanded_threads = 0
    if target_store == "v1" and thread_path(source).exists():
        with read_store(thread_path(source), compact=True) as store:
            require_v1_representable(store)
            for row in store._conn.execute("SELECT id FROM threads"):
                expanded_threads += (
                    sum(
                        len(item.text.encode("utf-8")) + len(item.raw.encode("utf-8"))
                        for item in store.messages(row[0])
                    )
                    * 4
                )
    size = sum(
        path.stat().st_size
        for path in source.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    if source_store == "compact" and (source / "palace" / "compact.sqlite3").exists():
        with closing(
            sqlite3.connect((source / "palace" / "compact.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as conn:
            expanded_palace = (
                conn.execute("SELECT coalesce(sum(raw_size),0) FROM documents").fetchone()[0] * 4
            )
        size = max(size, expanded_palace + expanded_threads)
    # Four simultaneous generations/snapshots, plus SQLite/index overhead.
    estimated_peak = size * 4 + 16 * 1024 * 1024
    if shutil.disk_usage(source.parent).free < estimated_peak:
        raise ValueError(
            f"Insufficient free space for estimated migration peak ({estimated_peak} bytes)"
        )
    return estimated_peak


def _verify_generation(source, destination, target_store):
    from mempalace.backend_copy import verify_sidecars

    from .handoff.thread_store_migrate import read_store, verify_stores

    skip = {
        *_ADMIN_FILES,
        SETTINGS,
        "palace",
        "threads.sqlite3",
        "threads.v2.sqlite3",
        ".store-comparison.json",
        "config/config.json",
    }
    verify_sidecars(source, destination, skip=skip)
    expected = settings_for(source)
    expected["store"] = target_store
    if read_json(destination / SETTINGS) != expected:
        raise ValueError("Copied workspace settings mismatch")
    config = read_json(source / "config" / "config.json")
    config["backend"] = "compact" if target_store == "compact" else "chroma"
    if read_json(destination / "config" / "config.json") != config:
        raise ValueError("Copied worker configuration mismatch")
    source_store = settings_for(source).get("store", "v1")
    if (source / "palace").exists():
        verify_sidecars(
            source / "palace",
            destination / "palace",
            "compact" if source_store == "compact" else "chroma",
            skip={".store-comparison.json"},
        )
    if thread_path(source).exists():
        with (
            read_store(thread_path(source), compact=source_store == "compact") as old,
            read_store(thread_path(destination), compact=target_store == "compact") as new,
        ):
            verify_stores(old, new)


def backup_workspace(root, active, workspace_id):
    """Run the operator's off-host command; store its receipt, never backup data."""
    command = os.environ.get("COMMONPLACE_BACKUP_CMD")
    if not command or not command.strip():
        raise BackupError("Store switch requires COMMONPLACE_BACKUP_CMD for an off-host backup")
    before = newest_write(active)
    nonce = secrets.token_hex(16)
    env = dict(
        os.environ,
        COMMONPLACE_WORKSPACE_DIR=str(active),
        COMMONPLACE_WORKSPACE_ID=workspace_id,
        COMMONPLACE_BACKUP_NONCE=nonce,
    )
    started = time.time_ns()
    try:
        result = subprocess.run(
            shlex.split(command), env=env, capture_output=True, text=True, timeout=3600
        )
    except (OSError, subprocess.SubprocessError) as exc:
        diagnostic = getattr(exc, "stderr", "") or ""
        if isinstance(diagnostic, bytes):
            diagnostic = diagnostic.decode("utf-8", "replace")
        atomic_write_text(root / BACKUP_STDERR, diagnostic, follow_symlinks=False)
        raise BackupError("Off-host workspace backup command failed") from None
    atomic_write_text(root / BACKUP_STDERR, result.stderr, follow_symlinks=False)
    if result.returncode != 0:
        raise BackupError("Off-host workspace backup command failed")
    try:
        receipt = json.loads(result.stdout)
        stamp = datetime.fromisoformat(receipt["completed_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or receipt.get("off_host") is not True:
            raise ValueError
        timestamp = int(stamp.timestamp() * 1_000_000_000)
        if not isinstance(receipt.get("nonce"), str) or not secrets.compare_digest(
            receipt["nonce"], nonce
        ):
            raise ValueError
        if timestamp <= before or timestamp < started or timestamp > time.time_ns() + 5_000_000_000:
            raise ValueError
        if newest_write(active) != before:
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise BackupError(
            "Backup receipt must echo this run nonce and confirm off-host completion after its start and the latest write"
        ) from None
    recorded = {
        "completed_at": stamp.isoformat(),
        "off_host": True,
        "last_write_ns": before,
        "nonce": nonce,
    }
    atomic_write_json(root / RECEIPT, recorded, follow_symlinks=False)
    return recorded


def _copy_tree(source, target, *, skip=()):
    """Staging copy: checkpoint-safe SQLite plus verbatim files and sidecars."""
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    for path in source.iterdir():
        if path.name in skip:
            continue
        if any(
            path.name.endswith(suffix) and path.with_name(path.name[: -len(suffix)]).is_file()
            for suffix in ("-wal", "-shm", "-journal")
        ):
            continue
        dest = target / path.name
        if path.is_symlink():
            # Only the disposable shared model cache may point outside the tree.
            if path.name != "chroma" or path.parent.name != ".cache":
                raise ValueError("Unsupported symlink in workspace staging copy")
            dest.symlink_to(os.readlink(path), target_is_directory=True)
        elif path.is_dir():
            _copy_tree(path, dest)
        elif path.is_file():
            with path.open("rb") as stream:
                sqlite = stream.read(16) == b"SQLite format 3\0"
            if sqlite:
                _snapshot(path, dest)
            else:
                shutil.copy2(path, dest)
                dest.chmod(0o600)

    from mempalace.backend_copy import verify_sidecars

    verify_sidecars(source, target, skip=skip)


def _embedder(directory):
    from .workers import _PASSED_ENV

    directory = Path(directory)

    def embed(documents, metadatas, *, identity=None, dimension=None):
        env = {key: value for key, value in os.environ.items() if key in _PASSED_ENV}
        env.update(
            HOME=str(directory / "home"),
            USERPROFILE=str(directory / "home"),
            MEMPALACE_CONFIG_DIR=str(directory / "config"),
            MEMPALACE_PALACE_PATH=str(directory / "palace"),
        )
        result = subprocess.run(
            [sys.executable, "-m", "commonplace.store_embed"],
            env=env,
            input=json.dumps(
                {
                    "documents": documents,
                    "metadatas": metadatas,
                    "identity": asdict(identity) if identity else None,
                    "dimension": dimension,
                },
                ensure_ascii=True,
            ),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 2:
            from mempalace.backend_copy import RollbackEmbedderMismatch

            if json.loads(result.stdout).get("error") == "embedder_identity_mismatch":
                raise RollbackEmbedderMismatch(
                    "Rollback refused: configured embedder model or dimension differs from stored identity"
                )
        if result.returncode:
            raise ValueError("Workspace embedder unavailable")
        return json.loads(result.stdout)

    return embed


def _stage(source, destination, target_store, *, embed=None, checkpoint=lambda step: None):
    from mempalace.backend_copy import copy_palace

    from .handoff.thread_store_migrate import migrate_v1, migrate_v2

    source_store = settings_for(source).get("store", "v1")
    _copy_tree(
        source,
        destination,
        skip=(
            *_ADMIN_FILES,
            "palace",
            "threads.sqlite3",
            "threads.v2.sqlite3",
            ".store-comparison.json",
        ),
    )
    checkpoint("files")
    if (source / "palace").exists():
        snapshot = destination.with_name(destination.name + ".source-palace")
        try:
            # Chroma's client may checkpoint or recover even a read-only API
            # open. Only open that client on a staging snapshot of the palace.
            _copy_tree(source / "palace", snapshot)
            copy_palace(
                snapshot,
                destination / "palace",
                "compact" if source_store == "compact" else "chroma",
                "compact" if target_store == "compact" else "chroma",
                max_records=migration_limit(source)
                if target_store == "compact"
                else DEFAULT_MAX_RECORDS,
                embed=embed,
            )
        finally:
            shutil.rmtree(snapshot, ignore_errors=True)
    else:
        (destination / "palace").mkdir(mode=0o700)
    checkpoint("palace")
    settings = settings_for(source)
    settings["store"] = target_store
    atomic_write_json(destination / SETTINGS, settings)
    if thread_path(source).exists():
        if target_store == "compact":
            migrate_v1(str(thread_path(source)), str(thread_path(destination)))
        else:
            migrate_v2(str(thread_path(source)), str(thread_path(destination)))
    worker_config(destination)
    _verify_generation(source, destination, target_store)
    checkpoint("threads")


def _sync_tree(directory):
    for root, _directories, files in os.walk(directory, followlinks=False):
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
        fsync_directory(root)
    fsync_directory(Path(directory).parent)


def _rehearse(directory):
    """Exercise rollback including post-switch writes before publishing compact."""
    from mempalace.backend_copy import open_collection, verify_palaces
    from mempalace.backends import CompactBackend
    from mempalace.backends.compact import KEYWORD_ONLY

    from .handoff.models import ThreadMessage
    from .handoff.thread_store_migrate import read_store, verify_stores
    from .handoff.thread_store_v2 import ThreadStoreV2

    probe = directory.with_name(directory.name + ".probe")
    restored = directory.with_name(directory.name + ".rollback-probe")
    try:
        _copy_tree(directory, probe)
        token = secrets.token_hex(12)
        with closing(ThreadStoreV2(str(probe / "threads.v2.sqlite3"))) as store:
            store.store(
                "rollback-test",
                "rollback-test-" + token,
                [
                    ThreadMessage(
                        seq=0,
                        role="user",
                        kind="text",
                        name="",
                        call_id="",
                        at="",
                        text="rollback verification",
                        raw="rollback verification",
                    )
                ],
                actor="test",
            )
        backend = CompactBackend()
        try:
            col = open_collection(backend, probe / "palace", "rollback_probe_" + token, create=True)
            col.add(
                ids=["probe_" + token],
                documents=["rollback verification"],
                metadatas=[{"last_modified": "2026-01-01"}],
                embeddings=[[1.0, 0.0]],
            )
            drawers = read_json(directory / "config" / "config.json").get(
                "collection_name", "mempalace_drawers"
            )
            col = open_collection(backend, probe / "palace", drawers, create=True)
            col.add(
                ids=["keyword_probe_" + token],
                documents=["rollback keyword verification"],
                metadatas=[{KEYWORD_ONLY: True, "last_modified": "2026-01-01"}],
            )
        finally:
            backend.close()
        _stage(probe, restored, "v1", embed=_embedder(directory))
        verify_palaces(probe / "palace", restored / "palace", "compact", "chroma")
        with (
            read_store(probe / "threads.v2.sqlite3", compact=True) as src,
            read_store(restored / "threads.sqlite3") as dst,
        ):
            verify_stores(src, dst)
    finally:
        shutil.rmtree(probe, ignore_errors=True)
        shutil.rmtree(restored, ignore_errors=True)


def switch_workspace(
    data_dir, workspace_id, target_store, *, checkpoint=lambda step: None, authorize=None
):
    """Back up, stage, verify, rehearse and atomically publish while offline."""
    if target_store not in ("v1", "compact"):
        raise ValueError("Workspace store must be v1 or compact")
    data_dir = Path(data_dir).resolve()
    with data_lock(data_dir, exclusive=True):
        if authorize is not None:
            workspace_id = authorize(workspace_id)
        root = workspace_root(data_dir, workspace_id)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _verify_offline(data_dir)
        pointer = read_json(root / POINTER)
        _cleanup_unpublished(root, pointer)
        _seal_retired(root, pointer)
        source = resolve_workspace(root)
        source_store = settings_for(source).get("store", "v1")
        if source_store == target_store:
            state = read_json(root / STATE)
            if state.get("staging") and state.get("staging") == pointer.get("active"):
                atomic_write_json(
                    root / STATE,
                    {"state": "complete", "store": target_store},
                    follow_symlinks=False,
                )
            return {"store": source_store, "state": "unchanged"}
        from mempalace.palace import mine_palace_lock

        with mine_palace_lock(
            str(source / "palace"), lock_dir=str(source / "home" / ".mempalace" / "locks")
        ):
            _preflight(source, target_store)
            receipt = backup_workspace(root, source, workspace_id)
            checkpoint("backup")
            name = root.name + ".generation-" + secrets.token_hex(12)
            staging = generation_path(root, name)
            atomic_write_json(
                root / STATE,
                {"state": "copying", "staging": name, "store": target_store},
                follow_symlinks=False,
            )
            try:
                checkpoint("staging")
                _stage(
                    source, staging, target_store, embed=_embedder(source), checkpoint=checkpoint
                )
                checkpoint("copied")
                if target_store == "compact":
                    _rehearse(staging)
                checkpoint("rehearsed")
                _verify_generation(source, staging, target_store)
                atomic_write_json(
                    staging / ".store-comparison.json",
                    {"previous": source.name, "store": source_store},
                    follow_symlinks=False,
                )
                atomic_write_json(
                    staging / "palace" / ".store-comparison.json",
                    {
                        "previous": source.name,
                        "workspace": workspace_id,
                        "backend": "compact" if source_store == "compact" else "chroma",
                    },
                    follow_symlinks=False,
                )
                _sync_tree(staging)
                if newest_write(source) != receipt["last_write_ns"]:
                    raise ValueError("Workspace changed after backup; refusing store switch")
                _verify_offline(data_dir)
                checkpoint("verified")
                retained = pointer.get("retained", []) + [
                    {
                        "name": source.name,
                        "retired_at": time.time(),
                        "digest": _retirement_digest(source),
                    }
                ]
                published = {"active": name, "retained": retained}
                atomic_write_json(root / POINTER, published, follow_symlinks=False)
                _seal_retired(root, published)
                checkpoint("published")
                atomic_write_json(
                    root / STATE,
                    {"state": "complete", "store": target_store},
                    follow_symlinks=False,
                )
                checkpoint("complete")
                return {"store": target_store, "state": "complete"}
            except BaseException:
                if read_json(root / POINTER).get("active") != name:
                    _remove_staging(staging)
                    atomic_write_json(
                        root / STATE,
                        {"state": "failed", "store": source_store},
                        follow_symlinks=False,
                    )
                raise


def prune_generations(data_dir, workspace_id, *, now=None, authorize=None):
    with data_lock(data_dir, exclusive=True):
        if authorize is not None:
            workspace_id = authorize(workspace_id)
        root = workspace_root(data_dir, workspace_id)
        _verify_offline(Path(data_dir).resolve())
        pointer = read_json(root / POINTER)
        _cleanup_unpublished(root, pointer)
        if not pointer:
            return
        _seal_retired(root, pointer)
        kept, modified = [], []
        for item in pointer.get("retained", []):
            path = generation_path(root, item["name"])
            if path.name == pointer.get("active"):
                raise ValueError("Refusing to delete the active workspace generation")
            if _retired_changed(path, item):
                kept.append(item)
                modified.append(path.name)
                continue
            if (time.time() if now is None else now) - item["retired_at"] < RETENTION_SECONDS:
                kept.append(item)
                continue
            if path == root:
                for child in path.iterdir():
                    if child.name not in _ADMIN_FILES:
                        if child.is_dir() and not child.is_symlink():
                            shutil.rmtree(child)
                        else:
                            child.unlink()
            elif path.exists():
                shutil.rmtree(path)
        pointer["retained"] = kept
        atomic_write_json(root / POINTER, pointer, follow_symlinks=False)
        if modified:
            raise ValueError(
                "Refusing to prune retired generations with newer writes: " + ", ".join(modified)
            )


def doctor_workspace(data_dir, workspace_id):
    root = workspace_root(data_dir, workspace_id)
    active = resolve_workspace(root)
    store = settings_for(active).get("store", "v1")
    path = thread_path(active)
    index = None
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            index = (
                conn.execute("SELECT index_version FROM index_meta").fetchone()[0]
                if store == "compact"
                else conn.execute("PRAGMA user_version").fetchone()[0]
            )
    palace = active / "palace" / ("compact.sqlite3" if store == "compact" else "chroma.sqlite3")
    schema = None
    if palace.exists():
        with closing(sqlite3.connect(palace.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            schema = conn.execute("PRAGMA user_version").fetchone()[0]
    recorded = read_json(root / STATE)
    state = recorded.get("state", "not_started")
    if recorded.get("staging") == read_json(root / POINTER).get("active") and recorded.get(
        "staging"
    ):
        state = "complete"
    retained = []
    for item in read_json(root / POINTER).get("retained", []):
        path = generation_path(root, item["name"])
        retained.append(
            {
                "name": item["name"],
                "older_than_14_days": time.time() - item["retired_at"] >= RETENTION_SECONDS,
                "newer_writes": _retired_changed(path, item),
            }
        )
    return {
        "active_path": str(active),
        "retained_generations": retained,
        "workspace": workspace_id,
        "store": store,
        "thread_index_version": index,
        "palace_schema_version": schema,
        "backfill": state,
    }
