"""Thread access at the mirror response boundary (synthetic data only)."""

import sqlite3
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from commonplace.continuity_read import PrivateReader
from commonplace.handoff.mirror_access import gate
from commonplace.handoff.recall import Recall
from commonplace.rules import checks
from tests.test_handoff_recall import MemoryStore, reader, stored


@pytest.fixture
def world():
    principal = reader(scopes=("memory:read", "private:read", "threads:read"), wings=("*",))
    principal = replace(principal, policy=replace(principal.policy, tools="full"))
    store = MemoryStore()
    thread = stored()
    store.threads[thread.id] = thread
    opener = Mock(return_value=store)
    private = Mock(readable=Mock(return_value=True))
    recall = Recall(opener, private=private)
    ctx = SimpleNamespace(recall=recall, _stores={principal.workspace_id: store})
    return SimpleNamespace(
        principal=principal,
        store=store,
        thread=thread,
        opener=opener,
        private=private,
        gateway=SimpleNamespace(handoff=ctx),
    )


def hit(w, **changes):
    return dict(
        wing="private_threads", room=w.thread.id, drawer_id="drawer_one", text="words", **changes
    )


def search(w, item=None):
    return gate(w.gateway, w.principal, "search", {"results": [hit(w) if item is None else item]})[
        "results"
    ]


def test_u5_a_other_workspace(world):
    w = world
    w.store.threads[w.thread.id] = replace(w.thread, workspace_id="other")
    assert search(w) == []


def test_u5_b_grant_filer(world):
    w = world
    w.store.threads[w.thread.id] = replace(w.thread, source_thread_id="import-private:other:source")
    assert search(w) == []
    w.store.threads[w.thread.id] = replace(
        w.thread, source_thread_id=f"import-private:{w.principal.user_id}:source"
    )
    assert search(w)
    w.private.view.assert_not_called()


def test_u5_c_scope_and_invalid_id(world):
    w = world
    w.principal = reader(scopes=("memory:read", "private:read"), wings=("*",))
    assert search(w) == []
    w.opener.assert_not_called()
    assert not w.gateway.handoff.recall.can_read(reader(), "invalid")
    w.opener.assert_not_called()


def test_u5_d_missing_store_and_exception(world):
    w = world
    w.opener.return_value = None
    assert search(w) == []
    w.opener.side_effect = RuntimeError("unavailable")
    assert search(w) == []
    w.gateway.handoff = None
    assert search(w) == []


def test_u5_e_startup_cold_and_warm(world):
    w = world
    w.gateway.handoff._stores.clear()
    with checks.deadline(60):
        assert search(w) == []
    w.opener.assert_not_called()
    w.gateway.handoff._stores[w.principal.workspace_id] = w.store
    with checks.deadline(60):
        assert search(w)
    w.opener.assert_not_called()
    w.private.view.assert_not_called()


def test_u5_f_revoked_capture_connection(world):
    w = world
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE connections (id TEXT, user_id TEXT, revoked INTEGER)")
    conn.execute("INSERT INTO connections VALUES (?, ?, 1)", ("conn_old", w.principal.user_id))
    try:
        private = PrivateReader(SimpleNamespace(_lock=threading.Lock(), _conn=conn), w.opener)
        w.gateway.handoff.recall = Recall(w.opener, private=private)
        w.store.threads[w.thread.id] = replace(
            w.thread, wing="private_chats", source_thread_id="capture:conn_old:source"
        )
        assert search(w)
        w.principal = replace(w.principal, user_id="other")
        assert search(w) == []
    finally:
        conn.close()


def test_u5_g_null_filer_owner_only(world):
    w = world
    private = PrivateReader(None, w.opener)
    w.gateway.handoff.recall = Recall(w.opener, private=private)
    w.store.threads[w.thread.id] = replace(
        w.thread, wing="private_chats", source_thread_id="unknown"
    )
    assert search(w) == []
    w.principal = replace(w.principal, role="owner")
    assert search(w)


def test_u5_h_shapes_and_cache(world):
    w = world
    result = {"results": [hit(w), hit(w)]}
    assert gate(w.gateway, w.principal, "search", result) == result
    assert w.store.calls == [("get", w.thread.id)]
    w.private.readable.return_value = False
    expected = {"drawer_id": "drawer_one", "error": "Drawer not found: drawer_one"}
    assert gate(w.gateway, w.principal, "drawer_read", result) == {
        "results": [expected, expected],
        "count": 2,
        "errors": 2,
    }
    assert gate(w.gateway, w.principal, "drawer_read", hit(w)) == {"error": expected["error"]}
    plain = {"wing": "shared", "text": "words"}
    assert search(w, plain) == [plain]
    assert gate(w.gateway, w.principal, "read_wing", {"nested": hit(w)}) == {
        "error": "not found",
        "denied": True,
    }


@pytest.mark.parametrize("field", ["text", "content", "document", "snippet"])
@pytest.mark.parametrize("room", [None, "", 123])
def test_u5_a1_roomless_words_fail_closed(world, field, room):
    item = {"wing": " WING-Private-Threads ", "room": room, field: "words"}
    assert search(world, item) == []


def test_roomless_taxonomy_fails_closed(world):
    # The rooms mapping exposes stored thread ids and counts without a readable room.
    item = {"wing": "private_threads", "rooms": {"thr_ab": 2}, "text": ""}
    assert search(world, item) == []


@pytest.mark.parametrize(
    "payload", [{"object": "words"}, {"summary": "words"}, {"documents": ["words"]}]
)
def test_roomless_private_objects_fail_closed(world, payload):
    assert search(world, {"wing": "private_threads", **payload}) == []


def test_room_only_and_wrong_wing_fail_closed(world):
    world.private.readable.return_value = False
    for wing in (None, "shared"):
        assert search(world, {"wing": wing, "room": world.thread.id, "object": "words"}) == []


def test_identity_for_unchanged_results(world):
    plain = {"results": [{"wing": "shared", "content": "words"}]}
    assert gate(world.gateway, world.principal, "search", plain) is plain
    mirror = {"results": [hit(world)]}
    assert gate(world.gateway, world.principal, "drawer_read", mirror) is mirror


@pytest.mark.parametrize("value", [None, "unexpected words", 42, True])
@pytest.mark.parametrize("rule", ["search", "drawer_read", "read_wing"])
def test_odd_result_fails_closed(world, value, rule):
    assert "error" in gate(world.gateway, world.principal, rule, value)


def test_preview_uses_own_room_and_drops_connection(world):
    world.private.readable.return_value = False
    connection = {
        "connected_wing": "shared",
        "connected_room": "public",
        "drawer_wing": "private_threads",
        "drawer_room": world.thread.id,
        "drawer_preview": "words",
        "drawer_id": "hidden",
        "tunnel_id": "link",
    }
    result = gate(world.gateway, world.principal, "all_wings", [connection])
    assert result == []


def test_list_counts_after_drops(world):
    world.private.readable.return_value = False
    result = gate(
        world.gateway,
        world.principal,
        "read_wing",
        {
            "drawers": [hit(world), {"wing": "shared", "room": "public"}],
            "count": 2,
            "total": 2,
        },
    )
    assert result["count"] == 1
    assert "total" not in result


@pytest.mark.parametrize("metadata", [{}, {"drawer_wing": None, "drawer_room": None}])
def test_missing_preview_metadata_is_unreadable(world, metadata):
    assert (
        gate(
            world.gateway,
            world.principal,
            "all_wings",
            [
                {
                    "connected_wing": "shared",
                    "connected_room": "public",
                    "drawer_preview": "words",
                    "drawer_id": "drawer",
                    **metadata,
                }
            ],
        )
        == []
    )


def test_private_endpoint_hides_connection_without_preview(world):
    world.private.readable.return_value = False
    assert (
        gate(
            world.gateway,
            world.principal,
            "all_wings",
            [
                {
                    "connected_wing": "private_threads",
                    "connected_room": world.thread.id,
                }
            ],
        )
        == []
    )


def test_gateway_returns_original_worker_response(world):
    from commonplace.gateway import Mediator, _tool_response

    response = _tool_response(1, {"wing": "shared", "content": "words"})
    workers = Mock(post=Mock(return_value=response))
    mediator = Mediator(Mock(), workers)
    mediator.gateway = world.gateway
    assert (
        mediator._tools_call(
            world.principal,
            {
                "id": 1,
                "params": {"name": "mempalace_status", "arguments": {}},
            },
        )
        is response
    )


@pytest.mark.parametrize(
    "tool,args",
    [
        ("mempalace_add_drawer", {"wing": "private_threads", "room": "{room}", "content": "words"}),
        (
            "mempalace_checkpoint",
            {"items": [{"wing": "private_threads", "room": "{room}", "content": "words"}]},
        ),
        ("mempalace_update_drawer", {"drawer_id": "drawer", "content": "new words"}),
        ("mempalace_delete_drawer", {"drawer_id": "drawer"}),
        ("mempalace_delete_drawers", {"drawer_ids": ["drawer"]}),
        (
            "mempalace_create_tunnel",
            {
                "source_wing": "shared",
                "source_room": "public",
                "target_wing": "private_threads",
                "target_room": "{room}",
            },
        ),
        (
            "mempalace_create_tunnel",
            {
                "source_wing": "shared",
                "source_room": "public",
                "target_wing": "shared",
                "target_room": "public",
                "target_drawer_id": "drawer",
            },
        ),
    ],
)
def test_refused_thread_writes_never_reach_worker(world, tool, args):
    import json

    from commonplace.gateway import Mediator, _tool_result
    from commonplace.policy import Policy

    w = world
    w.private.readable.return_value = False
    policy = Policy.build(
        profile="full",
        trust="trusted",
        read_wings=["*"],
        write_wings=["*"],
        scopes=[
            "memory:read",
            "memory:write",
            "memory:delete",
            "threads:read",
            "private:read",
            "private:write",
        ],
    )
    principal = replace(w.principal, policy=policy)
    workers = Mock()
    workers.call_tool.return_value = {"results": [hit(w)]}
    mediator = Mediator(Mock(), workers)
    mediator.gateway = w.gateway
    args = json.loads(json.dumps(args).replace("{room}", w.thread.id))
    response = mediator._tools_call(
        principal, {"id": 1, "params": {"name": tool, "arguments": args}}
    )
    expected = (
        "Room not found"
        if tool in ("mempalace_add_drawer", "mempalace_checkpoint")
        or "target_room" in args
        and not args.get("target_drawer_id")
        else "Drawer not found: drawer"
    )
    assert _tool_result(response) == {"success": False, "error": expected}
    workers.post.assert_not_called()


def test_gate_cost_info_only(world, monkeypatch):
    from statistics import median
    from time import perf_counter

    from commonplace.gateway import Mediator, _tool_response
    from commonplace.handoff import mirror_access

    w = world
    hits = {"results": [hit(w) for _ in range(100)]}
    gate(w.gateway, w.principal, "search", hits)  # Warm store and access path.

    def sample(call, repeats):
        values = []
        for _ in range(repeats):
            start = perf_counter()
            call()
            values.append((perf_counter() - start) * 1000)
        return median(values)

    before_hits = sample(lambda: hits, 100)
    after_hits = sample(lambda: gate(w.gateway, w.principal, "search", hits), 100)
    response = _tool_response(
        1,
        {
            "items": [{"wing": "shared", "content": "x" * 1024} for _ in range(1024)],
        },
    )
    workers = Mock(post=Mock(return_value=response))
    mediator = Mediator(Mock(), workers)
    mediator.gateway = w.gateway
    request = {"id": 1, "params": {"name": "mempalace_status", "arguments": {}}}
    # Baseline models the former whitelist's skip for a non-search tool.
    with monkeypatch.context() as patch:
        patch.setattr(mirror_access, "gate", lambda _g, _p, _r, result: result)
        before_large = sample(lambda: mediator._tools_call(w.principal, request), 20)
    after_large = sample(lambda: mediator._tools_call(w.principal, request), 20)
    print(
        f"P1 warm 100 hits ms: before={before_hits:.6f} after={after_hits:.6f} delta={after_hits - before_hits:.6f}"
    )
    size = len(response["result"]["content"][0]["text"].encode())
    print(
        f"P1 non-mirror {size} bytes ms: before={before_large:.6f} after={after_large:.6f} delta={after_large - before_large:.6f}"
    )


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("metadata", [{}, {"drawer_wing": "", "drawer_room": ""}])
def test_unhydrated_or_empty_preview_metadata_fails_closed(world, preview, metadata):
    connection = {
        "connected_wing": "shared",
        "connected_room": "public",
        "drawer_id": "drawer",
        **metadata,
    }
    if preview:
        connection["drawer_preview"] = "words"
    assert gate(world.gateway, world.principal, "all_wings", [connection]) == []


@pytest.mark.parametrize(
    "response",
    [
        None,
        {"error": "unavailable"},
        {"result": {"content": [{"text": "not JSON"}]}},
        {"result": {"content": [{"text": '"unexpected words"'}]}},
    ],
)
def test_gateway_malformed_drawer_response_is_not_found(world, response):
    from commonplace.gateway import Mediator, _tool_result

    workers = Mock(post=Mock(return_value=response))
    mediator = Mediator(Mock(), workers)
    mediator.gateway = world.gateway
    result = mediator._tools_call(
        world.principal,
        {
            "id": 1,
            "params": {"name": "mempalace_get_drawer", "arguments": {"drawer_id": "missing"}},
        },
    )
    if not isinstance(response, dict) or "result" not in response:
        assert result is response
    else:
        assert _tool_result(result) == {"error": "Drawer not found: None"}


@pytest.mark.parametrize("shape", ["wing", "theme", "public-source", "flat-echo"])
def test_derived_fact_requires_a_readable_source(world, shape):
    fact = {"subject": "Sample", "predicate": "states", "object": "words"}
    if shape == "flat-echo":
        fact = {
            "fact": "Sample states words",
            "wing": "private_threads",
            "source_thread": world.thread.id,
        }
    elif shape == "wing":
        fact.update(wing="private_threads", sources=[{"source_thread": world.thread.id}])
    else:
        fact.update(
            theme="private_threads" if shape == "theme" else "shared",
            source={"thread": world.thread.id},
        )
    result = {"facts": [fact]}
    assert gate(world.gateway, world.principal, "kg_read", result) is result
    world.private.readable.return_value = False
    assert gate(world.gateway, world.principal, "kg_read", result) == {"facts": []}


@pytest.mark.parametrize("field", ["wing", "theme"])
def test_private_fact_without_sources_fails_closed(world, field):
    assert gate(
        world.gateway,
        world.principal,
        "kg_read",
        {
            "facts": [
                {
                    field: "private_threads",
                    "subject": "Sample",
                    "predicate": "states",
                    "object": "words",
                }
            ]
        },
    ) == {"facts": []}


def test_fact_keeps_readable_secondary_source_and_scrubs_primary_provenance(world):
    hidden = "thr_ffffffffffffffff"
    source = {"source_thread": world.thread.id, "source_seq_start": 0}
    fact = {
        "wing": "private_threads",
        "object": "words",
        "source_thread": hidden,
        "origin_id": hidden,
        "origin_link": "/app/threads/" + hidden,
        "source_link": "/app/threads/" + hidden,
        "source": {"thread": hidden},
        "sources": [{"source_thread": hidden}, source],
    }
    result = gate(world.gateway, world.principal, "kg_read", {"facts": [fact]})
    assert result == {
        "facts": [{"wing": "private_threads", "object": "words", "sources": [source]}]
    }
    assert fact["source_thread"] == hidden  # Never mutate the worker's object.


@pytest.mark.parametrize("items", [None, "invalid", 42, {}])
def test_checkpoint_non_list_items_reaches_worker_validation(world, items):
    from commonplace.gateway import Mediator, _tool_response
    from commonplace.policy import Policy

    principal = replace(
        world.principal,
        policy=Policy.build(
            profile="full",
            trust="trusted",
            read_wings=["*"],
            write_wings=["*"],
            scopes=["memory:read", "private:read", "memory:write", "private:write"],
        ),
    )
    response = _tool_response(1, {"error": "items must be a list"})
    workers = Mock(post=Mock(return_value=response))
    mediator = Mediator(Mock(), workers)
    mediator.gateway = world.gateway
    assert (
        mediator._tools_call(
            principal,
            {
                "id": 1,
                "params": {
                    "name": "mempalace_checkpoint",
                    "arguments": {"items": items},
                },
            },
        )
        is response
    )
    workers.post.assert_called_once()


@pytest.mark.parametrize(
    "tool,args",
    [
        ("mempalace_search", {"query": "synthetic"}),
        ("mempalace_get_drawer", {"drawer_id": "drawer"}),
        ("mempalace_list_drawers", {"wing": "shared"}),
    ],
)
def test_worker_jsonrpc_error_preserves_envelope(world, tool, args):
    from commonplace.gateway import Mediator

    response = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Invalid params"}}
    workers = Mock(post=Mock(return_value=response))
    workers.call_tool.return_value = {"facts": []}
    mediator = Mediator(Mock(), workers)
    mediator.gateway = world.gateway
    assert (
        mediator._tools_call(
            world.principal, {"id": 1, "params": {"name": tool, "arguments": args}}
        )
        is response
    )


def test_unreadable_related_fact_drops_write_echo(world):
    result = {
        "wing": "private_threads",
        "fact": "Sample states new words",
        "sources": [{"source_thread": world.thread.id}],
        "previous": ["hidden old words"],
        "related_facts": [
            {
                "wing": "private_threads",
                "object": "hidden old words",
                "sources": [{"source_thread": "thr_ffffffffffffffff"}],
            }
        ],
    }
    assert gate(world.gateway, world.principal, "kg_write", result) == {
        "error": "not found",
        "denied": True,
    }


def test_fact_search_is_gated_before_cli_rendering(world):
    from commonplace.gateway import Mediator, _tool_response, _tool_result

    world.private.readable.return_value = False
    principal = replace(
        world.principal,
        policy=replace(world.principal.policy, scopes=(*world.principal.policy.scopes, "kg:read")),
    )
    workers = Mock(
        post=Mock(return_value=_tool_response(1, {"results": [], "cli_output": "No drawers"}))
    )
    workers.call_tool.return_value = {
        "facts": [
            {
                "subject": "Sample",
                "predicate": "states",
                "object": "hidden words",
                "theme": "private_threads",
                "source": {"thread": world.thread.id},
            }
        ]
    }
    mediator = Mediator(Mock(), workers)
    mediator.gateway = world.gateway
    response = mediator._tools_call(
        principal,
        {
            "id": 1,
            "params": {
                "name": "mempalace_search",
                "arguments": {"query": "Sample", "cli_compatible": True},
            },
        },
    )
    result = _tool_result(response)
    assert result.get("facts", []) == []
    assert result["cli_output"] == "No drawers"


def test_memory_map_uses_same_fact_source_rule(world, tmp_path):
    from commonplace import memory_tools
    from mempalace.knowledge_graph import KnowledgeGraph
    from mempalace.paths import KNOWLEDGE_GRAPH_FILENAME

    w = world
    principal = replace(
        w.principal,
        policy=replace(w.principal.policy, scopes=(*w.principal.policy.scopes, "kg:read")),
    )
    hidden = "thr_ffffffffffffffff"
    w.store.threads[hidden] = replace(w.thread, id=hidden)
    path = tmp_path / "palace" / KNOWLEDGE_GRAPH_FILENAME
    path.parent.mkdir()
    with KnowledgeGraph(str(path)) as graph:
        for name in ("PersonOne", "PersonTwo", "HiddenPerson"):
            graph.add_entity(name, "person")
        graph.add_triple("PersonOne", "knows", "PersonTwo", wing="shared")
        graph.add_triple(
            "HiddenPerson",
            "private_knows",
            "PersonOne",
            wing="private_threads",
            source_thread=hidden,
            source_seq_start=0,
            source_seq_end=0,
        )
    ctx = SimpleNamespace(
        principal=principal, gateway=w.gateway, workspace_dir=lambda: str(tmp_path)
    )
    nodes, links = [], []
    memory_tools._people(ctx, nodes, links, set())
    assert "HiddenPerson" in {node["label"] for node in nodes}
    del w.store.threads[hidden]
    nodes, links = [], []
    memory_tools._people(ctx, nodes, links, set())
    assert {node["label"] for node in nodes} == {"PersonOne", "PersonTwo"}
    assert {link["label"] for link in links} == {"knows"}
