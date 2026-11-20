import json

import pytest

from commonplace.projects import model_modes
from commonplace.runner import registry
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_runner import PROVIDER, World, http
from tests.test_projects_escalation import set_rule


@pytest.mark.parametrize("different_provider", [False, True])
def test_personal_escalation_keeps_the_final_providers_authority(hub, different_provider):
    w = World(hub)
    model_modes.put(w.db, w.owner, {"mode": "personal"})
    first = model_modes.add_connection(
        w.db,
        w.owner,
        {"provider": PROVIDER, "label": "First personal plan", "machine": w.machine_id},
    )
    if different_provider:
        other = registry.enroll(
            w.db,
            w.owner,
            {
                "label": "runner-two",
                "projects": ["demo"],
                "topics": ["worker"],
                "wings": ["shared"],
                "requesters": [w.a.connection_id, w.b.connection_id],
            },
        )
        registry.attest(
            w.db,
            w.owner,
            other["machine_id"],
            {
                "topics": ["worker"],
                "provider_instance_id": "worker-two",
                "worker_connection_id": other["worker_connection_id"],
            },
        )
        provider = "example-provider-two"
        final = model_modes.add_connection(
            w.db,
            w.owner,
            {"provider": provider, "label": "Second personal plan", "machine": other["machine_id"]},
        )
    else:
        provider = PROVIDER
        final = first
        other = None
    task = w.card(labels=["security"])
    w.configure()
    set_rule(w, model={"provider": provider, "model": "example-strong"})
    status, reply = w.person_start(task)
    assert status == 201, reply
    [before] = w.jobs_for(task)
    status_a, _ = w.machine_call("lease")
    status_b = None
    if other is not None:
        status_b, _ = http(
            w.hub.port, "POST", "/api/runner/lease", token=other["machine_token"], body={}
        )
    [after] = w.jobs_for(task)
    fields = ["machine_id", "model_provider", "model", "account_kind", "account_label", "state"]
    print(
        json.dumps(
            {
                "different_provider": different_provider,
                "admission": status,
                "before": {k: before.get(k) for k in fields},
                "lease_first": status_a,
                "lease_second": status_b,
                "after": {k: after.get(k) for k in fields},
            }
        )
    )
    assert before["model_provider"] == provider
    assert before["account_connection_id"] == final["id"], (
        "the admitted account must belong to the final selected provider"
    )
    assert before["machine_id"] == final["machine"]
    assert (status_b if different_provider else status_a) == 200
