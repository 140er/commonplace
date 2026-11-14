"""Phone chat preserves OAuth scopes and uses the person's existing web rights."""

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from commonplace import events, sessions
from tests.test_commonplace_boards import api, service
from tests.test_commonplace_oauth import REDIRECT, Client
from tests.test_commonplace_person_actions import (
    CHAT,
    grant,
    hub_fixture as hub_fixture,
    member_client,
    phone as phone,
)

# Deliberately low-entropy synthetic input; matches the display redactor's key shape.
SYNTHETIC_KEY = "sk-" + "TEST_ONLY_NOT_A_REAL_KEY"


def test_mobile_oauth_sign_in_receives_person_chat(hub, monkeypatch):
    """Also runs against the development base, where the GET returns 403."""
    monkeypatch.setenv("COMMONPLACE_PHONE_ACTIONS", "on")
    client = Client(hub)
    client.login()
    client.cid = client.register(
        client_name="Mobile client", redirect_uris=["cmmn://auth/callback"]
    )
    owner = service(hub, sessions.person_principal(hub.db, hub.admin, hub.workspace))
    owner.board_add(slug="work", wing="shared")
    pair = grant(client, capabilities=[CHAT])
    assert pair["scope"] == "memory:read"
    assert api(hub, pair["access_token"], "GET", "/api/chat")[0] == 200


def test_phone_reads_and_posts_as_person_without_memory_write(phone):
    pair = grant(phone)
    token = pair["access_token"]
    assert "memory:write" not in pair["scope"].split()
    assert api(phone.hub, token, "GET", "/api/chat")[0] == 200
    assert api(phone.hub, token, "GET", "/api/chat/work?last=50")[0] == 200
    status, message = api(
        phone.hub, token, "POST", "/api/boards/work/chat", {"text": "My exact message"}
    )
    assert status == 201, message
    assert message["actor"] == "user:" + phone.hub.admin
    assert message["stamp_user"] == phone.hub.admin and message["stamp_connection"] is None
    recorded = [
        e for e in events.after(phone.hub.db, phone.hub.workspace) if e["kind"] == "space_message"
    ]
    assert recorded[-1]["actor"] == message["actor"]
    audit = [
        r
        for r in phone.hub.db.audit_entries()
        if r["action"] == "tool.call" and r["tool"] == "space_post"
    ]
    assert len(audit) == 1 and audit[0]["user_id"] == phone.hub.admin
    assert "Mobile client" in audit[0]["reason"]
    app = phone.hub.gateway.authenticate("Bearer " + token)
    assert not app.person and app.auth == "oauth"
    assert set(app.policy.scopes) == {"memory:read"}
    cookie = sessions.start_session(phone.hub.db, phone.hub.admin, phone.hub.workspace)
    web = api(phone.hub, {"Cookie": f"{sessions.COOKIE}={cookie}"}, "GET", "/api/chat/work?last=50")
    mobile = api(phone.hub, token, "GET", "/api/chat/work?last=50")
    assert web == mobile


def listing(phone, pair):
    status, result = api(phone.hub, pair["access_token"], "GET", "/api/chat")
    assert status == 200, result
    return result["projects"]


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/work?last=50", "/api/boards/work/chat"])
@pytest.mark.parametrize("kind", ["ordinary-oauth", "agent", "machine", "worker", "revoked", "off"])
def test_unverified_and_revoked_chat_authority_refused(phone, monkeypatch, path, kind):
    pair = grant(phone, checked=[] if kind == "ordinary-oauth" else None)
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    if kind == "off":
        monkeypatch.delenv("COMMONPLACE_PHONE_ACTIONS")
    elif kind == "agent":
        with phone.hub.db._lock:
            phone.hub.db._conn.execute(
                "UPDATE connections SET started_by='agent' WHERE id=?", (app.connection_id,)
            )
    elif kind in ("machine", "worker"):
        with phone.hub.db._lock:
            phone.hub.db._conn.execute(
                "INSERT INTO runner_connections VALUES (?,?,?)",
                (app.connection_id, "synthetic", kind),
            )
    elif kind == "revoked":
        phone.request(
            "POST", "/oauth/revoke", {"client_id": phone.cid, "token": pair["access_token"]}
        )
    method = "POST" if path.startswith("/api/boards") else "GET"
    status, _ = api(phone.hub, pair["access_token"], method, path, {"text": "Attempt"})
    assert status in (401, 403)


def test_non_phone_oauth_post_keeps_connection_semantics(phone):
    pair = grant(phone, checked=[], oauth_scopes=("memory:read", "memory:write"))
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    # A normal app can post only where its existing memory policy allows it.
    phone.owner.board_add(slug="inbox", wing="inbox")
    status, message = api(
        phone.hub,
        pair["access_token"],
        "POST",
        "/api/boards/inbox/chat",
        {"text": "Connection reply"},
    )
    assert status == 403
    # An explicitly configured ordinary app retains connection-reply semantics.
    row = phone.hub.db.connection(app.connection_id)
    policy = json.loads(row["policy"])
    policy.update(tools="standard", trust="trusted", write_wing="", write_wings=["inbox"])
    with phone.hub.db._lock:
        phone.hub.db._conn.execute(
            "UPDATE connections SET policy=? WHERE id=?", (json.dumps(policy), app.connection_id)
        )
    status, message = api(
        phone.hub,
        pair["access_token"],
        "POST",
        "/api/boards/inbox/chat",
        {"text": "Connection reply"},
    )
    assert status == 201 and message["actor"] == app.actor
    assert message["stamp_connection"] == app.connection
    assert api(phone.hub, pair["access_token"], "GET", "/api/chat")[0] == 403


def test_chat_capability_does_not_authorize_settings_status_search_or_other_aliases(phone):
    pair = grant(phone, capabilities=[CHAT])
    for path in ("/api/chat-settings", "/api/chat/work/status", "/api/chat/work/search?q=one"):
        assert api(phone.hub, pair["access_token"], "GET", path)[0] == 403
    assert (
        api(
            phone.hub,
            pair["access_token"],
            "POST",
            "/api/spaces/work/chat",
            {"text": "Not this alias"},
        )[0]
        == 403
    )
    assert (
        api(
            phone.hub,
            pair["access_token"],
            "POST",
            "/api/cards/1/decide",
            {"reply": "Not authorized"},
        )[0]
        == 403
    )


def test_member_chat_visibility_and_cross_workspace_match_web(phone):
    member_client(phone)
    pair = grant(phone, capabilities=[CHAT])
    phone.owner.board_add(slug="private-work", wing="private")
    assert (
        api(
            phone.hub,
            pair["access_token"],
            "POST",
            "/api/boards/work/chat",
            {"text": "Member message"},
        )[0]
        == 201
    )
    for method, path in (
        ("GET", "/api/chat/private-work?last=50"),
        ("POST", "/api/boards/private-work/chat"),
    ):
        assert api(phone.hub, pair["access_token"], method, path, {"text": "Forbidden"})[0] == 404
    foreign = phone.hub.db.create_workspace("foreign", phone.hub.admin)
    other = service(phone.hub, sessions.person_principal(phone.hub.db, phone.hub.admin, foreign))
    other.board_add(slug="foreign-work", wing="shared")
    for method, path in (
        ("GET", "/api/chat/foreign-work?last=50"),
        ("POST", "/api/boards/foreign-work/chat"),
    ):
        assert api(phone.hub, pair["access_token"], method, path, {"text": "Forbidden"})[0] == 404
    assert [p["slug"] for p in listing(phone, pair)] == ["work"]


def test_phone_chat_uses_existing_rate_limit(phone, monkeypatch):
    from commonplace import ratelimit

    pair = grant(phone, capabilities=[CHAT])
    monkeypatch.setattr(ratelimit, "AUTHENTICATED", ratelimit.Limit("phone-chat-test", 1, 3600))
    phone.hub.gateway.rate_limits = True
    assert api(phone.hub, pair["access_token"], "GET", "/api/chat")[0] == 200
    assert (
        api(phone.hub, pair["access_token"], "POST", "/api/boards/work/chat", {"text": "Limited"})[
            0
        ]
        == 429
    )


def test_list_metadata_is_phone_only_and_null_without_evidence(phone):
    pair = grant(phone, capabilities=[CHAT])
    projects = listing(phone, pair)
    assert projects == [
        {"slug": "work", "name": "work", "orchestrator": None, "status": None, "last_message": None}
    ]
    cookie = {"Cookie": f"{sessions.COOKIE}={phone.cookies[sessions.COOKIE]}"}
    status, web = api(phone.hub, cookie, "GET", "/api/chat")
    assert status == 200
    assert set(web["projects"][0]) == {"slug", "name", "orchestrator"}


@pytest.mark.parametrize(
    "age,expected", [(None, "unused"), (30, "working"), (600, "idle"), (3600, "quiet")]
)
def test_activity_uses_web_chat_classifier(phone, age, expected):
    from commonplace.console import authority

    pair = grant(phone, capabilities=[CHAT])
    token = phone.hub.owner_token("coordinator")
    app = phone.hub.db.authenticate_token(token)
    phone.owner.board_set("work", app.actor)
    stamp = (
        None
        if age is None
        else (datetime.now(timezone.utc) - timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    with phone.hub.db._lock:
        phone.hub.db._conn.execute(
            "UPDATE connections SET last_used_at=? WHERE id=?", (stamp, app.connection_id)
        )
    assert listing(phone, pair)[0]["status"] == expected
    assert expected == authority.status(stamp, datetime.now(timezone.utc))


def test_previews_are_redacted_plain_bounded_and_use_visible_latest_message(phone, monkeypatch):
    from commonplace.chat import store
    from commonplace.chat.phone_list import PREVIEW_CHARS

    pair = grant(phone, capabilities=[CHAT])
    # A normal connection reply creates no automatic front receipt.
    app = phone.hub.db.authenticate_token(phone.hub.owner_token("speaker"))
    monkeypatch.setattr("commonplace.boards.service.now", lambda: "2026-01-01T00:00:00Z")
    message = service(phone.hub, app).space_post(
        "work", "<b>Latest</b> " + SYNTHETIC_KEY + " plain " + "z" * 300
    )
    preview = listing(phone, pair)[0]["last_message"]
    assert preview["author_kind"] == "bot" and preview["at"] == "2026-01-01T00:00:00Z"
    assert "<b>" not in preview["text"] and len(preview["text"]) == PREVIEW_CHARS
    assert SYNTHETIC_KEY not in preview["text"]
    assert preview["text"] == ("Latest [redacted:openai_key] plain " + "z" * 300)[:PREVIEW_CHARS]
    replies = store.open_replies(phone.hub.gateway, phone.hub.workspace)
    monkeypatch.setattr(store, "_stamp", lambda: "2026-01-01T00:00:00.500000+00:00")
    replies.add(
        board="work",
        message_id=message["id"],
        for_user=phone.hub.admin,
        kind="answer",
        code="ok",
        text="My private answer",
        redacted=(),
        model=None,
    )
    assert listing(phone, pair)[0]["last_message"]["text"] == "My private answer"
    # Fractional times compare as instants, not as mixed UTC string formats.
    assert listing(phone, pair)[0]["last_message"]["at"] == "2026-01-01T00:00:00.500000Z"


@pytest.mark.parametrize("source", ["board", "reply"])
@pytest.mark.parametrize("shape", ["tag", "entity", "cut"])
def test_preview_normalizes_before_redaction_and_keeps_cut_marker_whole(phone, source, shape):
    from commonplace import redact
    from commonplace.chat import store
    from commonplace.chat.phone_list import PREVIEW_CHARS

    marker = "[redacted:openai_key]"
    assert redact.redact(SYNTHETIC_KEY) == (marker, ["openai_key"])
    if shape == "tag":
        text, expected = SYNTHETIC_KEY[:3] + "<i></i>" + SYNTHETIC_KEY[3:], marker
    elif shape == "entity":
        text, expected = "s&#107;" + SYNTHETIC_KEY[2:], marker
    else:
        prefix = "p" * (PREVIEW_CHARS - 10) + " "
        text, expected = prefix + SYNTHETIC_KEY, prefix
        assert len(prefix) < PREVIEW_CHARS < len(text)
        assert len(prefix) < PREVIEW_CHARS < len(prefix + marker)

    pair = grant(phone, capabilities=[CHAT])
    app = phone.hub.db.authenticate_token(phone.hub.owner_token("speaker"))
    message = service(phone.hub, app).space_post("work", text if source == "board" else "Seed")
    if source == "reply":
        store.open_replies(phone.hub.gateway, phone.hub.workspace).add(
            board="work",
            message_id=message["id"],
            for_user=phone.hub.admin,
            kind="answer",
            code="ok",
            text=text,
            redacted=(),
            model=None,
        )
    preview = listing(phone, pair)[0]["last_message"]["text"]
    assert preview == expected
    assert SYNTHETIC_KEY not in preview and "TEST_ONLY_NOT_A_REAL_KEY" not in preview
    assert len(preview) <= PREVIEW_CHARS


def test_hidden_project_other_person_reply_and_private_thread_do_not_change_list(phone):
    from commonplace.bots.store import open_bots
    from commonplace.chat import store

    member = member_client(phone)
    pair = grant(phone, capabilities=[CHAT])
    speaker = phone.hub.db.authenticate_token(phone.hub.owner_token("speaker"))
    message = service(phone.hub, speaker).space_post("work", "Visible shared message")
    before = listing(phone, pair)
    phone.owner.board_add(slug="private-project", wing="private")
    phone.owner.space_post("private-project", "Unreadable project sentinel")
    replies = store.open_replies(phone.hub.gateway, phone.hub.workspace)
    replies.add(
        board="work",
        message_id=message["id"],
        for_user=phone.hub.admin,
        kind="answer",
        code="ok",
        text="Other person's private reply sentinel",
        redacted=(),
        model=None,
    )
    bots = open_bots(phone.hub.gateway, phone.hub.workspace)
    bot = bots.create(phone.hub.admin, "Private conversation", kind="project", board="work")
    bots.add_user_message(
        bot["id"], phone.hub.admin, "Private thread sentinel", "private-thread-request"
    )
    assert listing(phone, pair) == before
    assert [p["slug"] for p in before] == ["work"]
    assert before[0]["status"] is None
    assert before[0]["last_message"]["text"] == "Visible shared message"
    assert member != phone.hub.admin


def test_batched_list_query_count_stays_bounded_with_fifty_projects(phone, monkeypatch):
    from commonplace.boards.store import BoardStore
    from commonplace.chat import store

    pair = grant(phone, capabilities=[CHAT])
    # Prime both sources before counting, including an own private front reply.
    speaker = phone.hub.db.authenticate_token(phone.hub.owner_token("speaker"))
    message = service(phone.hub, speaker).space_post("work", "Recorded message")
    replies = store.open_replies(phone.hub.gateway, phone.hub.workspace)
    replies.add(
        board="work",
        message_id=message["id"],
        for_user=phone.hub.admin,
        kind="answer",
        code="ok",
        text="Own reply",
        redacted=(),
        model=None,
    )
    listing(phone, pair)
    statements = []

    def record(sql):
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(sql)

    original = BoardStore.connection

    @contextmanager
    def traced(self):
        with original(self) as conn:
            conn.set_trace_callback(record)
            yield conn

    monkeypatch.setattr(BoardStore, "connection", traced)
    phone.hub.db._conn.set_trace_callback(record)
    replies._connection().set_trace_callback(record)
    listing(phone, pair)
    one = len(statements)
    for n in range(49):
        phone.owner.board_add(slug=f"project-{n}", wing="shared")
    listing(phone, pair)  # Prime any one-time migration metadata after fixture writes.
    statements.clear()
    projects = listing(phone, pair)
    fifty = len(statements)
    assert len(projects) == 50
    assert fifty == one and fifty < 35, (one, fifty)
    assert sum("WITH ranked AS" in q for q in statements) == 2


def test_chat_audit_failure_rolls_back_person_message(phone, monkeypatch):
    pair = grant(phone, capabilities=[CHAT])
    original = phone.hub.db.audit

    def fail(**fields):
        if fields["action"] == "tool.call" and fields.get("tool") == "space_post":
            raise RuntimeError("audit unavailable")
        return original(**fields)

    monkeypatch.setattr(phone.hub.db, "audit", fail)
    status, _ = api(
        phone.hub,
        pair["access_token"],
        "POST",
        "/api/boards/work/chat",
        {"text": "Uncommitted message"},
    )
    assert status == 500
    assert phone.owner.space_read("work")["messages"] == []


@pytest.mark.parametrize("denied", [False, True])
def test_phone_chat_uses_checked_rest_write_before_mutation(phone, monkeypatch, denied):
    from commonplace.person_actions import PersonCardService
    from commonplace.rules import enforce

    pair = grant(phone, capabilities=[CHAT])
    checked = []
    posted = []
    original = enforce.check
    original_post = PersonCardService.space_post

    def check(ctx, tool, supplied):
        checked.append((ctx.principal.person, ctx.principal.user_id, tool, supplied))
        return (
            enforce.Check("test refusal", "Refresh required", True)
            if denied
            else original(ctx, tool, supplied)
        )

    def post(service, *args, **kwargs):
        assert checked
        posted.append(True)
        return original_post(service, *args, **kwargs)

    monkeypatch.setattr(enforce, "check", check)
    monkeypatch.setattr(PersonCardService, "space_post", post)
    status, result = api(
        phone.hub,
        {"Authorization": "Bearer " + pair["access_token"], "Commonplace-Rules-Seq": "7"},
        "POST",
        "/api/boards/work/chat",
        {"text": "Checked message"},
    )
    assert status == (428 if denied else 201), result
    assert checked == [(True, phone.hub.admin, "space_post", "7")]
    assert len(posted) == (0 if denied else 1)
    messages = phone.owner.space_read("work")["messages"]
    assert len(messages) == (0 if denied else 1)
    rows = [
        r
        for r in phone.hub.db.audit_entries()
        if r["action"] == "tool.call" and r["tool"] == "space_post"
    ]
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    assert len(rows) == 1
    assert rows[0]["decision"] == ("deny" if denied else "ok")
    assert rows[0]["user_id"] == phone.hub.admin
    assert rows[0]["connection_id"] == app.connection_id and rows[0]["auth"] == "oauth"
    assert "Mobile client" in rows[0]["reason"] and "transport=rest" in rows[0]["reason"]


def test_chat_post_cannot_choose_another_author(phone):
    pair = grant(phone, capabilities=[CHAT])
    status, _ = api(
        phone.hub,
        pair["access_token"],
        "POST",
        "/api/boards/work/chat",
        {"text": "Attempt", "user_id": "other"},
    )
    assert status == 400
    assert phone.owner.space_read("work")["messages"] == []


def test_non_native_client_cannot_acquire_phone_capability_through_forged_checkbox(phone):
    import re

    cid = phone.register(client_name="Ordinary app")
    path = phone.authorize_path(cid, scope="memory:read")
    status, _, html = phone.request("GET", path)
    assert status == 200 and "grant_person_chat" not in html
    request = re.search(r'name="request" value="([^"]+)"', html)[1]
    status, headers, _ = phone.request(
        "POST", path, {"request": request, "decision": "allow", "grant_person_chat": "1"}
    )
    assert status == 303
    code = parse_qs(urlsplit(headers["Location"]).query)["code"][0]
    status, _, text = phone.exchange(cid, code, redirect_uri=REDIRECT)
    assert status == 200
    assert api(phone.hub, json.loads(text)["access_token"], "GET", "/api/chat")[0] == 403
