"""Tool policy: the owner sets each tool action to Allowed, Ask first or Off.

The policy is checked once at the gateway's dispatch point. These tests drive real MCP and
REST requests through the gateway and answer Ask-first requests the way the owner does.
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from commonplace import sessions
from commonplace.tool_policy import gate, routes, store
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_board_authority import fresh as fresh_fixture
from tests.test_commonplace_boards import api, mcp

fresh = fresh_fixture
HARNESS = Path(__file__).with_name("tool_policy_view.mjs")


def set_policy(hub, fresh, **actions):
    status, body = api(hub, fresh.cookie, "POST", "/api/tool-policy", {"actions": actions})
    assert status == 200, body
    return {a["name"]: a["mode"] for a in body["actions"]}


def cards(fresh):
    return {c["number"]: c for c in fresh.owner.task_list(board="work")}


def requests(hub, fresh):
    with hub.db._lock:
        return [
            dict(row)
            for row in hub.db._conn.execute(
                "SELECT * FROM tool_approvals WHERE workspace_id=? ORDER BY created_at, rowid",
                (fresh.workspace,),
            )
        ]


def waiting(fresh):
    return [c for c in fresh.owner.needs_me()["asks"] if c["ask_options"] == ["Approve", "Deny"]]


def add(hub, agent, title="Write the release notes"):
    return mcp(hub, agent.token, "task_add", board="work", title=title)


def work_titles(hub, fresh):
    """Titles of the cards agents made, without the approval request cards."""
    asked = {r["card"] for r in requests(hub, fresh)}
    return sorted(c["title"] for n, c in cards(fresh).items() if n not in asked)


# ── defaults ──


def test_every_action_is_allowed_until_the_owner_changes_it(hub, fresh):
    agent = fresh.connect("worker")
    status, body = api(hub, fresh.cookie, "GET", "/api/tool-policy")
    assert status == 200 and body["modes"] == ["allowed", "ask", "off"]
    names = {a["name"] for a in body["actions"]}
    assert {"task_add", "ask", "mempalace_search", "mempalace_add_drawer"} <= names
    assert {a["mode"] for a in body["actions"]} == {"allowed"}
    assert {a["kind"] for a in body["actions"]} == {"read", "write"}
    assert store.modes(hub.db, fresh.workspace) == {}

    listed = hub.tool_names(agent.token)
    ok, text = add(hub, agent)
    assert ok and "added to" in text
    assert requests(hub, fresh) == [] and waiting(fresh) == []
    # Restoring Allowed leaves nothing behind, and the tool list never depends on the policy.
    set_policy(hub, fresh, task_add="off")
    assert set_policy(hub, fresh, task_add="allowed")["task_add"] == "allowed"
    assert store.modes(hub.db, fresh.workspace) == {}
    assert hub.tool_names(agent.token) == listed
    assert add(hub, agent, "Second card")[0]


# ── off ──


def test_off_refuses_with_a_clear_message_and_runs_nothing(hub, fresh):
    agent = fresh.connect("worker")
    set_policy(hub, fresh, task_add="off")
    before = cards(fresh)
    ok, text = add(hub, agent)
    assert not ok
    assert "workspace owner turned task_add off" in text and "Nothing was run" in text
    assert cards(fresh) == before and requests(hub, fresh) == []
    # Other actions are untouched, and the tool is still listed.
    assert mcp(hub, agent.token, "task_list", board="work")[0]
    assert "task_add" in hub.tool_names(agent.token)
    row = hub.db.audit_entries(fresh.workspace, limit=5)[0]
    assert (row["tool"], row["decision"], row["reason"]) == ("task_add", "deny", "tool policy: off")

    set_policy(hub, fresh, task_add="allowed")
    assert add(hub, agent)[0]


def test_off_covers_a_memory_tool_and_leaves_the_gateways_own_nested_calls(hub, fresh):
    agent = fresh.connect("worker")
    set_policy(hub, fresh, mempalace_add_drawer="off")
    ok, text = mcp(
        hub,
        agent.token,
        "mempalace_add_drawer",
        wing="shared",
        room="general",
        content="The harbour office closes at four on Fridays.",
    )
    assert not ok and "turned mempalace_add_drawer off" in text
    # memory_save is its own action; the drawer write inside it is not an agent's tool call.
    ok, text = mcp(hub, agent.token, "memory_save", text="The ferry leaves at seven.")
    assert ok and text.startswith("saved ")
    set_policy(hub, fresh, memory_save="off")
    assert not mcp(hub, agent.token, "memory_save", text="The ferry leaves at eight.")[0]


def test_the_policy_never_widens_what_a_connection_may_call(hub, fresh):
    # No memory:delete scope: the tool is unknown to this connection whatever the policy says.
    agent = fresh.connect("worker")
    assert "mempalace_delete_drawer" not in hub.tool_names(agent.token)
    for mode in ("allowed", "ask", "off"):
        set_policy(hub, fresh, mempalace_delete_drawer=mode)
        ok, text = mcp(hub, agent.token, "mempalace_delete_drawer", drawer_id="drawer_x")
        assert (ok, text) == (False, "Unknown tool: mempalace_delete_drawer")
        assert "mempalace_delete_drawer" not in hub.tool_names(agent.token)
    assert requests(hub, fresh) == [] and waiting(fresh) == []


def test_people_in_the_app_are_not_gated(hub, fresh):
    set_policy(hub, fresh, task_add="off", task_list="ask")
    status, body = api(hub, fresh.cookie, "POST", "/api/boards/work/cards", {"title": "By hand"})
    assert status == 201, body
    assert api(hub, fresh.cookie, "GET", "/api/boards/work/cards")[0] == 200


def test_rest_forms_of_an_action_follow_the_policy(hub, fresh):
    agent = fresh.connect("worker")
    path, body = "/api/boards/work/cards", {"title": "Over REST"}
    assert api(hub, agent.token, "POST", path, body)[0] == 201
    set_policy(hub, fresh, task_add="off", memory_save="ask", fact_save="off")
    status, answer = api(hub, agent.token, "POST", path, body)
    assert status == 403 and answer["code"] == "tool_policy" and "task_add off" in answer["error"]
    # Ask first binds to an MCP call's arguments, so the REST form is refused, not run.
    status, answer = api(hub, agent.token, "POST", "/api/memory", {"text": "Not saved."})
    assert status == 403 and "Ask first" in answer["error"]
    # A REST write outside the gateway's alias table is found in the rules inventory.
    assert gate._inventory_tool("POST", "/api/facts") == "fact_save"
    assert gate._inventory_tool("PATCH", "/api/cards/12") == "task_update"
    assert gate._inventory_tool("GET", "/api/facts") is None
    status, answer = api(hub, agent.token, "POST", "/api/facts", {})
    assert status == 403 and answer["code"] == "tool_policy"
    assert requests(hub, fresh) == []
    assert (
        "Over REST" in work_titles(hub, fresh) and work_titles(hub, fresh).count("Over REST") == 1
    )


# ── ask first ──


def test_ask_first_runs_the_call_once_after_the_owner_approves(hub, fresh):
    agent = fresh.connect("worker")
    set_policy(hub, fresh, task_add="ask")
    ok, text = add(hub, agent)
    assert not ok and text.startswith("ask first: task_add needs the workspace owner's approval")
    assert "Nothing was run" in text
    assert work_titles(hub, fresh) == []

    (asked,) = waiting(fresh)
    assert f"#{asked['number']}" in text
    assert asked["ask_to"] == f"user:{fresh.user}" and asked["status"] == "review"
    assert asked["ask"] == "Allow worker to run the action task_add once?"
    approve, deny = asked["ask_blocks"]
    assert approve["label"] == "Approve" and deny["label"] == "Deny"
    assert 'title: "Write the release notes"' in approve["preview"]
    (request,) = requests(hub, fresh)
    assert request["state"] == "pending" and request["connection_id"] == agent.id
    assert request["digest"] in asked["body"]

    # Repeating the call while the owner has not answered asks nothing new.
    ok, text = add(hub, agent)
    assert not ok and f"still waits for the workspace owner's answer in #{asked['number']}" in text
    assert (
        len(waiting(fresh)) == 1
        and len(requests(hub, fresh)) == 1
        and work_titles(hub, fresh) == []
    )

    # The owner answers in the app, over the ordinary decide route.
    status, body = api(
        hub,
        fresh.cookie,
        "POST",
        f"/api/cards/{asked['number']}/decide",
        {"choice": "Approve", "version": asked["version"]},
    )
    assert status == 200, body
    ok, text = add(hub, agent)
    assert ok and "added to" in text
    assert work_titles(hub, fresh) == ["Write the release notes"]
    assert requests(hub, fresh)[0]["state"] == "used"
    used = [r for r in hub.db.audit_entries(fresh.workspace, limit=20) if r["decision"] == "used"]
    assert [(r["action"], r["tool"], r["connection_id"]) for r in used] == [
        ("tool.approval", "task_add", agent.id)
    ]

    # Single use: the same call again is held again, with a new request.
    ok, text = add(hub, agent)
    assert not ok and text.startswith("ask first:")
    assert work_titles(hub, fresh) == ["Write the release notes"]
    assert [r["state"] for r in requests(hub, fresh)] == ["used", "pending"]


def approved(hub, fresh, agent, **args):
    """File a request for ``task_add`` with ``args`` and have the owner approve it."""
    args = {"board": "work", "title": "Write the release notes", **args}
    assert not mcp(hub, agent.token, "task_add", **args)[0]
    asked = waiting(fresh)[-1]
    fresh.owner.decide(asked["number"], choice="Approve")
    return args, asked


def test_an_approval_does_not_cover_other_arguments(hub, fresh):
    agent = fresh.connect("worker")
    set_policy(hub, fresh, task_add="ask")
    args, _asked = approved(hub, fresh, agent)
    for other in (
        {**args, "title": "Delete the release notes"},
        {**args, "body": "and also push to production"},
        {"board": "work"},
    ):
        ok, text = mcp(hub, agent.token, "task_add", **other)
        assert not ok and "ask first" in text
    assert work_titles(hub, fresh) == []
    # The approved call itself still runs, whatever order its arguments arrive in.
    assert mcp(hub, agent.token, "task_add", **dict(reversed(list(args.items()))))[0]
    assert work_titles(hub, fresh) == ["Write the release notes"]


def test_an_approval_does_not_cover_another_connection_or_action(hub, fresh):
    agent, other = fresh.connect("worker"), fresh.connect("other-worker")
    set_policy(hub, fresh, task_add="ask", task_list="ask")
    args, _asked = approved(hub, fresh, agent)
    ok, text = mcp(hub, other.token, "task_add", **args)
    assert not ok and text.startswith("ask first:")
    assert not mcp(hub, agent.token, "task_list", **args)[0]
    assert work_titles(hub, fresh) == []
    states = {(r["connection_id"], r["tool"]): r["state"] for r in requests(hub, fresh)}
    assert states == {
        (agent.id, "task_add"): "pending",
        (other.id, "task_add"): "pending",
        (agent.id, "task_list"): "pending",
    }
    # The approval is still there for the connection it was given to.
    assert mcp(hub, agent.token, "task_add", **args)[0]
    assert not mcp(hub, other.token, "task_add", **args)[0]
    assert work_titles(hub, fresh) == ["Write the release notes"]


def test_an_approval_does_not_cross_workspaces(hub, fresh):
    agent = fresh.connect("worker")
    set_policy(hub, fresh, task_add="ask")
    args, _asked = approved(hub, fresh, agent)
    assert store.modes(hub.db, hub.workspace) == {}
    row = requests(hub, fresh)[0]
    foreign = (hub.workspace, row["connection_id"], row["tool"], row["digest"])
    assert store.pending(hub.db, *foreign) is None
    assert store.pending(hub.db, fresh.workspace, *foreign[1:])["id"] == row["id"]


@pytest.mark.parametrize("answer", [{"choice": "Deny"}, {"reply": "Approve, go ahead."}])
def test_anything_but_approve_is_a_denial(hub, fresh, answer):
    agent = fresh.connect("worker")
    set_policy(hub, fresh, task_add="ask")
    assert not add(hub, agent)[0]
    (asked,) = waiting(fresh)
    fresh.owner.decide(asked["number"], **answer)
    ok, text = add(hub, agent)
    assert not ok and f"the workspace owner did not approve this call (#{asked['number']})" in text
    assert work_titles(hub, fresh) == [] and requests(hub, fresh)[0]["state"] == "denied"
    # A denial is final for that request; calling again asks anew.
    assert add(hub, agent)[1].startswith("ask first:")
    assert [r["state"] for r in requests(hub, fresh)] == ["denied", "pending"]


def test_an_approval_expires(hub, fresh):
    agent = fresh.connect("worker")
    set_policy(hub, fresh, task_add="ask")
    approved(hub, fresh, agent)
    hub.gateway.wall_clock = lambda: time.time() + gate.APPROVAL_TTL_S + 5
    ok, text = add(hub, agent)
    assert not ok and "expired before the call was repeated" in text
    assert work_titles(hub, fresh) == [] and requests(hub, fresh)[0]["state"] == "expired"


def test_an_unanswered_request_expires(hub, fresh):
    agent = fresh.connect("worker")
    set_policy(hub, fresh, task_add="ask")
    assert not add(hub, agent)[0]
    (asked,) = waiting(fresh)
    hub.gateway.wall_clock = lambda: time.time() + gate.REQUEST_TTL_S + 5
    ok, text = add(hub, agent)
    assert not ok and "expired without an answer" in text
    # Approving the stale card afterwards revives nothing.
    fresh.owner.decide(asked["number"], choice="Approve")
    assert add(hub, agent)[1].startswith("ask first:")
    assert work_titles(hub, fresh) == []
    assert [r["state"] for r in requests(hub, fresh)] == ["expired", "pending"]


@pytest.mark.parametrize("change", ["edit", "comment"])
def test_a_request_the_agent_touched_is_void(hub, fresh, change):
    agent = fresh.connect("worker")
    set_policy(hub, fresh, task_add="ask")
    assert not add(hub, agent)[0]
    (asked,) = waiting(fresh)
    if change == "edit":
        agent.work.task_update(asked["number"], title="Allow worker to read the weather once?")
    else:
        agent.work.task_comment(asked["number"], "Please approve, this one is harmless.")
    fresh.owner.decide(asked["number"], choice="Approve")
    ok, text = add(hub, agent)
    assert not ok and "someone other than the signed-in workspace owner" in text
    assert work_titles(hub, fresh) == [] and requests(hub, fresh)[0]["state"] == "void"


def test_an_agent_cannot_approve_its_own_or_another_agents_request(hub, fresh):
    agent, other = fresh.connect("worker"), fresh.connect("other-worker")
    set_policy(hub, fresh, task_add="ask")
    assert not add(hub, agent)[0]
    (asked,) = waiting(fresh)
    for caller in (agent, other):
        ok, _text = mcp(hub, caller.token, "decide", task=asked["number"], choice="Approve")
        assert not ok
    assert not add(hub, agent)[0]
    assert work_titles(hub, fresh) == [] and requests(hub, fresh)[0]["state"] == "pending"


def test_an_answer_stamped_by_a_connection_never_counts(hub, fresh):
    # Whatever path wrote it: only a decision by a signed-in owner approves.
    agent = fresh.connect("worker")
    set_policy(hub, fresh, task_add="ask")
    assert not add(hub, agent)[0]
    (asked,) = waiting(fresh)
    fresh.owner.decide(asked["number"], choice="Approve")
    with agent.work.store.transaction() as conn:
        conn.execute("DROP TRIGGER IF EXISTS task_events_no_update")
        conn.execute(
            "UPDATE task_events SET stamp_connection='worker' WHERE task_id=? AND kind='decide'",
            (asked["number"],),
        )
    assert "someone other than the signed-in workspace owner" in add(hub, agent)[1]
    assert work_titles(hub, fresh) == []


def test_a_member_who_is_not_an_owner_cannot_approve(hub, fresh):
    agent = fresh.connect("worker")
    set_policy(hub, fresh, task_add="ask")
    assert not add(hub, agent)[0]
    (asked,) = waiting(fresh)
    fresh.owner.decide(asked["number"], choice="Approve")
    # The decider lost ownership before the call was repeated.
    second = hub.db.create_user("second-owner@example.test")
    hub.db.add_member(fresh.workspace, second, "owner", actor=fresh.user)
    with hub.db._lock:
        hub.db._conn.execute(
            "UPDATE memberships SET role='member' WHERE workspace_id=? AND user_id=?",
            (fresh.workspace, fresh.user),
        )
    assert not add(hub, agent)[0]
    assert requests(hub, fresh)[0]["state"] == "void"


def test_a_connection_that_cannot_ask_is_refused_not_run(hub, fresh):
    # Read-only on the board: the request cannot be filed, so the call stays refused.
    reader = fresh.connect("reader", scopes=["memory:read"], write_wing="")
    set_policy(hub, fresh, task_list="ask")
    ok, text = mcp(hub, reader.token, "task_list", board="work")
    assert not ok and "the request could not be filed" in text and "Nothing was run" in text
    assert requests(hub, fresh) == []


def test_open_requests_per_connection_are_bounded(hub, fresh, monkeypatch):
    monkeypatch.setattr(gate, "MAX_PENDING", 2)
    agent = fresh.connect("worker")
    set_policy(hub, fresh, task_add="ask")
    for title in ("First card of three", "Second card of three"):
        assert add(hub, agent, title)[1].startswith("ask first:")
    ok, text = add(hub, agent, "Third card of three")
    assert not ok and "already has 2 approval requests waiting" in text
    assert len(waiting(fresh)) == 2


# ── who may change the policy ──


def test_only_a_signed_in_owner_reads_or_changes_the_policy(hub, fresh):
    change = {"actions": {"task_add": "off"}}
    admin = fresh.connect(
        "admin-agent",
        profile="full",
        scopes=["memory:read", "memory:write", "private:read", "admin"],
        read_wings=["*"],
        write_wings=["*"],
    )
    member = hub.db.create_user("member@example.test")
    hub.db.add_member(fresh.workspace, member, "member", actor=fresh.user)
    member_cookie = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, "member@example.test", fresh.workspace)
    )
    outsider = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, "owner@example.com")
    )
    for auth in (admin.token, {"Cookie": f"{sessions.COOKIE}={member_cookie}"}):
        for method, body in (("GET", None), ("POST", change)):
            status, answer = api(hub, auth, method, "/api/tool-policy", body)
            assert status == 403 and "only a signed-in workspace owner" in answer["error"]
    assert api(hub, None, "POST", "/api/tool-policy", change)[0] == 401
    assert api(hub, None, "GET", "/api/tool-policy")[0] == 401
    # Another workspace's owner changes only their own workspace.
    status, _body = api(
        hub, {"Cookie": f"{sessions.COOKIE}={outsider}"}, "POST", "/api/tool-policy", change
    )
    assert status == 200
    assert store.modes(hub.db, fresh.workspace) == {}
    assert store.modes(hub.db, hub.workspace) == {"task_add": "off"}
    assert add(hub, fresh.connect("worker"))[0]
    # No tool reaches the policy either.
    assert not [n for n in hub.tool_names(admin.token) if "policy" in n]
    assert not [n for n in routes.catalog() if "tool_policy" in n]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"actions": {}},
        {"actions": []},
        {"actions": {"task_add": "sometimes"}},
        {"actions": {"task_add": None}},
        {"actions": {"no_such_action": "off"}},
        {"actions": {"task_add": "off"}, "workspace": "other"},
        {"actions": {"task_add": "off", "no_such_action": "ask"}},
    ],
)
def test_invalid_changes_are_refused_and_store_nothing(hub, fresh, body):
    status, answer = api(hub, fresh.cookie, "POST", "/api/tool-policy", body)
    assert status == 400 and answer["code"] == "invalid"
    assert store.modes(hub.db, fresh.workspace) == {}


def test_a_change_is_audited_and_a_vanished_action_can_be_cleared(hub, fresh):
    set_policy(hub, fresh, task_add="ask", ask="off")
    set_policy(hub, fresh, task_add="ask", ask="allowed")
    rows = [
        (r["tool"], r["decision"], r["user_id"])
        for r in hub.db.audit_entries(fresh.workspace, limit=10)
        if r["action"] == "tool_policy.set"
    ]
    assert sorted(rows) == sorted(
        [
            ("task_add", "ask", fresh.user),
            ("ask", "off", fresh.user),
            ("ask", "allowed", fresh.user),
        ]
    )
    store.set_modes(hub.db, fresh.workspace, {"retired_action": "off"}, actor="test", now=1.0)
    listed = {a["name"]: a for a in api(hub, fresh.cookie, "GET", "/api/tool-policy")[1]["actions"]}
    assert listed["retired_action"] == {"name": "retired_action", "kind": "unknown", "mode": "off"}
    assert (
        api(hub, fresh.cookie, "POST", "/api/tool-policy", {"actions": {"retired_action": "ask"}})[
            0
        ]
        == 400
    )
    assert set_policy(hub, fresh, retired_action="allowed").get("retired_action") is None


# ── pieces ──


def test_the_digest_binds_the_action_and_every_argument():
    base = store.digest("task_add", {"board": "work", "title": "A", "labels": ["x", "y"]})
    assert base == store.digest("task_add", {"labels": ["x", "y"], "title": "A", "board": "work"})
    assert len(base) == 64
    for tool, args in (
        ("task_update", {"board": "work", "title": "A", "labels": ["x", "y"]}),
        ("task_add", {"board": "work", "title": "A", "labels": ["y", "x"]}),
        ("task_add", {"board": "work", "title": "A"}),
        ("task_add", {"board": "work", "title": "a", "labels": ["x", "y"]}),
        ("task_add", {"board": "work", "title": "A", "labels": ["x", "y"], "claim": False}),
        ("task_add", {"board": "work", "title": 1, "labels": ["x", "y"]}),
    ):
        assert store.digest(tool, args) != base


def test_the_preview_is_bounded_plain_text():
    from commonplace.boards import decision_blocks

    big = {f"key{i:02d}": "x" * 500 for i in range(40)}
    big["key00"] = "line one\nline two​\x07 <b>bold</b>"
    for arguments in ({}, {"title": "Short"}, big):
        text = gate.preview(arguments)
        assert decision_blocks._text({"preview": text}, "preview", decision_blocks.PREVIEW_LIMIT)
        assert len(text.splitlines()) <= decision_blocks.PREVIEW_LINES
    assert gate.preview({}) == "(no arguments)"
    assert "\\u200b\\u0007" in gate.preview(big) and "… and 28 more" in gate.preview(big)


def test_a_stored_mode_this_version_does_not_know_only_narrows(hub, fresh):
    store.ensure_schema(hub.db)
    with hub.db._lock:
        hub.db._conn.execute("PRAGMA ignore_check_constraints=ON")
        hub.db._conn.execute(
            "INSERT INTO tool_policy VALUES (?,?,?,?,?)",
            (fresh.workspace, "task_add", "confirm", "test", 1.0),
        )
        hub.db._conn.execute("PRAGMA ignore_check_constraints=OFF")
    assert store.mode(hub.db, fresh.workspace, "task_add") == "off"
    assert not add(hub, fresh.connect("worker"))[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_settings_section_renders_and_saves():
    result = subprocess.run(
        ["node", str(HARNESS)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
