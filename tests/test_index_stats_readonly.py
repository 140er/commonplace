"""Operational figures must never activate Chroma or a worker."""

import builtins
import io
import json
import os
import pickle
import sqlite3
from pathlib import Path

import chromadb
import pytest

from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_admin_api import cookie, member
from tests.test_admin_instance_api import instance


@pytest.fixture
def disk(hub):
    palace = hub.supervisor.workspace_dir(hub.workspace) / "palace"
    palace.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(palace))
    collection = client.create_collection(
        "mempalace_drawers",
        embedding_function=None,
        configuration={"hnsw": {"sync_threshold": 100, "batch_size": 100}},
    )
    collection.add(
        ids=[str(i) for i in range(101)],
        embeddings=[[1.0, 0.0, 0.0] for _ in range(101)],
        documents=["synthetic" for _ in range(101)],
    )
    conn = sqlite3.connect(palace / "chroma.sqlite3")
    segment = conn.execute("SELECT id FROM segments WHERE scope='VECTOR'").fetchone()[0]
    conn.close()
    return palace, palace / segment / "header.bin"


def status_response(hub, headers):
    code, raw = hub.raw(
        None,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "mempalace_status", "arguments": {}},
        },
        headers=headers,
    )
    assert code == 200
    return json.loads(raw)


def status(hub, headers):
    response = status_response(hub, headers)
    assert "result" in response, response
    return json.loads(response["result"]["content"][0]["text"])


def no_load(monkeypatch, hub):
    def forbidden(*args, **kwargs):
        raise AssertionError("index/worker operation forbidden")

    monkeypatch.setattr(chromadb, "PersistentClient", forbidden)
    monkeypatch.setattr(chromadb, "Client", forbidden)
    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)
    monkeypatch.setattr(hub.supervisor.launcher, "start", forbidden)
    from chromadb.api.client import Client

    monkeypatch.setattr(Client, "get_collection", forbidden)
    monkeypatch.setattr(Client, "__init__", forbidden)
    from chromadb.api.models.Collection import Collection

    for method in ("get", "query", "count", "peek", "add", "upsert", "delete"):
        monkeypatch.setattr(Collection, method, forbidden)
    original_open = os.open
    original_read = os.read
    header_fds = set()

    def open_fd(path, *args, **kwargs):
        if not isinstance(path, int):
            assert Path(path).name not in {
                "data_level0.bin",
                "link_lists.bin",
                "index_metadata.pickle",
            }
        fd = original_open(path, *args, **kwargs)
        if Path(path).name == "header.bin":
            header_fds.add(fd)
        return fd

    def read_fd(fd, length):
        if fd in header_fds:
            assert length <= 100
        return original_read(fd, length)

    monkeypatch.setattr(os, "open", open_fd)
    monkeypatch.setattr(os, "read", read_fd)
    for module in (builtins, io):
        original = module.open

        def guarded(file, *args, _open=original, **kwargs):
            assert Path(file).name not in {
                "data_level0.bin",
                "link_lists.bin",
                "index_metadata.pickle",
            }
            return _open(file, *args, **kwargs)

        monkeypatch.setattr(module, "open", guarded)


def test_exact_figures_no_load(hub, disk, monkeypatch):
    palace, header = disk
    # The fixture is a real Chroma 1.5.9 index, not a fabricated header layout.
    assert header.stat().st_size == 100
    before = {
        p.relative_to(palace): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in palace.rglob("*")
        if p.is_file()
    }
    from commonplace.handoff import overview

    monkeypatch.setattr(
        overview, "summary", lambda *a, **kw: {"total_drawers": 101, "wings": {}, "rooms": {}}
    )
    no_load(monkeypatch, hub)
    headers = cookie(hub)
    code, console, _ = instance(hub, "index-stats", headers)
    assert code == 200
    figures = console["workspaces"][0]
    assert figures == status(hub, headers).get("index_stats")
    assert figures == {
        "workspace_id": hub.workspace,
        "drawers": {"value": 101, "reason": None},
        "embedding_model": {"value": None, "reason": "model_not_in_sqlite"},
        "dimension": {"value": 3, "reason": None},
        "vector_count": {"value": 101, "reason": None},
        "hnsw_capacity": {"value": 128, "reason": None},
        "tombstones": {"value": None, "reason": "unsynchronized_sources"},
    }
    assert not console["has_more"] and console["next_cursor"] is None
    assert before == {
        p.relative_to(palace): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in palace.rglob("*")
        if p.is_file()
    }
    assert not hub.supervisor.launcher.started


def test_nonadmins_unchanged(hub, monkeypatch):
    # Fixed existing worker result makes exact non-admin shape comparable on
    # the pinned base and head without any real worker/index setup.
    from commonplace.handoff import overview

    expected = {"total_drawers": 7, "wings": {"notes": 7}, "rooms": {"day": 7}}
    monkeypatch.setattr(overview, "summary", lambda *a, **kw: dict(expected))
    uid = member(hub)
    token = {
        "Authorization": "Bearer "
        + hub.token("member", user=uid, scopes=["memory:read"], read_wings=["notes"])
    }
    # The identical call on the pinned base returns this exact RPC error:
    # the restricted member token does not expose mempalace_status.
    response = status_response(hub, token)
    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32601, "message": "Unknown tool: mempalace_status"},
    }
    assert "index_stats" not in json.dumps(response)
    headers = [cookie(hub, "member@example.com")]
    hub.db._conn.execute("UPDATE users SET is_admin=0 WHERE id=?", (hub.admin,))
    headers.append(cookie(hub))
    for auth in headers:
        assert status(hub, auth) == expected
        code, data, _ = instance(hub, "index-stats", auth)
        assert (code, data) == (404, {"error": "not found"})
    assert instance(hub, "index-stats", token)[:2] == (404, {"error": "not found"})


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("missing", "header_unavailable"),
        ("short", "invalid_header_length"),
        ("long", "invalid_header_length"),
        ("version", "unsupported_header_format"),
        ("counts", "invalid_header_counts"),
    ],
)
def test_bad_header(hub, disk, monkeypatch, mutation, reason):
    _, header = disk
    raw = bytearray(header.read_bytes())
    if mutation == "missing":
        header.unlink()
    elif mutation == "short":
        header.write_bytes(raw[:28])
    elif mutation == "long":
        header.write_bytes(raw + b"x")
    elif mutation == "version":
        raw[:4] = (99).to_bytes(4, "little")
        header.write_bytes(raw)
    else:
        raw[20:28] = (999999).to_bytes(8, "little")
        header.write_bytes(raw)
    no_load(monkeypatch, hub)
    code, data, _ = instance(hub, "index-stats", cookie(hub))
    assert code == 200
    assert data["workspaces"][0]["hnsw_capacity"] == {"value": None, "reason": reason}
    assert data["workspaces"][0]["drawers"]["value"] == 101


def test_missing_corrupt_and_backend(hub, monkeypatch):
    headers = cookie(hub)
    code, data, _ = instance(hub, "index-stats", headers)
    assert code == 200
    assert {v["reason"] for k, v in data["workspaces"][0].items() if k != "workspace_id"} == {
        "missing_database"
    }
    palace = hub.supervisor.workspace_dir(hub.workspace) / "palace"
    palace.mkdir(parents=True, exist_ok=True)
    (palace / "chroma.sqlite3").write_bytes(b"broken")
    no_load(monkeypatch, hub)
    assert instance(hub, "index-stats", headers)[1]["workspaces"][0]["dimension"] == {
        "value": None,
        "reason": "invalid_database",
    }
    from commonplace.index_stats import read

    assert all(
        x == {"value": None, "reason": "unsupported_backend"}
        for x in read(palace, backend="compact").values()
    )


def test_workspace_pagination(hub, monkeypatch):
    from commonplace.web import admin

    monkeypatch.setattr(admin, "PAGE_SIZE", 1)
    hub.db.create_workspace("other", hub.admin)
    headers = cookie(hub)
    code, first, _ = instance(hub, "index-stats", headers)
    assert code == 200 and first["has_more"]
    code, second, _ = instance(hub, "index-stats", headers, "?cursor=" + first["next_cursor"])
    assert code == 200 and not second["has_more"]
    assert first["workspaces"][0]["workspace_id"] != second["workspaces"][0]["workspace_id"]
    assert instance(hub, "index-stats", headers, "?path=forbidden")[0] == 400


def test_read_timeout_and_no_file_work_under_control_snapshot(hub, disk, monkeypatch):
    from contextlib import contextmanager

    from commonplace.web import admin

    opened = []
    real = admin.read_control

    @contextmanager
    def control(*args):
        opened.append(True)
        try:
            with real(*args) as conn:
                yield conn
        finally:
            opened.pop()

    monkeypatch.setattr(admin, "read_control", control)
    headers = cookie(hub)
    code, data, _ = instance(hub, "index-stats", headers)
    assert code == 200
    from commonplace import index_stats

    original = index_stats._header

    def header(*args):
        assert not opened, "Control snapshot must close before workspace file work"
        return original(*args)

    monkeypatch.setattr(index_stats, "_header", header)
    assert instance(hub, "index-stats", headers)[0] == 200
    times = iter([0.0, 1.0, 1.0])
    assert all(
        v == {"value": None, "reason": "read_timeout"}
        for v in index_stats.read(disk[0], clock=lambda: next(times)).values()
    )


def test_oversized_header_never_read(hub, disk, monkeypatch):
    _, header = disk
    header.write_bytes(b"x" * 101)
    original = os.read

    def guarded(fd, length):
        if os.fstat(fd).st_size == 101:
            raise AssertionError("Oversized header must be refused before read")
        return original(fd, length)

    monkeypatch.setattr(os, "read", guarded)
    code, data, _ = instance(hub, "index-stats", cookie(hub))
    assert code == 200
    assert data["workspaces"][0]["hnsw_capacity"] == {
        "value": None,
        "reason": "invalid_header_length",
    }


def test_authority_fault_and_override_do_not_expose_figures(hub, disk, monkeypatch):
    from commonplace.handoff import overview
    from commonplace.web import admin

    headers = cookie(hub)
    directory = disk[0].parent
    (directory / "workspace.json").write_text('{"store":"compact"}')
    code, data, _ = instance(hub, "index-stats", headers)
    assert code == 200
    assert all(
        v == {"value": None, "reason": "backend_not_established"}
        for k, v in data["workspaces"][0].items()
        if k != "workspace_id"
    )
    expected = {"total_drawers": 0, "wings": {}, "rooms": {}}
    monkeypatch.setattr(overview, "summary", lambda *a, **kw: dict(expected))

    def unavailable(*a, **kw):
        raise sqlite3.OperationalError("synthetic")

    monkeypatch.setattr(admin, "instance_authorized", unavailable)
    assert status(hub, headers) == expected
    assert instance(hub, "index-stats", headers)[0] == 503


def test_status_without_worker(hub, disk, monkeypatch):
    from commonplace.handoff import overview

    expected = {
        "total_drawers": 101,
        "wings": {"notes": 101},
        "rooms": {"day": 101},
        "protocol": "MCP",
        "aaak_dialect": True,
    }
    monkeypatch.setattr(overview, "summary", lambda *a, **kw: dict(expected))
    no_load(monkeypatch, hub)
    result = status(hub, cookie(hub))
    assert {k: v for k, v in result.items() if k != "index_stats"} == expected
    assert "index_stats" in result
    assert result["index_stats"]["workspace_id"] == hub.workspace
    assert result["index_stats"]["vector_count"] == {"value": 101, "reason": None}
    assert not hub.supervisor.launcher.started


def test_generation_pointer_is_not_read(hub, disk, monkeypatch):
    pointer = disk[0].parent / ".store-pointer.json"
    pointer.write_text("synthetic pointer whose contents must never be read")
    original = io.open

    def guarded(path, *a, **kw):
        assert Path(path) != pointer
        return original(path, *a, **kw)

    monkeypatch.setattr(io, "open", guarded)
    code, data, _ = instance(hub, "index-stats", cookie(hub))
    assert code == 200
    assert all(
        v == {"value": None, "reason": "generation_not_established"}
        for k, v in data["workspaces"][0].items()
        if k != "workspace_id"
    )
