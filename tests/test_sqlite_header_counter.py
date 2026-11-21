"""The capacity fingerprint reads counters only from opened regular files."""

import builtins
import errno
import os
import socket
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest

from mempalace.backends import chroma

HEADER = b"SQLite format 3\x00" + b"\x00" * 8 + b"\x01\x02\x03\x04"


@contextmanager
def _fifo_with_header(path: Path) -> Iterator[int]:
    os.mkfifo(path)
    descriptor = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        assert os.write(descriptor, HEADER) == len(HEADER)
        yield descriptor
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("length", [0, 23, 24, 27, 28, 29, 64])
def test_regular_header_counter(tmp_path: Path, length: int) -> None:
    content = (HEADER + b"x" * 64)[:length]
    (tmp_path / "chroma.sqlite3").write_bytes(content)
    expected = HEADER[24:28] if length >= 28 else None
    assert chroma._db_change_counter(str(tmp_path)) == expected


def test_missing_directory_and_invalid_paths(tmp_path: Path) -> None:
    assert chroma._db_change_counter(str(tmp_path)) is None
    (tmp_path / "chroma.sqlite3").mkdir()
    assert chroma._db_change_counter(str(tmp_path)) is None
    assert chroma._db_change_counter("\x00") is None


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="requires filesystem symlinks")
def test_regular_symlink_keeps_its_counter(tmp_path: Path) -> None:
    target = tmp_path / "database"
    target.write_bytes(HEADER)
    (tmp_path / "chroma.sqlite3").symlink_to(target)
    assert chroma._db_change_counter(str(tmp_path)) == HEADER[24:28]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires filesystem FIFOs")
@pytest.mark.parametrize("symlink", [False, True])
def test_fifo_counter_is_refused_without_consuming_bytes(tmp_path: Path, symlink: bool) -> None:
    database = tmp_path / "chroma.sqlite3"
    target = tmp_path / "pipe" if symlink else database
    with _fifo_with_header(target) as descriptor:
        if symlink:
            database.symlink_to(target)
        assert chroma._db_change_counter(str(tmp_path)) is None
        assert os.read(descriptor, len(HEADER)) == HEADER


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires filesystem FIFOs")
def test_regular_path_replaced_by_fifo_at_open(tmp_path: Path) -> None:
    database = tmp_path / "chroma.sqlite3"
    database.write_bytes(HEADER)
    actual_open = os.open
    actual_builtin_open = builtins.open
    swaps: list[str] = []
    with _fifo_with_header(tmp_path / "replacement") as descriptor:

        def swap() -> None:
            os.replace(tmp_path / "replacement", database)
            swaps.append("replaced")

        def open_descriptor(path: str, flags: int) -> int:
            assert path == str(database)
            swap()
            return actual_open(path, flags)

        def open_stream(path: str, mode: str) -> object:
            assert path == str(database)
            swap()
            return actual_builtin_open(path, mode)

        with (
            patch.object(os, "open", side_effect=open_descriptor),
            patch.object(builtins, "open", side_effect=open_stream),
        ):
            result = chroma._db_change_counter(str(tmp_path))
        assert swaps == ["replaced"]
        assert result is None
        assert os.read(descriptor, len(HEADER)) == HEADER


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="requires filesystem sockets")
def test_socket_counter_is_refused(tmp_path: Path) -> None:
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(tmp_path / "chroma.sqlite3"))
        assert chroma._db_change_counter(str(tmp_path)) is None


@pytest.mark.parametrize("operation", ["fstat", "read"])
def test_descriptor_is_closed_after_io_failure(tmp_path: Path, operation: str) -> None:
    (tmp_path / "chroma.sqlite3").write_bytes(HEADER)
    actual_open = os.open
    descriptor: list[int] = []

    def record_open(path: str, flags: int) -> int:
        opened = actual_open(path, flags)
        descriptor.append(opened)
        return opened

    with (
        patch.object(os, "open", side_effect=record_open),
        patch.object(os, operation, side_effect=OSError(errno.EIO, "read failure")),
    ):
        result = chroma._db_change_counter(str(tmp_path))
    assert result is None
    assert len(descriptor) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(descriptor[0])
    assert closed.value.errno == errno.EBADF


def test_open_failure_is_not_retried_as_a_blocking_open(tmp_path: Path) -> None:
    (tmp_path / "chroma.sqlite3").write_bytes(HEADER)
    with patch.object(os, "open", side_effect=BlockingIOError(errno.EAGAIN, "lease")) as opening:
        result = chroma._db_change_counter(str(tmp_path))
    assert result is None
    opening.assert_called_once()
    assert opening.call_args.args[1] & getattr(os, "O_NONBLOCK", 0) == getattr(os, "O_NONBLOCK", 0)


def test_sqlite_commit_changes_counter_with_identical_stat_signature(tmp_path: Path) -> None:
    database = tmp_path / "chroma.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('first')")
    before = chroma._capacity_fingerprint(str(tmp_path), None)
    stamp = database.stat()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE sample SET value = 'other'")
    os.utime(database, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    after = chroma._capacity_fingerprint(str(tmp_path), None)
    assert before[:3] == after[:3]
    assert before[3] is not None
    assert after[3] != before[3]
