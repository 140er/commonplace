"""Factory policy: the optional ``[factory]`` section of an orchestrator config.

Schema (valid policy, contradictions, canonical form, render, diff, experiment rule),
the read path (``orch_config_get``, ``orch_checkpoint``), the merge guard of the journal,
and a board without a policy, which behaves as before.
"""

import copy
import hashlib

import pytest

from commonplace.extensions import ToolError
from commonplace.orch import durable, schema, store as orch_store, tools
from commonplace.orch.schema import ConfigError
from tests import test_orch_durable as base
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_durable import BOARD, HEAD, REPO, ROLE, at, code_of, lease

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

FACTORY = """\
[factory.gate]
required = true
name = "merge gate"
[factory.checks.pr-check]
pass = ["PASS"]
never_pass = ["SUPERSEDED", "QUICK-PASS"]
[factory.checks.lint]
required = false
pass = ["ok"]
[factory.batch]
branch = "develop"
full_suite = "per-batch"
on_red = "hold-merges"
"""
POLICY = {
    "gate": {"required": True, "name": "merge gate"},
    "checks": {
        "lint": {"required": False, "scope": "affected", "pass": ["ok"]},
        "pr-check": {
            "required": True,
            "scope": "affected",
            "pass": ["PASS"],
            "never_pass": ["SUPERSEDED", "QUICK-PASS"],
        },
    },
    "batch": {"branch": "develop", "full_suite": "per-batch", "on_red": "hold-merges"},
}


def doc(factory=FACTORY):
    return tomllib.loads(base.config_text() + factory)


def refused(data, code, path):
    with pytest.raises(ConfigError) as info:
        schema.validate(data)
    assert (info.value.code, info.value.path) == (code, path), str(info.value)
    assert info.value.code in schema.CODES


# ---------------------------------------------------------------------- schema


def test_valid_policy_is_accepted_and_has_one_canonical_form():
    config = schema.parse(base.config_text() + FACTORY)
    assert config.factory.to_dict() == POLICY
    assert config.to_dict()["factory"] == POLICY
    assert schema.validate(config.to_dict()).factory == config.factory


def test_summary_and_render_state_the_policy_in_plain_lines():
    config = schema.validate(doc())
    assert config.factory.summary() == [
        "Gate (merge gate): required. Only a pass verdict counts; fail, error and budget never do.",
        "Check lint (affected tests): optional. Passes on ok. Never a pass: TIMEOUT, SKIP, ERROR.",
        "Check pr-check (affected tests): required. Passes on PASS. "
        "Never a pass: SUPERSEDED, QUICK-PASS, TIMEOUT, SKIP, ERROR.",
        "Full suite on develop: once per batch of merges. "
        "A red run holds merges until it is green.",
    ]
    rendered = schema.render(config)
    assert "## Merge policy" in rendered
    assert all(f"- {line}" in rendered for line in config.factory.summary())


def test_config_without_factory_is_unchanged():
    config = schema.parse(base.config_text())
    assert config.factory is None
    assert "factory" not in config.to_dict()
    assert "Merge policy" not in schema.render(config)


def test_minimal_policies():
    gate_only = schema.validate(doc("[factory.gate]\nrequired = true\n")).factory
    assert gate_only.to_dict() == {"gate": {"required": True}}
    check_only = schema.validate(
        doc('[factory.gate]\nrequired = false\n[factory.checks.unit]\npass = ["green"]\n')
    ).factory
    assert check_only.summary()[0] == "Gate: not required."
    per_pr = doc(
        '[factory.gate]\nrequired = true\n[factory.checks.suite]\nscope = "full"\n'
        'pass = ["PASS"]\n[factory.batch]\nbranch = "main"\nfull_suite = "per-pr"\n'
    )
    assert schema.validate(per_pr).factory.batch.full_suite == "per-pr"


def _with(path, value):
    data = doc()
    node = data["factory"]
    *parents, last = path
    for key in parents:
        node = node[key]
    node[last] = value
    return data


@pytest.mark.parametrize(
    ("path", "value", "where"),
    [
        (("checks", "pr-check", "pass"), ["PASS", "timeout"], "factory.checks.pr-check.pass"),
        (("checks", "pr-check", "pass"), ["Skip"], "factory.checks.pr-check.pass"),
        (("checks", "pr-check", "pass"), ["ERROR"], "factory.checks.pr-check.pass"),
        (("checks", "pr-check", "never_pass"), ["pass"], "factory.checks.pr-check.never_pass"),
        (("checks", "pr-check", "scope"), "full", "factory.checks.pr-check.scope"),
        (("batch", "full_suite"), "per-pr", "factory.batch.full_suite"),
        (("batch", "full_suite"), "off", "factory.batch.on_red"),
    ],
)
def test_contradictions_are_refused(path, value, where):
    refused(_with(path, value), "factory_contradiction", where)


def test_a_policy_that_requires_nothing_is_refused():
    data = _with(("gate", "required"), False)
    data["factory"]["checks"]["pr-check"]["required"] = False
    refused(data, "factory_contradiction", "factory")


@pytest.mark.parametrize(
    ("path", "value", "code", "where"),
    [
        (("gate", "required"), "yes", "wrong_type", "factory.gate.required"),
        (("gate", "command"), "x", "unknown_key", "factory.gate.command"),
        (("gate", "name"), "bad\nname", "invalid_value", "factory.gate.name"),
        (("checks", "pr-check", "pass"), [], "invalid_value", "factory.checks.pr-check.pass"),
        (
            ("checks", "pr-check", "pass"),
            ["a b"],
            "invalid_value",
            "factory.checks.pr-check.pass[0]",
        ),
        (("checks", "pr-check", "pass"), ["x"] * 17, "too_many", "factory.checks.pr-check.pass"),
        (("checks", "pr-check", "scope"), "some", "invalid_value", "factory.checks.pr-check.scope"),
        (("checks", "Bad Name"), {"pass": ["PASS"]}, "invalid_value", "factory.checks.<key>"),
        (("checks",), [], "wrong_type", "factory.checks"),
        (("batch", "branch"), "a b", "invalid_value", "factory.batch.branch"),
        (("batch", "on_red"), "ignore", "invalid_value", "factory.batch.on_red"),
        (("loops",), {}, "unknown_key", "factory.loops"),
    ],
)
def test_structure_refusals(path, value, code, where):
    refused(_with(path, value), code, where)


def test_missing_keys_and_wrong_section_type():
    for path, where in (
        (("gate",), "factory.gate"),
        (("gate", "required"), "factory.gate.required"),
        (("checks", "pr-check", "pass"), "factory.checks.pr-check.pass"),
        (("batch", "branch"), "factory.batch.branch"),
    ):
        data = doc()
        node = data["factory"]
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
        refused(data, "missing_key", where)
    data = doc()
    data["factory"] = "strict"
    refused(data, "wrong_type", "factory")


def test_secret_shaped_value_in_factory_is_refused_not_echoed():
    secret = "ghp_" + "Ab1" * 12
    with pytest.raises(ConfigError) as info:
        schema.validate(_with(("gate", "name"), secret))
    assert info.value.code == "inline_secret" and secret not in str(info.value)


def test_diff_names_the_changed_policy_field_and_experiments_may_not_change_it():
    live = schema.validate(doc())
    changed = schema.validate(_with(("batch", "on_red"), "report"))
    assert [(c.path, c.old, c.new) for c in schema.diff(live, changed)] == [
        ("factory.batch.on_red", "hold-merges", "report")
    ]
    assert schema.experiment_violations(live, changed) == ["factory.batch.on_red"]
    removed = copy.deepcopy(doc())
    del removed["factory"]
    with pytest.raises(ConfigError) as info:
        schema.check_experiment(live, schema.validate(removed))
    assert info.value.code == "experiment_not_allowed"


# ------------------------------------------------- read path and merge guard


@pytest.fixture
def env(hub):
    return base.Env(hub)


def accept(env, text):
    st = orch_store.open_store(env.hub.gateway, env.ws)
    st.write(
        env.owner, BOARD, text, connections=[], sha256=hashlib.sha256(text.encode()).hexdigest()
    )


def gate(env, ctx, epoch, token, key, verdict, card, head=HEAD, second=1):
    durable.gate_open(
        ctx, BOARD, ROLE, epoch, token, key, repo=REPO, pr=7, head=head, card=card, now=at(second)
    )
    return durable.gate_record(ctx, BOARD, ROLE, epoch, token, key, verdict, now=at(second))


def merge(ctx, epoch, token, key, card=None, second=50):
    return durable.journal_intent(
        ctx, BOARD, ROLE, epoch, token, key, "merge", card=card, now=at(second)
    )


def test_board_without_a_policy_behaves_as_before(env):
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    card = env.card()
    assert merge(ctx, epoch, token, "m-free")["state"] == "intent"
    gate(env, ctx, epoch, token, "g1", "fail", card)
    assert merge(ctx, epoch, token, "m-card", card=card)["state"] == "intent"
    assert "factory" not in durable.checkpoint(ctx, BOARD, ROLE, now=at(60))
    assert "factory" not in tools.config_get(ctx, BOARD)
    assert "Merge policy" not in tools.config_render(ctx, BOARD)["markdown"]


def test_read_path_returns_the_accepted_policy(env):
    accept(env, base.config_text() + FACTORY)
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    gate(env, ctx, epoch, token, "g1", "fail", env.card())
    gate(env, ctx, epoch, token, "g2", "pass", env.card(), head="b" * 40)
    durable.gate_open(
        ctx, BOARD, ROLE, epoch, token, "g3", repo=REPO, pr=8, head="c" * 40, now=at(3)
    )
    read = tools.config_get(ctx, BOARD)
    assert read["factory"]["policy"] == POLICY
    assert read["factory"]["summary"][0].startswith("Gate (merge gate): required.")
    assert "[factory.gate]" in read["accepted"]["body"]
    assert "## Merge policy" in tools.config_render(ctx, BOARD)["markdown"]
    factory = durable.checkpoint(ctx, BOARD, ROLE, now=at(60))["factory"]
    assert factory["policy"] == POLICY and factory["summary"] == read["factory"]["summary"]
    assert factory["gates"] == {
        "open": 1,
        "gate_pass": 1,
        "gate_fail": 1,
        "gate_error": 0,
        "gate_budget": 0,
    }


def test_merge_step_needs_the_cards_newest_gate_to_have_passed(env):
    accept(env, base.config_text() + FACTORY)
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    card, other = env.card(), env.card("Other card")
    with pytest.raises(ToolError) as exc:
        merge(ctx, epoch, token, "m1")
    assert code_of(exc) == "factory_card_required"
    with pytest.raises(ToolError) as exc:
        merge(ctx, epoch, token, "m1", card=card)
    assert code_of(exc) == "factory_gate_required" and exc.value.details["gate"] is None
    for second, (key, verdict) in enumerate(
        (("g-fail", "fail"), ("g-error", "error"), ("g-budget", "budget")), 1
    ):
        gate(env, ctx, epoch, token, key, verdict, card, head=str(second) * 40, second=second)
        with pytest.raises(ToolError) as exc:
            merge(ctx, epoch, token, "m1", card=card)
        assert code_of(exc) == "factory_gate_required"
        assert exc.value.details["gate"]["state"] == "gate_" + verdict
    durable.gate_open(
        ctx,
        BOARD,
        ROLE,
        epoch,
        token,
        "g-open",
        repo=REPO,
        pr=7,
        head="d" * 40,
        card=card,
        now=at(5),
    )
    with pytest.raises(ToolError) as exc:
        merge(ctx, epoch, token, "m1", card=card)
    assert exc.value.details["gate"]["state"] == "open"
    # Another card's pass does not count.
    gate(env, ctx, epoch, token, "g-other", "pass", other, head="e" * 40, second=6)
    with pytest.raises(ToolError) as exc:
        merge(ctx, epoch, token, "m1", card=card)
    assert code_of(exc) == "factory_gate_required"
    with env.runner_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orch_steps WHERE kind='merge'").fetchone()[0] == 0
    durable.gate_record(ctx, BOARD, ROLE, epoch, token, "g-open", "pass", now=at(7))
    step = merge(ctx, epoch, token, "m1", card=card)
    assert (step["kind"], step["state"], step["card"]) == ("merge", "intent", card)
    # A later head that fails its gate: the stored step is returned on retry, a new one refused.
    gate(env, ctx, epoch, token, "g-late", "fail", card, head="f" * 40, second=8)
    assert merge(ctx, epoch, token, "m1", card=card) == step
    with pytest.raises(ToolError) as exc:
        merge(ctx, epoch, token, "m2", card=card)
    assert code_of(exc) == "factory_gate_required"
    # The owner voids the failed gate: the newest unvoided gate is the pass again.
    durable.void_step(env.ctx(env.owner), BOARD, ROLE, "g-late", now=at(9))
    assert merge(ctx, epoch, token, "m2", card=card)["state"] == "intent"


def test_a_new_accepted_version_changes_what_the_hub_enforces(env):
    accept(env, base.config_text() + FACTORY)
    ctx = env.ctx(env.a)
    epoch, token = lease(env)
    with pytest.raises(ToolError) as exc:
        merge(ctx, epoch, token, "m1")
    assert code_of(exc) == "factory_card_required"
    accept(env, base.config_text() + FACTORY.replace("required = true", "required = false", 1))
    assert merge(ctx, epoch, token, "m1")["state"] == "intent"
    assert tools.config_get(ctx, BOARD)["factory"]["policy"]["gate"]["required"] is False
    accept(env, base.config_text())
    assert merge(ctx, epoch, token, "m2")["state"] == "intent"
    assert "factory" not in durable.checkpoint(ctx, BOARD, ROLE, now=at(60))
