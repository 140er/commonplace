"""Plaintext snapshots may take longer than 30 seconds while making progress."""

import itertools
import sqlite3
from contextlib import closing
from functools import partial

import pytest

import commonplace.backup as backup_module
from commonplace.backup import BackupError, _snapshot


def test_large_snapshot_is_not_cut_off_by_total_time(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    target = tmp_path / "snapshot.sqlite3"
    row_count = 4000
    with closing(sqlite3.connect(source)) as conn:
        # Small pages give this small database several 4096-page backup steps.
        conn.execute("PRAGMA page_size = 512")
        conn.execute("CREATE TABLE memory (content BLOB)")
        conn.executemany("INSERT INTO memory VALUES (zeroblob(2048))", [()] * row_count)
        conn.commit()
        assert conn.execute("PRAGMA page_count").fetchone()[0] > 4 * 4096

    clock = itertools.count(0, 10)
    monkeypatch.setattr(backup_module.time, "monotonic", lambda: next(clock))
    ok = True
    try:
        _snapshot(source, target)
    except BackupError:
        ok = False
    assert ok  # RED

    with closing(sqlite3.connect(target)) as conn:
        assert conn.execute("SELECT count(*) FROM memory").fetchone() == (row_count,)


def test_stalled_snapshot_still_times_out(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("CREATE TABLE memory (content BLOB)")

    class StalledConnection(sqlite3.Connection):
        def backup(self, target, *, pages, progress, sleep):
            for _ in range(8):
                progress(sqlite3.SQLITE_BUSY, 10, 10)
            pytest.fail("stalled snapshot did not time out")

    clock = itertools.count(0, 10)
    monkeypatch.setattr(backup_module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        backup_module.sqlite3, "connect", partial(sqlite3.connect, factory=StalledConnection)
    )
    with pytest.raises(BackupError, match="database backup timed out: source.sqlite3"):
        _snapshot(source, tmp_path / "snapshot.sqlite3")
