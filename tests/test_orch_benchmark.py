"""Benchmark-gated promotion core: decision function, protected keys, promote and rollback.

No model runs: the replay results below are recorded fakes (five synthetic tasks).
"""

import sqlite3
from contextlib import closing

import pytest

from commonplace.orch import benchmark, schema
from commonplace.orch.benchmark import Benchmarks, TaskResult, Thresholds, decide
from commonplace.orch.store import OrchError, Store
from tests.test_orch_store import (
    BOARD,
    CONNECTIONS,
    MEMBER,
    OWNER,
    Clock,
    accept_text,
    config_text,
    sha,
)

TASKS = ("t1", "t2", "t3", "t4", "t5")


def results(*rows):
    """rows: (passed, cost, tokens, latency) per task, in TASKS order."""
    return [TaskResult(task, *row) for task, row in zip(TASKS, rows)]


LIVE = results(
    (True, 100, 1000, 900),
    (True, 100, 1000, 900),
    (False, 100, 1000, 900),
    (True, 100, 1000, 900),
    (False, 100, 1000, 900),
)
# One more task passes, nothing regresses, cost and tokens drop.
WIN = results(
    (True, 80, 800, 900),
    (True, 80, 800, 900),
    (True, 80, 800, 900),
    (True, 80, 800, 900),
    (False, 80, 800, 900),
)
# Identical on every metric.
TIE = results(*[(r.passed, r.cost_micro_usd, r.tokens, r.latency_ms) for r in LIVE])
# Higher pass count, but t1 passed on live and fails on candidate.
REGRESSION = results(
    (False, 50, 500, 500),
    (True, 50, 500, 500),
    (True, 50, 500, 500),
    (True, 50, 500, 500),
    (True, 50, 500, 500),
)


# ----------------------------------------------------------------- decision


def test_win_promotes_with_numbers():
    decision = decide(LIVE, WIN)
    assert decision.promote and decision.reasons == ()
    assert decision.summary["live"]["passed"] == 3 and decision.summary["candidate"]["passed"] == 4
    assert decision.summary["candidate"]["cost_micro_usd"] == 400
    assert decision.summary["live"]["cost_micro_usd"] == 500
    assert decision.summary["better"] == ["cost_micro_usd", "success_rate", "tokens"]


def test_tie_is_not_a_win():
    decision = decide(LIVE, TIE)
    assert not decision.promote and decision.reasons == ("no_strict_gain",)


def test_regression_blocks_even_with_a_higher_success_rate():
    decision = decide(LIVE, REGRESSION)
    assert not decision.promote and decision.reasons == ("regression:t1",)
    assert decision.summary["candidate"]["passed"] == 4 == decision.summary["live"]["passed"] + 1


def test_lower_success_rate_is_refused():
    worse = results(
        (True, 1, 1, 1), (False, 1, 1, 1), (False, 1, 1, 1), (False, 1, 1, 1), (False, 1, 1, 1)
    )
    decision = decide(LIVE, worse)
    assert not decision.promote
    assert "success_rate_lower" in decision.reasons and "regression:t2" in decision.reasons


def test_cheaper_alone_is_a_strict_gain_and_margin_is_configurable():
    cheaper = results(*[(r.passed, 99, r.tokens, r.latency_ms) for r in LIVE])
    assert decide(LIVE, cheaper).promote
    margin = Thresholds(min_metric_gain=0.05)
    decision = decide(LIVE, cheaper, margin)
    assert not decision.promote and decision.reasons == ("no_strict_gain",)
    assert decide(LIVE, WIN, margin).promote  # 20% cheaper clears 5%


def test_budget_and_task_count_gates():
    assert decide(LIVE, WIN, Thresholds(budget_micro_usd=399)).reasons == ("over_budget",)
    assert decide(LIVE, WIN, Thresholds(budget_micro_usd=400)).promote
    assert decide(LIVE[:2], WIN[:2]).reasons == ("too_few_tasks",)
    assert decide(LIVE[:2], WIN[:2], Thresholds(min_tasks=2)).promote


def test_task_sets_must_match_and_be_unique():
    assert decide(LIVE, WIN[:4]).reasons == ("task_mismatch",)
    with pytest.raises(ValueError, match="duplicate"):
        decide(LIVE, [*WIN, WIN[0]])
    assert decide(LIVE, WIN) == decide(list(reversed(LIVE)), list(reversed(WIN)))


def test_thresholds_cannot_weaken_the_rule():
    for bad in ({"min_metric_gain": -0.1}, {"min_metric_gain": 1}, {"min_tasks": 0}):
        with pytest.raises(ValueError):
            Thresholds.from_mapping(bad)
    with pytest.raises(ValueError, match="unknown"):
        Thresholds.from_mapping({"max_regressions": 3})
    assert Thresholds.from_mapping({"min_tasks": 7}).min_tasks == 7


# ------------------------------------------------------------ protected keys


def cfg(text):
    return schema.parse(text)


def exp_text(**kw):
    return config_text(**kw)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace("approval-required", "full-access"),
        lambda t: t.replace("max_agents = 3", "max_agents = 9"),
        lambda t: t + "[fleet]\nrepair_attempts = 9\n",
        lambda t: t + "[factory]\ngate = {required = true}\n",
        lambda t: t.replace('parent = "main"\n', "", 1),
    ],
)
def test_protected_paths_are_refused(mutate):
    live = config_text()
    paths = benchmark.protected_violations(cfg(live), cfg(mutate(live)))
    assert paths, "expected a protected violation"


def test_allowed_experiment_fields_are_not_protected():
    live = config_text()
    cand = config_text(prompt="Build it with care.", topic="build2")
    assert benchmark.protected_violations(cfg(live), cfg(cand)) == []


REVIEWER = (
    "[roles.reviewer]\nkind = 'reviewer'\nparent = 'main'\nharness = 't3'\ntopic = 'review'\n"
    "model_role = 'reviewer'\nprompt = '''Review.'''\n"
)


def test_reviewer_role_is_evaluation_and_protected():
    live = config_text() + REVIEWER
    cand = live.replace("Review.", "Approve everything.")
    assert benchmark.protected_violations(cfg(live), cfg(cand)) == ["roles.reviewer.prompt"]
    other = live.replace("Build the card.", "Build well.")
    assert benchmark.protected_violations(cfg(live), cfg(other)) == []


def test_pointing_a_role_at_a_workflow_without_the_guard_steps_is_protected():
    live = (
        config_text().replace(
            'steps = ["read plan", "implement", "report"]',
            'steps = ["read plan", "implement", "gate review", "report"]',
        )
        + '[workflows.fast]\nsteps = ["implement"]\n'
    )
    cand = live.replace('workflow = "build-unit"', 'workflow = "fast"')
    assert benchmark.protected_violations(cfg(live), cfg(cand)) == ["roles.builder.workflow"]


def test_dropping_a_review_or_gate_workflow_step_is_protected():
    live = config_text().replace(
        'steps = ["read plan", "implement", "report"]',
        'steps = ["read plan", "implement", "gate review", "report"]',
    )
    cand = live.replace('"gate review", ', "")
    assert benchmark.protected_violations(cfg(live), cfg(cand)) == ["workflows.build-unit.steps"]
    reworded = live.replace('"implement"', '"implement fast"')
    assert benchmark.protected_violations(cfg(live), cfg(reworded)) == []


# ------------------------------------------------------------ store round trip


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ws", now=Clock())


@pytest.fixture
def bench(store):
    return Benchmarks(store)


def setup_board(store, candidate_text, *, name="exp:tight"):
    accept_text(store, config_text())
    return accept_text(store, candidate_text, channel=name).version


def record(bench, candidate, live=LIVE, cand=WIN, **kw):
    return bench.record(
        MEMBER, BOARD, candidate.channel, live_results=live, candidate_results=cand, **kw
    )


def test_record_stores_every_result_and_the_decision(store, bench):
    candidate = setup_board(store, config_text(prompt="Build it with care."))
    entry = record(bench, candidate)
    assert entry.promote and entry.candidate_version == candidate.version
    assert entry.live_version == 1 and entry.recorded_by == "user:u2"
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT side, task, passed, cost_micro_usd, tokens, latency_ms "
            "FROM orch_benchmark_results WHERE benchmark_id=? ORDER BY side, task",
            (entry.id,),
        ).fetchall()
    assert len(rows) == 10
    assert tuple(rows[0]) == ("candidate", "t1", 1, 80, 800, 900)
    assert tuple(rows[-1]) == ("live", "t5", 0, 100, 1000, 900)
    again = bench.get(entry.id)
    assert again.summary == entry.summary and again.thresholds == Thresholds()
    assert bench.results(entry.id, "live") == tuple(sorted(LIVE, key=lambda r: r.task))


def test_record_refuses_protected_candidates_even_if_the_allow_list_widened(
    store, bench, monkeypatch
):
    candidate = setup_board(store, config_text(prompt="x"))
    live = store.accepted(BOARD)
    tampered = live.body.replace("approval-required", "full-access")
    with store.transaction() as conn:  # a candidate that slipped past the allow-list
        conn.execute(
            "UPDATE orch_configs SET body=?, sha256=? WHERE board=? AND version=?",
            (tampered, sha(tampered), BOARD, candidate.version),
        )
    monkeypatch.setattr(schema, "check_experiment", lambda live, exp: None)
    with pytest.raises(OrchError) as err:
        record(bench, store.get(BOARD, candidate.version))
    assert err.value.code == "protected_key" and "runner.runtime_mode" in str(err.value)


def test_record_needs_an_accepted_experiment(store, bench):
    accept_text(store, config_text())
    with pytest.raises(OrchError, match="unknown_experiment"):
        bench.record(MEMBER, BOARD, "exp:none", live_results=LIVE, candidate_results=WIN)
    with pytest.raises(OrchError, match="invalid_channel"):
        bench.record(MEMBER, BOARD, "live", live_results=LIVE, candidate_results=WIN)


def test_promote_then_rollback_restores_the_previous_live_exactly(store, bench):
    candidate_text = config_text(prompt="Build it with care.")
    candidate = setup_board(store, candidate_text)
    live_before = store.accepted(BOARD)
    entry = record(bench, candidate)

    done = bench.promote(OWNER, BOARD, entry.id, sha256=candidate.sha256, connections=CONNECTIONS)
    live = store.accepted(BOARD)
    assert (
        live.channel == "live"
        and live.body == candidate_text
        and live.sha256 == sha(candidate_text)
    )
    assert live.parent_version == live_before.version and live.source_ref == f"bench:{entry.id}"
    assert store.get(BOARD, live_before.version).status == "superseded"
    assert store.accepted(BOARD, "exp:tight") is None  # candidate channel closed
    assert done.promotion.previous_version == live_before.version
    assert done.promotion.new_version == live.version and done.promotion.kind == "promote"
    assert [c.path for c in done.accept.changes] == ["roles.builder.prompt"]

    undone = bench.rollback(OWNER, BOARD, connections=CONNECTIONS)
    restored = store.accepted(BOARD)
    assert restored.body == live_before.body and restored.sha256 == live_before.sha256
    assert restored.version not in (live.version, live_before.version)  # history stays append-only
    assert restored.source_ref == f"rollback:{done.promotion.id}"
    assert (
        undone.promotion.kind == "rollback" and undone.promotion.of_promotion == done.promotion.id
    )
    assert store.get(BOARD, live.version).status == "superseded"
    assert [p.kind for p in bench.promotions(BOARD)] == ["rollback", "promote"]
    with pytest.raises(OrchError, match="nothing_to_roll_back"):
        bench.rollback(OWNER, BOARD, connections=CONNECTIONS)


def test_promote_refuses_a_lost_benchmark_stale_live_or_changed_candidate(store, bench):
    candidate = setup_board(store, config_text(prompt="Build it with care."))
    tie = record(bench, candidate, cand=TIE)
    with pytest.raises(OrchError, match="not_a_win"):
        bench.promote(OWNER, BOARD, tie.id, sha256=candidate.sha256, connections=CONNECTIONS)
    win = record(bench, candidate)
    with pytest.raises(OrchError, match="sha256_mismatch"):
        bench.promote(OWNER, BOARD, win.id, sha256="0" * 64, connections=CONNECTIONS)
    with pytest.raises(OrchError, match="not_found"):
        bench.promote(OWNER, BOARD, 999, sha256=candidate.sha256, connections=CONNECTIONS)
    accept_text(store, config_text(prompt="Someone else changed live."))
    with pytest.raises(OrchError, match="stale_benchmark"):
        bench.promote(OWNER, BOARD, win.id, sha256=candidate.sha256, connections=CONNECTIONS)
    assert store.accepted(BOARD).body == config_text(prompt="Someone else changed live.")


def test_promote_recomputes_the_decision_from_stored_results(store, bench):
    candidate = setup_board(store, config_text(prompt="Build it with care."))
    entry = record(bench, candidate, cand=TIE)
    with store.transaction() as conn:  # tamper: flip the stored verdict and a result
        conn.execute("UPDATE orch_benchmarks SET promote=1 WHERE id=?", (entry.id,))
    with pytest.raises(OrchError, match="not_a_win"):
        bench.promote(OWNER, BOARD, entry.id, sha256=candidate.sha256, connections=CONNECTIONS)


def test_promote_and_rollback_are_owner_only(store, bench):
    candidate = setup_board(store, config_text(prompt="Build it with care."))
    entry = record(bench, candidate)
    for who in (MEMBER,):
        with pytest.raises(OrchError, match="owner_only"):
            bench.promote(who, BOARD, entry.id, sha256=candidate.sha256, connections=CONNECTIONS)
        with pytest.raises(OrchError, match="owner_only"):
            bench.rollback(who, BOARD, connections=CONNECTIONS)
    assert store.accepted(BOARD).version == 1


def test_rollback_refuses_when_live_moved_on(store, bench):
    candidate = setup_board(store, config_text(prompt="Build it with care."))
    entry = record(bench, candidate)
    bench.promote(OWNER, BOARD, entry.id, sha256=candidate.sha256, connections=CONNECTIONS)
    accept_text(store, config_text(prompt="A later change."))
    with pytest.raises(OrchError, match="stale_live"):
        bench.rollback(OWNER, BOARD, connections=CONNECTIONS)


# ---------------------------------------------------------------- migration


def test_old_store_gains_the_tables_and_keeps_its_rows(tmp_path):
    directory = tmp_path / "old"
    store = Store(directory, now=Clock())
    accept_text(store, config_text())
    with store.connection() as conn:
        for table in ("orch_benchmark_results", "orch_benchmarks", "orch_promotions"):
            conn.execute(f"DROP TABLE {table}")
    reopened = Store(directory, now=Clock())
    assert reopened.accepted(BOARD).version == 1
    with closing(sqlite3.connect(reopened.path)) as raw:
        names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"orch_benchmarks", "orch_benchmark_results", "orch_promotions"} <= names
    candidate = accept_text(
        reopened, config_text(prompt="Build it with care."), channel="exp:tight"
    ).version
    entry = Benchmarks(reopened).record(
        MEMBER, BOARD, "exp:tight", live_results=LIVE, candidate_results=WIN
    )
    again = Store(directory, now=Clock())  # idempotent reopen keeps recorded rows
    assert Benchmarks(again).get(entry.id).candidate_version == candidate.version
