"""Workspace bootstrap keeps the owner queue optional without granting authority."""

import subprocess
from pathlib import Path

import pytest

from commonplace import sessions
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_boards import api

HARNESS = Path(__file__).with_name("member_bootstrap.mjs")


@pytest.mark.parametrize("look", ["deck", "classic"])
@pytest.mark.parametrize(
    "role,failure",
    [
        ("member", "success"),
        ("member", "boards"),
        ("owner", "success"),
        ("owner", "401"),
        ("owner", "403"),
        ("owner", "503"),
    ],
)
def test_workspace_bootstrap(role, failure, look):
    result = subprocess.run(
        ["node", str(HARNESS), role, failure, look],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_member_cannot_read_owner_queue(hub):
    user = hub.db.create_user("member@example.test")
    hub.db.add_member(hub.workspace, user, "member", actor=hub.admin)
    session = sessions.redeem_login_link(
        hub.db,
        sessions.create_login_link(hub.db, "member@example.test", hub.workspace),
    )
    cookie = {"Cookie": f"{sessions.COOKIE}={session}"}
    assert api(hub, cookie, "GET", "/api/me")[1]["can_admin"] is False
    status, payload = api(hub, cookie, "GET", "/api/needs-me")
    assert status == 403
    assert "owner" in payload["error"]
    assert api(hub, cookie, "GET", "/api/boards")[0] == 200
