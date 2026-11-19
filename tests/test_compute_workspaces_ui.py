"""Cloud workspace JS contract cases, using an in-memory synthetic server."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case",
    [
        "pure",
        "names",
        "states",
        "status-copy",
        "escaping",
        "create",
        "server-errors",
        "edit",
        "conflict",
        "discard",
        "registry-hidden",
        "member",
        "failures",
        "focus",
    ],
)
def test_compute_workspaces_ui(case):
    node = shutil.which("node")
    assert node, "Node is required for Compute workspace UI checks"
    result = subprocess.run(
        [node, str(Path(__file__).with_name("compute_workspaces_ui.mjs")), case],
        capture_output=True,
        text=True,
        timeout=15,
    )
    diagnostic = re.sub(r"data:text/javascript;base64,[A-Za-z0-9+/=]+", "<module>", result.stderr)
    assert result.returncode == 0, result.stdout + diagnostic
