"""Map-only person privacy uses current workspace ownership."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from commonplace import memory_tools, sessions
from commonplace.extensions import ToolContext
from mempalace.knowledge_graph import KnowledgeGraph
from mempalace.paths import KNOWLEDGE_GRAPH_FILENAME
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_memory import _call, _http, _save, _scoped, board_store  # noqa: F401


def person(hub):
    uid = hub.db.create_user("map-reader@example.test")
    hub.db.add_member(hub.workspace, uid, "member", actor=hub.admin)
    cookie = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, "map-reader@example.test", hub.workspace)
    )
    return uid, {"Cookie": f"{sessions.COOKIE}={cookie}"}


def graph(hub, headers, suffix=""):
    status, raw = hub.raw(None, b"", method="GET", path=f"/api/memory/map{suffix}", headers=headers)
    assert status == 200, raw
    return json.loads(raw)


def seed_private(hub):
    owner = hub.owner_token("seed-owner")
    identifiers = [_save(hub, owner, "PRIVATE_OWNER_PROBE", theme="private_notes")]
    identifiers.append(_save(hub, owner, "SECOND_PRIVATE_PROBE", theme="private.other"))
    path = Path(hub.gateway.workspace_dir(hub.workspace)) / "palace" / KNOWLEDGE_GRAPH_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with KnowledgeGraph(str(path)) as kg:
        kg.add_entity("Hidden person", "person")
        kg.add_entity("Other hidden person", "person")
        kg.add_triple(
            "Hidden person", "hidden predicate", "Other hidden person", wing="private_notes"
        )
        kg.add_entity("Sensitive person", "person")
        kg.add_triple(
            "Sensitive person",
            "private sensitivity",
            "Other hidden person",
            wing="shared",
            sensitivity="private",
        )
        kg.add_entity("Secret person", "person")
        kg.add_triple(
            "Secret person",
            "secret sensitivity",
            "Other hidden person",
            wing="shared",
            sensitivity="secret",
        )
    return identifiers


def test_member_private_wing_leak_reproducer(hub):
    identifiers = seed_private(hub)
    _, headers = person(hub)
    body = graph(hub, headers)
    raw = json.dumps(body)
    assert not any(identifier in raw for identifier in identifiers)
    assert "PRIVATE_OWNER_PROBE" not in raw and "private_notes" not in raw
    assert body == {"nodes": [], "links": [], "themes": [], "weeks": []}


def test_member_private_data_is_identical_to_absent(hub):
    _, headers = person(hub)
    visible = _save(hub, hub.owner_token(), "Visible exact words", theme="shared")
    before = graph(hub, headers)
    assert [n["id"] for n in before["nodes"]] == [visible]
    seed_private(hub)
    assert graph(hub, headers) == before
    assert graph(hub, headers, "?theme=private_notes") == {
        "nodes": [],
        "links": [],
        "themes": [],
        "weeks": [],
    }


def test_owner_person_retains_private_data(hub):
    identifiers = seed_private(hub)
    cookie = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
    )
    body = graph(hub, {"Cookie": f"{sessions.COOKIE}={cookie}"})
    assert set(identifiers) <= {n["id"] for n in body["nodes"]}
    assert {t["id"] for t in body["themes"]} == {"private_notes", "private.other"}
    assert "hidden_predicate" in json.dumps(body)


@pytest.mark.parametrize("lookup", ["error", "missing", "member"])
def test_unproved_current_role_hides_private_data(hub, monkeypatch, lookup):
    _save(hub, hub.owner_token(), "Visible exact words", theme="shared")
    owner = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    ctx = ToolContext(principal=owner, gateway=hub.gateway)
    before = memory_tools.memory_map(ctx)
    seed_private(hub)
    calls = []

    def role(workspace, user):
        calls.append((workspace, user))
        if lookup == "error":
            raise RuntimeError("role unavailable")
        return None if lookup == "missing" else "member"

    monkeypatch.setattr(hub.db, "role", role)
    assert memory_tools.memory_map(ctx) == before
    assert calls == [(hub.workspace, hub.admin)]


def test_member_cached_owner_role_cannot_authorize_map(hub):
    uid, _ = person(hub)
    member = sessions.person_principal(hub.db, uid, hub.workspace)
    ctx = ToolContext(principal=replace(member, role="owner"), gateway=hub.gateway)
    before = memory_tools.memory_map(ctx)
    seed_private(hub)
    assert memory_tools.memory_map(ctx) == before


def test_scoped_connection_remains_limited_to_its_wings(hub):
    _save(hub, hub.owner_token(), "Readable", theme="shared")
    scoped = _scoped(hub)
    before = _http(hub, scoped, "/api/memory/map")
    seed_private(hub)
    assert _http(hub, scoped, "/api/memory/map") == before


def test_map_selection_obeys_private_boundary_without_changing_other_reads(hub):
    identifiers = seed_private(hub)
    _, headers = person(hub)
    path = f"/api/memory/{identifiers[0]}"
    assert hub.raw(None, b"", method="GET", path=path, headers=headers)[0] == 200
    status, raw = hub.raw(None, b"", method="GET", path=path + "?view=map", headers=headers)
    assert status == 404 and json.loads(raw) == {"error": "no: memory not found"}


def test_private_drawer_metadata_does_not_change_member_counts(hub, collection):
    _, headers = person(hub)
    _save(hub, hub.owner_token(), "Visible", theme="shared")
    before = graph(hub, headers)
    collection.add(
        ids=["private-visibility-drawer"],
        documents=["Private drawer label"],
        metadatas=[{"wing": "shared", "room": "notes", "visibility": "private"}],
    )
    assert graph(hub, headers) == before


def test_private_fact_citation_cannot_reveal_people(hub):
    _, headers = person(hub)
    _save(hub, hub.owner_token(), "Visible", theme="shared")
    before = graph(hub, headers)
    identifiers = seed_private(hub)
    path = Path(hub.gateway.workspace_dir(hub.workspace)) / "palace" / KNOWLEDGE_GRAPH_FILENAME
    with KnowledgeGraph(str(path)) as kg:
        kg.add_triple(
            "Hidden person",
            "public citation",
            "Other hidden person",
            wing="shared",
            source_drawer_id=identifiers[0],
        )
    assert graph(hub, headers) == before


def test_private_linked_card_is_hidden_in_graph_and_selection(hub, board_store):  # noqa: F811
    _, headers = person(hub)
    owner = hub.owner_token()
    identifier = _save(hub, owner, "Visible", theme="shared")
    before = graph(hub, headers)
    _call(hub, owner, "memory_link", id=identifier, task=8)
    assert graph(hub, headers) == before
    status, raw = hub.raw(
        None, b"", method="GET", path=f"/api/memory/{identifier}?view=map", headers=headers
    )
    assert status == 200 and json.loads(raw)["cards"] == []
