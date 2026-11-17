"""Composer discovery stays responsive when the palace cannot answer quickly."""

import json
import time

from tests._commonplace_helpers import hub_fixture  # noqa: F401


def test_composer_open_never_waits_for_slow_taxonomy(hub, monkeypatch):
    token = hub.owner_token()
    calls = []

    def slow(principal, name, args):
        calls.append(name)
        time.sleep(30)
        return {"taxonomy": {"shared": {"late-room": 1}}}

    monkeypatch.setattr(hub.gateway, "call_memory_tool", slow)
    started = time.monotonic()
    status, raw = hub.raw(token, b"", method="GET", path="/api/memory/rooms?compose=1", timeout=40)
    elapsed = time.monotonic() - started
    print(f"composer with 30s taxonomy stub: {elapsed:.4f}s", flush=True)
    assert elapsed < 1, f"composer waited {elapsed:.4f}s for optional discovery"
    assert status == 200
    composer = json.loads(raw)["composer"]
    assert composer["default_wing"] == "shared"
    assert composer["wings"] == [
        {"wing": "shared", "name": "Workspace notes", "rooms": ["general"]}
    ]
    assert calls == []


def test_lazy_rooms_cache_hit_expiry_and_wing_authorization(hub, monkeypatch):
    from commonplace import memory_places

    token = hub.owner_token()
    calls = []
    clock = [100.0]
    monkeypatch.setattr(memory_places, "monotonic", lambda: clock[0])

    def rooms(principal, name, args):
        assert name == "mempalace_list_rooms" and args == {"wing": "shared"}
        calls.append(principal.workspace_id)
        return {"rooms": {"decisions": 2}}

    monkeypatch.setattr(hub.gateway, "call_memory_tool", rooms)
    for expected in [1, 1, 2]:
        status, raw = hub.raw(
            token, b"", method="GET", path="/api/memory/rooms?compose=1&wing=shared"
        )
        assert status == 200
        assert json.loads(raw)["composer"]["wings"][0]["rooms"] == ["general", "decisions"]
        assert len(calls) == expected
        if expected == 1:
            clock[0] += memory_places.ROOM_CACHE_TTL_S / 2 + 1
    status, _ = hub.raw(token, b"", method="GET", path="/api/memory/rooms?compose=1&wing=unknown")
    assert status == 403 and len(calls) == 2


def test_late_room_lookup_is_bounded_and_not_cached(hub, monkeypatch):
    import threading

    token = hub.owner_token()
    release, finished = threading.Event(), threading.Event()
    calls = []

    def slow(principal, name, args):
        calls.append(name)
        try:
            assert release.wait(5), "test must release its optional reader"
            return {"rooms": {"too-late": 1}}
        finally:
            finished.set()

    monkeypatch.setattr(hub.gateway, "call_memory_tool", slow)
    try:
        started = time.monotonic()
        status, raw = hub.raw(
            token, b"", method="GET", path="/api/memory/rooms?compose=1&wing=shared"
        )
        elapsed = time.monotonic() - started
        print(f"lazy rooms with blocked reader: {elapsed:.4f}s", flush=True)
        assert elapsed < 1 and status == 200
        assert json.loads(raw)["composer"]["wings"][0]["rooms"] == ["general"]
        # A still-running reader cannot spawn another one on repeated requests.
        hub.raw(token, b"", method="GET", path="/api/memory/rooms?compose=1&wing=shared")
        assert len(calls) == 1
    finally:
        release.set()
        assert finished.wait(2)
    monkeypatch.setattr(hub.gateway, "call_memory_tool", lambda *args: {"rooms": {"fresh": 1}})
    status, raw = hub.raw(token, b"", method="GET", path="/api/memory/rooms?compose=1&wing=shared")
    assert status == 200
    assert json.loads(raw)["composer"]["wings"][0]["rooms"] == ["general", "fresh"]


def test_room_cache_isolates_workspace_and_connection(hub, monkeypatch):
    from dataclasses import replace

    from commonplace.extensions import ToolContext
    from commonplace.memory_places import room_choices

    token = hub.owner_token()
    principal = hub.gateway.authenticate(f"Bearer {token}")
    calls = []

    def rooms(person, name, args):
        calls.append((person.workspace_id, person.connection_id))
        return {"rooms": {f"room-{len(calls)}": 1}}

    monkeypatch.setattr(hub.gateway, "call_memory_tool", rooms)
    ctx = ToolContext(principal, hub.gateway)
    assert room_choices(ctx, "shared") == ["general", "room-1"]
    assert room_choices(ctx, "shared") == ["general", "room-1"]
    assert room_choices(
        ToolContext(replace(principal, workspace_id="other"), hub.gateway), "shared"
    ) == ["general", "room-2"]
    assert room_choices(
        ToolContext(replace(principal, connection_id="other"), hub.gateway), "shared"
    ) == ["general", "room-3"]


def test_optional_rooms_unavailable_keeps_general(hub, monkeypatch):
    token = hub.owner_token()

    def unavailable(*args):
        raise TimeoutError("unavailable")

    monkeypatch.setattr(hub.gateway, "call_memory_tool", unavailable)
    status, raw = hub.raw(token, b"", method="GET", path="/api/memory/rooms?compose=1&wing=shared")
    assert status == 200
    assert json.loads(raw)["composer"]["wings"][0]["rooms"] == ["general"]


def test_default_wing_and_lazy_room_refusal_preserve_connection_scope(hub, monkeypatch):
    token = hub.token(
        "note-inbox",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["inbox"],
        write_wings=["inbox"],
        write_wing="inbox",
        trust="standard",
    )
    calls = []
    monkeypatch.setattr(hub.gateway, "call_memory_tool", lambda *args: calls.append(args))
    status, raw = hub.raw(token, b"", method="GET", path="/api/memory/rooms?compose=1")
    composer = json.loads(raw)["composer"]
    assert status == 200 and composer["default_wing"] == "inbox"
    assert [row["wing"] for row in composer["wings"]] == ["inbox"]
    for wing in ["shared", "private", "project_other"]:
        status, _ = hub.raw(
            token, b"", method="GET", path=f"/api/memory/rooms?compose=1&wing={wing}"
        )
        assert status == 403
    assert calls == []


def test_map_wing_scan_has_a_short_deadline(hub, monkeypatch):
    from mempalace.deadline import remaining

    token = hub.owner_token()
    observed = []

    def bounded(principal, name, args):
        observed.append(remaining(660))
        return {"wings": {}}

    monkeypatch.setattr(hub.gateway, "call_memory_tool", bounded)
    status, _ = hub.raw(token, b"", method="GET", path="/api/memory/map")
    assert status == 200
    assert observed and all(0 < seconds <= 1 for seconds in observed)
