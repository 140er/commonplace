"""Shared teardown releases clients and lock-preserving header probes."""

import os
import sqlite3
import subprocess
import sys

import chromadb
import pytest
from chromadb.api.shared_system_client import SharedSystemClient

from commonplace.control_db import ControlDB
from mempalace.backends import _inproc_sqlite, _magic
from mempalace.backends.sqlite_exact import SQLiteExactBackend


@pytest.fixture(scope="module")
def resources():
    clients = []
    probes = []
    databases = []
    backends = []
    yield clients, probes, databases, backends
    # Module teardown runs after the function-scoped resource cleanup.
    assert clients
    if os.name == "posix":
        assert probes
    for client in clients:
        assert client._closed
        assert client._identifier not in SharedSystemClient._identifier_to_system
        stat = os.stat(os.path.join(client._identifier, "chroma.sqlite3"))
        key = (stat.st_dev, stat.st_ino)
        assert key not in _magic._retained_conns
        assert key not in _magic._positive
        anchor_path = _inproc_sqlite._key(os.path.join(client._identifier, "chroma.sqlite3"))
        assert anchor_path not in _inproc_sqlite._anchors
    for connection in probes:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    for database in databases:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            database._conn.execute("SELECT 1")
    for backend in backends:
        assert not backend._clients
        assert not backend._read_only_clients
        assert not backend._closed


def test_discarded_and_shared_clients_are_closed(tmp_path, resources):
    clients, probes, databases, backends = resources
    path = str(tmp_path / "palace")
    for _ in range(2):
        client = chromadb.PersistentClient(path=path)
        client.get_or_create_collection("cleanup_test")
        clients.append(client)
    # A reference discarded by the caller still contributes to Chroma's
    # shared System count and must be explicitly closed at teardown.
    chromadb.PersistentClient(path=path)
    assert _magic.has_sqlite_magic(str(tmp_path / "palace" / "chroma.sqlite3"))
    key_stat = os.stat(tmp_path / "palace" / "chroma.sqlite3")
    key = (key_stat.st_dev, key_stat.st_ino)
    connection = _magic._retained_conns.get(key)
    if connection is not None:

        class OrderedProbe:
            def close(self):
                assert all(client._closed for client in clients), "clients must close before probes"
                assert anchor_path in _inproc_sqlite._anchors, "anchors must close after probes"
                connection.close()

        _magic._retained_conns[key] = OrderedProbe()
        probes.append(connection)

    anchor_path = _inproc_sqlite._key(str(tmp_path / "palace" / "chroma.sqlite3"))

    class OrderedAnchor:
        def discard(self):
            assert all(client._closed for client in clients), "clients must close before anchors"
            assert key not in _magic._retained_conns, "probes must close before anchors"

    _inproc_sqlite._anchors[anchor_path] = OrderedAnchor()
    databases.append(ControlDB(str(tmp_path / "control.sqlite3")))
    backend = SQLiteExactBackend()
    backend.get_collection(str(tmp_path / "exact"), "cleanup_test", create=True)
    backends.append(backend)


def test_one_client_close_failure_does_not_skip_other_clients(monkeypatch):
    from chromadb.api.client import Client
    from conftest import _close_test_storage_clients

    closed = []
    monkeypatch.setattr(Client, "__init__", lambda self: None)

    def close(client):
        closed.append(client)
        if len(closed) == 1:
            raise RuntimeError("close failed")

    monkeypatch.setattr(Client, "close", close)
    # Exercise the fixture's actual callback registration and teardown.
    with pytest.MonkeyPatch.context() as inner_patch:
        cleanup = _close_test_storage_clients.__wrapped__(inner_patch)
        next(cleanup)
        first, second = Client(), Client()
        with pytest.raises(RuntimeError, match="close failed"):
            next(cleanup)
    assert closed == [second, first]


_LOCK_PROBE = """
import fcntl, sys
with open(sys.argv[1], "r+b") as handle:
    try:
        fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB, 510, 0x40000002)
    except OSError:
        print("held")
    else:
        print("released")
"""


def _assert_shared_lock(db_path):
    result = subprocess.run(
        [sys.executable, "-c", _LOCK_PROBE, str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "held"


@pytest.fixture(scope="module")
def module_writer(tmp_path_factory):
    path = tmp_path_factory.mktemp("module_writer")
    db_path = path / "chroma.sqlite3"
    with sqlite3.connect(db_path) as database:
        database.execute("PRAGMA journal_mode=WAL")
    database.close()
    client = chromadb.PersistentClient(path=str(path))
    collection = client.get_or_create_collection("module_writer")
    collection.add(ids=["one"], embeddings=[[1.0, 0.0]])
    try:
        yield db_path
        # This assertion runs after both function-scoped teardowns, including
        # when either test is selected on its own.
        _assert_shared_lock(db_path)
        stat = db_path.stat()
        probe = _magic._retained_conns[(stat.st_dev, stat.st_ino)]
        probe.execute("SELECT 1")
    finally:
        client.close()
        stat = db_path.stat()
        probe = _magic._retained_conns.pop((stat.st_dev, stat.st_ino), None)
        if probe is not None:
            probe.close()
        _magic.forget(str(db_path))
        _inproc_sqlite.release(str(db_path))


@pytest.mark.skipif(os.name != "posix", reason="POSIX shared locks")
def test_module_writer_probe_survives_first_cleanup(module_writer):
    assert _magic.has_sqlite_magic(str(module_writer))
    _assert_shared_lock(module_writer)


@pytest.mark.skipif(os.name != "posix", reason="POSIX shared locks")
def test_module_writer_probe_survives_second_cleanup(module_writer):
    _assert_shared_lock(module_writer)
    assert _magic.has_sqlite_magic(str(module_writer))


@pytest.fixture(
    scope="module",
    params=[
        ("sqlite_exact", False),
        ("sqlite_exact", True),
        ("rust_exact", False),
        ("rust_exact", True),
    ],
)
def module_exact_palace(request, tmp_path_factory):
    from mempalace.backends.rust_exact import RustExactBackend

    name, read_only = request.param
    backend = RustExactBackend() if name == "rust_exact" else SQLiteExactBackend()
    path = str(tmp_path_factory.mktemp(name))
    collection = backend.get_collection(path, "shared", create=True)
    collection.add(ids=["one"], documents=["shared document"], embeddings=[[1.0, 0.0]])
    if read_only:
        backend.close_palace(path)
        collection = backend.get_collection(path, "shared", options={"read_only": True})
    try:
        yield backend, path, collection, read_only
        # Also covers selecting either test alone: its teardown must leave
        # the module fixture's collection usable.
        assert collection.get(ids=["one"]).ids == ["one"]
    finally:
        backend.close()


def test_module_exact_palace_survives_first_read(module_exact_palace):
    backend, path, _, read_only = module_exact_palace
    collection = backend.get_collection(path, "shared", options={"read_only": read_only})
    assert collection.get(ids=["one"]).ids == ["one"]


def test_module_exact_palace_survives_second_read(module_exact_palace):
    backend, path, original, read_only = module_exact_palace
    assert original.get(ids=["one"]).ids == ["one"]
    assert backend.get_collection(path, "shared", options={"read_only": read_only}).get(
        ids=["one"]
    ).ids == ["one"]
