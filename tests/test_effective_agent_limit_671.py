"""One effective agent limit: Admin, project settings and the runtime agree (card 671)."""

import pytest

from commonplace.projects import limits, orchestration
from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests._phase_b_helpers import World, cap, claim, seed
from tests.test_admin_runtime_api import (  # noqa: F401
    cookie,
    get,
    runner,
    runtime,
    switch,
)
from tests.test_orch_store import config_text


def accept(conn, board, body, version=1):
    import hashlib

    conn.executescript(open("commonplace/orch/schema.sql").read())
    conn.execute(
        "INSERT INTO orch_configs(board,version,channel,status,sha256,body,proposed_by,proposer_trust,created_at) "
        "VALUES (?,?,'live','accepted',?,?,'synthetic','trusted','2026-11-07T10:00:00Z')",
        (board, version, hashlib.sha256(body.encode()).hexdigest(), body),
    )


# ------------------------------------------------------------------ runtime


def test_claims_respect_the_config_limit_with_the_workspace_switch_off(hub):
    """A configured board with limit 1: a second agent is refused whatever the switch says."""
    w = World(hub)
    seed(w, on=False)
    w.configure(max_agents=1)
    first, second = w.card(), w.card()
    assert claim(w, first, w.a)[0] == 200
    status, body = claim(w, second, w.b)
    assert status != 200 and "maximum of 1 agents" in str(body)


def test_claims_use_the_smaller_of_config_and_project_limit_with_the_switch_off(hub):
    w = World(hub)
    seed(w, on=False)
    w.configure(max_agents=3)
    cap(w, 1)
    first, second = w.card(), w.card()
    assert claim(w, first, w.a)[0] == 200
    assert claim(w, second, w.b)[0] != 200


# -------------------------------------------------------------------- Admin


def alpha(hub):
    return get(hub, "limits", cookie(hub))[1]["projects"][0]


@pytest.mark.parametrize("on", [False, True])
def test_admin_table_shows_the_accepted_config_limit(hub, runtime, on):  # noqa: F811
    if on:
        switch(hub)
    with runner(runtime) as conn:
        accept(conn, "alpha", config_text(board="alpha", max_agents=2))
    row = alpha(hub)
    assert (row["accepted_config_max_agents"], row["effective_max_agents"]) == (2, 2)
    assert row["limit_source"] == "accepted_config" and row["accepted_config_applies"] is True


@pytest.mark.parametrize("on", [False, True])
def test_admin_table_takes_the_smaller_of_config_and_stepper(hub, runtime, on):  # noqa: F811
    if on:
        switch(hub)
    with runtime[0].connection() as conn:
        limits.put(conn, 1, {"max_agents": 1})
    with runner(runtime) as conn:
        accept(conn, "alpha", config_text(board="alpha", max_agents=2))
    row = alpha(hub)
    assert (row["configured_max_agents"], row["accepted_config_max_agents"]) == (1, 2)
    assert (row["effective_max_agents"], row["limit_source"]) == (1, "project")


def test_config_without_runner_is_shown_but_does_not_apply(hub, runtime):  # noqa: F811
    with runner(runtime) as conn:
        accept(conn, "alpha", config_text(board="alpha", runner=False, max_agents=2))
    row = alpha(hub)
    assert row["accepted_config_max_agents"] == 2 and row["accepted_config_applies"] is False
    assert row["effective_max_agents"] is None and row["limit_source"] == "unlimited"


# ------------------------------------------------------------ one function


def test_runtime_and_admin_read_the_same_resolver(hub, runtime):  # noqa: F811
    switch(hub)
    with runtime[0].connection() as conn:
        limits.put(conn, 1, {"max_agents": 5})
    with runner(runtime) as conn:
        accept(conn, "alpha", config_text(board="alpha", max_agents=2))
        with runtime[0].connection() as bc:
            found = orchestration.resolve(bc, conn, 1, "alpha", on=True)
            used = orchestration.usage(
                bc,
                conn,
                board_id=1,
                board_slug="alpha",
                workers=frozenset(),
                now="2026-11-07T12:00:00Z",
            )
    assert found.effective == used.cap == alpha(hub)["effective_max_agents"] == 2
