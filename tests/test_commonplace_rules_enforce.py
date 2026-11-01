"""Checked-write behavior through authenticated MCP and REST dispatch."""

import ast
import copy
import http.client
import inspect
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from commonplace import events, extensions, memory_tools, webhooks
from commonplace.boards import BoardService
from commonplace.gateway import Gateway
from commonplace.handoff import api as handoff_api, wiring
from commonplace.policy import GATEWAY_NEVER
from commonplace.rules import checks, enforce
from commonplace.rules.service import effective_rules
from mempalace.tool_capabilities import TOOL_CAPABILITIES
from tests import test_commonplace_rules as rule_tests
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_boards import api, mcp
from tests.test_handoff_api import HANDOFF_BODY, _Data, _Recall, _Service, _Settings
from tests.test_handoff_fact_integration import enabled as _enabled, query as fact_query

env_fixture = pytest.fixture(name="env")(rule_tests.env.__wrapped__)
enabled_handoff_fixture = pytest.fixture(name="enabled_handoff")(_enabled.__wrapped__)


@pytest.fixture(autouse=True)
def disable_limits(monkeypatch):
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    monkeypatch.setenv("COMMONPLACE_PLANS", "off")
    monkeypatch.delenv("COMMONPLACE_RULES_CHECK", raising=False)


def rpc(hub, env, name, **args):
    return hub.rpc(env.token, "tools/call", {"name": name, "arguments": args})["result"]


def refresh(hub, env):
    ok, text = mcp(hub, env.token, "rules_get", effective=True, format="md")
    assert ok, text
    return int(text.splitlines()[-1].split()[1])


def setup(hub, env, mode="enforce"):
    board = BoardService(env.owner.ctx)
    board.board_add(slug="work", wing="shared")
    card = board.task_add(board="work", title="Original")
    card = BoardService(env.worker.ctx).task_take_next(task=card["number"])
    env.owner.create("*", "Follow the approved rules.", "instruction")
    env.owner.set_check("*", mode)
    return board, card


def last_audit(hub, tool=None):
    return next(
        r
        for r in hub.db.audit_entries(limit=1000)
        if r["action"] == "tool.call" and (tool is None or r["tool"] == tool)
    )


def request(hub, env, method, path, body=None, seq=None, *, person=False):
    headers = {"Content-Type": "application/json"}
    headers.update(env.cookie if person else {"Authorization": "Bearer " + env.token})
    if seq is not None:
        headers["Commonplace-Rules-Seq"] = str(seq)
    connection = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=5)
    try:
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
        connection.close()


def test_schemas_off_warn_enforce_deepcopies_and_people(hub, env):
    setup(hub, env, "off")
    original = {n: copy.deepcopy(t.input_schema) for n, t in extensions.REGISTRY.items()}
    off = hub.rpc(env.token, "tools/list")["result"]["tools"]
    for mode in ("warn", "enforce"):
        env.owner.set_check("*", mode)
        tools = hub.rpc(env.token, "tools/list")["result"]["tools"]
        for tool in tools:
            name, value = tool["name"], tool["inputSchema"]
            assert ("rules_seq" in value.get("properties", {})) == (name in enforce.CHECKED_WRITES)
            assert ("rules_seq" in value.get("required", [])) == (
                mode == "enforce" and name in enforce.CHECKED_WRITES and name != "ask"
            )
            if name in enforce.CHECKED_WRITES:
                field = value["properties"]["rules_seq"]
                assert {v["type"] for v in field["oneOf"]} == {"integer", "string"}
                assert "rules_get" in field["description"]
                value["properties"]["rules_seq"]["oneOf"][0]["type"] = "invalid"
        assert {n: t.input_schema for n, t in extensions.REGISTRY.items()} == original
    other = hub.owner_token("off-agent")
    env.owner.set_check("conn:off-agent", "off")
    assert hub.rpc(other, "tools/list")["result"]["tools"] == off
    response = hub.gateway.mediator.handle(env.person, {"id": 1, "method": "tools/list"})
    assert all(
        "rules_seq" not in t["inputSchema"].get("properties", {})
        for t in response["result"]["tools"]
    )
    env.owner.set_check("*", "off")
    assert hub.rpc(env.token, "tools/list")["result"]["tools"] == off
    assert mcp(hub, env.token, "task_update", task=1, title="Off write", rules_seq={"bad": True})[0]
    assert last_audit(hub)["reason"] == ""


def test_warn_preserves_json_first_block_and_adds_notice(hub, env):
    setup(hub, env, "warn")
    result = rpc(hub, env, "space_post", board="work", text="Exact words")
    assert not result.get("isError")
    assert json.loads(result["content"][0]["text"])["text"] == "Exact words"
    assert len(result["content"]) == 2
    assert result["content"][1]["text"] == enforce.MESSAGES["missing"].replace(
        "no:", "Commonplace:", 1
    )
    assert last_audit(hub)["reason"] == "rules_seq missing (warn); transport=mcp"
    seq = refresh(hub, env)
    assert (
        len(rpc(hub, env, "space_post", board="work", text="Fresh", rules_seq=str(seq))["content"])
        == 1
    )
    env.owner.create("*", "A later rule.", "style")
    result = rpc(hub, env, "space_post", board="work", text="Stale", rules_seq=seq)
    assert len(result["content"]) == 2 and "rules changed" in result["content"][1]["text"]
    # Handler refusals still carry the separate refresh notice.
    result = rpc(hub, env, "task_comment", task=99999, text="Missing card")
    assert result["isError"] and len(result["content"]) == 2


@pytest.mark.parametrize("value", [None, "abc", -1, True, 1.5, {}, [], "-1", " 123", "１２３"])
def test_enforce_malformed_values_do_not_write_or_emit(hub, env, value):
    board, card = setup(hub, env)
    refresh(hub, env)
    before = events.latest_seq(hub.db, hub.workspace)
    result = rpc(hub, env, "task_update", task=card["number"], title="Forbidden", rules_seq=value)
    assert result["isError"] and result["content"][0]["text"] == enforce.MESSAGES["missing"]
    assert board.task_show(card["number"])["title"] == "Original"
    assert events.latest_seq(hub.db, hub.workspace) == before
    entry = last_audit(hub)
    assert entry["decision"] == "deny" and entry["reason"] == "rules_seq missing; transport=mcp"
    assert (
        entry["workspace_id"] == hub.workspace and entry["connection_id"] == env.agent.connection_id
    )


def test_stale_refresh_success_and_non_serving_reads_real_http(hub, env):
    board, card = setup(hub, env)
    current = effective_rules(env.worker.ctx)["rules_seq"]
    for tool, args in (("rules_get", {}), ("rules_changed", {"since": 0})):
        assert mcp(hub, env.token, tool, **args)[0]
        result = rpc(hub, env, "task_update", task=1, title="Not read", rules_seq=current)
        assert result["isError"] and result["content"][0]["text"] == enforce.MESSAGES["not served"]
    seq = refresh(hub, env)
    env.owner.create("*", "Changed after serving.", "limit")
    result = rpc(hub, env, "task_update", task=1, title="Stale", rules_seq=seq)
    assert result["isError"] and result["content"][0]["text"] == enforce.MESSAGES["stale"]
    assert board.task_show(card["number"])["title"] == "Original"
    new = refresh(hub, env)
    assert new != seq
    assert not rpc(hub, env, "task_update", task=1, title="Updated", rules_seq=str(new)).get(
        "isError"
    )
    assert board.task_show(1)["title"] == "Updated"
    assert last_audit(hub)["decision"] == "ok"


def test_overcap_ask_before_parsing_and_recovery_after_trim_or_bump(hub, env, monkeypatch):
    board, _ = setup(hub, env)
    old = refresh(hub, env)
    rows = [env.owner.create("*", str(i) + "\u200b" * 990, "instruction") for i in range(5)]
    assert "too_large" in mcp(hub, env.token, "rules_get", effective=True)[1].splitlines()[-1]
    with monkeypatch.context() as patch:
        patch.setattr(
            enforce, "_number", lambda value: pytest.fail("over-cap check parsed sequence")
        )
        for value in (None, old, {}, "9" * 5000):
            assert not rpc(
                hub, env, "ask", board="work", question="Please trim.", rules_seq=value
            ).get("isError")
        result = rpc(hub, env, "task_update", task=1, title="No")
        assert result["isError"] and result["content"][0]["text"] == enforce.MESSAGES["too_large"]
    assert last_audit(hub, "ask")["reason"] == "rules_seq too_large; transport=mcp"
    env.owner.create("*", "Later bump, still over cap.", "style")
    for value, reason in ((None, "missing"), (old, "stale")):
        result = rpc(hub, env, "ask", board="work", question="Reread?", rules_seq=value)
        assert result["isError"] and result["content"][0]["text"] == enforce.MESSAGES[reason]
        assert "call ask without one" in result["content"][0]["text"]
    assert "too_large" in mcp(hub, env.token, "rules_get", effective=True)[1]
    assert not rpc(hub, env, "ask", board="work", question="Still too big.", rules_seq=old).get(
        "isError"
    )
    for row in rows[-2:]:
        env.owner.action(row["id"], row["version"], "retire")
    for value, reason in ((None, "missing"), (old, "stale")):
        assert (
            rpc(hub, env, "ask", board="work", question="After trim.", rules_seq=value)["content"][
                0
            ]["text"]
            == enforce.MESSAGES[reason]
        )
    current = effective_rules(env.worker.ctx)["rules_seq"]
    assert (
        rpc(hub, env, "task_update", task=1, title="Not read", rules_seq=current)["content"][0][
            "text"
        ]
        == enforce.MESSAGES["not served"]
    )
    seq = refresh(hub, env)
    assert not rpc(hub, env, "task_update", task=1, title="Recovered", rules_seq=seq).get("isError")
    assert board.task_show(1)["title"] == "Recovered"
    for text in [*enforce.MESSAGES.values(), enforce.TOO_LARGE_NOTICE]:
        assert not any(c.isdigit() for c in text)


def test_overcap_warn_both_tools_run_with_notice(hub, env):
    setup(hub, env, "warn")
    for i in range(5):
        env.owner.create("*", str(i) + "\u200b" * 990, "instruction")
    assert "too_large" in mcp(hub, env.token, "rules_get", effective=True)[1]
    for name, args in (
        ("ask", {"board": "work", "question": "Trim?"}),
        ("task_update", {"task": 1, "title": "Allowed"}),
    ):
        result = rpc(hub, env, name, **args)
        assert (
            not result.get("isError") and result["content"][1]["text"] == enforce.TOO_LARGE_NOTICE
        )
        assert last_audit(hub)["reason"] == "rules_seq too_large (warn); transport=mcp"


def test_internal_nested_memory_once_people_and_unforgeable_marker(hub, env, monkeypatch):
    setup(hub, env)
    seq = refresh(hub, env)
    calls = []
    original = enforce.check

    def record(ctx, tool, supplied):
        calls.append((tool, checks.is_internal_call()))
        return original(ctx, tool, supplied)

    monkeypatch.setattr(enforce, "check", record)
    assert not rpc(
        hub, env, "task_update", task=1, lesson="Keep exact words.", status="done", rules_seq=seq
    ).get("isError")
    assert calls == [("task_update", False)]
    assert rpc(hub, env, "space_post", board="work", text="No bypass", internal_call=True)[
        "isError"
    ]
    calls.clear()
    wiring.HandoffContext(hub.gateway, hub.db, Path(env.worker.ctx.workspace_dir())).save_fact(
        env.agent,
        {
            "subject": "Avery",
            "predicate": "likes",
            "object": "tea",
            "theme": "shared",
            "source": {"thread": "mcp:synthetic"},
        },
    )
    assert calls == [("fact_save", True)]
    assert last_audit(hub, "fact_save")["decision"] == "ok"
    person = request(
        hub,
        env,
        "POST",
        "/mcp",
        {
            "id": 1,
            "method": "tools/call",
            "params": {"name": "space_post", "arguments": {"board": "work", "text": "Person"}},
        },
        person=True,
    )
    assert person[0] == 200 and not person[1]["result"].get("isError")
    assert (
        request(hub, env, "POST", "/api/boards/work/chat", {"text": "Person REST"}, person=True)[0]
        == 201
    )
    assert not checks.is_internal_call()


@pytest.mark.parametrize("missing", ["tool", "scope"])
def test_unenforceable_downgrades_and_owner_cannot_enable(hub, env, missing):
    setup(hub, env)
    token = hub.token(
        "writer-only",
        tools=["space_post"] if missing == "tool" else ["rules_get", "rules_publish"],
        scopes=["memory:read", "memory:write"] if missing == "tool" else ["memory:write"],
        read_wings=["shared"],
        write_wings=["shared"],
        trust="trusted",
    )
    result = hub.rpc(
        token,
        "tools/call",
        {"name": "space_post", "arguments": {"board": "work", "text": "Allowed"}}
        if missing == "tool"
        else {"name": "rules_publish", "arguments": {"rules": []}},
    )["result"]
    assert not result.get("isError") and "cannot read rules" in result["content"][1]["text"]
    assert last_audit(hub)["reason"] == "rules_seq unenforceable (warn); transport=mcp"
    assert api(
        hub,
        env.cookie,
        "PATCH",
        "/api/rules/checks",
        {"agent": "conn:writer-only", "mode": "enforce"},
    ) == (400, {"error": "this connection cannot read rules"})


def test_automatic_handoff_upload_files_facts_under_enforce(hub, env, enabled_handoff):
    context, principal, token = enabled_handoff
    setup(hub, env)
    client = SimpleNamespace(token=token, agent=principal, cookie=env.cookie)
    status, result, _ = request(
        hub,
        client,
        "POST",
        "/api/handoff",
        {
            "meta": {"thread_id": "checked-upload"},
            "transcript": json.dumps(
                [{"role": "user", "content": "For Kestrel the deploy port is 8443."}]
            ),
            "transcript_format": "messages",
        },
    )
    assert status == 200 and result["action"] == "handoff", result
    context.service.wait_for_facts(timeout=10)
    assert [f["object"] for f in fact_query(hub, token)] == ["8443"]
    assert last_audit(hub, "fact_save")["decision"] == "ok"
    assert not any(
        r["decision"] == "deny" for r in hub.db.audit_entries() if r["action"] == "tool.call"
    )


@pytest.mark.parametrize("mode", ["off", "warn", "enforce"])
@pytest.mark.parametrize("failure", ["missing", "corrupt", "old"])
def test_store_failures_fail_open_guard_in_all_modes(hub, env, mode, failure):
    setup(hub, env, mode)
    seq = refresh(hub, env) if mode != "off" else None
    path = Path(env.worker.ctx.workspace_dir()) / "rules.sqlite3"
    if failure == "missing":
        path.unlink()
    elif failure == "corrupt":
        path.write_bytes(b"broken synthetic store")
    else:
        with checks.connection(path, write=True) as conn:
            conn.execute("DROP TABLE agent_seq")
            conn.execute("DROP TABLE agent_served")
    assert hub.rpc(env.token, "tools/list")["result"]["tools"]
    result = rpc(hub, env, "space_post", board="work", text="Keep working", rules_seq=seq)
    assert not result.get("isError") and len(result["content"]) == 1
    reason = last_audit(hub, "space_post")["reason"]
    assert reason == "" if mode == "off" else reason.startswith("rules_seq unchecked")
    assert checks.mode(hub.gateway, env.agent) == mode
    if failure == "missing":
        assert not path.exists()
        assert any(e["kind"] == "rule.store_lost" for e in events.after(hub.db, hub.workspace))


def test_unknown_mode_and_baseline_supervisor_do_not_break_calls(hub, env, monkeypatch):
    setup(hub, env)
    monkeypatch.setattr(
        hub.db,
        "rule_checks",
        lambda _: (_ for _ in ()).throw(sqlite3.OperationalError("unreadable")),
    )
    assert not rpc(hub, env, "space_post", board="work", text="Unknown").get("isError")
    assert last_audit(hub)["reason"].startswith("rules_seq unchecked")
    gateway = Gateway(hub.db, SimpleNamespace())
    gateway.mediator.workers = hub.gateway.mediator.workers
    try:
        assert gateway.mediator.handle(env.agent, {"id": 1, "method": "tools/list"})["result"][
            "tools"
        ]
        # A handler without store access can still run; the check fails open.
        tool = extensions.get("space_post")
        with monkeypatch.context() as patch:
            patch.setitem(
                extensions.REGISTRY, "space_post", replace(tool, handler=lambda c, a: {"ok": True})
            )
            result = gateway.mediator.handle(
                env.agent,
                {
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "space_post", "arguments": {"text": "ok"}},
                },
            )
            assert not result["result"].get("isError")
    finally:
        webhooks.stop(gateway)


def test_no_rules_yet_and_lost_store_accept_recovery(hub, env):
    env.owner.set_check("*", "enforce")
    assert not rpc(hub, env, "memory_save", text="First memory", theme="shared").get("isError")
    assert last_audit(hub, "memory_save")["reason"] == ""
    setup(hub, env)
    seq = refresh(hub, env)
    path = Path(env.worker.ctx.workspace_dir()) / "rules.sqlite3"
    path.unlink()
    assert not rpc(hub, env, "space_post", board="work", text="Incident").get("isError")
    assert api(hub, env.cookie, "POST", "/api/rules/store/accept", {})[0] == 200
    assert mcp(hub, env.token, "rules_get", effective=True)[0]
    env.owner.create("*", "Restored rules.", "instruction")
    result = rpc(hub, env, "space_post", board="work", text="Stale", rules_seq=seq)
    assert result["isError"] and "rules changed" in result["content"][0]["text"]
    assert not rpc(
        hub, env, "space_post", board="work", text="Recovered", rules_seq=refresh(hub, env)
    ).get("isError")


def test_unchecked_alarm_counts_authenticated_connection_both_transports(hub, env):
    setup(hub, env)
    Path(env.worker.ctx.workspace_dir(), "rules.sqlite3").unlink()
    for i in range(8):
        if i % 2:
            assert (
                request(hub, env, "POST", "/api/boards/work/chat", {"text": "Synthetic incident"})[
                    0
                ]
                == 201
            )
        else:
            assert not rpc(hub, env, "space_post", board="work", text="Synthetic incident").get(
                "isError"
            )
        alerts = [e for e in events.after(hub.db, hub.workspace) if e["kind"] == "rule.unchecked"]
        assert len(alerts) == (1 if i >= 5 else 0)
    assert alerts[0]["subject"] == env.agent.connection_id
    assert alerts[0]["data"] == {"connection_id": env.agent.connection_id, "count": 6}
    rows = [r for r in hub.db.audit_entries() if r["reason"].startswith("rules_seq unchecked")]
    assert len(rows) == 8 and {r["reason"].rsplit("=", 1)[-1] for r in rows} == {"mcp", "rest"}
    assert {r["connection_id"] for r in rows} == {env.agent.connection_id}
    assert all("Synthetic incident" not in r["reason"] for r in rows)
    # Another connection has its own threshold.
    other = SimpleNamespace(**vars(env))
    other.token = hub.owner_token("other")
    other.agent = hub.gateway.authenticate("Bearer " + other.token)
    assert not rpc(hub, other, "space_post", board="work", text="Other").get("isError")
    assert (
        len([e for e in events.after(hub.db, hub.workspace) if e["kind"] == "rule.unchecked"]) == 1
    )


def test_relevant_rule_and_visibility_changes_make_real_writes_stale(hub, env):
    setup(hub, env)
    seq = refresh(hub, env)
    proposal = rule_tests.publish(env)
    assert not rpc(
        hub, env, "space_post", board="work", text="Proposal unchanged", rules_seq=seq
    ).get("isError")
    other = env.owner.create("conn:other", "Unrelated", "limit")
    assert not rpc(hub, env, "space_post", board="work", text="Other unchanged", rules_seq=seq).get(
        "isError"
    )
    accepted = env.owner.action(proposal["id"], proposal["version"], "accept")
    assert rpc(hub, env, "space_post", board="work", text="Old", rules_seq=seq)["isError"]
    seq = refresh(hub, env)
    moved = env.owner.set_agent(accepted["id"], accepted["version"], other["agent"])
    assert moved["agent"] != env.agent.actor
    assert rpc(hub, env, "space_post", board="work", text="Moved", rules_seq=seq)["isError"]
    env.owner.create("*", "Repo rule", "limit", "repo", "example.test/org/app")
    seq = refresh(hub, env)
    BoardService(env.owner.ctx).board_add(slug="linked", wing="shared", repo="example.test/org/app")
    assert rpc(hub, env, "space_post", board="work", text="Visibility", rules_seq=seq)["isError"]
    seq = refresh(hub, env)
    barrier = Barrier(50)

    def parallel_write(i):
        barrier.wait(timeout=10)
        return hub.gateway.mediator.handle(
            env.agent,
            {
                "id": i,
                "method": "tools/call",
                "params": {
                    "name": "task_comment",
                    "arguments": {
                        "task": 1,
                        "text": f"Parallel {i}",
                        "rules_seq": seq,
                    },
                },
            },
        )["result"]

    # Exercise simultaneous checks and commits independently of the HTTP
    # fixture's small accept backlog. Both transports have real-HTTP proofs.
    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(parallel_write, range(50)))
    assert len(results) == 50 and all(not r.get("isError") for r in results)
    rows = [r for r in hub.db.audit_entries() if r["tool"] == "task_comment"]
    assert len(rows) == 50 and all(r["decision"] == "ok" and not r["reason"] for r in rows)


@pytest.fixture
def handoff(hub, env, tmp_path):
    env.token = hub.token(
        "handoff-agent",
        profile="full",
        scopes=[*env.agent.policy.scopes, "handoff:write", "threads:read"],
        read_wings=["*"],
        write_wings=["*"],
        trust="trusted",
    )
    env.agent = hub.gateway.authenticate("Bearer " + env.token)
    env.worker = rule_tests.service(hub, env.agent)
    calls = []
    ctx = wiring.install(hub.gateway, tmp_path / "handoff")
    data = _Data(hub.workspace, "other-workspace", calls)
    data.records["hof_local"] = replace(
        data.records["hof_local"], connection_id=env.agent.connection_id
    )
    ctx.handoff_db = data
    ctx.settings = _Settings(data, calls)
    original_service = ctx.service
    ctx.service = _Service(data, calls)
    ctx.recall = _Recall(hub.workspace, "other-workspace", calls)
    yield SimpleNamespace(ctx=ctx, calls=calls)
    ctx.service = original_service
    ctx.close()


def rest_case(route, board, card, env):
    path = (
        route.path.replace("<n>", str(card["number"]))
        .replace("<slug>", "work")
        .replace("<id>", "hof_local")
    )
    bodies = {
        "task_update": {"version": card["version"], "title": "Changed"},
        "task_comment": {"text": "Comment"},
        "task_handoff": {"to": env.person.actor, "note": "Review please"},
        "decide": {"version": card["version"], "reply": "Approved"},
        "task_add": {"title": "New card"},
        "board_add": {"slug": "new", "wing": "shared"},
        "board_set": {"coordinator": None},
        "space_post": {"text": "Chat"},
        "memory_save": {"text": "Exact memory", "theme": "shared"},
        "fact_save": {
            "subject": "Avery",
            "predicate": "likes",
            "object": "tea",
            "theme": "shared",
            "source": {"thread": "mcp:synthetic"},
        },
        "relaunch": {},
    }
    if route.tool == "decide":
        updated = board.ask("Review?", task=card["number"])
        bodies["decide"]["version"] = updated["version"]
    return path, {**bodies[route.tool], "rules_seq": {"ignored": True}}


@pytest.mark.parametrize(
    "route", [r for r in enforce.REST_ROUTES if r.kind == "checked"], ids=lambda r: r.tool
)
def test_every_checked_rest_shape_refuses_and_accepts_fresh_header(hub, env, handoff, route):
    board, card = setup(hub, env)
    path, body = rest_case(route, board, card, env)
    before = events.latest_seq(hub.db, hub.workspace)
    status, result, _ = request(hub, env, route.method, path, body)
    assert status == 428 and result == {"error": enforce.MESSAGES["missing"], "rules_refresh": True}
    assert events.latest_seq(hub.db, hub.workspace) == before
    entry = last_audit(hub, route.tool)
    assert entry["decision"] == "deny" and entry["reason"] == "rules_seq missing; transport=rest"
    seq = refresh(hub, env)
    status, result, headers = request(hub, env, route.method, path, body, seq)
    assert status in (200, 201), result
    assert "Commonplace-Rules-Notice" not in headers
    entry = last_audit(hub, route.tool)
    assert entry["decision"] == "ok" and entry["reason"] == "transport=rest"


def test_rest_stale_refresh_success_warn_notice_and_callstack_exact_body(hub, env):
    board, card = setup(hub, env)
    seq = refresh(hub, env)
    env.owner.create("*", "Later change.", "limit")
    body = {"version": card["version"], "title": "Updated", "rules_seq": seq}
    status, result, _ = request(hub, env, "PATCH", "/api/cards/1", body, seq)
    assert status == 428 and result["error"] == enforce.MESSAGES["stale"]
    assert request(hub, env, "PATCH", "/api/cards/1", body, refresh(hub, env))[0] == 200
    current = board.task_show(1)
    assert (
        request(
            hub, env, "PATCH", "/api/cards/1", {"version": current["version"], "callstack": None}
        )[0]
        == 200
    )
    for extra in ({"title": "No bypass"}, {"task": 1}, {"status": "todo"}):
        assert (
            request(
                hub,
                env,
                "PATCH",
                "/api/cards/1",
                {"version": current["version"], "callstack": None, **extra},
            )[0]
            == 428
        )
    assert request(hub, env, "PATCH", "/api/cards/1", {"callstack": None})[0] == 428
    env.owner.set_check("*", "warn")
    status, _, headers = request(hub, env, "POST", "/api/boards/work/chat", {"text": "Warn"})
    assert status == 201 and "this write needs rules_seq" in headers["Commonplace-Rules-Notice"]
    assert last_audit(hub)["reason"] == "rules_seq missing (warn); transport=rest"
    # A notice cannot leak onto the next request on a reused handler.
    assert "Commonplace-Rules-Notice" not in request(hub, env, "GET", "/api/boards")[2]


@pytest.mark.parametrize(
    "route", [r for r in enforce.REST_ROUTES if r.kind != "checked"], ids=lambda r: r.path
)
def test_every_exempt_rest_shape_and_read_reaches_real_router(
    hub, env, handoff, route, monkeypatch
):
    setup(hub, env)
    bodies = {
        "/api/handoff": HANDOFF_BODY,
        "/api/handoffs/<id>/successor": {"agent_ref": "agent:new"},
        "/api/handoffs/<id>/launch-failed": {"error": "Synthetic exit"},
        "/api/threads/<id>/recall": {"query": "tea"},
        "/api/settings/handoff": {"scope": "task", "ref": "#142", "enabled": True},
        "/api/webhooks": {"url": "https://example.test/hook"},
    }
    path = route.path.replace("<id>", "thr_local" if "threads" in route.path else "hof_local")
    if route.method == "DELETE" and "settings" in path:
        path += "?scope=task&ref=%23142"
    if route.module == "webhooks":
        # Avoid external delivery/DNS while exercising authentication and routing.
        monkeypatch.setattr(webhooks, "add", lambda *a, **k: {"id": "hook", "secret": "synthetic"})
        monkeypatch.setattr(webhooks, "remove", lambda *a, **k: None)
        monkeypatch.setattr(webhooks, "test_webhook", lambda *a, **k: "delivery")
    status, result, headers = request(
        hub, env, route.method, path, {**bodies.get(route.path, {}), "rules_seq": "ignored"}
    )
    assert status in (200, 201, 202), result
    assert "Commonplace-Rules-Notice" not in headers
    assert not any(
        r["decision"] == "deny" for r in hub.db.audit_entries() if r["action"] == "tool.call"
    )


def test_owner_agent_intent_tools_are_checked_and_people_routes_stay_people_only(hub, env, handoff):
    setup(hub, env)
    for name, args in (
        ("board_set", {"board": "work", "coordinator": None}),
        ("decide", {"task": 1, "reply": "yes"}),
        ("handoff_start", {}),
        ("rules_publish", {"rules": []}),
        ("rules_publish", {"rules": [], "replace": True}),
    ):
        result = rpc(hub, env, name, **args)
        assert result["isError"] and result["content"][0]["text"] == enforce.MESSAGES["missing"]
    for method, path, body in (
        ("GET", "/api/rules", None),
        ("POST", "/api/rules", {"agent": "*", "text": "No", "kind": "limit"}),
        ("PATCH", "/api/voice", {}),
    ):
        assert request(hub, env, method, path, body)[0] == 403
    assert (
        request(
            hub,
            env,
            "POST",
            "/api/rules",
            {"agent": "*", "text": "Person rule", "kind": "limit"},
            person=True,
        )[0]
        == 201
    )
    member = hub.db.create_user("member@example.test")
    hub.db.add_member(hub.workspace, member, "member", actor=hub.admin)
    token = hub.token("member", user=member, profile="full", trust="trusted")
    for name in ("decide", "board_set"):
        result = hub.rpc(token, "tools/call", {"name": name, "arguments": {}})
        assert result["error"]["message"] == "Unknown tool: " + name


def test_write_inventory_names_every_gateway_write_and_worker_side_door():
    extensions.load_modules()
    writes = {name for name, tool in extensions.REGISTRY.items() if tool.kind == "write"}
    writes |= {
        name
        for name, (kind, _) in TOOL_CAPABILITIES.items()
        if kind != "read" and name not in GATEWAY_NEVER
    }
    assert writes == enforce.CHECKED_WRITES | enforce.EXEMPT_WRITES.keys()
    assert not enforce.CHECKED_WRITES & enforce.EXEMPT_WRITES.keys()
    assert all(enforce.EXEMPT_WRITES.values())
    documentation = Path("docs/design/RULES.md").read_text()
    assert all(f"`{name}`" in documentation for name in enforce.EXEMPT_WRITES)


def _write_branches(function):
    """Count method branches, expanding action dictionaries and action tuples."""
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            methods = [
                n
                for n in ast.walk(node.test)
                if isinstance(n, ast.Compare)
                and isinstance(n.left, (ast.Name, ast.Attribute))
                and (
                    getattr(n.left, "id", None) == "method"
                    or getattr(n.left, "attr", None) == "command"
                )
                and any(
                    isinstance(c, ast.Constant) and c.value in {"POST", "PATCH", "PUT", "DELETE"}
                    for c in n.comparators
                )
            ]
            for _ in methods:
                actions = [
                    n
                    for n in ast.walk(node.test)
                    if isinstance(n, ast.Compare)
                    and isinstance(n.ops[0], ast.In)
                    and isinstance(n.comparators[0], ast.Tuple)
                ]
                dictionaries = [
                    n.value
                    for n in node.body
                    if isinstance(n, ast.Assign)
                    and isinstance(n.value, ast.Dict)
                    and any(isinstance(t, ast.Name) and t.id == "handlers" for t in n.targets)
                ]
                count += (
                    len(actions[0].comparators[0].elts)
                    if actions
                    else len(dictionaries[0].keys)
                    if dictionaries
                    else 1
                )
        # Method-to-tool dispatch maps and the (method, action) allow-list.
        if isinstance(node, ast.Dict):
            count += sum(
                isinstance(k, ast.Constant) and k.value in {"POST", "PATCH", "PUT", "DELETE"}
                for k in node.keys
            )
        if isinstance(node, ast.Tuple) and len(node.elts) == 2:
            count += int(
                isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value in {"POST", "PATCH", "PUT", "DELETE"}
            )
    return count


def test_rest_route_inventory_matches_ast_write_branches():
    from commonplace.boards import rest

    functions = {
        "boards.rest": (rest.dispatch, rest._space_route, rest._card_route),
        "memory_tools": (memory_tools.install,),
        "handoff.api": (
            handoff_api._API.dispatch,
            handoff_api._API.threads,
            handoff_api._API.settings,
        ),
        "webhooks": (webhooks._rest,),
    }
    for module, dispatchers in functions.items():
        count = sum(_write_branches(f) for f in dispatchers)
        # The fact writer is registered directly, rather than a method branch.
        if module == "memory_tools":
            tree = ast.parse(inspect.getsource(memory_tools.install))
            count += sum(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "add_route"
                and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == "POST"
                for n in ast.walk(tree)
            )
        routes = [r for r in enforce.REST_ROUTES if r.module == module]
        assert count == len(routes), (module, count, routes)
    assert len({(r.method, r.path) for r in enforce.REST_ROUTES}) == len(enforce.REST_ROUTES)
