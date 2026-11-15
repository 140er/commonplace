"""The pipeline numbers: one pure function from recorded events to steps, waits and lead time."""

from datetime import datetime, timedelta, timezone

import pytest

from commonplace.pipeline import demo
from commonplace.pipeline.compute import STEPS, compute_pr, parse_at, stamp

T0 = datetime(2026, 11, 15, 9, 0, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(hours=3)


def ev(step, state, seconds, ident=None, **extra):
    return {
        "id": ident or f"{step}-{state}-{seconds}",
        "step": step,
        "state": state,
        "at": stamp(T0 + timedelta(seconds=seconds)),
        **extra,
    }


def step(row, name):
    return next(s for s in row["steps"] if s["step"] == name)


def full_run(extra=()):
    """card 0-10, builder 20-100, pr opened 110, gate 150-200, check 230-300, merge 400-405,
    deploy 420-500 in four steps, backup proof 600-620."""
    return [
        ev("card", "running", 0),
        ev("card", "pass", 10),
        ev("builder", "queued", 12),
        ev("builder", "running", 20),
        ev("builder", "pass", 100),
        ev("pr_opened", "pass", 110),
        ev("gate", "queued", 120),
        ev("gate", "running", 150),
        ev("gate", "pass", 200),
        ev("check", "queued", 210),
        ev("check", "running", 230),
        ev("check", "pass", 300),
        ev("merge", "running", 400),
        ev("merge", "pass", 405),
        ev("deploy_snapshot", "running", 420),
        ev("deploy_snapshot", "pass", 440),
        ev("deploy_rollback_image", "running", 440),
        ev("deploy_rollback_image", "pass", 455),
        ev("deploy_smoke", "running", 460),
        ev("deploy_smoke", "pass", 490),
        ev("deploy_health", "running", 490),
        ev("deploy_health", "pass", 500),
        ev("backup_proof", "running", 600),
        ev("backup_proof", "pass", 620),
        *extra,
    ]


def test_a_finished_pr_has_times_durations_waits_and_a_total():
    row = compute_pr(full_run(), NOW, repo="a/b", pr=7, title="T")
    assert (row["repo"], row["pr"], row["title"], row["state"]) == ("a/b", 7, "T", "done")
    assert [s["step"] for s in row["steps"]] == list(STEPS)
    gate = step(row, "gate")
    assert (gate["state"], gate["start"], gate["end"], gate["duration"]) == (
        "pass",
        stamp(T0 + timedelta(seconds=150)),
        stamp(T0 + timedelta(seconds=200)),
        50,
    )
    # Waiting for the runner: queued at 120, running at 150; the wait is counted from the
    # end of the previous step (pr opened at 110) to the start (150).
    assert gate["wait"]["seconds"] == 40 and gate["wait"]["queued_seconds"] == 30
    assert gate["wait"]["from_step"] == "pr_opened" and not gate["wait"]["ongoing"]
    assert row["lead"] == {
        "seconds": 500,
        "in_progress": False,
        "from": stamp(T0),
        "to": stamp(T0 + timedelta(seconds=500)),
        "basis": "card",
    }
    assert row["current_step"] is None and row["updated_at"] == stamp(T0 + timedelta(seconds=620))
    assert row["slowest"] == {"step": "builder", "seconds": 80}
    assert row["longest_wait"]["before"] == "merge"
    assert row["longest_wait"]["after"] == "check" and row["longest_wait"]["seconds"] == 100


def test_the_lead_time_stops_at_the_healthy_deploy_not_at_the_backup_proof():
    done = compute_pr(full_run(), NOW)
    without_proof = compute_pr(full_run()[:-2], NOW)
    assert done["lead"]["seconds"] == without_proof["lead"]["seconds"] == 500
    assert without_proof["state"] == "done" and without_proof["current_step"] == "backup_proof"


def test_a_pr_in_progress_counts_the_time_so_far_and_a_running_step_its_time_so_far():
    events = full_run()[:11]  # the check is running since 230
    now = T0 + timedelta(seconds=290)
    row = compute_pr(events, now)
    check = step(row, "check")
    assert (row["state"], row["current_step"]) == ("running", "check")
    assert check["running"] is True and check["duration"] == 60 and check["end"] is None
    assert row["lead"]["in_progress"] is True and row["lead"]["seconds"] == 290
    assert row["lead"]["to"] is None
    # The merge step has not appeared: nothing is claimed about it.
    assert step(row, "merge")["state"] == "pending" and step(row, "merge")["wait"] is None


def test_the_step_after_the_last_finished_one_carries_a_growing_wait():
    events = full_run()[:12]  # the check passed at 300 and nothing followed
    row = compute_pr(events, T0 + timedelta(seconds=390))
    merge = step(row, "merge")
    assert merge["state"] == "pending"
    assert merge["wait"] == {
        "from_step": "check",
        "seconds": 90,
        "from": stamp(T0 + timedelta(seconds=300)),
        "to": stamp(T0 + timedelta(seconds=390)),
        "queued_seconds": None,
        "ongoing": True,
        "overlap": False,
    }
    assert row["current_step"] == "merge" and row["longest_wait"]["ongoing"] is True
    # Only the next step waits; the ones behind it do not.
    assert step(row, "deploy_snapshot")["wait"] is None


def test_a_queued_step_that_has_not_started_waits_since_the_previous_step_ended():
    events = [ev("gate", "queued", 100), ev("pr_opened", "pass", 50)]
    row = compute_pr(events, T0 + timedelta(seconds=160))
    gate = step(row, "gate")
    assert gate["state"] == "queued" and gate["duration"] is None
    assert gate["wait"]["seconds"] == 110 and gate["wait"]["ongoing"] is True


def test_a_retry_shows_the_last_run_counts_attempts_and_keeps_the_first_wait():
    events = [
        ev("pr_opened", "pass", 0),
        ev("gate", "queued", 5),
        ev("gate", "running", 10),
        ev("gate", "fail", 70, ref="v1", detail="one finding"),
        ev("gate", "queued", 300),
        ev("gate", "running", 310),
        ev("gate", "pass", 400, ref="v2"),
    ]
    row = compute_pr(events, NOW)
    gate = step(row, "gate")
    assert (gate["state"], gate["attempts"], gate["duration"]) == ("pass", 2, 90)
    assert gate["start"] == stamp(T0 + timedelta(seconds=310))
    assert gate["first_start"] == stamp(T0 + timedelta(seconds=10))
    assert gate["span"] == 390
    assert gate["ref"] == "v2" and gate["detail"] is None  # nothing of the failed run leaks in
    # The retry's gap is not a wait: the wait ends where the first run started.
    assert gate["wait"]["seconds"] == 10


def test_a_failed_last_run_ends_the_pipeline_and_later_steps_stay_pending():
    events = [ev("pr_opened", "pass", 0), ev("gate", "running", 10), ev("gate", "fail", 60)]
    row = compute_pr(events, NOW)
    assert (row["state"], row["current_step"]) == ("failed", "gate")
    assert step(row, "gate")["state"] == "fail"
    assert all(step(row, n)["state"] == "pending" and not step(row, n)["wait"] for n in STEPS[4:])
    assert row["lead"]["in_progress"] is False and row["lead"]["seconds"] == 60
    assert row["lead"]["basis"] == "first_event"  # no card event: the first event starts the clock


def test_a_failed_step_followed_by_later_work_does_not_fail_the_pr():
    events = [
        ev("gate", "running", 10),
        ev("gate", "fail", 60),
        ev("check", "running", 70),
        ev("check", "pass", 90),
    ]
    row = compute_pr(events, T0 + timedelta(seconds=100))
    assert step(row, "gate")["state"] == "fail" and row["state"] == "running"
    assert row["current_step"] == "merge"


def test_missing_steps_are_pending_and_the_wait_skips_them():
    events = [ev("card", "pass", 0), ev("gate", "running", 100), ev("gate", "pass", 130)]
    row = compute_pr(events, NOW)
    for name in ("builder", "pr_opened"):
        assert step(row, name)["present"] is False and step(row, name)["state"] == "pending"
    gate = step(row, "gate")
    assert gate["wait"]["from_step"] == "card" and gate["wait"]["seconds"] == 100


def test_steps_out_of_order_overlap_with_a_zero_wait():
    events = [
        ev("gate", "running", 100),
        ev("gate", "pass", 200),
        ev("check", "running", 150),  # started while the gate was still going
        ev("check", "pass", 260),
    ]
    row = compute_pr(events, NOW)
    check = step(row, "check")
    assert check["wait"]["overlap"] is True and check["wait"]["seconds"] == 0
    # Only the wait for the step that has not come yet is left; the overlap is not a wait.
    assert row["longest_wait"]["before"] == "merge" and row["longest_wait"]["ongoing"]


def test_events_arrive_in_any_order_and_ties_keep_their_recorded_order():
    shuffled = list(reversed(full_run()))
    assert compute_pr(shuffled, NOW)["steps"] == compute_pr(full_run(), NOW)["steps"]
    tie = [ev("gate", "queued", 5, "a", seq=1), ev("gate", "running", 5, "b", seq=2)]
    assert step(compute_pr(tie, NOW), "gate")["state"] == "running"


def test_a_duplicate_event_id_counts_once_and_the_first_wins():
    events = [
        ev("gate", "running", 10, "same"),
        ev("gate", "pass", 60, "same"),  # same id: ignored, the gate is still running
    ]
    row = compute_pr(events, T0 + timedelta(seconds=70))
    gate = step(row, "gate")
    assert gate["state"] == "running" and gate["attempts"] == 1 and gate["events"] == 1


def test_clock_skew_never_gives_negative_numbers():
    # A step that "ended" before it started, and a running step started after now.
    events = [ev("gate", "pass", 0, "x"), ev("gate", "running", 50, "y")]
    row = compute_pr(events, T0 + timedelta(seconds=10))
    gate = step(row, "gate")
    assert gate["duration"] == 0  # running since 50 but now is 10
    row = compute_pr(
        [ev("card", "running", 100), ev("card", "pass", 40)], T0 + timedelta(seconds=10)
    )
    assert step(row, "card")["duration"] == 0 and row["lead"]["seconds"] >= 0


def test_equal_durations_pick_the_earlier_step_and_zero_durations_pick_none():
    events = [
        ev("builder", "running", 0),
        ev("builder", "pass", 60),
        ev("gate", "running", 60),
        ev("gate", "pass", 120),
    ]
    assert compute_pr(events, NOW)["slowest"]["step"] == "builder"
    assert compute_pr([ev("pr_opened", "pass", 0)], NOW)["slowest"] is None


def test_unknown_steps_states_and_bad_times_are_left_out_not_fatal():
    events = [
        ev("gate", "running", 0),
        {"id": "u1", "step": "nope", "state": "pass", "at": stamp(T0)},
        {"id": "u2", "step": "gate", "state": "weird", "at": stamp(T0)},
        {"id": "u3", "step": "gate", "state": "pass", "at": "not a time"},
    ]
    assert step(compute_pr(events, NOW), "gate")["events"] == 1


def test_no_events_is_an_empty_pending_pipeline():
    row = compute_pr([], NOW, repo="a/b", pr=1)
    assert row["state"] == "running" and row["current_step"] == "card"
    assert row["lead"]["seconds"] is None and row["updated_at"] is None
    assert all(not s["present"] for s in row["steps"])


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-11-15T10:00:00Z", datetime(2026, 11, 15, 10, tzinfo=timezone.utc)),
        ("2026-11-15T10:00:00+00:00", datetime(2026, 11, 15, 10, tzinfo=timezone.utc)),
        ("2026-11-15T11:00:00+01:00", datetime(2026, 11, 15, 10, tzinfo=timezone.utc)),
        ("2026-11-15T10:00:00", datetime(2026, 11, 15, 10, tzinfo=timezone.utc)),
    ],
)
def test_times_are_read_as_utc(text, expected):
    assert parse_at(text) == expected


@pytest.mark.parametrize("bad", ["", "yesterday", 5, None, "2026-13-45T00:00:00Z"])
def test_a_bad_time_is_refused(bad):
    with pytest.raises(ValueError):
        parse_at(bad)


# ── The example the empty state and the view tests use ───────────────────
def test_the_example_is_deterministic_and_has_three_kinds_of_pr():
    first, second = demo.prs(NOW), demo.prs(NOW)
    assert first == second and demo.events(NOW) == demo.events(NOW)
    assert demo.prs(NOW + timedelta(seconds=5)) != first  # an open PR keeps counting
    by_number = {row["pr"]: row for row in first}
    assert [row["pr"] for row in first] == [102, 103, 101]  # most recent activity first
    assert by_number[101]["state"] == "done" and by_number[101]["lead"]["in_progress"] is False
    assert by_number[102]["state"] == "running" and step(by_number[102], "gate")["attempts"] == 2
    assert step(by_number[102], "gate")["state"] == "pass"
    assert by_number[103]["current_step"] == "builder"


# ── The card step comes from the board ──────────────────────────────────
def feeder_events():
    """What the deploy script sends: no card events."""
    return [e for e in full_run() if e["step"] != "card"]


def card(claimed=-300, linked=None, number=5):
    return {
        "number": number,
        "claimed_at": stamp(T0 + timedelta(seconds=claimed)),
        "linked_at": None if linked is None else stamp(T0 + timedelta(seconds=linked)),
    }


def test_a_claimed_card_runs_from_its_claim_until_the_pr_opens():
    row = compute_pr(feeder_events(), NOW, card=card(claimed=-300))
    box = step(row, "card")
    assert box["state"] == "pass" and box["ref"] == "#/card/5" and box["detail"] is None
    assert box["start"] == stamp(T0 + timedelta(seconds=-300))
    assert box["end"] == stamp(T0 + timedelta(seconds=110))  # the pr_opened event
    assert box["duration"] == 410
    # The lead time starts at the claim, not at the first event.
    assert row["lead"]["basis"] == "card" and row["lead"]["seconds"] == 800
    # The builder waited for the claim, not for the card's end (the card spans the build).
    assert step(row, "builder")["wait"]["from_step"] == "card"
    assert step(row, "builder")["wait"]["seconds"] == 320
    # The card spans the builder's work, so it is not the slowest step.
    assert row["slowest"]["step"] == "builder"


def test_a_card_claimed_after_the_pr_opened_is_a_zero_length_card_at_the_pr():
    row = compute_pr(feeder_events(), NOW, card=card(claimed=5000))
    box = step(row, "card")
    assert box["start"] == box["end"] == stamp(T0 + timedelta(seconds=110))
    assert box["duration"] == 0 and box["detail"] == "Card linked after the PR opened"
    assert row["lead"]["from"] == stamp(T0 + timedelta(seconds=110))


def test_without_a_pr_opened_event_the_card_passes_when_its_pr_was_linked():
    events = [e for e in feeder_events() if e["step"] != "pr_opened"]
    row = compute_pr(events, NOW, card=card(claimed=-100, linked=50))
    box = step(row, "card")
    assert box["state"] == "pass" and box["end"] == stamp(T0 + timedelta(seconds=50))


def test_without_a_pr_opened_event_or_a_link_time_the_card_is_still_running():
    events = [e for e in feeder_events() if e["step"] != "pr_opened"]
    row = compute_pr(events, NOW, card=card(claimed=-100))
    box = step(row, "card")
    assert box["state"] == "running" and box["end"] is None and box["duration"] == 3 * 3600 + 100


def test_a_pr_with_no_linked_card_starts_at_its_first_event_and_says_no_card():
    row = compute_pr(feeder_events(), NOW, card=None)
    box = step(row, "card")
    assert (box["state"], box["detail"], box["ref"]) == ("skipped", "No card", None)
    assert box["start"] == box["end"] == stamp(T0 + timedelta(seconds=12))
    assert row["lead"]["basis"] == "first_event" and row["lead"]["from"] == box["start"]
    assert row["lead"]["seconds"] == 488  # first event (12) to the healthy deploy (500)
    assert step(row, "builder")["wait"]["seconds"] == 8


def test_recorded_card_events_are_replaced_when_the_board_answers_and_kept_otherwise():
    assert step(compute_pr(full_run(), NOW), "card")["events"] == 2  # no board: as recorded
    row = compute_pr(full_run(), NOW, card=card(claimed=-300))
    assert step(row, "card")["start"] == stamp(T0 + timedelta(seconds=-300))
    assert compute_pr(full_run(), NOW, card=None)["steps"][0]["detail"] == "No card"


def test_the_view_fixture_is_the_example_at_a_fixed_time():
    """The view tests read tests/pipeline_fixture.json; this keeps it the server's own answer.
    To refresh it after changing the example or the numbers, write the JSON shown by
    ``fixture_text()`` to that file."""
    from pathlib import Path

    assert Path(__file__).with_name("pipeline_fixture.json").read_text() == fixture_text()


def fixture_text():
    import json

    now = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
    return (
        json.dumps(
            {"now": "2026-11-15T12:00:00Z", "demo": True, "prs": demo.prs(now)},
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )
