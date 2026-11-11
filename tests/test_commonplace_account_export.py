"""Account export: complete, verbatim, streamed, and only the person's own data."""

import base64
import hashlib
import io
import json
import sqlite3
import tracemalloc
import zipfile

import pytest

from commonplace import account, sessions, tokens
from commonplace.account import export, tables
from tests._commonplace_account_helpers import (  # noqa: F401
    ALICE_NOTE,
    BOB_NOTE,
    TEAM_NOTE,
    site_fixture,
)


def test_export_holds_the_account_and_owned_workspaces_verbatim(site):
    session = site.session(site.alice, site.alice_home)
    status, headers, data = site.request("GET", "/api/account/export", session=session)
    assert status == 200
    assert headers["Content-Disposition"].startswith("attachment;")
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert archive.testzip() is None
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format"] == export.FORMAT
    assert manifest["complete"] is True and manifest["unclassified_tables"] == []
    assert archive.namelist()[-1] == "manifest.json"

    def rows(name):
        return [json.loads(line) for line in archive.read(name).splitlines()]

    (user,) = rows("account/users.ndjson")
    assert user["email"] == "alice@example.test" and user["name"] == "Alice Example"
    assert {r["workspace_id"] for r in rows("account/memberships.ndjson")} == {
        site.alice_home,
        site.team,
    }
    assert {r["name"] for r in rows("account/connections.ndjson")} == {
        "alice-laptop",
        "alice-agent",
    }
    assert any(r["action"] == "session.create" for r in rows("account/audit_log.ndjson"))

    prefix = f"workspaces/{site.alice_home}"
    tasks = rows(f"{prefix}/stores/boards.sqlite3/tasks.ndjson")
    assert [t["title"] for t in tasks] == [ALICE_NOTE, "second " + ALICE_NOTE]
    assert base64.b64decode(tasks[0]["attachment"]["$base64"]) == b"\x00\xff\x10"
    assert tasks[1]["attachment"] is None
    assert archive.read(f"{prefix}/files/palace/notes.txt").decode("utf-8") == ALICE_NOTE
    assert rows(f"{prefix}/control/workspaces.ndjson")[0]["name"] == "alice home"
    assert not any(name.endswith(("-wal", "-shm")) for name in archive.namelist())
    listed = {entry["name"]: entry for entry in manifest["entries"]}
    assert listed[f"{prefix}/stores/boards.sqlite3/tasks.ndjson"]["rows"] == 2


def test_export_never_contains_credentials(site):
    session = site.session(site.alice, site.alice_home)
    text = site.export(site.alice, site.alice_home).text()
    for secret in (
        site.alice_home_token,
        tokens.token_hash(site.alice_home_token),
        tokens.token_hash(site.alice_team_token),
        session,
        hashlib.sha256(session.encode("utf-8")).hexdigest(),
    ):
        assert secret not in text
    archive = site.export(site.alice, site.alice_home)
    assert {r["token_hash"] for r in archive.rows("account/connections.ndjson")} == {
        tables.REDACTED
    }
    assert {r["id_hash"] for r in archive.rows("account/sessions.ndjson")} == {tables.REDACTED}


def test_member_gets_no_content_of_a_workspace_they_do_not_own(site):
    alice = site.export(site.alice, site.alice_home)
    by_id = {w["id"]: w for w in alice.manifest["workspaces"]}
    assert by_id[site.team] == {
        "id": site.team,
        "name": "team",
        "role": "member",
        "content": False,
    }
    assert by_id[site.alice_home]["content"] is True
    assert not any(name.startswith(f"workspaces/{site.team}/") for name in alice.names)
    assert not any(site.bob_home in name for name in alice.names)
    text = alice.text()
    assert TEAM_NOTE not in text and BOB_NOTE not in text
    assert "bob@example.test" not in text
    assert "bob-agent" not in text and tokens.token_hint(site.bob_team_token) not in text

    bob = site.export(site.bob, site.bob_home)
    assert TEAM_NOTE in bob.text() and BOB_NOTE in bob.text()
    assert ALICE_NOTE not in bob.text()
    assert any(name.startswith(f"workspaces/{site.team}/stores/") for name in bob.names)


def test_export_needs_a_signed_in_person(site):
    assert site.request("GET", "/api/account/export")[0] == 401
    status, body = site.json("GET", "/api/account/export", token=site.alice_home_token)
    assert status == 403 and "signed-in person" in body["error"]
    assert site.json("GET", "/api/account", token=site.alice_home_token)[0] == 403


def test_account_route_describes_the_export_and_the_deletion_switch(site):
    status, body = site.json("GET", "/api/account", session=site.session(site.bob, site.bob_home))
    assert status == 200
    assert body["email"] == "bob@example.test" and body["export"] == "/api/account/export"
    assert body["deletion"]["enabled"] is False and body["deletion"]["request"] is None


def test_unreadable_workspace_database_refuses_before_sending(site):
    (site.directory(site.alice_home) / "palace" / "compact.sqlite3").write_bytes(b"\x01" * 64)
    status, body = site.json(
        "GET", "/api/account/export", session=site.session(site.alice, site.alice_home)
    )
    assert status == 409 and "palace/compact.sqlite3" in body["error"]
    assert site.rows("SELECT decision FROM audit_log WHERE action = 'account.export'") == [
        {"decision": "deny"}
    ]


def test_symlinks_are_not_followed(site, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("not part of any workspace", encoding="utf-8")
    (site.directory(site.alice_home) / "link.txt").symlink_to(outside)
    (site.directory(site.alice_home) / "linked-dir").symlink_to(tmp_path)
    archive = site.export(site.alice, site.alice_home)
    assert "not part of any workspace" not in archive.text()
    assert not any("link" in name for name in archive.names)


def test_unclassified_table_is_exported_and_marks_the_archive_incomplete(site):
    with site.db._lock:
        site.db._conn.execute("CREATE TABLE later_feature (user_id TEXT, note TEXT)")
        site.db._conn.execute(
            "INSERT INTO later_feature VALUES (?, 'mine'), (?, 'not mine')", (site.alice, site.bob)
        )
    archive = site.export(site.alice, site.alice_home)
    assert archive.manifest["complete"] is False
    assert archive.manifest["unclassified_tables"] == ["later_feature"]
    assert [r["note"] for r in archive.rows("account/later_feature.ndjson")] == ["mine"]


def test_a_broken_download_is_not_a_valid_archive(site, monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("store went away")

    monkeypatch.setattr(export, "_store", fail)
    status, _headers, data = site.request(
        "GET", "/api/account/export", session=site.session(site.alice, site.alice_home)
    )
    assert status == 200
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(io.BytesIO(data))


def test_one_export_per_account_at_a_time(site):
    account._exporting.add(site.alice)
    try:
        status, _body = site.json(
            "GET", "/api/account/export", session=site.session(site.alice, site.alice_home)
        )
    finally:
        account._exporting.discard(site.alice)
    assert status == 429


def test_export_memory_does_not_grow_with_the_account(site):
    """About 40 MB of rows leaves the process through a few hundred KB of buffers."""
    conn = sqlite3.connect(site.directory(site.alice_home) / "threads.sqlite3")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
    conn.executemany(
        "INSERT INTO messages(body) VALUES (?)",
        ((f"{i:08d} " + "x" * 2000,) for i in range(20_000)),
    )
    conn.commit()
    conn.close()

    class Counter:
        total = 0
        largest = 0

        def write(self, data):
            self.total += len(data)
            self.largest = max(self.largest, len(data))

    sink = Counter()
    planned = export.plan(site.gateway, site.alice)
    tracemalloc.start()
    try:
        manifest = export.write(site.gateway, planned, sink)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    name = f"workspaces/{site.alice_home}/stores/threads.sqlite3/messages.ndjson"
    assert {e["name"]: e for e in manifest["entries"]}[name]["rows"] == 20_000
    assert peak < 12 * 1024 * 1024
    assert sink.largest <= 2 * export.BLOCK


def test_session_start_time_is_only_known_for_a_live_session(site):
    session = site.session(site.alice, site.alice_home)
    assert sessions.session_created_at(site.db, session) is not None
    sessions.end_session(site.db, session)
    assert sessions.session_created_at(site.db, session) is None
    assert sessions.session_created_at(site.db, "unknown") is None
    assert sessions.session_created_at(site.db, None) is None
