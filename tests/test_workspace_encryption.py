"""Synthetic owner/workspace checks against the real optional SQLCipher driver."""

import json
import os
import sqlite3
import sys
from contextlib import closing

import pytest

from commonplace.backup import BackupError, backup, restore, verify_backup
from commonplace.cli import main
from commonplace.control_db import ControlDB
from commonplace.handoff.models import ThreadMessage
from commonplace.workspace_encryption import encrypt_workspace
from commonplace.workspace_store import POINTER, open_threads, workspace_path, workspace_root
from mempalace import sqlite_crypto as crypto


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    pytest.importorskip("sqlcipher3")
    data = tmp_path / "data"
    with closing(ControlDB(str(data / "control.sqlite3"))) as db:
        owner = db.create_user("owner@example.test")
        wid = db.create_workspace("Synthetic workspace", owner)
    server = tmp_path / "server.key"
    recovery = tmp_path / "recovery.key"
    for path in (server, recovery):
        path.write_bytes(os.urandom(32))
        path.chmod(0o600)
    slots = tmp_path / "slots"
    monkeypatch.setenv("COMMONPLACE_KEY_STORE", str(slots))
    monkeypatch.setenv("COMMONPLACE_WRAPPING_KEY_FILE", str(server))
    monkeypatch.delenv("COMMONPLACE_KEY_SLOT", raising=False)
    return data, owner, wid, server, recovery, slots


def enable(workspace, *, checkpoint=lambda step: None):
    data, owner, wid, server, recovery, slots = workspace

    def authorize(ref):
        with closing(ControlDB(str(data / "control.sqlite3"))) as db:
            row = db.workspace(ref)
            db.require_owner(row["id"], owner)
            return row["id"]

    return encrypt_workspace(
        data,
        wid,
        backup_dir=data.parent / "before-encryption",
        key_store=slots,
        server_key_file=server,
        recovery_key_file=recovery,
        authorize=authorize,
        checkpoint=checkpoint,
    )


def store_message(path, wid, text="verbatim snowman ☃\x00"):
    with closing(open_threads(path)) as store:
        thread = store.store(
            wid,
            "synthetic-source",
            [ThreadMessage(0, "user", "message", text, "raw:" + text)],
            actor="person:synthetic",
        )
        return thread.id


def test_fresh_non_admin_owner_cli_encrypted_reopen(workspace):
    data, owner, wid, server, recovery, slots = workspace
    assert (
        main(
            [
                "--data",
                str(data),
                "workspace",
                "encrypt",
                wid,
                "--by",
                owner,
                "--backup-dir",
                str(data.parent / "before-encryption"),
                "--key-store",
                str(slots),
                "--server-key-file",
                str(server),
                "--recovery-key-file",
                str(recovery),
            ]
        )
        == 0
    )
    active = workspace_path(data, wid)
    thread = store_message(active, wid)
    with closing(open_threads(active)) as store:
        assert store.messages(thread)[0].text == "verbatim snowman ☃\x00"
        assert store.messages(thread)[0].raw == "raw:verbatim snowman ☃\x00"
        assert store.search([thread], "snowman")[0].thread_id == thread
        for pragma, value in (
            ("cipher_page_size", "16384"),
            ("cipher_hmac_algorithm", "HMAC_SHA256"),
            ("cache_size", "-65536"),
            ("temp_store", "2"),
        ):
            assert str(store._conn.execute("PRAGMA " + pragma).fetchone()[0]) == value
    path = active / "threads.v2.sqlite3"
    assert not path.read_bytes().startswith(b"SQLite format 3\x00")
    with closing(sqlite3.connect(path)) as plain, pytest.raises(sqlite3.DatabaseError):
        plain.execute("SELECT * FROM sqlite_master").fetchall()
    record = json.loads((slots / (wid + ".json")).read_text())
    assert set(record["slots"]) == {"server", "recovery"}
    assert str(server) not in json.dumps(record)
    assert not (data / "slots").exists()


def test_migration_preserves_compact_threads_boards_rules_graph_and_palace(workspace):
    from commonplace.boards.store import BoardStore
    from commonplace.rules.store import RuleStore
    from mempalace.backend_copy import open_collection
    from mempalace.backends.compact import CompactBackend
    from mempalace.knowledge_graph import KnowledgeGraph

    data, _, wid, *_ = workspace
    root = workspace_root(data, wid)
    root.mkdir(parents=True)
    (root / "workspace.json").write_text('{"store":"compact"}')
    thread = store_message(root, wid)
    BoardStore(root)
    RuleStore(root)
    with KnowledgeGraph(str(root / "palace" / "knowledge_graph.sqlite3")) as graph:
        graph.add_entity("Synthetic person", "person")
    backend = CompactBackend()
    try:
        col = open_collection(backend, root / "palace", "test", create=True)
        col.add(ids=["synthetic"], documents=["a verbatim drawer"], embeddings=[[1.0, 0.0]])
    finally:
        backend.close()
    result = enable(workspace)
    assert result["databases"] == 5
    active = workspace_path(data, wid)
    assert active != root
    with closing(open_threads(active)) as store:
        assert store.messages(thread)[0].raw == "raw:verbatim snowman ☃\x00"
    with BoardStore(active).connection() as conn:
        assert conn.execute("SELECT count(*) FROM boards").fetchone()[0] == 0
    with RuleStore(active).connection() as conn:
        assert conn.execute("SELECT count(*) FROM rules").fetchone()[0] == 0
    backend = CompactBackend()
    try:
        assert backend.detect(str(active / "palace"))
        col = open_collection(backend, active / "palace", "test")
        assert col.get(ids=["synthetic"]).documents == ["a verbatim drawer"]
    finally:
        backend.close()
    with KnowledgeGraph(str(active / "palace" / "knowledge_graph.sqlite3")) as graph:
        assert (
            graph._conn()
            .execute("SELECT name FROM entities WHERE name=?", ("Synthetic person",))
            .fetchone()[0]
            == "Synthetic person"
        )
    # Plaintext rollback generations are retained deliberately, not called encrypted.
    with closing(sqlite3.connect(root / "threads.v2.sqlite3")) as original:
        assert original.execute("PRAGMA quick_check").fetchone() == ("ok",)


@pytest.mark.parametrize("failure", ["wrong", "missing", "missing-slots", "missing-driver"])
def test_key_and_driver_fail_closed(workspace, monkeypatch, failure):
    data, _, wid, server, _, slots = workspace
    enable(workspace)
    active = workspace_path(data, wid)
    if failure == "wrong":
        server.write_bytes(os.urandom(32))
    elif failure == "missing":
        monkeypatch.delenv("COMMONPLACE_WRAPPING_KEY_FILE")
    elif failure == "missing-slots":
        (slots / (wid + ".json")).unlink()
    else:
        monkeypatch.setitem(sys.modules, "sqlcipher3", None)
    target = active / "not-created.sqlite3"
    with pytest.raises(crypto.EncryptionError):
        crypto.connect(target)
    assert not target.exists()


def test_wrong_raw_data_key_fails_closed(workspace):
    data, _, wid, *_ = workspace
    enable(workspace)
    active = workspace_path(data, wid)
    store_message(active, wid)
    path = active / "threads.v2.sqlite3"
    before = path.read_bytes()
    with pytest.raises(crypto.EncryptionError):
        crypto.cipher_connect(path, os.urandom(32))
    assert path.read_bytes() == before


def test_ciphertext_backup_restore_without_keys_then_recovery_reopen(workspace, monkeypatch):
    data, _, wid, _, recovery, slots = workspace
    enable(workspace)
    active = workspace_path(data, wid)
    thread = store_message(active, wid)
    # Leave committed rows in a live WAL to prove the checkpoint includes them.
    writer = crypto.connect(active / "wal-fixture.sqlite3", isolation_level=None)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE fixture(content TEXT)")
        writer.execute("INSERT INTO fixture VALUES ('committed synthetic WAL row')")
        snapshot = data.parent / "encrypted-backup"
        manifest = backup(data, snapshot)
        relative = active.relative_to(data) / "wal-fixture.sqlite3"
        assert (snapshot / relative).read_bytes() == (data / relative).read_bytes()
        assert not any(entry["path"].endswith(("-wal", "-shm")) for entry in manifest["files"])
    finally:
        writer.close()
    monkeypatch.delenv("COMMONPLACE_WRAPPING_KEY_FILE")
    monkeypatch.delenv("COMMONPLACE_KEY_STORE")
    restored = data.parent / "restored"
    verify_backup(snapshot)
    restore(snapshot, restored)
    recovered = workspace_path(restored, wid)
    for path in ("threads.v2.sqlite3", "wal-fixture.sqlite3"):
        assert (recovered / path).read_bytes() == (
            snapshot / recovered.relative_to(restored) / path
        ).read_bytes()
    with pytest.raises(crypto.EncryptionError):
        open_threads(recovered)
    # Only a recovery slot and its independently held credential are needed.
    record_path = slots / (wid + ".json")
    record = json.loads(record_path.read_text())
    del record["slots"]["server"]
    record_path.write_text(json.dumps(record))
    monkeypatch.setenv("COMMONPLACE_KEY_STORE", str(slots))
    monkeypatch.setenv("COMMONPLACE_WRAPPING_KEY_FILE", str(recovery))
    monkeypatch.setenv("COMMONPLACE_KEY_SLOT", "recovery")
    with closing(open_threads(recovered)) as store:
        assert store.messages(thread)[0].text == "verbatim snowman ☃\x00"
    with closing(crypto.connect(recovered / "wal-fixture.sqlite3")) as conn:
        assert (
            conn.execute("SELECT content FROM fixture").fetchone()[0]
            == "committed synthetic WAL row"
        )


def test_byte_copy_retains_unchanged_cipher_pages(workspace):
    data, _, wid, *_ = workspace
    enable(workspace)
    active = workspace_path(data, wid)
    path = active / "dedup.sqlite3"
    with closing(crypto.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE fixture(id INTEGER PRIMARY KEY, value BLOB)")
        conn.executemany(
            "INSERT INTO fixture VALUES (?,?)", [(i, os.urandom(12000)) for i in range(100)]
        )
    first, second = data.parent / "backup-one", data.parent / "backup-two"
    backup(data, first)
    with closing(crypto.connect(path)) as conn, conn:
        conn.execute("UPDATE fixture SET value=? WHERE id=50", (os.urandom(12000),))
    backup(data, second)
    relative = path.relative_to(data)
    before, after = (first / relative).read_bytes(), (second / relative).read_bytes()
    page = 16384
    same = sum(before[i : i + page] == after[i : i + page] for i in range(0, len(before), page))
    total = len(before) // page
    print(f"ciphertext dedup: {same}/{total} unchanged pages ({same / total:.1%})")
    assert same / total > 0.9
    assert before != after


def test_migration_failure_keeps_original_and_verified_backup(workspace):
    data, _, wid, _, _, slots = workspace
    root = workspace_root(data, wid)
    root.mkdir(parents=True)
    (root / "workspace.json").write_text('{"store":"compact"}')
    thread = store_message(root, wid)

    def fail(step):
        if step == "encrypted":
            raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError, match="synthetic"):
        enable(workspace, checkpoint=fail)
    assert workspace_path(data, wid) == root
    assert not (root / POINTER).exists()
    assert not list(root.parent.glob(wid + ".generation-*"))
    assert not list(slots.iterdir())
    with closing(open_threads(root)) as store:
        assert store.messages(thread)[0].text == "verbatim snowman ☃\x00"
    restored = data.parent / "rollback"
    restore(data.parent / "before-encryption", restored)
    with closing(open_threads(workspace_path(restored, wid))) as store:
        assert store.messages(thread)[0].raw == "raw:verbatim snowman ☃\x00"


def test_non_owner_cannot_enable_encryption(workspace):
    data, _, wid, server, recovery, slots = workspace
    with closing(ControlDB(str(data / "control.sqlite3"))) as db:
        other = db.create_user("other@example.test")
    assert (
        main(
            [
                "--data",
                str(data),
                "workspace",
                "encrypt",
                wid,
                "--by",
                other,
                "--backup-dir",
                str(data.parent / "before-encryption"),
                "--key-store",
                str(slots),
                "--server-key-file",
                str(server),
                "--recovery-key-file",
                str(recovery),
            ]
        )
        == 2
    )
    assert not (workspace_root(data, wid) / POINTER).exists()


def test_keyslot_binding_rejects_workspace_or_slot_substitution():
    key, wrapping = os.urandom(32), os.urandom(32)
    info = {"workspace_id": "ws_synthetic", "key_id": "a" * 32}
    wrapped = crypto.wrap_key(key, wrapping, info, "server")
    record = {
        "version": 1,
        "algorithm": "AES-256-GCM",
        **info,
        "slots": {"server": wrapped, "recovery": wrapped},
    }
    assert crypto.unwrap_key(record, wrapping, info, "server") == key
    with pytest.raises(crypto.EncryptionError):
        crypto.unwrap_key(record, wrapping, info, "recovery")
    with pytest.raises(crypto.EncryptionError):
        crypto.unwrap_key(record, wrapping, {**info, "workspace_id": "ws_other"}, "server")


def test_plaintext_driver_cannot_satisfy_cipher_request(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto, "driver", lambda: sqlite3)
    with pytest.raises(crypto.EncryptionError, match="no SQLCipher"):
        crypto.cipher_connect(tmp_path / "unkeyed.sqlite3", os.urandom(32))


def test_backup_refuses_active_writer(workspace):
    data, _, wid, *_ = workspace
    enable(workspace)
    active = workspace_path(data, wid)
    thread = store_message(active, wid)
    with closing(crypto.connect(active / "threads.v2.sqlite3", isolation_level=None)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(BackupError, match="checkpoint|changed"):
            backup(data, data.parent / "busy-backup")
    assert not (data.parent / "busy-backup").exists()
    with closing(open_threads(active)) as store:
        assert store.messages(thread)[0].text == "verbatim snowman ☃\x00"


def test_publish_fsync_failure_preserves_selected_ciphertext_and_slots(workspace, monkeypatch):
    from commonplace import workspace_encryption as migration

    data, _, wid, _, _, slots = workspace
    write = migration.atomic_write_json

    def fail_after_replace(path, value, **kwargs):
        write(path, value, **kwargs)
        if path.name == POINTER:
            raise OSError("synthetic post-replace failure")

    monkeypatch.setattr(migration, "atomic_write_json", fail_after_replace)
    with pytest.raises(OSError, match="post-replace"):
        enable(workspace)
    active = workspace_path(data, wid)
    assert active != workspace_root(data, wid)
    assert (slots / (wid + ".json")).is_file()
    thread = store_message(active, wid)
    with closing(open_threads(active)) as store:
        assert store.messages(thread)[0].text == "verbatim snowman ☃\x00"
