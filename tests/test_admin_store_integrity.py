"""Read-only metadata cannot revive damaged or missing stores from sidecar data."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_admin_api import cookie
from tests.test_instance_admin_read import (
    metadata_reads_without_background_writes,  # noqa: F401
    read,
    seed_board,
)


@pytest.fixture
def foreign(hub):
    user = hub.db.create_user("other@example.test")
    return hub.db.create_workspace("Other workspace", user)


def test_valid_live_wal_metadata_read_preserves_main_file(hub, foreign):
    headers = cookie(hub)
    hub.workspace = foreign
    path = seed_board(hub, foreign) / "boards.sqlite3"
    with closing(sqlite3.connect(path, isolation_level=None)) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE boards SET name='Current metadata' WHERE id=1")
        before = path.read_bytes()
        schema = writer.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
        code, payload, _ = read(hub, "spaces", headers)
        assert code == 200 and payload["items"][0]["name"] == "Current metadata"
        assert read(hub, "workspace_settings", headers)[0] == 200
        assert path.read_bytes() == before
        assert (
            writer.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall() == schema
        )


@pytest.mark.parametrize("truncate", [False, True])
def test_damaged_main_store_is_unavailable_even_with_live_wal(hub, foreign, truncate):
    headers = cookie(hub)
    hub.workspace = foreign
    path = seed_board(hub, foreign) / "boards.sqlite3"
    # Keep a writer open so committed page one remains available in the WAL.
    with closing(sqlite3.connect(path, isolation_level=None)) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA user_version=37")
        writer.execute("UPDATE boards SET name='Old metadata' WHERE id=1")
        assert Path(str(path) + "-wal").stat().st_size > 32
        if truncate:
            path.write_bytes(b"damaged source")
        else:
            with path.open("r+b") as file:
                file.write(b"X" * 16)
        before = path.read_bytes()
        files = {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()}
        for kind in ("spaces", "workspace_settings"):
            assert read(hub, kind, headers)[:2] == (503, {"error": "source_unavailable"})
        assert path.read_bytes() == before
        assert {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()} == files


def test_absent_store_read_never_opens_or_creates_a_domain_store(hub, foreign, monkeypatch):
    from commonplace.boards import service, store
    from mempalace import sqlite_crypto

    headers = cookie(hub)
    hub.workspace = foreign
    path = Path(hub.supervisor.workspace_dir(foreign))
    assert not path.exists()

    def forbidden(*args, **kwargs):
        raise AssertionError("metadata reads must not initialize a domain store")

    monkeypatch.setattr(service.BoardService, "__init__", forbidden)
    monkeypatch.setattr(store.BoardStore, "__init__", forbidden)
    monkeypatch.setattr(sqlite_crypto, "connect", forbidden)
    for kind in ("spaces", "workspace_settings"):
        code, payload, _ = read(hub, kind, headers)
        assert code == 200 and payload["state"] == "unknown"
    assert not path.exists()
