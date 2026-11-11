"""Organigram: reporting lines as config data, escalation and reassignment along them.

The tree checks are pure schema tests. The routing tests use the durable test board
(``main`` with ``builder`` reporting to it) and a second board whose config has no
``parent`` key at all.
"""

import copy
import hashlib

import pytest

from commonplace.extensions import ToolError
from commonplace.orch import durable, organigram, rest, schema, store as orch_store, tools
from commonplace.orch.schema import ConfigError
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_durable import (
    BOARD,
    HEAD,
    OTHER_ROLE,
    REPO,
    ROLE,
    Env,
    at,
    code_of,
    config_text,
    lease,
)

FLAT = "plain"  # Env's second board; its config below has no reporting lines

TREE = {
    "schema": schema.SCHEMA_ID,
    "project": "acme",
    "roles": {
        "lead": {"kind": "orchestrator"},
        "backend": {"kind": "sub-orchestrator", "parent": "lead"},
        "frontend": {"kind": "sub-orchestrator", "parent": "lead"},
        "builder": {"kind": "builder", "parent": "backend"},
        "reviewer": {"kind": "reviewer", "parent": "backend"},
        "solo": {"kind": "builder"},
    },
}


def tree(**parents):
    doc = copy.deepcopy(TREE)
    for name, parent in parents.items():
        doc["roles"][name]["parent"] = parent
    return doc


@pytest.fixture
def env(hub):
    return Env(hub)


def flat_env(env):
    """Accept a config without any ``parent`` on the second board."""
    text = config_text(board=FLAT).replace('parent = "main"\n', "")
    assert "parent" not in text
    orch_store.open_store(env.hub.gateway, env.ws).write(
        env.owner, FLAT, text, connections=[], sha256=hashlib.sha256(text.encode()).hexdigest()
    )


def blocked_step(env, ctx, role, epoch, token, key, state="needs_person", board=BOARD):
    durable.journal_intent(ctx, board, role, epoch, token, key, "merge", now=at(1))
    durable.journal_record(ctx, board, role, epoch, token, key, "in_doubt", now=at(2))
    if state == "needs_person":
        durable.journal_record(ctx, board, role, epoch, token, key, "needs_person", now=at(3))


# ------------------------------------------------------------------ the tree


def test_valid_tree_is_accepted_and_readable():
    config = schema.validate(tree())
    assert organigram.reports_to(config, "builder") == "backend"
    assert organigram.reports_to(config, "lead") is None
    assert organigram.reports_to(config, "ghost") is None
    assert organigram.line(config, "builder") == ("backend", "lead")
    assert organigram.line(config, "solo") == ()
    assert organigram.direct_reports(config, "lead") == ("backend", "frontend")
    assert organigram.direct_reports(config, "backend") == ("builder", "reviewer")
    assert organigram.direct_reports(config, "builder") == ()
    assert organigram.escalation_target(config, "reviewer") == "backend"
    assert organigram.escalation_target(config, "lead") == organigram.OWNER
    assert organigram.escalation_target(config, "solo") == organigram.OWNER


@pytest.mark.parametrize(
    "parents",
    [
        {"lead": "builder"},  # lead -> builder -> backend -> lead
        {"backend": "builder"},  # two roles reporting to each other
        {"solo": "solo"},  # a role reporting to itself
    ],
)
def test_cycle_is_refused(parents):
    with pytest.raises(ConfigError) as exc:
        schema.validate(tree(**parents))
    assert exc.value.code == "parent_cycle"


def test_unknown_parent_is_refused():
    with pytest.raises(ConfigError) as exc:
        schema.validate(tree(builder="ghost"))
    assert (exc.value.code, exc.value.path) == ("unknown_role", "roles.builder.parent")


# ---------------------------------------------------------------- escalation


def test_blocked_result_is_routed_to_the_role_it_reports_to(env):
    ctx_a, ctx_b = env.ctx(env.a), env.ctx(env.b)
    em, tm = lease(env, env.a, ROLE)
    eb, tb = lease(env, env.b, OTHER_ROLE)
    blocked_step(env, ctx_b, OTHER_ROLE, eb, tb, "b-stuck")
    blocked_step(env, ctx_b, OTHER_ROLE, eb, tb, "b-doubt", state="in_doubt")
    durable.journal_intent(ctx_b, BOARD, OTHER_ROLE, eb, tb, "b-open", "merge", now=at(4))
    blocked_step(env, ctx_a, ROLE, em, tm, "m-stuck")

    main = durable.checkpoint(env.ctx(env.owner), BOARD, ROLE, now=at(5))
    assert [(s["role"], s["step_key"], s["state"]) for s in main["escalations"]] == [
        (OTHER_ROLE, "b-stuck", "needs_person"),
        (OTHER_ROLE, "b-doubt", "in_doubt"),
    ]
    # The top of the line still reports to the owner: its own view is as before.
    assert [s["step_key"] for s in main["needs_person"]] == ["m-stuck"]

    builder = durable.checkpoint(env.ctx(env.owner), BOARD, OTHER_ROLE, now=at(5))
    assert "escalations" not in builder  # nobody reports to the builder
    assert [s["step_key"] for s in builder["needs_person"]] == ["b-stuck"]
    assert [s["step_key"] for s in builder["in_doubt"]] == ["b-doubt"]


def test_voided_and_settled_steps_are_not_escalated(env):
    ctx_b = env.ctx(env.b)
    eb, tb = lease(env, env.b, OTHER_ROLE)
    blocked_step(env, ctx_b, OTHER_ROLE, eb, tb, "gone")
    blocked_step(env, ctx_b, OTHER_ROLE, eb, tb, "settled", state="in_doubt")
    durable.journal_record(ctx_b, BOARD, OTHER_ROLE, eb, tb, "settled", "failed", now=at(4))
    durable.void_step(env.ctx(env.owner), BOARD, OTHER_ROLE, "gone", now=at(5))
    assert durable.checkpoint(env.ctx(env.owner), BOARD, ROLE, now=at(6))["escalations"] == []


# ---------------------------------------------------------------- delegation


def test_only_the_role_it_reports_to_reassigns_a_step(env):
    ctx_a, ctx_b = env.ctx(env.a), env.ctx(env.b)
    em, tm = lease(env, env.a, ROLE)
    eb, tb = lease(env, env.b, OTHER_ROLE)
    blocked_step(env, ctx_b, OTHER_ROLE, eb, tb, "b-stuck")
    blocked_step(env, ctx_a, ROLE, em, tm, "m-stuck")

    # Against the line: a report cannot take its manager's work, nor its own.
    for report, key in ((ROLE, "m-stuck"), (OTHER_ROLE, "b-stuck")):
        with pytest.raises(ToolError) as exc:
            organigram.reassign_step(ctx_b, BOARD, OTHER_ROLE, eb, tb, report, key, now=at(10))
        assert code_of(exc) == "not_in_line"
    # The manager role, but not its lease holder.
    with pytest.raises(ToolError) as exc:
        organigram.reassign_step(ctx_b, BOARD, ROLE, em, "wrong-token", OTHER_ROLE, "b-stuck")
    assert code_of(exc) == "fenced"
    with pytest.raises(ToolError) as exc:
        organigram.reassign_step(ctx_a, BOARD, ROLE, em, tm, "ghost", "b-stuck", now=at(10))
    assert code_of(exc) == "unknown_role"
    with pytest.raises(ToolError) as exc:
        organigram.reassign_step(ctx_a, BOARD, ROLE, em, tm, OTHER_ROLE, "nope", now=at(10))
    assert code_of(exc) == "step_not_found"
    with env.runner_db() as conn:
        assert (
            conn.execute("SELECT count(*) FROM orch_steps WHERE voided_at IS NOT NULL").fetchone()[
                0
            ]
            == 0
        )

    step = organigram.reassign_step(ctx_a, BOARD, ROLE, em, tm, OTHER_ROLE, "b-stuck", now=at(11))
    assert step["voided"] and step["voided_by"] == "conn:" + env.a.connection_id
    assert step["state"] == "needs_person"  # history kept
    again = organigram.reassign_step(ctx_a, BOARD, ROLE, em, tm, OTHER_ROLE, "b-stuck", now=at(12))
    assert again["voided_at"] == step["voided_at"] == at(11)
    with env.runner_db() as conn:
        log = conn.execute(
            "SELECT role, epoch, actor FROM orch_journal WHERE event='reassign'"
        ).fetchall()
    assert [tuple(row) for row in log] == [(OTHER_ROLE, None, "conn:" + env.a.connection_id)]

    owner = env.ctx(env.owner)
    assert durable.checkpoint(owner, BOARD, ROLE, now=at(13))["escalations"] == []
    assert durable.checkpoint(owner, BOARD, OTHER_ROLE, now=at(13))["needs_person"] == []
    # The owner's void is untouched, also for the top of the line.
    assert durable.void_step(owner, BOARD, ROLE, "m-stuck", now=at(14))["voided"]


def test_gates_and_holds_stay_with_the_owner(env):
    ctx_a, ctx_b = env.ctx(env.a), env.ctx(env.b)
    em, tm = lease(env, env.a, ROLE)
    eb, tb = lease(env, env.b, OTHER_ROLE)
    durable.gate_open(ctx_b, BOARD, OTHER_ROLE, eb, tb, "g1", repo=REPO, pr=7, head=HEAD, now=at(1))
    durable.gate_record(ctx_b, BOARD, OTHER_ROLE, eb, tb, "g1", "fail", now=at(2))
    durable.journal_intent(ctx_b, BOARD, OTHER_ROLE, eb, tb, "h1", "hold", now=at(3))
    for key in ("g1", "h1"):
        with pytest.raises(ToolError) as exc:
            organigram.reassign_step(ctx_a, BOARD, ROLE, em, tm, OTHER_ROLE, key, now=at(4))
        assert code_of(exc) == "owner_only_kind"
    # The failed head stays gated: no second attempt by reassigning.
    with pytest.raises(ToolError) as exc:
        durable.gate_open(ctx_a, BOARD, ROLE, em, tm, "g2", repo=REPO, pr=7, head=HEAD, now=at(5))
    assert code_of(exc) == "gate_exists"


# ------------------------------------------------------- no reporting lines


def test_config_without_reporting_lines_is_unchanged(env):
    flat_env(env)
    ctx_a, ctx_b = env.ctx(env.a), env.ctx(env.b)
    em, tm = (c := durable.resume(ctx_a, FLAT, ROLE, now=at(0)))["epoch"], c["token"]
    c = durable.resume(ctx_b, FLAT, OTHER_ROLE, now=at(0))
    eb, tb = c["epoch"], c["token"]
    blocked_step(env, ctx_b, OTHER_ROLE, eb, tb, "b-stuck", board=FLAT)

    owner = env.ctx(env.owner)
    lined = durable.checkpoint(owner, BOARD, ROLE, now=at(5))
    for role in (ROLE, OTHER_ROLE):
        view = durable.checkpoint(owner, FLAT, role, now=at(5))
        assert "escalations" not in view
        assert set(view) == set(lined) - {"escalations"}
    # Nobody is in anybody's line, in either direction.
    for ctx, role, epoch, token, report in (
        (ctx_a, ROLE, em, tm, OTHER_ROLE),
        (ctx_b, OTHER_ROLE, eb, tb, ROLE),
    ):
        with pytest.raises(ToolError) as exc:
            organigram.reassign_step(ctx, FLAT, role, epoch, token, report, "b-stuck", now=at(6))
        assert code_of(exc) == "not_in_line"
    builder = durable.checkpoint(owner, FLAT, OTHER_ROLE, now=at(7))
    assert [s["step_key"] for s in builder["needs_person"]] == ["b-stuck"]


# ------------------------------------------------------------------ surface


def test_reassign_is_a_holder_tool_with_a_rest_route(env):
    handler, properties, required, kind, trusted, owner, _text = tools.OPERATIONS[
        "orch_reassign_step"
    ]
    assert (kind, trusted, owner) == ("write", True, False)
    assert (
        set(required)
        == set(properties)
        == {
            "board",
            "role",
            "epoch",
            "token",
            "report",
            "step_key",
        }
    )
    assert rest.resolve(
        "POST", ["boards", BOARD, "roles", ROLE, "reports", "builder", "steps", "s1", "reassign"]
    ) == (
        "orch_reassign_step",
        {"board": BOARD, "role": ROLE, "report": "builder", "step_key": "s1"},
    )
    ctx_a, ctx_b = env.ctx(env.a), env.ctx(env.b)
    em, tm = lease(env, env.a, ROLE)
    eb, tb = lease(env, env.b, OTHER_ROLE)
    blocked_step(env, ctx_b, OTHER_ROLE, eb, tb, "b-stuck")
    step = tools.dispatch(
        ctx_a,
        "orch_reassign_step",
        dict(board=BOARD, role=ROLE, epoch=em, token=tm, report=OTHER_ROLE, step_key="b-stuck"),
    )
    assert step["voided"] and step["role"] == OTHER_ROLE
