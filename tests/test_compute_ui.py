"""Production Compute JS contract/lifecycle cases, using inert synthetic requests."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case",
    [
        "escaping",
        "payloads",
        "enrollment",
        "scopes",
        "proposals",
        "approval",
        "isolation",
        "lifecycle",
        "failures",
        "routing",
        "discovery",
        "discovery-cancellation",
        "scripts",
    ],
)
def test_compute_ui(case):
    node = shutil.which("node")
    assert node, "Node is required for Compute UI checks"
    result = subprocess.run(
        [node, str(Path(__file__).with_name("compute_ui.mjs")), case],
        capture_output=True,
        text=True,
        timeout=15,
    )
    diagnostic = re.sub(r"data:text/javascript;base64,[A-Za-z0-9+/=]+", "<module>", result.stderr)
    assert result.returncode == 0, result.stdout + diagnostic
