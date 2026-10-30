"""Canary leak suite: a scoped token never sees or changes what it may not.

Canary drawers, diary entries, a KG fact, a tunnel, an event and an artifact
are filed in wings a scoped token can't read. Then every tool MemPalace
classifies is called through the gateway with arguments aimed at those
canaries. No canary marker may appear in any response, and the canaries must
be unchanged afterwards. New tools join the suite automatically from
``TOOL_CAPABILITIES``.
"""

import json

import pytest

from mempalace.tool_capabilities import TOOL_CAPABILITIES
from tests._commonplace_helpers import hub_fixture  # noqa: F401

FORBIDDEN_WINGS = ("private", "vault_x", "private_notes")

# Markers stored only in forbidden places, never sent by a caller.
MARKERS = {
    "private": [
        "CANARYPRIVATEDRAWER",
        "CANARYPRIVATEDIARY",
        "CANARYPRIVATENOTES",
        "CANARYPRIVATENOTESDIARY",
    ],
    "vault_x": ["CANARYVAULTDRAWER", "CANARYVAULTDIARY", "CANARYAGENTWING", "CANARYTUNNEL"],
    "kg": ["CANARYKGFACT"],
    "events": ["CANARYEVENT", "CANARYARTIFACT"],
}


@pytest.fixture
def seeded(hub):
    # The canary calls every tool many times; it tests leaks, not rate limits.
    hub.gateway.rate_limits = False
    owner = hub.owner_token()
    ids = {}

    def add(wing, content):
        result, _ = hub.call(owner, "mempalace_add_drawer", wing=wing, room="r", content=content)
        assert result["success"], result
        return result["drawer_id"]

    ids["allowed"] = add("shared", "zebra quartz harbour, the allowed memory")
    ids["private"] = add("private", "zebra quartz harbour CANARYPRIVATEDRAWER")
    ids["vault"] = add("vault_x", "zebra quartz harbour CANARYVAULTDRAWER")
    ids["project"] = add("project_alpha", "zebra quartz project note")
    # Stored under the slug the worker folds "private.notes" and "Private-Notes" into.
    ids["private_notes"] = add("private_notes", "zebra quartz harbour CANARYPRIVATENOTES")
    for agent, wing, marker in (
        ("spy", "private", "CANARYPRIVATEDIARY"),
        ("spy", "vault_x", "CANARYVAULTDIARY"),
        ("spy2", "", "CANARYAGENTWING"),
        ("spy", "private_notes", "CANARYPRIVATENOTESDIARY"),
    ):
        result, _ = hub.call(
            owner, "mempalace_diary_write", agent_name=agent, entry=f"zebra {marker}", wing=wing
        )
        assert result["success"], result
        ids.setdefault("diary", []).append(result["entry_id"])
    result, _ = hub.call(
        owner,
        "mempalace_create_tunnel",
        source_wing="shared",
        source_room="r",
        target_wing="vault_x",
        target_room="r",
        label="CANARYTUNNEL",
    )
    ids["tunnel"] = (result or {}).get("tunnel_id") or (result or {}).get("id") or "tunnel"
    result, _ = hub.call(
        owner, "mempalace_kg_add", subject="avery", predicate="likes", object="CANARYKGFACT"
    )
    assert result["success"], result
    result, _ = hub.call(
        owner,
        "mempalace_event_append",
        type="note",
        stream="s",
        room="r",
        from_agent="owner",
        body="CANARYEVENT",
    )
    ids["event"] = ((result or {}).get("event") or {}).get("event_id") or "event"
    result, _ = hub.call(
        owner, "mempalace_artifact_put", kind="note", content="CANARYARTIFACT", created_by="owner"
    )
    ids["artifact"] = ((result or {}).get("artifact") or {}).get("artifact_id") or "artifact"
    return owner, ids


def _schema(hub, token):
    return {t["name"]: t for t in hub.rpc(token, "tools/list")["result"]["tools"]}


def _value(prop, schema, ids, wing, agent):
    if prop in ("wing", "source_wing", "target_wing", "wing_a", "wing_b", "project_dir"):
        return wing
    if prop in ("drawer_id", "source_drawer_id", "target_drawer_id"):
        return ids["vault"] if wing == "vault_x" else ids["private"]
    if prop == "drawer_ids":
        return [ids["vault"], ids["private"], *ids["diary"]]
    if prop in ("room", "source_room", "target_room", "start_room"):
        return "r"
    if prop in ("query", "content", "entry"):
        return "zebra quartz harbour"
    if prop == "agent_name":
        return agent
    if prop in ("entity", "subject"):
        return "avery"
    if prop in ("object", "old_object", "new_object", "predicate"):
        return "likes"
    if prop == "tunnel_id":
        return ids["tunnel"]
    if prop == "event_id":
        return ids["event"]
    if prop == "artifact_id":
        return ids["artifact"]
    if prop == "source_file":
        return "x"
    if prop == "items":
        return [{"wing": wing, "room": "r", "content": "zebra probe"}]
    if prop == "diary":
        return {"agent_name": agent, "entry": "probe", "wing": wing}
    if prop == "timeout_ms":
        return 10
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = kind[0]
    return {"integer": 5, "number": 0.9, "boolean": False, "array": [], "object": {}}.get(
        kind, "probe"
    )


def _variants(name, all_schemas, ids):
    entry = all_schemas.get(name) or {}
    schema = entry.get("inputSchema") or entry.get("input_schema") or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    out = []
    for wing, agent in (
        ("vault_x", "spy"),
        ("private", "spy"),
        ("", "spy2"),
        ("shared", "spy2"),
        # Spellings the worker folds into a forbidden wing.
        ("private.notes", "spy"),
        ("Private-Notes", "spy"),
        ("wing_vault-x", "spy"),
        ("VAULT X", "spy"),
        ("linux_workstation", "spy"),
    ):
        full = {p: _value(p, s, ids, wing, agent) for p, s in props.items()}
        full = {k: v for k, v in full.items() if v != ""}
        out.append(full)
        out.append({p: full[p] for p in required if p in full})
        # Shapes the worker coerces: ids as a JSON string, mixed with numbers.
        for key in ("drawer_ids", "drawer_id"):
            if key in full:
                forbidden = [ids["vault"], ids["private"], ids["private_notes"]]
                out.append({**full, key: json.dumps(forbidden) if key == "drawer_ids" else 7})
                if key == "drawer_ids":
                    out.append({**full, key: [*forbidden, 1]})
    return out


def _assert_clean(text, forbidden_markers, label):
    for marker in forbidden_markers:
        assert marker not in text, f"{label} leaked {marker}: {text[:600]}"


SCOPED_TOKENS = {
    # Holds kg:read and kg:write but reads only some wings: the graph stays shut.
    "scoped_with_kg": dict(
        profile="full",
        scopes=["memory:read", "memory:write", "kg:read", "kg:write"],
        read_wings=["shared", "project_*"],
        write_wings=["shared"],
        trust="trusted",
    ),
    # A cloud chat bot: slim, untrusted, inbox writes, no KG.
    "untrusted_slim": dict(
        profile="slim",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared", "inbox"],
        write_wing="inbox",
        trust="untrusted",
    ),
    # A trusted coding agent on some wings, with delete and events.
    "trusted_full_scoped": dict(
        profile="full",
        scopes=["memory:read", "memory:write", "memory:delete", "events:read", "events:write"],
        read_wings=["shared", "project_*"],
        write_wings=["shared", "project_*"],
        trust="trusted",
    ),
    # Every wing except private ones.
    "standard_all_but_private": dict(
        profile="full",
        scopes=["memory:read", "memory:write", "kg:write"],
        read_wings=["*"],
        write_wings=["shared"],
        trust="standard",
    ),
}


def _forbidden_markers(kind):
    if kind == "standard_all_but_private":
        return MARKERS["private"] + MARKERS["kg"] + MARKERS["events"]
    return [m for group in MARKERS.values() for m in group]


@pytest.mark.parametrize("kind", sorted(SCOPED_TOKENS))
def test_scoped_token_sees_no_canary_through_any_tool(hub, seeded, kind):
    owner, ids = seeded
    token = hub.token(kind, **SCOPED_TOKENS[kind])
    owner_schemas = _schema(hub, owner)
    visible = set(_schema(hub, token))
    forbidden = _forbidden_markers(kind)
    called = 0
    for name in sorted(TOOL_CAPABILITIES):
        for args in _variants(name, owner_schemas, ids):
            _result, text = hub.call(token, name, **args)
            _assert_clean(text, forbidden, f"{kind}/{name}({json.dumps(args)[:200]})")
            if name not in visible:
                assert f"Unknown tool: {name}" in text, f"hidden {name} answered: {text[:300]}"
            called += 1
    assert called >= len(TOOL_CAPABILITIES)

    # tools/list itself: no tool the token can't call, nothing forbidden.
    listing = json.dumps(hub.rpc(token, "tools/list"))
    _assert_clean(listing, forbidden, f"{kind}/tools/list")


@pytest.mark.parametrize("kind", sorted(SCOPED_TOKENS))
def test_scoped_token_changes_no_canary(hub, seeded, kind):
    owner, ids = seeded
    token = hub.token(kind, **SCOPED_TOKENS[kind])
    owner_schemas = _schema(hub, owner)
    before, _ = hub.call(owner, "mempalace_list_wings")
    for name, (capability, _flags) in sorted(TOOL_CAPABILITIES.items()):
        if capability == "read":
            continue
        for args in _variants(name, owner_schemas, ids):
            hub.call(token, name, **args)

    for key in ("private", "vault", "private_notes"):
        drawer, _ = hub.call(owner, "mempalace_get_drawer", drawer_id=ids[key])
        assert "CANARY" in drawer["content"], drawer
    after, _ = hub.call(owner, "mempalace_list_wings")
    for wing in FORBIDDEN_WINGS + ("wing_spy2", "private_notes", "linux_workstation"):
        assert after["wings"].get(wing) == before["wings"].get(wing), (wing, before, after)
    facts, _ = hub.call(owner, "mempalace_kg_query", entity="avery")
    # Adding facts may be allowed; changing the canary fact never is.
    canary = [f for f in facts["facts"] if f["object"] == "CANARYKGFACT"]
    assert len(canary) == 1 and canary[0]["current"], facts


def test_hidden_and_unknown_tools_answer_the_same(hub, seeded):
    token = hub.token("slim", **SCOPED_TOKENS["untrusted_slim"])
    hidden = hub.rpc(token, "tools/call", {"name": "mempalace_delete_drawer", "arguments": {}})
    unknown = hub.rpc(token, "tools/call", {"name": "mempalace_nope", "arguments": {}})
    assert hidden["error"]["code"] == unknown["error"]["code"] == -32601
    assert hidden["error"]["message"] == "Unknown tool: mempalace_delete_drawer"


def test_hidden_drawer_reads_exactly_like_a_missing_one(hub, seeded):
    _owner, ids = seeded
    token = hub.token("scoped", **SCOPED_TOKENS["trusted_full_scoped"])
    hidden, _ = hub.call(token, "mempalace_get_drawer", drawer_id=ids["vault"])
    missing, _ = hub.call(token, "mempalace_get_drawer", drawer_id="drawer_none_r_000000000000")
    assert hidden == {"error": f"Drawer not found: {ids['vault']}"}
    assert set(hidden) == set(missing)
    hidden, _ = hub.call(token, "mempalace_delete_drawer", drawer_id=ids["vault"])
    missing, _ = hub.call(token, "mempalace_delete_drawer", drawer_id="drawer_none_r_000000000000")
    assert hidden == {"success": False, "error": f"Drawer not found: {ids['vault']}"}
    assert set(hidden) == set(missing)
