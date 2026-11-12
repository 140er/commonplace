"""A wing grant by another workspace's owner is refused before any wing name is checked."""

import pytest

from tests.runner_harness import (
    owners,  # noqa: F401
    signup_hub,  # noqa: F401
)
from tests.test_runner_owner import edit, enroll, listing

KNOWN = "shared"  # exists in every signed-up workspace
UNKNOWN = "no-such-wing"


@pytest.mark.parametrize("wings", [[KNOWN], [UNKNOWN], [KNOWN, UNKNOWN]])
def test_stranger_gets_one_refusal_whatever_the_wing_names(signup_hub, owners, wings):  # noqa: F811
    hub, owner, stranger = signup_hub, owners["A"], owners["B"]
    machine = enroll(hub, owner)
    before = listing(hub, owner)
    status, body = edit(hub, stranger, machine, body={"wings": wings})
    reference = edit(hub, stranger, machine, body={"wings": [KNOWN]})
    assert (status, body) == reference == (404, {"error": "machine not found"})
    assert listing(hub, owner) == before


def test_unknown_machine_gets_the_same_refusal_as_a_stranger_machine(signup_hub, owners):  # noqa: F811
    hub, owner, stranger = signup_hub, owners["A"], owners["B"]
    machine = enroll(hub, owner)
    ghost = {"machine_id": "mach_" + "0" * 16}
    for wings in ([KNOWN], [UNKNOWN]):
        assert edit(hub, stranger, ghost, body={"wings": wings}) == edit(
            hub, stranger, machine, body={"wings": wings}
        )


def test_owner_still_gets_wing_validation_on_own_machine(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    machine = enroll(hub, owner)
    status, body = edit(hub, owner, machine, body={"wings": [UNKNOWN]})
    assert status == 400, (status, body)
    status, body = edit(hub, owner, machine, body={"wings": [KNOWN]})
    assert status == 200, (status, body)
