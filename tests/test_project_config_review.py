"""Coworker config review controls and gateway authority."""

import shutil
import subprocess
from pathlib import Path

import pytest

from commonplace import sessions
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_surfaces import BOARD, env as env


@pytest.mark.parametrize(
    "case",
    [
        "reject",
        "cancel",
        "member",
        "failure",
        "busy",
        "stale",
        "escape",
        "first",
        "refresh_failure",
        "deck_names",
        "deck_unknown",
        "deck_wrap",
        "deck_escape",
        "classic_bytes",
    ],
)
def test_config_review_controls(case):
    result = subprocess.run(
        [shutil.which("node"), str(Path(__file__).with_name("project_config_review.mjs")), case],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_owner_rejection_is_final_and_audited(env):
    version, digest = env.propose(prompt="A proposed change.")
    path = f"boards/{BOARD}/configs/{version}"
    status, result = env.person("POST", path + "/reject", {})
    assert status == 200, result
    assert result["status"] == "rejected"
    assert result["version"] == version and result["decided_by"] == env.owner.actor
    assert result["decided_at"]
    status, result = env.person("POST", path + "/accept", {"sha256": digest})
    assert status == 409 and result["code"] == "not_proposed", result
    status, configs = env.person("GET", f"boards/{BOARD}/configs")
    assert status == 200 and configs["accepted"]["version"] != version
    rows = [r for r in env.hub.db.audit_entries(env.ws) if r["tool"] == "orch_config_reject"]
    assert len(rows) == 1
    assert rows[0]["decision"] == "ok" and rows[0]["user_id"] == env.owner.user_id


@pytest.mark.parametrize("action", ["accept", "reject"])
def test_member_can_read_but_cannot_decide_config(env, action):
    version, digest = env.propose(prompt="A member reviews this change.")
    email = "config-reader@example.test"
    user = env.hub.db.create_user(email)
    env.hub.db.add_member(env.ws, user, "member", actor=env.hub.admin)
    link = sessions.create_login_link(env.hub.db, email, env.ws)
    cookie = f"{sessions.COOKIE}={sessions.redeem_login_link(env.hub.db, link)}"
    path = f"boards/{BOARD}/configs/{version}"
    status, config = env.rest("GET", path, cookie=cookie)
    assert status == 200 and config["version"]["status"] == "proposed"
    status, result = env.rest(
        "POST",
        path + "/" + action,
        {"sha256": digest} if action == "accept" else {},
        cookie=cookie,
    )
    assert status == 403 and result["code"] == "owner_only", result
    status, config = env.person("GET", path)
    assert status == 200 and config["version"]["status"] == "proposed"
