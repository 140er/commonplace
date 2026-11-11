"""The owner-granted cards:decide scope through the production gateway, synthetic data only."""

import http.client
import json
import re
from dataclasses import replace
from html import unescape
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from commonplace import decide_grant, sessions
from commonplace.boards.service import BoardService
from commonplace.control_db import ControlError
from commonplace.extensions import ToolContext, ToolError
from commonplace.policy import Policy
from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_commonplace_oauth import Client

OPTIONS = ["Ship it", "Hold"]
REFUSAL = "only a signed-in workspace owner can answer this"
# Today's REST refusal for an untrusted browser-made PAT, before the service runs.
HIDDEN = "tool not allowed for this connection"
FULL = "memory:read memory:write cards:decide"


def api(hub, auth, method, path, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if isinstance(auth, dict):
        headers.update(auth)
    elif auth:
        headers["Authorization"] = f"Bearer {auth}"
    try:
        connection.request(
            method, path, json.dumps(body).encode() if body is not None else None, headers
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b"null")
    finally:
        connection.close()


def mcp(hub, token, name, **args):
    response = hub.rpc(token, "tools/call", {"name": name, "arguments": args})
    if "error" in response:
        return False, response["error"]["message"]
    return not response["result"].get("isError", False), response["result"]["content"][0]["text"]


def service(hub, principal):
    return BoardService(ToolContext(principal=principal, gateway=hub.gateway))


def cookie_for(hub, email):
    session = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, email, hub.workspace)
    )
    return {"Cookie": f"{sessions.COOKIE}={session}"}


@pytest.fixture
def world(hub):
    person = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    owner = service(hub, person)
    owner.board_add(slug="work", wing="shared")
    asker_token = hub.owner_token("asker")
    asker = service(hub, hub.gateway.authenticate(f"Bearer {asker_token}"))
    return SimpleNamespace(
        hub=hub,
        owner=owner,
        cookie=cookie_for(hub, "owner@example.com"),
        asker=asker,
        asker_token=asker_token,
        serial=0,
    )


def ask(w, question="Ship the release?"):
    card = w.asker.ask(
        f"{question} Choose one of the options.",
        options=OPTIONS,
        board="work",
        context="The release notes are ready for review.",
    )
    assert card["ask_to"] == f"user:{w.hub.admin}"
    return card["number"], card["version"]


def mint(w, cookie=None, **extra):
    w.serial += 1
    return api(
        w.hub,
        cookie or w.cookie,
        "POST",
        "/api/connections",
        {"name": f"phone-{w.serial}", "client": "generic", **extra},
    )


def owner_app(w, **extra):
    status, body = mint(w, decide=True, **extra)
    assert status == 201, body
    assert body["connection"]["decide"] is True
    return body["token"], body["connection"]["id"]


def rest_decide(w, token, number, version, choice=OPTIONS[0]):
    return api(
        w.hub,
        token,
        "POST",
        f"/api/cards/{number}/decide",
        {"version": version, "choice": choice, "then": "return"},
    )


def oauth_grant(hub, *, email="owner@example.com", scope=FULL, tick=True):
    client = Client(hub)
    client.login(email)
    cid = client.register(client_name="Owner Phone")
    path = client.authorize_path(cid, scope=scope)
    status, _, page = client.request("GET", path)
    assert status == 200, page
    request = unescape(re.search(r'name="request" value="([^"]+)"', page)[1])
    data = {"request": request, "decision": "allow"}
    if tick:
        data["grant_decide"] = "1"
    status, headers, _ = client.request("POST", path, data)
    assert status == 303
    query = parse_qs(urlsplit(headers["Location"]).query)
    assert "code" in query, query
    status, _, raw = client.exchange(cid, query["code"][0])
    assert status == 200, raw
    issued = json.loads(raw)
    principal = hub.gateway.authenticate("Bearer " + issued["access_token"])
    return SimpleNamespace(page=page, issued=issued, token=issued["access_token"], p=principal)


def delegated_audit(hub):
    return [
        row
        for row in hub.db.audit_entries(hub.workspace, limit=500)
        if row["action"] == decide_grant.AUDIT_ACTION
    ]


# ── 1. an owner's PAT or OAuth token with cards:decide answers that owner's ask ──


def test_a1_owner_pat_with_scope_answers_own_ask_over_rest_and_mcp(world):
    w = world
    token, _cid = owner_app(w)
    # The browser-made token is untrusted; the owner's explicit grant admits decide.
    assert "decide" in w.hub.tool_names(token)
    number, version = ask(w)
    status, card = rest_decide(w, token, number, version)
    assert status == 200, card
    assert card["status"] == "todo" and card["ask_to"] is None
    shown = w.owner.task_show(number)
    decision = [e for e in shown["events"] if e["kind"] == "decide"][-1]
    assert decision["choice"] == OPTIONS[0]
    app = w.hub.gateway.authenticate(f"Bearer {token}")
    assert decision["actor"] == app.actor

    number, version = ask(w, "Second question?")
    ok, text = mcp(w.hub, token, "decide", task=number, version=version, choice=OPTIONS[1])
    assert ok, text
    assert w.owner.task_show(number)["status"] == "todo"


def test_a1_owner_oauth_with_scope_answers_own_ask(world):
    w = world
    grant = oauth_grant(w.hub)
    assert decide_grant.SCOPE_TEXT in unescape(grant.page)
    assert "cards:decide" in grant.issued["scope"].split()
    assert grant.p.policy.has_scope("cards:decide")
    number, version = ask(w)
    status, card = rest_decide(w, grant.token, number, version)
    assert status == 200, card
    assert w.owner.task_show(number)["status"] == "todo"


# ── 2. without the scope it is refused exactly as today ──


def test_a2_without_scope_is_refused_as_today(world):
    w = world
    # Consent shown but left unticked: the client gets a token without the scope.
    unticked = oauth_grant(w.hub, tick=False)
    assert decide_grant.SCOPE_TEXT in unescape(unticked.page)
    assert "cards:decide" not in unticked.issued["scope"].split()
    status, body = mint(w)
    assert status == 201 and body["connection"]["decide"] is False
    plain = body["token"]
    number, version = ask(w)
    for token, refusal in ((unticked.token, REFUSAL), (plain, HIDDEN), (w.asker_token, REFUSAL)):
        status, refused = rest_decide(w, token, number, version)
        assert status == 403 and refusal in refused["error"], refused
    # The untrusted browser PAT still does not see decide; the OAuth client sees it and is refused.
    assert "decide" not in w.hub.tool_names(plain)
    ok, text = mcp(w.hub, unticked.token, "decide", task=number, version=version, choice=OPTIONS[0])
    assert not ok and REFUSAL in text
    assert w.owner.task_show(number)["status"] == "review"
    assert delegated_audit(w.hub) == []


# ── 3. a token cannot decide another person's ask ──


def test_a3_token_cannot_decide_another_persons_ask(world):
    w = world
    hub = w.hub
    token, _cid = owner_app(w)
    member = hub.db.create_user("member@example.com")
    hub.db.add_member(hub.workspace, member, "member", actor=hub.admin)
    co_owner = hub.db.create_user("co-owner@example.com")
    hub.db.add_member(hub.workspace, co_owner, "owner", actor=hub.admin)

    # An ask addressed to a member.
    card = w.asker.task_add(board="work", title="Member review")
    w.asker.task_handoff(
        card["number"],
        to="member@example.com",
        note="Member, do you approve this change?",
        options=OPTIONS,
        context="Only the member decides this question.",
    )
    for_member = w.owner.task_show(card["number"])
    assert for_member["ask_to"] == f"user:{member}"
    status, refused = rest_decide(w, token, card["number"], for_member["version"])
    assert status == 403 and REFUSAL in refused["error"]

    # Another owner's app cannot answer an ask addressed to the first owner.
    status, other = mint(w, cookie=cookie_for(hub, "co-owner@example.com"), decide=True)
    assert status == 201 and other["connection"]["decide"] is True
    number, version = ask(w)
    status, refused = rest_decide(w, other["token"], number, version)
    assert status == 403 and REFUSAL in refused["error"]
    assert w.owner.task_show(number)["status"] == "review"
    assert w.owner.task_show(card["number"])["status"] == "review"

    # An ask routed to an agent coordinator is not the owner's either.
    p = hub.gateway.authenticate(f"Bearer {token}")
    assert not decide_grant.answers_for(p, {"ask_to": "conn:asker"})
    status, ok = rest_decide(w, token, number, version)
    assert status == 200, ok
    assert delegated_audit(hub)[0]["connection_id"] == p.connection_id


# ── 4. a non-owner or an agent connection cannot grant the scope ──


def test_a4_non_owner_or_agent_cannot_grant(world):
    w = world
    hub = w.hub
    _token, cid = owner_app(w)
    member = hub.db.create_user("member@example.com")
    hub.db.add_member(hub.workspace, member, "member", actor=hub.admin)

    # A member sees no consent box; a forged tick grants nothing.
    forged = oauth_grant(hub, email="member@example.com")
    assert decide_grant.OWNER_ONLY_TEXT in unescape(forged.page)
    assert 'name="grant_decide"' not in forged.page
    assert "cards:decide" not in forged.issued["scope"].split()
    member_cookie = cookie_for(hub, "member@example.com")
    assert mint(w, cookie=member_cookie, decide=True)[0] == 403
    assert api(hub, member_cookie, "PATCH", f"/api/connections/{cid}", {"decide": True})[0] == 403

    # Agent connections cannot grant themselves or others, through REST or consent.
    agent = w.asker_token
    me = hub.gateway.authenticate(f"Bearer {agent}")
    assert api(hub, agent, "POST", "/api/connections", {"name": "x", "decide": True})[0] == 403
    status, _ = api(hub, agent, "PATCH", f"/api/connections/{me.connection_id}", {"decide": True})
    assert status == 403
    assert not me.policy.has_scope("cards:decide")

    # A child agent token never holds it, at creation or later.
    status, refused = mint(w, decide=True, parent=cid)
    assert status == 403 and refused["error"] == decide_grant.TOP_LEVEL_TEXT
    status, child = mint(w, parent=cid)
    assert status == 201
    child_id = child["connection"]["id"]
    status, refused = api(hub, w.cookie, "PATCH", f"/api/connections/{child_id}", {"decide": True})
    assert status == 409 and refused["error"] == decide_grant.TOP_LEVEL_TEXT

    # Control-level guards: only the owner's own top-level connection.
    policy = Policy.build(scopes=["memory:read", "cards:decide"], read_wings=["*"])
    with pytest.raises(ControlError, match="cards:decide"):
        hub.db.create_token(
            workspace_ref=hub.workspace,
            user_ref=member,
            name="member-app",
            client="test",
            policy=policy,
            actor=hub.admin,
        )
    # A former owner's connection loses the scope with the role.
    assert not policy.for_role("member").has_scope("cards:decide")
    assert "cards:decide" not in sessions.person_policy("owner").scopes


def test_a4_cli_refuses_the_scope(monkeypatch):
    from commonplace import cli

    monkeypatch.setattr(cli, "_db", lambda args: None)
    with pytest.raises(ValueError, match="browser consent"):
        cli.cmd_token_add(SimpleNamespace(scopes="memory:read,cards:decide"))


# ── 5. revoking the scope stops it at once ──


def test_a5_revoking_the_scope_stops_it_at_once(world):
    w = world
    hub = w.hub
    token, cid = owner_app(w)
    number, version = ask(w)
    assert rest_decide(w, token, number, version)[0] == 200

    status, body = api(hub, w.cookie, "PATCH", f"/api/connections/{cid}", {"decide": False})
    assert status == 200 and body["connection"]["decide"] is False
    number, version = ask(w, "After revoke?")
    status, refused = rest_decide(w, token, number, version)
    # Exactly the refusal of a PAT that never held the scope.
    assert status == 403 and HIDDEN in refused["error"]
    assert "decide" not in hub.tool_names(token)
    revoked = [r for r in hub.db.audit_entries(hub.workspace) if r["connection_id"] == cid]
    assert "connection.decide.revoke" in {r["action"] for r in revoked}

    # Granting again on the same PAT, then revoking the whole connection.
    status, body = api(hub, w.cookie, "PATCH", f"/api/connections/{cid}", {"decide": True})
    assert status == 200 and body["connection"]["decide"] is True
    assert api(hub, w.cookie, "DELETE", f"/api/connections/{cid}")[0] == 200
    assert rest_decide(w, token, number, version)[0] == 401

    # An OAuth client's grant is withdrawn from the same Agents API.
    grant = oauth_grant(hub)
    oauth_id = grant.p.connection_id
    status, body = api(hub, w.cookie, "PATCH", f"/api/connections/{oauth_id}", {"decide": False})
    assert status == 200 and body["connection"]["decide"] is False
    status, refused = rest_decide(w, grant.token, number, version)
    assert status == 403 and REFUSAL in refused["error"]
    # Re-granting an OAuth client needs fresh consent.
    status, _ = api(hub, w.cookie, "PATCH", f"/api/connections/{oauth_id}", {"decide": True})
    assert status == 409
    assert w.owner.task_show(number)["status"] == "review"


# ── 6. an audit row per decide ──


def test_a6_audit_row_per_delegated_decide(world):
    w = world
    hub = w.hub
    token, cid = owner_app(w)
    grant = oauth_grant(hub)
    first, v1 = ask(w, "One?")
    second, v2 = ask(w, "Two?")
    third, v3 = ask(w, "Three?")
    assert rest_decide(w, token, first, v1)[0] == 200
    assert mcp(hub, token, "decide", task=second, version=v2, choice=OPTIONS[1])[0]
    assert rest_decide(w, grant.token, third, v3)[0] == 200
    # A refused attempt and a signed-in person's decide add no delegated row.
    fourth, v4 = ask(w, "Four?")
    assert rest_decide(w, w.asker_token, fourth, v4)[0] == 403
    w.owner.decide(fourth, choice=OPTIONS[0], version=v4)

    rows = sorted(delegated_audit(hub), key=lambda r: r["seq"])
    assert [(r["connection_id"], r["auth"], json.loads(r["target_ids"])) for r in rows] == [
        (cid, "pat", [str(first)]),
        (cid, "pat", [str(second)]),
        (grant.p.connection_id, "oauth", [str(third)]),
    ]
    assert all(
        r["user_id"] == hub.admin
        and r["decision"] == "ok"
        and r["tool"] == "decide"
        and r["reason"] == "board=work"
        for r in rows
    )


# ── guard units ──


def test_answers_for_requires_every_condition(world):
    w = world
    token, _cid = owner_app(w)
    p = w.hub.gateway.authenticate(f"Bearer {token}")
    row = {"ask_to": f"user:{p.user_id}"}
    assert decide_grant.answers_for(p, row)
    for changed in (
        replace(p, person=True),
        replace(p, auth="web"),
        replace(p, role="member"),
        replace(p, parent_connection_id="conn_parent"),
        replace(p, runner="worker"),
        replace(p, policy=replace(p.policy, scopes=("memory:read", "memory:write"))),
    ):
        assert not decide_grant.answers_for(changed, row)
    assert not decide_grant.answers_for(p, {"ask_to": "user:someone-else"})
    assert not decide_grant.answers_for(p, {"ask_to": None})


def test_delegated_decide_still_needs_board_access(world):
    w = world
    hub = w.hub
    number, version = ask(w)
    # The grant does not widen board visibility: an unreadable board stays not found.
    narrow = hub.token(
        "narrow-app",
        profile="slim",
        scopes=["memory:read", "memory:write", "cards:decide"],
        read_wings=["elsewhere"],
        write_wing="inbox",
        trust="untrusted",
    )
    assert rest_decide(w, narrow, number, version)[0] == 404
    # Nor does it replace memory:write.
    read_only = hub.token(
        "read-only-app",
        profile="slim",
        scopes=["memory:read", "cards:decide"],
        read_wings=["*"],
        trust="untrusted",
    )
    status, refused = rest_decide(w, read_only, number, version)
    assert status == 403 and HIDDEN in refused["error"]
    p = hub.gateway.authenticate(f"Bearer {read_only}")
    with pytest.raises(ToolError, match="cannot change this board"):
        service(hub, p).decide(number, choice=OPTIONS[0], version=version)
    assert w.owner.task_show(number)["status"] == "review"
    assert delegated_audit(hub) == []
