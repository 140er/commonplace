"""Per-person environment registry: consent, isolation, idempotent upsert, no secrets."""

import json
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest

from commonplace import environments, oauth, sessions
from commonplace.control_db import ControlDB
from commonplace.oauth import store
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_boards import api
from tests.test_commonplace_oauth import REDIRECT, Client, client_approval

NATIVE = "cmmn://auth/callback"
BODY = {"name": "Desk", "endpoint": "desk.local", "machineId": "mach_1"}


def _client(hub, email="owner@example.com"):
    client = Client(hub)
    client.login(email)
    client.cid = client.register(client_name="Phone", redirect_uris=[NATIVE])
    return client


def _pair(client, *, scope="memory:read environments:person", checked=True, redirect_uri=NATIVE):
    path = client.authorize_path(client.cid, redirect_uri=redirect_uri, scope=scope)
    status, _, html = client.request("GET", path)
    assert status == 200, html
    request = re.search(r'name="request" value="([^"]+)"', html)[1]
    data = {"request": request, "decision": "allow", **client_approval(html)}
    if checked:
        data[environments.FIELD] = "1"
    status, headers, text = client.request("POST", path, data)
    assert status == 303, text
    query = parse_qs(urlsplit(headers["Location"]).query)
    assert "error" not in query, query
    status, _, text = client.exchange(client.cid, query["code"][0], redirect_uri=redirect_uri)
    assert status == 200, text
    return json.loads(text), html


def _rows(hub, user_id=None):
    with hub.db._lock:
        if user_id is None:
            return hub.db._conn.execute("SELECT * FROM person_environments").fetchall()
        return hub.db._conn.execute(
            "SELECT * FROM person_environments WHERE user_id = ?", (user_id,)
        ).fetchall()


def test_consent_is_an_unticked_request_and_not_an_oauth_scope(hub):
    client = _client(hub)
    path = client.authorize_path(
        client.cid, redirect_uri=NATIVE, scope="memory:read environments:person"
    )
    status, _, html = client.request("GET", path)
    assert status == 200
    assert environments.TEXT in unescape(html)
    tag = re.search(r'<input[^>]*name="' + environments.FIELD + r'"[^>]*>', html)[0]
    assert 'type="checkbox"' in tag and "checked" not in tag
    plain = client.authorize_path(client.cid, redirect_uri=NATIVE, scope="memory:read")
    assert environments.FIELD not in client.request("GET", plain)[2]
    assert environments.SCOPE not in oauth._server_metadata()["scopes_supported"]
    with pytest.raises(Exception) as caught:
        store.scope(environments.SCOPE)
    assert caught.value.error == "invalid_scope"
    pair, _ = _pair(client)
    assert pair["scope"] == "memory:read"
    principal = hub.gateway.authenticate("Bearer " + pair["access_token"])
    assert set(principal.policy.scopes) == {"memory:read"}


def test_memory_read_session_and_pat_cannot_use_the_registry(hub):
    client = _client(hub)
    unchecked, _ = _pair(client, checked=False)
    assert api(hub, unchecked["access_token"], "GET", "/api/environments") == (
        403,
        {"error": "environments:person required"},
    )
    plain, _ = _pair(client, scope="memory:read", checked=True)
    assert api(hub, plain["access_token"], "PUT", "/api/environments/env_1", BODY)[0] == 403
    assert api(hub, None, "GET", "/api/environments")[0] == 401
    cookie = {"Cookie": f"{sessions.COOKIE}={client.cookies[sessions.COOKIE]}"}
    assert api(hub, cookie, "GET", "/api/environments")[0] == 403
    assert api(hub, hub.owner_token("pat-env"), "DELETE", "/api/environments/env_1")[0] == 403
    assert _rows(hub) == []


def test_upsert_is_idempotent_isolated_and_stores_no_secrets(hub):
    owner = _client(hub)
    pair, _ = _pair(owner)
    token = pair["access_token"]
    first = api(hub, token, "PUT", "/api/environments/env_1", BODY)
    assert first[0] == 200
    assert set(first[1]) == {"id", "name", "endpoint", "machineId", "updatedAt"}
    assert first[1]["id"] == "env_1"
    assert first[1]["machineId"] == "mach_1"
    with hub.db._lock:
        hub.db._conn.execute(
            "UPDATE person_environments SET last_seen_at = ? WHERE user_id = ? AND id = ?",
            ("2020-01-01T00:00:00Z", hub.admin, "env_1"),
        )
    second = api(hub, token, "PUT", "/api/environments/env_1", BODY)
    assert second == first
    with hub.db._lock:
        seen = hub.db._conn.execute(
            "SELECT updated_at, last_seen_at FROM person_environments WHERE id = ?",
            ("env_1",),
        ).fetchone()
    assert seen["updated_at"] == first[1]["updatedAt"]
    assert seen["last_seen_at"] != "2020-01-01T00:00:00Z"
    with hub.db._lock:
        hub.db._conn.execute(
            "UPDATE person_environments SET updated_at = ? WHERE user_id = ? AND id = ?",
            ("2020-01-01T00:00:00Z", hub.admin, "env_1"),
        )
    renamed = api(hub, token, "PUT", "/api/environments/env_1", {**BODY, "name": "Laptop"})
    assert renamed[0] == 200
    assert renamed[1]["name"] == "Laptop"
    assert renamed[1]["updatedAt"] != "2020-01-01T00:00:00Z"
    assert len(_rows(hub, hub.admin)) == 1
    omitted = api(
        hub, token, "PUT", "/api/environments/env_2", {"name": "Home", "endpoint": "home.local"}
    )
    assert omitted[1]["machineId"] is None
    listed = api(hub, token, "GET", "/api/environments")
    assert listed[0] == 200
    assert {row["id"]: row["machineId"] for row in listed[1]["environments"]} == {
        "env_1": "mach_1",
        "env_2": None,
    }
    secret = "pair-secret-do-not-store"
    refused = api(
        hub,
        token,
        "PUT",
        "/api/environments/env_3",
        {**BODY, "token": secret, "pairing_secret": secret, "password": secret},
    )
    assert refused == (400, {"error": "secrets are not accepted"})
    leaked = api(
        hub,
        token,
        "PUT",
        "/api/environments/env_3",
        {"name": "Desk", "endpoint": "https://user:" + secret + "@desk.example"},
    )
    assert leaked[0] == 400
    with hub.db._lock:
        stored = " ".join(repr(tuple(row)) for row in _rows(hub))
        for table in (
            row[0]
            for row in hub.db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ):
            for record in hub.db._conn.execute(f'SELECT * FROM "{table}"'):
                assert secret not in repr(tuple(record)), table
    assert secret not in stored
    assert api(hub, token, "DELETE", "/api/environments/env_2") == (200, {"ok": True})
    assert api(hub, token, "DELETE", "/api/environments/env_2")[0] == 404
    assert [
        row["id"] for row in api(hub, token, "GET", "/api/environments")[1]["environments"]
    ] == ["env_1"]

    member_id = hub.db.create_user("member@example.test")
    hub.db.add_member(hub.workspace, member_id, "member", actor=hub.admin)
    member = Client(hub)
    member.login("member@example.test")
    member.cid = owner.cid
    member_token = _pair(member)[0]["access_token"]
    assert api(hub, member_token, "GET", "/api/environments") == (200, {"environments": []})
    assert api(hub, member_token, "DELETE", "/api/environments/env_1")[0] == 404
    assert api(hub, token, "GET", "/api/environments")[1]["environments"][0]["name"] == "Laptop"
    copied = api(hub, member_token, "PUT", "/api/environments/env_1", BODY)
    assert copied[0] == 200
    assert [row["name"] for row in _rows(hub, hub.admin)] == ["Laptop"]
    assert [row["name"] for row in _rows(hub, member_id)] == ["Desk"]
    status, _, text = owner.refresh(owner.cid, pair["refresh_token"])
    assert status == 200
    refreshed = json.loads(text)
    assert refreshed["scope"] == "memory:read"
    assert (
        api(hub, refreshed["access_token"], "GET", "/api/environments")[1]["environments"][0][
            "name"
        ]
        == "Laptop"
    )
    with hub.db._lock:
        hub.db._conn.execute(
            "UPDATE oauth_person_grants SET revoked_at = strftime('%s','now') "
            "WHERE capability = ? AND user_id = ?",
            (environments.SCOPE, hub.admin),
        )
    assert api(hub, refreshed["access_token"], "GET", "/api/environments")[0] == 403


def test_non_native_and_unrequested_checkbox_grants_nothing(hub):
    client = _client(hub)
    client.cid = client.register(client_name="Browser", redirect_uris=[REDIRECT])
    pair, html = _pair(
        client,
        scope="memory:read environments:person",
        checked=True,
        redirect_uri=REDIRECT,
    )
    assert environments.FIELD not in html
    assert pair["scope"] == "memory:read"
    assert api(hub, pair["access_token"], "GET", "/api/environments")[0] == 403


def test_environment_limit_blocks_a_new_id_and_still_updates(hub):
    client = _client(hub)
    token = _pair(client)[0]["access_token"]
    stamp = "2020-01-01T00:00:00Z"
    with hub.db._lock:
        hub.db._conn.executemany(
            "INSERT INTO person_environments "
            "(user_id, id, name, endpoint, machine_id, created_at, updated_at, last_seen_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                (hub.admin, f"e{i}", "n", "h.local", None, stamp, stamp, stamp)
                for i in range(environments.MAX_PER_USER)
            ],
        )
    assert api(hub, token, "PUT", "/api/environments/extra", BODY) == (
        409,
        {"error": "environment limit reached"},
    )
    updated = api(hub, token, "PUT", "/api/environments/e0", BODY)
    assert updated[0] == 200 and updated[1]["name"] == "Desk"
    assert len(_rows(hub, hub.admin)) == environments.MAX_PER_USER


def test_table_migration_adds_last_seen_and_keeps_rows(tmp_path):
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    try:
        user = db.create_user("person@example.test")
        db._conn.execute(
            """
            CREATE TABLE person_environments (
                user_id TEXT NOT NULL,
                id TEXT NOT NULL,
                name TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                machine_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, id)
            )
            """
        )
        db._conn.execute(
            "INSERT INTO person_environments VALUES (?,?,?,?,?,?,?)",
            (
                user,
                "env_old",
                "Old",
                "old.local",
                None,
                "2020-01-01T00:00:00Z",
                "2020-01-02T00:00:00Z",
            ),
        )
        environments.ensure(db)
        environments.ensure(db)
        columns = [row[1] for row in db._conn.execute("PRAGMA table_info(person_environments)")]
        assert columns == [
            "user_id",
            "id",
            "name",
            "endpoint",
            "machine_id",
            "created_at",
            "updated_at",
            "last_seen_at",
        ]
        row = db._conn.execute("SELECT * FROM person_environments").fetchone()
        assert row["name"] == "Old" and row["endpoint"] == "old.local"
        assert row["last_seen_at"] is None
        assert not any(environments._SECRET_KEY.search(column) for column in columns)
    finally:
        db.close()


def test_fresh_table_matches_the_registry_columns(tmp_path):
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    try:
        environments.ensure(db)
        environments.ensure(db)
        columns = [row[1] for row in db._conn.execute("PRAGMA table_info(person_environments)")]
        assert "last_seen_at" in columns and "endpoint" in columns
        assert "secret" not in columns and "token" not in columns
    finally:
        db.close()
