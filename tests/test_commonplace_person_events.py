"""Consented phone events use the current person's web visibility."""

import base64
import io
import json
import re
import zipfile
from html import unescape
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from commonplace import events, sessions
from tests.test_commonplace_boards import api
from tests.test_commonplace_person_actions import (
    grant,
    hub_fixture as hub_fixture,
    member_client,
    phone as phone,
)

CAPABILITY = "events:person"
CONSENT_TEXT = (
    "Allow this app to read your notifications and activity feed as you in this workspace. "
    "It can show only what you can see on the website. Nothing can be changed through this. "
    "Disconnect this app to stop access."
)


def event_grant(client, checked=True):
    path = client.authorize_path(
        client.cid, redirect_uri="cmmn://auth/callback", scope="memory:read"
    )
    status, _, html = client.request("GET", path)
    assert status == 200
    request = re.search(r'name="request" value="([^"]+)"', html)[1]
    data = {"request": request, "decision": "allow"}
    if checked:
        data["grant_person_events"] = "1"
    status, headers, _ = client.request("POST", path, data)
    assert status == 303
    code = parse_qs(urlsplit(headers["Location"]).query)["code"][0]
    status, _, text = client.exchange(client.cid, code, redirect_uri="cmmn://auth/callback")
    assert status == 200
    return json.loads(text)


def feed(client, pair, **params):
    return api(client.hub, pair["access_token"], "GET", "/api/events?" + urlencode(params))


def emit(client, title="Visible update", **data):
    return events.emit(
        client.hub.db,
        client.hub.workspace,
        "card.updated",
        data={"board": "work", "title": title, **data},
    )


def test_consented_phone_events_contract_and_web_order(phone):
    pair = event_grant(phone)
    assert pair["scope"] == "memory:read"
    status, initial = feed(phone, pair)
    assert status == 200, initial
    start = initial["cursor"]
    first = emit(phone, "First", number=1)
    second = emit(phone, "Second", number=2)
    status, page = feed(phone, pair, cursor=start, limit=1)
    assert status == 200, page
    assert set(page) == {"events", "cursor", "has_more", "reset"}
    assert page["has_more"] and not page["reset"]
    assert page["events"] == [
        {
            "id": page["events"][0]["id"],
            "kind": first["kind"],
            "at": first["at"],
            "summary": "First",
            "target": "#/card/1",
        }
    ]
    status, next_page = feed(phone, pair, cursor=page["cursor"], limit=1)
    assert status == 200 and not next_page["has_more"]
    assert next_page["events"][0]["summary"] == "Second"
    assert next_page["events"][0]["at"] == second["at"]
    assert page["events"][0]["id"] != next_page["events"][0]["id"]
    assert feed(phone, pair, cursor=start, limit=1) == (200, page)
    assert feed(phone, pair, cursor=next_page["cursor"])[1]["events"] == []
    cookie = {"Cookie": f"{sessions.COOKIE}={phone.cookies[sessions.COOKIE]}"}
    web = api(phone.hub, cookie, "GET", "/api/events")[1]
    assert [e["data"]["title"] for e in web["events"] if e["kind"] == "card.updated"] == [
        "First",
        "Second",
    ]


def test_unconsented_phone_has_existing_refusal(phone):
    for pair in (event_grant(phone, False), grant(phone)):
        assert feed(phone, pair)[0] == 403


def test_explicit_consent_scope_and_grant_audit(phone):
    from commonplace import oauth, person_actions
    from commonplace.oauth import store

    path = phone.authorize_path(phone.cid, redirect_uri="cmmn://auth/callback", scope="memory:read")
    html = phone.request("GET", path)[2]
    assert CONSENT_TEXT in unescape(html) and person_actions.EVENTS_TEXT == CONSENT_TEXT
    tag = re.search(r'<input[^>]*name="grant_person_events"[^>]*>', html)[0]
    assert 'type="checkbox"' in tag and "checked" not in tag
    assert CAPABILITY not in oauth._server_metadata()["scopes_supported"]
    with pytest.raises(Exception) as error:
        store.scope(CAPABILITY)
    assert error.value.error == "invalid_scope"
    old = event_grant(phone, False)
    assert feed(phone, old)[0] == 403
    pair = event_grant(phone)
    assert feed(phone, old)[0] == 403
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    assert not app.person and set(app.policy.scopes) == {"memory:read"}
    rows = [r for r in phone.hub.db.audit_entries() if r["action"] == "person_action.grant"]
    assert len(rows) == 1 and rows[0]["user_id"] == app.user_id
    assert rows[0]["connection_id"] == app.connection_id and CAPABILITY in rows[0]["reason"]
    assert phone.refresh(phone.cid, old["refresh_token"], scope=CAPABILITY)[0] == 400
    status, _, text = phone.refresh(phone.cid, old["refresh_token"])
    assert status == 200 and feed(phone, json.loads(text))[0] == 403
    emit(phone)
    before_refresh = feed(phone, pair)
    status, _, text = phone.refresh(phone.cid, pair["refresh_token"])
    assert status == 200 and feed(phone, json.loads(text)) == before_refresh
    assert feed(phone, json.loads(text), cursor=before_refresh[1]["cursor"])[1]["events"] == []


def test_default_off_and_live_kill_switch(phone, monkeypatch):
    pair = event_grant(phone)
    monkeypatch.delenv("COMMONPLACE_PHONE_ACTIONS")
    assert feed(phone, pair)[0] == 403
    path = phone.authorize_path(phone.cid, redirect_uri="cmmn://auth/callback", scope="memory:read")
    assert "grant_person_events" not in phone.request("GET", path)[2]
    fresh = event_grant(phone)
    monkeypatch.setenv("COMMONPLACE_PHONE_ACTIONS", "on")
    assert feed(phone, fresh)[0] == 403


@pytest.mark.parametrize(
    "kind", ["agent", "seat", "coordinator", "machine", "worker", "child", "pat"]
)
def test_machinery_and_other_bearers_refused(phone, kind):
    pair = event_grant(phone)
    db = phone.hub.db
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    if kind == "coordinator":
        with phone.owner.store.transaction() as conn:
            conn.execute("UPDATE boards SET coordinator=? WHERE slug='work'", (app.actor,))
    elif kind == "child":
        from commonplace.policy import Policy

        parent = db.authenticate_token(phone.hub.owner_token("parent"))
        _, token = db.create_token(
            workspace_ref=phone.hub.workspace,
            user_ref=phone.hub.admin,
            actor=phone.hub.admin,
            name="child",
            client="test",
            parent=parent.connection_id,
            policy=Policy.build(scopes=["memory:read"], trust="trusted"),
        )
        pair = {"access_token": token}
    elif kind == "pat":
        pair = {"access_token": phone.hub.token("ordinary", scopes=["memory:read"])}
    else:
        with db._lock:
            if kind == "agent":
                db._conn.execute(
                    "UPDATE connections SET started_by='agent' WHERE id=?", (app.connection_id,)
                )
            elif kind == "seat":
                from commonplace.decide_grant import SEAT_CLIENT

                db._conn.execute(
                    "UPDATE connections SET client=? WHERE id=?", (SEAT_CLIENT, app.connection_id)
                )
            else:
                db._conn.execute(
                    "INSERT INTO runner_connections VALUES (?,?,?)",
                    (app.connection_id, "synthetic", kind),
                )
    assert feed(phone, pair)[0] in (401, 403)


@pytest.mark.parametrize(
    "mutation",
    [
        "revoke",
        "expire",
        "disabled",
        "membership",
        "grant",
        "scope",
        "person",
        "workspace",
        "client",
    ],
)
def test_live_authority_binding_is_rechecked(phone, mutation):
    pair = event_grant(phone)
    assert feed(phone, pair)[0] == 200
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    db = phone.hub.db
    other_client = phone.register(client_name="Other client") if mutation == "client" else None
    with db._lock:
        if mutation == "revoke":
            db._conn.execute(
                "UPDATE oauth_tokens SET revoked_at=1 WHERE connection_id=?", (app.connection_id,)
            )
        elif mutation == "expire":
            db._conn.execute(
                "UPDATE oauth_tokens SET expires_at=0 WHERE connection_id=?", (app.connection_id,)
            )
        elif mutation == "disabled":
            db._conn.execute("UPDATE users SET disabled_at='2000-01-01' WHERE id=?", (app.user_id,))
        elif mutation == "membership":
            db._conn.execute("PRAGMA foreign_keys=OFF")
            db._conn.execute(
                "DELETE FROM memberships WHERE workspace_id=? AND user_id=?",
                (app.workspace_id, app.user_id),
            )
            db._conn.execute("PRAGMA foreign_keys=ON")
        elif mutation == "grant":
            db._conn.execute(
                "UPDATE oauth_person_grants SET revoked_at=1 WHERE connection_id=?",
                (app.connection_id,),
            )
        elif mutation == "scope":
            db._conn.execute(
                "UPDATE oauth_tokens SET scope='' WHERE connection_id=?", (app.connection_id,)
            )
        elif mutation == "person":
            other = db.create_user("other@example.test")
            db._conn.execute(
                "UPDATE oauth_person_grants SET user_id=? WHERE connection_id=?",
                (other, app.connection_id),
            )
        elif mutation == "workspace":
            other = db.create_workspace("foreign", phone.hub.admin)
            db._conn.execute(
                "UPDATE oauth_person_grants SET workspace_id=? WHERE connection_id=?",
                (other, app.connection_id),
            )
        else:
            db._conn.execute(
                "UPDATE oauth_person_grants SET client_id=? WHERE connection_id=?",
                (other_client, app.connection_id),
            )
    assert feed(phone, pair)[0] in (401, 403)


def test_member_feed_matches_web_and_hidden_activity_changes_nothing(phone):
    from commonplace.bots.store import open_bots

    member = member_client(phone)
    pair = event_grant(phone)
    emit(phone, "Shared update")
    before = feed(phone, pair)
    cookie = {"Cookie": f"{sessions.COOKIE}={phone.cookies[sessions.COOKIE]}"}
    epoch = api(phone.hub, cookie, "GET", "/api/events")[1]["epoch"]
    web = api(phone.hub, cookie, "GET", "/api/events?epoch=" + epoch)[1]
    assert [e["kind"] for e in before[1]["events"]] == [e["kind"] for e in web["events"]]
    phone.owner.board_add(slug="private-project", wing="private")
    emit(phone, "Unreadable project sentinel", board="private-project")
    foreign = phone.hub.db.create_workspace("foreign", phone.hub.admin)
    events.emit(
        phone.hub.db, foreign, "card.updated", data={"title": "Foreign sentinel", "board": "work"}
    )
    bots = open_bots(phone.hub.gateway, phone.hub.workspace)
    bot = bots.create(phone.hub.admin, "Private conversation", kind="project", board="work")
    bots.add_user_message(
        bot["id"], phone.hub.admin, "Other person's thread sentinel", "private-request"
    )
    assert feed(phone, pair) == before
    assert feed(phone, pair, cursor=before[1]["cursor"])[1]["events"] == []
    assert member != phone.hub.admin
    # Visibility loss resets without replay, and ids remain stable if access returns.
    with phone.owner.store.transaction() as conn:
        conn.execute("UPDATE boards SET wing='private' WHERE slug='work'")
    reset = feed(phone, pair, cursor=before[1]["cursor"])[1]
    assert reset["reset"] and reset["events"] == []
    assert feed(phone, pair)[1]["events"] == []
    with phone.owner.store.transaction() as conn:
        conn.execute("UPDATE boards SET wing='shared' WHERE slug='work'")
    assert feed(phone, pair) == before


@pytest.mark.parametrize(
    "change", ["tamper", "payload", "person", "client", "connection", "workspace"]
)
def test_cursors_are_bound_and_tampering_is_refused(phone, change):
    pair = event_grant(phone)
    cursor = feed(phone, pair)[1]["cursor"]
    original = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    with phone.hub.db._lock:
        secret = phone.hub.db._conn.execute(
            "SELECT cursor_secret FROM oauth_person_grants WHERE connection_id=?",
            (original.connection_id,),
        ).fetchone()[0]
    if change == "tamper":
        cursor = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    elif change == "payload":
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        value[0][1] += 1
        cursor = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    elif change == "person":
        member_client(phone)
        pair = event_grant(phone)
    elif change == "workspace":
        other = phone.hub.db.create_workspace("foreign", phone.hub.admin)
        cookie = sessions.start_session(phone.hub.db, phone.hub.admin, other)
        phone.cookies[sessions.COOKIE] = cookie
        pair = event_grant(phone)
    else:
        if change == "client":
            phone.cid = phone.register(
                client_name="Second mobile client", redirect_uris=["cmmn://auth/callback"]
            )
        pair = event_grant(phone)
    if change not in ("tamper", "payload"):
        target = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
        with phone.hub.db._lock:
            # Even accidental key reuse cannot remove the explicit identity binding.
            phone.hub.db._conn.execute(
                "UPDATE oauth_person_grants SET cursor_secret=? WHERE connection_id=?",
                (secret, target.connection_id),
            )
    status, result = feed(phone, pair, cursor=cursor)
    assert status == 400 and result == {"error": "Invalid event cursor"}


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": "x"},
        {"limit": -1},
        {"cursor": ""},
        {"cursor": "[]"},
        {"after": 0},
        {"workspace": "foreign"},
        {"feed": "sync"},
    ],
)
def test_strict_cursor_and_limit_arguments(phone, params):
    assert feed(phone, event_grant(phone), **params)[0] == 400


def test_page_cap_empty_poll_and_read_only_authority(phone):
    pair = event_grant(phone)
    ordinary = event_grant(phone, False)
    for n in range(110):
        emit(phone, str(n))
    page = feed(phone, pair, limit=100)[1]
    assert len(page["events"]) == 100 and page["has_more"]
    assert len(feed(phone, pair)[1]["events"]) == 50
    assert len(feed(phone, pair, cursor=page["cursor"], limit=100)[1]["events"]) == 11
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert api(phone.hub, pair["access_token"], method, "/api/events", {})[0] >= 400
    for path in ("/api/needs-me", "/api/chat", "/api/account", "/api/account/export"):
        assert api(phone.hub, pair["access_token"], "GET", path) == api(
            phone.hub, ordinary["access_token"], "GET", path
        )
    assert api(phone.hub, pair["access_token"], "POST", "/api/person/cards/1/decide", {})[0] == 404


@pytest.mark.parametrize("shape", ["tag", "entity", "cut"])
def test_summary_normalizes_redacts_then_truncates_and_targets_stay_local(phone, shape):
    key = "sk-" + "TEST_ONLY_NOT_A_REAL_KEY"
    if shape == "tag":
        title, expected = key[:3] + "<b></b>" + key[3:], "[redacted:openai_key]"
    elif shape == "entity":
        title, expected = "s&#107;" + key[2:], "[redacted:openai_key]"
    else:
        prefix = "p" * 230 + " "
        title, expected = prefix + key, prefix
    pair = event_grant(phone)
    start = feed(phone, pair)[1]["cursor"]
    event = emit(phone, title, number="https://example.test/escape", board="//example.test")
    with phone.hub.db._lock:
        phone.hub.db._conn.execute(
            "UPDATE events SET at='2026-01-01T01:00:00.500+01:00' WHERE seq=?", (event["seq"],)
        )
    item = feed(phone, pair, cursor=start)[1]["events"][0]
    assert item["summary"] == expected and len(item["summary"]) <= 240
    assert item["at"] == "2026-01-01T00:00:00.500000Z" and item["target"] == "#/"


def test_read_has_no_new_audit_or_secret_logging_and_uses_existing_budget(
    phone, monkeypatch, caplog
):
    from commonplace import ratelimit
    from commonplace.account import tables

    pair = event_grant(phone)
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    with phone.hub.db._lock:
        secret = phone.hub.db._conn.execute(
            "SELECT cursor_secret FROM oauth_person_grants WHERE connection_id=?",
            (app.connection_id,),
        ).fetchone()[0]
    assert tables.secret("cursor_secret") and secret
    before = phone.hub.db.audit_entries()
    cursor = feed(phone, pair)[1]["cursor"]
    assert feed(phone, pair, cursor=cursor)[0] == 200
    assert phone.hub.db.audit_entries() == before
    logged = caplog.text + json.dumps([dict(row) for row in before])
    assert all(
        s not in logged for s in (secret, cursor, pair["access_token"], pair["refresh_token"])
    )
    monkeypatch.setattr(ratelimit, "AUTHENTICATED", ratelimit.Limit("phone-events-test", 1, 3600))
    phone.hub.gateway.rate_limits = True
    assert feed(phone, pair)[0] == 200 and feed(phone, pair)[0] == 429


def test_non_native_forged_consent_cannot_acquire_events(phone):
    from tests.test_commonplace_oauth import REDIRECT

    cid = phone.register(client_name="Ordinary app")
    path = phone.authorize_path(cid, scope="memory:read")
    html = phone.request("GET", path)[2]
    assert "grant_person_events" not in html
    request = re.search(r'name="request" value="([^"]+)"', html)[1]
    status, headers, _ = phone.request(
        "POST", path, {"request": request, "decision": "allow", "grant_person_events": "1"}
    )
    assert status == 303
    code = parse_qs(urlsplit(headers["Location"]).query)["code"][0]
    status, _, text = phone.exchange(cid, code, redirect_uri=REDIRECT)
    assert status == 200 and feed(phone, json.loads(text))[0] == 403


def test_cursor_secret_export_and_classification(phone):
    from commonplace.account import export, tables

    pair = event_grant(phone)
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    with phone.hub.db._lock:
        secret = phone.hub.db._conn.execute(
            "SELECT cursor_secret FROM oauth_person_grants WHERE connection_id=?",
            (app.connection_id,),
        ).fetchone()[0]
    buffer = io.BytesIO()
    export.write(phone.hub.gateway, export.plan(phone.hub.gateway, app.user_id), buffer)
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        name = next(
            n
            for n in archive.namelist()
            if n.startswith("account/") and n.endswith("oauth_person_grants.ndjson")
        )
        rows = [json.loads(line) for line in archive.read(name).splitlines()]
        assert rows[0]["cursor_secret"] == tables.REDACTED
        content = b"".join(archive.read(n) for n in archive.namelist())
        assert all(
            s.encode() not in content for s in (secret, pair["access_token"], pair["refresh_token"])
        )
    rule = tables.RULES["oauth_person_grants"]
    assert rule.credential and rule.user == ("user_id",) and rule.connection == ("connection_id",)


def test_account_purge_removes_event_credentials(phone, monkeypatch):
    from commonplace.account import deletion

    member = member_client(phone)
    pair = event_grant(phone)
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    principal = sessions.person_principal(phone.hub.db, member, phone.hub.workspace)
    deletion.request(
        phone.hub.gateway, principal, phone.cookies[sessions.COOKIE], "member@example.test"
    )
    assert deletion.purge(phone.hub.gateway, member)["state"] == "purged"
    with phone.hub.db._lock:
        assert not phone.hub.db._conn.execute(
            "SELECT 1 FROM oauth_person_grants WHERE user_id=?", (member,)
        ).fetchone()
    assert feed(phone, pair)[0] == 401


def test_legacy_grant_schema_migrates_without_event_authority(phone):
    from commonplace.oauth import store

    pair = grant(phone)
    with phone.hub.db._lock:
        phone.hub.db._conn.execute("ALTER TABLE oauth_person_grants DROP COLUMN cursor_secret")
    store.ensure_schema(phone.hub.db)
    store.ensure_schema(phone.hub.db)
    with phone.hub.db._lock:
        rows = phone.hub.db._conn.execute(
            "SELECT capability,cursor_secret FROM oauth_person_grants"
        ).fetchall()
    assert all(r["cursor_secret"] is None and r["capability"] != CAPABILITY for r in rows)
    assert feed(phone, pair)[0] == 403
    assert feed(phone, event_grant(phone))[0] == 200


def test_member_pagination_has_no_hidden_gaps(phone):
    member_client(phone)
    pair = event_grant(phone)
    start = feed(phone, pair)[1]["cursor"]
    phone.owner.board_add(slug="private-project", wing="private")
    for n in range(205):
        emit(phone, "Hidden", board="private-project")
        emit(phone, str(n))
    items = []
    cursor = start
    for size in (100, 100, 100):
        status, page = feed(phone, pair, cursor=cursor, limit=size)
        assert status == 200 and not page["reset"]
        items.extend(page["events"])
        cursor = page["cursor"]
    assert [e["summary"] for e in items] == [str(n) for n in range(205)]
    assert len({e["id"] for e in items}) == 205 and not page["has_more"]
    assert feed(phone, pair, cursor=cursor)[1]["events"] == []


def test_recheck_before_response_and_duplicate_arguments(phone, monkeypatch):
    pair = event_grant(phone)
    assert api(phone.hub, pair["access_token"], "GET", "/api/events?limit=1&limit=2")[0] == 400
    original = events.person_page

    def switch_off(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.delenv("COMMONPLACE_PHONE_ACTIONS")
        return result

    monkeypatch.setattr(events, "person_page", switch_off)
    status, result = feed(phone, pair)
    assert status == 404 and "events" not in result
