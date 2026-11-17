"""Human connection names keep identifiers and token authority separate."""

from tests._commonplace_helpers import functional_worker_transport, hub_fixture  # noqa: F401
from tests.test_commonplace_agent_connections import fresh as fresh, http


def test_human_name_is_stored_and_returned_with_identifier(hub, fresh):
    status, _, result = http(hub, "POST", cookie=fresh.cookie, body={"name": "Research Helper"})
    assert status == 201, result
    connection = result["connection"]
    assert connection["name"] == "research-helper"
    assert connection["display_name"] == "Research Helper"
    row = hub.db.connection(connection["id"], fresh.workspace)
    assert row["display_name"] == "Research Helper"
    principal = hub.db.authenticate_token(result["token"])
    assert principal.connection == "research-helper"
    assert principal.policy.trust == "untrusted"
    status, _, listing = http(hub, "GET", cookie=fresh.cookie)
    assert status == 200
    assert listing["connections"][0]["display_name"] == "Research Helper"
    status, _, duplicate = http(hub, "POST", cookie=fresh.cookie, body={"name": "Research Helper"})
    assert status == 400
    assert "already exists" in duplicate["error"]
    assert "research-helper" in duplicate["error"]


def test_name_client_and_parent_failures_name_the_rule(hub, fresh):
    for body, expected in [
        ({"name": "Bad / Name"}, "letters"),
        ({"name": "   "}, "letters"),
        ({"name": "!!!"}, "letters"),
        ({"name": "x" * 121}, "120"),
        ({"name": "Good Name", "client": ""}, "client"),
        ({"name": "Good Name", "parent": "missing"}, "parent"),
    ]:
        status, _, result = http(hub, "POST", cookie=fresh.cookie, body=body)
        assert status == 400
        assert expected in result["error"].lower(), result
        assert "Invalid or duplicate" not in result["error"]


def test_connection_name_input_and_submit_use_the_same_rule():
    import re
    import shutil
    import subprocess
    from pathlib import Path

    result = subprocess.run(
        [shutil.which("node"), str(Path(__file__).with_name("agent_human_names.mjs"))],
        capture_output=True,
        text=True,
        timeout=15,
    )
    diagnostic = re.sub(r"data:text/javascript;base64,[A-Za-z0-9+/=]+", "<module>", result.stderr)
    assert result.returncode == 0, result.stdout + diagnostic


def test_legacy_connection_survives_display_name_migration(tmp_path):
    from commonplace.control_db import ControlDB
    from commonplace.policy import Policy
    from commonplace.web.agents import connection_view

    path = str(tmp_path / "legacy.sqlite3")
    db = ControlDB(path)
    owner = db.create_user("owner@example.test")
    workspace = db.create_workspace("Legacy fixture", owner)
    cid, token = db.create_token(
        workspace_ref=workspace,
        user_ref=owner,
        actor=owner,
        name="Legacy:Helper",
        client="generic",
        policy=Policy.build(profile="slim", scopes=["memory:read"]),
    )
    db._conn.execute("ALTER TABLE connections DROP COLUMN display_name")
    db.close()
    db = ControlDB(path)
    try:
        row = db.connection(cid, workspace)
        assert row["name"] == "Legacy:Helper" and row["display_name"] == ""
        assert connection_view(row)["display_name"] == "Legacy:Helper"
        assert db.authenticate_token(token).connection == "Legacy:Helper"
    finally:
        db.close()


def test_explicit_display_name_matches_identifier_and_survives_rotation(hub, fresh):
    status, _, result = http(
        hub,
        "POST",
        cookie=fresh.cookie,
        body={
            "name": "research-helper",
            "display_name": "Research Helper",
            "client": "codex",
        },
    )
    assert status == 201, result
    original = result["token"]
    cid = result["connection"]["id"]
    status, _, rotated = http(
        hub, "POST", f"/api/connections/{cid}/rotate", cookie=fresh.cookie, body={}
    )
    assert status == 200
    assert rotated["connection"]["display_name"] == "Research Helper"
    assert rotated["connection"]["name"] == "research-helper"
    assert hub.db.authenticate_token(original) is None
    assert hub.db.authenticate_token(rotated["token"]).connection == "research-helper"
    status, _, error = http(
        hub,
        "POST",
        cookie=fresh.cookie,
        body={
            "name": "different-id",
            "display_name": "Other Helper",
        },
    )
    assert status == 400 and error["field"] == "name"
