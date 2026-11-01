"""Rules: browser rendering, clarify prompts and owner request contracts."""

import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from commonplace import control_db, sessions
from commonplace.boards import BoardService
from commonplace.rules import render
from tests import test_commonplace_rules as rules_tests
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_boards import api

env_fixture = pytest.fixture(name="env")(rules_tests.env.__wrapped__)


def test_rules_render_and_requests():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed for the Rules render check")
    script = Path(__file__).with_name("commonplace_rules_render.mjs")
    subprocess.run([node, str(script)], check=True, timeout=10)


def test_rules_rest_projects_unchecked_audits_by_workspace_and_connection(hub, env, monkeypatch):
    other_workspace = hub.db.create_workspace("other", hub.admin)
    other_token = hub.token("agent", workspace=other_workspace, scopes=["memory:read"])
    other = hub.gateway.authenticate(f"Bearer {other_token}")
    recent = datetime.now(timezone.utc) - timedelta(minutes=1)
    monkeypatch.setattr(control_db, "_now", lambda: recent)

    def audit(reason="rules_seq unchecked mcp", **kwargs):
        hub.db.audit(
            action="tool.call",
            decision="ok",
            **dict(
                workspace_id=env.agent.workspace_id,
                connection_id=env.agent.connection_id,
                reason=reason,
                target_ids=["conn:someone-else"],
            )
            | kwargs,
        )

    for _ in range(105):
        audit()
    audit("rules_seq unchecked (warn) rest")
    audit("rules_seq unchecked rest")
    for reason in ("rules_seq stale mcp", "rules_seq too_large rest", "unrelated unchecked"):
        audit(reason)
    audit(connection_id=None)
    audit(connection_id=env.agent.actor)
    audit(connection_id=other.connection_id)
    audit(workspace_id=other_workspace)
    audit(workspace_id=other_workspace, connection_id=other.connection_id)
    monkeypatch.setattr(control_db, "_now", lambda: recent - timedelta(days=2))
    audit()
    monkeypatch.setattr(control_db, "_now", lambda: recent + timedelta(days=2))
    audit()
    status, listing = api(hub, env.cookie, "GET", "/api/rules")
    assert status == 200
    assert listing["unchecked_by_agent"] == {env.agent.actor: 107}
    assert listing["too_large_by_agent"] == {}
    assert "someone-else" not in str(listing)
    assert api(hub, env.token, "GET", "/api/rules")[0] == 403


def test_rules_rest_projects_only_current_too_large_state(hub, env):
    rows = [env.owner.create("*", str(i) + "é" * 2400, "limit") for i in range(7)]
    assert env.worker.rules_get(effective=True)["state"] == "too_large"

    def listing():
        status, result = api(hub, env.cookie, "GET", "/api/rules")
        assert status == 200
        return result

    assert listing()["too_large_by_agent"] == {env.agent.actor: True}
    env.owner.create("conn:unrelated", "A separate rule", "limit")
    assert listing()["too_large_by_agent"] == {env.agent.actor: True}
    for row in rows[1:]:
        env.owner.action(row["id"], row["version"], "retire")
    assert listing()["too_large_by_agent"] == {}
    assert env.worker.rules_get(effective=True)["state"] == "served"
    assert listing()["too_large_by_agent"] == {}
    for row in rows[1:]:
        env.owner.action(row["id"], row["version"] + 1, "restore")
    assert env.worker.rules_get(effective=True)["state"] == "too_large"
    # Reconcile a board visibility change before comparing served versions.
    env.owner.create("*", "Scoped rule", "limit", "board", "work")
    assert env.worker.rules_get(effective=True)["state"] == "too_large"
    BoardService(env.owner.ctx).board_add(slug="work", wing="shared")
    assert listing()["too_large_by_agent"] == {}


def test_rules_rest_marks_legacy_oversized_text_without_changing_it(hub, env):
    row = env.owner.create("*", "Legacy rule", "limit")
    legacy_text = "<" * 1200
    assert render.length(render.quote(legacy_text)) > render.RULE_BYTES
    with env.owner.store.transaction() as conn:
        conn.execute("UPDATE rules SET text=? WHERE id=?", (legacy_text, row["id"]))
    status, listing = api(hub, env.cookie, "GET", "/api/rules")
    assert status == 200
    assert listing["oversized_rule_ids"] == [row["id"]]
    assert listing["rules"][0]["text"] == legacy_text


def test_rules_rest_controls_and_lost_store_recovery(hub, env):
    env.owner.create("*", "Keep this rule", "limit")
    for agent, modes in (
        ("*", ("off", "warn", "enforce")),
        (env.agent.actor, ("off", "warn", "enforce", "default")),
    ):
        for mode in modes:
            status, result = api(
                hub, env.cookie, "PATCH", "/api/rules/checks", {"agent": agent, "mode": mode}
            )
            assert status == 200
            if mode == "default":
                assert agent not in result["checks"]
            else:
                assert result["checks"][agent] == mode
    member_id = hub.db.create_user("member@example.test")
    hub.db.add_member(hub.workspace, member_id, role="member", actor=hub.admin)
    session = sessions.redeem_login_link(
        hub.db, sessions.create_login_link(hub.db, "member@example.test", hub.workspace)
    )
    member_cookie = {"Cookie": f"{sessions.COOKIE}={session}"}
    for auth in (env.token, member_cookie):
        assert api(hub, auth, "PATCH", "/api/rules/checks", {"agent": "*", "mode": "off"})[0] == 403
        assert api(hub, auth, "POST", "/api/rules/store/accept", {})[0] == 403
    path = Path(env.owner.store.path)
    path.unlink()
    for auth, can_edit in ((env.cookie, True), (member_cookie, False)):
        status, result = api(hub, auth, "GET", "/api/rules")
        assert status == 409 and result["store_lost"] is True
        assert result["can_edit"] is can_edit
        assert "rules" not in result
        assert not path.exists()
    # The recovery and check routes remain usable while all other rule routes are guarded.
    assert (
        api(hub, env.cookie, "PATCH", "/api/rules/checks", {"agent": "*", "mode": "warn"})[0] == 200
    )
    assert api(hub, env.cookie, "POST", "/api/rules/store/accept", {}) == (200, {"accepted": True})
    status, listing = api(hub, env.cookie, "GET", "/api/rules")
    assert status == 200 and listing["rules"] == []
    assert listing["checks"] == {"*": "warn"}
