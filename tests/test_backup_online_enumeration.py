"""Online enumeration tolerates only SQLite's disappearing sidecars."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from commonplace.backup import BackupError, _inventory
from commonplace.backup_online import _Cut, _NoGateway
from commonplace.backup_pause import Knobs


def _cut(root):
    return _Cut(root, root / "unused", _NoGateway(), Knobs(), None, None, None)


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_sqlite_sidecar_disappears_between_listing_and_stat(tmp_path, monkeypatch, suffix):
    database = tmp_path / "runner.sqlite3"
    sidecar = Path(str(database) + suffix)
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("PRAGMA journal_mode=" + ("DELETE" if suffix == "-journal" else "WAL"))
        conn.execute("CREATE TABLE memories (content TEXT)")
        conn.execute("INSERT INTO memories VALUES (?)", ("Every word, exactly.\n",))
        if suffix != "-journal":
            conn.commit()
        assert sidecar.exists()
        lstat = Path.lstat
        interleaved = []

        def finish_write_before_stat(path, *args, **kwargs):
            if path == sidecar and not interleaved:
                interleaved.append(path)
                # Enumeration has already listed the name. A real SQLite
                # commit/close removes it before the inventory can lstat it.
                if suffix == "-journal":
                    conn.commit()
                else:
                    conn.close()
            return lstat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", finish_write_before_stat)
        error = None
        try:
            tree = _cut(tmp_path)._walk()
        except FileNotFoundError as exc:
            error = exc
        assert error is None, f"online enumeration raced SQLite sidecar cleanup: {error}"
        assert interleaved == [sidecar]
        assert tree.databases == [database]
        assert set(tree.sizes) == {database}
        assert not tree.other and not tree.worker
    with closing(sqlite3.connect(database)) as restored:
        assert restored.execute("SELECT content FROM memories").fetchall() == [
            ("Every word, exactly.\n",)
        ]


def test_online_enumeration_still_refuses_missing_user_file(tmp_path, monkeypatch):
    content = tmp_path / "verbatim.txt"
    content.write_text("Keep every word.")
    lstat = Path.lstat

    def remove_before_stat(path, *args, **kwargs):
        if path == content:
            content.unlink(missing_ok=True)
        return lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", remove_before_stat)
    with pytest.raises(FileNotFoundError):
        _cut(tmp_path)._walk()


def test_online_enumeration_still_refuses_sidecar_symlinks(tmp_path):
    (tmp_path / "runner.sqlite3-journal").symlink_to(tmp_path / "outside")
    with pytest.raises(BackupError, match="refusing symlink"):
        _cut(tmp_path)._walk()


def test_offline_inventory_still_refuses_missing_sidecar(tmp_path, monkeypatch):
    sidecar = tmp_path / "runner.sqlite3-journal"
    sidecar.touch()
    lstat = Path.lstat

    def remove_before_stat(path, *args, **kwargs):
        if path == sidecar:
            sidecar.unlink(missing_ok=True)
        return lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", remove_before_stat)
    with pytest.raises(FileNotFoundError):
        _inventory(tmp_path)
