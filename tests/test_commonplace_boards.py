"""Boards through the production gateway, real worker fixture and SQLite."""

import http.client
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from commonplace import events, sessions
from commonplace.boards.service import BoardService, needs_me
from commonplace.extensions import ToolContext, ToolError
from tests._commonplace_helpers import hub_fixture  # noqa: F401

CARD_KEYS = set(
    "number parent board title body status assignee claim_until ask ask_options ask_to return_to flags repo branch pr_url pr_number checks status_line heartbeat_at labels links version created_by created_at updated_at position need message_id".split()
)


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
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def mcp(hub, token, name, **args):
    response = hub.rpc(token, "tools/call", {"name": name, "arguments": args})
    if "error" in response:
        return False, response["error"]["message"]
    return not response["result"].get("isError", False), response["result"]["content"][0]["text"]


def service(hub, principal):
    return BoardService(ToolContext(principal=principal, gateway=hub.gateway))


@pytest.fixture
def env(hub):
    token = hub.owner_token("agent")
    principal = hub.gateway.authenticate(f"Bearer {token}")
    person = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    session = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, "owner@example.com")
    )
    cookie = {"Cookie": f"{sessions.COOKIE}={session}"}
    owner = service(hub, person)
    owner.board_add(slug="work", wing="shared", repo="https://github.com/140er/mempalace.git")
    return token, principal, person, cookie, owner


def test_registration_real_worker_and_profiles(hub, env):
    token, _agent, _person, _cookie, _owner = env
    names = hub.tool_names(token)
    assert {"board_add", "task_take_next", "ask", "decide", "needs_me"} <= names
    slim = hub.token(
        "slim",
        profile="slim",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wing="inbox",
        trust="untrusted",
    )
    names = hub.tool_names(slim)
    assert {"board_list", "task_list", "task_add", "task_show", "task_comment"} <= names
    assert (
        not {"board_add", "task_take_next", "task_update", "task_handoff", "ask", "decide"} & names
    )
    assert hub.supervisor.get(hub.workspace) is not None


def test_card_shape_metadata_and_routes(hub, env):
    token, agent, _person, cookie, _owner = env
    status, created = api(
        hub,
        token,
        "POST",
        "/api/boards/work/cards",
        {"title": "Exact\nwords", "body": "  keep all words\n", "labels": ["backend"]},
    )
    assert status == 201 and set(created) == CARD_KEYS | {"url"}
    assert created["created_by"] == agent.actor
    assert created["repo"] == "github.com/140er/mempalace"
    number = created["number"]
    assert mcp(hub, token, "task_take_next", task=number)[0]
    status, current = api(hub, token, "GET", f"/api/cards/{number}")
    status, updated = api(
        hub,
        token,
        "PATCH",
        f"/api/cards/{number}",
        {
            "version": current["version"],
            "branch": "feat/work",
            "pr": 12,
            "checks": "passing",
            "status_line": "Testing",
        },
    )
    assert status == 200 and set(updated) == CARD_KEYS | {"url"}
    assert updated["pr_url"] == "https://github.com/140er/mempalace/pull/12"
    assert updated["heartbeat_at"] and updated["claim_until"]
    assert updated["body"] == "  keep all words\n" and updated["labels"] == ["backend"]
    ok, output = mcp(hub, token, "task_show", task=number)
    assert ok and all(value in output for value in ("feat/work", "pull/12", "passing", "Testing"))
    assert "wing" not in output and "palace" not in output.replace("mempalace", "")
    assert api(hub, None, "GET", "/api/boards")[0] == 401
    assert api(hub, cookie, "GET", "/api/boards/work/cards")[1]["cards"][0]["number"] == number


MOVES = [
    ("backlog", "todo", "update", False),
    ("todo", "doing", "update", False),
    ("todo", "doing", "take", True),
    ("doing", "todo", "update", False),
    ("doing", "todo", "handoff", True),
    ("doing", "review", "update", True),
    ("doing", "review", "handoff", True),
    ("doing", "done", "update", True),
    ("review", "todo", "update", False),
    ("review", "done", "update", False),
    ("review", "backlog", "update", False),
    ("review", "done", "decide", True),  # a trusted owner agent relays Avery's answer
    ("todo", "review", "handoff", True),
    ("todo", "review", "update", True),
    ("todo", "done", "update", True),
    ("done", "todo", "update", True),
    ("todo", "backlog", "update", True),
    ("doing", "backlog", "update", True),
]


@pytest.mark.parametrize("surface", ["mcp", "rest"])
@pytest.mark.parametrize("old,new,operation,allowed", MOVES)
def test_agent_move_rules(hub, env, surface, old, new, operation, allowed):
    token, agent, _person, _cookie, owner = env
    card = owner.seed_card(board="work", title="move", status=old)
    number = card["number"]
    if old == "doing":
        with owner.store.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET assignee=?,claim_until='2099-01-01T00:00:00Z' WHERE id=?",
                (agent.actor, number),
            )
    if surface == "mcp" or operation == "take":
        if operation == "take":
            ok, _text = mcp(hub, token, "task_take_next", task=number)
        elif operation == "handoff":
            ok, _text = mcp(
                hub,
                token,
                "task_handoff",
                task=number,
                to="owner@example.com" if new == "review" else "anyone",
                note="Verbatim note",
            )
        elif operation == "decide":
            ok, _text = mcp(hub, token, "decide", task=number, choice="yes", then="done")
        else:
            ok, _text = mcp(hub, token, "task_update", task=number, status=new)
    else:
        current = owner.task_show(number)
        if operation == "handoff":
            status, _body = api(
                hub,
                token,
                "POST",
                f"/api/cards/{number}/handoff",
                {
                    "to": "owner@example.com" if new == "review" else "anyone",
                    "note": "Verbatim note",
                },
            )
        elif operation == "decide":
            status, _body = api(
                hub,
                token,
                "POST",
                f"/api/cards/{number}/decide",
                {"version": current["version"], "choice": "yes", "then": "done"},
            )
        else:
            status, _body = api(
                hub,
                token,
                "PATCH",
                f"/api/cards/{number}",
                {"version": current["version"], "status": new},
            )
        ok = status == 200
    assert ok is allowed
    assert owner.task_show(number)["status"] == (new if allowed else old)
    if allowed and operation == "update":
        with owner.store.connection() as conn:
            event = conn.execute(
                "SELECT * FROM task_events WHERE task_id=? AND kind='move' ORDER BY id DESC",
                (number,),
            ).fetchone()
        assert event["stamp_connection"] == agent.actor.removeprefix("conn:")
        assert (event["from_status"], event["to_status"]) == (old, new)
        emitted = [
            e
            for e in events.after(hub.db, hub.workspace)
            if e["kind"] == "card.moved" and e["data"]["number"] == number
        ]
        assert emitted[-1]["data"]["from"] == old
        assert emitted[-1]["data"]["to"] == new


@pytest.mark.parametrize(
    "old,new",
    [
        ("backlog", "todo"),
        ("todo", "doing"),
        ("doing", "todo"),
        ("doing", "review"),
        ("doing", "done"),
        ("todo", "review"),
        ("done", "todo"),
        ("todo", "backlog"),
        ("doing", "backlog"),
        ("done", "backlog"),
    ],
)
def test_person_moves_rest(hub, env, old, new):
    _token, _agent, person, cookie, owner = env
    created = owner.seed_card(board="work", title="Person move", status=old)
    status, current = api(
        hub,
        cookie,
        "PATCH",
        f"/api/cards/{created['number']}",
        {"version": created["version"], "status": new},
    )
    assert status == 200 and current["status"] == new
    if new == "doing":
        assert current["assignee"] == person.actor and current["claim_until"] is None


def test_review_only_decide_and_done_is_never_deleted(hub, env):
    _token, _agent, _person, cookie, owner = env
    created = owner.ask("Proceed?", options=["yes"], board="work")
    assert (
        api(
            hub,
            cookie,
            "PATCH",
            f"/api/cards/{created['number']}",
            {"version": created["version"], "status": "todo"},
        )[0]
        == 403
    )
    status, current = api(
        hub,
        cookie,
        "POST",
        f"/api/cards/{created['number']}/decide",
        {"version": created["version"], "choice": "yes", "then": "done"},
    )
    assert status == 200 and current["status"] == "done"
    assert api(hub, cookie, "DELETE", f"/api/cards/{created['number']}")[0] == 404
    with owner.store.connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            conn.execute("DELETE FROM tasks WHERE id=?", (created["number"],))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM task_events WHERE task_id=?", (created["number"],))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE task_events SET text='replaced' WHERE task_id=?", (created["number"],)
            )


def test_take_next_thread_race_separate_connections(hub, env):
    _token, _agent, _person, _cookie, owner = env
    for number in range(12):
        owner.task_add(title=f"race {number}", board="work")
    tokens = [hub.owner_token(f"racer-{i}") for i in range(4)]
    principals = [hub.gateway.authenticate(f"Bearer {token}") for token in tokens]
    gate = threading.Barrier(4)

    def take(principal):
        worker = service(hub, principal)
        gate.wait(timeout=5)
        result = []
        while current := worker.task_take_next(board="work"):
            result.append(current["number"])
        return result

    with ThreadPoolExecutor(max_workers=4) as executor:
        taken = [n for batch in executor.map(take, principals) for n in batch]
    assert len(taken) == len(set(taken)) == 12
    assert len(owner.task_list(board="work", status="doing")) == 12


def test_lazy_expiry_and_change_counter(hub, env):
    token, _agent, _person, _cookie, owner = env
    card = owner.task_add(title="Expiry", board="work")
    mcp(hub, token, "task_take_next", task=card["number"])
    with owner.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET claim_until='2000-01-01T00:00:00Z' WHERE id=?", (card["number"],)
        )
    changes = owner.store.changes
    owner.board_list()
    current = owner.task_show(card["number"])
    assert current["status"] == "todo" and current["assignee"] is None
    assert len([e for e in current["events"] if e["kind"] == "expire"]) == 1
    assert owner.store.changes > changes
    changes = owner.store.changes
    owner.board_list()
    assert owner.store.changes == changes
    assert len([e for e in events.after(hub.db, hub.workspace) if e["kind"] == "card.expired"]) == 1


def test_ask_decide_return_priority_and_decision_line(hub, env):
    token, agent, person, cookie, owner = env
    first = owner.task_add(title="Earlier free card", board="work")
    card = owner.task_add(title="Needs a decision", board="work")
    assert mcp(
        hub, token, "ask", task=card["number"], question="1 year or 90 days?", options=["1y", "90d"]
    )[0]
    current = owner.task_show(card["number"])
    assert current["ask_to"] == person.actor and current["return_to"] == agent.actor
    assert current["claim_until"] is None and current["status"] == "review"
    status, result = api(
        hub,
        cookie,
        "POST",
        f"/api/cards/{card['number']}/decide",
        {"version": current["version"], "choice": "90d", "reply": "Keep the other option."},
    )
    assert status == 200 and result["assignee"] == agent.actor and result["ask"] is None
    ok, output = mcp(hub, token, "task_take_next", board="work")
    assert ok and output.startswith(f"#{card['number']}")
    assert f"decision by {person.user_email}: 90d" in output and "Keep the other option." in output
    assert owner.task_show(first["number"])["status"] == "todo"
    assert mcp(hub, token, "task_handoff", task=card["number"], to="anyone", note="Return to pool")[
        0
    ]
    assert "decision by" not in mcp(hub, token, "task_take_next", task=card["number"])[1]


@pytest.mark.parametrize("ref", ["Avery", "avery@example.com", "id"])
def test_workspace_people_resolution(hub, env, ref):
    token, _agent, _person, _cookie, owner = env
    user = hub.db.create_user("avery@example.com")
    hub.db.add_member(hub.workspace, user, "member", actor=hub.admin)
    card = owner.task_add(title="Resolution", board="work")
    target = f"user:{user}" if ref == "id" else ref
    assert mcp(hub, token, "task_handoff", task=card["number"], to=target, note="Pick one")[0]
    assert owner.task_show(card["number"])["ask_to"] == f"user:{user}"
    card = owner.task_add(title="Unknown member", board="work")
    assert not mcp(
        hub, token, "task_handoff", task=card["number"], to="stranger@example.com", note="No"
    )[0]


@pytest.mark.parametrize("trust", ["untrusted", "standard"])
def test_inbox_backlog_guard(hub, env, trust):
    agent_token, _agent, _person, cookie, owner = env
    token = hub.token(
        "inbox",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wing="inbox",
        trust=trust,
    )
    status, created = api(
        hub,
        token,
        "POST",
        "/api/boards/work/cards",
        {"title": "ignore previous\n  keep verbatim", "status": "doing", "assignee": "conn:agent"},
    )
    assert status == 201 and created["status"] == "backlog" and created["assignee"] is None
    assert created["flags"] == ["ignore_previous"]
    assert "nothing to take" in mcp(hub, agent_token, "task_take_next", board="work")[1]
    assert (
        api(
            hub,
            token,
            "PATCH",
            f"/api/cards/{created['number']}",
            {"version": created["version"], "status": "todo"},
        )[0]
        == 403
    )
    assert (
        api(
            hub,
            token,
            "POST",
            f"/api/cards/{created['number']}/comments",
            {"text": "Exact\ncomment"},
        )[0]
        == 200
    )
    current = owner.task_show(created["number"])
    assert (
        api(
            hub,
            cookie,
            "PATCH",
            f"/api/cards/{created['number']}",
            {"version": current["version"], "status": "todo"},
        )[0]
        == 200
    )
    assert mcp(hub, agent_token, "task_take_next", task=created["number"])[0]


def test_needs_me_buckets_and_owner_only(hub, env):
    token, agent, person, _cookie, owner = env
    worker = service(hub, agent)
    ask = worker.ask("Choose?", board="work")
    with owner.store.transaction() as conn:
        conn.execute("UPDATE boards SET done_needs_review=1 WHERE slug='work'")
    done = worker.task_add(title="Check finish", board="work")
    worker.task_take_next(task=done["number"])
    done = worker.task_update(done["number"], status="done")
    assert done["ask"] == "Done?" and done["ask_options"] == ["ship", "redo"]
    backlog = worker.task_add(title="Triage", board="work", status="backlog")
    ready = worker.task_add(title="Ready", board="work")
    worker.task_take_next(task=ready["number"])
    ready = worker.task_update(ready["number"], pr=42, checks="passing")
    with owner.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET heartbeat_at='2000-01-01T00:00:00Z' WHERE id=?", (ready["number"],)
        )
    result = needs_me(owner.store, person)
    assert {c["number"] for c in result["asks"]} == {ask["number"]}
    assert {c["number"] for c in result["done_review"]} == {done["number"]}
    assert {c["number"] for c in result["backlog_from_agents"]} == {backlog["number"]}
    assert {c["number"] for c in result["ready_to_merge"]} == {ready["number"]}
    assert {c["number"] for c in result["stale"]} == {ready["number"]}
    assert result["count"] == 4
    assert all(
        set(c) == CARD_KEYS
        for bucket in result.values()
        if isinstance(bucket, list)
        for c in bucket
    )
    assert mcp(hub, token, "needs_me")[0]
    member = hub.db.create_user("member@example.com")
    hub.db.add_member(hub.workspace, member, "member", actor=hub.admin)
    member_token = hub.token(
        "member",
        user=member,
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wings=["shared"],
        trust="standard",
    )
    assert not {"decide", "needs_me"} & hub.tool_names(member_token)
    assert api(hub, member_token, "GET", "/api/needs-me")[0] == 403


def test_forbidden_board_canary_every_surface(hub, env):
    _token, _agent, _person, _cookie, owner = env
    owner.board_add(slug="hidden-canary", wing="private.secret", repo="github.com/secret/canary")
    secret = owner.seed_card(
        board="hidden-canary", title="CANARY-TITLE", body="CANARY-BODY", status="review"
    )
    owner.task_comment(secret["number"], "CANARY-COMMENT")
    scoped = hub.token(
        "scoped-owner",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wings=["shared"],
        trust="trusted",
    )
    forbidden = (
        "hidden-canary",
        "CANARY-TITLE",
        "CANARY-BODY",
        "CANARY-COMMENT",
        "private.secret",
        "secret/canary",
    )
    operations = [
        ("board_list", {}),
        ("task_list", {}),
        ("task_list", {"mine": True}),
        ("task_list", {"board": "hidden-canary"}),
        ("board_add", {"slug": "hidden-canary", "wing": "shared"}),
        ("task_add", {"title": "attempt", "board": "hidden-canary"}),
        ("task_show", {"task": secret["number"]}),
        ("task_comment", {"task": secret["number"], "text": "attempt"}),
        ("task_take_next", {"task": secret["number"]}),
        ("task_take_next", {"board": "hidden-canary"}),
        ("task_update", {"task": secret["number"], "status": "done"}),
        ("task_handoff", {"task": secret["number"], "to": "owner@example.com", "note": "attempt"}),
        ("ask", {"task": secret["number"], "question": "attempt"}),
        ("ask", {"board": "hidden-canary", "question": "attempt"}),
        ("decide", {"task": secret["number"], "reply": "attempt"}),
        ("needs_me", {}),
    ]
    for name, args in operations:
        _ok, output = mcp(hub, scoped, name, **args)
        assert not any(value in output for value in forbidden), (name, output)
    n = secret["number"]
    requests = [
        ("GET", "/api/boards", None),
        ("GET", "/api/needs-me", None),
        ("GET", "/api/boards/hidden-canary/cards", None),
        ("POST", "/api/boards", {"slug": "hidden-canary", "wing": "shared"}),
        ("POST", "/api/boards/hidden-canary/cards", {"title": "attempt"}),
        ("GET", f"/api/cards/{n}", None),
        ("PATCH", f"/api/cards/{n}", {"version": -1, "status": "done"}),
        ("POST", f"/api/cards/{n}/comments", {"text": "attempt"}),
        ("POST", f"/api/cards/{n}/handoff", {"to": "owner@example.com", "note": "attempt"}),
        ("POST", f"/api/cards/{n}/decide", {"version": -1, "reply": "attempt"}),
    ]
    for method, path, body in requests:
        status, result = api(hub, scoped, method, path, body)
        assert status in (200, 404), (path, status, result)
        assert not any(value in json.dumps(result) for value in forbidden), (path, result)
    scoped_principal = hub.gateway.authenticate(f"Bearer {scoped}")
    owner.store.link_drawer(secret["number"], "mem-visible", owner.principal.actor)
    assert owner.store.cards_for_drawer("mem-visible", principal=scoped_principal) == []
    for lookup in (owner.store.board_for_task, owner.store.task_links):
        with pytest.raises(ToolError, match="card not found"):
            lookup(n, principal=scoped_principal)
    with pytest.raises(ToolError, match="card not found"):
        owner.store.link_drawer(
            n, "other-memory", scoped_principal.actor, principal=scoped_principal
        )
    visible = owner.task_add(title="Visible", board="work")
    assert not mcp(hub, scoped, "task_update", task=visible["number"], before=n)[0]


def test_version_conflict_is_atomic_and_returns_current(hub, env):
    token, agent, _person, _cookie, owner = env
    created = service(hub, agent).task_add(title="Versioned", board="work")
    status, current = api(
        hub,
        token,
        "PATCH",
        f"/api/cards/{created['number']}",
        {"version": created["version"], "title": "Current"},
    )
    assert status == 200
    changes = owner.store.changes
    seq = events.latest_seq(hub.db, hub.workspace)
    status, conflict = api(
        hub,
        token,
        "PATCH",
        f"/api/cards/{created['number']}",
        {"version": created["version"], "title": "Stale", "link": "should-not-link"},
    )
    assert status == 409 and conflict["current"] == current
    assert owner.store.changes == changes and events.latest_seq(hub.db, hub.workspace) == seq
    assert owner.store.task_links(created["number"]) == []
    assert (
        api(hub, token, "PATCH", f"/api/cards/{created['number']}", {"title": "No version"})[0]
        == 400
    )


def test_link_api_and_seed_all_statuses(hub, env):
    _token, agent, person, _cookie, owner = env
    for status in ("backlog", "todo", "doing", "review", "done"):
        current = owner.seed_card(
            board="new",
            title=status,
            status=status,
            labels=["seed"],
            repo="github.com/org/repo",
            branch="feat/seed",
            pr_url="https://github.com/org/repo/pull/7",
        )
        assert set(current) == CARD_KEYS and current["status"] == status
        assert current["labels"] == ["seed"] and current["pr_number"] == 7
        changes = owner.store.changes
        repeat = owner.seed_card(board="new", title=status, status="todo", body="ignore repeat")
        assert repeat == current and owner.store.changes == changes
    assert owner.task_list(board="git@github.com:org/repo.git", all_statuses=True)
    card = owner.task_add(title="Linked", board="work")
    owner.store.link_drawer(
        card["number"], "memory_a", agent.actor, title="Exact title", principal=agent
    )
    assert owner.store.task_links(card["number"], principal=person) == [
        {"drawer_id": "memory_a", "title": "Exact title", "invalidated": False}
    ]
    assert owner.store.cards_for_drawer("memory_a", principal=person) == [
        {
            "number": card["number"],
            "title": "Linked",
            "status": "todo",
            "board": "work",
            "wing": "shared",
        }
    ]
    readonly = replace(agent, policy=replace(agent.policy, write_wings=(), write_wing=""))
    with pytest.raises(ToolError, match="cannot change"):
        owner.store.link_drawer(card["number"], "denied", readonly.actor, principal=readonly)


def test_events_after_commit_and_renewal(hub, env):
    token, agent, _person, _cookie, owner = env
    observed = []

    def listener(event):
        if event["workspace_id"] == hub.workspace and event["subject"].startswith("#"):
            # A fresh connection sees the mutation: listeners run after commit.
            with owner.store.connection() as conn:
                observed.append(
                    (
                        event["kind"],
                        conn.execute(
                            "SELECT status FROM tasks WHERE id=?", (int(event["subject"][1:]),)
                        ).fetchone()[0],
                    )
                )

    events.add_listener(listener)
    try:
        current = owner.task_add(title="Renew", board="work")
        mcp(hub, token, "task_take_next", task=current["number"])
        with owner.store.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET claim_until='2099-01-01T00:00:00Z' WHERE id=?",
                (current["number"],),
            )
        before = owner.task_show(current["number"])
        assert mcp(hub, token, "task_comment", task=current["number"], text="Exact progress")[0]
        after = owner.task_show(current["number"])
        assert (
            after["claim_until"] != before["claim_until"]
            and after["version"] == before["version"] + 1
        )
        assert ("card.created", "todo") in observed and ("card.claimed", "doing") in observed
        assert ("card.commented", "doing") in observed
        comment = after["events"][-1]
        assert comment["actor"] == agent.actor and comment["text"] == "Exact progress"
    finally:
        events.remove_listener(listener)


def test_save_lesson_uses_real_memory_worker(hub, env):
    token, _agent, _person, _cookie, owner = env
    current = owner.task_add(title="Lesson", board="work")
    assert mcp(hub, token, "task_take_next", task=current["number"])[0]
    ok, result = mcp(
        hub,
        token,
        "task_update",
        task=current["number"],
        status="done",
        lesson="  Exact lesson\nsecond line",
    )
    assert ok, result
    linked = owner.store.task_links(current["number"])
    assert len(linked) == 1
    memory = hub.gateway.call_memory_tool(
        owner.principal, "mempalace_get_drawer", {"drawer_id": linked[0]["drawer_id"]}
    )
    assert "Exact lesson" in json.dumps(memory)


def test_board_autocreate_write_scope_and_reserved_card(hub, env):
    token, agent, _person, _cookie, owner = env
    assert mcp(hub, token, "task_add", board="auto-created", title="Automatic")[0]
    limited = hub.token(
        "read-only",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wings=["different"],
        trust="standard",
    )
    assert not mcp(hub, limited, "task_add", board="work", title="Denied")[0]
    assert not mcp(hub, limited, "task_add", board="no-auto", title="Denied")[0]
    card = owner.task_add(title="Reserved", board="work", assignee="someone-else")
    assert not mcp(hub, token, "task_take_next", task=card["number"])[0]
    worker = service(hub, agent)
    card = worker.task_add(title="Held", board="work")
    worker.task_take_next(task=card["number"])
    other = hub.owner_token("other")
    assert not mcp(hub, other, "task_update", task=card["number"], title="Steal")[0]
    assert not mcp(hub, other, "task_handoff", task=card["number"], to="anyone", note="Steal")[0]
    assert mcp(hub, other, "task_comment", task=card["number"], text="Allowed comment")[0]


def test_expired_version_conflict_returns_committed_card(hub, env):
    token, _agent, _person, _cookie, owner = env
    created = owner.task_add(title="Expired version", board="work")
    assert mcp(hub, token, "task_take_next", task=created["number"])[0]
    current = owner.task_show(created["number"])
    with owner.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET claim_until='2000-01-01T00:00:00Z' WHERE id=?", (created["number"],)
        )
    status, conflict = api(
        hub,
        token,
        "PATCH",
        f"/api/cards/{created['number']}",
        {"version": current["version"], "title": "Should not apply"},
    )
    assert status == 409 and conflict["current"]["status"] == "todo"
    actual = owner.task_show(created["number"])
    assert {key: actual[key] for key in CARD_KEYS} == {
        k: v for k, v in conflict["current"].items() if k != "url"
    }
    assert actual["title"] == "Expired version"


def test_memory_link_renews_holder_and_never_needs_the_hold(hub, env):
    token, agent, _person, _cookie, owner = env
    created = owner.task_add(title="Linked while doing", board="work")
    assert mcp(hub, token, "task_take_next", task=created["number"])[0]
    with owner.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET claim_until='2099-01-01T00:00:00Z' WHERE id=?", (created["number"],)
        )
    owner.store.link_drawer(created["number"], "memory-holder", agent.actor, principal=agent)
    assert owner.task_show(created["number"])["claim_until"] != "2099-01-01T00:00:00Z"
    other = hub.gateway.authenticate(f"Bearer {hub.owner_token('other-linker')}")
    owner.store.link_drawer(created["number"], "memory-other", other.actor, principal=other)
    assert owner.task_show(created["number"])["status"] == "doing"
    with owner.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET claim_until='2000-01-01T00:00:00Z' WHERE id=?", (created["number"],)
        )
    owner.store.link_drawer(created["number"], "memory-after-expiry", other.actor, principal=other)
    assert owner.task_show(created["number"])["status"] == "todo"


def test_an_agent_never_answers_its_own_question(hub, env):
    token, _agent, _person, _cookie, owner = env
    ok, text = mcp(hub, token, "ask", question="Ship it?", options=["yes", "no"], board="work")
    assert ok, text
    number = int(text.split()[0].lstrip("#"))
    card = owner.task_show(number)
    assert card["status"] == "review"
    ok, text = mcp(hub, token, "decide", task=number, choice="yes")
    assert not ok and "only the owner" in text
    assert owner.task_show(number)["status"] == "review"


def test_reverse_links_include_authoritative_board_and_wing(hub, env):
    _token, agent, person, _cookie, owner = env
    owner.board_add(slug="restricted", wing="private.reverse-links")
    owner.board_add(slug="invalid-theme", wing="shared")
    allowed = owner.task_add(title="Allowed link", board="work")
    denied = owner.task_add(title="Restricted link", board="restricted")
    invalid = owner.task_add(title="Missing theme", board="invalid-theme")
    for item in (allowed, denied, invalid):
        owner.store.link_drawer(item["number"], "reverse-link-memory", person.actor)
    with owner.store.transaction() as conn:
        conn.execute("UPDATE boards SET wing='' WHERE slug='invalid-theme'")
    raw = owner.store.cards_for_drawer("reverse-link-memory")
    assert {item["number"]: (item["board"], item["wing"]) for item in raw} == {
        allowed["number"]: ("work", "shared"),
        denied["number"]: ("restricted", "private.reverse-links"),
        invalid["number"]: ("invalid-theme", ""),
    }
    scoped = replace(agent, policy=replace(agent.policy, read_wings=("shared",)))
    scoped_cards = owner.store.cards_for_drawer("reverse-link-memory", principal=scoped)
    assert [item["number"] for item in scoped_cards] == [allowed["number"]]
    owner_cards = owner.store.cards_for_drawer("reverse-link-memory", principal=person)
    assert {item["number"] for item in owner_cards} == {allowed["number"], denied["number"]}
    # Match the memory producer's fail-closed filter, including for owners.
    assert owner_cards == [
        item for item in raw if person.policy.can_read_wing(item.get("wing", ""))
    ]
    assert not person.policy.can_read_wing({}.get("wing", ""))


def test_cards_carry_short_links_and_people_read_as_names(hub, env):
    token, _agent, _person, cookie, owner = env
    ok, text = mcp(hub, token, "ask", question="Ship?", options=["yes", "no"], board="work")
    assert ok, text
    assert "owner@example.com" in text and "user:" not in text
    assert "/s/" in text
    number = int(text.split()[0].lstrip("#"))
    status, card = api(hub, cookie, "GET", f"/api/cards/{number}")
    assert status == 200 and "/s/" in card["url"]
    status, needs = api(hub, cookie, "GET", "/api/needs-me")
    assert all("url" in c for c in needs["asks"])


def test_handoff_to_a_person_by_name_or_owner(hub, env):
    token, _agent, _person, _cookie, owner = env
    hub.db._conn.execute("UPDATE users SET name='Avery' WHERE id=?", (hub.admin,))
    for to in ("avery", "owner"):
        card = owner.task_add(title=f"hand to {to}", board="work")
        assert mcp(hub, token, "task_take_next", task=card["number"])[0]
        ok, text = mcp(
            hub,
            token,
            "task_handoff",
            task=card["number"],
            to=to,
            note="Pick one",
            options=["a", "b"],
        )
        assert ok, text
        assert owner.task_show(card["number"])["status"] == "review"


@pytest.mark.parametrize(
    "field,value", [("title", "Changed"), ("body", "Changed"), ("labels", ["changed"])]
)
@pytest.mark.parametrize("status", ["backlog", "todo", "doing", "review", "done"])
def test_agent_cannot_edit_another_persons_card(hub, env, field, value, status):
    _token, agent, _person, _cookie, owner = env
    created = owner.seed_card(board="work", title="Original", body="Original body", status=status)
    worker = service(hub, agent)
    for args in ({field: value}, {field: value, "status": "backlog"}):
        with pytest.raises(ToolError):
            worker.task_update(created["number"], **args)
    actual = owner.task_show(created["number"])
    assert actual[field] == created[field] and actual["status"] == status
    changed = owner.task_update(created["number"], **{field: value})
    assert changed[field] == value


@pytest.mark.parametrize("status", ["backlog", "todo", "doing", "done"])
def test_agent_edits_only_own_unclaimed_or_held_cards(hub, env, status):
    _token, agent, _person, _cookie, owner = env
    worker = service(hub, agent)
    creator = owner if status == "doing" else worker
    created = creator.task_add(
        board="work", title="Own", status="backlog" if status == "backlog" else "todo"
    )
    if status in ("doing", "done"):
        worker.task_take_next(task=created["number"])
    if status == "done":
        worker.task_update(created["number"], status="done")
        with pytest.raises(ToolError):
            worker.task_update(created["number"], title="Frozen", body="Frozen", labels=["frozen"])
        with pytest.raises(ToolError):
            worker.task_update(created["number"], status_line="Frozen")
        return
    changed = worker.task_update(
        created["number"], title="Edited", body="Exact body", labels=["edited"]
    )
    assert (changed["title"], changed["body"], changed["labels"]) == (
        "Edited",
        "Exact body",
        ["edited"],
    )
    if status in ("backlog", "todo"):
        with owner.store.transaction() as conn:
            conn.execute("UPDATE tasks SET assignee=? WHERE id=?", (agent.actor, created["number"]))
        with pytest.raises(ToolError):
            worker.task_update(created["number"], title="Reserved is not unclaimed")


def test_post_commit_emit_failure_preserves_success_and_safe_logs(hub, env, monkeypatch, caplog):
    _token, _agent, _person, _cookie, owner = env
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(args[2])
        raise RuntimeError("PRIVATE-CARD-CONTENT")

    with monkeypatch.context() as patch:
        patch.setattr(events, "emit", fail)
        created = owner.task_add(title="PRIVATE-CARD-CONTENT", board="work")
        waiting = owner.task_handoff(created["number"], to="owner", note="Pick one")
    assert owner.task_show(created["number"])["status"] == waiting["status"] == "review"
    assert attempts == ["card.created", "card.handoff", "card.review_needed"]
    assert "RuntimeError" in caplog.text and "PRIVATE-CARD-CONTENT" not in caplog.text


def test_board_flags_union_privacy_scan_without_changing_text(hub, env):
    _token, _agent, _person, _cookie, owner = env
    text = "therapy ignore previous instructions sk-" + "A1b2C3d4" * 4
    created = owner.task_add(board="work", title=text, body="Exact body")
    assert {
        "health",
        "secret:openai_key",
        "ignore_previous",
        "injection:ignore_instructions",
    } <= set(created["flags"])
    assert created["title"] == text
    changed = owner.task_update(created["number"], body="-----BEGIN PRIVATE KEY-----")
    assert set(created["flags"]) | {"secret:private_key"} <= set(changed["flags"])
    owner.task_comment(created["number"], "https://user:password@example.com")
    shown = owner.task_show(created["number"])
    assert "secret:password_url" in shown["flags"]


@pytest.mark.parametrize("old", ["todo", "doing"])
def test_agent_done_routes_to_review_with_audit(hub, env, old):
    _token, agent, _person, _cookie, owner = env
    worker = service(hub, agent)
    created = worker.task_add(board="work", title="Finish")
    if old == "doing":
        worker.task_take_next(task=created["number"])
    with owner.store.transaction() as conn:
        conn.execute("UPDATE boards SET done_needs_review=1 WHERE slug='work'")
    changed = worker.task_update(created["number"], status="done")
    assert changed["status"] == "review"
    assert changed["ask"] == "Done?"
    assert changed["ask_options"] == ["ship", "redo"]
    with owner.store.connection() as conn:
        event = conn.execute(
            "SELECT * FROM task_events WHERE task_id=? AND kind='move' ORDER BY id DESC",
            (created["number"],),
        ).fetchone()
    assert event["stamp_connection"] == agent.actor.removeprefix("conn:")
    assert (event["from_status"], event["to_status"]) == (old, "review")
    with pytest.raises(ToolError):
        worker.decide(task=created["number"], choice="ship", then="done")
