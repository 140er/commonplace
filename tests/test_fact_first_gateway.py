"""Fact-first recall uses the real gateway's KG scope, even with drawer fan-out."""

import pytest

from tests._commonplace_helpers import hub_fixture  # noqa: F401


def plant(hub):
    owner = hub.owner_token()
    for wing, value, sensitivity in (
        ("shared", "public-value", "public"),
        ("shared", "private-value", "private"),
        ("project_only", "project-value", "internal"),
        ("vault", "hidden-value", "internal"),
    ):
        result, text = hub.call(
            owner,
            "mempalace_kg_save",
            subject="Kestrel",
            predicate="deploy_port",
            object=value,
            wing=wing,
            sensitivity=sensitivity,
            source_thread="mcp:source",
            source_seq_start=0,
            source_seq_end=1,
            single_valued=False,
        )
        assert result and result.get("success"), text
    result, text = hub.call(
        owner,
        "mempalace_add_drawer",
        wing="shared",
        room="r",
        content="Kestrel drawer stays verbatim",
    )
    assert result and result.get("success"), text
    return owner


@pytest.mark.parametrize("wing", [None, "shared"])
def test_search_matches_scoped_kg_visibility_and_makes_one_fact_lookup(hub, monkeypatch, wing):
    plant(hub)
    token = hub.token(
        "reader",
        profile="full",
        scopes=["memory:read", "kg:read"],
        read_wings=["shared", "project_*"],
        trust="standard",
    )
    kg, text = hub.call(
        token, "mempalace_kg_query", entity="Kestrel", **({"wings": [wing]} if wing else {})
    )
    assert kg, text
    calls = []
    original = hub.gateway.mediator.workers.call_tool

    def recorded(principal, name, args):
        calls.append((name, args))
        return original(principal, name, args)

    monkeypatch.setattr(hub.gateway.mediator.workers, "call_tool", recorded)
    result, raw = hub.call(
        token, "mempalace_search", query="Kestrel", **({"wing": wing} if wing else {})
    )
    assert result, raw
    assert {f["id"] for f in result["facts"]} == {f["id"] for f in kg["facts"]}
    assert result["facts"] and result["results"]
    assert not any(value in raw for value in ("private-value", "hidden-value"))
    assert (
        sum(name == "mempalace_kg_query" and args.get("search", False) for name, args in calls) == 1
    )
    # Wing enumeration belongs to the shared KG policy path, owned by #40.
    assert sum(name in ("mempalace_kg_stats", "mempalace_kg_list_wings") for name, _ in calls) == 1


@pytest.mark.parametrize(
    "scopes,tools", [(["memory:read"], "full"), (["memory:read", "kg:read"], ["mempalace_search"])]
)
def test_search_without_kg_permission_never_returns_facts(hub, scopes, tools):
    plant(hub)
    token = hub.token(
        "drawer-only",
        profile="full",
        tools=None if tools == "full" else tools,
        scopes=scopes,
        read_wings=["*"],
    )
    result, raw = hub.call(token, "mempalace_search", query="Kestrel", wing="shared")
    assert result and result["facts"] == [], raw
    assert "public-value" not in raw and "private-value" not in raw


def test_kg_only_wing_is_searchable_without_any_drawer(hub):
    plant(hub)
    token = hub.token(
        "facts-only",
        profile="full",
        scopes=["memory:read", "kg:read"],
        read_wings=["project_only"],
        trust="standard",
    )
    result, raw = hub.call(token, "mempalace_search", query="Kestrel")
    assert result and [f["object"] for f in result["facts"]] == ["project-value"], raw
    assert result["results"] == []


def test_failed_fact_scope_check_does_not_gate_drawers_or_widen_scope(hub, monkeypatch):
    plant(hub)
    token = hub.token(
        "reader", profile="full", scopes=["memory:read", "kg:read"], read_wings=["shared"]
    )
    plain, _ = hub.call(token, "mempalace_search", query="Kestrel", include_facts=False)
    original = hub.gateway.mediator.workers.call_tool

    def broken(principal, name, arguments):
        return (
            {"error": "fact index unavailable"}
            if name in ("mempalace_kg_stats", "mempalace_kg_list_wings")
            else original(principal, name, arguments)
        )

    monkeypatch.setattr(hub.gateway.mediator.workers, "call_tool", broken)
    combined, raw = hub.call(token, "mempalace_search", query="Kestrel")
    assert combined and combined["facts"] == [], raw
    assert combined["results"] == plain["results"]


@pytest.mark.parametrize("wing", [None, "shared"])
def test_unexpected_fact_worker_error_keeps_drawers_and_generic_marker(hub, monkeypatch, wing):
    plant(hub)
    token = hub.token(
        "reader", profile="full", scopes=["memory:read", "kg:read"], read_wings=["shared"]
    )
    args = {"query": "Kestrel", **({"wing": wing} if wing else {})}
    plain, _ = hub.call(token, "mempalace_search", include_facts=False, **args)
    original = hub.gateway.mediator.workers.call_tool

    def broken(principal, name, arguments):
        if name == "mempalace_kg_query" and arguments.get("search"):
            raise RuntimeError("private detail")
        return original(principal, name, arguments)

    monkeypatch.setattr(hub.gateway.mediator.workers, "call_tool", broken)
    combined, raw = hub.call(token, "mempalace_search", **args)
    assert combined["results"] == plain["results"]
    assert combined["facts"] == [] and combined["facts_error"] == "unavailable"
    assert "private detail" not in raw


def test_multiwing_drawer_error_is_not_decorated_with_facts(hub, monkeypatch):
    plant(hub)
    token = hub.token(
        "reader", profile="full", scopes=["memory:read", "kg:read"], read_wings=["shared"]
    )
    original = hub.gateway.mediator.workers.call_tool

    def failed_drawers(principal, name, arguments):
        if name == "mempalace_search":
            return {"error": "Drawer search failed"}
        return original(principal, name, arguments)

    monkeypatch.setattr(hub.gateway.mediator.workers, "call_tool", failed_drawers)
    result, _ = hub.call(token, "mempalace_search", query="Kestrel")
    assert result == {"error": "Drawer search failed"}


def test_captured_cli_output_and_local_hub_are_scoped(hub):
    owner = plant(hub)
    token = hub.token(
        "cli",
        profile="full",
        scopes=["memory:read", "kg:read"],
        read_wings=["shared"],
        trust="standard",
    )
    result, raw = hub.call(
        token, "mempalace_search", query="Kestrel", wing="shared", cli_compatible=True
    )
    assert result and result["facts"], raw
    output = result["cli_output"]
    assert output.index("Facts") < output.index("[1]")
    assert "public-value" in output and "private-value" not in raw and "hidden-value" not in raw
    all_facts, raw = hub.call(owner, "mempalace_search", query="Kestrel")
    assert len(all_facts["facts"]) == 4, raw


def test_search_source_opens_verbatim_range_through_gateway_recall(hub):
    from commonplace.handoff.models import ThreadMessage
    from commonplace.handoff.recall import ThreadNotFound

    token = hub.token(
        "source-reader",
        profile="full",
        scopes=[
            "memory:read",
            "memory:write",
            "kg:read",
            "kg:write",
            "threads:read",
            "private:read",
        ],
        read_wings=["*"],
        write_wings=["shared"],
        trust="trusted",
    )
    principal = hub.db.authenticate_token(token)
    context = hub.gateway.handoff
    text = "For Kestrel the deploy port is 8443.\nKeep these exact words."
    stored = context.store_for(hub.workspace).store(
        hub.workspace,
        "source-exact",
        [
            ThreadMessage(seq=0, role="user", kind="message", text=text, raw=text),
        ],
        actor=principal.actor,
    )
    saved, raw = hub.call(
        token,
        "mempalace_kg_save",
        subject="Kestrel",
        predicate="deploy_port",
        object="8443",
        wing="shared",
        source_thread=stored.id,
        source_seq_start=0,
        source_seq_end=0,
    )
    assert saved and saved["success"], raw
    drawer, raw = hub.call(
        token, "mempalace_add_drawer", wing="shared", room="source", content=text
    )
    assert drawer and drawer["success"], raw
    result, raw = hub.call(token, "mempalace_search", query="Kestrel deploy port")
    assert result and result["facts"], raw
    (source,) = result["facts"][0]["sources"]
    assert source["source_link"] == "/app/threads/" + stored.id
    opened = context.recall.get(
        principal, source["source_thread"], source["source_seq_start"], source["source_seq_end"]
    )
    assert [m.text for m in opened] == [text]
    restricted = hub.token(
        "no-source", profile="full", scopes=["memory:read", "kg:read"], read_wings=["shared"]
    )
    with pytest.raises(ThreadNotFound):
        context.recall.get(hub.db.authenticate_token(restricted), stored.id)
