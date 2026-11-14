"""Gateway vector diagnostics keep global counts private and local detail intact."""

import json

import pytest

from commonplace.gateway import WorkerClient, _replace_result, _tool_result
from commonplace.handoff.models import ThreadMessage
from tests._commonplace_helpers import functional_worker_transport, hub_fixture  # noqa: F401
from tests.test_card253_s02 import seed, world  # noqa: F401

RAW = "HNSW index holds 791 elements but sqlite has 864 embeddings - repair required"
FIXED = "Vector index unavailable; repair required."


@pytest.fixture
def diverged(request, monkeypatch):
    w = request.getfixturevalue("world")
    request.getfixturevalue("functional_worker_transport")
    seed(w)
    probes = []

    def refresh():
        probes.append(True)
        w.mcp._vector_disabled = True
        w.mcp._vector_disabled_reason = RAW

    monkeypatch.setattr(w.mcp, "_refresh_vector_disabled_flag", refresh)
    monkeypatch.setattr(w.mcp, "_vector_disabled", True)
    monkeypatch.setattr(w.mcp, "_vector_disabled_reason", RAW)
    monkeypatch.setenv("COMMONPLACE_RULES", "off")
    w.probes = probes
    w.requests = []
    original_post = WorkerClient._post

    def post(self, worker, principal, request):
        w.requests.append(request)
        return original_post(self, worker, principal, request)

    monkeypatch.setattr(WorkerClient, "_post", post)
    # Person-private reads belong to owners, so the witness is a real shared-only member.
    user = w.h.db.create_user("diagnostic-reader@example.test")
    w.h.db.add_member(w.h.workspace, user, "member", actor=w.h.admin)
    w.readable = w.h.gateway.handoff.store_for(w.h.workspace).store(
        w.h.workspace,
        "native-readable-diagnostic",
        [ThreadMessage(0, "user", "message", "readable", "readable")],
        actor=user,
    )
    w.token = w.h.token(
        "diagnostic-reader",
        user=user,
        tools=["mempalace_search", "mempalace_check_duplicate", "mempalace_add_drawer"],
        scopes=["memory:read", "memory:write", "threads:read"],
        read_wings=["*"],
        write_wings=["wing_fixture"],
        trust="trusted",
    )
    w.principal = w.h.db.authenticate_token(w.token)
    assert w.principal.role == "member" and not w.principal.person
    assert not w.principal.policy.has_scope("private:read")
    assert w.principal.policy.can_read_wing("wing_fixture")
    assert w.principal.policy.has_scope("threads:read")
    assert w.h.gateway.handoff.recall.can_read(w.principal, w.readable.id)
    assert not w.h.gateway.handoff.recall.can_read(w.principal, w.hidden.id)
    # Whole-palace duplicate checks are limited to all-wing readers, which now means owners.
    assert w.principal.policy.refusal("mempalace_check_duplicate") is not None
    w.owner_token = w.h.token(
        "diagnostic-owner",
        user=w.h.db.authenticate_token(w.tokens["A"]).user_id,
        tools=["mempalace_search", "mempalace_check_duplicate"],
        scopes=["memory:read", "private:read", "threads:read"],
        read_wings=["*"],
        trust="trusted",
    )
    w.owner = w.h.db.authenticate_token(w.owner_token)
    assert w.owner.role == "owner" and w.owner.policy.sees_all_wings
    assert w.owner.policy.refusal("mempalace_check_duplicate") is None
    assert w.h.gateway.handoff.recall.can_read(w.owner, w.readable.id)
    return w


def assert_private(reply):
    text = json.dumps(reply)
    assert not any(digit in text for digit in set(RAW) if digit.isdigit()), text
    assert RAW not in text


@pytest.mark.parametrize("route", ["direct", "fanout"])
def test_card253_s13_search(diverged, route):
    w = diverged
    args = {"query": "zznonmatchingpresencefixture", "include_facts": False}
    if route == "direct":
        args["wing"] = "wing_fixture"
    else:
        args["wings"] = ["wing_fixture"]
    assert w.principal.policy.refusal("mempalace_search") is None
    reply, _ = w.h.call(w.token, "mempalace_search", **args)
    assert w.probes and reply["vector_disabled"] is True
    if route == "fanout":
        assert any(
            r.get("params", {}).get("arguments", {}).get("wings") == ["wing_fixture"]
            for r in w.requests
        )
    assert_private(reply)
    assert reply["vector_disabled_reason"] == FIXED


def test_card253_s13_duplicate(diverged):
    w = diverged
    reply, _ = w.h.call(w.owner_token, "mempalace_check_duplicate", content="ordinary words")
    assert w.probes and reply["vector_disabled"] is True
    assert reply["is_duplicate"] is False and reply["matches"] == []
    assert_private(reply)
    assert reply["vector_disabled_reason"] == FIXED


def test_card253_s13_protocol(diverged):
    w = diverged
    assert w.principal.policy.refusal("mempalace_add_drawer") is None
    reply = w.h.rpc(
        w.token,
        "tools/call",
        {
            "name": "mempalace_add_drawer",
            "arguments": {
                "wing": "wing_fixture",
                "room": "ordinary",
                "content": "verbatim words",
            },
        },
    )
    assert w.probes and reply["error"]["code"] == w.mcp._DIVERGED_INDEX_ERROR_CODE
    assert reply["error"]["data"]["tool"] == "mempalace_add_drawer"
    assert_private(reply["error"]["data"].get("vector_disabled_reason"))
    assert "vector_disabled_reason" not in reply["error"]["data"]


def test_card253_s13_local_control(diverged):
    w = diverged
    # Start the actual transport as well as calling the local public API.
    w.h.call(w.owner_token, "mempalace_check_duplicate", content="ordinary words")
    assert w.mcp.tool_check_duplicate("ordinary words")["vector_disabled_reason"] == RAW
    assert (
        w.mcp._mcp_diverged_index_refusal(1, "mempalace_add_drawer")["error"]["data"][
            "vector_disabled_reason"
        ]
        == RAW
    )


def test_card253_s13_diary_reconnect_blocked(diverged):
    w = diverged
    w.h.call(w.token, "mempalace_search", query="zznonmatchingpresencefixture", wing="wing_fixture")
    assert w.principal.policy.refusal("mempalace_reconnect") is not None
    reply = w.h.rpc(w.token, "tools/call", {"name": "mempalace_reconnect", "arguments": {}})
    assert reply["error"]["code"] == -32601
    assert_private(reply["error"]["message"])


@pytest.mark.parametrize("flag", [None, False, True])
def test_card253_s13_orphan_reason(diverged, monkeypatch, flag):
    w = diverged
    original = WorkerClient._post

    def post(self, worker, principal, request):
        response = original(self, worker, principal, request)
        payload = _tool_result(response)
        if flag is None:
            payload.pop("vector_disabled")
        else:
            payload["vector_disabled"] = flag
        return _replace_result(response, payload)

    monkeypatch.setattr(WorkerClient, "_post", post)
    reply = w.h.gateway.call_memory_tool(
        w.owner, "mempalace_check_duplicate", {"content": "ordinary words"}
    )
    assert w.probes
    assert_private(reply)
    if flag is True:
        assert reply["vector_disabled_reason"] == FIXED
    else:
        assert "vector_disabled_reason" not in reply
