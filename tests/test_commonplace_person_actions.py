"""Person-consented card actions through real OAuth and HTTP boundaries."""

import io
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest

from commonplace import oauth, sessions
from commonplace.oauth import store
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_boards import api, service
from tests.test_commonplace_oauth import Client

DECIDE = "cards:decide:person"
APPROVE = "cards:approve:person"
CHAT = "chat:person"
SCOPES = (DECIDE, APPROVE, CHAT)


@pytest.fixture
def phone(hub, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_PHONE_ACTIONS", "on")
    from commonplace import person_actions

    person_actions.install(hub.gateway)
    owner = service(hub, sessions.person_principal(hub.db, hub.admin, hub.workspace))
    owner.board_add(slug="work", wing="shared")
    client = Client(hub)
    client.login()
    client.cid = client.register(
        client_name="Mobile client", redirect_uris=["cmmn://auth/callback"]
    )
    client.owner = owner
    return client


def grant(client, capabilities=SCOPES, *, checked=None, oauth_scopes=("memory:read",)):
    checked = capabilities if checked is None else checked
    path = client.authorize_path(
        client.cid, redirect_uri="cmmn://auth/callback", scope=" ".join(oauth_scopes)
    )
    status, _, html = client.request("GET", path)
    assert status == 200, html
    request = re.search(r'name="request" value="([^"]+)"', html)[1]
    data = {"request": request, "decision": "allow"}
    for scope in checked:
        data[
            {
                DECIDE: "grant_person_decide",
                APPROVE: "grant_person_approve",
                CHAT: "grant_person_chat",
            }[scope]
        ] = "1"
    status, headers, text = client.request("POST", path, data)
    assert status == 303, text
    query = parse_qs(urlsplit(headers["Location"]).query)
    if "error" in query:
        return query
    status, _, text = client.exchange(
        client.cid, query["code"][0], redirect_uri="cmmn://auth/callback"
    )
    assert status == 200, text
    return json.loads(text)


def call(client, pair, card, action="decide", body=None, key="one", **headers):
    return api(
        client.hub,
        {"Authorization": "Bearer " + pair["access_token"], "Idempotency-Key": key, **headers},
        "POST",
        f"/api/person/cards/{card['number']}/{action}",
        body or {"version": card["version"], "reply": "Proceed"},
    )


def ask(client):
    return client.owner.ask(
        board="work",
        question="Proceed with this change?",
        context="Review the completed change.",
        options=["Proceed", "Wait"],
    )


def test_off_has_no_routes_scopes_or_consent(hub, monkeypatch):
    monkeypatch.delenv("COMMONPLACE_PHONE_ACTIONS", raising=False)
    assert not any("/api/person/" in path for _, path in hub.gateway.routes)
    assert not any("/api/person/" in path for _, path, _ in hub.gateway.prefix_routes)
    assert set(SCOPES).isdisjoint(oauth._server_metadata()["scopes_supported"])
    for scope in SCOPES:
        with pytest.raises(Exception) as caught:
            store.scope(scope)
        assert caught.value.error == "invalid_scope"
    assert set(SCOPES).isdisjoint(sessions.person_policy("owner").scopes)


def test_phone_capabilities_never_widen_oauth_scopes(monkeypatch):
    monkeypatch.setenv("COMMONPLACE_PHONE_ACTIONS", "on")
    assert set(SCOPES).isdisjoint(oauth._server_metadata()["scopes_supported"])
    assert store.scope("memory:read") == "memory:read"


def test_consent_is_explicit_separate_and_audited(phone):
    from commonplace import person_actions

    path = phone.authorize_path(phone.cid, redirect_uri="cmmn://auth/callback", scope="memory:read")
    status, _, html = phone.request("GET", path)
    assert status == 200
    for _scope, field, text in person_actions.CONSENT:
        assert unescape(text) in unescape(html)
        tag = re.search(r'<input[^>]*name="' + field + r'"[^>]*>', html)[0]
        assert 'type="checkbox"' in tag and "checked" not in tag
    unchecked = grant(phone, checked=[])
    assert unchecked["scope"] == "memory:read"
    assert call(phone, unchecked, ask(phone))[0] == 404
    pair = grant(phone, checked=[DECIDE])
    assert pair["scope"] == "memory:read"
    principal = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    rows = phone.hub.db.audit_entries(phone.hub.workspace)
    grants = [r for r in rows if r["action"] == "person_action.grant"]
    assert len(grants) == 1
    assert grants[0]["user_id"] == principal.user_id
    assert grants[0]["connection_id"] == principal.connection_id
    assert DECIDE in grants[0]["reason"] and "Mobile client" in grants[0]["reason"]
    assert call(phone, pair, ask(phone), "approve")[0] == 404


def test_decide_retry_is_atomic_and_records_person_and_client(phone):
    pair = grant(phone)
    card = ask(phone)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: call(phone, pair, card), range(2)))
    assert replies[0][0] == 200, replies
    assert replies[0] == replies[1]
    assert replies[0][1]["status"] == "todo"
    with phone.owner.store.connection() as conn:
        history = conn.execute(
            "SELECT * FROM task_events WHERE task_id=? AND kind='decide'", (card["number"],)
        ).fetchall()
    assert len(history) == 1
    assert history[0]["stamp_user"] == phone.hub.admin
    assert "Mobile client" in history[0]["context"]
    audits = [
        r
        for r in phone.hub.db.audit_entries()
        if r["action"] == "tool.call" and r["tool"] == "decide"
    ]
    assert len(audits) == 1 and audits[0]["decision"] == "ok"
    assert "Mobile client" in audits[0]["reason"]
    assert call(phone, pair, card, body={"version": card["version"], "reply": "Wait"})[0] == 409


def test_live_kill_switch_refuses_grant_and_use(phone, monkeypatch):
    pair = grant(phone)
    card = ask(phone)
    monkeypatch.delenv("COMMONPLACE_PHONE_ACTIONS")
    assert call(phone, pair, card)[0] == 404
    assert set(SCOPES).isdisjoint(oauth._server_metadata()["scopes_supported"])
    assert phone.owner.task_show(card["number"])["status"] == "review"


def test_old_client_and_token_never_gain_person_authority(phone):
    old = grant(phone, capabilities=[], oauth_scopes=("memory:read", "memory:write"))
    card = ask(phone)
    assert call(phone, old, card)[0] == 404
    status, _, text = phone.refresh(phone.cid, old["refresh_token"], scope=DECIDE)
    assert status == 400 and json.loads(text)["error"] == "invalid_scope"
    fresh = grant(phone, capabilities=[DECIDE])
    assert call(phone, fresh, card)[0] == 200
    assert call(phone, old, card)[0] == 404


def member_client(phone):
    member = phone.hub.db.create_user("member@example.test")
    phone.hub.db.add_member(phone.hub.workspace, member, "member", actor=phone.hub.admin)
    phone.login("member@example.test")
    return member


def make_proposal(phone):
    from commonplace.policy import Policy

    db = phone.hub.db
    parent = db.authenticate_token(phone.hub.owner_token("parent"))
    _, token = db.create_token(
        workspace_ref=phone.hub.workspace,
        user_ref=phone.hub.admin,
        actor=phone.hub.admin,
        name="proposer",
        client="test",
        parent=parent.connection_id,
        policy=Policy.build(
            profile="full",
            scopes=["memory:read", "memory:write"],
            read_wings=["shared"],
            write_wings=["shared"],
            trust="trusted",
        ),
    )
    child = service(phone.hub, db.authenticate_token(token))
    card = child.task_add(board="work", title="Review this proposal")
    # Origin release is a separate web action; proposal approval is still pending.
    card = phone.owner.task_approve(card["number"], reason="Release for independent review")
    assert card["proposal_status"] == "pending"
    return card


@pytest.mark.parametrize("approve", [True, False])
def test_approve_matches_web_and_is_idempotent(phone, approve):
    pair = grant(phone, capabilities=[APPROVE])
    card = make_proposal(phone)
    assert card["proposal_status"] == "pending"
    body = {"version": card["version"], "approve": approve, "reason": "Reviewed"}
    first = call(phone, pair, card, "approve", body)
    assert first[0] == 200, first
    assert call(phone, pair, card, "approve", body) == first
    assert first[1]["proposal_status"] == ("approved" if approve else "rejected")
    rows = [
        r
        for r in phone.hub.db.audit_entries()
        if r["action"] == "tool.call" and r["tool"] == "task_approve"
    ]
    assert len(rows) == 1 and "Mobile client" in rows[0]["reason"]


def test_member_cannot_answer_owner_ask_or_approve_proposal(phone):
    card = ask(phone)
    proposal = make_proposal(phone)
    member_client(phone)
    pair = grant(phone)
    assert call(phone, pair, card)[0] == 403
    assert (
        call(phone, pair, proposal, "approve", {"version": proposal["version"], "approve": True})[0]
        == 403
    )
    assert phone.owner.task_show(card["number"])["status"] == "review"
    assert phone.owner.task_show(proposal["number"])["proposal_status"] == "pending"


def test_member_can_answer_only_their_chosen_completion_review(phone):
    chosen = member_client(phone)
    token = phone.hub.owner_token("worker")
    worker = service(phone.hub, phone.hub.db.authenticate_token(token))
    card = phone.owner.task_add(
        board="work",
        title="Work to review",
        reviewer="user:" + chosen,
        body="Completed work is ready for review.",
    )
    worker.task_take_next(task=card["number"])
    card = worker.task_update(card["number"], status="done")
    assert card["status"] == "review" and card["review_kind"] == "completion"
    pair = grant(phone, capabilities=[DECIDE])
    answer = call(
        phone, pair, card, body={"version": card["version"], "reply": "Accepted", "then": "done"}
    )
    assert answer[0] == 200 and answer[1]["status"] == "done", answer
    # The same member cannot answer a completion assigned to another person.
    with phone.owner.store.transaction() as conn:
        conn.execute("UPDATE boards SET done_needs_review=1 WHERE slug='work'")
    card = phone.owner.task_add(
        board="work", title="Other review", body="Completed work is ready for review."
    )
    worker.task_take_next(task=card["number"])
    card = worker.task_update(card["number"], status="done")
    assert call(phone, pair, card, key="other")[0] == 403


@pytest.mark.parametrize(
    "mutation",
    [
        "revoked",
        "expired",
        "disabled",
        "membership",
        "grant",
        "scope",
        "workspace",
        "user",
        "client",
    ],
)
def test_live_authority_changes_refuse_actions_and_replays(phone, mutation):
    pair = grant(phone)
    card = ask(phone)
    assert call(phone, pair, card)[0] == 200
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    db = phone.hub.db
    other_client = phone.register(client_name="Other client") if mutation == "client" else None
    with db._lock:
        if mutation == "revoked":
            db._conn.execute(
                "UPDATE connections SET revoked_at='2000-01-01' WHERE id=?", (app.connection_id,)
            )
        elif mutation == "expired":
            db._conn.execute(
                "UPDATE oauth_tokens SET expires_at=0 WHERE connection_id=?", (app.connection_id,)
            )
        elif mutation == "disabled":
            db._conn.execute("UPDATE users SET disabled_at='2000-01-01' WHERE id=?", (app.user_id,))
        elif mutation == "membership":
            # Simulate an absent membership; normal connection tombstones retain it.
            db._conn.execute("PRAGMA foreign_keys=OFF")
            db._conn.execute(
                "DELETE FROM memberships WHERE user_id=? AND workspace_id=?",
                (app.user_id, app.workspace_id),
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
        elif mutation == "workspace":
            foreign = db.create_workspace("other", phone.hub.admin)
            db._conn.execute(
                "UPDATE oauth_person_grants SET workspace_id=? WHERE connection_id=?",
                (foreign, app.connection_id),
            )
        elif mutation == "user":
            other = db.create_user("other@example.test")
            db._conn.execute(
                "UPDATE oauth_person_grants SET user_id=? WHERE connection_id=?",
                (other, app.connection_id),
            )
        elif mutation == "client":
            db._conn.execute(
                "UPDATE oauth_person_grants SET client_id=? WHERE connection_id=?",
                (other_client, app.connection_id),
            )
    assert call(phone, pair, card)[0] in (401, 404)


@pytest.mark.parametrize("kind", ["agent", "child", "seat", "coordinator", "machine", "worker"])
def test_oauth_machinery_refused_even_with_consented_scopes(phone, kind):
    pair = grant(phone)
    card = ask(phone)
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    db = phone.hub.db
    with db._lock:
        if kind == "agent":
            db._conn.execute(
                "UPDATE connections SET started_by='agent' WHERE id=?", (app.connection_id,)
            )
        elif kind == "child":
            from commonplace.policy import Policy

            parent = db.authenticate_token(phone.hub.owner_token("parent"))
            cid, token = db.create_token(
                workspace_ref=phone.hub.workspace,
                user_ref=phone.hub.admin,
                actor=phone.hub.admin,
                name="child",
                client="test",
                parent=parent.connection_id,
                policy=Policy.build(scopes=["memory:read"], trust="trusted"),
            )
            policy = Policy.build(scopes=["memory:read"], trust="trusted").to_json()
            db._conn.execute(
                "UPDATE connections SET policy=? WHERE id=?",
                (policy, cid),
            )
            pair = {"access_token": token}
        elif kind == "seat":
            from commonplace.decide_grant import SEAT_CLIENT

            db._conn.execute(
                "UPDATE connections SET client=? WHERE id=?", (SEAT_CLIENT, app.connection_id)
            )
        elif kind in ("machine", "worker"):
            db._conn.execute(
                "INSERT INTO runner_connections(connection_id,machine_id,kind) VALUES (?,?,?)",
                (app.connection_id, "synthetic", kind),
            )
    if kind == "coordinator":
        # Stored identity is checked independently of the app's tool profile.
        with phone.owner.store.transaction() as conn:
            conn.execute("UPDATE boards SET coordinator=? WHERE slug='work'", (app.actor,))
    assert call(phone, pair, card)[0] == 404
    assert phone.owner.task_show(card["number"])["status"] == "review"


def test_pat_session_and_unconsented_app_refused(phone):
    from commonplace.policy import Policy, PolicyError

    card = ask(phone)
    with pytest.raises(PolicyError, match="unknown scopes"):
        phone.hub.token("ordinary", scopes=[DECIDE], trust="trusted")
    token = phone.hub.owner_token("ordinary")
    assert call(phone, {"access_token": token}, card)[0] == 404
    assert (
        api(
            phone.hub,
            {"Cookie": f"{sessions.COOKIE}={phone.cookies[sessions.COOKIE]}"},
            "POST",
            f"/api/person/cards/{card['number']}/decide",
            {"version": card["version"], "reply": "Proceed"},
        )[0]
        == 404
    )
    pair = grant(phone)
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    with phone.hub.db._lock:
        phone.hub.db._conn.execute(
            "DELETE FROM oauth_person_grants WHERE connection_id=?", (app.connection_id,)
        )
    assert call(phone, pair, card)[0] == 404
    assert set(SCOPES).isdisjoint(Policy.build(profile="full", trust="trusted").scopes)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"version": True},
        {"version": 0},
        {"version": 1, "workspace_id": "other"},
        {"version": 1, "user_id": "other"},
    ],
)
def test_invalid_arguments_cannot_select_authority(phone, body):
    pair = grant(phone)
    card = ask(phone)
    assert call(phone, pair, card, body=body or {"reply": "Proceed"})[0] == 400


def test_new_scopes_cannot_access_other_person_routes(phone):
    pair = grant(phone)
    for path in ("/api/account", "/api/account/export", "/api/account/deletion"):
        method = "POST" if path.endswith("deletion") else "GET"
        assert api(phone.hub, pair["access_token"], method, path, {})[0] == 403
    assert api(phone.hub, pair["access_token"], "GET", "/api/events")[0] == 403


def test_missing_key_version_conflict_scope_separation_and_revocation(phone):
    pair = grant(phone, capabilities=[APPROVE])
    card = ask(phone)
    assert call(phone, pair, card)[0] == 404
    pair = grant(phone, capabilities=[DECIDE])
    assert (
        call(phone, pair, card, body={"version": card["version"] + 1, "reply": "Proceed"})[0] == 409
    )
    assert call(phone, pair, card, key="")[0] == 400
    status, _, _ = phone.request(
        "POST", "/oauth/revoke", {"client_id": phone.cid, "token": pair["access_token"]}
    )
    assert status == 200
    assert call(phone, pair, card)[0] == 401


def test_audit_failure_rolls_back_decision_and_receipt(phone, monkeypatch):
    pair = grant(phone)
    card = ask(phone)
    original = phone.hub.db.audit

    def fail(**fields):
        if fields["action"] == "tool.call" and fields.get("tool") == "decide":
            raise RuntimeError("audit unavailable")
        return original(**fields)

    monkeypatch.setattr(phone.hub.db, "audit", fail)
    assert call(phone, pair, card)[0] == 500
    assert phone.owner.task_show(card["number"])["status"] == "review"
    monkeypatch.setattr(phone.hub.db, "audit", original)
    assert call(phone, pair, card)[0] == 200


@pytest.mark.parametrize("action", ["decide", "approve"])
@pytest.mark.parametrize("denied", [False, True])
def test_phone_card_uses_checked_rest_write_before_mutation(phone, monkeypatch, action, denied):
    from commonplace.person_actions import PersonCardService
    from commonplace.rules import enforce

    pair = grant(phone)
    card = ask(phone) if action == "decide" else make_proposal(phone)
    body = {
        "version": card["version"],
        **({"reply": "Proceed"} if action == "decide" else {"approve": True}),
    }
    checked = []
    changed = []
    original = enforce.check
    tool = "decide" if action == "decide" else "task_approve"
    original_action = getattr(PersonCardService, tool)

    def check(ctx, tool, supplied):
        checked.append((ctx.principal.person, ctx.principal.user_id, tool, supplied))
        return (
            enforce.Check("test refusal", "Refresh required", True)
            if denied
            else original(ctx, tool, supplied)
        )

    def perform(service, *args, **kwargs):
        assert checked
        changed.append(True)
        return original_action(service, *args, **kwargs)

    monkeypatch.setattr(enforce, "check", check)
    monkeypatch.setattr(PersonCardService, tool, perform)
    status, result = call(phone, pair, card, action, body, **{"Commonplace-Rules-Seq": "7"})
    assert status == (428 if denied else 200), result
    assert checked == [(True, phone.hub.admin, tool, "7")]
    assert len(changed) == (0 if denied else 1)
    if denied:
        assert phone.owner.task_show(card["number"])["version"] == card["version"]
    rows = [
        r for r in phone.hub.db.audit_entries() if r["action"] == "tool.call" and r["tool"] == tool
    ]
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    assert len(rows) == 1
    assert rows[0]["decision"] == ("deny" if denied else "ok")
    assert rows[0]["user_id"] == phone.hub.admin
    assert rows[0]["connection_id"] == app.connection_id and rows[0]["auth"] == "oauth"
    assert "Mobile client" in rows[0]["reason"] and "transport=rest" in rows[0]["reason"]


def test_bearer_sentinel_is_not_logged(phone, caplog):
    pair = grant(phone)
    assert call(phone, pair, ask(phone))[0] == 200
    assert pair["access_token"] not in caplog.text
    assert pair["refresh_token"] not in caplog.text


def test_consent_form_cannot_grant_after_kill_switch(phone, monkeypatch):
    path = phone.authorize_path(phone.cid, redirect_uri="cmmn://auth/callback", scope="memory:read")
    status, _, html = phone.request("GET", path)
    assert status == 200
    request = re.search(r'name="request" value="([^"]+)"', html)[1]
    monkeypatch.delenv("COMMONPLACE_PHONE_ACTIONS")
    status, headers, _ = phone.request(
        "POST", path, {"request": request, "decision": "allow", "grant_person_decide": "1"}
    )
    assert status == 303
    assert "code" in parse_qs(urlsplit(headers["Location"]).query)
    with phone.hub.db._lock:
        assert (
            phone.hub.db._conn.execute("SELECT count(*) FROM oauth_person_grants").fetchone()[0]
            == 0
        )


def test_authority_is_rechecked_after_waiting_for_board_lock(phone, monkeypatch):
    from commonplace import person_actions

    pair = grant(phone)
    card = ask(phone)
    original = person_actions.authority
    calls = 0

    def recheck(gateway, h, scope, board_conn=None):
        nonlocal calls
        calls += 1
        if board_conn is not None:
            monkeypatch.delenv("COMMONPLACE_PHONE_ACTIONS")
        return original(gateway, h, scope, board_conn)

    monkeypatch.setattr(person_actions, "authority", recheck)
    assert call(phone, pair, card)[0] == 404
    assert calls == 2 and phone.owner.task_show(card["number"])["status"] == "review"


def test_phone_actions_use_existing_authenticated_rate_limit(phone, monkeypatch):
    from commonplace import ratelimit

    pair = grant(phone)
    card = ask(phone)
    monkeypatch.setattr(ratelimit, "AUTHENTICATED", ratelimit.Limit("phone-test", 1, 3600))
    phone.hub.gateway.rate_limits = True
    assert call(phone, pair, card)[0] == 200
    assert call(phone, pair, card)[0] == 429


def test_person_grants_export_without_bearers_and_are_classified(phone):
    from commonplace.account import export, tables

    pair = grant(phone)
    assert call(phone, pair, ask(phone))[0] == 200
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    rule = tables.RULES["oauth_person_grants"]
    assert rule.user == ("user_id",) and rule.workspace == ("workspace_id",)
    assert rule.connection == ("connection_id",) and rule.credential
    buffer = io.BytesIO()
    export.write(phone.hub.gateway, export.plan(phone.hub.gateway, app.user_id), buffer)
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        name = next(
            n
            for n in archive.namelist()
            if n.startswith("account/") and n.endswith("oauth_person_grants.ndjson")
        )
        grants = [json.loads(line) for line in archive.read(name).splitlines()]
        assert {row["capability"] for row in grants} == set(SCOPES)
        assert all(row["user_id"] == app.user_id for row in grants)
        content = b"".join(archive.read(n) for n in archive.namelist())
        assert pair["access_token"].encode() not in content
        assert pair["refresh_token"].encode() not in content


def test_cross_workspace_target_and_private_board_refused(phone):
    pair = grant(phone)
    foreign = phone.hub.db.create_workspace("foreign", phone.hub.admin)
    other = service(phone.hub, sessions.person_principal(phone.hub.db, phone.hub.admin, foreign))
    other.board_add(slug="work", wing="shared")
    card = other.ask(
        board="work",
        question="Proceed with this change?",
        context="Review the completed change.",
        options=["Proceed", "Wait"],
    )
    assert call(phone, pair, card)[0] == 404  # The token's own workspace contains no such card.
    chosen = member_client(phone)
    pair = grant(phone, capabilities=[DECIDE])
    phone.owner.board_add(slug="private-work", wing="private")
    card = phone.owner.task_add(
        board="private-work", title="Private review", reviewer="user:" + chosen
    )
    # Even a named reviewer may not cross the person's current wing visibility.
    assert call(phone, pair, card)[0] == 404


@pytest.mark.parametrize("kind", ["connection", "person-with-oauth-auth", "person-with-connection"])
def test_code_creation_cannot_mint_person_grants_for_non_session_authority(phone, kind):
    from commonplace.oauth.http import OAuthError

    principal = phone.hub.db.authenticate_token(phone.hub.owner_token("ordinary"))
    if kind == "person-with-oauth-auth":
        principal = replace(principal, person=True, auth="oauth", connection_id="")
    elif kind == "person-with-connection":
        principal = replace(principal, person=True, auth="session")
    db = phone.hub.db
    with db._lock:
        before = db._conn.execute("SELECT count(*) FROM connections").fetchone()[0]
    with pytest.raises(OAuthError, match="invalid_scope"), db._lock, db._tx() as conn:
        store.create_code(
            conn,
            store.client(db, phone.cid),
            principal,
            {
                "scope": "memory:read",
                "redirect_uri": "cmmn://auth/callback",
                "resource": oauth.urls.mcp_url(),
                "code_challenge": "unused",
            },
            person_capabilities=[DECIDE],
        )
    with db._lock:
        assert db._conn.execute("SELECT count(*) FROM connections").fetchone()[0] == before
        assert db._conn.execute("SELECT count(*) FROM oauth_person_grants").fetchone()[0] == 0


def test_refresh_can_narrow_but_never_restore_person_scope(phone):
    pair = grant(phone, capabilities=[DECIDE])
    status, _, text = phone.refresh(phone.cid, pair["refresh_token"], scope="memory:read")
    assert status == 200
    narrowed = json.loads(text)
    assert narrowed["scope"] == "memory:read"
    card = ask(phone)
    assert call(phone, narrowed, card, "approve")[0] == 404
    assert call(phone, narrowed, card)[0] == 200
    status, _, text = phone.refresh(phone.cid, narrowed["refresh_token"], scope=APPROVE)
    assert status == 400 and json.loads(text)["error"] == "invalid_scope"
