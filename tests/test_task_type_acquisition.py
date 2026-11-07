"""Legacy typed rows exercise acquisition boundaries on both base and head.

SQL creates legacy fixtures only: base has no public type-writing API. All
acquisition, update and replacement actions under test use production methods.
"""

import pytest

from commonplace.extensions import ToolError
from commonplace.runner import predicate
from tests.test_commonplace_boards import env as env, hub_fixture as hub_fixture, service


@pytest.fixture
def legacy(hub, env):
    _, agent, person, cookie, owner = env
    cards = [owner.task_add(title, board="work") for title in ("Wrong first", "Code", "Plain")]
    with owner.store.transaction() as conn:
        if "task_type" not in {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}:
            conn.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS connection_task_types (connection_id TEXT NOT NULL, task_type TEXT NOT NULL, PRIMARY KEY(connection_id,task_type))"
        )
        for card, label in zip(cards, ["review", "code", ""]):
            conn.execute("UPDATE tasks SET task_type=? WHERE id=?", (label, card["number"]))
    return agent, person, owner, cards


def refuse(call):
    try:
        call()
    except ToolError as exc:
        assert "task type" in str(exc).lower(), str(exc)
    else:
        raise AssertionError("task type guard did not refuse acquisition")


@pytest.mark.parametrize("operation", ["explicit", "update", "auto"])
def test_legacy_unmapped_acquisition(hub, legacy, operation):
    agent, _, _, cards = legacy
    worker = service(hub, agent)
    if operation == "auto":
        result = worker.task_take_next(board="work")
        assert result["number"] == cards[2]["number"], "auto must skip typed cards without grants"
    elif operation == "explicit":
        refuse(lambda: worker.task_take_next(task=cards[1]["number"]))
    else:
        refuse(lambda: worker.task_update(cards[1]["number"], status="doing"))


def test_legacy_wrong_first_skip(hub, legacy):
    agent, _, owner, cards = legacy
    with owner.store.transaction() as conn:
        conn.execute("INSERT INTO connection_task_types VALUES (?, 'code')", (agent.connection_id,))
    result = service(hub, agent).task_take_next(board="work")
    assert result["number"] == cards[1]["number"], "only exact allowed label is eligible"


@pytest.mark.parametrize("corruption", ["type", "mapping", "missing-table"])
def test_legacy_corruption_refuses(hub, legacy, corruption):
    agent, _, owner, cards = legacy
    with owner.store.transaction() as conn:
        if corruption == "type":
            conn.execute("UPDATE tasks SET task_type='Invalid' WHERE id=?", (cards[1]["number"],))
        elif corruption == "mapping":
            conn.execute(
                "INSERT INTO connection_task_types VALUES (?, 'Invalid')", (agent.connection_id,)
            )
        else:
            conn.execute("DROP TABLE connection_task_types")
    # Reuse already-open service: initialization must not repair the corruption fixture.
    worker = (
        service(hub, agent)
        if corruption != "missing-table"
        else service(
            hub,
            agent,
        )
    )
    if corruption == "missing-table":
        with owner.store.transaction() as conn:
            conn.execute("DROP TABLE IF EXISTS connection_task_types")
    refuse(lambda: worker.task_take_next(task=cards[1]["number"]))


@pytest.mark.parametrize("allowed", [False, True])
def test_legacy_runner_exact_reason(hub, legacy, allowed):
    agent, _, owner, cards = legacy
    if allowed:
        with owner.store.transaction() as conn:
            conn.execute(
                "INSERT INTO connection_task_types VALUES (?, 'code')", (agent.connection_id,)
            )
    with owner.store.connection() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (cards[1]["number"],)).fetchone()
        board = conn.execute("SELECT * FROM boards WHERE slug='work'").fetchone()
    # Runner worker's single write wing is part of existing admission authority.
    token = hub.token(
        "runner-worker",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared"],
        write_wing="shared",
        trust="trusted",
    )
    worker = hub.gateway.authenticate(f"Bearer {token}")
    if allowed:
        with owner.store.transaction() as conn:
            conn.execute(
                "INSERT INTO connection_task_types VALUES (?, 'code')", (worker.connection_id,)
            )
    machine = {"workspace_id": hub.workspace, "worker_connection_id": worker.connection_id}
    if allowed:
        predicate.startable(hub.gateway, machine, row, board)
    else:
        try:
            predicate.startable(hub.gateway, machine, row, board)
        except predicate.Refused as exc:
            assert exc.reason == "task_type_not_allowed"
        else:
            raise AssertionError("runner must refuse unmapped typed work")


def test_legacy_person_and_untyped_unchanged(hub, legacy):
    agent, person, owner, cards = legacy
    assert owner.task_take_next(task=cards[0]["number"])["assignee"] == person.actor
    assert (
        service(hub, agent).task_take_next(task=cards[2]["number"])["number"] == cards[2]["number"]
    )


def test_legacy_rollback_expiry(hub, legacy):
    agent, _, owner, cards = legacy
    held = owner.task_add("Expired other card", board="work", status="doing", assignee=agent.actor)
    with owner.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET claim_until='2000-01-01T00:00:00Z' WHERE id=?", (held["number"],)
        )
    with owner.store.connection() as conn:
        before = [tuple(r) for r in conn.execute("SELECT * FROM tasks ORDER BY id")]
        events = [tuple(r) for r in conn.execute("SELECT * FROM task_events ORDER BY id")]
    refuse(lambda: service(hub, agent).task_take_next(task=cards[1]["number"]))
    with owner.store.connection() as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM tasks ORDER BY id")] == before
        assert [tuple(r) for r in conn.execute("SELECT * FROM task_events ORDER BY id")] == events
