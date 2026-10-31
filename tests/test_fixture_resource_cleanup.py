"""Shared teardown releases clients and lock-preserving header probes."""

import os
import sqlite3

import chromadb
import pytest
from chromadb.api.shared_system_client import SharedSystemClient

from commonplace.control_db import ControlDB
from mempalace.backends import _magic
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
    assert not _magic._retained_conns
    assert not _magic._positive


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
    probes.extend(_magic._retained_conns.values())
    databases.append(ControlDB(str(tmp_path / "control.sqlite3")))
    backend = SQLiteExactBackend()
    backend.get_collection(str(tmp_path / "exact"), "cleanup_test", create=True)
    backends.append(backend)
