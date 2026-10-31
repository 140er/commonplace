"""Offline, backup-gated and restartable workspace store cutovers.

One durable generation pointer publishes palace, threads, boards and settings
at once. Interrupted staging generations are never served. Old generations
remain available for comparison for at least fourteen days.
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from mempalace.fsio import atomic_write_json, fsync_directory

from .backup import BackupError, _snapshot, _verify_offline, data_lock
from .workspace_store import (
    DEFAULT_MAX_RECORDS,
    POINTER,
    RECEIPT,
    SETTINGS,
    STATE,
    generation_path,
    read_json,
    resolve_workspace,
    settings_for,
    thread_path,
    worker_config,
    workspace_root,
)

RETENTION_SECONDS = 14 * 24 * 60 * 60
_ADMIN_FILES = frozenset({POINTER, RECEIPT, STATE})


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


def backup_workspace(root, active, workspace_id):
    """Run the operator's off-host command; store its receipt, never backup data."""
    command = os.environ.get("COMMONPLACE_BACKUP_CMD")
    if not command:
        raise BackupError("Store switch requires COMMONPLACE_BACKUP_CMD for an off-host backup")
    before = newest_write(active)
    env = dict(
        os.environ, COMMONPLACE_WORKSPACE_DIR=str(active), COMMONPLACE_WORKSPACE_ID=workspace_id
    )
    try:
        result = subprocess.run(
            shlex.split(command), env=env, capture_output=True, text=True, timeout=3600
        )
    except (OSError, subprocess.SubprocessError):
        raise BackupError("Off-host workspace backup command failed") from None
    if result.returncode != 0:
        raise BackupError("Off-host workspace backup command failed")
    try:
        receipt = json.loads(result.stdout)
        stamp = datetime.fromisoformat(receipt["completed_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or receipt.get("off_host") is not True:
            raise ValueError
        timestamp = int(stamp.timestamp() * 1_000_000_000)
        if timestamp <= before or timestamp > time.time_ns() + 5_000_000_000:
            raise ValueError
        if newest_write(active) != before:
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise BackupError(
            "Backup receipt must confirm off-host completion after the latest write"
        ) from None
    recorded = {"completed_at": stamp.isoformat(), "off_host": True, "last_write_ns": before}
    atomic_write_json(root / RECEIPT, recorded)
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


def _embedder(directory):
    from .workers import _PASSED_ENV

    directory = Path(directory)

    def embed(documents, metadatas):
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
            input=json.dumps({"documents": documents, "metadatas": metadatas}, ensure_ascii=True),
            capture_output=True,
            text=True,
            timeout=600,
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
                max_records=settings_for(source).get(
                    "compact_max_collection_records", DEFAULT_MAX_RECORDS
                ),
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

    from .handoff.models import ThreadMessage
    from .handoff.thread_store_migrate import read_store, verify_stores
    from .handoff.thread_store_v2 import ThreadStoreV2

    probe = directory.with_name(directory.name + ".probe")
    restored = directory.with_name(directory.name + ".rollback-probe")
    try:
        _copy_tree(directory, probe)
        with closing(ThreadStoreV2(str(probe / "threads.v2.sqlite3"))) as store:
            store.store(
                "rollback-test",
                "rollback-test",
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
            col = open_collection(backend, probe / "palace", "rollback_verification", create=True)
            col.add(
                ids=["probe"],
                documents=["rollback verification"],
                metadatas=[{"last_modified": "2026-01-01"}],
                embeddings=[[1.0, 0.0]],
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


def switch_workspace(data_dir, workspace_id, target_store, *, checkpoint=lambda step: None):
    """Back up, stage, verify, rehearse and atomically publish while offline."""
    if target_store not in ("v1", "compact"):
        raise ValueError("Workspace store must be v1 or compact")
    data_dir = Path(data_dir).resolve()
    root = workspace_root(data_dir, workspace_id)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with data_lock(data_dir, exclusive=True):
        _verify_offline(data_dir)
        source = resolve_workspace(root)
        source_store = settings_for(source).get("store", "v1")
        if source_store == target_store:
            state = read_json(root / STATE)
            if state.get("staging") == read_json(root / POINTER).get("active") and state.get(
                "staging"
            ):
                atomic_write_json(root / STATE, {"state": "complete", "store": target_store})
            return {"store": source_store, "state": "unchanged"}
        from mempalace.palace import mine_palace_lock

        with mine_palace_lock(str(source / "palace")):
            # Staging is restartable: unpublished generations never become live.
            pointer = read_json(root / POINTER)
            state = read_json(root / STATE)
            orphan = state.get("staging")
            retained_names = {item["name"] for item in pointer.get("retained", [])}
            if orphan and orphan != pointer.get("active") and orphan not in retained_names:
                shutil.rmtree(generation_path(root, orphan), ignore_errors=True)
                for suffix in (
                    ".probe",
                    ".rollback-probe",
                    ".source-palace",
                    ".probe.source-palace",
                    ".rollback-probe.source-palace",
                ):
                    shutil.rmtree(root.parent / (orphan + suffix), ignore_errors=True)
            receipt = backup_workspace(root, source, workspace_id)
            checkpoint("backup")
            name = root.name + ".generation-" + secrets.token_hex(12)
            staging = generation_path(root, name)
            atomic_write_json(
                root / STATE, {"state": "copying", "staging": name, "store": target_store}
            )
            checkpoint("staging")
            try:
                _stage(
                    source, staging, target_store, embed=_embedder(source), checkpoint=checkpoint
                )
                checkpoint("copied")
                if target_store == "compact":
                    _rehearse(staging)
                checkpoint("rehearsed")
                # Record comparison settings in this generation, including
                # the old source format, never an executable path or command.
                atomic_write_json(
                    staging / ".store-comparison.json",
                    {"previous": source.name, "store": source_store},
                )
                atomic_write_json(
                    staging / "palace" / ".store-comparison.json",
                    {
                        "previous": str(source / "palace"),
                        "backend": "compact" if source_store == "compact" else "chroma",
                    },
                )
                _sync_tree(staging)
                if newest_write(source) != receipt["last_write_ns"]:
                    raise ValueError("Workspace changed after backup; refusing store switch")
                _verify_offline(data_dir)
                checkpoint("verified")
                retained = pointer.get("retained", []) + [
                    {"name": source.name, "retired_at": time.time()}
                ]
                atomic_write_json(root / POINTER, {"active": name, "retained": retained})
                checkpoint("published")
                atomic_write_json(root / STATE, {"state": "complete", "store": target_store})
                checkpoint("complete")
                return {"store": target_store, "state": "complete"}
            except BaseException:
                # A crash after publication already committed a completely
                # verified generation. Never restore stale data over it.
                if read_json(root / POINTER).get("active") != name:
                    atomic_write_json(
                        root / STATE, {"state": "failed", "staging": name, "store": source_store}
                    )
                raise


def prune_generations(data_dir, workspace_id, *, now=None):
    root = workspace_root(data_dir, workspace_id)
    with data_lock(data_dir, exclusive=True):
        _verify_offline(Path(data_dir).resolve())
        pointer = read_json(root / POINTER)
        if not pointer:
            return
        kept = []
        for item in pointer.get("retained", []):
            if (time.time() if now is None else now) - item["retired_at"] < RETENTION_SECONDS:
                kept.append(item)
                continue
            path = generation_path(root, item["name"])
            if path.name == pointer.get("active"):
                raise ValueError("Refusing to delete the active workspace generation")
            if path == root:
                for child in path.iterdir():
                    if child.name not in _ADMIN_FILES:
                        if child.is_dir() and not child.is_symlink():
                            shutil.rmtree(child)
                        else:
                            child.unlink()
            else:
                shutil.rmtree(path, ignore_errors=True)
        pointer["retained"] = kept
        atomic_write_json(root / POINTER, pointer)


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
    return {
        "workspace": workspace_id,
        "store": store,
        "thread_index_version": index,
        "palace_schema_version": schema,
        "backfill": state,
    }
