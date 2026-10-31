"""One resolver and thread factory for opt-in workspace store generations."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

POINTER = ".store-pointer.json"
POINTER_PREVIOUS = ".store-pointer.prev.json"
POINTER_REPAIR = (
    "Invalid or missing workspace generation pointer; stop the gateway and verify "
    f"{POINTER_PREVIOUS} before restoring {POINTER}"
)
SETTINGS = "workspace.json"
STATE = ".store-backfill.json"
RECEIPT = ".store-backup.json"
DEFAULT_MAX_RECORDS = 100_000


def read_json(path, default=None):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {} if default is None else default
    if not isinstance(value, dict):
        raise ValueError("Workspace store configuration must be an object")
    return value


def workspace_root(data_dir, workspace_id):
    if not workspace_id.startswith("ws_") or not workspace_id[3:].isalnum():
        raise ValueError("Invalid workspace id")
    return Path(data_dir) / "workspaces" / workspace_id


def generation_path(root, name):
    root = Path(root)
    if root.is_symlink():
        raise ValueError("Workspace root cannot be a symlink")
    if name == root.name:
        return root
    if not re.fullmatch(re.escape(root.name) + r"\.generation-[0-9a-f]{24}", name):
        raise ValueError("Invalid workspace generation")
    target = root.parent / name
    if target.is_symlink():
        raise ValueError("Workspace generation cannot be a symlink")
    return target


def resolve_workspace(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError("Workspace root cannot be a symlink")
    if not (root / POINTER).exists():
        if any(root.parent.glob(root.name + ".generation-*")):
            raise ValueError(POINTER_REPAIR)
        return root
    try:
        pointer = read_json(root / POINTER)
    except ValueError:
        raise ValueError(POINTER_REPAIR) from None
    return validate_pointer(root, pointer)


def validate_pointer(root, pointer):
    try:
        if not isinstance(pointer, dict):
            raise ValueError
        if not isinstance(pointer.get("active"), str):
            raise ValueError
        active = generation_path(root, pointer["active"])
        if not active.is_dir():
            raise ValueError
        retained = pointer.get("retained", [])
        if not isinstance(retained, list):
            raise ValueError
        for item in retained:
            generation_path(root, item["name"])
            if item["name"] == pointer["active"]:
                raise ValueError
            float(item["retired_at"])
    except (ValueError, KeyError, TypeError):
        raise ValueError(POINTER_REPAIR) from None
    return active


def workspace_path(data_dir, workspace_id):
    return resolve_workspace(workspace_root(data_dir, workspace_id))


def settings_for(directory):
    settings = read_json(Path(directory) / SETTINGS)
    if settings.get("store", "v1") not in ("v1", "compact"):
        raise ValueError("Workspace store must be v1 or compact")
    return settings


def migration_limit(directory):
    limit = settings_for(directory).get("compact_max_collection_records", DEFAULT_MAX_RECORDS)
    if type(limit) is not int or limit < 1:
        raise ValueError("Compact collection size limit must be a positive integer")
    return limit


def thread_path(directory):
    compact = settings_for(directory).get("store", "v1") == "compact"
    return Path(directory) / ("threads.v2.sqlite3" if compact else "threads.sqlite3")


def open_threads(directory, *, sample_every=100):
    from .handoff.thread_store import ThreadStore
    from .handoff.thread_store_v2 import ThreadStoreV2

    directory = Path(directory)
    path = thread_path(directory)
    other = directory / (
        "threads.sqlite3" if path.name == "threads.v2.sqlite3" else "threads.v2.sqlite3"
    )
    if not path.exists() and other.exists():
        raise ValueError(
            "Workspace thread format disagrees with settings; a verified store switch is required"
        )
    store = (ThreadStoreV2 if path.name == "threads.v2.sqlite3" else ThreadStore)(str(path))
    try:
        comparison = read_json(directory / ".store-comparison.json")
    except ValueError:
        return store
    if comparison:
        previous = directory.parent / comparison["previous"]
        if previous.is_dir() and thread_path(previous).exists():
            from mempalace.dual_read import SampledReads

            from .handoff.thread_store_migrate import read_store

            context = read_store(thread_path(previous), compact=comparison["store"] == "compact")
            try:
                old = context.__enter__()
            except (OSError, ValueError, sqlite3.Error):
                return store
            reader = SampledReads(
                store,
                old,
                reads=("get", "messages", "chunks", "search", "doc", "parent_of", "by_source"),
                sample_every=sample_every,
            )

            def close():
                try:
                    store.close()
                finally:
                    context.__exit__(None, None, None)

            reader.close = close
            return reader
    return store


def worker_config(directory):
    """Workspace settings override backend environment selection."""
    from mempalace.fsio import atomic_write_json

    directory = Path(directory)
    settings = settings_for(directory)
    path = directory / "config" / "config.json"
    config = read_json(path)
    desired = "compact" if settings.get("store", "v1") == "compact" else "chroma"
    if config.get("backend") != desired:
        config["backend"] = desired
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write_json(path, config)
    return config
