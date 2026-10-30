"""fact_save through the hub: wing-scoped, secret-free, merged and replaced."""

import json

from commonplace import events
from tests._commonplace_helpers import hub_fixture  # noqa: F401

SCOPED = dict(
    profile="full",
    scopes=["memory:read", "memory:write", "kg:read", "kg:write"],
    read_wings=["shared"],
    write_wings=["shared"],
    write_wing="shared",
    trust="standard",
)


def _save(hub, token, **args):
    response = hub.rpc(token, "tools/call", {"name": "fact_save", "arguments": args})
    assert "error" not in response, response
    result = response["result"]
    return result["content"][0]["text"], result.get("isError", False)


def _facts(hub, entity):
    token = hub.owner_token(name=f"reader-{hub._ids}")
    return hub.call(token, "mempalace_kg_query", entity=entity)[0]["facts"]


def test_fact_save_merges_and_replaces(hub):
    token = hub.token("scoped", **SCOPED)
    text, error = _save(
        hub,
        token,
        subject="Jeffrey",
        predicate="works_at",
        object="Acme",
        source={"thread": "t1", "seq_start": 3},
        valid_from="2024-01-01",
    )
    assert not error and text.startswith("saved ")
    first = text.split()[1]
    text, _ = _save(
        hub,
        token,
        subject="Jeffrey",
        predicate="works_at",
        object="Acme",
        source={"thread": "t2", "seq_start": 8},
    )
    assert text == f"merged into {first}"
    text, _ = _save(
        hub,
        token,
        subject="Jeffrey",
        predicate="works_at",
        object="Initech",
        valid_from="2026-01-01",
    )
    assert text.endswith('replacing "Acme"')
    facts = {f["object"]: f for f in _facts(hub, "Jeffrey")}
    assert facts["Acme"]["status"] == "superseded" and facts["Acme"]["wing"] == "shared"
    assert [s["source_thread"] for s in facts["Acme"]["sources"]] == ["t1", "t2"]
    assert facts["Initech"]["current"]
    kinds = [e["kind"] for e in events.after(hub.db, hub.workspace)]
    assert kinds.count("fact.saved") == 3
    assert "Initech" not in json.dumps(events.after(hub.db, hub.workspace))


def test_fact_save_refuses_secrets_and_stores_nothing(hub):
    owner = hub.owner_token()
    text, error = _save(hub, owner, subject="deploy", predicate="token", object="ghp_" + "a" * 36)
    assert error and "secret" in text
    assert _facts(hub, "deploy") == []


def test_fact_save_stays_in_writable_wings(hub):
    token = hub.token("scoped", **SCOPED)
    text, error = _save(hub, token, subject="a", predicate="b", object="c", theme="vault_x")
    assert error
    assert _facts(hub, "a") == []


def test_fact_save_needs_kg_write(hub):
    token = hub.token(
        "reader",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wing="shared",
        trust="standard",
    )
    assert "fact_save" not in hub.tool_names(token)
