"""Thread tools: strict arguments, verbatim results and gateway authorization."""

import json
from types import SimpleNamespace

import pytest

from commonplace.gateway import LocalTool, Mediator
from commonplace.handoff.mcp_tools import TOOL_NAMES, register
from commonplace.handoff.models import RecallHit, StoredThread, ThreadMessage
from commonplace.handoff.recall import ThreadNotFound
from commonplace.policy import GATEWAY_LOCAL_TOOLS, TOOL_RULES, Policy, PolicyError
from mempalace.tool_capabilities import TOOL_CAPABILITIES
from tests._commonplace_helpers import hub_fixture  # noqa: F401

TEXT = '  Earlier agent: “keep every word”\n\t{"answer": 42}\n'
RAW = '{"role":"assistant", "content":"unchanged"}\n'
MESSAGE = ThreadMessage(2, "assistant", "message", TEXT, RAW, "tool", "call_1", "then")
HIT = RecallHit("thr_old", 3, 2, 4, 0.75, TEXT)
THREAD = StoredThread(
    "thr_old", "ws_home", "source_1", 5, 2, "hash", created_at="2026-10-30T01:00:00Z"
)


class FakeRecall:
    def __init__(self, workspace_id=None):
        self.workspace_id = workspace_id
        self.calls = []
        self.failure = None
        self.empty = False

    def _record(self, method, principal, thread_id, *args, **kwargs):
        self.calls.append((method, principal, thread_id, args, kwargs))
        if self.failure is not None:
            raise self.failure
        if thread_id != THREAD.id or (
            self.workspace_id is not None and principal.workspace_id != self.workspace_id
        ):
            raise ThreadNotFound("private details must not escape")

    def recall(self, principal, thread_id, query, **kwargs):
        self._record("recall", principal, thread_id, query, **kwargs)
        return [] if self.empty else [HIT]

    def get(self, principal, thread_id, **kwargs):
        self._record("get", principal, thread_id, **kwargs)
        return [] if self.empty else [MESSAGE]

    def chain(self, principal, thread_id):
        self._record("chain", principal, thread_id)
        return [] if self.empty else [THREAD]


@pytest.fixture
def setup_tools():
    recall = FakeRecall()
    mediator = SimpleNamespace(local_tools={"other": object()})
    ctx = SimpleNamespace(gateway=SimpleNamespace(mediator=mediator), recall=recall)
    register(ctx)
    principal = SimpleNamespace(
        workspace_id="ws_home",
        policy=Policy.build(profile="standard", scopes=["threads:read"], trust="standard"),
    )
    return ctx, principal, mediator.local_tools


def _args(name):
    args = {"thread_id": THREAD.id}
    if name == "thread_recall":
        args["query"] = "earlier agent"
    return args


def _value(response):
    return json.loads(response["result"]["content"][0]["text"])


def test_registration_schemas_and_repeat_registration(setup_tools):
    ctx, principal, tools = setup_tools
    other = tools["other"]
    register(ctx)
    assert tools["other"] is other
    assert set(tools) == {"other", *TOOL_NAMES}
    for name in TOOL_NAMES:
        tool = tools[name]
        assert isinstance(tool, LocalTool)
        assert tool.name == tool.schema["name"] == name
        assert tool.read and tool.refusal(principal) is None
        assert tool.schema["description"]
        schema = tool.schema["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["properties"]["thread_id"] == {"type": "string"}
    search = tools["thread_recall"].schema["inputSchema"]
    assert search["required"] == ["thread_id", "query"]
    assert search["properties"]["limit"] == {"type": "integer", "minimum": 1, "maximum": 50}
    assert search["properties"]["follow_chain"] == {"type": "boolean"}
    get = tools["thread_get"].schema["inputSchema"]
    assert get["required"] == ["thread_id"]
    assert get["properties"]["start"] == {"type": "integer", "minimum": 0}
    assert get["properties"]["end"] == {"type": "integer"}
    assert get["properties"]["include_raw"] == {"type": "boolean"}
    chain = tools["thread_chain"].schema["inputSchema"]
    assert chain["required"] == ["thread_id"]
    assert set(chain["properties"]) == {"thread_id"}


def test_recall_shape_and_omitted_defaults(setup_tools):
    ctx, principal, tools = setup_tools
    result = tools["thread_recall"].handler(principal, _args("thread_recall"))
    assert result == {
        "thread_id": THREAD.id,
        "hits": [
            {
                "thread_id": HIT.stored_thread_id,
                "chunk": 3,
                "seq_start": 2,
                "seq_end": 4,
                "score": 0.75,
                "text": TEXT,
            }
        ],
    }
    assert ctx.recall.calls == [("recall", principal, THREAD.id, ("earlier agent",), {})]


@pytest.mark.parametrize("limit", [1, 50])
@pytest.mark.parametrize("follow_chain", [False, True])
def test_recall_options_and_verbatim_query(setup_tools, limit, follow_chain):
    ctx, principal, tools = setup_tools
    tools["thread_recall"].handler(
        principal,
        {"thread_id": THREAD.id, "query": TEXT, "limit": limit, "follow_chain": follow_chain},
    )
    assert ctx.recall.calls == [
        ("recall", principal, THREAD.id, (TEXT,), {"limit": limit, "follow_chain": follow_chain})
    ]


@pytest.mark.parametrize("include_raw", [None, False, True])
def test_get_shape_and_raw_opt_in(setup_tools, include_raw):
    ctx, principal, tools = setup_tools
    args = _args("thread_get")
    if include_raw is not None:
        args["include_raw"] = include_raw
    expected = MESSAGE.to_dict()
    if not include_raw:
        del expected["raw"]
    assert tools["thread_get"].handler(principal, args) == {
        "thread_id": THREAD.id,
        "messages": [expected],
    }
    assert ctx.recall.calls == [("get", principal, THREAD.id, (), {})]
    assert MESSAGE.raw == RAW


@pytest.mark.parametrize("options", [{"start": 0}, {"end": 0}, {"start": 5, "end": -2}])
def test_get_forwards_ranges_without_inventing_end_constraints(setup_tools, options):
    ctx, principal, tools = setup_tools
    tools["thread_get"].handler(principal, {**_args("thread_get"), **options})
    assert ctx.recall.calls == [("get", principal, THREAD.id, (), options)]


def test_chain_shape(setup_tools):
    ctx, principal, tools = setup_tools
    assert tools["thread_chain"].handler(principal, _args("thread_chain")) == {
        "threads": [{"thread_id": THREAD.id, "created_at": THREAD.created_at, "message_count": 5}]
    }
    assert ctx.recall.calls == [("chain", principal, THREAD.id, (), {})]


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_empty_results(setup_tools, name):
    ctx, principal, tools = setup_tools
    ctx.recall.empty = True
    result = tools[name].handler(principal, _args(name))
    expected = {
        "thread_recall": {"thread_id": THREAD.id, "hits": []},
        "thread_get": {"thread_id": THREAD.id, "messages": []},
        "thread_chain": {"threads": []},
    }
    assert result == expected[name]


@pytest.mark.parametrize("name", TOOL_NAMES)
@pytest.mark.parametrize("bad", [None, [], "args", 1, True])
def test_arguments_must_be_objects(setup_tools, name, bad):
    ctx, principal, tools = setup_tools
    assert tools[name].handler(principal, bad) == {
        "error": "invalid arguments: arguments must be an object"
    }
    assert ctx.recall.calls == []


@pytest.mark.parametrize("name", TOOL_NAMES)
@pytest.mark.parametrize("field", ["missing", "unknown"])
def test_missing_and_unknown_arguments(setup_tools, name, field):
    ctx, principal, tools = setup_tools
    args = _args(name)
    if field == "missing":
        del args["thread_id"]
    else:
        args["workspace_id"] = "ws_other"
    result = tools[name].handler(principal, args)
    assert result["error"].startswith("invalid arguments:")
    assert ctx.recall.calls == []


def test_recall_query_is_required(setup_tools):
    ctx, principal, tools = setup_tools
    assert tools["thread_recall"].handler(principal, {"thread_id": THREAD.id}) == {
        "error": "invalid arguments: query is required"
    }
    assert ctx.recall.calls == []


@pytest.mark.parametrize("name", TOOL_NAMES)
@pytest.mark.parametrize("bad", [None, True, 3, 3.0, [], {}])
def test_thread_id_requires_a_string(setup_tools, name, bad):
    ctx, principal, tools = setup_tools
    result = tools[name].handler(principal, {**_args(name), "thread_id": bad})
    assert result == {"error": "invalid arguments: thread_id must be string"}
    assert ctx.recall.calls == []


@pytest.mark.parametrize(
    "name,field,bad",
    [
        *[("thread_recall", "query", value) for value in (None, False, 4, [], {})],
        *[("thread_recall", "limit", value) for value in (None, True, 8.0, "8", [], 0, -1, 51)],
        *[("thread_recall", "follow_chain", value) for value in (None, 0, "false", [])],
        *[("thread_get", "start", value) for value in (None, False, 0.0, "0", [], -1)],
        *[("thread_get", "end", value) for value in (None, True, 2.0, "2", [])],
        *[("thread_get", "include_raw", value) for value in (None, 0, "true", [])],
    ],
)
def test_invalid_optional_arguments_never_reach_recall(setup_tools, name, field, bad):
    ctx, principal, tools = setup_tools
    result = tools[name].handler(principal, {**_args(name), field: bad})
    assert result["error"].startswith(f"invalid arguments: {field} must be")
    assert ctx.recall.calls == []


@pytest.mark.parametrize("query", ["", " \n\t"])
def test_string_queries_are_forwarded_without_normalization(setup_tools, query):
    ctx, principal, tools = setup_tools
    tools["thread_recall"].handler(principal, {"thread_id": THREAD.id, "query": query})
    assert ctx.recall.calls[0][3] == (query,)


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_not_found_is_fail_closed(setup_tools, name):
    ctx, principal, tools = setup_tools
    for thread_id in ("", "thr_missing"):
        assert tools[name].handler(principal, {**_args(name), "thread_id": thread_id}) == {
            "error": "thread not found"
        }
    ctx.recall.failure = ThreadNotFound("authorization detail")
    assert tools[name].handler(principal, _args(name)) == {"error": "thread not found"}


@pytest.mark.parametrize("name", TOOL_NAMES)
@pytest.mark.parametrize("failure", [RuntimeError("bug"), ValueError("bug"), LookupError("bug")])
def test_unexpected_errors_are_not_disguised_as_missing_threads(setup_tools, name, failure):
    ctx, principal, tools = setup_tools
    ctx.recall.failure = failure
    with pytest.raises(type(failure), match="bug"):
        tools[name].handler(principal, _args(name))


def test_policy_profiles_accept_local_tools_without_worker_classification():
    assert GATEWAY_LOCAL_TOOLS == set(TOOL_NAMES)
    assert not GATEWAY_LOCAL_TOOLS & set(TOOL_RULES)
    assert not GATEWAY_LOCAL_TOOLS & set(TOOL_CAPABILITIES)
    for profile in ("standard", "full"):
        policy = Policy.build(profile=profile, scopes=["threads:read"], trust="standard")
        assert set(TOOL_NAMES) <= policy.tool_names()
        assert not set(TOOL_NAMES) & set(policy.visible_tools())
        assert Policy.from_json(policy.to_json()) == policy
    assert not set(TOOL_NAMES) & Policy.build(scopes=[]).tool_names()
    explicit = Policy.build(tools=TOOL_NAMES, scopes=["threads:read"])
    assert explicit.tool_names() == set(TOOL_NAMES)
    assert Policy.from_json(explicit.to_json()) == explicit
    with pytest.raises(PolicyError, match="unknown or ungated tools"):
        Policy.build(tools=[*TOOL_NAMES, "thread_unknown"])


@pytest.mark.parametrize("name", TOOL_NAMES)
@pytest.mark.parametrize(
    "options,allowed",
    [
        ({"profile": "slim"}, False),
        ({"scopes": []}, False),
        ({"trust": "untrusted"}, False),
        ({"tools": []}, False),
        ({"tools": ["thread_get"]}, None),
        ({}, True),
        ({"profile": "full", "trust": "trusted"}, True),
    ],
)
def test_refusal_honors_scope_tool_set_and_trust(setup_tools, name, options, allowed):
    _ctx, principal, tools = setup_tools
    policy = Policy.build(
        **{"profile": "standard", "scopes": ["threads:read"], "trust": "standard", **options}
    )
    if allowed is None:
        allowed = name == "thread_get"
    principal.policy = policy
    assert (tools[name].refusal(principal) is None) is allowed


def test_worker_cannot_advertise_unregistered_local_tools(setup_tools):
    _ctx, principal, tools = setup_tools
    listing = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": [tools[n].schema for n in TOOL_NAMES]},
    }
    workers = SimpleNamespace(post=lambda _principal, _request: listing)
    mediator = Mediator(None, workers)
    result = mediator.handle(principal, {"id": 1, "method": "tools/list"})
    assert result["result"]["tools"] == []


def test_real_hub_visibility_dispatch_and_workspace_isolation(hub):
    recall = FakeRecall(hub.workspace)
    ctx = SimpleNamespace(gateway=hub.gateway, recall=recall)
    standard = hub.token(
        "handoff-reader",
        profile="standard",
        scopes=["threads:read", "memory:read"],
        read_wings=["threads"],
        trust="standard",
    )

    def assert_hidden(token, names=TOOL_NAMES):
        assert not set(names) & hub.tool_names(token)
        before = len(recall.calls)
        for name in names:
            response = hub.rpc(token, "tools/call", {"name": name, "arguments": _args(name)})
            assert response["error"] == {"code": -32601, "message": f"Unknown tool: {name}"}
        assert len(recall.calls) == before

    assert_hidden(standard)
    register(ctx)
    assert set(TOOL_NAMES) <= hub.tool_names(standard)
    for name in TOOL_NAMES:
        result, _ = hub.call(standard, name, **_args(name))
        assert "error" not in result
        call = recall.calls[-1]
        assert call[1].workspace_id == hub.workspace
        assert call[2] == THREAD.id

    for label, options in (
        ("slim", {"profile": "slim"}),
        ("no-scope", {"scopes": ["memory:read"]}),
        ("untrusted", {"trust": "untrusted"}),
        ("explicit-denied", {"tools": []}),
        ("explicit-untrusted", {"tools": TOOL_NAMES, "trust": "untrusted"}),
    ):
        token = hub.token(
            label,
            **{
                "profile": "standard",
                "scopes": ["threads:read", "memory:read"],
                "read_wings": ["threads"],
                "trust": "standard",
                **options,
            },
        )
        assert_hidden(token)

    explicit = hub.token(
        "explicit-reader", tools=["thread_get"], scopes=["threads:read"], trust="trusted"
    )
    assert hub.tool_names(explicit) == {"thread_get"}
    assert_hidden(explicit, ("thread_recall", "thread_chain"))
    result, _ = hub.call(explicit, "thread_get", **_args("thread_get"), include_raw=True)
    assert result["messages"][0]["raw"] == RAW

    # Registration is global: another workspace has the tools, never this thread.
    foreign_workspace = hub.db.create_workspace("other", hub.admin)
    foreign = hub.token(
        "foreign-reader",
        workspace=foreign_workspace,
        profile="standard",
        scopes=["threads:read"],
        trust="standard",
    )
    assert set(TOOL_NAMES) <= hub.tool_names(foreign)
    for name in TOOL_NAMES:
        denied = hub.rpc(foreign, "tools/call", {"name": name, "arguments": _args(name)})
        missing = hub.rpc(
            standard,
            "tools/call",
            {"name": name, "arguments": {**_args(name), "thread_id": "thr_missing"}},
        )
        assert _value(denied) == _value(missing) == {"error": "thread not found"}
        assert denied["result"]["isError"] is missing["result"]["isError"] is True
        assert TEXT not in json.dumps(denied, ensure_ascii=False)

    invalid = hub.rpc(
        standard,
        "tools/call",
        {"name": "thread_recall", "arguments": {**_args("thread_recall"), "limit": True}},
    )
    assert invalid["result"]["isError"] is True
    assert _value(invalid)["error"].startswith("invalid arguments:")
    invalid = hub.rpc(standard, "tools/call", {"name": "thread_get", "arguments": None})
    assert _value(invalid) == {"error": "invalid arguments: thread_id is required"}

    # A principal is resolved by the gateway, never accepted as a tool argument.
    real_principal = recall.calls[0][1]
    assert real_principal.policy.trust == "standard"
