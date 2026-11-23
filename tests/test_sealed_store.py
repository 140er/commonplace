"""M01/M04-M10: synthetic internal storage, including deterministic open races."""

from __future__ import annotations

import builtins
import hashlib
import multiprocessing
import os
import shutil
import socket
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from commonplace.sealed import (
    SealedEnvelope,
    SealedStoreError,
    ServerWriter,
    open_sealed_store,
    store as implementation,
)

WRITER = ServerWriter("synthetic-user", "synthetic-connection", "trusted")
STAMP = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def envelope(number=1, kind="memory"):
    return SealedEnvelope(
        1,
        kind,
        f"{number:032x}",
        "f" * 32,
        number.to_bytes(12, "big"),
        (b"\x00\xff\r\n\x80\t" * 8) if kind == "printed-recovery" else b"\x00\xff\r\n\x80\t" * 11,
    )


def database(root, workspace="ws_A"):
    return root / "sealed" / workspace / "records.sqlite3"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_code(code, call):
    with pytest.raises(SealedStoreError) as failure:
        call()
    assert failure.value.code == code
    assert "synthetic-forbidden-body" not in str(failure.value)


def test_memory_and_printed_wrap_bytes_survive_close_reopen(tmp_path):
    with open_sealed_store(tmp_path, "ws_A", create=True, clock=lambda: STAMP) as store:
        memory = store.put(envelope(), writer=WRITER)
        wrap = store.put(envelope(2, "printed-recovery"), writer=WRITER)
    with open_sealed_store(tmp_path, "ws_A") as reopened:
        assert reopened.get(kind="memory", record_id=envelope().record_id) == memory.record
        assert reopened.get(kind="printed-recovery", record_id=envelope(2).record_id) == wrap.record
        assert reopened.list(kind="memory").records == (memory.record,)
        assert reopened.list(kind="printed-recovery").records == (wrap.record,)
    assert memory.inserted and wrap.inserted
    assert memory.record.sequence < wrap.record.sequence
    assert memory.record.written_at == "2026-01-02T03:04:05.000000Z"
    assert (tmp_path / "sealed").stat().st_mode & 0o777 == 0o700
    assert database(tmp_path).stat().st_mode & 0o777 == 0o600


def test_workspace_binding_and_path_escape_refuse_without_touching_victim(tmp_path):
    with open_sealed_store(tmp_path, "ws_A", create=True) as store:
        original = store.put(envelope(), writer=WRITER).record
    source = database(tmp_path)
    before = digest(source)
    foreign = database(tmp_path, "ws_B")
    foreign.parent.mkdir(mode=0o700)
    shutil.copyfile(source, foreign)
    foreign.chmod(0o600)
    assert_code("WORKSPACE_MISMATCH", lambda: open_sealed_store(tmp_path, "ws_B"))
    assert digest(source) == digest(foreign) == before
    for workspace in ("../ws_A", "ws_../A", "ws_A/child", "ws_", ""):
        assert_code(
            "UNSAFE_PATH",
            lambda workspace=workspace: open_sealed_store(tmp_path, workspace, create=True),
        )
    linked = tmp_path / "sealed" / "ws_Link"
    linked.symlink_to(source.parent, target_is_directory=True)
    assert_code("UNSAFE_PATH", lambda: open_sealed_store(tmp_path, "ws_Link", create=True))
    with open_sealed_store(tmp_path, "ws_A") as reopened:
        assert reopened.get(kind="memory", record_id=envelope().record_id) == original
    assert digest(source) == before


@pytest.mark.parametrize(
    "replacement",
    [
        "database-link",
        "database-hardlink",
        "database-copy",
        "directory-link",
        "-journal",
        "-wal",
        "-shm",
        "journal-hardlink",
    ],
)
def test_replacement_between_validation_and_sqlite_open_preserves_victim(
    tmp_path, monkeypatch, replacement
):
    root = tmp_path / "owned"
    with open_sealed_store(root, "ws_A", create=True) as store:
        store.put(envelope(), writer=WRITER)
    with open_sealed_store(tmp_path / "victim", "ws_A", create=True) as victim_store:
        victim_store.put(envelope(99), writer=WRITER)
    path, victim = database(root), database(tmp_path / "victim")
    original_hash, victim_hash = digest(path), digest(victim)
    original_open = implementation._Connection.__init__
    backup = path.with_suffix(".preserved")

    def replace_at_open(self, selected):
        assert selected == path
        if replacement == "directory-link":
            path.parent.rename(path.parent.with_name("preserved-directory"))
            path.parent.symlink_to(victim.parent, target_is_directory=True)
        elif replacement.startswith("database-"):
            path.rename(backup)
            if replacement == "database-link":
                path.symlink_to(victim)
            elif replacement == "database-hardlink":
                os.link(victim, path)
            else:
                shutil.copyfile(victim, path)
                path.chmod(0o600)
        else:
            side = path.with_name(
                path.name + ("-journal" if replacement == "journal-hardlink" else replacement)
            )
            if replacement == "journal-hardlink":
                os.link(victim, side)
            else:
                side.symlink_to(victim)
        original_open(self, selected)

    monkeypatch.setattr(implementation._Connection, "__init__", replace_at_open)
    assert_code("UNSAFE_PATH", lambda: open_sealed_store(root, "ws_A"))
    assert digest(victim) == victim_hash
    if replacement.startswith("database-"):
        assert digest(backup) == original_hash
    elif replacement == "directory-link":
        assert digest(path.parent.with_name("preserved-directory") / path.name) == original_hash
    else:
        assert digest(path) == original_hash


def test_writable_directories_hardlinks_and_unsupported_platform_refuse(tmp_path, monkeypatch):
    with open_sealed_store(tmp_path, "ws_A", create=True):
        pass
    path = database(tmp_path)
    before = digest(path)
    path.parent.chmod(0o750)
    assert_code("UNSAFE_PATH", lambda: open_sealed_store(tmp_path, "ws_A"))
    path.parent.chmod(0o700)
    os.link(path, tmp_path / "hardlinked-victim")
    assert_code("UNSAFE_PATH", lambda: open_sealed_store(tmp_path, "ws_A"))
    (tmp_path / "hardlinked-victim").unlink()
    implementation._library.cache_clear()
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "win32")
        assert_code("UNSAFE_PATH", lambda: open_sealed_store(tmp_path, "ws_A"))
    implementation._library.cache_clear()
    assert digest(path) == before


def test_server_stamp_is_stable_across_retry(tmp_path):
    times = iter([STAMP, STAMP + timedelta(days=1)])
    with open_sealed_store(tmp_path, "ws_A", create=True, clock=lambda: next(times)) as store:
        first = store.put(envelope(), writer=WRITER)
        retry = store.put(envelope(), writer=replace(WRITER, trust="untrusted"))
        second = store.put(envelope(2), writer=WRITER)
    assert first.record == retry.record
    assert first.inserted and not retry.inserted
    assert retry.record.writer.trust == "trusted"
    assert second.record.written_at.startswith("2026-01-03")


def test_put_retry_conflict_and_nonce_collision_preserve_original(tmp_path):
    with open_sealed_store(tmp_path, "ws_A", create=True) as store:
        first = store.put(envelope(), writer=WRITER).record
        for changed, writer in (
            (replace(envelope(), ciphertext=b"synthetic-forbidden-body"), WRITER),
            (envelope(), replace(WRITER, user_id="other")),
            (envelope(), replace(WRITER, connection_id="other")),
            (replace(envelope(2), nonce=envelope().nonce), WRITER),
        ):
            assert_code(
                "CONFLICT", lambda changed=changed, writer=writer: store.put(changed, writer=writer)
            )
        assert store.put(envelope(), writer=WRITER).record == first
        assert store.list(kind="memory").records == (first,)
        # The namespace includes kind; this does not prove wrapping-key nonce safety.
        store.put(replace(envelope(2, "printed-recovery"), nonce=envelope().nonce), writer=WRITER)
    with open_sealed_store(tmp_path, "ws_A") as reopened:
        assert reopened.get(kind="memory", record_id=envelope().record_id) == first


def _append_process(root, numbers):
    with open_sealed_store(Path(root), "ws_A") as store:
        for number in numbers:
            store.put(envelope(number), writer=WRITER)


def test_concurrent_append_and_finite_cursor_have_no_loss(tmp_path):
    with open_sealed_store(tmp_path, "ws_A", create=True) as store:
        originals = tuple(store.put(envelope(n), writer=WRITER).record for n in range(1, 6))
        store.put(envelope(6, "printed-recovery"), writer=WRITER)
        first = store.list(kind="memory", limit=2)
        context = multiprocessing.get_context("spawn")
        processes = [
            context.Process(target=_append_process, args=(str(tmp_path), group))
            for group in ((10, 11, 12), (20, 21, 22))
        ]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join()
                assert process.exitcode == 0
        finally:
            for process in processes:
                if process.pid is not None:
                    process.join()
                    process.close()
        observed = list(first.records)
        page = first
        while page.next_after is not None:
            page = store.list(kind="memory", after=page.next_after, through=first.through, limit=2)
            assert page.through == first.through
            observed.extend(page.records)
        assert tuple(observed) == originals
        fresh = store.list(kind="memory")
        ids = [record.envelope.record_id for record in fresh.records]
        expected = [envelope(n).record_id for n in (1, 2, 3, 4, 5, 10, 11, 12, 20, 21, 22)]
        assert sorted(ids) == sorted(expected)
        assert len(ids) == len(set(ids))
        assert fresh.through > first.through


@pytest.mark.parametrize(
    "arguments",
    [
        {"after": -1},
        {"after": True},
        {"after": 1.0},
        {"after": "0"},
        {"after": 1 << 63},
        {"through": -1},
        {"through": False},
        {"through": 1.0},
        {"through": "1"},
        {"through": 1 << 63},
        {"after": 1, "through": 0},
        {"through": 2},
        {"after": 2},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": "1"},
        {"limit": 1.0},
    ],
)
def test_invalid_cursor_is_explicit_refusal_without_refresh_or_clamp(tmp_path, arguments):
    with open_sealed_store(tmp_path, "ws_A", create=True) as store:
        record = store.put(envelope(), writer=WRITER).record
        assert_code("INVALID_RECORD", lambda: store.list(kind="memory", **arguments))
        assert store.list(kind="memory").records == (record,)


def test_empty_cursor_bounds_are_finite_and_retained(tmp_path):
    with open_sealed_store(tmp_path, "ws_A", create=True) as store:
        empty = store.list(kind="memory")
        assert empty.through == 0 and empty.records == () and empty.next_after is None
        record = store.put(envelope(), writer=WRITER).record
        retained = store.list(kind="memory", through=empty.through)
        assert retained == empty
        exhausted = store.list(kind="memory", after=record.sequence, through=record.sequence)
        assert exhausted.records == () and exhausted.next_after is None
        filtered = store.list(kind="printed-recovery")
        assert filtered.records == () and filtered.through == record.sequence


def test_interrupted_insert_and_initialization_keep_committed_records(tmp_path, monkeypatch):
    root = tmp_path / "existing"
    with open_sealed_store(root, "ws_A", create=True) as store:
        original = store.put(envelope(), writer=WRITER).record
    before = digest(database(root))
    execute = implementation._Connection.execute

    def fail_after_insert(self, sql, parameters=()):
        rows = execute(self, sql, parameters)
        if sql.startswith("INSERT INTO sealed_objects"):
            raise implementation._SQLiteFailure(10)
        return rows

    with monkeypatch.context() as patch:
        patch.setattr(implementation._Connection, "execute", fail_after_insert)
        with open_sealed_store(root, "ws_A") as store:
            assert_code("STORAGE_UNAVAILABLE", lambda: store.put(envelope(2), writer=WRITER))
    with open_sealed_store(root, "ws_A") as reopened:
        assert reopened.get(kind="memory", record_id=envelope().record_id) == original
        assert reopened.get(kind="memory", record_id=envelope(2).record_id) is None
    assert digest(database(root)) == before

    def fail_after_schema(self, sql, parameters=()):
        rows = execute(self, sql, parameters)
        if sql == implementation._OBJECTS_SQL:
            raise implementation._SQLiteFailure(10)
        return rows

    failed = tmp_path / "failed-init"
    with monkeypatch.context() as patch:
        patch.setattr(implementation._Connection, "execute", fail_after_schema)
        assert_code("STORAGE_UNAVAILABLE", lambda: open_sealed_store(failed, "ws_A", create=True))
    failed_hash = digest(database(failed))
    assert_code("UNSUPPORTED_SCHEMA", lambda: open_sealed_store(failed, "ws_A", create=True))
    assert digest(database(failed)) == failed_hash
    assert digest(database(root)) == before


def test_store_works_without_palace_keys_network_or_embeddings(tmp_path, monkeypatch):
    real_import = builtins.__import__
    forbidden = []

    def guarded_import(name, *args, **kwargs):
        if name.startswith(
            (
                "mempalace",
                "commonplace.policy",
                "commonplace.extensions",
                "commonplace.workspace_encryption",
                "cryptography",
            )
        ):
            forbidden.append(name)
            raise AssertionError("forbidden runtime dependency")
        return real_import(name, *args, **kwargs)

    def network_forbidden(*args, **kwargs):
        forbidden.append("network")
        raise AssertionError("network unavailable")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(socket, "socket", network_forbidden)
    with open_sealed_store(tmp_path, "ws_A", create=True) as store:
        saved = store.put(envelope(), writer=WRITER).record
        assert store.get(kind="memory", record_id=envelope().record_id) == saved
        assert store.list(kind="memory").records == (saved,)
    assert forbidden == []
    assert sorted(path.name for path in tmp_path.iterdir()) == ["sealed"]
    assert sorted(path.name for path in database(tmp_path).parent.iterdir()) == ["records.sqlite3"]


def _hold_sqlite(path, connection):
    with closing(sqlite3.connect(path)) as database_connection:
        database_connection.execute("BEGIN EXCLUSIVE")
        connection.send("locked")
        assert connection.recv() == "release"
        database_connection.rollback()
    connection.close()


def test_missing_corrupt_unknown_and_busy_store_fail_closed(tmp_path):
    missing = tmp_path / "missing"
    assert_code("NOT_INITIALIZED", lambda: open_sealed_store(missing, "ws_A"))
    assert not missing.exists()
    with open_sealed_store(tmp_path, "ws_A", create=True) as store:
        original = store.put(envelope(), writer=WRITER).record
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_hold_sqlite, args=(str(database(tmp_path)), child))
        process.start()
        child.close()
        try:
            assert parent.recv() == "locked"
            assert_code("STORAGE_UNAVAILABLE", lambda: store.list(kind="memory"))
            assert_code(
                "STORAGE_UNAVAILABLE",
                lambda: store.get(kind="memory", record_id=envelope().record_id),
            )
        finally:
            parent.send("release")
            parent.close()
            process.join()
            assert process.exitcode == 0
            process.close()
        assert store.get(kind="memory", record_id=envelope().record_id) == original
    with closing(sqlite3.connect(database(tmp_path))) as connection:
        connection.execute("CREATE INDEX unrecognized ON sealed_objects(key_id)")
        connection.commit()
    unknown_hash = digest(database(tmp_path))
    assert_code("UNSUPPORTED_SCHEMA", lambda: open_sealed_store(tmp_path, "ws_A", create=True))
    assert digest(database(tmp_path)) == unknown_hash
    database(tmp_path).write_bytes(b"synthetic-forbidden-body: corrupt store")
    corrupt_hash = digest(database(tmp_path))
    assert_code("UNSUPPORTED_SCHEMA", lambda: open_sealed_store(tmp_path, "ws_A", create=True))
    assert digest(database(tmp_path)) == corrupt_hash


def test_context_close_and_naive_clock_refuse_without_appending(tmp_path):
    with open_sealed_store(
        tmp_path, "ws_A", create=True, clock=lambda: datetime(2026, 1, 1)
    ) as store:
        assert_code("STORAGE_UNAVAILABLE", lambda: store.put(envelope(), writer=WRITER))
        assert store.list(kind="memory").records == ()
    store.close()
    assert_code("STORAGE_UNAVAILABLE", lambda: store.list(kind="memory"))
    with open_sealed_store(tmp_path, "ws_A") as reopened:
        assert reopened.get(kind="memory", record_id=envelope().record_id) is None
