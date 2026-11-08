"""Console route and lifecycle checks start with an independently served asset."""

import json
import shutil
import subprocess
from pathlib import Path

from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_commonplace_web import request


def run_console_case(hub, case):
    code, _, raw = request(hub, "/app/instance-admin.js")
    assert code == 200
    node = shutil.which("node")
    if not node:
        raise RuntimeError("JavaScript runtime required")
    sources = {"instance": raw.decode(), "case": case}
    for name in ("admin", "rail", "app", "api", "palette"):
        sources[name] = request(hub, f"/app/{name}.js")[2].decode()
    result = subprocess.run(
        [node, str(Path(__file__).with_name("instance_admin_ui.mjs"))],
        input=json.dumps(sources),
        text=True,
        capture_output=True,
        timeout=15,
    )
    if result.returncode not in (0, 42):
        raise RuntimeError(result.stderr)
    assert result.returncode == 0, result.stderr


def test_instance_console_lifecycle_and_independent_sections(hub):
    run_console_case(hub, "lifecycle")


def test_instance_console_stale_submit_prevents_navigation(hub):
    run_console_case(hub, "stale-submit")


def test_instance_console_expired_cursor_requires_refresh(hub):
    run_console_case(hub, "expired-cursor")


def test_workspace_settings_omit_hidden_project_pagination(hub):
    run_console_case(hub, "settings-pagination")
