"""Workspace invites over public HTTP: invite, join, expiry, reuse, revoke, refusals."""

import http.client
import json
import logging
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest

from commonplace import invites, sessions
from commonplace.control_db import ControlDB, ControlError, _parse
from commonplace.gateway import Gateway
from commonplace.policy import Policy
from commonplace.web.invites import INVITE_COOKIE
from commonplace.workers import WorkerSupervisor


class NoWorker:
    def start(self, *_):
        raise AssertionError("Invites must not start a memory worker")


class Browser:
    """One person's cookie jar against the gateway."""

    def __init__(self, port, session=None):
        self.port = port
        self.cookies = {sessions.COOKIE: session} if session else {}

    def request(self, method, path, body=None, *, headers=None, form=False, token=None):
        sent = dict(headers or {})
        if self.cookies and token is None:
            sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if token:
            sent["Authorization"] = f"Bearer {token}"
        raw = None
        if form:
            raw, sent["Content-Type"] = b"confirm=1", "application/x-www-form-urlencoded"
        elif body is not None or method == "POST":
            raw, sent["Content-Type"] = json.dumps(body or {}).encode(), "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, raw, headers=sent)
            response = conn.getresponse()
            text = response.read().decode("utf-8")
            for name, value in response.getheaders():
                if name.lower() == "set-cookie":
                    key, _, rest = value.partition("=")
                    cookie = rest.split(";", 1)[0]
                    if cookie:
                        self.cookies[key] = cookie
                    else:
                        self.cookies.pop(key, None)
            return SimpleNamespace(
                status=response.status,
                headers=response.getheaders(),
                location=response.getheader("Location"),
                text=text,
                json=lambda: json.loads(text),
            )
        finally:
            conn.close()

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, body=None, **kwargs):
        return self.request("POST", path, body, **kwargs)

    def me(self):
        return self.get("/api/me")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    data = tmp_path / "data"
    data.mkdir()
    db = ControlDB(str(data / "control.sqlite3"))
    supervisor = WorkerSupervisor(str(data), launcher=NoWorker())
    gateway = Gateway(db, supervisor)
    server = gateway.build_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    port = server.server_address[1]
    w = SimpleNamespace(db=db, gateway=gateway, port=port)

    def person(email, workspace_name):
        user = db.create_user(email)
        workspace = db.create_workspace(workspace_name, user, personal=True)
        browser = Browser(port, sessions.start_session(db, user, workspace))
        return SimpleNamespace(id=user, email=email, workspace=workspace, browser=browser)

    w.person = person
    w.owner = person("owner@example.test", "Owner Home")
    w.guest = person("guest@example.test", "Guest Home")
    try:
        yield w
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        handoff = getattr(gateway, "handoff", None)
        if handoff is not None:
            handoff.close()
        supervisor.stop_all()
        db.close()


def invite(w, owner=None, **body):
    response = (owner or w.owner).browser.post("/api/invites", body)
    assert response.status == 201, response.text
    return response.json()


def join(w, person, secret):
    """Open the link, then confirm. Returns the confirming response."""
    opened = person.browser.get(f"/app/join?t={secret}")
    assert opened.status == 302 and opened.location == "/app/join", opened.text
    page = person.browser.get("/app/join")
    if page.status != 200 or "Join workspace" not in page.text:
        return page
    return person.browser.post("/app/join", form=True)


def dump(db):
    with db._lock:
        return "\n".join(db._conn.iterdump())


def test_owner_invite_secret_is_shown_once_and_stored_hashed(world):
    made = invite(world)
    secret = made["secret"]
    assert invites.looks_like_secret(secret)
    assert made["link"].endswith("/app/join?t=" + secret)
    assert made["invite"]["state"] == "pending" and made["invite"]["email"] == ""
    stored = dump(world.db)
    assert secret not in stored and secret.removeprefix(invites.PREFIX) not in stored
    assert invites._hash(secret) in stored

    listing = world.owner.browser.get("/api/invites")
    assert listing.status == 200
    assert [row["id"] for row in listing.json()["invites"]] == [made["invite"]["id"]]
    assert secret not in listing.text and invites._hash(secret) not in listing.text
    assert "secret" not in listing.text
    audit = [row["action"] for row in world.db.audit_entries(world.owner.workspace)]
    assert "invite.create" in audit


def test_join_makes_a_member_in_a_new_session(world):
    secret = invite(world)["secret"]
    guest = world.guest
    before = guest.browser.cookies[sessions.COOKIE]

    opened = guest.browser.get(f"/app/join?t={secret}")
    assert opened.status == 302 and opened.location == "/app/join"
    cookie = next(v for k, v in opened.headers if k.lower() == "set-cookie")
    assert cookie.startswith(f"{INVITE_COOKIE}={secret}")
    assert all(part in cookie for part in ("HttpOnly", "SameSite=Lax", "Path=/app/join"))

    page = guest.browser.get("/app/join")
    assert page.status == 200
    assert "Join Owner Home" in page.text and guest.email in page.text
    assert secret not in page.text
    assert world.db.role(world.owner.workspace, guest.id) is None, "opening the page joins nobody"

    done = guest.browser.post("/app/join", form=True)
    assert done.status == 303 and done.location == "/app/"
    assert INVITE_COOKIE not in guest.browser.cookies
    assert guest.browser.cookies[sessions.COOKIE] != before
    assert world.db.role(world.owner.workspace, guest.id) == "member"

    me = guest.browser.me().json()
    assert me["workspace"] == world.owner.workspace and me["user"] == guest.id
    assert me["can_admin"] is False and me["can_admin_instance"] is False
    assert Browser(world.port, before).me().status == 401, "the old session ended"

    row = world.owner.browser.get("/api/invites").json()["invites"][0]
    assert row["state"] == "accepted" and row["accepted_by_email"] == guest.email
    actions = [row["action"] for row in world.db.audit_entries(world.owner.workspace)]
    assert "invite.accept" in actions


def test_join_never_logs_or_caches_the_secret(world, caplog, capsys):
    secret = invite(world)["secret"]
    with caplog.at_level(logging.DEBUG):
        opened = world.guest.browser.get(f"/app/join?t={secret}")
        page = world.guest.browser.get("/app/join")
        done = world.guest.browser.post("/app/join", form=True)
    assert done.status == 303
    output = capsys.readouterr()
    assert secret not in caplog.text + output.out + output.err
    for response in (opened, page, done):
        headers = {name.lower(): value for name, value in response.headers}
        assert headers["cache-control"] == "no-store"
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert secret not in dump(world.db)


def test_signed_out_visitor_is_sent_to_sign_in_without_the_secret(world):
    secret = invite(world)["secret"]
    visitor = Browser(world.port)
    assert visitor.get(f"/app/join?t={secret}").location == "/app/join"
    redirect = visitor.get("/app/join")
    assert redirect.status == 302 and redirect.location == "/app/?next=%2Fapp%2Fjoin"
    assert secret not in redirect.location
    assert visitor.post("/app/join", form=True).status == 401
    assert world.owner.browser.get("/api/invites").json()["invites"][0]["state"] == "pending"


def test_join_gives_member_rights_and_nothing_more(world):
    assert join(world, world.guest, invite(world)["secret"]).status == 303
    db, guest, workspace = world.db, world.guest, world.owner.workspace
    assert [dict(m) for m in db.members(workspace)] == [
        {"id": world.owner.id, "email": world.owner.email, "role": "owner"},
        {"id": guest.id, "email": guest.email, "role": "member"},
    ]
    assert [c for c in db.connections(workspace) if c["user_id"] == guest.id] == []
    policy = sessions.person_principal(db, guest.id, workspace).policy
    assert "private:read" not in policy.scopes and "admin" not in policy.scopes
    for scope in ("private:read", "admin"):
        with pytest.raises(ControlError):
            db.create_token(
                workspace_ref=workspace,
                user_ref=guest.id,
                name="guest-" + scope.replace(":", "-"),
                client="test",
                policy=Policy.build(profile="full", scopes=[scope], read_wings=["*"]),
                actor=world.owner.id,
            )
    # The guest's own workspace and role there are untouched.
    assert db.role(guest.workspace, guest.id) == "owner"
    assert db.role(guest.workspace, world.owner.id) is None


def test_reuse_is_refused(world):
    secret = invite(world)["secret"]
    assert join(world, world.guest, secret).status == 303
    late = world.person("late@example.test", "Late Home")
    refused = join(world, late, secret)
    assert refused.status == 404 and "This invite does not work" in refused.text
    late.browser.cookies[INVITE_COOKIE] = secret
    assert late.browser.post("/app/join", form=True).status == 404
    assert world.db.role(world.owner.workspace, late.id) is None
    assert late.browser.me().json()["workspace"] == late.workspace


def test_two_people_racing_for_one_invite_admit_one(world):
    secret = invite(world)["secret"]
    people = [world.guest, world.person("late@example.test", "Late Home")]
    barrier, results = threading.Barrier(2), []

    def attempt(person):
        barrier.wait(timeout=5)
        try:
            results.append(invites.accept(world.db, secret, person.id))
        except invites.InviteError as exc:
            results.append(exc.kind)

    threads = [threading.Thread(target=attempt, args=(p,)) for p in people]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(results) == sorted([world.owner.workspace, "invalid"])
    assert len(world.db.members(world.owner.workspace)) == 2


@pytest.mark.parametrize("days,after,works", [(7, 6, True), (7, 8, False), (1, 2, False)])
def test_expiry(world, monkeypatch, days, after, works):
    made = invite(world, days=days)
    real = invites._now
    monkeypatch.setattr(invites, "_now", lambda: real() + timedelta(days=after))
    response = join(world, world.guest, made["secret"])
    row = world.owner.browser.get("/api/invites").json()["invites"][0]
    if works:
        assert response.status == 303 and row["state"] == "accepted"
        assert world.db.role(world.owner.workspace, world.guest.id) == "member"
    else:
        assert response.status == 404 and "This invite does not work" in response.text
        assert row["state"] == "expired"
        assert world.db.role(world.owner.workspace, world.guest.id) is None


def test_default_expiry_is_seven_days(world):
    row = invite(world)["invite"]
    assert _parse(row["expires_at"]) - _parse(row["created_at"]) == timedelta(days=7)


def test_revoke(world):
    made = invite(world)
    path = f"/api/invites/{made['invite']['id']}/revoke"
    revoked = world.owner.browser.post(path)
    assert revoked.status == 200 and revoked.json()["invite"]["state"] == "revoked"
    again = world.owner.browser.post(path)
    assert again.status == 200 and again.json()["invite"]["revoked_at"]
    assert again.json()["invite"]["revoked_at"] == revoked.json()["invite"]["revoked_at"]

    refused = join(world, world.guest, made["secret"])
    assert refused.status == 404
    assert world.db.role(world.owner.workspace, world.guest.id) is None

    assert world.owner.browser.post("/api/invites/invite_0000000000000000/revoke").status == 404
    assert world.owner.browser.post("/api/invites/" + made["invite"]["id"]).status == 404
    used = invite(world)
    assert join(world, world.guest, used["secret"]).status == 303
    conflict = world.owner.browser.post(f"/api/invites/{used['invite']['id']}/revoke")
    assert conflict.status == 409
    assert world.db.role(world.owner.workspace, world.guest.id) == "member"


@pytest.mark.parametrize(
    "secret", ["", "nope", "inv_short", "inv_" + "a" * 44, "cmmn_" + "a" * 43, "<script>x</script>"]
)
def test_malformed_and_unknown_secrets_never_join(world, secret):
    from urllib.parse import quote

    opened = world.guest.browser.get("/app/join?t=" + quote(secret))
    assert opened.status == 404 and "<script>x" not in opened.text
    assert INVITE_COOKIE not in world.guest.browser.cookies
    world.guest.browser.cookies[INVITE_COOKIE] = secret or "x"
    assert world.guest.browser.post("/app/join", form=True).status == 404


def test_unknown_wellformed_secret_and_missing_cookie(world):
    unknown = "inv_" + "A" * 43
    assert join(world, world.guest, unknown).status == 404
    missing = world.guest.browser.get("/app/join")
    assert missing.status == 404 and "Open your invite link again" in missing.text
    assert world.guest.browser.post("/app/join", form=True).status == 404
    assert len(world.db.members(world.owner.workspace)) == 1


def test_email_bound_invite_admits_only_that_account(world):
    made = invite(world, email="Guest@Example.test")
    assert made["invite"]["email"] == "guest@example.test"
    other = world.person("other@example.test", "Other Home")
    refused = join(world, other, made["secret"])
    assert refused.status == 403 and "different account" in refused.text
    other.browser.cookies[INVITE_COOKIE] = made["secret"]
    assert other.browser.post("/app/join", form=True).status == 403
    assert world.db.role(world.owner.workspace, other.id) is None
    assert join(world, world.guest, made["secret"]).status == 303
    assert world.db.role(world.owner.workspace, world.guest.id) == "member"


def test_existing_member_does_not_use_up_an_invite(world):
    first, second = invite(world), invite(world)
    assert join(world, world.guest, first["secret"]).status == 303
    already = join(world, world.guest, second["secret"])
    assert already.status == 200 and "already in this workspace" in already.text
    states = {
        r["id"]: r["state"] for r in world.owner.browser.get("/api/invites").json()["invites"]
    }
    assert states[second["invite"]["id"]] == "pending"
    owner_try = join(world, world.owner, second["secret"])
    assert (
        owner_try.status == 200 and world.db.role(world.owner.workspace, world.owner.id) == "owner"
    )


def test_invite_dies_with_its_owners_authority(world):
    secret = invite(world)["secret"]
    with world.db._lock:
        world.db._conn.execute(
            "UPDATE memberships SET role = 'member' WHERE workspace_id = ? AND user_id = ?",
            (world.owner.workspace, world.owner.id),
        )
    assert join(world, world.guest, secret).status == 404
    assert world.db.role(world.owner.workspace, world.guest.id) is None


def test_foreign_origin_cannot_confirm_a_join(world):
    secret = invite(world)["secret"]
    world.guest.browser.get(f"/app/join?t={secret}")
    refused = world.guest.browser.post(
        "/app/join", form=True, headers={"Origin": "https://evil.example"}
    )
    assert refused.status == 403
    assert world.db.role(world.owner.workspace, world.guest.id) is None
    assert world.guest.browser.post("/app/join", form=True).status == 303


def owner_token(world, person):
    _cid, token = world.db.create_token(
        workspace_ref=person.workspace,
        user_ref=person.id,
        name="owner-agent",
        client="test",
        policy=Policy.build(
            profile="full", scopes=["memory:read", "admin"], read_wings=["*"], trust="trusted"
        ),
        actor=person.id,
    )
    return token


def test_only_a_signed_in_owner_manages_invites(world):
    made = invite(world)
    assert join(world, world.guest, invite(world)["secret"]).status == 303
    revoke = f"/api/invites/{made['invite']['id']}/revoke"
    member, token = world.guest.browser, owner_token(world, world.owner)
    signed_out = Browser(world.port)

    for call in (
        lambda b, **kw: b.get("/api/invites", **kw),
        lambda b, **kw: b.post("/api/invites", {}, **kw),
        lambda b, **kw: b.post(revoke, **kw),
    ):
        refused = call(member)
        assert refused.status == 403 and "invite" not in refused.json()
        assert made["invite"]["id"] not in refused.text
        assert call(signed_out).status == 401
        agent = call(signed_out, token=token)
        assert agent.status == 403, "an owner's agent token is not a signed-in owner"
    rows = world.owner.browser.get("/api/invites").json()["invites"]
    assert len(rows) == 2 and {r["state"] for r in rows} == {"pending", "accepted"}

    # An agent token cannot join anything, whatever cookie comes with it.
    fresh = invite(world)["secret"]
    late = world.person("late@example.test", "Late Home")
    late_token = owner_token(world, late)
    conn = http.client.HTTPConnection("127.0.0.1", world.port, timeout=10)
    try:
        conn.request(
            "POST",
            "/app/join",
            b"confirm=1",
            headers={
                "Authorization": f"Bearer {late_token}",
                "Cookie": f"{INVITE_COOKIE}={fresh}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        assert conn.getresponse().status == 401
    finally:
        conn.close()
    assert world.db.role(world.owner.workspace, late.id) is None


def test_another_workspaces_owner_sees_and_revokes_nothing(world):
    made = invite(world)
    stranger = world.guest  # owner of their own workspace, no part of the owner's
    listing = stranger.browser.get("/api/invites")
    assert listing.status == 200 and listing.json() == {"invites": []}
    refused = stranger.browser.post(f"/api/invites/{made['invite']['id']}/revoke")
    assert refused.status == 404 and refused.json() == {"error": "Invite not found"}
    assert world.owner.browser.get("/api/invites").json()["invites"][0]["state"] == "pending"
    assert join(world, world.person("late@example.test", "Late Home"), made["secret"]).status == 303


@pytest.mark.parametrize(
    "body",
    [
        {"days": 0},
        {"days": 31},
        {"days": "7"},
        {"days": True},
        {"days": 1.5},
        {"email": "not-an-email"},
        {"email": 7},
        {"email": "a b@example.test"},
        {"role": "owner"},
        {"workspace": "ws_other"},
    ],
)
def test_invalid_invite_requests_are_refused(world, body):
    response = world.owner.browser.post("/api/invites", body)
    assert response.status == 400 and "secret" not in response.text
    assert world.owner.browser.get("/api/invites").json() == {"invites": []}


def test_open_invites_are_capped(world, monkeypatch):
    monkeypatch.setattr(invites, "MAX_PENDING", 2)
    first = invite(world)
    invite(world)
    assert world.owner.browser.post("/api/invites", {}).status == 400
    world.owner.browser.post(f"/api/invites/{first['invite']['id']}/revoke")
    invite(world)


def test_workspace_list_and_switch_stay_inside_own_memberships(world):
    assert join(world, world.guest, invite(world)["secret"]).status == 303
    guest, stranger = world.guest, world.person("stranger@example.test", "Stranger Home")
    listed = guest.browser.get("/api/workspaces").json()["workspaces"]
    assert listed == [
        {
            "id": guest.workspace,
            "name": "Guest Home",
            "role": "owner",
            "personal": True,
            "current": False,
        },
        {
            "id": world.owner.workspace,
            "name": "Owner Home",
            "role": "member",
            "personal": False,
            "current": True,
        },
    ]
    assert "Stranger Home" not in json.dumps(listed)

    for target in (stranger.workspace, "ws_missing", "", None, 7):
        refused = guest.browser.post("/api/workspaces/switch", {"workspace": target})
        assert refused.status == 404 and refused.json() == {"error": "Workspace not found"}
    assert (
        guest.browser.post("/api/workspaces/switch", {"workspace": guest.workspace, "x": 1}).status
        == 404
    )
    assert guest.browser.me().json()["workspace"] == world.owner.workspace

    before = guest.browser.cookies[sessions.COOKIE]
    moved = guest.browser.post("/api/workspaces/switch", {"workspace": guest.workspace})
    assert moved.status == 200 and moved.json() == {"workspace": guest.workspace}
    me = guest.browser.me().json()
    assert me["workspace"] == guest.workspace and me["can_admin"] is True
    assert Browser(world.port, before).me().status == 401

    token = owner_token(world, stranger)
    anonymous = Browser(world.port)
    assert anonymous.get("/api/workspaces").status == 401
    assert anonymous.get("/api/workspaces", token=token).status == 403
    switch = anonymous.post(
        "/api/workspaces/switch", {"workspace": stranger.workspace}, token=token
    )
    assert switch.status == 403
    foreign = guest.browser.post(
        "/api/workspaces/switch",
        {"workspace": world.owner.workspace},
        headers={"Origin": "https://evil.example"},
    )
    assert foreign.status == 403 and guest.browser.me().json()["workspace"] == guest.workspace
