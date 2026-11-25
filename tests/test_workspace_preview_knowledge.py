"""Effect previews describe measured knowledge without changing lifecycle permissions."""

import pytest

from commonplace.compute import workspace_lifecycle as lifecycle
from tests.compute_overview_helpers import FakeExecutor
from tests.test_compute_control import compute_env as compute_env
from tests.test_compute_workspace_lifecycle import api as api, ask, conflict, item, make


def prepared(api, state):
    workspace = make(api)
    if state == "new":
        return workspace
    if state == "legacy":
        with api.env.store._journal() as connection:
            api.env.store._set_workspace_state(
                connection, workspace["id"], "ready", applied_revision=1
            )
        return workspace
    executor = FakeExecutor(api.env, register=False)
    assert ask(api, workspace, "start")[0] == 202
    claimed = executor.claim()
    assert claimed is not None
    if "running" in state or state in ("stopped", "withdraw_stop"):
        executor.run(claimed, "running")
    if state in ("stopped", "withdraw_stop", "fresh_stopping"):
        if state == "fresh_stopping":
            executor.run(claimed, "running")
        assert ask(api, workspace, "stop")[0] == 202
        claimed = executor.claim()
        assert claimed is not None
    if state == "stopped":
        executor.run(claimed, "stopped")
    if state.startswith("stale_") or state.startswith("withdraw_"):
        api.env.clock.now += lifecycle.REPORT_STALE_S + 5
    if state.startswith("withdraw_"):
        status, result = ask(api, workspace, "withdraw")
        assert status == 200 and result["item"]["status"]["code"] == "failed"
    return workspace


def preview(api, workspace):
    status, result = api.request(path=f"/overview/workspace/{workspace['id']}/preview/start")
    assert status == 200
    assert result["executor"] == {"available": False}
    assert "Nothing is started, stopped or created" in " ".join(result["effect"])
    return result


@pytest.mark.parametrize("state", ["legacy", "stale_running", "stale_starting"])
def test_unknown_start_preview_does_not_claim_known_running_or_empty_work(api, state):
    workspace = prepared(api, state)
    before = item(api, workspace)
    assert before["status"]["code"] == "unknown"
    expected_reason = "request_pending" if state == "stale_starting" else "already_running"
    result = preview(api, workspace)
    assert result["allowed"] is False
    assert result["refusal"]["reason"] == expected_reason
    text = result["running_work"].lower()
    assert "does not know" in text and "running" in text
    assert "does not stop existing work" in text
    if expected_reason == "already_running":
        assert "state is not known" in result["refusal"]["text"]
        start = next(action for action in before["actions"] if action["action"] == "start")
        assert start["reason"] == expected_reason
        assert start["refusal"] == result["refusal"]["text"]
    assert item(api, workspace) == before
    requested = ask(api, workspace, "start")
    if expected_reason == "request_pending":
        status, repeated = requested
        assert status == 200 and repeated["repeated"] is True
        assert repeated["request"]["state"] == "in_progress"
        assert item(api, workspace) == before
    else:
        conflict(requested, expected_reason)


@pytest.mark.parametrize("state", ["withdraw_start", "withdraw_stop"])
def test_unreachable_claim_withdrawal_keeps_start_effect_uncertainty(api, state):
    workspace = prepared(api, state)
    before = item(api, workspace)
    result = preview(api, workspace)
    assert result["allowed"] is True and result["refusal"] is None
    text = result["running_work"].lower()
    assert "does not know" in text and "running" in text
    assert "does not stop existing work" in text
    assert item(api, workspace) == before
    status, requested = ask(api, workspace, "start")
    assert status == 202 and requested["request"]["state"] == "requested"
    assert requested["item"]["status"]["code"] == "start_requested"


@pytest.mark.parametrize("state", ["fresh_running", "fresh_starting", "fresh_stopping"])
def test_refused_start_preview_does_not_describe_active_phase_as_empty(api, state):
    workspace = prepared(api, state)
    before = item(api, workspace)
    assert before["status"]["code"] == state.removeprefix("fresh_")
    result = preview(api, workspace)
    assert result["allowed"] is False
    assert "may already be running" in result["running_work"]
    assert "does not stop existing work" in result["running_work"]
    assert item(api, workspace) == before


@pytest.mark.parametrize("state", ["new", "stopped"])
def test_known_quiet_start_preview_retains_the_plain_effect_control(api, state):
    workspace = prepared(api, state)
    before = item(api, workspace)
    result = preview(api, workspace)
    assert result["allowed"] is True
    assert result["running_work"].startswith("Nothing is running in it now")
    assert item(api, workspace) == before
