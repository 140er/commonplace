"""Operational subpanels use the existing page lifecycle and fake deadlines."""

import shutil
import subprocess
from pathlib import Path

from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_commonplace_web import request


def run(hub, case):
    status, _, body = request(hub, "/app/admin.js")
    assert status == 200
    result = subprocess.run(
        [shutil.which("node"), str(Path(__file__).with_name("admin_instance_ui.mjs")), case],
        input=body.decode(),
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode not in (0, 42):
        raise RuntimeError(result.stderr[-2000:])
    assert result.returncode == 0, result.stderr


def test_health_and_version_independent(hub):
    run(hub, "health")


def test_complete_admin_and_operational_retries(hub):
    run(hub, "complete")
