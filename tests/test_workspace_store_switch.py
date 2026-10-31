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
        "import datetime,json\nprint(json.dumps({'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'off_host':True}))\n"
    )
    monkeypatch.setenv("COMMONPLACE_BACKUP_CMD", shlex.join([sys.executable, str(command)]))
    monkeypatch.setattr(switch, "_verify_offline", lambda directory: None)
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
    verify_palaces(root / "palace", active / "palace", "chroma", "compact")
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

        def embed(documents, metadatas):
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
                stdout=json.dumps(
                    {
                        "completed_at": __import__("datetime")
                        .datetime.now(__import__("datetime").timezone.utc)
                        .isoformat(),
                        "off_host": True,
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
    assert switch._embedder(root)(["text"], [{}]) == [[1, 0]]
    assert seen == [{"documents": ["text"], "metadatas": [{}]}]


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
