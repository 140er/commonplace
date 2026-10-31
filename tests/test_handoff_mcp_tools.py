"""Thread tools through the gateway: availability, policy and verbatim results."""

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from commonplace import extensions, webhooks
from commonplace.control_db import ControlDB
from commonplace.gateway import Gateway
from commonplace.handoff.mcp_tools import TOOL_NAMES
from commonplace.handoff.models import RecallHit, SettingRow, StoredThread, ThreadMessage
from commonplace.handoff.recall import Recall, ThreadNotFound
from commonplace.handoff.wiring import install
from commonplace.policy import TOOL_RULES, Policy, PolicyError
from mempalace.tool_capabilities import TOOL_CAPABILITIES
from tests._commonplace_helpers import Hub

TEXT = '  Earlier agent: “keep every word”\n\t{"answer": 42}\n'
RAW = '{"role":"assistant", "content":"unchanged"}\n'
MESSAGE = ThreadMessage(2, "assistant", "message", TEXT, RAW, "tool", "call_1", "then")
HIT = RecallHit("thr_old", 3, 2, 4, 0.75, TEXT)
THREAD = StoredThread(
    "thr_old", "ws_home", "source_1", 5, 2, "hash", created_at="2026-10-30T01:00:00Z"
)


PROVENANCE = {
    "origin_kind": "thread",
    "origin_id": THREAD.source_thread_id,
    "origin_link": "/app/threads/" + THREAD.id,
    "filed_by": "unknown",
    "approval_status": "unknown",
}


class FakeRecall:
    def __init__(self, workspace_id):
        self.workspace_id = workspace_id
        self.calls = []
        self.failure = None
        self.empty = False

    def _record(self, method, principal, thread_id, *args, **kwargs):
        self.calls.append((method, principal, thread_id, args, kwargs))
        if self.failure is not None:
            raise self.failure
        if thread_id != THREAD.id or principal.workspace_id != self.workspace_id:
            raise ThreadNotFound("private details must not escape")

    def thread(self, principal, thread_id):
        if thread_id != THREAD.id or principal.workspace_id != self.workspace_id:
            raise ThreadNotFound("thread not found")
        return THREAD

    def recall(self, principal, thread_id, query, **kwargs):
        self._record("recall", principal, thread_id, query, **kwargs)
        return [] if self.empty else [HIT]

    def get(self, principal, thread_id, **kwargs):
        self._record("get", principal, thread_id, **kwargs)
        return [] if self.empty else [MESSAGE]

    def chain(self, principal, thread_id):
        self._record("chain", principal, thread_id)
        return [] if self.empty else [THREAD]


class ToolHub(Hub):
    """Real HTTP gateway and control DB, without starting a Memory worker."""

    def __init__(self, data_dir):
        self.db = ControlDB(str(data_dir / "control.sqlite3"))
        self.admin = self.db.create_user("owner@example.com", is_admin=True)
        self.workspace = self.db.create_workspace("home", self.admin, personal=True)
        self.supervisor = SimpleNamespace()
        self.gateway = Gateway(self.db, self.supervisor)
        self.ctx = install(self.gateway, data_dir)
        self.recall = FakeRecall(self.workspace)
        self.ctx.recall = self.recall
        self.worker_tools = []
        self.worker_calls = []
        self.gateway.mediator.workers = SimpleNamespace(post=self.worker_post)
        self.reader = self.reader_token("handoff-reader")
        self.principal = self.gateway.authenticate(f"Bearer {self.reader}")
        self.server = self.gateway.build_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._ids = 0
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        webhooks.stop(self.gateway)
        self.ctx.close()
        self.db.close()

    def worker_post(self, principal, request):
        self.worker_calls.append((principal, request))
        assert request["method"] == "tools/list", "thread calls must stay in the gateway"
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"tools": self.worker_tools},
        }

    def reader_token(self, name, workspace=None, **options):
        return self.token(
            name,
            workspace=workspace,
            **{
                "profile": "standard",
                "scopes": ["threads:read", "memory:read"],
                "read_wings": ["threads"],
                "trust": "standard",
                **options,
            },
        )

    def setting(self, scope="workspace", ref="", enabled=True, workspace=None):
        return self.ctx.handoff_db.put_setting(
            SettingRow(workspace or self.workspace, scope, ref, enabled=enabled), actor=self.admin
        )

    def invoke(self, name, arguments):
        return self.rpc(self.reader, "tools/call", {"name": name, "arguments": arguments})

    def state(self):
        """Handoff state only; authentication and refusal audits may advance."""
        with self.db._lock:
            settings = tuple(
                tuple(row)
                for row in self.db._conn.execute(
                    "SELECT * FROM handoff_settings ORDER BY workspace_id, scope, ref"
                )
            )
            handoffs = tuple(tuple(row) for row in self.db._conn.execute("SELECT * FROM handoffs"))
            event_rows = tuple(
                tuple(row) for row in self.db._conn.execute("SELECT * FROM events ORDER BY seq")
            )
        stores = {
            workspace: tuple(store._conn.iterdump())
            for workspace, store in self.ctx._stores.items()
        }
        files = tuple(sorted(self.ctx.data_dir.rglob("threads.sqlite3")))
        return settings, handoffs, event_rows, stores, files


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    extensions.load_modules()
    monkeypatch.setattr(extensions, "REGISTRY", dict(extensions.REGISTRY))


@pytest.fixture
def tool_hub(tmp_path):
    hub = ToolHub(tmp_path)
    try:
        yield hub
    finally:
        hub.close()


@pytest.fixture
def setup_tools(tool_hub):
    tool_hub.setting()
    tool_hub.initial_state = tool_hub.state()
    return tool_hub


def _args(name):
    args = {"thread_id": THREAD.id}
    if name == "thread_recall":
        args["query"] = "earlier agent"
    return args


def _value(response):
    assert "error" not in response
    assert not response["result"].get("isError", False)
    assert response["result"]["content"][0]["type"] == "text"
    return json.loads(response["result"]["content"][0]["text"])


def _error(response):
    assert "error" not in response
    assert response["result"]["isError"] is True
    content = response["result"]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert content[0]["text"].startswith("no:")
    return content[0]["text"]


def _invalid(hub, response, message):
    assert _error(response) == f"no: invalid arguments: {message}"
    assert hub.recall.calls == []
    assert hub.worker_calls == []
    assert hub.state() == hub.initial_state


def _hidden(hub, token=None, names=TOOL_NAMES):
    token = token or hub.reader
    before = hub.state()
    unexpected = set(names) & hub.tool_names(token)
    assert not unexpected
    calls = list(hub.recall.calls)
    worker_calls = list(hub.worker_calls)
    for name in names:
        for arguments in (_args(name), None, {"workspace_id": "other"}):
            response = hub.rpc(token, "tools/call", {"name": name, "arguments": arguments})
            assert response["error"] == {"code": -32601, "message": f"Unknown tool: {name}"}
    assert hub.recall.calls == calls
    assert hub.worker_calls == worker_calls
    assert hub.state() == before


def test_registration_schemas_and_repeat_registration(setup_tools):
    hub = setup_tools
    before = dict(extensions.REGISTRY)
    for name in TOOL_NAMES:
        tool = extensions.REGISTRY[name]
        assert extensions.register(tool) is tool
        assert isinstance(tool, extensions.LocalTool)
        assert tool.name == tool.schema()["name"] == name
        assert tool.kind == "read"
        assert tool.scope == "threads:read"
        assert tool.profiles == extensions.WORK_PROFILES
        assert tool.trusted_only
        assert hub.principal.policy.local_refusal(tool) is None
        assert tool.usable_by(hub.principal, hub.gateway)
        assert tool.description
        schema = tool.input_schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["properties"]["thread_id"] == {"type": "string"}
    assert extensions.REGISTRY == before
    listed = hub.rpc(hub.reader, "tools/list")["result"]["tools"]
    assert {tool["name"]: tool for tool in listed if tool["name"] in TOOL_NAMES} == {
        name: extensions.REGISTRY[name].schema() for name in TOOL_NAMES
    }
    search = extensions.REGISTRY["thread_recall"].input_schema
    assert search["required"] == ["thread_id", "query"]
    assert search["properties"]["limit"] == {"type": "integer", "minimum": 1, "maximum": 50}
    assert search["properties"]["follow_chain"] == {"type": "boolean"}
    get = extensions.REGISTRY["thread_get"].input_schema
    assert get["required"] == ["thread_id"]
    assert get["properties"]["start"] == {"type": "integer", "minimum": 0}
    assert get["properties"]["end"] == {"type": "integer"}
    assert get["properties"]["include_raw"] == {"type": "boolean"}
    chain = extensions.REGISTRY["thread_chain"].input_schema
    assert chain["required"] == ["thread_id"]
    assert set(chain["properties"]) == {"thread_id"}


def test_install_is_idempotent_without_supervisor_root(tool_hub):
    hub = tool_hub
    assert not hasattr(hub.supervisor, "root")
    routes = dict(hub.gateway.routes)
    prefixes = list(hub.gateway.prefix_routes)
    registered = dict(extensions.REGISTRY)
    assert hub.ctx is hub.gateway.handoff
    assert install(hub.gateway, hub.ctx.data_dir) is hub.ctx
    assert hub.ctx.recall is hub.recall
    assert hub.gateway.routes == routes
    assert hub.gateway.prefix_routes == prefixes
    assert extensions.REGISTRY == registered
    _hidden(hub)


def test_recall_shape_and_omitted_defaults(setup_tools):
    hub = setup_tools
    assert _value(hub.invoke("thread_recall", _args("thread_recall"))) == {
        "thread_id": THREAD.id,
        "hits": [
            {
                "thread_id": HIT.stored_thread_id,
                "chunk": 3,
                "seq_start": 2,
                "seq_end": 4,
                "score": 0.75,
                "text": TEXT,
                "provenance": PROVENANCE,
            }
        ],
    }
    assert hub.recall.calls == [("recall", hub.principal, THREAD.id, ("earlier agent",), {})]
    assert hub.worker_calls == []


@pytest.mark.parametrize("limit", [1, 50])
@pytest.mark.parametrize("follow_chain", [False, True])
def test_recall_options_and_verbatim_query(setup_tools, limit, follow_chain):
    hub = setup_tools
    _value(
        hub.invoke(
            "thread_recall",
            {"thread_id": THREAD.id, "query": TEXT, "limit": limit, "follow_chain": follow_chain},
        )
    )
    assert hub.recall.calls == [
        (
            "recall",
            hub.principal,
            THREAD.id,
            (TEXT,),
            {"limit": limit, "follow_chain": follow_chain},
        )
    ]


@pytest.mark.parametrize("include_raw", [None, False, True])
def test_get_shape_and_raw_opt_in(setup_tools, include_raw):
    hub = setup_tools
    args = _args("thread_get")
    if include_raw is not None:
        args["include_raw"] = include_raw
    expected = MESSAGE.to_dict()
    if not include_raw:
        del expected["raw"]
    assert _value(hub.invoke("thread_get", args)) == {
        "thread_id": THREAD.id,
        "messages": [expected],
        "provenance": PROVENANCE,
    }
    assert hub.recall.calls == [("get", hub.principal, THREAD.id, (), {})]
    assert MESSAGE.raw == RAW


@pytest.mark.parametrize("options", [{"start": 0}, {"end": 0}, {"start": 5, "end": -2}])
def test_get_forwards_ranges_without_inventing_end_constraints(setup_tools, options):
    hub = setup_tools
    _value(hub.invoke("thread_get", {**_args("thread_get"), **options}))
    assert hub.recall.calls == [("get", hub.principal, THREAD.id, (), options)]


def test_chain_shape(setup_tools):
    hub = setup_tools
    assert _value(hub.invoke("thread_chain", _args("thread_chain"))) == {
        "threads": [
            {
                "thread_id": THREAD.id,
                "created_at": THREAD.created_at,
                "message_count": 5,
                "provenance": PROVENANCE,
            }
        ]
    }
    assert hub.recall.calls == [("chain", hub.principal, THREAD.id, (), {})]


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_empty_results(setup_tools, name):
    hub = setup_tools
    hub.recall.empty = True
    expected = {
        "thread_recall": {"thread_id": THREAD.id, "hits": []},
        "thread_get": {"thread_id": THREAD.id, "messages": [], "provenance": PROVENANCE},
        "thread_chain": {"threads": []},
    }
    assert _value(hub.invoke(name, _args(name))) == expected[name]


@pytest.mark.parametrize("name", TOOL_NAMES)
@pytest.mark.parametrize("bad", [None, [], [1], "args", "", 1, 0, True, False])
def test_arguments_must_be_objects(setup_tools, name, bad):
    hub = setup_tools
    # The gateway turns a malformed arguments container into an empty object.
    _invalid(hub, hub.invoke(name, bad), "thread_id is required")


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_omitted_arguments_are_rejected(setup_tools, name):
    hub = setup_tools
    response = hub.rpc(hub.reader, "tools/call", {"name": name})
    _invalid(hub, response, "thread_id is required")


@pytest.mark.parametrize("name", TOOL_NAMES)
@pytest.mark.parametrize("field", ["missing", "unknown"])
def test_missing_and_unknown_arguments(setup_tools, name, field):
    hub = setup_tools
    args = _args(name)
    if field == "missing":
        del args["thread_id"]
        message = "thread_id is required"
    else:
        args["workspace_id"] = "ws_other"
        message = "unknown argument: workspace_id"
    _invalid(hub, hub.invoke(name, args), message)


def test_recall_query_is_required(setup_tools):
    hub = setup_tools
    _invalid(hub, hub.invoke("thread_recall", {"thread_id": THREAD.id}), "query is required")


@pytest.mark.parametrize("name", TOOL_NAMES)
@pytest.mark.parametrize("bad", [None, True, 3, 3.0, [], {}])
def test_thread_id_requires_a_string(setup_tools, name, bad):
    hub = setup_tools
    _invalid(hub, hub.invoke(name, {**_args(name), "thread_id": bad}), "thread_id must be string")


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
    hub = setup_tools
    rule = extensions.REGISTRY[name].input_schema["properties"][field]
    if type(bad) is int and "minimum" in rule and bad < rule["minimum"]:
        message = f"{field} must be at least {rule['minimum']}"
    elif type(bad) is int and "maximum" in rule and bad > rule["maximum"]:
        message = f"{field} must be at most {rule['maximum']}"
    else:
        message = f"{field} must be {rule['type']}"
    _invalid(hub, hub.invoke(name, {**_args(name), field: bad}), message)


@pytest.mark.parametrize("query", ["", " \n\t"])
def test_string_queries_are_forwarded_without_normalization(setup_tools, query):
    hub = setup_tools
    _value(hub.invoke("thread_recall", {"thread_id": THREAD.id, "query": query}))
    assert hub.recall.calls[0][3] == (query,)


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_not_found_is_fail_closed(setup_tools, name):
    hub = setup_tools
    before = hub.state()
    for thread_id in ("", "thr_missing"):
        response = hub.invoke(name, {**_args(name), "thread_id": thread_id})
        assert _error(response) == "no: thread not found"
    hub.recall.failure = ThreadNotFound("authorization detail")
    assert _error(hub.invoke(name, _args(name))) == "no: thread not found"
    assert hub.state() == before
    assert hub.worker_calls == []


@pytest.mark.parametrize("name", TOOL_NAMES)
@pytest.mark.parametrize("failure", [RuntimeError("bug"), ValueError("bug"), LookupError("bug")])
def test_unexpected_errors_are_not_disguised_as_missing_threads(setup_tools, name, failure):
    hub = setup_tools
    hub.recall.failure = failure
    before = hub.state()
    status, text = hub.raw(
        hub.reader,
        {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {"name": name, "arguments": _args(name)},
        },
    )
    assert status == 500
    assert json.loads(text) == {
        "jsonrpc": "2.0",
        "id": 42,
        "error": {"code": -32603, "message": "Internal error"},
    }
    assert len(hub.recall.calls) == 1
    assert hub.state() == before


def test_policy_profiles_accept_local_tools_without_worker_classification():
    assert set(TOOL_NAMES) <= extensions.names()
    assert not set(TOOL_NAMES) & set(TOOL_RULES)
    assert not set(TOOL_NAMES) & set(TOOL_CAPABILITIES)
    for profile in ("standard", "full"):
        policy = Policy.build(profile=profile, scopes=["threads:read"], trust="standard")
        assert set(TOOL_NAMES) <= policy.tool_names()
        assert set(TOOL_NAMES) <= set(policy.visible_tools())
        assert Policy.from_json(policy.to_json()) == policy
        no_scope = Policy.build(profile=profile, scopes=[], trust="standard")
        assert set(TOOL_NAMES) <= no_scope.tool_names()
        assert not set(TOOL_NAMES) & set(no_scope.visible_tools())
    slim = Policy.build(profile="slim", scopes=["threads:read"], trust="trusted")
    assert not set(TOOL_NAMES) & slim.tool_names()
    explicit = Policy.build(tools=TOOL_NAMES, scopes=["threads:read"], trust="standard")
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
        ({"tools": TOOL_NAMES, "trust": "untrusted"}, False),
        ({"tools": TOOL_NAMES, "scopes": []}, False),
        ({"profile": "slim", "tools": TOOL_NAMES}, True),
        ({}, True),
        ({"profile": "full", "trust": "trusted"}, True),
    ],
)
def test_refusal_honors_scope_tool_set_and_trust(setup_tools, name, options, allowed):
    hub = setup_tools
    token = hub.reader_token("policy-reader", **options)
    if allowed is None:
        allowed = name == "thread_get"
    if allowed:
        assert name in hub.tool_names(token)
        _value(hub.rpc(token, "tools/call", {"name": name, "arguments": _args(name)}))
        assert hub.recall.calls[-1][1].workspace_id == hub.workspace
    else:
        _hidden(hub, token, (name,))


def test_worker_cannot_advertise_unregistered_local_tools(setup_tools, monkeypatch):
    hub = setup_tools
    hub.worker_tools = [extensions.REGISTRY[name].schema() for name in TOOL_NAMES]
    for name in TOOL_NAMES:
        monkeypatch.delitem(extensions.REGISTRY, name)
    _hidden(hub)


@pytest.mark.parametrize("installed", [False, True])
def test_worker_cannot_advertise_unavailable_handoff_tools(tool_hub, monkeypatch, installed):
    hub = tool_hub
    hub.worker_tools = [extensions.REGISTRY[name].schema() for name in TOOL_NAMES]
    if not installed:
        monkeypatch.delattr(hub.gateway, "handoff")
    _hidden(hub)


def test_worker_cannot_duplicate_registered_handoff_tools(setup_tools):
    hub = setup_tools
    hub.worker_tools = [extensions.REGISTRY[name].schema() for name in TOOL_NAMES]
    listed = hub.rpc(hub.reader, "tools/list")["result"]["tools"]
    names = [tool["name"] for tool in listed]
    assert {name: names.count(name) for name in TOOL_NAMES} == dict.fromkeys(TOOL_NAMES, 1)


@pytest.mark.parametrize(
    "scope,ref,enabled",
    [
        ("workspace", "", None),
        ("workspace", "", False),
        ("agent", "codex:home", False),
        ("task", "#1", False),
    ],
)
def test_handoff_off_without_threads_is_hidden(tool_hub, scope, ref, enabled):
    hub = tool_hub
    assert set(TOOL_NAMES) <= hub.principal.policy.tool_names()
    assert hub.principal.policy.has_scope("threads:read")
    hub.setting(scope, ref, enabled)
    _hidden(hub)


@pytest.mark.parametrize("scope,ref", [("workspace", ""), ("agent", "codex:home"), ("task", "#1")])
def test_enabled_setting_at_any_level_makes_tools_available(tool_hub, scope, ref):
    hub = tool_hub
    _hidden(hub)
    hub.setting(enabled=False)
    hub.setting(scope, ref)
    assert set(TOOL_NAMES) <= hub.tool_names(hub.reader)
    hub.worker_calls.clear()
    for name in TOOL_NAMES:
        _value(hub.invoke(name, _args(name)))
        assert hub.recall.calls[-1][1].workspace_id == hub.workspace
    assert hub.worker_calls == []


def test_existing_threads_keep_tools_visible_when_handoff_off(tool_hub):
    hub = tool_hub
    hub.setting(enabled=False)
    hub.ctx.store_for(hub.workspace).store(
        hub.workspace,
        "source",
        [ThreadMessage(0, "assistant", "message", TEXT, RAW)],
        actor=hub.admin,
    )
    assert set(TOOL_NAMES) <= hub.tool_names(hub.reader)
    for name in TOOL_NAMES:
        _value(hub.invoke(name, _args(name)))


def test_empty_thread_store_does_not_enable_handoff(tool_hub):
    hub = tool_hub
    hub.ctx.store_for(hub.workspace)
    assert hub.ctx.has_threads(hub.workspace)
    _hidden(hub)


def test_foreign_thread_row_does_not_enable_handoff(tool_hub):
    hub = tool_hub
    foreign = hub.db.create_workspace("other", hub.admin)
    hub.ctx.store_for(hub.workspace).store(
        foreign, "source", [ThreadMessage(0, "assistant", "message", TEXT, RAW)], actor=hub.admin
    )
    _hidden(hub)


@pytest.mark.parametrize(
    "failure", [OSError("unavailable"), sqlite3.OperationalError("unavailable")]
)
def test_unreadable_thread_store_keeps_tools_hidden(tool_hub, monkeypatch, failure):
    hub = tool_hub
    hub.ctx.store_for(hub.workspace)

    def unavailable(_workspace):
        raise failure

    monkeypatch.setattr(hub.ctx, "store_for", unavailable)
    _hidden(hub)


def test_unknown_tools_follow_gateway_errors(setup_tools):
    hub = setup_tools
    _hidden(hub, names=("thread_unknown",))


def test_real_hub_visibility_dispatch_and_workspace_isolation(setup_tools):
    hub = setup_tools
    assert set(TOOL_NAMES) <= hub.tool_names(hub.reader)
    for name in TOOL_NAMES:
        _value(hub.invoke(name, _args(name)))
        call = hub.recall.calls[-1]
        assert call[1] == hub.principal
        assert call[1].workspace_id == hub.workspace
        assert call[1].policy.trust == "standard"
        assert call[2] == THREAD.id

    explicit = hub.reader_token("explicit-reader", tools=["thread_get"], trust="trusted")
    assert hub.tool_names(explicit) == {"thread_get"}
    _hidden(hub, explicit, ("thread_recall", "thread_chain"))
    response = hub.rpc(
        explicit,
        "tools/call",
        {"name": "thread_get", "arguments": {**_args("thread_get"), "include_raw": True}},
    )
    assert _value(response)["messages"][0]["raw"] == RAW

    foreign_workspace = hub.db.create_workspace("other", hub.admin)
    foreign = hub.reader_token("foreign-reader", workspace=foreign_workspace)
    _hidden(hub, foreign)
    hub.setting(workspace=foreign_workspace)
    assert set(TOOL_NAMES) <= hub.tool_names(foreign)
    before = hub.state()
    for name in TOOL_NAMES:
        denied = hub.rpc(foreign, "tools/call", {"name": name, "arguments": _args(name)})
        missing = hub.invoke(name, {**_args(name), "thread_id": "thr_missing"})
        assert _error(denied) == _error(missing) == "no: thread not found"
        assert TEXT not in json.dumps(denied, ensure_ascii=False)
    assert hub.state() == before


def test_real_store_workspace_isolation(setup_tools):
    hub = setup_tools
    hub.ctx.recall = Recall(hub.ctx.store_for)
    message = ThreadMessage(0, "assistant", "message", TEXT, RAW)
    stored = hub.ctx.store_for(hub.workspace).store(
        hub.workspace, "source", [message], actor=hub.admin
    )
    foreign_workspace = hub.db.create_workspace("other", hub.admin)
    foreign = hub.reader_token("foreign-reader", workspace=foreign_workspace)
    hub.setting(workspace=foreign_workspace)
    hub.ctx.store_for(foreign_workspace)
    before = hub.state()
    for name in TOOL_NAMES:
        arguments = {**_args(name), "thread_id": stored.id}
        if name == "thread_recall":
            arguments["query"] = ""
        _value(hub.invoke(name, arguments))
        denied = hub.rpc(foreign, "tools/call", {"name": name, "arguments": arguments})
        missing = hub.invoke(name, {**arguments, "thread_id": "thr_deadbeef"})
        assert _error(denied) == _error(missing) == "no: thread not found"
        assert TEXT not in json.dumps(denied, ensure_ascii=False)
    assert hub.state() == before
