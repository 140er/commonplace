"""``mcp:`` source IDs in fact sources, tunnel labels and drawer provenance.

The room rule of the thread-reference gate (whole value, prefix-matched, every
matching thread readable) also covers every other place a thread can be named.
"""

import json
import re

import pytest

from commonplace.handoff import mirror_access
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_thread_reference_rooms import (  # noqa: F401
    SCOPES,
    agent,
    mcp,
    media_route,
    palace_files,
    store_thread,
)

# value built from a stored source "mcp:probe-<hex>"
VALUES = {
    "exact": lambda s: s,
    "upper_namespace": lambda s: "MCP:" + s[4:],
    "fullwidth_namespace": lambda s: "ｍｃｐ:" + s[4:],
    "suffix": lambda s: s + "_notes",
}
LABELS = {
    "exact": lambda s: s,
    "embedded": lambda s: f"see {s} notes",
    "upper_namespace": lambda s: f"see MCP:{s[4:]} notes",
}


@pytest.fixture
def blind(hub):
    """Sees every wing and fact but cannot read any thread."""
    return agent(hub, "source-blind", [*SCOPES, "kg:read"])


@pytest.fixture
def reader(hub):
    """Sees every wing and fact and reads its own threads."""
    return agent(hub, "source-reader", [*SCOPES, "kg:read", "threads:read"])


def probe(thread):
    return re.search(r"probe-[0-9a-f]+", thread.source_thread_id)[0]


def source_thread(hub, actor, wing):
    return store_thread(hub, "mcp_source", actor=actor, wing=wing)


def hidden(hub, blind):
    thread = source_thread(hub, hub.admin, "private_chats")
    assert not hub.gateway.handoff.recall.can_read(blind[2], thread.id)
    return thread


def readable(hub, reader):
    thread = source_thread(hub, reader[0], "shared")
    assert hub.gateway.handoff.recall.can_read(reader[2], thread.id)
    return thread


def fact(kg, value):
    kg.add_triple("SyntheticProject", "deploy_port", "8080", wing="shared", source_thread=value)


FACT_READS = {
    "kg_query": ("mempalace_kg_query", {"entity": "SyntheticProject"}),
    "kg_timeline": ("mempalace_kg_timeline", {"entity": "SyntheticProject"}),
}


@pytest.mark.parametrize("route", sorted(FACT_READS))
@pytest.mark.parametrize("form", sorted(VALUES))
def test_blind_reader_never_sees_mcp_source_in_fact_sources(hub, kg, blind, form, route):
    thread = hidden(hub, blind)
    fact(kg, VALUES[form](thread.source_thread_id))
    name, args = FACT_READS[route]
    raw = mcp(hub, blind[1], name, **args)
    assert "8080" in raw, raw  # the fact stays, its source does not
    assert probe(thread) not in raw, raw


@pytest.mark.parametrize("route", sorted(FACT_READS))
@pytest.mark.parametrize("form", sorted(VALUES))
def test_thread_reader_keeps_mcp_source_in_fact_sources(hub, kg, reader, form, route):
    thread = readable(hub, reader)
    fact(kg, VALUES[form](thread.source_thread_id))
    name, args = FACT_READS[route]
    raw = mcp(hub, reader[1], name, **args)
    assert probe(thread) in raw, raw


def test_fact_source_naming_no_stored_thread_is_unchanged(hub, kg, blind):
    fact(kg, "mcp:unfiled-source")
    raw = mcp(hub, blind[1], "mempalace_kg_query", entity="SyntheticProject")
    assert "mcp:unfiled-source" in raw, raw


def label_tunnel(config, label):
    from mempalace import palace_graph

    palace_graph._save_tunnels(
        [
            {
                "id": "probe-tunnel",
                "source": {"wing": "shared", "room": "plan"},
                "target": {"wing": "other", "room": "general"},
                "label": label,
            }
        ],
        config,
    )


@pytest.mark.parametrize("form", sorted(LABELS))
def test_blind_reader_never_sees_mcp_source_in_tunnel_label(hub, collection, config, blind, form):
    thread = hidden(hub, blind)
    label_tunnel(config, LABELS[form](thread.source_thread_id))
    raw = mcp(hub, blind[1], "mempalace_list_tunnels")
    assert probe(thread) not in raw, raw


@pytest.mark.parametrize("form", sorted(LABELS))
def test_thread_reader_keeps_mcp_source_in_tunnel_label(hub, collection, config, reader, form):
    thread = readable(hub, reader)
    label_tunnel(config, LABELS[form](thread.source_thread_id))
    raw = mcp(hub, reader[1], "mempalace_list_tunnels")
    assert probe(thread) in raw, raw


def test_tunnel_label_naming_no_stored_thread_is_unchanged(hub, collection, config, blind):
    label_tunnel(config, "see mcp:unfiled-source notes")
    raw = mcp(hub, blind[1], "mempalace_list_tunnels")
    assert "mcp:unfiled-source" in raw, raw


def test_tunnel_label_with_emoji_joiner_and_percent_is_unchanged(hub, collection, config, blind):
    label = "\U0001f469\u200d\U0001f4bb 50% ready"
    label_tunnel(config, label)
    raw = mcp(hub, blind[1], "mempalace_list_tunnels")
    assert "50% ready" in raw, raw


def test_blind_reader_never_sees_joiner_split_mcp_source_in_tunnel_label(
    hub, collection, config, blind
):
    thread = hidden(hub, blind)
    label_tunnel(config, "see m\u200dcp:" + thread.source_thread_id)
    raw = mcp(hub, blind[1], "mempalace_list_tunnels")
    assert probe(thread) not in raw, raw


def drawer(collection, value):
    collection.add(
        ids=["probe-provenance"],
        documents=["Shared words about the plan"],
        metadatas=[
            {
                "wing": "shared",
                "room": "general",
                "source_thread": value,
                "source_seq_start": 0,
                "source_seq_end": 1,
            }
        ],
    )


DRAWER_READS = {
    "get_drawer": ("mempalace_get_drawer", {"drawer_id": "probe-provenance"}),
    "get_drawers": ("mempalace_get_drawers", {"drawer_ids": ["probe-provenance"]}),
}


@pytest.mark.parametrize("route", sorted(DRAWER_READS))
@pytest.mark.parametrize("form", sorted(VALUES))
def test_blind_reader_never_sees_mcp_source_in_drawer_provenance(
    hub, collection, blind, form, route
):
    thread = hidden(hub, blind)
    drawer(collection, VALUES[form](thread.source_thread_id))
    name, args = DRAWER_READS[route]
    raw = mcp(hub, blind[1], name, **args)
    assert "Shared words about the plan" in raw, raw
    assert probe(thread) not in raw, raw


@pytest.mark.parametrize("route", sorted(DRAWER_READS))
@pytest.mark.parametrize("form", sorted(VALUES))
def test_thread_reader_keeps_mcp_source_in_drawer_provenance(hub, collection, reader, form, route):
    thread = readable(hub, reader)
    drawer(collection, VALUES[form](thread.source_thread_id))
    name, args = DRAWER_READS[route]
    raw = mcp(hub, reader[1], name, **args)
    assert probe(thread) in raw, raw


@pytest.mark.parametrize("form", sorted(VALUES))
def test_replayed_write_hides_kept_mcp_source_from_blind_reader(hub, blind, form):
    thread = hidden(hub, blind)
    reply = {
        "success": True,
        "reason": "already_exists",
        "drawer_id": "d1",
        "wing": "shared",
        "room": "general",
        "source_kept": {
            "thread": VALUES[form](thread.source_thread_id),
            "seq_start": 0,
            "seq_end": 1,
        },
    }
    gated = mirror_access.gate(hub.gateway, blind[2], "add_drawer", reply)
    assert probe(thread) not in json.dumps(gated)
    assert "source_kept" not in gated


@pytest.mark.parametrize("form", sorted(VALUES))
def test_replayed_write_keeps_kept_mcp_source_for_thread_reader(hub, reader, form):
    thread = readable(hub, reader)
    reply = {
        "success": True,
        "reason": "already_exists",
        "drawer_id": "d1",
        "wing": "shared",
        "room": "general",
        "source_kept": {
            "thread": VALUES[form](thread.source_thread_id),
            "seq_start": 0,
            "seq_end": 1,
        },
    }
    gated = mirror_access.gate(hub.gateway, reader[2], "add_drawer", reply)
    assert gated["source_kept"]["thread"] == VALUES[form](thread.source_thread_id)
