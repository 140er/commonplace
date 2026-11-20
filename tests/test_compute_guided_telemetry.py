"""Authoritative guided setup survives absent or rotated connection telemetry."""

import json

import pytest

from commonplace.compute import pairing, routes
from commonplace.runner import jobs, registry
from tests.compute_overview_helpers import Fleet
from tests.test_compute_api import API
from tests.test_compute_control import compute_env  # noqa: F401 -- real isolated stores
from tests.test_compute_overview import by_name, read


@pytest.fixture
def api(request, monkeypatch):
    env = request.getfixturevalue("compute_env")
    monkeypatch.setattr(routes, "open_store", lambda gateway, workspace_id: env.store)
    return API(env)


@pytest.fixture
def fleet(api):
    return Fleet(api.env)


def checked(fleet, *, rotate=False):
    made = fleet.enroll("checked-guided")
    fleet.guided(made, "connected", accepted=True)
    with fleet.env.db._lock:
        for step in pairing.STEPS:
            fleet.env.db._conn.execute(
                "INSERT OR REPLACE INTO machine_pairing_steps(pairing_id,step,state,reported_at) "
                "VALUES (?,?,?,?)",
                ("pair_" + made["machine_id"], step, "ok", "2026-11-20T12:00:00Z"),
            )
    if rotate:
        fleet.seen(made, 5)
        registry.rotate(fleet.env.db, fleet.env.principal, made["machine_id"], kind="machine")
    assert made["machine_id"] in pairing.setup_only_ids(fleet.env.db, fleet.env.workspace)
    return made


@pytest.mark.parametrize("rotate", [False, True], ids=["no-contact", "rotated-credential"])
@pytest.mark.parametrize("surface", ["list", "detail"])
def test_checked_guided_state_is_authoritative_without_connection_telemetry(
    api, fleet, rotate, surface
):
    made = checked(fleet, rotate=rotate)
    if surface == "list":
        item = by_name(read(api))["checked-guided"]
    else:
        status, result = api.request(path=f"/overview/runner/{made['machine_id']}")
        assert status == 200, result
        item = result["item"]
    assert (item["status"]["code"], item["status"]["label"]) == (
        "set_up",
        pairing.SET_UP_LABEL,
    )
    assert "Coworkers do not run on it in this version" in item["status"]["detail"]
    assert item["intake"]["reasons"] == ["setup_only"]
    assert item["intake"]["allowed"] == "No: this setup only checks the machine"
    assert "Not used in this version" in item["usage"]["runs"]["text"]
    assert item["usage"]["resources"]["reason"] == "not_used"
    assert "tokens" not in json.dumps(item["status"]).lower()
    assert "revoke and enrol" not in json.dumps(item["status"]).lower()
    if surface == "detail":
        assert not {"Runs at once", "Starts per hour"}.intersection(
            fact["label"] for fact in item["facts"]
        )
    machine = registry.get_machine(api.env.db, api.env.workspace, made["machine_id"])
    fleet.job(made, "queued", task=63901, binding_epoch=machine["binding_epoch"])
    assert jobs.lease(fleet.store(), machine) is None, (
        "checked setup must remain unable to run work"
    )


def test_disabled_guided_machine_keeps_disabled_state_but_has_no_usable_capacity(api, fleet):
    made = checked(fleet)
    fleet.disable(made)
    status, result = api.request(path=f"/overview/runner/{made['machine_id']}")
    assert status == 200, result
    item = result["item"]
    assert item["status"]["code"] == "disabled", "explicit owner disable remains authoritative"
    assert item["intake"]["reasons"] == ["disabled", "setup_only"]
    assert "Not used in this version" in item["usage"]["runs"]["text"]
    assert not {"Runs at once", "Starts per hour"}.intersection(
        fact["label"] for fact in item["facts"]
    )


def test_developer_without_contact_keeps_its_developer_status_and_capacity(api, fleet):
    made = fleet.enroll("developer-no-contact")
    item = by_name(read(api))["developer-no-contact"]
    assert made["machine_id"] not in pairing.setup_only_ids(api.env.db, api.env.workspace)
    assert item["status"]["code"] == "never_connected"
    assert "tokens" in item["status"]["next"]
    assert "Not used in this version" not in item["usage"]["runs"]["text"]


def test_disabled_developer_keeps_recorded_resources_and_usage(api, fleet):
    made = fleet.enroll("developer-history")
    fleet.seen(made, 5)
    fleet.resources(made)
    fleet.disable(made)
    status, result = api.request(path=f"/overview/runner/{made['machine_id']}")
    assert status == 200, result
    item = result["item"]
    assert item["status"]["code"] == "disabled"
    assert item["usage"]["resources"]["known"] is True
    assert "Not used in this version" not in item["usage"]["summary"]
    assert {"Runs at once", "Starts per hour"}.issubset(fact["label"] for fact in item["facts"])
