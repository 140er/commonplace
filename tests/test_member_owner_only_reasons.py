"""A member is told why an owner-only control is missing, and the server still refuses the write."""

import shutil
import subprocess
from pathlib import Path

from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._projects_helpers import projects_hub_fixture  # noqa: F401

HARNESS = Path(__file__).with_name("member_owner_only_reasons.mjs")


def test_reason_lines_render_for_members_and_not_for_owners():
    node = shutil.which("node")
    assert node, "Node is required for the member reason checks"
    result = subprocess.run(
        [node, "--test", str(HARNESS)], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_member_is_still_refused_where_the_ui_only_explains(projects_hub):
    h = projects_hub
    refused = {
        "list connections": h.request(h.member, "GET", "/api/connections", None),
        "create a connection": h.request(h.member, "POST", "/api/connections", {"name": "mine"}),
        "trust a Coworker": h.request(
            h.member,
            "POST",
            "/api/projects/work/orchestrator/trust",
            {"confirm": True, "machines": []},
        ),
        "turn bot management off": h.request(
            h.member, "PUT", "/api/settings/orchestration", {"enabled": False}
        ),
        "set workspace models": h.request(h.member, "PUT", "/api/settings/models", {"models": {}}),
        "set project models": h.request(h.member, "PUT", "/api/projects/work/models", {}),
    }
    statuses = {name: reply[0] for name, reply in refused.items()}
    # Connections and trust answer 403. The model writes answer the 404 of an unknown route on purpose,
    # so the server never shows a member that the setting can be edited.
    assert statuses["list connections"] == statuses["create a connection"] == 403, statuses
    assert statuses["trust a Coworker"] == statuses["turn bot management off"] == 403, statuses
    assert set(statuses.values()) <= {403, 404}, statuses
    # The refusal says what happened without naming anything the member cannot read.
    assert refused["list connections"][1] == {"error": "Owner access required"}
    # Nothing changed: the owner still lists no connection called "mine".
    status, body = h.request(h.owner, "GET", "/api/connections", None)[:2]
    assert status == 200 and "mine" not in str(body)
    assert h.get(h.member, "/api/projects/work")[1]["can_configure"] is False
