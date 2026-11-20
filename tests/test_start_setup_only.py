"""A machine the setup guide added only checks itself in this version: no start is ever offered, requested or leased for
it, and the refusal says what is true and where the next step is (the machine of the world is enrolled and attested)."""

import pytest

from commonplace import control_db
from commonplace.compute import pairing
from commonplace.runner import enrollment, predicate, setup_approvals
from tests import test_card_start_defaults as base
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_runner import BOARD, PLAIN

world, owner, options = (
    base.world,
    base.owner,
    base.options,
)  # the fixtures and helper of the card-start suite


def guide(world, *, accepted=True, heartbeat=False):
    """Make the world's machine the kind the setup guide adds: an exchanged enrollment and a pairing for it."""
    db = world.db
    enrollment.ensure(db)
    pairing.ensure(db)
    stamp = control_db._iso(control_db._now())
    eid, pid = "enr_" + world.machine_id, "pair_" + world.machine_id
    with db._lock:
        db._conn.execute(
            "INSERT INTO runner_enrollments(id, workspace_id, machine_label, expires_at, consumed_at, owner_id, "
            "request_id, manifest_digest, settings_version, intent, machine_id, created_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                eid,
                world.ws,
                "runner-one",
                stamp,
                stamp,
                "owner",
                "r-" + eid,
                "a" * 64,
                1,
                "{}",
                world.machine_id,
                stamp,
            ),
        )
        db._conn.execute(
            "INSERT INTO machine_pairings(id, workspace_id, kind, label, owner_id, request_id, request_hash, "
            "enrollment_id, version, accepted_by, accepted_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                pid,
                world.ws,
                "runner",
                "runner-one",
                "owner",
                "r-" + pid,
                "h",
                eid,
                1,
                "owner" if accepted else None,
                stamp if accepted else None,
                stamp,
                stamp,
            ),
        )
        if heartbeat:
            db._conn.execute(
                "INSERT INTO machine_pairing_steps(pairing_id, step, state, cause, reported_at) VALUES (?,?,?,?,?)",
                (pid, "heartbeat", "ok", None, stamp),
            )


@pytest.mark.parametrize("configured", [False, True])
def test_a_setup_only_machine_is_never_offered_requested_or_leased(world, owner, configured):
    card = world.card(board=BOARD if configured else PLAIN)
    if configured:
        world.configure()
    guide(world)
    status, body = options(owner, card)
    assert status == 200, body
    assert body["refusal"] == "setup_only"
    assert not body.get("machines"), "no machine is offered to start on"
    status, refused = owner.call("POST", "/api/card-starts", {"task": card})
    assert status >= 400 and world.all_jobs() == [], (status, refused)
    assert predicate.setup_only_refusal(world.gw, world.machine()) == "setup_only"


def test_the_same_machine_with_a_heartbeat_is_an_ordinary_runner(world, owner):
    card = world.card(board=PLAIN)
    guide(world, heartbeat=True)
    status, body = options(owner, card)
    assert status == 200 and body["refusal"] is None
    assert [m["id"] for m in body["machines"]] == [world.machine_id]


def test_a_machine_waiting_for_approval_and_one_with_its_confirm_card_up_say_where_to_go(
    world,
    owner,
    monkeypatch,
):
    card = world.card(board=PLAIN)
    guide(world, accepted=False)
    assert options(owner, card)[1]["refusal"] == "setup_only"
    monkeypatch.setattr(setup_approvals, "confirm_pending", lambda db, machine: True)
    assert options(owner, card)[1]["refusal"] == "setup_only_confirm"
