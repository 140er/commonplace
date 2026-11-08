"""Public hallway eligibility and visible-page witnesses in both stored formats."""

import json
from types import SimpleNamespace

import pytest

from commonplace import sessions
from commonplace.gateway import WorkerClient
from commonplace.handoff.models import ThreadMessage
from tests._commonplace_helpers import functional_worker_transport, hub_fixture  # noqa: F401

TOOL = "mempalace_list_hallways"


@pytest.fixture
def world(hub, monkeypatch, tmp_path, functional_worker_transport):  # noqa: F811
    from mempalace import hallways, mcp_server

    monkeypatch.setenv("COMMONPLACE_RULES", "off")
    member = hub.db.create_user("reader@example.test")
    hub.db.add_member(hub.workspace, member, "member", actor=hub.admin)
    store = hub.gateway.handoff.store_for(hub.workspace)
    readable = store.store(
        hub.workspace,
        "shared",
        [ThreadMessage(0, "user", "message", "shared", "shared")],
        actor=member,
    )
    hidden = store.store(
        hub.workspace,
        f"capture:{hub.admin}:hidden",
        [ThreadMessage(0, "user", "message", "private", "private")],
        actor=hub.admin,
        wing="private_chats",
    )
    token = hub.token(
        "hallway-reader",
        user=member,
        tools=[TOOL],
        scopes=["memory:read", "private:read", "threads:read"],
        read_wings=["*"],
        trust="trusted",
    )
    principal = hub.db.authenticate_token(token)
    assert principal.role != "owner" and principal.policy.trust == "trusted"
    assert principal.policy.sees_all_wings
    assert all(
        principal.policy.has_scope(s) for s in ("memory:read", "private:read", "threads:read")
    )
    assert principal.policy.refusal(TOOL) is None
    assert hub.gateway.handoff.recall.can_read(principal, readable.id)
    assert not hub.gateway.handoff.recall.can_read(principal, hidden.id)
    assert store.get(hidden.id) is not None
    calls = []
    original = WorkerClient.call_tool

    def call(self, p, name, args):
        result = original(self, p, name, args)
        if name == TOOL:
            calls.append((args, result))
        return result

    monkeypatch.setattr(WorkerClient, "call_tool", call)
    # Public calls use post on the old base; this pass-through proves that path too.
    worker_hits = []
    original_page = mcp_server.list_hallways_page

    def page(*a, **kw):
        worker_hits.append(True)
        return original_page(*a, **kw)

    monkeypatch.setattr(mcp_server, "list_hallways_page", page)
    w = SimpleNamespace(
        h=hub,
        principal=principal,
        token=token,
        readable=readable,
        hidden=hidden,
        hallways=hallways,
        root=tmp_path,
        calls=calls,
        worker_hits=worker_hits,
    )
    yield w
    hub.gateway.handoff.close()
    hub.gateway.webhooks.stop()


def record(w, ident, **changes):
    return {
        "id": ident,
        "wing": "wing_fixture",
        "rooms": [w.readable.id],
        "label": "Family 👩‍💻 25% complete %ab",
        "co_occurrence_count": 2,
        **changes,
    }


def install(w, monkeypatch, backend, records, name):
    path = w.root / name / "hallways.json"
    path.parent.mkdir()
    monkeypatch.setattr(w.hallways, "_get_hallway_file", lambda *a, **k: str(path))
    monkeypatch.setattr(w.hallways, "_legacy_hallway_file", lambda: str(path))
    if backend == "sqlite":
        w.hallways._save_hallways(records)
    else:
        path.write_text(json.dumps({"hallways": records}), encoding="utf-8")
    assert w.hallways.list_hallways() == records
    from mempalace import mcp_server

    mcp_server._invalidate_overview_caches()


def public(w, **args):
    value, raw = w.h.call(w.token, TOOL, wing="wing_fixture", **args)
    assert isinstance(value, dict) and isinstance(value.get("hallways"), list), raw
    return value


@pytest.mark.parametrize(
    "backend,mode",
    [(b, m) for b in ("sqlite", "json") for m in ("rooms", "label_only")],
    ids=[f"{b}-{m}" for b in ("sqlite", "json") for m in ("rooms", "label_only")],
)
def test_card253_s03_hallway(world, monkeypatch, backend, mode):
    w = world
    visible = record(w, "visible")
    hidden = record(
        w,
        "hidden",
        **(
            {"rooms": [w.readable.id, w.hidden.id]}
            if mode == "rooms"
            else {"rooms": [], "label": f"legacy {w.hidden.id}"}
        ),
    )
    install(w, monkeypatch, backend, [visible, hidden], "present")
    reply = public(w, limit=10, offset=0)
    assert w.worker_hits or w.calls
    install(w, monkeypatch, backend, [visible], "absent")
    control = public(w, limit=10, offset=0)
    assert "hidden" not in [h["id"] for h in reply["hallways"]], "s03 hidden hallway retained"
    assert reply == control


@pytest.mark.parametrize("backend", ["sqlite", "json"])
def test_card253_s03_page(world, monkeypatch, backend):
    w = world
    visible = [record(w, "first"), record(w, "second")]
    hidden = record(w, "hidden", rooms=[w.hidden.id], co_occurrence_count=99)
    install(w, monkeypatch, backend, [*visible, hidden], "present")
    replies = [public(w, limit=1, offset=i) for i in (0, 1)]
    assert w.worker_hits or w.calls
    install(w, monkeypatch, backend, visible, "absent")
    controls = [public(w, limit=1, offset=i) for i in (0, 1)]
    assert replies == controls, "s03 hidden hallway shifted page"


@pytest.mark.parametrize("backend", ["sqlite", "json"])
def test_card253_s03_readable_guard(world, monkeypatch, backend):
    w = world
    rows = [record(w, "first"), record(w, "second", label=f"shared {w.readable.id}")]
    install(w, monkeypatch, backend, rows, "readable")
    expected = {"hallways": rows, "total": 2, "count": 2, "limit": 10, "offset": 0}
    assert public(w, limit=10, offset=0) == expected
    assert public(w, limit=10, offset=0) == expected
    owner = sessions.person_principal(w.h.db, w.h.admin, w.h.workspace)
    assert owner.person and owner.role == "owner" and owner.policy.trust == "trusted"
    assert owner.policy.has_scope("threads:read")
    assert w.h.gateway.handoff.recall.can_read(owner, w.readable.id)
    assert (
        w.h.gateway.call_memory_tool(owner, TOOL, {"wing": "wing_fixture", "limit": 10}) == expected
    )


@pytest.mark.parametrize("backend", ["sqlite", "json"])
def test_card253_s03_eligibility_protocol(world, monkeypatch, backend):
    from commonplace.handoff import mirror_access

    w = world
    rows = [
        record(w, "visible"),
        record(w, "label", label=f"{w.readable.id} {w.hidden.id}"),
        record(w, "bad", rooms="not a list"),
        record(w, "blank", rooms=[""]),
        record(w, "mirror", wing="private_threads", rooms=[]),
        record(w, "private-label", label=f"capture:{w.h.admin}:hidden"),
        record(w, "legacy", rooms=[], label="plain label"),
    ]
    install(w, monkeypatch, backend, rows, "eligibility")
    gates = []
    original = mirror_access.gate

    def gate(*a):
        result = original(*a)
        if isinstance(a[-1], dict) and "hallways" in a[-1]:
            gates.append(a[-1]["hallways"] == result["hallways"])
        return result

    monkeypatch.setattr(mirror_access, "gate", gate)
    reply = public(w, limit=10)
    assert [r["id"] for r in reply["hallways"]] == ["visible", "legacy"]
    assert gates == [True]  # No filtering deferred until after the page.
    assert public(w, limit=1, offset=8) == {
        "hallways": [],
        "total": 2,
        "count": 0,
        "offset": 8,
        "limit": 1,
    }
    assert w.hallways.list_hallways() == rows  # O4: no storage cleanup.
    store = w.h.gateway.handoff.store_for(w.h.workspace)
    with store._lock:
        store._conn.execute(
            "UPDATE threads SET wing='private_chats', source_thread_id=? WHERE id=?",
            (f"capture:{w.h.admin}:revoked", w.readable.id),
        )
    assert [r["id"] for r in public(w, limit=10)["hallways"]] == ["legacy"]


@pytest.mark.parametrize("backend", ["sqlite", "json"])
def test_card253_s03_complete_pages(world, monkeypatch, backend):
    w = world
    rows = [record(w, f"h-{i}", rooms=[w.hidden.id]) for i in range(501)]
    rows += [record(w, "late-first"), record(w, "late-second")]
    install(w, monkeypatch, backend, rows, "many")
    assert [r["id"] for r in public(w, limit=1, offset=1)["hallways"]] == ["late-second"]
    probes = [r[0]["thread_scope"] for r in w.calls if r[0]["thread_scope"].get("probe")]
    assert len(probes) == 2 and probes[1]["after"] == 500


@pytest.mark.parametrize("backend", ["sqlite", "json"])
@pytest.mark.parametrize("mode", ["changed", "malformed", "failure", "empty"])
def test_card253_s03_snapshot_and_faults(world, monkeypatch, backend, mode):
    w = world
    install(w, monkeypatch, backend, [record(w, "visible")], "fault")
    original = WorkerClient.call_tool
    hits = []

    def call(self, p, name, args):
        scope = args.get("thread_scope", {})
        if name == TOOL and scope.get("probe"):
            result = original(self, p, name, args)
            hits.append("probe")
            if mode == "malformed":
                result["probe"]["next"] = 0
            if mode == "empty":
                result["probe"]["rows"][0]["record"]["rooms"] = [w.hidden.id]
            return result
        if name == TOOL and "allow" in scope:
            hits.append("final")
            if mode == "failure":
                raise TimeoutError("synthetic incomplete acquisition")
            if mode == "changed":
                install(
                    w, monkeypatch, backend, [record(w, "visible", rooms=[w.hidden.id])], "moved"
                )
        return original(self, p, name, args)

    monkeypatch.setattr(WorkerClient, "call_tool", call)
    assert public(w, limit=10, offset=0) == {
        "hallways": [],
        "total": 0,
        "count": 0,
        "offset": 0,
        "limit": 10,
    }
    assert hits and ("final" in hits) is (mode in ("changed", "failure"))


@pytest.mark.parametrize("backend", ["sqlite", "json"])
def test_card253_s03_principal_isolation(world, monkeypatch, backend):
    w = world
    rows = [record(w, "visible"), record(w, "owner-only", rooms=[w.hidden.id])]
    install(w, monkeypatch, backend, rows, "principals")
    first = public(w, limit=10)
    owner = sessions.person_principal(w.h.db, w.h.admin, w.h.workspace)
    assert owner.policy.has_scope("threads:read") and owner.policy.trust == "trusted"
    assert w.h.gateway.handoff.recall.can_read(owner, w.hidden.id)
    other = w.h.gateway.call_memory_tool(owner, TOOL, {"wing": "wing_fixture", "limit": 10})
    assert other["hallways"] == rows and other["total"] == 2
    assert public(w, limit=10) == first
    assert first["hallways"] == rows[:1] and first["total"] == 1


def test_card253_s03_deadline_and_missing_context(hub, monkeypatch):
    """Explicit deadline failures do not install functional transport allowances."""
    from commonplace.handoff.hallway_reads import project
    from commonplace.rules import checks

    owner = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    empty = {"hallways": [], "total": 0, "count": 0, "offset": 3, "limit": 2}
    missing = SimpleNamespace(workspace_id=None)
    assert (
        project(hub.gateway, missing, hub.gateway.mediator.workers, {"offset": 3, "limit": 2})
        == empty
    )
    hits = []

    def expired(timeout):
        hits.append(timeout)
        raise TimeoutError("synthetic deadline")

    monkeypatch.setattr(checks, "remaining", expired)
    assert (
        project(hub.gateway, owner, hub.gateway.mediator.workers, {"offset": 3, "limit": 2})
        == empty
    )
    assert hits
    hub.gateway.handoff.close()
    hub.gateway.webhooks.stop()


@pytest.mark.parametrize("backend", ["sqlite", "json"])
def test_card253_s03_worker_revalidation(world, monkeypatch, backend):
    from mempalace import mcp_server

    w = world
    install(w, monkeypatch, backend, [record(w, "visible", entity_a="kept entity")], "original")
    assert public(w, limit=10)["count"] == 1
    probe = mcp_server.tool_list_hallways(wing="wing_fixture", thread_scope={"probe": True})
    rows = probe["probe"]["rows"]
    assert probe["probe"]["next"] is None
    assert set(rows[0]["record"]) == {"id", "wing", "rooms", "label"}
    install(w, monkeypatch, backend, [record(w, "visible", rooms=[w.hidden.id])], "changed")
    assert mcp_server.tool_list_hallways(wing="wing_fixture", thread_scope={"allow": rows}) == {
        "hallways": []
    }
    assert mcp_server.tool_list_hallways(wing="wing_fixture", thread_scope={"allow": []}) == {
        "hallways": []
    }
