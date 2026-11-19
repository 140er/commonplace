"""Add-a-machine JS contract cases, using an in-memory synthetic server and a manual clock."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case",
    [
        "pure",
        "off",
        "member",
        "empty",
        "loading",
        "form",
        "create",
        "refusals",
        "copy",
        "connected",
        "polling",
        "reload-notices-change",
        "run-steps",
        "grants",
        "confirm-focus",
        "stalled",
        "no-repaint",
        "member-intro",
        "ready-row",
        "copy-in-place",
        "poll-failure-announced",
        "set-up-again-keeps-requesters",
        "list-quiet",
        "approve",
        "stale-accept",
        "decline",
        "failed",
        "closed",
        "remove",
        "offline",
        "escaping",
        "once-per-screen",
    ],
)
def test_compute_pairing_ui(case):
    node = shutil.which("node")
    assert node, "Node is required for Compute pairing UI checks"
    result = subprocess.run(
        [node, str(Path(__file__).with_name("compute_pairing_ui.mjs")), case],
        capture_output=True,
        text=True,
        timeout=30,
    )
    diagnostic = re.sub(r"data:text/javascript;base64,[A-Za-z0-9+/=]+", "<module>", result.stderr)
    assert result.returncode == 0, result.stdout + diagnostic
