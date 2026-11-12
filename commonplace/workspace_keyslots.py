"""Check and re-wrap the keyslots of an encrypted workspace.

A keyslot holds the workspace data key wrapped by one operator credential.
Re-wrapping replaces one slot's credential; it never changes the data key, so
it re-encrypts no database and old backups stay readable. For the same reason
it does not revoke a credential that was copied together with an old keyslot
record. Only AES-256-GCM wraps from ``mempalace.sqlite_crypto`` are used.
"""

from __future__ import annotations

import hmac
import json
import os
import stat
from pathlib import Path

from mempalace.fsio import atomic_write_json, fsync_directory
from mempalace.sqlite_crypto import (
    EncryptionError,
    cipher_connect,
    driver,
    encryption_settings,
    is_workspace_database,
    protected_key,
    unwrap_key,
    wrap_key,
)

from .backup import _verify_offline, data_lock
from .workspace_store import SETTINGS, resolve_workspace, workspace_root

SLOTS = ("server", "recovery")
_RECORD_FIELDS = {"version", "workspace_id", "key_id", "algorithm", "slots"}
_SIDECARS = ("-wal", "-shm", "-journal")


def _external(data, *paths):
    """Credentials and keyslots never live inside the data directory."""
    resolved = [Path(path).absolute() for path in paths]
    for path in resolved:
        if path.resolve().is_relative_to(data):
            raise EncryptionError("Key store and wrapping keys must be outside the data directory")
    return resolved


def _slot(name):
    if name not in SLOTS:
        raise EncryptionError("Keyslot must be server or recovery")
    return name


def _encryption(data, workspace_id):
    active = resolve_workspace(workspace_root(data, workspace_id))
    settings = encryption_settings(active / SETTINGS) if active.is_dir() else None
    if settings is None or settings[0].resolve() != active.resolve():
        raise EncryptionError("Workspace is not encrypted")
    return active, settings[1]


def _read_record(key_dir, info):
    path = key_dir / (info["workspace_id"] + ".json")
    if key_dir.is_symlink() or path.is_symlink():
        raise EncryptionError("Keyslot file cannot be a symlink")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EncryptionError("Workspace keyslot is unavailable") from None
    if (
        not isinstance(record, dict)
        or set(record) != _RECORD_FIELDS
        or not isinstance(record["slots"], dict)
        or not set(record["slots"]) <= set(SLOTS)
    ):
        raise EncryptionError("Workspace keyslot record has an unsupported format")
    return path, record


def _verify_databases(active, info, key):
    """Open every encrypted database read-only; a key that fits none is refused."""
    count = 0
    for directory, names, files in os.walk(active):
        names[:] = [name for name in names if not (Path(directory) / name).is_symlink()]
        for name in sorted(files):
            path = Path(directory) / name
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                continue  # A SQLite sidecar removed by a running service.
            if (
                not stat.S_ISREG(mode)
                or name.endswith(_SIDECARS)
                or not is_workspace_database(path, active, info)
            ):
                continue
            cipher_connect(path.as_uri() + "?mode=ro", key, uri=True).close()
            count += 1
    return count


def _unlock(data, workspace_id, key_dir, slot, key_file):
    active, info = _encryption(data, workspace_id)
    driver()
    path, record = _read_record(key_dir, info)
    key = unwrap_key(record, protected_key(key_file), info, slot)
    return info, path, record, key, _verify_databases(active, info, key)


def check_keyslot(data_dir, workspace_id, *, key_store, slot, key_file, authorize):
    """Prove that one credential unlocks its slot and the workspace databases.

    Nothing is written. ``authorize`` resolves the workspace reference. The
    caller holds the ordinary shared data lock; the service may keep running.
    """
    data = Path(data_dir).resolve()
    key_dir, key_file = _external(data, key_store, key_file)
    workspace_id = authorize(workspace_id)
    info, _, record, _, databases = _unlock(data, workspace_id, key_dir, _slot(slot), key_file)
    return {
        "state": "ok",
        "slot": slot,
        "slots": sorted(record["slots"]),
        "databases": databases,
        "key_id": info["key_id"],
    }


def rewrap_keyslot(
    data_dir,
    workspace_id,
    *,
    key_store,
    slot,
    new_key_file,
    unlock_slot,
    unlock_key_file,
    authorize,
):
    """Offline: wrap the unchanged data key for ``slot`` under a new credential.

    ``unlock_slot`` and its credential prove access first; unlocking with the
    recovery slot re-creates a lost server slot. ``authorize`` resolves the
    workspace reference and enforces owner authorization under the lock. The
    record is replaced atomically, other slots are kept byte for byte, and the
    published record is read back and unlocked before success is reported.
    """
    data = Path(data_dir).resolve()
    key_dir, new_key_file, unlock_key_file = _external(
        data, key_store, new_key_file, unlock_key_file
    )
    _slot(slot), _slot(unlock_slot)
    with data_lock(data, exclusive=True):
        workspace_id = authorize(workspace_id)
        _verify_offline(data)
        info, path, record, key, databases = _unlock(
            data, workspace_id, key_dir, unlock_slot, unlock_key_file
        )
        new_key = protected_key(new_key_file)
        # A credential that already opens a slot must not guard another one.
        for other in record["slots"]:
            try:
                unwrap_key(record, new_key, info, other)
            except EncryptionError:
                continue
            raise EncryptionError("The new wrapping key must differ from every current slot key")
        updated = {
            **record,
            "slots": {**record["slots"], slot: wrap_key(key, new_key, info, slot)},
        }
        if not hmac.compare_digest(key, unwrap_key(updated, new_key, info, slot)):
            raise EncryptionError("Workspace keyslot verification failed")
        atomic_write_json(path, updated, follow_symlinks=False)
        fsync_directory(key_dir)
        _, published = _read_record(key_dir, info)
        if published != updated or not hmac.compare_digest(
            key, unwrap_key(published, new_key, info, slot)
        ):
            raise EncryptionError("Workspace keyslot verification failed")
        return {
            "state": "rewrapped",
            "slot": slot,
            "slots": sorted(updated["slots"]),
            "databases": databases,
            "key_id": info["key_id"],
        }
