"""Current endpoint proof for explicit tunnel reads."""

# ruff: noqa: F811

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace.gateway import _tool_result
from commonplace.handoff.models import ThreadMessage
from tests._commonplace_helpers import functional_worker_transport, hub_fixture  # noqa: F401

TOOLS = [
    "mempalace_traverse",
    "mempalace_find_tunnels",
    "mempalace_graph_stats",
    "mempalace_list_tunnels",
    "mempalace_follow_tunnels",
]
hub = hub_fixture


@pytest.fixture
def world(hub, monkeypatch, tmp_path, request):
    from mempalace import mcp_server as mcp, palace_graph as graph

    monkeypatch.setattr(graph, "MempalaceConfig", lambda: mcp._config)
    monkeypatch.setattr(mcp, "_READ_ONLY", True)
    backend_name = "sqlite_exact"
    monkeypatch.setenv("MEMPALACE_BACKEND", backend_name)
    monkeypatch.setattr(mcp._config, "_palace_path_override", str(tmp_path / "exact"))
    from mempalace.backends import PalaceRef
    from mempalace.backends.sqlite_exact import SQLiteExactBackend

    backend = SQLiteExactBackend()
    collection = backend.get_collection(
        palace=PalaceRef(id=mcp._config.palace_path, local_path=mcp._config.palace_path),
        collection_name=mcp._config.collection_name,
        create=True,
    )
    from mempalace.backends.embedding_wrapper import EmbeddingCollection

    collection = EmbeddingCollection(collection)
    monkeypatch.setattr(mcp, "_get_collection", lambda **kwargs: collection)
    monkeypatch.setattr(mcp, "_selected_backend_name", lambda: backend_name)
    monkeypatch.setattr(mcp, "_is_chroma_backend", lambda: False)
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    monkeypatch.setenv("COMMONPLACE_RULES", "off")
    member = hub.db.create_user("member@example.test")
    hub.db.add_member(hub.workspace, member, "member", actor=hub.admin)
    store = hub.gateway.handoff.store_for(hub.workspace)
    readable = store.store(
        hub.workspace,
        "native-readable",
        [ThreadMessage(0, "user", "message", "readable", "readable")],
        actor=member,
    )
    hidden = store.store(
        hub.workspace,
        f"capture:{hub.admin}:native-hidden",
        [ThreadMessage(0, "user", "message", "hidden", "hidden")],
        actor=hub.admin,
        wing="private_chats",
    )

    class Tokens(dict):
        def __repr__(self):
            return "<synthetic credentials>"

    tokens = Tokens()
    for kind, wings in (("A", ["*"]),):
        tokens[kind] = hub.token(
            f"reader-{kind}",
            user=member,
            tools=TOOLS,
            scopes=["memory:read", "private:read", "threads:read"],
            read_wings=wings,
            trust="trusted",
        )
        principal = hub.db.authenticate_token(tokens[kind])
        assert principal.role != "owner" and principal.policy.trust == "trusted"
        assert all(
            principal.policy.has_scope(scope)
            for scope in ("memory:read", "private:read", "threads:read")
        )
        assert principal.policy.sees_all_wings is (kind == "A")
        assert hub.gateway.handoff.recall.can_read(principal, readable.id)
        assert not hub.gateway.handoff.recall.can_read(principal, hidden.id)
        assert store.get(hidden.id) is not None
        for tool in TOOLS:
            assert principal.policy.refusal(tool) is None
    hits = []
    for name in (
        "traverse",
        "find_tunnels",
        "graph_stats",
        "_sqlite_graph_stats",
        "list_tunnels",
        "follow_tunnels",
    ):
        original = getattr(mcp, name)

        def spy(*a, _name=name, _original=original, **kw):
            hits.append(_name)
            return _original(*a, **kw)

        monkeypatch.setattr(mcp, name, spy)
    from commonplace.gateway import WorkerClient

    worker_call = WorkerClient.call_tool

    def worker_spy(self, principal, name, args):
        hits.append(name)
        return worker_call(self, principal, name, args)

    monkeypatch.setattr(WorkerClient, "call_tool", worker_spy)
    mcp._invalidate_overview_caches()
    yield SimpleNamespace(
        h=hub, col=collection, mcp=mcp, readable=readable, hidden=hidden, tokens=tokens, hits=hits
    )
    hub.gateway.handoff.close()
    hub.gateway.webhooks.stop()
    backend.close()


def call(w, tool, args):
    args = dict(args)
    # The base RPC schema omits record; read-only mode passes record=False.
    args.pop("record", None)
    status, raw = w.h.raw(
        w.tokens["A"],
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
    )
    assert status == 200
    result = _tool_result(json.loads(raw))
    assert result is not None, raw
    return result


def seed(w, rows):
    from mempalace import palace_graph as graph

    w.col._inner.add(
        ids=[f"row-{i:04}" for i in range(len(rows))],
        documents=["verbatim" for _ in rows],
        metadatas=rows,
        embeddings=[[1.0] + [0.0] * 383 for _ in rows],
    )
    graph.invalidate_graph_cache()
    w.mcp._invalidate_overview_caches()


def control(w, rows, monkeypatch):
    from mempalace import palace_graph as graph

    source = Path(w.mcp._config.palace_path)
    target = source.parent / "control" / "palace"
    shutil.copytree(source, target)
    w.mcp._config._palace_path_override = str(target)
    from mempalace.backends import PalaceRef
    from mempalace.backends.sqlite_exact import SQLiteExactBackend

    backend = SQLiteExactBackend()
    col = backend.get_collection(
        palace=PalaceRef(id=str(target), local_path=str(target)),
        collection_name=w.mcp._config.collection_name,
        create=False,
    )
    col.delete(ids=[f"row-{i:04}" for i in range(2, len(rows))])
    monkeypatch.setattr(w.mcp, "_get_collection", lambda **kw: col)
    graph._save_tunnels([], w.mcp._config)
    graph.invalidate_graph_cache()
    w.mcp._invalidate_overview_caches()
    return backend


@pytest.mark.parametrize("path", ["sqlite", "collection"])
def test_card253_s05_stats(world, functional_worker_transport, monkeypatch, path):
    w = world
    if path == "collection":
        monkeypatch.setattr(w.mcp, "_selected_backend_name", lambda: "collection_fixture")
    rows = [
        {"wing": "wing_a", "room": "visible", "hall": "h"},
        {"wing": "wing_b", "room": "visible", "hall": "h"},
        {"wing": "private_chats", "room": w.hidden.id, "hall": "secret"},
    ]
    seed(w, rows)
    from mempalace import palace_graph as graph

    graph._save_tunnels(
        [
            {
                "id": "hidden-tunnel",
                "source": {"wing": "wing_a", "room": "visible"},
                "target": {"wing": "private_chats", "room": w.hidden.id},
            }
        ],
        w.mcp._config,
    )
    reply = call(w, "mempalace_graph_stats", {})
    assert any(
        name in w.hits for name in ("mempalace_graph_stats", "graph_stats", "_sqlite_graph_stats")
    )
    backend = control(w, rows, monkeypatch)
    try:
        expected = call(w, "mempalace_graph_stats", {})
        assert expected["total_rooms"] == 1 and expected["total_room_instances"] == 2
        assert reply == expected, "s05 graph counts include H"
    finally:
        backend.close()


def test_card253_s05_traverse(world, functional_worker_transport):
    w = world
    seed(
        w,
        [
            {"wing": "wing_a", "room": "readable_start"},
            {"wing": "wing_b", "room": "distant_room"},
            {"wing": "wing_a", "room": w.hidden.id},
            {"wing": "wing_b", "room": w.hidden.id},
        ],
    )
    reply = call(w, "mempalace_traverse", {"start_room": "readable_start", "max_hops": 2})
    assert ("mempalace_traverse" in w.hits or "traverse" in w.hits) and isinstance(reply, list)
    assert "readable_start" in [r["room"] for r in reply]
    assert "distant_room" not in [r["room"] for r in reply], "s05 H bridged readable nodes"


def test_card253_s05_find_top(world, functional_worker_transport, monkeypatch):
    w = world
    store = w.h.gateway.handoff.store_for(w.h.workspace)
    rows = [
        {"wing": wing, "room": "visible_candidate", "hall": "h"} for wing in ("wing_a", "wing_b")
    ]
    for i in range(50):
        thread = store.store(
            w.h.workspace,
            f"capture:{w.h.admin}:hidden-{i}",
            [ThreadMessage(0, "user", "message", "hidden", "hidden")],
            actor=w.h.admin,
            wing="private_chats",
        )
        principal = w.h.db.authenticate_token(w.tokens["A"])
        assert not w.h.gateway.handoff.recall.can_read(principal, thread.id)
        rows.extend(
            {"wing": wing, "room": thread.id, "hall": "secret", "date": "2099"}
            for wing in ("wing_a", "wing_b", "wing_a")
        )
    seed(w, rows)
    args = {"wing_a": "wing_a", "wing_b": "wing_b"}
    reply = call(w, "mempalace_find_tunnels", args)
    assert "mempalace_find_tunnels" in w.hits or "find_tunnels" in w.hits
    backend = control(w, rows, monkeypatch)
    try:
        expected = call(w, "mempalace_find_tunnels", args)
        assert [r["room"] for r in expected] == ["visible_candidate"]
        assert reply == expected, "s05 H displaced visible graph candidate"
    finally:
        backend.close()


@pytest.mark.parametrize(
    "tool", ["mempalace_list_tunnels", "mempalace_follow_tunnels", "mempalace_graph_stats"]
)
def test_card253_s05_label(world, functional_worker_transport, tool):
    from mempalace import palace_graph as graph

    w = world
    seed(w, [{"wing": "wing_a", "room": "visible"}])
    row = {
        "id": "label-tunnel",
        "source": {"wing": "wing_a", "room": "visible"},
        "target": {"wing": "wing_b", "room": "ordinary"},
        "label": f"see {w.hidden.id}",
    }
    graph._save_tunnels([row], w.mcp._config)
    assert graph._load_tunnels(w.mcp._config) == [row]
    args = {"wing": "wing_a", "room": "visible"} if tool == "mempalace_follow_tunnels" else {}
    reply = call(w, tool, args)
    assert "mempalace_list_tunnels" in w.hits or tool == "mempalace_graph_stats"
    assert reply["explicit_tunnels"] == 0 if tool == "mempalace_graph_stats" else reply == [], (
        "s05 hidden tunnel label retained"
    )


@pytest.mark.parametrize("path", ["sqlite", "collection"])
def test_card253_s05_readable_guard(world, functional_worker_transport, monkeypatch, path):
    from mempalace import palace_graph as graph

    w = world
    if path == "collection":
        monkeypatch.setattr(w.mcp, "_selected_backend_name", lambda: "collection_fixture")
        monkeypatch.setattr(w.mcp, "_graph_sqlite_reader", lambda: None)
    rows = [
        {"wing": wing, "room": "visible", "hall": "hall", "date": date, "note": "stored untouched"}
        for wing, date in (("wing_a", "2026-01"), ("wing_b", "2026-02"))
    ]
    seed(w, rows)
    row = {
        "id": "readable",
        "source": {"wing": "wing_a", "room": "visible"},
        "target": {"wing": "wing_b", "room": "ordinary"},
        "label": "👩‍💻 50% ready",
        "custom": {"preserve": True},
        "access_count": 9,
    }
    graph._save_tunnels([row], w.mcp._config)
    assert call(w, "mempalace_list_tunnels", {}) == [row]
    assert (
        call(w, "mempalace_follow_tunnels", {"wing": "wing_a", "room": "visible"})[0]["label"]
        == row["label"]
    )
    found = call(w, "mempalace_find_tunnels", {"wing_a": "wing-a"})
    assert found == [
        {
            "room": "visible",
            "wings": ["wing_a", "wing_b"],
            "halls": ["hall"],
            "count": 2,
            "recent": "2026-02",
        }
    ]
    stats = call(w, "mempalace_graph_stats", {})
    assert stats == {
        "total_rooms": 1,
        "total_room_instances": 2,
        "tunnel_rooms": 1,
        "passive_tunnel_rooms": 1,
        "explicit_tunnels": 1,
        "total_edges": 1,
        "total_connections": 2,
        "rooms_per_wing": {"wing_a": 1, "wing_b": 1},
        "top_tunnels": [{"room": "visible", "wings": ["wing_a", "wing_b"], "count": 2}],
    }
    assert graph._load_tunnels(w.mcp._config) == [row]
    assert (
        w.col.get(ids=["row-0000"], include=["metadatas"])["metadatas"][0]["note"]
        == "stored untouched"
    )


def test_card253_s05_literal_guard(world):
    w = world
    w.tokens["A"] = w.h.token(
        "literal",
        user=w.h.db.authenticate_token(w.tokens["A"]).user_id,
        tools=TOOLS,
        scopes=["memory:read", "threads:read"],
        read_wings=["wing_a"],
        trust="trusted",
    )
    principal = w.h.db.authenticate_token(w.tokens["A"])
    assert principal.role != "owner" and not principal.policy.sees_all_wings
    for tool, args in (
        ("mempalace_graph_stats", {}),
        ("mempalace_find_tunnels", {}),
        ("mempalace_traverse", {"start_room": "visible"}),
    ):
        assert principal.policy.refusal(tool) is not None
        status, raw = w.h.raw(
            w.tokens["A"],
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            },
        )
        assert status == 200 and json.loads(raw)["error"]["code"] == -32601


def test_card253_s05_owner_guard(world, functional_worker_transport):
    from commonplace.sessions import person_principal

    w = world
    owner = person_principal(w.h.db, w.h.admin, w.h.workspace)
    assert owner.person and owner.role == "owner" and owner.policy.trust == "trusted"
    assert owner.policy.has_scope("threads:read")
    assert w.h.gateway.handoff.recall.can_read(owner, w.hidden.id)
    seed(w, [{"wing": "wing_a", "room": w.hidden.id}, {"wing": "wing_b", "room": w.hidden.id}])
    assert (
        w.h.gateway.call_memory_tool(owner, "mempalace_graph_stats", {})["total_room_instances"]
        == 2
    )
    assert (
        w.h.gateway.call_memory_tool(owner, "mempalace_find_tunnels", {})[0]["room"] == w.hidden.id
    )


@pytest.mark.parametrize(
    "args", [{"max_hops": []}, {"max_hops": None}, {"max_hops": "2"}, {"start_room": []}]
)
def test_card253_s05_odd_inputs(world, args):
    reply = call(world, "mempalace_traverse", {"start_room": "visible", **args})
    assert isinstance(reply, dict) and isinstance(reply.get("error"), str)
