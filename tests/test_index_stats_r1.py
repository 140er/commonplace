"""Review regressions for additive, bounded operational figures."""

import json
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_admin_api import cookie, member
from tests.test_admin_instance_api import instance
from tests.test_index_stats_readonly import disk as disk, no_load, status, status_response


def sql_palace(path, mode="delete"):
    path.mkdir(parents=True)
    conn = sqlite3.connect(path / "chroma.sqlite3")
    conn.executescript("""
        CREATE TABLE collections(id TEXT PRIMARY KEY,name TEXT UNIQUE,dimension INTEGER);
        CREATE TABLE segments(id TEXT PRIMARY KEY,scope TEXT,collection TEXT);
        CREATE TABLE embeddings(id INTEGER PRIMARY KEY,segment_id TEXT,embedding_id TEXT,
                                UNIQUE(segment_id,embedding_id));
        INSERT INTO collections VALUES('c','mempalace_drawers',3);
        INSERT INTO segments VALUES('m','METADATA','c');
        INSERT INTO embeddings VALUES(1,'m','one');
    """)
    conn.execute("PRAGMA journal_mode=" + mode)
    conn.close()
    return path


def test_additive_admin_overview_and_no_stats_index_operations(hub, disk, monkeypatch):
    from commonplace.handoff import overview

    expected = {
        "total_drawers": 9,
        "wings": {"notes": 9},
        "rooms": {"today": 9},
        "protocol": "MCP",
        "aaak_dialect": True,
        "backend": "chroma",
    }
    calls = []

    def summary(*a, **kw):
        calls.append(True)
        return dict(expected)

    monkeypatch.setattr(overview, "summary", summary)
    no_load(monkeypatch, hub)
    result = status(hub, cookie(hub))
    assert {k: v for k, v in result.items() if k != "index_stats"} == expected
    assert calls == [True]
    assert result["index_stats"]["drawers"]["value"] == 101
    token = hub.token("admin-full", scopes=["memory:read"], read_wings=["*"], profile="full")
    response = status_response(hub, {"Authorization": "Bearer " + token})
    # Wildcard readers without private access get the ordinary projected status;
    # only an instance-admin person session receives figures.
    assert "error" not in response
    assert json.loads(response["result"]["content"][0]["text"]) == expected
    assert "index_stats" not in json.dumps(response)


def test_missing_pointer_with_generation_sibling(hub, disk):
    root = disk[0].parent
    root.with_name(root.name + ".generation-" + "a" * 24).mkdir()
    code, data, _ = instance(hub, "index-stats", cookie(hub))
    assert code == 200
    assert all(
        v == {"value": None, "reason": "generation_not_established"}
        for k, v in data["workspaces"][0].items()
        if k != "workspace_id"
    )


def test_closed_wal_creates_no_files(tmp_path):
    from commonplace import index_stats

    palace = sql_palace(tmp_path / "wal", "wal")
    before = {p.name: p.read_bytes() for p in palace.iterdir()}
    assert set(before) == {"chroma.sqlite3"}
    assert before["chroma.sqlite3"][18:20] == b"\x02\x02"
    result = index_stats.read(palace)
    assert result == index_stats.unknown("wal_unavailable")
    assert {p.name: p.read_bytes() for p in palace.iterdir()} == before


def test_rollback_journal_read_is_untouched(tmp_path):
    from commonplace import index_stats

    palace = sql_palace(tmp_path / "rollback")
    before = {p.name: p.read_bytes() for p in palace.iterdir()}
    assert before["chroma.sqlite3"][18:20] == b"\x01\x01"
    assert index_stats.read(palace)["drawers"] == {"value": 1, "reason": None}
    assert {p.name: p.read_bytes() for p in palace.iterdir()} == before


def test_busy_is_not_corruption(tmp_path):
    from commonplace import index_stats

    palace = sql_palace(tmp_path / "busy")
    writer = sqlite3.connect(palace / "chroma.sqlite3")
    try:
        writer.execute("BEGIN EXCLUSIVE")
        assert index_stats.read(palace) == index_stats.unknown("busy")
    finally:
        writer.close()


@pytest.mark.parametrize("kind", ["workspace", "palace", "database", "segment", "header"])
def test_symlink_reason(hub, disk, kind):
    palace, header = disk
    path = {
        "workspace": palace.parent,
        "palace": palace,
        "database": palace / "chroma.sqlite3",
        "segment": header.parent,
        "header": header,
    }[kind]
    target = path.with_name(path.name + "-original")
    path.rename(target)
    path.symlink_to(target, target_is_directory=target.is_dir())
    code, data, _ = instance(hub, "index-stats", cookie(hub))
    assert code == 200
    row = data["workspaces"][0]
    assert row["hnsw_capacity"] == {"value": None, "reason": "symlink"}
    if kind in ("workspace", "palace", "database"):
        assert all(
            v == {"value": None, "reason": "symlink"} for k, v in row.items() if k != "workspace_id"
        )
    else:
        assert row["drawers"]["value"] == 101


def test_denied_section_is_exactly_unknown_section(hub):
    uid = member(hub)
    token = hub.token("member", user=uid, scopes=["memory:read"], read_wings=["notes"])
    headers = [cookie(hub, "member@example.com"), {"Authorization": "Bearer " + token}]
    hub.db._conn.execute("UPDATE users SET is_admin=0 WHERE id=?", (hub.admin,))
    headers.append(cookie(hub))
    for auth in headers:
        assert (
            instance(hub, "index-stats", auth)[:2]
            == instance(hub, "unknown", auth)[:2]
            == (404, {"error": "not found"})
        )


def test_ui_missing_feature_reports_asserted_text(hub):
    from tests.test_commonplace_web import request

    code, _, raw = request(hub, "/app/admin.js")
    assert code == 200
    source = raw.decode().replace(
        "for (const key of ['deploy','health','backups','backup-online','backup-offline','index-stats'])",
        "for (const key of ['deploy','health','backups','backup-online','backup-offline'])",
    )
    result = subprocess.run(
        [
            shutil.which("node"),
            str(Path(__file__).with_name("admin_instance_ui.mjs")),
            "index-stats",
        ],
        input=source,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 42, result.stderr[-1000:]
    assert "AssertionError" in result.stderr
    assert "Memory index request must exist" in result.stderr


def test_page_deadline_nulls_current_and_remaining(hub, disk, monkeypatch):
    from commonplace import index_stats
    from tests.test_admin_api import clock

    now = [0.0]
    clock(hub, monotonic=lambda: now[0])
    second = hub.db.create_workspace("second", hub.admin)
    hub.db._conn.execute(
        "UPDATE workspaces SET created_at=? WHERE id=?", ("2100-01-01T00:00:00Z", second)
    )
    original = index_stats._header

    def slow(path):
        result = original(path)
        now[0] = 3.0
        return result

    monkeypatch.setattr(index_stats, "_header", slow)
    code, data, _ = instance(hub, "index-stats", cookie(hub))
    assert code == 200
    assert all(
        v == {"value": None, "reason": "read_timeout"}
        for row in data["workspaces"]
        for k, v in row.items()
        if k != "workspace_id"
    )


def test_half_second_budget_and_three_million_covering_count(tmp_path, monkeypatch):
    from commonplace import index_stats

    assert index_stats.READ_SECONDS == 0.5
    palace = sql_palace(tmp_path / "large")
    conn = sqlite3.connect(palace / "chroma.sqlite3")
    try:
        conn.execute("""WITH RECURSIVE n(i) AS (VALUES(2) UNION ALL SELECT i+1 FROM n WHERE i<3000000)
                        INSERT INTO embeddings SELECT i,'m','synthetic-'||i FROM n""")
        conn.commit()
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT count(*) FROM embeddings WHERE segment_id=?", ("m",)
        ).fetchall()
        assert "COVERING INDEX" in str(plan)
    finally:
        conn.close()
    # The 0.5 s constant is asserted above; the count itself runs under a generous budget so
    # a loaded machine cannot turn the covering-index check into a timing test.
    monkeypatch.setattr(index_stats, "READ_SECONDS", 30.0)
    started = time.monotonic()
    result = index_stats.read(palace)
    print(
        json.dumps(
            {"rows": 3000000, "elapsed_s": time.monotonic() - started, "drawers": result["drawers"]}
        )
    )
    assert result["drawers"] == result["vector_count"] == {"value": 3000000, "reason": None}
