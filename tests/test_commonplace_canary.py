"""Canary leak suite: a scoped token never sees or changes what it may not.

Canary drawers, diary entries, a KG fact, a tunnel, an event and an artifact
are filed in wings a scoped token can't read. Then every tool MemPalace
classifies is called through the gateway with arguments aimed at those
canaries. No canary marker may appear in any response, and the canaries must
be unchanged afterwards. New tools join the suite automatically from
``TOOL_CAPABILITIES``.

The Grok profile also probes Commonplace's memory aliases, board links and
decision queues, HTTP events, and the data reads behind short links.
"""

import http.client
import json
from urllib.parse import urlsplit

import pytest

from commonplace import sessions, urls
from commonplace.boards.service import BoardService
from commonplace.extensions import ToolContext
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
        owner, "mempalace_kg_add", subject="jeff", predicate="likes", object="CANARYKGFACT"
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
        return "jeff"
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
    facts, _ = hub.call(owner, "mempalace_kg_query", entity="jeff")
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


@pytest.fixture
def grok(hub):
    return hub.token("grok", client="grok", **SCOPED_TOKENS["untrusted_slim"])


def _http_get(hub, token, path):
    conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=10)
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode("utf-8")
    finally:
        conn.close()


def _local_read(hub, token, name, **arguments):
    # Local tools return plain text, unlike MemPalace's JSON tool results.
    response = hub.rpc(token, "tools/call", {"name": name, "arguments": arguments})
    assert "error" not in response, response
    assert not response["result"].get("isError"), response
    return response["result"]["content"][0]["text"]


@pytest.mark.parametrize("surface", ["mcp", "rest"])
def test_grok_profile_sees_no_canary_through_memory_aliases(hub, seeded, grok, surface):
    owner, ids = seeded
    forbidden = _forbidden_markers("untrusted_slim")
    for key in ("allowed", "private", "private_notes", "vault"):
        if surface == "mcp":
            owner_text = _local_read(hub, owner, "memory_get", id=ids[key])
            response = hub.rpc(
                grok, "tools/call", {"name": "memory_get", "arguments": {"id": ids[key]}}
            )
            assert "error" not in response, response
            assert bool(response["result"].get("isError")) is (key != "allowed"), response
            text = json.dumps(response)
        else:
            status, _, owner_text = _http_get(hub, owner, f"/api/memory/{ids[key]}")
            assert status == 200, owner_text
            status, _, text = _http_get(hub, grok, f"/api/memory/{ids[key]}")
            assert status == (200 if key == "allowed" else 404), text
        assert ("CANARY" in owner_text) is (key != "allowed"), owner_text
        if key == "allowed":
            assert "the allowed memory" in text
        _assert_clean(text, forbidden, f"{surface}/memory_get/{key}")

    for theme in (None, "private", "private_notes", "vault_x", "private.notes", "Private-Notes"):
        args = {"query": "zebra quartz harbour", **({"theme": theme} if theme else {})}
        if surface == "mcp":
            response = hub.rpc(grok, "tools/call", {"name": "memory_search", "arguments": args})
            assert "error" not in response, response
            assert bool(response["result"].get("isError")) is (theme is not None), response
            text = json.dumps(response)
        else:
            path = "/api/memory/search?q=zebra%20quartz%20harbour"
            if theme:
                path += f"&theme={theme}"
            status, _, text = _http_get(hub, grok, path)
            assert status == (403 if theme else 200), text
        if theme is None:
            assert ids["allowed"] in text and "the allowed memory" in text
        _assert_clean(text, forbidden, f"{surface}/memory_search/{theme}")


def test_grok_profile_sees_no_canary_through_memory_map(hub, seeded, grok):
    owner, _ids = seeded
    status, _, text = _http_get(hub, owner, "/api/memory/map")
    assert status == 200 and "CANARYPRIVATEDRAWER" in text, text
    # A slim token cannot call the list tools needed to build the map.
    status, _, text = _http_get(hub, grok, "/api/memory/map")
    assert status == 403, text
    _assert_clean(text, _forbidden_markers("untrusted_slim"), "memory/map")


@pytest.fixture
def board_canaries(hub, seeded):
    owner, ids = seeded
    principal = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    service = BoardService(ToolContext(principal, hub.gateway))
    service.board_add(slug="visible", wing="shared")
    visible = service.seed_card(board="visible", title="Allowed card", status="review")
    hidden = []
    for wing, key in (
        ("private", "private"),
        ("private_notes", "private_notes"),
        ("vault_x", "vault"),
    ):
        service.board_add(slug=wing, wing=wing)
        drawer, _ = hub.call(owner, "mempalace_get_drawer", drawer_id=ids[key])
        card = service.seed_card(
            board=wing, title=drawer["content"], body=drawer["content"], status="review"
        )
        service.task_comment(card["number"], drawer["content"])
        # A shared card caches the private drawer's first line as its link title.
        service.task_update(visible["number"], link=ids[key])
        # Also exercise backlinks from an allowed memory to a hidden card.
        service.task_update(card["number"], link=ids["allowed"])
        hidden.append(card)
    return service, visible, hidden


@pytest.mark.parametrize("surface", ["mcp", "rest"])
def test_grok_profile_sees_no_canary_through_boards_or_needs_me(
    hub, seeded, grok, board_canaries, surface
):
    owner, ids = seeded
    _service, visible, hidden = board_canaries
    forbidden = _forbidden_markers("untrusted_slim")
    owner_text = _local_read(hub, owner, "task_show", task=visible["number"])
    assert "CANARYPRIVATEDRAWER" in owner_text, owner_text
    if surface == "mcp":
        operations = [
            ("board_list", {}),
            ("task_list", {}),
            ("task_list", {"board": "visible"}),
            ("task_show", {"task": visible["number"]}),
            ("memory_get", {"id": ids["allowed"]}),
            ("needs_me", {}),
            ("needs_me", {"format": "digest"}),
        ]
        for name, args in operations:
            text = _local_read(hub, grok, name, **args)
            _assert_clean(text, forbidden, f"{name}/{args}")
            if name == "task_show":
                assert "Allowed card" in text and ids["private"] not in text
        for card in hidden:
            response = hub.rpc(
                grok, "tools/call", {"name": "task_show", "arguments": {"task": card["number"]}}
            )
            assert response["result"]["isError"], response
            _assert_clean(json.dumps(response), forbidden, "hidden task_show")
    else:
        for path in (
            "/api/boards",
            "/api/cards",
            "/api/boards/visible/cards",
            f"/api/cards/{visible['number']}",
            f"/api/memory/{ids['allowed']}",
            "/api/needs-me",
            "/api/needs-me/digest",
            "/api/needs-me/digest?format=text",
            "/api/needs-me/digest?format=ssml",
        ):
            status, _, text = _http_get(hub, grok, path)
            assert status == 200, (path, text)
            _assert_clean(text, forbidden, path)
            if path == f"/api/cards/{visible['number']}":
                assert "Allowed card" in text and ids["private"] not in text
        for card in hidden:
            for path in (f"/api/cards/{card['number']}", f"/api/boards/{card['board']}/cards"):
                status, _, text = _http_get(hub, grok, path)
                assert status == 404, (path, text)
                _assert_clean(text, forbidden, path)


def test_grok_profile_cannot_read_workspace_events(hub, seeded, grok, board_canaries):
    owner, _ids = seeded
    # Board events include private card titles, so the feed itself must be denied.
    status, _, text = _http_get(hub, owner, "/api/events?after=0")
    assert status == 200 and "CANARYPRIVATEDRAWER" in text, text
    status, _, text = _http_get(hub, grok, "/api/events?after=0")
    assert status == 403, text
    _assert_clean(text, _forbidden_markers("untrusted_slim"), "api/events")


def test_grok_profile_short_links_do_not_grant_private_access(hub, seeded, grok, board_canaries):
    owner, ids = seeded
    _service, _visible, hidden = board_canaries
    targets = [
        (f"/app/#/memory?id={ids['private']}", f"/api/memory/{ids['private']}"),
        (f"/app/#/card/{hidden[0]['number']}", f"/api/cards/{hidden[0]['number']}"),
    ]
    for app_path, api_path in targets:
        link = urls.share_link(hub.db, hub.workspace, app_path)
        for token in (None, grok):
            status, headers, text = _http_get(hub, token, urlsplit(link).path)
            assert status == 302 and headers["Location"] == urls.app_link(app_path)
            _assert_clean(json.dumps([headers, text]), _forbidden_markers("untrusted_slim"), link)
            # The redirect only opens the app shell. Its data read still uses
            # the caller's credentials, whether signed out or holding Grok's token.
            status, _, text = _http_get(hub, token, urlsplit(headers["Location"]).path)
            assert status == 200, text
            _assert_clean(text, _forbidden_markers("untrusted_slim"), app_path)
            status, _, text = _http_get(hub, token, api_path)
            assert status == (401 if token is None else 404), text
            _assert_clean(text, _forbidden_markers("untrusted_slim"), api_path)
        status, _, text = _http_get(hub, owner, api_path)
        assert status == 200 and "CANARYPRIVATEDRAWER" in text, text
