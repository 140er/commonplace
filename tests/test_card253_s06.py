"""Logical pages authorize physical membership before grouping and slicing."""

import pytest

from commonplace.handoff.models import ThreadMessage
from tests._commonplace_helpers import functional_worker_transport, hub_fixture  # noqa: F401


@pytest.fixture
def page_world(hub, collection, monkeypatch, request):
    request.getfixturevalue("functional_worker_transport")
    from mempalace import mcp_server as mcp

    monkeypatch.setenv("COMMONPLACE_RULES", "off")
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    monkeypatch.delenv("MEMPALACE_BACKEND", raising=False)
    monkeypatch.setattr(mcp, "_get_collection", lambda **kw: collection)
    member = hub.db.create_user("page-member@example.test")
    hub.db.add_member(hub.workspace, member, "member", actor=hub.admin)
    store = hub.gateway.handoff.store_for(hub.workspace)
    visible = store.store(
        hub.workspace, "visible", [ThreadMessage(0, "user", "message", "v", "v")], actor=member
    )
    other = hub.db.create_user("private-author@example.test")
    hidden = store.store(
        hub.workspace,
        f"capture:{other}:hidden",
        [ThreadMessage(0, "user", "message", "h", "h")],
        actor=other,
        wing="private_chats",
    )
    token = hub.token(
        "page-reader",
        user=member,
        tools=["mempalace_list_drawers", "mempalace_get_drawers"],
        scopes=["memory:read", "private:read", "threads:read"],
        read_wings=["*"],
        trust="trusted",
    )
    principal = hub.db.authenticate_token(token)
    assert principal.role != "owner" and principal.policy.sees_all_wings
    assert all(
        principal.policy.has_scope(s) for s in ("memory:read", "private:read", "threads:read")
    )
    assert principal.policy.refusal("mempalace_list_drawers") is None
    assert hub.gateway.handoff.recall.can_read(principal, visible.id)
    assert not hub.gateway.handoff.recall.can_read(principal, hidden.id)
    yield hub, mcp, collection, token, visible.id, hidden.id
    hub.gateway.handoff.close()
    hub.gateway.webhooks.stop()


def seed(w, hidden=True, chunks=False):
    _, _, col, _, visible, private = w
    ids = ["b-visible", "c-visible"]
    metas = [{"wing": "wing_fixture", "room": visible, "filed_at": "2026-10-01"} for _ in ids]
    if chunks:
        for n, meta in enumerate(metas):
            meta.update(parent_drawer_id="b-logical", chunk_index=n)
    if hidden:
        ids.insert(0, "a-hidden")
        metas.insert(
            0,
            {
                "wing": "wing_fixture",
                "room": private,
                "filed_at": "2026-10-01",
                **({"parent_drawer_id": "b-logical", "chunk_index": -1} if chunks else {}),
            },
        )
    col.add(ids=ids, metadatas=metas, documents=[f"text {i}" for i in ids])
    assert col.get(ids=["a-hidden"], include=["metadatas"])["ids"] == (
        ["a-hidden"] if hidden else []
    )


def page(w, offset=0, **args):
    h, _, _, token, _, _ = w
    result, raw = h.call(
        token, "mempalace_list_drawers", wing="wing_fixture", limit=1, offset=offset, **args
    )
    assert result is not None, raw
    return result


@pytest.mark.parametrize("path", ["sqlite", "sdk"])
def test_card253_s06_drawer_page(page_world, monkeypatch, path):
    w = page_world
    _, mcp, col, _, _, _ = w
    monkeypatch.setattr(mcp, "_is_chroma_backend", lambda: path == "sqlite")
    from mempalace.backends import chroma_sqlite

    branches = []
    sql_read = chroma_sqlite.sqlite_list_id_metadata
    sdk_get = type(col).get

    def sqlite_read(*args, **kwargs):
        branches.append("sqlite")
        return sql_read(*args, **kwargs)

    def sdk_read(self, *args, **kwargs):
        if "documents" in kwargs.get("include", []):
            branches.append("sdk")
        return sdk_get(self, *args, **kwargs)

    monkeypatch.setattr(chroma_sqlite, "sqlite_list_id_metadata", sqlite_read)
    monkeypatch.setattr(type(col), "get", sdk_read)
    hits = []
    original = mcp._collapse_drawer_rows

    def collapse(ids, docs, metas):
        value = original(ids, docs, metas)
        hits.append([d["drawer_id"] for d in value])
        return value

    monkeypatch.setattr(mcp, "_collapse_drawer_rows", collapse)
    seed(w)
    assert (
        original(
            ["a-hidden", "b-visible", "c-visible"],
            [],
            col.get(ids=["a-hidden", "b-visible", "c-visible"], include=["metadatas"])["metadatas"],
        )[0]["drawer_id"]
        == "a-hidden"
    )
    present = [page(w, n) for n in (0, 1)]
    assert hits and path in branches
    assert all(ids[0] == "a-hidden" for ids in hits if "a-hidden" in ids)
    col.delete(ids=["a-hidden"])
    mcp._invalidate_overview_caches()
    control = [page(w, n) for n in (0, 1)]
    assert present == control, "s06 H shifted drawer page"
    assert all(p["total"] == 2 and p["count"] == 1 for p in present)


def test_card253_s06_readable_guards(page_world):
    w = page_world
    seed(w, hidden=False, chunks=True)
    result = page(w, since="2026-10-01", before="2026-10-02")
    assert result["total"] == result["count"] == 1
    assert result["drawers"][0]["chunk_ids"] == ["b-visible", "c-visible"]
    assert result["drawers"][0]["content_preview"] == "text b-visibletext c-visible"
    assert page(w, since="2026-10-02")["total"] == 0
    h, _, _, token, _, _ = w
    batch, _ = h.call(token, "mempalace_get_drawers", drawer_ids=["b-visible", "missing"])
    assert batch["count"] == 2 and batch["errors"] == 1
    assert batch["results"][1]["drawer_id"] == "missing"


@pytest.mark.parametrize("path", ["sqlite", "sdk"])
def test_card253_s06_chunk_visibility(page_world, monkeypatch, path):
    from commonplace import sessions

    w = page_world
    h, mcp, _, token, _, hidden = w
    monkeypatch.setattr(mcp, "_is_chroma_backend", lambda: path == "sqlite")
    seed(w, chunks=True)
    result = page(w)
    assert result["total"] == result["count"] == 1
    drawer = result["drawers"][0]
    assert drawer["chunk_ids"] == ["b-visible", "c-visible"]
    assert drawer["content_preview"] == "text b-visibletext c-visible"
    owner = sessions.person_principal(h.db, h.admin, h.workspace)
    assert owner.person and owner.role == "owner"
    assert owner.policy.trust == "trusted" and owner.policy.has_scope("threads:read")
    assert not h.gateway.handoff.recall.can_read(owner, hidden)
    assert (
        h.gateway.call_memory_tool(owner, "mempalace_list_drawers", {"wing": "fixture", "limit": 1})
        == result
    )


@pytest.mark.parametrize("path", ["sqlite", "sdk"])
def test_card253_s06_changed_descriptor(page_world, monkeypatch, path):
    from commonplace.gateway import WorkerClient

    w = page_world
    _, mcp, col, _, _, hidden = w
    seed(w, hidden=False)
    monkeypatch.setattr(mcp, "_is_chroma_backend", lambda: path == "sqlite")
    original = WorkerClient.call_tool
    calls = []

    def call(self, principal, name, args):
        if name == "mempalace_list_drawers" and "thread_scope" in args:
            calls.append(name)
            col.update(ids=["b-visible"], metadatas=[{"room": hidden}])
        return original(self, principal, name, args)

    monkeypatch.setattr(WorkerClient, "call_tool", call)
    result = page(w)
    assert calls
    assert result["total"] == 1 and result["drawers"][0]["drawer_id"] == "c-visible"


@pytest.mark.parametrize("fault", ["malformed", "raised", "deadline"])
def test_card253_s06_incomplete_projection(fault):
    from types import SimpleNamespace

    from commonplace.handoff.drawer_pages import project
    from commonplace.rules import checks

    class Workers:
        def call_tool(self, *args):
            if fault == "raised":
                raise RuntimeError("private failure")
            if fault == "deadline":
                checks.remaining(0)
            return {"probe": {"rows": [], "next": "bad"}, "taxonomy": {}}

    principal = SimpleNamespace(workspace_id="x")
    result = project(
        SimpleNamespace(),
        principal,
        Workers(),
        {"offset": 7, "limit": 2},
        timeout=0 if fault == "deadline" else 1,
    )
    assert result == {"drawers": [], "total": 0, "count": 0, "offset": 7, "limit": 2}


@pytest.mark.parametrize("incomplete", [False, True])
def test_card253_s06_complete_enumeration(page_world, monkeypatch, incomplete):
    from commonplace.gateway import WorkerClient
    from commonplace.handoff import mirror_access

    w = page_world
    _, mcp, col, _, visible, _ = w
    monkeypatch.setattr(mcp, "_is_chroma_backend", lambda: False)
    seed(w)
    ids = [f"d-visible-{n:03}" for n in range(501)]
    col.add(
        ids=ids,
        metadatas=[{"wing": "wing_fixture", "room": visible} for _ in ids],
        documents=["visible"] * len(ids),
    )
    original, gates = WorkerClient.call_tool, []
    probes = []
    gate = mirror_access.gate

    def checked_gate(gateway, principal, rule, result):
        cleaned = gate(gateway, principal, rule, result)
        if isinstance(result, dict) and "drawers" in result:
            gates.append(True)
            assert cleaned == result
        return cleaned

    def call(self, principal, name, args):
        result = original(self, principal, name, args)
        if name == "mempalace_get_taxonomy":
            probes.append(args["thread_scope"]["after"])
            assert len(result["probe"]["rows"]) <= 500
            if incomplete and len(probes) == 2:
                return {"taxonomy": {}, "probe": {"rows": [], "next": "broken"}}
        return result

    monkeypatch.setattr(WorkerClient, "call_tool", call)
    monkeypatch.setattr(mirror_access, "gate", checked_gate)
    result = page(w, offset=502)
    assert len(probes) == 2 and gates
    assert result["total"] == (0 if incomplete else 503)
    assert result["count"] == (0 if incomplete else 1)


def test_card253_s06_provenance_scrub_keeps_visible_total(page_world):
    w = page_world
    _, _, col, _, _, hidden = w
    seed(w, hidden=False)
    col.update(ids=["b-visible"], metadatas=[{"source_thread": hidden}])
    result = page(w)
    assert result.get("total") == 2 and result["count"] == 1
    assert "source_thread" not in result["drawers"][0]["metadata"]
