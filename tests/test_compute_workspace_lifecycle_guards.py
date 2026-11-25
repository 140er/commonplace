"""Lifecycle refusals keep unknown workspaces, authority and the audit trail intact."""

import json

import pytest

from commonplace import sessions
from commonplace.compute import routes
from commonplace.compute.control import StoreError
from tests.test_compute_api import Request
from tests.test_compute_control import compute_env  # noqa: F401 -- shared real-store fixture
from tests.test_compute_workspace_lifecycle import (  # noqa: F401 -- shared real-store fixture
    api,
    ask,
    conflict,
    item,
    make,
)


def audit_actions(api, workspace_id):  # noqa: F811 -- imported fixture
    with api.env.store._journal() as conn:
        return [
            row["action"]
            for row in conn.execute(
                "SELECT action FROM audit WHERE object_id=? ORDER BY id", (workspace_id,)
            )
        ]


def test_a_legacy_ready_workspace_without_a_report_cannot_be_restarted(api):  # noqa: F811
    workspace = make(api)
    with api.env.store._journal() as conn:
        api.env.store._set_workspace_state(conn, workspace["id"], "ready", applied_revision=1)
    before = item(api, workspace)
    assert before["status"]["code"] == "unknown"
    actions_before = audit_actions(api, workspace["id"])

    # Check the request itself before the preview: a refused restart must not leave a request or audit line.
    conflict(ask(api, workspace, "restart"), "state_unknown")
    after = item(api, workspace)
    assert after["request"] is None and after["basis"] == before["basis"]
    assert audit_actions(api, workspace["id"]) == actions_before
    actions = {action["action"]: action for action in after["actions"]}
    assert actions["restart"]["allowed"] is False
    assert actions["restart"]["reason"] == "state_unknown"
    status, preview = api.request(path=f"/overview/workspace/{workspace['id']}/preview/restart")
    assert status == 200 and preview["allowed"] is False
    assert preview["refusal"]["reason"] == "state_unknown"

    # Stopping an unknown workspace remains possible; the endpoint records only the owner's request.
    status, result = ask(api, workspace, "stop")
    assert status == 202 and result["request"]["state"] == "requested"
    assert result["item"]["status"]["code"] == "stop_requested"
    assert audit_actions(api, workspace["id"]) == actions_before + ["workspace.stop_requested"]


@pytest.mark.parametrize("action", ["start", "stop", "restart", "withdraw", "drain", "resume"])
@pytest.mark.parametrize("caller", ["member", "agent_with_owner_cookie"])
def test_every_control_refuses_nonowners_before_reading_a_body(api, action, caller):  # noqa: F811
    workspace = make(api)
    if caller == "member":
        cookie = sessions.start_session(api.env.db, api.env.member, api.env.workspace)
        authorization = []
    else:
        cookie = api.cookie
        _, token = api.env.db.create_token(
            workspace_ref=api.env.workspace,
            user_ref=api.env.owner,
            name="lifecycle-agent",
            client="tests",
            policy=sessions.person_policy(),
            actor=api.env.owner,
        )
        authorization = [("Authorization", "Bearer " + token)]
    kind, target = (
        ("runner", "missing-runner")
        if action in ("drain", "resume")
        else (
            "workspace",
            workspace["id"],
        )
    )
    request = Request(
        "POST",
        f"/api/compute/overview/{kind}/{target}/{action}",
        [
            ("Host", "app.test"),
            ("Origin", "http://app.test"),
            ("Content-Type", "application/json"),
            ("Content-Length", "1"),
            ("Cookie", f"{sessions.COOKIE}={cookie}"),
            *authorization,
        ],
        b"{",
    )

    class UnreadableBody:
        def read(self, *args):
            raise AssertionError("The body was read before authority was checked")

    request.rfile = UnreadableBody()
    before = audit_actions(api, workspace["id"])
    routes.handle(api.env.gateway, request)
    assert request.status == 403
    assert json.loads(request.wfile.getvalue()) == {"error": "owner_required", "reason": "member"}
    assert item(api, workspace)["request"] is None
    assert audit_actions(api, workspace["id"]) == before


@pytest.mark.parametrize("action", ["start", "withdraw"])
def test_a_failed_workspace_audit_rolls_back_the_request(api, monkeypatch, action):  # noqa: F811
    workspace = make(api)
    if action == "withdraw":
        assert ask(api, workspace, "start")[0] == 202
    before = item(api, workspace)
    actions_before = audit_actions(api, workspace["id"])

    def unavailable(*args):
        raise StoreError("authority_unavailable", 503, reason="audit")

    monkeypatch.setattr(api.env.store, "_audit", unavailable)
    assert ask(api, workspace, action) == (
        503,
        {"error": "authority_unavailable", "reason": "audit"},
    )
    after = item(api, workspace)
    assert after["request"] == before["request"] and after["basis"] == before["basis"]
    assert audit_actions(api, workspace["id"]) == actions_before
