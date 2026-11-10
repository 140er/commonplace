"""Task-less execution approval through the real REST route and authority store."""

import pytest

from commonplace.compute import control
from tests.test_compute_api import API
from tests.test_compute_control import compute_env as compute_env

pytestmark = pytest.mark.usefixtures("compute_private_filesystem")


def _proposal(api, task_number):
    _, node = api.enrolled()
    status, result = api.propose(node, task_number=task_number)
    assert status == 201 and result["execution"]["state"] == "awaiting_approval"
    assert result["execution"]["task_number"] == task_number
    return result["execution"]


def _approve(api, proposal):
    return api.request(
        "POST",
        f"/executions/{proposal['id']}/approve",
        {"expected_digest": proposal["digest"], "expires_at": api.env.clock.now + 60},
    )


def test_taskless_proposal_approves_through_real_route(compute_env):
    api = API(compute_env)
    proposal = _proposal(api, None)
    status, result = _approve(api, proposal)
    assert status == 200 and result["execution"]["state"] == "approved"
    assert result["execution"]["task_number"] is None
    assert result["execution"]["digest"] == proposal["digest"]


@pytest.mark.parametrize("change", ["gains", "loses"])
def test_approval_denies_binding_that_gains_or_loses_task(compute_env, monkeypatch, change):
    env, api = compute_env, API(compute_env)
    proposal = _proposal(api, None if change == "gains" else env.task)
    original = control.Store._binding

    def changed(self, *args, **kwargs):
        binding = original(self, *args, **kwargs)
        if change == "gains":
            binding["authority"] = self._authority(env.principal, "compute", env.task)
        else:
            binding["authority"].pop("task")
        return binding

    # Alter only the fresh projection; stored consent and its digest stay intact.
    monkeypatch.setattr(control.Store, "_binding", changed)
    assert _approve(api, proposal) == (409, {"error": "conflict"})
    assert api.request(path="/executions")[1]["executions"][0]["state"] == "awaiting_approval"


@pytest.mark.parametrize("fresh_has_task", [False, True])
def test_absent_task_does_not_compare_equal_to_empty_task(fresh_has_task):
    absent = {"authority": {"board": "compute"}}
    present = {"authority": {"board": "compute", "task": {}}}
    fresh, stored = (present, absent) if fresh_has_task else (absent, present)
    assert not control._same_binding(fresh, stored)
