"""Shared teardown releases clients and lock-preserving header probes."""

import os
import sqlite3

import chromadb
import pytest
from chromadb.api.shared_system_client import SharedSystemClient

from mempalace.backends import _magic


@pytest.fixture(scope="module")
def resources():
    clients = []
    probes = []
    yield clients, probes
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
    assert not _magic._retained_conns
    assert not _magic._positive


def test_discarded_and_shared_clients_are_closed(tmp_path, resources):
    clients, probes = resources
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
