"""Index figures sum every wing, so they follow person private-read ownership."""

import json

from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_admin_api import cookie
from tests.test_admin_instance_api import instance
from tests.test_index_stats_readonly import status

SIZES = ("drawers", "vector_count", "hnsw_capacity")
FULL = {
    "drawers": {"value": 101, "reason": None},
    "embedding_model": {"value": None, "reason": "model_not_in_sqlite"},
    "dimension": {"value": 3, "reason": None},
    "vector_count": {"value": 101, "reason": None},
    "hnsw_capacity": {"value": 128, "reason": None},
    "tombstones": {"value": None, "reason": "unsynchronized_sources"},
}


def fixed_figures(monkeypatch):
    from commonplace import index_stats

    monkeypatch.setattr(index_stats, "read", lambda *a, **kw: dict(FULL))


def by_workspace(console):
    return {row["workspace_id"]: row for row in console["workspaces"]}


def test_console_hides_sizes_of_workspaces_the_admin_does_not_own(hub, monkeypatch):
    # Behavioral precondition first, before any implementation module is imported.
    assert instance(hub, "index-stats", cookie(hub))[0] == 200
    fixed_figures(monkeypatch)
    stranger = hub.db.create_user("stranger@example.com", "Stranger")
    foreign = hub.db.create_workspace("theirs", stranger)
    # A plain member of a foreign workspace is not its owner either.
    shared = hub.db.create_workspace("shared", stranger)
    hub.db.add_member(shared, hub.admin, "member", actor=stranger)
    code, console, _ = instance(hub, "index-stats", cookie(hub))
    assert code == 200
    rows = by_workspace(console)
    own = rows[hub.workspace]
    assert {k: own[k] for k in FULL} == FULL
    for ws in (foreign, shared):
        row = rows[ws]
        for key in SIZES:
            assert row[key] == {"value": None, "reason": "private_scope"}
        assert row["dimension"] == FULL["dimension"]
        assert row["embedding_model"] == FULL["embedding_model"]
    assert len(rows) == 3


def test_status_hides_sizes_without_private_read(hub, monkeypatch):
    headers = cookie(hub)
    assert "index_stats" in status(hub, headers)
    fixed_figures(monkeypatch)
    from commonplace import sessions

    assert status(hub, headers)["index_stats"]["drawers"] == FULL["drawers"]
    real = sessions.person_policy
    monkeypatch.setattr(sessions, "person_policy", lambda role=None: real("member"))
    figures = status(hub, cookie(hub))["index_stats"]
    for key in SIZES:
        assert figures[key] == {"value": None, "reason": "private_scope"}
    assert figures["dimension"] == FULL["dimension"]
    json.dumps(figures)
