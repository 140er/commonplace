"""Offline backups preserve committed SQLite WAL data and verbatim files."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from commonplace.backup import BackupError, backup, data_lock
from commonplace.control_db import ControlDB


@pytest.fixture
def populated(tmp_path):
    data = tmp_path / "data"
    db = ControlDB(str(data / "control.sqlite3"))
    user = db.create_user("owner@example.test", is_admin=True)
    ws = db.create_workspace("shared", user)
    workspace = data / "workspaces" / ws
    palace = workspace / "palace"
    palace.mkdir(parents=True)
    (palace / "segments").mkdir()
    content = b"Every word, exactly.\n\x00\xff"
    (palace / "segments" / "verbatim.bin").write_bytes(content)
    (workspace / "config").mkdir()
    (workspace / "config" / "config.json").write_text('{"local": true}\n')
    legacy = workspace / "home" / ".mempalace"
    legacy.mkdir(parents=True)
    connections = []
    relative_databases = [
        f"workspaces/{ws}/boards.sqlite3",
        f"workspaces/{ws}/palace/knowledge_graph.sqlite3",
        f"workspaces/{ws}/palace/chroma.sqlite3",
        f"workspaces/{ws}/home/.mempalace/knowledge_graph.sqlite3",
    ]
    for relative in relative_databases:
        conn = sqlite3.connect(data / relative, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute("INSERT INTO memories(content) VALUES (?)", ("Never paraphrase me.\n",))
        connections.append(conn)
    try:
        yield data, ws, relative_databases
    finally:
        for conn in connections:
            conn.close()
        db.close()


def test_backup_restores_committed_wal_rows_and_hashes(populated, tmp_path):
    data, ws, databases = populated
    original_files = {
        p.relative_to(data): p.read_bytes()
        for p in data.rglob("*")
        if p.is_file() and not p.name.endswith("-shm")
    }
    # Connections are deliberately still open: these rows have not been
    # checkpointed into the main databases when the snapshot starts.
    assert all(Path(str(data / p) + "-wal").stat().st_size for p in databases)
    destination = tmp_path / "backup"
    manifest = backup(data, destination)
    assert json.loads((destination / "manifest.json").read_text()) == manifest
    entries = {entry["path"]: entry for entry in manifest["files"]}
    actual = {
        p.relative_to(destination).as_posix()
        for p in destination.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    assert set(entries) == actual
    for relative, entry in entries.items():
        content = (destination / relative).read_bytes()
        assert entry["size"] == len(content)
        assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    assert not any(name.endswith(("-wal", "-shm", "-journal")) for name in entries)
    for relative in databases:
        # DELETE journal mode means the restored DB needs no WAL sidecars.
        conn = sqlite3.connect(destination / relative)
        try:
            assert conn.execute("SELECT content FROM memories").fetchall() == [
                ("Never paraphrase me.\n",)
            ]
            assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
            assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        finally:
            conn.close()
    repeated = backup(destination, tmp_path / "backup-again")
    assert "manifest.json" not in {entry["path"] for entry in repeated["files"]}
    assert {entry["path"] for entry in repeated["files"]} == set(entries)
    for entry in repeated["files"]:
        assert (
            entry["sha256"]
            == hashlib.sha256((tmp_path / "backup-again" / entry["path"]).read_bytes()).hexdigest()
        )
    restored = ControlDB(str(destination / "control.sqlite3"))
    try:
        assert restored.users()[0]["email"] == "owner@example.test"
        assert restored.workspace(ws)["name"] == "shared"
    finally:
        restored.close()
    for relative, content in original_files.items():
        assert (data / relative).read_bytes() == content
    assert (destination / "workspaces" / ws / "palace/segments/verbatim.bin").read_bytes() == (
        b"Every word, exactly.\n\x00\xff"
    )


def test_cross_process_active_lock_rejects_before_opening_database(
    populated, tmp_path, monkeypatch
):
    data, _, _ = populated
    script = (
        "import sys; from commonplace.backup import data_lock; "
        "lock = data_lock(sys.argv[1]); lock.__enter__(); "
        "print('locked', flush=True); sys.stdin.read()"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(data)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "locked"

        def forbidden(*_args, **_kwargs):
            raise AssertionError("backup opened SQLite before obtaining the exclusive lock")

        monkeypatch.setattr(sqlite3, "connect", forbidden)
        with pytest.raises(BackupError, match="in use"):
            backup(data, tmp_path / "backup")
        assert not (tmp_path / "backup").exists()
        assert not list(tmp_path.glob(".backup.partial-*"))
    finally:
        proc.communicate(timeout=5)
        assert proc.returncode == 0


def test_exclusive_lock_blocks_cli_mutations(populated):
    data, _, _ = populated
    with data_lock(data, exclusive=True):
        with pytest.raises(BackupError, match="in use"):
            with data_lock(data):
                pytest.fail("shared lock admitted a mutation during backup")


def test_orphan_worker_environment_rejected(populated, tmp_path):
    data, ws, _ = populated
    env = {**os.environ, "MEMPALACE_PALACE_PATH": str(data / "workspaces" / ws / "palace")}
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.read()"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "ready"
        with pytest.raises(BackupError, match="worker process"):
            backup(data, tmp_path / "backup")
        assert not (tmp_path / "backup").exists()
    finally:
        proc.communicate(timeout=5)


def test_orphan_worker_command_rejected(populated, tmp_path):
    data, ws, _ = populated
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEMPALACE_")}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.read()",
            "mempalace.mcp_server",
            "--palace",
            str(data / "workspaces" / ws / "palace"),
        ],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "ready"
        with pytest.raises(BackupError, match="in use by process"):
            backup(data, tmp_path / "backup")
    finally:
        proc.communicate(timeout=5)


def test_invalid_destinations_preserve_source(populated, tmp_path):
    data, _, _ = populated
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "keep"
    sentinel.write_text("unchanged")
    for dest in (data, data / "backup", data.parent, existing, tmp_path / "missing" / "backup"):
        with pytest.raises(BackupError):
            backup(data, dest)
    assert sentinel.read_text() == "unchanged"
    assert (data / "control.sqlite3").is_file()
    with pytest.raises(BackupError, match="existing directory"):
        backup(tmp_path / "not-set-up", tmp_path / "backup")


def test_symlinks_refused_but_shared_model_cache_omitted(populated, tmp_path):
    data, ws, _ = populated
    home = data / "workspaces" / ws / "home"
    cache = home / ".cache"
    cache.mkdir()
    external = tmp_path / "operator-model-cache"
    external.mkdir()
    (external / "weights").write_text("disposable")
    (cache / "chroma").symlink_to(external, target_is_directory=True)
    destination = tmp_path / "backup"
    backup(data, destination)
    assert not (destination / "workspaces" / ws / "home/.cache/chroma").exists()
    unsafe = data / "workspaces" / ws / "palace" / "outside"
    unsafe.symlink_to(external, target_is_directory=True)
    with pytest.raises(BackupError, match="symlink"):
        backup(data, tmp_path / "unsafe-backup")
    assert not (tmp_path / "unsafe-backup").exists()


def test_snapshot_failure_removes_partial_and_unlocks(populated, tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("commonplace.backup")
    data, _, _ = populated
    original = module._snapshot

    def fail(*_args):
        raise sqlite3.OperationalError("broken snapshot")

    monkeypatch.setattr(module, "_snapshot", fail)
    with pytest.raises(sqlite3.OperationalError, match="broken snapshot"):
        backup(data, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()
    assert not list(tmp_path.glob(".backup.partial-*"))
    monkeypatch.setattr(module, "_snapshot", original)
    backup(data, tmp_path / "backup")


@pytest.mark.parametrize("candidate", [False, True])
def test_denied_process_environment_only_blocks_worker_candidates(tmp_path, monkeypatch, candidate):
    import importlib

    module = importlib.import_module("commonplace.backup")
    proc_root = tmp_path / "proc"
    (proc_root / "self").mkdir(parents=True)
    (proc_root / "self" / "environ").write_bytes(b"")
    process = proc_root / str(os.getpid() + 1000000)
    process.mkdir()
    (process / "cmdline").write_bytes(
        b"/usr/bin/python3\x00-m\x00mempalace.mcp_server\x00"
        if candidate
        else b"/usr/lib/systemd/systemd\x00--user\x00"
    )
    denied = process / "environ"
    read_bytes = Path.read_bytes

    def read(path):
        if path == denied:
            raise PermissionError("protected process environment")
        return read_bytes(path)

    monkeypatch.setattr(module, "_PROC_ROOT", proc_root)
    monkeypatch.setattr(Path, "read_bytes", read)
    if candidate:
        with pytest.raises(BackupError, match="cannot verify"):
            module._verify_offline(tmp_path / "data")
    else:
        module._verify_offline(tmp_path / "data")


def test_parent_that_looks_like_a_worker_is_not_one(populated, tmp_path):
    # docker-init under compose `init: true` (or any wrapper) runs with our
    # command line and COMMONPLACE_DATA; it is an ancestor, not a writer.
    data = populated[0]
    dest = tmp_path / "snap"
    wrapper = (
        "import subprocess, sys; "
        f"sys.exit(subprocess.call([sys.executable, '-m', 'commonplace', '--data', {str(data)!r}, "
        f"'backup', {str(dest)!r}]))"
    )
    env = {**os.environ, "COMMONPLACE_DATA": str(data)}
    result = subprocess.run(
        [sys.executable, "-c", wrapper, "commonplace"], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert (dest / "manifest.json").exists()
