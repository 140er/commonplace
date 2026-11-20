"""Deleting a preview's files (card #622): owner only, the removal runs with the control-database
lock released, and a publish racing the delete never leaves a ready preview without its files.

Real local HTTP over the console fixture; the race is made deterministic by running the delete
in the gap between a publish writing its files and committing them. Nothing sleeps.
"""

import os
import shutil
import sqlite3
import threading

from commonplace.previews import service, store
from tests._console_helpers import console_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import mcp

PAGE = [
    {"path": "index.html", "text": "<h1>Hello</h1>"},
    {"path": "assets/app.css", "text": "body{color:#222}"},
]


def card(h, title="Build the page"):
    status, body, _ = h.request(h.owner, "POST", "/api/boards/work/cards", {"title": title})
    assert status == 201, body
    return body["number"]


def published(h, name="home", files=None):
    number = card(h)
    ok, text = mcp(h, h.t1, "preview_publish", card=number, name=name, files=files or PAGE)
    assert ok, text
    listed = h.request(h.owner, "GET", f"/api/previews?card={number}")[1]["previews"]
    return number, next(p for p in listed if p["name"] == name)["id"]


def delete(h, auth, identity, confirm=None):
    return h.request(auth, "DELETE", f"/api/previews/{identity}", {"confirm": confirm or identity})


def one(h, identity):
    return h.request(h.owner, "GET", f"/api/previews/{identity}")[1]


def files_of(h, identity):
    return store.root(h.gateway, h.workspace) / identity


def test_an_owner_deletes_the_files_and_everything_says_so(console_hub):
    h = console_hub
    number, identity = published(h)
    assert delete(h, h.member, identity)[0] == 403
    status, body, _ = delete(h, h.owner, identity, confirm="yes")
    assert status == 400 and files_of(h, identity).is_dir()
    assert delete(h, h.other, identity)[0] in (403, 404)
    status, body, _ = delete(h, h.owner, identity)
    assert status == 200
    assert (body["state"], body["reason"], body["url"]) == ("stopped", "files_deleted", None)
    assert body["revisions"][0]["files_deleted"] is True and "files_remaining" not in body
    assert not files_of(h, identity).exists()
    assert not list(store.root(h.gateway, h.workspace).glob(".deleting-*"))
    listed = h.request(h.owner, "GET", f"/api/previews?card={number}")[1]
    assert listed["usage"] == {"ready": 0, "bytes": 0} and listed["can_delete"] is True
    assert h.request(h.member, "GET", f"/api/previews?card={number}")[1]["can_delete"] is False
    status, again, _ = h.request(h.owner, "POST", f"/api/previews/{identity}/open-again", {})
    assert status == 409 and again["reason"] == "files_deleted" and again["next"]
    # A Coworker has no way to delete, and publishing the name again starts a new revision.
    assert not [n for n in h.hub.tool_names(h.t1) if "delete" in n and "preview" in n]
    ok, text = mcp(h, h.t1, "preview_publish", card=number, name="home", files=PAGE)
    assert ok, text
    shown = one(h, identity)
    assert (shown["state"], shown["revision"]) == ("ready", 2)
    assert (store.revision_dir(h.gateway, h.workspace, identity, 2) / "index.html").is_file()


def test_the_removal_runs_with_the_lock_released(console_hub, monkeypatch):
    h = console_hub
    _number, identity = published(h)
    calls = []
    real = shutil.rmtree

    def watched(path, *args, **kwargs):
        calls.append(str(path))
        assert not h.db._lock._is_owned(), f"rmtree of {path} ran under the database lock"
        return real(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", watched)
    assert delete(h, h.owner, identity)[0] == 200
    assert any(".deleting-" in c for c in calls)
    assert not files_of(h, identity).exists()


def test_a_delete_between_a_republish_writing_and_committing_never_leaves_a_ready_preview_without_files(
    console_hub, monkeypatch
):
    h = console_hub
    number, identity = published(h)
    original = store.write_files
    done = []

    def written(*args, **kwargs):
        result = original(*args, **kwargs)
        if not done:
            done.append(delete(h, h.owner, identity)[0])
        return result

    monkeypatch.setattr(store, "write_files", written)
    ok, text = mcp(
        h,
        h.t1,
        "preview_publish",
        card=number,
        name="home",
        files=[{"path": "index.html", "text": "two"}],
    )
    assert done == [200]
    assert not ok and "[changed_meanwhile]" in text
    shown = one(h, identity)
    assert (shown["state"], shown["reason"]) == ("stopped", "files_deleted")
    base = store.root(h.gateway, h.workspace)
    assert (
        not list(base.glob("*/r*"))
        and not list(base.glob("*/.tmp-*"))
        and not list(base.glob(".deleting-*"))
    )
    monkeypatch.setattr(store, "write_files", original)
    ok, text = mcp(
        h,
        h.t1,
        "preview_publish",
        card=number,
        name="home",
        files=[{"path": "index.html", "text": "two"}],
    )
    assert ok, text
    shown = one(h, identity)
    assert shown["state"] == "ready"
    assert (
        store.revision_dir(h.gateway, h.workspace, identity, shown["revision"]) / "index.html"
    ).read_text() == "two"


def test_a_preview_still_being_built_cannot_be_deleted_and_finishes(console_hub, monkeypatch):
    h = console_hub
    number = card(h)
    original = store.write_files
    done = []

    def written(*args, **kwargs):
        result = original(*args, **kwargs)
        if not done:
            with h.db._lock:
                identity = h.db._conn.execute("SELECT id FROM previews").fetchone()[0]
            status, body, _ = delete(h, h.owner, identity)
            done.append((status, body.get("reason")))
        return result

    monkeypatch.setattr(store, "write_files", written)
    ok, text = mcp(h, h.t1, "preview_publish", card=number, name="home", files=PAGE)
    assert ok, text
    assert done == [(409, "validating")]


def test_a_removal_that_leaves_files_says_so_and_asking_again_finishes_it(console_hub, monkeypatch):
    h = console_hub
    _number, identity = published(h)
    monkeypatch.setattr(service, "_remove_tree", lambda path: None)
    status, body, _ = delete(h, h.owner, identity)
    assert status == 200 and body["files_remaining"] is True and body["reason"] == "files_deleted"
    with h.db._lock:
        kinds = [
            r[0]
            for r in h.db._conn.execute(
                "SELECT kind FROM preview_events WHERE preview_id=?", (identity,)
            )
        ]
    assert "delete_incomplete" in kinds
    monkeypatch.undo()
    status, body, _ = delete(h, h.owner, identity)
    assert status == 200 and "files_remaining" not in body
    assert not list(store.root(h.gateway, h.workspace).glob(".deleting-*"))


def test_a_rename_the_disk_refuses_changes_nothing(console_hub, monkeypatch):
    h = console_hub
    _number, identity = published(h)
    real = os.replace

    def refusing(source, target, *args, **kwargs):
        if ".deleting-" in str(target):
            raise OSError("read-only file system")
        return real(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", refusing)
    status, body, _ = delete(h, h.owner, identity)
    assert status == 503 and body["reason"] == "storage_unavailable"
    shown = one(h, identity)
    assert shown["state"] == "ready" and shown["revisions"][0]["files_deleted"] is False
    assert files_of(h, identity).is_dir()


def test_deleted_previews_free_their_place_under_the_cap(console_hub, monkeypatch):
    h = console_hub
    monkeypatch.setattr(store, "MAX_ROWS", 1)
    number, identity = published(h)
    ok, text = mcp(h, h.t1, "preview_publish", card=number, name="second", files=PAGE)
    assert not ok and "[too_many_previews]" in text
    assert delete(h, h.owner, identity)[0] == 200
    ok, text = mcp(h, h.t1, "preview_publish", card=number, name="second", files=PAGE)
    assert ok, text


def test_a_revision_table_from_the_first_slice_gains_the_deleted_column():
    class Db:
        _lock = threading.RLock()
        _conn = sqlite3.connect(":memory:")

    Db._conn.execute(
        "CREATE TABLE preview_revisions (preview_id TEXT NOT NULL, revision INTEGER NOT NULL, "
        "digest TEXT NOT NULL, size_bytes INTEGER NOT NULL, file_count INTEGER NOT NULL, "
        "published_by TEXT NOT NULL, by_person INTEGER NOT NULL, published_at TEXT NOT NULL, "
        "PRIMARY KEY (preview_id, revision))"
    )
    Db._conn.execute(
        "INSERT INTO preview_revisions VALUES ('pv_1',1,'d',1,1,'a1',0,'2026-01-01T00:00:00Z')"
    )
    store.ensure_schema(Db)
    store.ensure_schema(Db)  # twice is harmless
    columns = [r[1] for r in Db._conn.execute("PRAGMA table_info(preview_revisions)")]
    assert columns[-1] == "deleted_at"
    assert Db._conn.execute("SELECT deleted_at FROM preview_revisions").fetchall() == [(None,)]


def test_the_served_address_says_the_files_were_deleted(console_hub):
    import http.client

    h = console_hub
    _number, identity = published(h)
    assert delete(h, h.owner, identity)[0] == 200
    conn = http.client.HTTPConnection("127.0.0.1", h.hub.port, timeout=30)
    conn.request("GET", f"/p/{identity}/", headers=h.owner)
    response = conn.getresponse()
    body = response.read()
    conn.close()
    assert response.status == 410 and b"deleted the files" in body
