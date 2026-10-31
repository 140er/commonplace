"""Synthetic workspace cutover, rollback, off-host backup and crash recovery."""

import json
import logging
import shlex
import sqlite3
import sys
from contextlib import closing

import pytest

from commonplace import store_switch as switch
from commonplace.backup import BackupError, data_lock
from commonplace.handoff.models import ThreadMessage
from commonplace.handoff.thread_store import ThreadStore
from commonplace.handoff.thread_store_migrate import read_store, verify_stores
from commonplace.workspace_store import (
    POINTER,
    RECEIPT,
    SETTINGS,
    open_threads,
    read_json,
    thread_path,
    workspace_path,
    workspace_root,
)
from mempalace.backend_copy import open_collection, verify_palaces
from mempalace.backends import ChromaBackend, CompactBackend

WORKSPACE = "ws_fixture"
REAL_EMBEDDER = switch._embedder


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    data = tmp_path / "data"
    root = workspace_root(data, WORKSPACE)
    root.mkdir(parents=True)
    with closing(ThreadStore(str(root / "threads.sqlite3"))) as store:
        thread = store.store(
            WORKSPACE,
            "source",
            [ThreadMessage(0, "user", "message", "first", "raw first")],
            actor="actor",
        )
        store.append(
            thread.id,
            [
                ThreadMessage(0, "user", "message", "first", "raw first"),
                ThreadMessage(1, "assistant", "message", "second", "raw second"),
            ],
        )
        store.put_doc("handoff", "verbatim handoff")
        store.advance_fact_cursor(thread.id, 1, 1)
    backend = ChromaBackend()
    try:
        for name in ("mempalace_drawers", "mempalace_closets"):
            open_collection(backend, root / "palace", name, create=True).add(
                ids=["a"],
                documents=["verbatim drawer"],
                metadatas=[{"wing": "test", "last_modified": "2026-01-01"}],
                embeddings=[[1.0, 0.3, -0.1]],
            )
    finally:
        backend.close()
    for path in (
        root / "boards.sqlite3",
        root / "palace" / "knowledge_graph.sqlite3",
        root / "palace" / "logstream.sqlite3",
    ):
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE fixture(id TEXT PRIMARY KEY, content TEXT)")
            conn.execute("INSERT INTO fixture VALUES ('a','verbatim board or fact')")
    from commonplace.boards.store import BoardStore

    BoardStore(root)
    with closing(sqlite3.connect(root / "boards.sqlite3")) as conn, conn:
        conn.execute(
            "INSERT INTO boards(id,slug,name,wing,stamp_user,stamp_trust,created_at) "
            "VALUES (1,'fixture','Fixture','test','actor','standard','2026-01-01')"
        )
        conn.execute(
            "INSERT INTO tasks(id,board_id,title,body,status,position,stamp_user,stamp_trust,created_at,updated_at,updated_by) "
            "VALUES (1,1,'Original card','verbatim task','todo',1,'actor','standard','2026-01-01','2026-01-01','actor')"
        )
    (root / "palace" / "replica.json").write_text('{"replica_id":"fixture"}')
    (root / "palace" / "custom-sidecar").write_bytes(b"preserved\0")
    command = tmp_path / "backup_command.py"
    command.write_text(
        "import datetime,json,os\nprint(json.dumps({'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'off_host':True,'nonce':os.environ['COMMONPLACE_BACKUP_NONCE']}))\n"
    )
    monkeypatch.setenv("COMMONPLACE_BACKUP_CMD", shlex.join([sys.executable, str(command)]))
    monkeypatch.setattr(switch, "_verify_offline", lambda directory: None)
    monkeypatch.setattr(
        switch,
        "_embedder",
        lambda directory: (
            lambda documents, metadatas, **kwargs: [[1.0, 0.3, -0.1] for _ in documents]
        ),
    )
    return data, root, thread


def test_switch_write_rollback_every_record(workspace):
    data, root, thread = workspace
    assert switch.switch_workspace(data, WORKSPACE, "compact")["state"] == "complete"
    active = workspace_path(data, WORKSPACE)
    assert active != root
    assert (active / "palace" / "compact.sqlite3").exists()
    assert not (active / "palace" / "chroma.sqlite3").exists()
    assert read_json(active / SETTINGS)["store"] == "compact"
    assert read_json(active / "config" / "config.json")["backend"] == "compact"
    assert read_json(root / RECEIPT)["off_host"] is True
    assert (root / "threads.sqlite3").exists()
    from pathlib import Path

    from mempalace.backend_copy import verify_sidecars

    snapshot = Path(str(active) + ".verify-snapshot")
    switch._copy_tree(root / "palace", snapshot)
    verify_palaces(snapshot, active / "palace", "chroma", "compact")
    verify_sidecars(root / "palace", active / "palace", "chroma", skip={".store-comparison.json"})
    with closing(open_threads(active, sample_every=1)) as store:
        assert store.messages(thread.id)[1].text == "second"
        store.append(
            thread.id,
            store.messages(thread.id)
            + [ThreadMessage(2, "assistant", "message", "after switch", "raw after switch")],
        )
    backend = CompactBackend()
    try:
        open_collection(backend, active / "palace", "mempalace_drawers", create=True).add(
            ids=["new"],
            documents=["after switch"],
            metadatas=[{"last_modified": "2026-01-02"}],
            embeddings=[[0.5, 1.0, 0.7]],
        )
    finally:
        backend.close()
    with closing(sqlite3.connect(active / "boards.sqlite3")) as conn, conn:
        conn.execute("INSERT INTO fixture VALUES ('new','after switch')")
        conn.execute(
            "INSERT INTO tasks(id,board_id,title,body,status,position,stamp_user,stamp_trust,created_at,updated_at,updated_by) "
            "VALUES (2,1,'New card','after switch','todo',2,'actor','standard','2026-01-02','2026-01-02','actor')"
        )
    with closing(sqlite3.connect(active / "palace" / "knowledge_graph.sqlite3")) as conn, conn:
        conn.execute("INSERT INTO fixture VALUES ('new','after switch')")
    assert switch.switch_workspace(data, WORKSPACE, "v1")["state"] == "complete"
    restored = workspace_path(data, WORKSPACE)
    verify_palaces(active / "palace", restored / "palace", "compact", "chroma")
    with (
        read_store(thread_path(active), compact=True) as src,
        read_store(thread_path(restored)) as dst,
    ):
        verify_stores(src, dst)
        assert dst.messages(thread.id)[2].text == "after switch"
    for relative in ("boards.sqlite3", "palace/knowledge_graph.sqlite3"):
        with closing(sqlite3.connect(restored / relative)) as conn, conn:
            assert conn.execute("SELECT count(*) FROM fixture").fetchone()[0] == 2
    with closing(sqlite3.connect(restored / "boards.sqlite3")) as conn, conn:
        assert conn.execute("SELECT title,body FROM tasks ORDER BY id").fetchall() == [
            ("Original card", "verbatim task"),
            ("New card", "after switch"),
        ]
    doctor = switch.doctor_workspace(data, WORKSPACE)
    assert doctor["store"] == "v1" and doctor["backfill"] == "complete"


def test_missing_backup_refuses_before_staging(workspace, monkeypatch):
    data, root, _ = workspace
    monkeypatch.delenv("COMMONPLACE_BACKUP_CMD")
    with pytest.raises(BackupError, match="COMMONPLACE_BACKUP_CMD"):
        switch.switch_workspace(data, WORKSPACE, "compact")
    assert workspace_path(data, WORKSPACE) == root
    assert not list(root.parent.glob("*.generation-*"))


@pytest.mark.parametrize(
    "receipt,exit_code",
    [
        ({"completed_at": "2000-01-01T00:00:00+00:00", "off_host": True}, 0),
        ({"completed_at": "2099-01-01T00:00:00+00:00", "off_host": True}, 0),
        ({"completed_at": "2026-01-01T00:00:00+00:00", "off_host": False}, 0),
        ({"completed_at": "2026-01-01T00:00:00", "off_host": True}, 0),
        ({"completed_at": "invalid", "off_host": True}, 0),
        ({}, 1),
    ],
)
def test_backup_gate_rejects_bad_receipt_or_failed_command(
    workspace, monkeypatch, receipt, exit_code
):
    data, root, _ = workspace
    code = f"import sys; print({json.dumps(json.dumps(receipt))}); sys.exit({exit_code})"
    monkeypatch.setenv("COMMONPLACE_BACKUP_CMD", shlex.join([sys.executable, "-c", code]))
    with pytest.raises(BackupError):
        switch.switch_workspace(data, WORKSPACE, "compact")
    assert workspace_path(data, WORKSPACE) == root
    assert not (root / POINTER).exists()


def test_failed_verification_keeps_live_store_and_is_restartable(workspace, monkeypatch):
    data, root, _ = workspace
    from mempalace import backend_copy

    original = backend_copy.verify_records

    def mismatch(*args, **kwargs):
        raise backend_copy.CopyMismatch("injected mismatch")

    monkeypatch.setattr(backend_copy, "verify_records", mismatch)
    with pytest.raises(backend_copy.CopyMismatch):
        switch.switch_workspace(data, WORKSPACE, "compact")
    assert workspace_path(data, WORKSPACE) == root
    assert switch.doctor_workspace(data, WORKSPACE)["backfill"] == "failed"
    with data_lock(data):
        pass
    monkeypatch.setattr(backend_copy, "verify_records", original)
    switch.switch_workspace(data, WORKSPACE, "compact")
    assert switch.doctor_workspace(data, WORKSPACE)["backfill"] == "complete"


def test_switch_refuses_online_workspace(workspace):
    data, root, _ = workspace
    with data_lock(data):
        with pytest.raises(BackupError, match="in use"):
            switch.switch_workspace(data, WORKSPACE, "compact")
    assert workspace_path(data, WORKSPACE) == root


def test_lock_blocks_writes_during_backfill_and_releases_after(workspace):
    data, root, thread = workspace
    seen = []

    def checkpoint(step):
        if step in ("staging", "palace", "verified"):
            with pytest.raises(BackupError, match="in use"):
                with data_lock(data):
                    raise AssertionError("A writer acquired the offline workspace")
            if step == "palace":
                import subprocess

                code = """
import sys
from mempalace.palace import mine_palace_lock, MineAlreadyRunning
try:
    with mine_palace_lock(sys.argv[1]):
        raise AssertionError('Writer lease was not held')
except MineAlreadyRunning:
    pass
"""
                result = subprocess.run(
                    [sys.executable, "-c", code, str(root / "palace")],
                    capture_output=True,
                    env=dict(
                        __import__("os").environ,
                        HOME=str(root / "home"),
                        USERPROFILE=str(root / "home"),
                    ),
                    timeout=20,
                )
                assert result.returncode == 0, result.stderr.decode()
            seen.append(step)

    switch.switch_workspace(data, WORKSPACE, "compact", checkpoint=checkpoint)
    assert seen == ["staging", "palace", "verified"]
    with data_lock(data), closing(open_threads(workspace_path(data, WORKSPACE))) as store:
        assert store.get(thread.id) is not None


def test_retention_never_deletes_before_fourteen_days(workspace):
    data, root, _ = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    retired_at = read_json(root / POINTER)["retained"][0]["retired_at"]
    switch.prune_generations(data, WORKSPACE, now=retired_at + switch.RETENTION_SECONDS - 1)
    assert (root / "threads.sqlite3").exists()
    switch.prune_generations(data, WORKSPACE, now=retired_at + switch.RETENTION_SECONDS)
    assert not (root / "threads.sqlite3").exists()
    assert workspace_path(data, WORKSPACE) != root
    assert thread_path(workspace_path(data, WORKSPACE)).exists()


def test_workspace_size_limit_setting(workspace):
    data, root, _ = workspace
    backend = ChromaBackend()
    try:
        open_collection(backend, root / "palace", "mempalace_drawers", create=True).add(
            ids=["b"],
            documents=["another"],
            metadatas=[{"last_modified": "2026-01-01"}],
            embeddings=[[1.0, 1.0, 1.0]],
        )
    finally:
        backend.close()
    (root / SETTINGS).write_text('{"compact_max_collection_records":1}')
    with pytest.raises(ValueError, match="size limit"):
        switch.switch_workspace(data, WORKSPACE, "compact")
    assert workspace_path(data, WORKSPACE) == root
    (root / SETTINGS).write_text('{"compact_max_collection_records":2}')
    switch.switch_workspace(data, WORKSPACE, "compact")


def test_surrogate_rollback_refuses_and_keeps_every_byte(workspace):
    data, root, _ = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    active = workspace_path(data, WORKSPACE)
    with closing(open_threads(active)) as store:
        thread = store.store(
            WORKSPACE,
            "surrogate",
            [ThreadMessage(0, "user", "message", "lone \ud800", "raw \ud800")],
            actor="actor",
        )
    with pytest.raises(ValueError, match="1 threads.*cannot be represented by v1"):
        switch.switch_workspace(data, WORKSPACE, "v1")
    assert workspace_path(data, WORKSPACE) == active
    with closing(open_threads(active)) as store:
        assert store.messages(thread.id)[0].text == "lone \ud800"


@pytest.mark.parametrize("available", [True, False])
def test_keyword_only_rollback_uses_configured_embedder(workspace, monkeypatch, caplog, available):
    from mempalace.backends.compact import KEYWORD_ONLY

    data, root, _ = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    active = workspace_path(data, WORKSPACE)
    backend = CompactBackend()
    try:
        open_collection(backend, active / "palace", "mempalace_drawers", create=True).add(
            ids=["keyword"],
            documents=["keyword-only exact text"],
            metadatas=[{KEYWORD_ONLY: True, "last_modified": "2026-01-01"}],
        )
    finally:
        backend.close()

    def factory(directory):
        assert directory == active

        def embed(documents, metadatas, **kwargs):
            assert documents == ["keyword-only exact text"]
            if not available:
                raise ValueError("unavailable")
            return [[1.0, 0.1, 0.3]]

        return embed

    monkeypatch.setattr(switch, "_embedder", factory)
    with caplog.at_level(logging.WARNING):
        if available:
            switch.switch_workspace(data, WORKSPACE, "v1")
        else:
            with pytest.raises(ValueError, match="embedder unavailable"):
                switch.switch_workspace(data, WORKSPACE, "v1")
            assert workspace_path(data, WORKSPACE) == active
    assert "count=1" in caplog.text and "estimated_seconds=" in caplog.text
    assert "keyword-only exact text" not in caplog.text
    if available:
        backend = ChromaBackend()
        try:
            row = open_collection(
                backend, workspace_path(data, WORKSPACE) / "palace", "mempalace_drawers"
            ).get(ids=["keyword"], include=["documents", "metadatas", "embeddings"])
            assert row.documents == ["keyword-only exact text"]
            assert row.embeddings[0] is not None
            assert KEYWORD_ONLY not in row.metadatas[0]
        finally:
            backend.close()


@pytest.mark.parametrize(
    "step",
    [
        "backup",
        "staging",
        "files",
        "palace",
        "threads",
        "copied",
        "rehearsed",
        "verified",
        "published",
        "complete",
    ],
)
def test_process_crash_at_each_boundary_is_restartable(workspace, step):
    import subprocess

    data, root, thread = workspace
    code = """
import os,sys
from pathlib import Path
from commonplace import store_switch as switch
switch._verify_offline = lambda path: None
switch._embedder = lambda directory: lambda documents,metadatas,**kwargs: [[1.,.3,-.1] for _ in documents]
def checkpoint(step):
    if step == sys.argv[3]:
        os._exit(91)
switch.switch_workspace(Path(sys.argv[1]),sys.argv[2],'compact',checkpoint=checkpoint)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(data), WORKSPACE, step], capture_output=True, timeout=60
    )
    assert result.returncode == 91, result.stderr.decode()
    committed = step in ("published", "complete")
    assert (workspace_path(data, WORKSPACE) != root) == committed
    with data_lock(data), closing(open_threads(workspace_path(data, WORKSPACE))) as store:
        assert store.get(thread.id) is not None
        assert store.messages(thread.id)[1].text == "second"
    switch.switch_workspace(data, WORKSPACE, "compact")
    assert switch.doctor_workspace(data, WORKSPACE)["store"] == "compact"
    assert switch.doctor_workspace(data, WORKSPACE)["backfill"] == "complete"
    assert not any(
        path.name.endswith((".source-palace", ".probe", ".rollback-probe"))
        for path in root.parent.iterdir()
    )


def test_all_resolvers_follow_generation_pointer(workspace, monkeypatch):
    import argparse
    from contextlib import ExitStack
    from types import SimpleNamespace

    from commonplace.cli import _stores
    from commonplace.handoff.wiring import HandoffContext
    from commonplace.workers import WorkerSupervisor
    from commonplace.workspace_store import worker_config

    data, root, thread = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    active = workspace_path(data, WORKSPACE)
    supervisor = WorkerSupervisor(str(data))
    assert supervisor.workspace_dir(WORKSPACE) == active
    context = HandoffContext(gateway=None, control_db=None, data_dir=data)
    try:
        assert context.has_threads(WORKSPACE)
        assert context.store_for(WORKSPACE).get(thread.id)
        assert (
            context.store_for(WORKSPACE)._conn.execute("PRAGMA application_id").fetchone()[0] != 0
        )
    finally:
        context.close()
    with ExitStack() as stack:
        cli_store = _stores(argparse.Namespace(data=str(data)), stack)(WORKSPACE)
        assert cli_store.get(thread.id)
        assert cli_store._conn.execute("PRAGMA application_id").fetchone()[0] != 0
    seen = []
    monkeypatch.setattr(
        switch.subprocess,
        "run",
        lambda *a, **kw: (
            seen.append(kw["env"]["COMMONPLACE_WORKSPACE_DIR"])
            or SimpleNamespace(
                returncode=0,
                stderr="",
                stdout=json.dumps(
                    {
                        "completed_at": __import__("datetime")
                        .datetime.now(__import__("datetime").timezone.utc)
                        .isoformat(),
                        "off_host": True,
                        "nonce": kw["env"]["COMMONPLACE_BACKUP_NONCE"],
                    }
                ),
            )
        ),
    )
    switch.backup_workspace(root, active, WORKSPACE)
    assert seen == [str(active)]
    monkeypatch.setenv("MEMPALACE_BACKEND", "chroma")
    assert worker_config(active)["backend"] == "compact"
    monkeypatch.setenv("MEMPALACE_CONFIG_DIR", str(active / "config"))
    from mempalace.config import MempalaceConfig

    assert MempalaceConfig().backend == "compact"
    assert switch.doctor_workspace(data, WORKSPACE)["thread_index_version"] == 0


def test_fixed_workspace_thread_paths_are_only_built_by_resolver():
    import ast
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "commonplace"
    for path in package.rglob("*.py"):
        if path.name in ("workspace_store.py", "store_switch.py"):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                assert not (
                    isinstance(node.right, ast.Constant)
                    and node.right.value in ("threads.sqlite3", "threads.v2.sqlite3")
                ), str(path.relative_to(package))


def test_embedding_process_uses_workspace_config_and_excludes_operator_secrets(
    workspace, monkeypatch
):
    from types import SimpleNamespace

    data, root, _ = workspace
    monkeypatch.setenv("OPENAI_API_KEY", "operator-secret")
    monkeypatch.setenv("MEMPALACE_EMBEDDING_API_URL", "operator-endpoint")
    seen = []

    def run(argv, **kwargs):
        assert argv[-2:] == ["-m", "commonplace.store_embed"]
        assert kwargs["env"]["MEMPALACE_CONFIG_DIR"] == str(root / "config")
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "MEMPALACE_EMBEDDING_API_URL" not in kwargs["env"]
        seen.append(json.loads(kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout="[[1,0]]")

    monkeypatch.setattr(switch.subprocess, "run", run)
    assert REAL_EMBEDDER(root)(["text"], [{}]) == [[1, 0]]
    assert seen == [{"documents": ["text"], "metadatas": [{}], "identity": None, "dimension": None}]


def test_cli_store_doctor_and_rollback(workspace, monkeypatch, capsys):
    from commonplace.cli import main
    from commonplace.control_db import ControlDB

    data, root, _ = workspace
    with closing(ControlDB(str(data / "control.sqlite3"))) as db:
        db.create_user("owner@example.test", is_admin=True)
        wid = db.create_workspace("fixture", "owner@example.test")
    root.rename(root.with_name(wid))
    args = ["--data", str(data)]
    assert main(args + ["workspace", "store", "fixture", "compact"]) == 0
    capsys.readouterr()
    assert main(args + ["doctor", "--workspace", "fixture"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["store"] == "compact" and report["thread_index_version"] == 0
    assert main(args + ["workspace", "store", "fixture", "v1"]) == 0
    assert main(args + ["workspace", "prune-stores", "fixture"]) == 0


def test_failed_switch_removes_all_staging_and_unchanged_cleans_orphans(workspace):
    data, root, _ = workspace

    def fail(step):
        if step == "palace":
            raise ValueError("injected failure")

    with pytest.raises(ValueError, match="injected"):
        switch.switch_workspace(data, WORKSPACE, "compact", checkpoint=fail)
    assert list(root.parent.glob(root.name + ".generation-*")) == []
    orphan = root.with_name(root.name + ".generation-" + "a" * 24)
    (orphan / "payload").mkdir(parents=True)
    switch.switch_workspace(data, WORKSPACE, "v1")
    assert not orphan.exists()
    orphan.mkdir()
    switch.prune_generations(data, WORKSPACE)
    assert not orphan.exists()


def test_disk_space_gate_precedes_backup(workspace, monkeypatch):
    from types import SimpleNamespace

    data, root, _ = workspace
    monkeypatch.setattr(switch.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    monkeypatch.setattr(
        switch, "backup_workspace", lambda *args: pytest.fail("Backup ran before disk gate")
    )
    with pytest.raises(ValueError, match="free space"):
        switch.switch_workspace(data, WORKSPACE, "compact")
    assert workspace_path(data, WORKSPACE) == root


def test_prune_refuses_writes_in_retired_generation(workspace):
    import os

    data, root, _ = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    assert (root / "threads.sqlite3").stat().st_mode & 0o222 == 0
    # Simulate an older binary restoring write permissions and adding content.
    (root / "late-record").write_text("new verbatim data")
    retired_at = read_json(root / POINTER)["retained"][0]["retired_at"]
    stamp = int((retired_at + 1) * 1_000_000_000)
    os.utime(root / "late-record", ns=(stamp, stamp))
    with pytest.raises(ValueError, match="newer writes"):
        switch.prune_generations(data, WORKSPACE, now=retired_at + switch.RETENTION_SECONDS + 1)
    assert (root / "late-record").read_text() == "new verbatim data"
    assert switch.doctor_workspace(data, WORKSPACE)["retained_generations"][0]["newer_writes"]


@pytest.mark.parametrize("damage", ["missing", "missing_active", "nonexistent"])
@pytest.mark.parametrize("operation", ["prune", "compact", "v1"])
def test_invalid_pointer_preserves_active_generation_and_post_switch_writes(
    workspace, damage, operation
):
    data, root, _ = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    active = workspace_path(data, WORKSPACE)
    with closing(open_threads(active)) as store:
        added = store.store(
            WORKSPACE,
            "after-switch",
            [ThreadMessage(0, "user", "message", "post-switch verbatim", "raw")],
            actor="actor",
        )
    pointer = read_json(root / POINTER)
    if damage == "missing":
        (root / POINTER).unlink()
    else:
        if damage == "missing_active":
            pointer.pop("active")
        else:
            pointer["active"] = root.name + ".generation-" + "f" * 24
        (root / POINTER).write_text(json.dumps(pointer))
    with pytest.raises(ValueError, match="generation pointer"):
        if operation == "prune":
            switch.prune_generations(data, WORKSPACE)
        else:
            switch.switch_workspace(data, WORKSPACE, operation)
    assert active.is_dir()
    with read_store(active / "threads.v2.sqlite3", compact=True) as store:
        assert store.messages(added.id)[0].text == "post-switch verbatim"


def test_missing_pointer_preserves_generation_published_before_final_state(workspace):
    data, root, _ = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    active = workspace_path(data, WORKSPACE)
    (root / switch.STATE).write_text(
        json.dumps({"state": "copying", "staging": active.name, "store": "compact"})
    )
    (root / POINTER).unlink()
    with pytest.raises(ValueError, match="generation pointer"):
        switch.prune_generations(data, WORKSPACE)
    assert (active / "threads.v2.sqlite3").exists()


def test_invalid_pointer_only_cleans_recorded_unpublished_staging(workspace):
    data, root, _ = workspace
    staged = root.with_name(root.name + ".generation-" + "b" * 24)
    protected = root.with_name(root.name + ".generation-" + "c" * 24)
    protected.mkdir()
    (protected / "post-switch-data").write_text("preserve")
    for path in switch._staging_paths(staged):
        path.mkdir()
        (path / "payload").write_text("unfinished")
    (root / switch.STATE).write_text(json.dumps({"state": "copying", "staging": staged.name}))
    (root / POINTER).write_text("{}")
    with pytest.raises(ValueError, match="generation pointer"):
        switch.prune_generations(data, WORKSPACE)
    assert all(not path.exists() for path in switch._staging_paths(staged))
    assert (protected / "post-switch-data").read_text() == "preserve"
    assert read_json(root / switch.STATE)["state"] == "failed"


def test_doctor_and_unexpired_prune_do_not_hash_retained_data(workspace, monkeypatch):
    data, root, _ = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    monkeypatch.setattr(
        switch, "_retirement_digest", lambda *args: pytest.fail("Hashed unexpired generation")
    )
    assert not switch.doctor_workspace(data, WORKSPACE)["retained_generations"][0]["newer_writes"]
    retired_at = read_json(root / POINTER)["retained"][0]["retired_at"]
    switch.prune_generations(data, WORKSPACE, now=retired_at + switch.RETENTION_SECONDS - 1)
    assert (root / "threads.sqlite3").exists()


def test_prune_drops_missing_generation_and_resets_crash_state(workspace):
    import shutil

    data, root, _ = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    compact = workspace_path(data, WORKSPACE)
    switch.switch_workspace(data, WORKSPACE, "v1")
    shutil.rmtree(compact)
    staged = root.with_name(root.name + ".generation-" + "d" * 24)
    staged.mkdir()
    (root / switch.STATE).write_text(json.dumps({"state": "copying", "staging": staged.name}))
    switch.prune_generations(data, WORKSPACE)
    assert compact.name not in [item["name"] for item in read_json(root / POINTER)["retained"]]
    assert not staged.exists()
    assert switch.doctor_workspace(data, WORKSPACE)["backfill"] == "failed"


def test_corrupt_retained_thread_database_does_not_block_live_store(workspace):
    data, root, thread = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    (root / "threads.sqlite3").chmod(0o600)
    (root / "threads.sqlite3").write_bytes(b"corrupt retired database")
    with closing(open_threads(workspace_path(data, WORKSPACE))) as store:
        assert store.get(thread.id)


def test_prune_cli_authorizes_inside_exclusive_lock(workspace, monkeypatch, capsys):
    import commonplace.cli as cli

    data, _, _ = workspace
    original = cli._db
    opened = []

    def locked_db(args):
        with pytest.raises(BackupError):
            with data_lock(data):
                pytest.fail("Control database opened outside exclusive lock")
        opened.append(True)
        return original(args)

    monkeypatch.setattr(cli, "_db", locked_db)
    assert cli.main(["--data", str(data), "workspace", "prune-stores", "missing"]) == 2
    assert opened and "no such workspace" in capsys.readouterr().err


def test_palace_dual_read_skips_chroma_without_touching_retired_files(workspace):
    import hashlib
    from types import SimpleNamespace

    from mempalace.dual_read import observe_collection

    data, root, _ = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    active = workspace_path(data, WORKSPACE)
    before = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    current = SimpleNamespace(get=lambda: "live")
    assert observe_collection(current, str(active / "palace"), "mempalace_drawers") is current
    after = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert before == after


def test_thread_dual_read_and_close_from_another_thread(workspace, caplog):
    from concurrent.futures import ThreadPoolExecutor

    from commonplace.handoff.wiring import HandoffContext

    data, root, thread = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    context = HandoffContext(gateway=None, control_db=None, data_dir=data)
    store = context.store_for(WORKSPACE)
    store._sample_every = 1
    with (
        caplog.at_level(logging.INFO, logger="mempalace.dual_read"),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        assert pool.submit(store.messages, thread.id).result()[0].text == "first"
        pool.submit(context.close).result()
    assert "match=True" in caplog.text
    assert "comparison_unavailable" not in caplog.text


def test_rehearsal_probe_does_not_collide_with_existing_collection(workspace):
    data, root, _ = workspace
    backend = ChromaBackend()
    try:
        open_collection(backend, root / "palace", "rollback_verification", create=True).add(
            ids=["probe"],
            documents=["original"],
            metadatas=[{"last_modified": "2026-01-01"}],
            embeddings=[[1.0, 0.0, 0.0, 0.0]],
        )
    finally:
        backend.close()
    switch.switch_workspace(data, WORKSPACE, "compact")


def test_unexpected_source_backend_is_refused_before_backup(workspace, monkeypatch):
    import shutil

    from mempalace.backends import SQLiteExactBackend

    data, root, _ = workspace
    shutil.rmtree(root / "palace")
    backend = SQLiteExactBackend()
    try:
        open_collection(backend, root / "palace", "mempalace_drawers", create=True).add(
            ids=["original"], documents=["verbatim"], embeddings=[[1.0, 2.0]]
        )
    finally:
        backend.close()
    monkeypatch.setattr(
        switch, "backup_workspace", lambda *args: pytest.fail("Backup ran for unsupported backend")
    )
    with pytest.raises(ValueError, match="backend disagrees"):
        switch.switch_workspace(data, WORKSPACE, "compact")
    assert workspace_path(data, WORKSPACE) == root
    assert (root / "palace" / "sqlite_exact.sqlite3").exists()


def test_blank_backup_command_is_a_clear_refusal(workspace, monkeypatch):
    data, root, _ = workspace
    monkeypatch.setenv("COMMONPLACE_BACKUP_CMD", "   ")
    with pytest.raises(BackupError, match="COMMONPLACE_BACKUP_CMD"):
        switch.switch_workspace(data, WORKSPACE, "compact")
    assert workspace_path(data, WORKSPACE) == root


@pytest.mark.parametrize(
    "payload", ["boards.sqlite3", "rules.sqlite3", "config/extra.json", "home/verbatim"]
)
def test_final_verification_checks_all_workspace_files(workspace, payload):
    data, root, _ = workspace
    target = root / payload
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text("original")

    def corrupt(step):
        if step == "files":
            stage = root.parent / read_json(root / switch.STATE)["staging"]
            (stage / payload).write_bytes(b"changed")

    with pytest.raises((ValueError, sqlite3.Error, OSError)):
        switch.switch_workspace(data, WORKSPACE, "compact", checkpoint=corrupt)
    assert workspace_path(data, WORKSPACE) == root
    assert not list(root.parent.glob(root.name + ".generation-*"))


def test_stale_nonce_receipt_and_backup_diagnostics(workspace, monkeypatch):
    data, root, _ = workspace
    code = "import datetime,json,sys; print('backup diagnostic',file=sys.stderr); print(json.dumps({'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'off_host':True,'nonce':'other-run'}))"
    monkeypatch.setenv("COMMONPLACE_BACKUP_CMD", shlex.join([sys.executable, "-c", code]))
    with pytest.raises(BackupError, match="nonce"):
        switch.switch_workspace(data, WORKSPACE, "compact")
    assert (root / switch.BACKUP_STDERR).read_text() == "backup diagnostic\n"


def test_runtime_ignores_migration_limit_typo_and_refuses_missing_v1_settings(workspace):
    data, root, thread = workspace
    (root / SETTINGS).write_text('{"compact_max_collection_records":"typo"}')
    with closing(open_threads(root)) as store:
        assert store.get(thread.id)
    with pytest.raises(ValueError, match="positive integer"):
        switch.switch_workspace(data, WORKSPACE, "compact")
    (root / SETTINGS).unlink()
    switch.switch_workspace(data, WORKSPACE, "compact")
    active = workspace_path(data, WORKSPACE)
    (active / SETTINGS).unlink()
    with pytest.raises(ValueError, match="disagrees with settings"):
        open_threads(active)
    assert not (active / "threads.sqlite3").exists()


def test_rollback_preflight_refuses_surrogates_before_backup(workspace, monkeypatch):
    data, root, _ = workspace
    switch.switch_workspace(data, WORKSPACE, "compact")
    with closing(open_threads(workspace_path(data, WORKSPACE))) as store:
        store.store(
            WORKSPACE,
            "unrepresentable",
            [ThreadMessage(0, "user", "message", "\ud800", "\ud800")],
            actor="actor",
        )
    monkeypatch.setattr(
        switch, "backup_workspace", lambda *args: pytest.fail("Backup ran before surrogate gate")
    )
    with pytest.raises(ValueError, match="cannot be represented"):
        switch.switch_workspace(data, WORKSPACE, "v1")


@pytest.mark.parametrize(
    "identity,dimension",
    [
        ({"model_name": "other-model", "dimension": 3}, 3),
        ({"model_name": "fixture-model", "dimension": 4}, 4),
    ],
)
def test_store_embed_refuses_wrong_model_or_dimension_before_embedding(
    monkeypatch, identity, dimension
):
    import io

    import mempalace.backends.embedding_wrapper as wrapper
    import mempalace.embedding as embedding
    from commonplace.store_embed import main
    from mempalace.backends.base import EmbedderIdentity

    monkeypatch.setattr(
        embedding, "get_embedder_identity", lambda: EmbedderIdentity("fixture-model", 3)
    )
    monkeypatch.setattr(
        wrapper, "_embed_texts", lambda *a, **kw: pytest.fail("Embedded before identity check")
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "documents": ["text"],
                    "metadatas": [{}],
                    "identity": identity,
                    "dimension": dimension,
                }
            )
        ),
    )
    with pytest.raises(ValueError, match="does not match stored"):
        main()


def test_store_embed_checks_identity_and_embeds_keyword_probe(monkeypatch, capsys):
    import io

    import mempalace.backends.embedding_wrapper as wrapper
    import mempalace.embedding as embedding
    from commonplace.store_embed import main
    from mempalace.backends.base import EmbedderIdentity
    from mempalace.backends.compact import KEYWORD_ONLY

    seen = []
    monkeypatch.setattr(
        embedding, "get_embedder_identity", lambda: EmbedderIdentity("fixture-model", 3)
    )
    monkeypatch.setattr(
        wrapper, "_embed_texts", lambda documents, **kw: seen.extend(documents) or [[1.0, 0.0, 0.0]]
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "documents": ["keyword probe"],
                    "metadatas": [{KEYWORD_ONLY: True}],
                    "identity": {"model_name": "fixture-model", "dimension": 3},
                    "dimension": 3,
                }
            )
        ),
    )
    main()
    assert seen == ["keyword probe"]
    assert json.loads(capsys.readouterr().out) == [[1.0, 0.0, 0.0]]


def test_keyword_estimate_precedes_backup_and_rehearsal_exercises_it(
    workspace, monkeypatch, caplog
):
    from mempalace.backends.compact import KEYWORD_ONLY

    data, root, _ = workspace
    calls = []

    def factory(directory):
        def embed(documents, metadatas, *, identity=None, dimension=None):
            assert dimension == 3
            assert all(meta.get(KEYWORD_ONLY) is True for meta in metadatas)
            calls.extend(documents)
            return [[1.0, 0.0, 0.0] for _ in documents]

        return embed

    monkeypatch.setattr(switch, "_embedder", factory)
    switch.switch_workspace(data, WORKSPACE, "compact")
    assert "rollback keyword verification" in calls
    active = workspace_path(data, WORKSPACE)
    backend = CompactBackend()
    try:
        open_collection(backend, active / "palace", "mempalace_drawers", create=True).add(
            ids=["keyword"], documents=["verbatim"], metadatas=[{KEYWORD_ONLY: True}]
        )
    finally:
        backend.close()

    def before_backup(*args):
        assert "Rollback re-embedding count=1" in caplog.text
        raise BackupError("stopped before backup")

    caplog.clear()
    monkeypatch.setattr(switch, "backup_workspace", before_backup)
    with caplog.at_level(logging.WARNING), pytest.raises(BackupError, match="before backup"):
        switch.switch_workspace(data, WORKSPACE, "v1")


def test_palace_lock_namespace_does_not_share_reentrant_credit(tmp_path):
    import os
    import subprocess

    from mempalace.palace import mine_palace_lock

    palace = str(tmp_path / "palace")
    other = str(tmp_path / "other-locks")
    with mine_palace_lock(palace), mine_palace_lock(palace, lock_dir=other):
        code = """
import sys
from mempalace.palace import mine_palace_lock, MineAlreadyRunning
try:
    with mine_palace_lock(sys.argv[1], lock_dir=sys.argv[2]):
        raise AssertionError('Namespaced lease was bypassed')
except MineAlreadyRunning:
    pass
"""
        result = subprocess.run(
            [sys.executable, "-c", code, palace, other],
            env=dict(os.environ),
            capture_output=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr.decode()


def test_unknown_workspace_cli_refuses_without_traceback(workspace, capsys):
    from commonplace.cli import main

    data, _, _ = workspace
    assert main(["--data", str(data), "workspace", "store", "missing", "compact"]) == 2
    error = capsys.readouterr().err
    assert "no such workspace" in error and "Traceback" not in error


def test_backup_completion_must_follow_this_command_start(workspace, monkeypatch):
    import os
    import time
    from datetime import datetime, timezone
    from types import SimpleNamespace

    data, root, _ = workspace
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (time.time() - 100, time.time() - 100))

    def completed_before_start(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "completed_at": datetime.fromtimestamp(
                        time.time() - 1, timezone.utc
                    ).isoformat(),
                    "off_host": True,
                    "nonce": kwargs["env"]["COMMONPLACE_BACKUP_NONCE"],
                }
            ),
        )

    monkeypatch.setattr(switch.subprocess, "run", completed_before_start)
    with pytest.raises(BackupError, match="after its start"):
        switch.backup_workspace(root, root, WORKSPACE)


def test_size_gate_runs_before_backup(workspace, monkeypatch):
    data, root, _ = workspace
    (root / SETTINGS).write_text('{"compact_max_collection_records":0}')
    monkeypatch.setattr(
        switch, "backup_workspace", lambda *args: pytest.fail("Backup ran before size gate")
    )
    with pytest.raises(ValueError, match="positive integer"):
        switch.switch_workspace(data, WORKSPACE, "compact")
