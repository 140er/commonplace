"""Meeting-join bots, foundation: switches, sealed credentials, connections, people, kill switch.

Real HTTP against the console hub. Fixture names are generic (an in-memory fake provider); no
platform is named and nothing touches the network.
"""

import logging
import os
import sqlite3
from pathlib import Path

import pytest

from commonplace.cli import main as cli_main
from commonplace.meetings import adapter, seal, store
from tests._console_helpers import console_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import mcp

SENTINEL = "sentinel-credential-7f3a91c2"
SENTINEL_TWO = "sentinel-passcode-5d2e84b1"
ROUTES = (
    ("GET", "/api/meetings"),
    ("POST", "/api/meetings"),
    ("GET", "/api/meetings/me"),
    ("PATCH", "/api/meetings/me"),
    ("GET", "/api/meetings/settings"),
    ("PUT", "/api/meetings/settings"),
    ("GET", "/api/meetings/overview"),
    ("GET", "/api/meetings/connections"),
    ("POST", "/api/meetings/connections"),
    ("PATCH", "/api/meetings/connections/abc"),
    ("DELETE", "/api/meetings/connections/abc"),
    ("POST", "/api/meetings/connections/abc/test"),
    ("GET", "/api/meetings/abc"),
)


@pytest.fixture(autouse=True)
def keys_and_providers(tmp_path, monkeypatch):
    """A wrapping key outside the data directory, and the fake provider registered."""
    key = tmp_path / "keys" / "wrapping.key"
    key.parent.mkdir()
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    monkeypatch.setenv("COMMONPLACE_MEETINGS_KEY_FILE", str(key))
    fake = adapter.register(adapter.FakeAdapter())
    yield fake
    adapter.unregister("fake")


def data_files(hub):
    return sorted(Path(hub.hub.supervisor.root.parent).rglob("meetings.sqlite3*"))


def store_for(hub):
    return store.open_meetings(hub.hub.gateway, hub.hub.workspace)


def enable(hub, **extra):
    status, body, _ = hub.request(
        hub.owner, "PUT", "/api/meetings/settings", {"enabled": True, **extra}
    )
    assert status == 200, body
    return body


def add_connection(hub, label="main", secrets=None):
    status, body, raw = hub.request(
        hub.owner,
        "POST",
        "/api/meetings/connections",
        {"provider": "fake", "label": label, "secrets": secrets or {"client_secret": SENTINEL}},
    )
    assert status == 201, body
    return body["connection"]["id"], raw


def go_live(hub):
    enable(hub)
    connection_id, _ = add_connection(hub)
    assert (
        hub.request(hub.owner, "POST", f"/api/meetings/connections/{connection_id}/test")[0] == 200
    )
    status, body, _ = hub.request(
        hub.owner, "PATCH", f"/api/meetings/connections/{connection_id}", {"enabled": True}
    )
    assert status == 200 and body["connection"]["enabled"], body
    return connection_id


# --- clean workspace: absent and off until configured ---------------------------------------


def test_a_clean_workspace_has_no_meetings_and_creates_nothing(console_hub, caplog):
    hub = console_hub
    for method, path in ROUTES:
        body = {"enabled": True} if method in ("PUT", "PATCH", "POST") else None
        anon = hub.request(None, method, path, body)[0]
        member = hub.request(hub.member, method, path, body)[0]
        agent = hub.request(hub.t1, method, path, body)[0]
        assert (anon, agent) == (401, 404), (method, path, anon, agent)
        if (method, path) == ("GET", "/api/meetings/settings"):
            continue  # the owner may read the defaults; checked below
        assert member == 404, (method, path)
        if method == "PUT":
            continue  # the owner's switch is the one write that creates the file
        owner = hub.request(hub.owner, method, path, body)
        assert owner[0] in (404, 409), (method, path, owner)
        assert owner[0] == 409 or path.endswith("/abc") or path == "/api/meetings/abc"
    status, body, _ = hub.get(hub.owner, "/api/meetings/settings")
    assert (status, body["settings"]["enabled"]) == (200, False)
    assert data_files(hub) == []
    assert adapter.providers() == ["fake"]  # only what this test registered
    assert "meeting_status" not in hub.hub.tool_names(hub.t1)


def test_the_owner_switch_gates_everything_and_a_member_reads_limits_only(console_hub):
    hub = console_hub
    assert hub.get(hub.member, "/api/meetings/settings")[0] == 404
    body = enable(hub, max_minutes=60)
    assert body["settings"] == {
        "enabled": True,
        "transcript_ok": False,
        "external_model_ok": False,
        "max_concurrent": 1,
        "max_minutes": 60,
        "daily_meetings": 10,
    }
    status, body, _ = hub.get(hub.member, "/api/meetings/settings")
    assert status == 200
    assert set(body["settings"]) == {"enabled", "max_concurrent", "max_minutes", "daily_meetings"}
    assert hub.request(hub.member, "PUT", "/api/meetings/settings", {"enabled": False})[0] == 404
    assert store_for(hub).enabled()
    for bad in ({"max_minutes": 4}, {"max_minutes": "60"}, {"enabled": "yes"}, {"x": 1}, {}):
        assert hub.request(hub.owner, "PUT", "/api/meetings/settings", bad)[0] == 400, bad
    off = hub.request(hub.owner, "PUT", "/api/meetings/settings", {"enabled": False})[1]
    assert off["settings"]["enabled"] is False
    assert hub.get(hub.owner, "/api/meetings/connections")[0] == 409
    assert hub.get(hub.member, "/api/meetings/me")[0] == 404


# --- connections: write-only credentials ----------------------------------------------------


def test_a_connection_is_untested_and_off_and_cannot_be_enabled_before_a_passing_test(
    console_hub, keys_and_providers
):
    hub = console_hub
    enable(hub)
    connection_id, _ = add_connection(hub)
    connection = hub.get(hub.owner, "/api/meetings/connections")[1]["connections"][0]
    assert (connection["enabled"], connection["tested_at"], connection["has_secrets"]) == (
        False,
        None,
        True,
    )
    path = f"/api/meetings/connections/{connection_id}"
    status, body, _ = hub.request(hub.owner, "PATCH", path, {"enabled": True})
    assert (status, body["code"]) == (409, "not_tested")
    keys_and_providers.result = adapter.TestResult(False, "bad_credentials")
    body = hub.request(hub.owner, "POST", path + "/test")[1]
    assert (body["ok"], body["reason"], body["connection"]["tested_at"]) == (
        False,
        "bad_credentials",
        None,
    )
    assert hub.request(hub.owner, "PATCH", path, {"enabled": True})[0] == 409
    keys_and_providers.result = adapter.TestResult(True)
    body = hub.request(hub.owner, "POST", path + "/test")[1]
    assert (body["ok"], bool(body["connection"]["tested_at"])) == (True, True)
    assert hub.request(hub.owner, "PATCH", path, {"enabled": True})[1]["connection"]["enabled"]
    # the adapter saw the unsealed credentials, in memory, for this call only
    assert keys_and_providers.tested[-1].secrets == {"client_secret": SENTINEL}
    assert SENTINEL not in repr(keys_and_providers.tested[-1])
    # new credentials are untested credentials: switched off until they pass again
    body = hub.request(hub.owner, "PATCH", path, {"secrets": {"client_secret": SENTINEL_TWO}})[1]
    assert (body["connection"]["enabled"], body["connection"]["tested_at"]) == (False, None)
    hub.request(hub.owner, "POST", path + "/test")
    hub.request(hub.owner, "PATCH", path, {"enabled": True})
    # a failing test switches a working connection off
    keys_and_providers.result = adapter.TestResult(False, "unreachable")
    body = hub.request(hub.owner, "POST", path + "/test")[1]
    assert body["connection"]["enabled"] is False


def test_a_provider_without_chat_send_cannot_pass(console_hub, keys_and_providers):
    hub = console_hub
    enable(hub)
    connection_id, _ = add_connection(hub)
    keys_and_providers.caps = adapter.Caps(chat_send=False)
    body = hub.request(hub.owner, "POST", f"/api/meetings/connections/{connection_id}/test")[1]
    assert (body["ok"], body["reason"]) == (False, "no_chat_send")


def test_connection_input_is_validated_and_unknown_providers_refused(console_hub):
    hub = console_hub
    enable(hub)
    url = "/api/meetings/connections"
    ok = {"provider": "fake", "label": "main", "secrets": {"token": "x"}}
    for bad in (
        {**ok, "provider": "nope"},
        {**ok, "provider": 3},
        {**ok, "label": " "},
        {**ok, "label": "a\nb"},
        {**ok, "secrets": {}},
        {**ok, "secrets": {"Bad Key": "x"}},
        {**ok, "secrets": {"token": 5}},
        {**ok, "secrets": {"token": "x" * 5000}},
        {"provider": "fake", "label": "main"},
        {**ok, "extra": 1},
    ):
        assert hub.request(hub.owner, "POST", url, bad)[0] == 400, bad
    assert hub.request(hub.owner, "POST", url, ok)[0] == 201
    assert hub.request(hub.owner, "POST", url, ok)[0] == 409  # same provider and label
    assert len(hub.get(hub.owner, url)[1]["connections"]) == 1


def test_a_missing_key_store_refuses_the_write_and_stores_nothing(console_hub, monkeypatch):
    hub = console_hub
    enable(hub)
    monkeypatch.delenv("COMMONPLACE_MEETINGS_KEY_FILE")
    monkeypatch.delenv("COMMONPLACE_GITHUB_KEY_FILE", raising=False)
    status, body, raw = hub.request(
        hub.owner,
        "POST",
        "/api/meetings/connections",
        {"provider": "fake", "label": "main", "secrets": {"token": SENTINEL}},
    )
    assert (status, body["code"]) == (503, "key_store_unavailable")
    assert SENTINEL.encode() not in raw
    assert store_for(hub).connections() == []
    for name in (hub.hub.supervisor.root.parent,):
        for path in Path(name).rglob("meetings.sqlite3*"):
            assert SENTINEL.encode() not in path.read_bytes()


def test_a_key_file_inside_the_data_directory_is_refused(console_hub, monkeypatch):
    hub = console_hub
    enable(hub)
    inside = Path(hub.hub.supervisor.root.parent) / "wrapping.key"
    inside.write_bytes(os.urandom(32))
    inside.chmod(0o600)
    monkeypatch.setenv("COMMONPLACE_MEETINGS_KEY_FILE", str(inside))
    status = hub.request(
        hub.owner,
        "POST",
        "/api/meetings/connections",
        {"provider": "fake", "label": "main", "secrets": {"token": "x"}},
    )[0]
    assert status == 503


def test_credentials_and_links_never_appear_in_a_response_a_log_an_audit_row_or_the_file(
    console_hub, keys_and_providers, caplog
):
    hub = console_hub
    caplog.set_level(logging.DEBUG)
    keys_and_providers.raises = RuntimeError(f"provider said {SENTINEL}")
    seen = []
    enable(hub)
    connection_id, raw = add_connection(
        hub, secrets={"client_secret": SENTINEL, "passcode": SENTINEL_TWO}
    )
    seen.append(raw)
    path = f"/api/meetings/connections/{connection_id}"
    body = hub.request(hub.owner, "POST", path + "/test")
    seen.append(body[2])
    assert (body[1]["ok"], body[1]["reason"]) == (False, "adapter_error")
    keys_and_providers.raises = None
    keys_and_providers.result = adapter.TestResult(False, f"bad token {SENTINEL}")
    body = hub.request(hub.owner, "POST", path + "/test")
    seen.append(body[2])
    assert body[1]["reason"] == "failed"
    keys_and_providers.result = adapter.TestResult(True)
    hub.request(hub.owner, "POST", path + "/test")
    seen.append(hub.request(hub.owner, "PATCH", path, {"enabled": True})[2])
    seen.append(
        hub.request(hub.owner, "PATCH", path, {"secrets": {"client_secret": SENTINEL_TWO}})[2]
    )
    for route in ("/api/meetings/connections", "/api/meetings/overview", "/api/meetings/settings"):
        seen.append(hub.get(hub.owner, route)[2])
    # a sealed meeting link is written the same way and stays unreadable through the store
    blob = seal.seal(
        hub.hub.supervisor.root.parent, hub.hub.workspace, "join_ref", "m1", {"ref": SENTINEL}
    )
    store_for(hub)  # the file exists
    audit = repr([dict(r) for r in hub.hub.db.audit_entries(limit=500)])
    wal = [p.read_bytes() for p in data_files(hub)]
    for needle in (SENTINEL, SENTINEL_TWO):
        assert all(needle.encode() not in raw for raw in seen)
        assert needle not in caplog.text
        assert needle not in audit
        assert all(needle.encode() not in data for data in wal)
        assert needle.encode() not in blob
    assert any(row["action"].startswith("meetings.") for row in hub.hub.db.audit_entries(limit=500))


def test_sealed_blobs_are_bound_to_their_row_kind_and_workspace(console_hub):
    root = console_hub.hub.supervisor.root.parent
    blob = seal.seal(root, "ws_a", "secrets", "row1", {"k": "v"})
    assert seal.unseal(root, "ws_a", "secrets", "row1", blob) == {"k": "v"}
    for args in (
        ("ws_b", "secrets", "row1"),
        ("ws_a", "join_ref", "row1"),
        ("ws_a", "secrets", "row2"),
    ):
        with pytest.raises(seal.SealError):
            seal.unseal(root, *args, blob)
    with pytest.raises(seal.SealError):
        seal.unseal(root, "ws_a", "secrets", "row1", blob[:-1] + bytes([blob[-1] ^ 1]))
    with pytest.raises(ValueError):
        seal.seal(root, "ws_a", "other", "row1", {})


def test_connections_are_owner_only_and_deleting_a_used_one_is_refused(console_hub):
    hub = console_hub
    connection_id = go_live(hub)
    path = f"/api/meetings/connections/{connection_id}"
    for who in (hub.member, hub.t1):
        for method, route in (
            ("GET", "/api/meetings/connections"),
            ("PATCH", path),
            ("DELETE", path),
            ("POST", path + "/test"),
            ("GET", "/api/meetings/overview"),
        ):
            assert hub.request(who, method, route, {"enabled": False})[0] == 404, (method, route)
    # another workspace's owner has their own, separate connections
    hub.request(hub.other, "PUT", "/api/meetings/settings", {"enabled": True})
    assert hub.get(hub.other, "/api/meetings/connections")[1]["connections"] == []
    assert hub.request(hub.other, "PATCH", path, {"enabled": False})[0] == 404
    with store_for(hub)._tx() as conn:
        conn.execute(
            "INSERT INTO meetings(id,user_id,connection_id,provider,title,bot_name,state,"
            "attested_at,request_id,created_at) VALUES "
            "('m1','u','%s','fake','t','b','live','x','r','x')" % connection_id
        )
    assert hub.request(hub.owner, "DELETE", path)[1]["code"] == "in_use"
    with store_for(hub)._tx() as conn:
        conn.execute("DELETE FROM meetings")
    assert hub.request(hub.owner, "DELETE", path)[0] == 200
    assert hub.request(hub.owner, "DELETE", path)[0] == 404


# --- people ---------------------------------------------------------------------------------


def test_a_person_who_did_not_opt_in_cannot_invite(console_hub):
    hub = console_hub
    enable(hub)
    status, body, _ = hub.request(hub.member, "POST", "/api/meetings", {})
    assert (status, body["code"]) == (403, "not_opted_in")
    me = hub.get(hub.member, "/api/meetings/me")[1]["me"]
    assert (me["enabled"], me["keep_transcript"], me["language"], me["notes_wing"]) == (
        False,
        False,
        "en",
        None,
    )
    assert hub.request(hub.member, "PATCH", "/api/meetings/me", {"enabled": True})[0] == 200
    status, body, _ = hub.request(hub.member, "POST", "/api/meetings", {})
    assert (status, body["code"]) == (409, "no_connection")
    go_live_connection = add_connection(hub, label="second")[0]
    hub.request(hub.owner, "POST", f"/api/meetings/connections/{go_live_connection}/test")
    hub.request(
        hub.owner, "PATCH", f"/api/meetings/connections/{go_live_connection}", {"enabled": True}
    )
    status, body, _ = hub.request(hub.member, "POST", "/api/meetings", {})
    assert (status, body["code"]) == (
        400,
        "invalid_field",
    )  # opted in, connection on: the body rules
    assert hub.get(hub.member, "/api/meetings")[1] == {"meetings": []}
    # opting in is per person: the owner never opted in
    assert hub.request(hub.owner, "POST", "/api/meetings", {})[1]["code"] == "not_opted_in"
    refusals = [
        r["reason"] for r in hub.hub.db.audit_entries(limit=100) if r["action"] == "meetings.invite"
    ]
    assert "not_opted_in" in refusals


def test_person_settings_are_validated_and_private(console_hub):
    hub = console_hub
    enable(hub)
    url = "/api/meetings/me"
    for bad in ({"language": "xx"}, {"enabled": 1}, {"notes_wing": "../x"}, {"x": 1}, {}):
        assert hub.request(hub.member, "PATCH", url, bad)[0] == 400, bad
    status, body, _ = hub.request(hub.member, "PATCH", url, {"keep_transcript": True})
    assert (status, body["code"]) == (409, "transcript_not_allowed")
    enable(hub, transcript_ok=True)
    assert hub.get(hub.member, url)[1]["me"]["transcript_allowed"] is True
    body = hub.request(
        hub.member, "PATCH", url, {"enabled": True, "keep_transcript": True, "notes_wing": "notes"}
    )[1]["me"]
    assert (body["enabled"], body["keep_transcript"], body["notes_wing"]) == (True, True, "notes")
    assert hub.get(hub.owner, url)[1]["me"]["enabled"] is False
    assert hub.get(hub.t1, url)[0] == 404


# --- kill switch ----------------------------------------------------------------------------


def seed_active(hub, connection_id, state="live"):
    with store_for(hub)._tx() as conn:
        conn.execute(
            "INSERT INTO meetings(id,user_id,connection_id,provider,join_ref,title,bot_name,state,"
            "attested_at,request_id,created_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?)",
            (state, "u", connection_id, "fake", b"sealed", "t", "b", state, "x", state, "x"),
        )
        conn.execute(
            "INSERT INTO meeting_segments VALUES (?,?,?,?,?,?)", (state, 1, None, 0, 1, "words")
        )
        conn.execute(
            "INSERT INTO meeting_outbox(id,meeting_id,kind,ref,state) VALUES (?,?,?,?,?)",
            (state, state, "announce", "a", "queued"),
        )
        conn.execute(
            "INSERT INTO meeting_notes(id,meeting_id,kind,text,evidence,model,created_at) VALUES "
            "(?,?,?,?,?,?,?)",
            (state, state, "summary", "kept", "[[1,1]]", "m", "x"),
        )


def state_of(hub):
    with store_for(hub)._tx(write=False) as conn:
        return (
            {
                r["id"]: (r["state"], r["join_ref"], r["end_reason"])
                for r in conn.execute("SELECT * FROM meetings")
            },
            conn.execute("SELECT COUNT(*) AS n FROM meeting_segments").fetchone()["n"],
            [r["state"] for r in conn.execute("SELECT * FROM meeting_outbox")],
            conn.execute("SELECT COUNT(*) AS n FROM meeting_notes").fetchone()["n"],
        )


def test_switching_meetings_off_ends_active_meetings_and_discards_what_they_hold(console_hub):
    hub = console_hub
    connection_id = go_live(hub)
    for state in ("live", "joining", "done"):
        seed_active(hub, connection_id, state)
    hub.request(hub.owner, "PUT", "/api/meetings/settings", {"enabled": False})
    meetings, segments, outbox, notes = state_of(hub)
    assert meetings["live"] == ("cancelled", None, "cancelled")
    assert meetings["joining"] == ("cancelled", None, "cancelled")
    assert meetings["done"] == ("done", b"sealed", None)
    assert segments == 1 and notes == 3  # only the ended meeting's segments remain; notes stay
    assert sorted(outbox) == ["cancelled", "cancelled", "queued"]


def test_cli_status_and_disable_are_the_kill_switch(console_hub, capsys):
    hub = console_hub
    data = str(hub.hub.supervisor.root.parent)
    connection_id = go_live(hub)
    seed_active(hub, connection_id)
    assert cli_main(["--data", data, "meetings", "status"]) == 0
    out = capsys.readouterr().out
    assert f"{hub.hub.workspace}  meetings: on  connections: 1/1 on  meetings: live=1" in out
    assert f"{hub.other_workspace}  meetings: off (never used)" in out
    assert cli_main(["--data", data, "meetings", "disable"]) == 2  # needs --workspace or --all
    capsys.readouterr()
    assert cli_main(["--data", data, "meetings", "disable", "--all"]) == 0
    assert "1 active meeting(s) ended" in capsys.readouterr().out
    assert not store_for(hub).enabled()
    assert state_of(hub)[0]["live"][0] == "cancelled"
    assert hub.request(hub.owner, "GET", "/api/meetings/connections")[0] == 409
    # a workspace that never used meetings gets no file
    assert not any(str(hub.other_workspace) in str(p) for p in data_files(hub))
    row = [r for r in hub.hub.db.audit_entries(limit=50) if r["action"] == "meetings.disable"]
    assert row and all(r["auth"] == "cli" for r in row)


# --- MCP status -----------------------------------------------------------------------------


def test_meeting_status_is_a_read_only_text_free_view_of_ones_own_meetings(console_hub):
    hub = console_hub
    token = hub.hub.token("status", profile="full", scopes=["events:read"], trust="standard")
    assert "meeting_status" not in hub.hub.tool_names(token)  # off: hidden like any unusable tool
    connection_id = go_live(hub)
    assert "meeting_status" in hub.hub.tool_names(token)
    seed_active(hub, connection_id)
    ok, text = mcp(hub.hub, token, "meeting_status")
    assert ok and text == "{}" or ok  # the owner token's person has no meetings of their own
    import json

    assert json.loads(text)["meetings"] == []
    with store_for(hub)._tx() as conn:
        conn.execute("UPDATE meetings SET user_id=?", (hub.hub.admin,))
    body = json.loads(mcp(hub.hub, token, "meeting_status")[1])
    (meeting,) = body["meetings"]
    assert meeting["state"] == "live" and meeting["counts"] == {
        "participants": 0,
        "segments": 1,
        "notes": 1,
    }
    assert not {"title", "bot_name", "board", "text", "join_ref", "secrets"} & set(meeting)
    assert "words" not in text and "kept" not in text
    tool = next(
        t
        for t in hub.hub.rpc(token, "tools/list")["result"]["tools"]
        if t["name"] == "meeting_status"
    )
    assert tool["inputSchema"]["additionalProperties"] is False
    # nobody else's meetings: another person's token sees none
    other = hub.hub.token(
        "other", user=hub.member_user, profile="full", scopes=["events:read"], trust="standard"
    )
    assert json.loads(mcp(hub.hub, other, "meeting_status")[1])["meetings"] == []


def test_the_schema_matches_the_design_data_model(console_hub):
    hub = console_hub
    enable(hub)
    with store_for(hub)._tx(write=False) as conn:
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert tables >= {
        "meeting_settings",
        "meeting_connections",
        "meeting_people",
        "meetings",
        "meeting_participants",
        "meeting_segments",
        "meeting_notes",
        "meeting_chat",
        "meeting_outbox",
        "meeting_events",
    }
    # one settings row at most
    with pytest.raises(sqlite3.IntegrityError):
        with store_for(hub)._tx() as conn:
            conn.execute("INSERT INTO meeting_settings(changed_by, changed_at) VALUES ('a','b')")


def test_the_fake_adapter_refuses_foreign_links_and_makes_no_network_calls(keys_and_providers):
    fake = keys_and_providers
    ref = fake.parse_link("https://meet.example.test/m/abcd-1234")
    assert isinstance(ref, adapter.JoinRef) and ref.ext_meeting == "abcd-1234"
    for link in (
        "https://evil.example.test/m/abcd-1234",
        "http://meet.example.test/m/abcd-1234",
        "x",
    ):
        assert fake.parse_link(link) == adapter.Reject("foreign_host")
    with pytest.raises(NotImplementedError):  # the interface itself has no behaviour
        adapter.Adapter().post_chat(None, None, "x", "k")
    with pytest.raises(ValueError):
        adapter.register(adapter.FakeAdapter(provider="Bad Name"))
