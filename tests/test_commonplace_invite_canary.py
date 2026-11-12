"""Second-user canaries: what a person who joined through an invite can reach.

The owner files canaries where a member must not look: private wings, a
private captured chat thread, and a second workspace the guest has no part in.
The guest then joins through a real invite and, with only their own browser
session, calls every MemPalace tool, every GET route the gateway serves, the
owner-only routes and the thread reads. No canary may come back, nothing may
change, and each refusal must look like the thing does not exist.

Today's private-wing rule is kept and only observed here: a member reads no
private wing at all, their own notes included, while the owner reads them all.
Whether that stays is an open product decision, not settled by this suite.
"""

import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from commonplace import capture, invites, sessions
from commonplace.policy import Policy
from mempalace.tool_capabilities import TOOL_CAPABILITIES
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_canary import (  # noqa: F401
    MARKERS,
    _assert_clean,
    _schema,
    _variants,
    seeded,
)
from tests.test_commonplace_invites import Browser, invite, join

THREAD_MARKER = "CANARYPRIVATETHREADWORDS"
OTHER = {
    "workspace": "CANARYOTHERWORKSPACE",
    "owner": "canaryotherowner@example.test",
    "connection": "CANARYOTHERCONNECTION",
    "invitee": "canaryotherinvitee@example.test",
}
PRIVATE = MARKERS["private"] + [THREAD_MARKER]
MISSING_THREAD = "thr_" + "0" * 32


def _person(hub, user, workspace, email):
    browser = Browser(hub.port, sessions.start_session(hub.db, user, workspace))
    return SimpleNamespace(id=user, email=email, workspace=workspace, browser=browser)


def _capture_private_thread(hub, owner):
    """A real captured chat: the owner's words in the private chat wing."""
    enabled = owner.browser.post("/api/settings/capture", {"enabled": True})
    assert enabled.status == 200, enabled.text
    _cid, token = hub.db.create_token(
        workspace_ref=hub.workspace,
        user_ref=hub.admin,
        name="owner-capture",
        client="claude-code",
        policy=Policy.build(profile="slim", trust="untrusted", write_wing="inbox"),
        actor=hub.admin,
    )
    record = {
        "type": "user",
        "sessionId": "canary-session",
        "timestamp": "2026-10-20T12:34:56Z",
        "message": {"role": "user", "content": THREAD_MARKER},
    }
    saved = Browser(hub.port).post(
        "/api/capture",
        {
            "source": "claude-code",
            "thread_id": "canary-session",
            "offset": 0,
            "prefix_sha256": capture.EMPTY_SHA256,
            "transcript": json.dumps(record) + "\n",
        },
        token=token,
    )
    assert saved.status == 200, saved.text
    return saved.json()["stored_thread_id"]


@pytest.fixture
def joined(hub, seeded):  # noqa: F811
    owner_token, ids = seeded
    db = hub.db
    owner = _person(hub, hub.admin, hub.workspace, "owner@example.com")
    thread = _capture_private_thread(hub, owner)

    other_owner = db.create_user(OTHER["owner"])
    other_workspace = db.create_workspace(OTHER["workspace"], other_owner)
    db.create_token(
        workspace_ref=other_workspace,
        user_ref=other_owner,
        name=OTHER["connection"],
        client="test",
        policy=Policy.build(profile="slim", trust="untrusted", write_wing="inbox"),
        actor=other_owner,
    )
    other_invite, _secret = invites.create(db, other_workspace, other_owner, email=OTHER["invitee"])

    guest_id = db.create_user("guest@example.test")
    guest_home = db.create_workspace("Guest Home", guest_id, personal=True)
    guest = _person(hub, guest_id, guest_home, "guest@example.test")
    world = SimpleNamespace(owner=owner, port=hub.port)
    assert join(world, guest, invite(world)["secret"]).status == 303
    assert guest.browser.me().json()["workspace"] == hub.workspace
    return SimpleNamespace(
        owner=owner,
        owner_token=owner_token,
        guest=guest,
        ids=ids,
        thread=thread,
        other_workspace=other_workspace,
        other_owner=other_owner,
        other_invite=other_invite["id"],
        forbidden=PRIVATE + list(OTHER.values()) + [other_workspace, other_owner],
    )


def _mcp(browser, name, **arguments):
    response = browser.post(
        "/mcp",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status == 200, (name, response.status, response.text[:300])
    return response.text


def _canaries_intact(hub, j):
    for key in ("private", "private_notes"):
        drawer, _ = hub.call(j.owner_token, "mempalace_get_drawer", drawer_id=j.ids[key])
        assert "CANARY" in drawer["content"], drawer
    read = j.owner.browser.get(f"/api/threads/{j.thread}/messages?start=0&end=199")
    assert read.status == 200 and THREAD_MARKER in read.text


def test_the_canaries_are_real_and_the_guest_is_inside(hub, joined):
    j = joined
    _canaries_intact(hub, j)
    owner_view = _mcp(j.owner.browser, "mempalace_get_drawer", drawer_id=j.ids["private"])
    assert "CANARYPRIVATEDRAWER" in owner_view, "the owner's own session reads the canary"
    shared = _mcp(j.guest.browser, "mempalace_get_drawer", drawer_id=j.ids["allowed"])
    assert "the allowed memory" in shared, "the guest reads shared memory as a member"
    assert hub.db.role(hub.workspace, j.guest.id) == "member"


def test_invited_member_sees_and_changes_no_private_canary_through_any_tool(hub, joined):
    j = joined
    schemas = _schema(hub, j.owner_token)
    called = 0
    for name in sorted(TOOL_CAPABILITIES):
        for args in _variants(name, schemas, j.ids):
            text = _mcp(j.guest.browser, name, **args)
            _assert_clean(text, j.forbidden, f"member/{name}({json.dumps(args)[:200]})")
            called += 1
    assert called >= len(TOOL_CAPABILITIES)
    listing = j.guest.browser.post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    _assert_clean(listing.text, j.forbidden, "member/tools/list")

    hidden = json.loads(_mcp(j.guest.browser, "mempalace_get_drawer", drawer_id=j.ids["private"]))
    missing = json.loads(
        _mcp(j.guest.browser, "mempalace_get_drawer", drawer_id="drawer_none_r_000000000000")
    )
    assert json.loads(hidden["result"]["content"][0]["text"]) == {
        "error": f"Drawer not found: {j.ids['private']}"
    }
    assert set(hidden["result"]) == set(missing["result"])
    _canaries_intact(hub, j)


def _get_paths(hub, j):
    """Every GET the gateway serves under /api/, aimed at the canaries."""
    gateway = hub.gateway
    tails = [
        j.ids["private"],
        j.ids["private_notes"],
        j.thread,
        f"{j.thread}/messages?start=0&end=199",
        f"{j.thread}/chain",
        f"{hub.workspace}/users",
        f"{hub.workspace}/connections",
        f"{j.other_workspace}/users",
        f"{j.other_workspace}/connections",
        "search?q=CANARY&limit=100",
        "search?q=CANARY&theme=private",
        "map",
        j.other_invite,
        OTHER["connection"],
    ]
    paths = {path for (method, path) in gateway.routes if method == "GET"}
    for method, prefix, _handler in gateway.prefix_routes:
        if method == "GET":
            paths.update(prefix.rstrip("/") + "/" + tail for tail in tails)
            paths.add(prefix)
    # A stream holds its response open; its listing twin is swept instead.
    return sorted(p for p in paths if p.startswith("/api/") and "/stream" not in p)


def test_invited_member_sees_no_canary_through_any_get_route(hub, joined):
    j = joined
    paths = _get_paths(hub, j)
    assert len(paths) > 40 and "/api/invites" in paths and "/api/workspaces" in paths
    statuses = {}
    for path in paths:
        response = j.guest.browser.get(path)
        statuses[path] = response.status
        _assert_clean(response.text, j.forbidden, f"member GET {path}")
        assert response.status < 500, (path, response.text[:300])
    assert any(status == 200 for status in statuses.values())
    # The sweep is not vacuous: the owner's session does find canaries on it.
    owner_hits = [
        path
        for path in paths
        if any(marker in j.owner.browser.get(path).text for marker in PRIVATE)
    ]
    assert any(j.thread in path for path in owner_hits), owner_hits
    assert any("/api/memory/" in path for path in owner_hits), owner_hits
    _canaries_intact(hub, j)


OWNER_ONLY = [
    ("GET", "/api/connections", None),
    ("GET", "/api/invites", None),
    ("POST", "/api/invites", {}),
    ("POST", "/api/invites/{invite}/revoke", None),
    ("GET", "/api/admin/workspaces/{workspace}/users", None),
    ("GET", "/api/admin/workspaces/{workspace}/connections", None),
    ("GET", "/api/admin/workspaces/{workspace}/runtime", None),
    ("GET", "/api/runners", None),
    ("POST", "/api/connections/", {"name": "guest-agent", "client": "test"}),
    ("POST", "/api/settings/capture", {"enabled": False}),
    ("POST", "/api/task-types/connections/{connection}", {"allowed_types": []}),
]


@pytest.mark.parametrize("method,path,body", OWNER_ONLY)
def test_invited_member_is_refused_on_owner_only_routes(hub, joined, method, path, body):
    j = joined
    world = SimpleNamespace(owner=j.owner, port=hub.port)
    connection = hub.db.connections(hub.workspace)[0]["id"]
    target = path.format(
        invite=invite(world)["invite"]["id"], workspace=hub.workspace, connection=connection
    )
    before = (len(hub.db.connections(hub.workspace)), len(hub.db.members(hub.workspace)))
    response = j.guest.browser.request(method, target, body)
    assert response.status == 403, (target, response.status, response.text[:300])
    _assert_clean(response.text, j.forbidden + [connection], f"member {method} {target}")
    assert before == (len(hub.db.connections(hub.workspace)), len(hub.db.members(hub.workspace)))
    assert capture_enabled(hub), "the member changed no owner setting"
    states = [row["state"] for row in j.owner.browser.get("/api/invites").json()["invites"]]
    assert states.count("pending") == 1 and states.count("accepted") == 1


def capture_enabled(hub):
    owner = Browser(hub.port, sessions.start_session(hub.db, hub.admin, hub.workspace))
    return owner.get("/api/settings/capture").json().get("enabled") is True


def test_invited_member_reaches_no_other_workspace(hub, joined):
    j = joined
    guest = j.guest.browser
    names = {w["name"]: w["role"] for w in guest.get("/api/workspaces").json()["workspaces"]}
    assert names == {"home": "member", "Guest Home": "owner"}
    me = guest.me()
    _assert_clean(me.text, j.forbidden, "member /api/me")
    assert set(me.json()["people"]) == {f"user:{hub.admin}", f"user:{j.guest.id}"}

    refused = guest.post("/api/workspaces/switch", {"workspace": j.other_workspace})
    assert refused.status == 404 and refused.json() == {"error": "Workspace not found"}
    assert guest.me().json()["workspace"] == hub.workspace
    for path in (
        f"/api/admin/workspaces/{j.other_workspace}/users",
        f"/api/admin/workspaces/{j.other_workspace}/connections",
        "/api/admin/instance/health",
    ):
        admin = guest.get(path)
        assert admin.status in (403, 404), (path, admin.status)
        _assert_clean(admin.text, j.forbidden, f"member GET {path}")
    assert guest.post(f"/api/invites/{j.other_invite}/revoke").status == 403
    # Even the first workspace's owner cannot touch the other workspace's invite.
    cross = j.owner.browser.post(f"/api/invites/{j.other_invite}/revoke")
    assert cross.status == 404
    assert hub.db.role(j.other_workspace, j.guest.id) is None
    assert set(hub.supervisor.launcher.started) <= {hub.workspace}, "no other worker was started"


def test_invited_member_finds_no_unreadable_thread(hub, joined):
    j = joined
    guest = j.guest.browser
    for path in ("/api/sync/sessions", "/api/imports", "/api/chat", "/api/events"):
        listing = guest.get(path)
        assert listing.status < 500, (path, listing.text[:300])
        _assert_clean(listing.text, j.forbidden + [j.thread], f"member listing {path}")
    assert guest.get("/api/sync/sessions").json() == {"sessions": []}

    for tail in ("", "/messages?start=0&end=199", "/chain"):
        hidden = guest.get(f"/api/threads/{j.thread}{tail}")
        missing = guest.get(f"/api/threads/{MISSING_THREAD}{tail}")
        assert hidden.status == missing.status and hidden.status in (403, 404), (tail, hidden.text)
        assert hidden.text.replace(j.thread, MISSING_THREAD) == missing.text
        _assert_clean(hidden.text, PRIVATE, f"member thread{tail}")
    recall = guest.post(f"/api/threads/{j.thread}/recall", {"query": THREAD_MARKER})
    assert recall.status in (403, 404) and "hits" not in recall.text

    for name, args in (
        ("thread_get", {"thread_id": j.thread}),
        ("thread_chain", {"thread_id": j.thread}),
        ("thread_recall", {"thread_id": j.thread, "query": "CANARY words"}),
        ("mempalace_search", {"query": "CANARY words", "limit": 100}),
        ("mempalace_list_rooms", {"wing": "private_chats"}),
        ("mempalace_list_drawers", {"wing": "private_chats"}),
        ("mempalace_get_taxonomy", {}),
    ):
        text = _mcp(guest, name, **args)
        _assert_clean(text, PRIVATE + [j.thread], f"member {name}")
    search = guest.get("/api/memory/search?q=" + quote(THREAD_MARKER) + "&limit=100")
    _assert_clean(search.text, PRIVATE + [j.thread], "member memory search")
    _canaries_intact(hub, j)
