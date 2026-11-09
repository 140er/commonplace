"""Isolated production-JS controller checks; no server or model is started."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case",
    [
        "escaping",
        "send",
        "reconciliation",
        "isolation",
        "lifecycle",
        "routing",
        "memory",
        "forms",
    ],
)
def test_coworker_ui(case):
    node = shutil.which("node")
    assert node, "Node is required for the coworker UI checks"
    result = subprocess.run(
        [node, str(Path(__file__).with_name("coworker_ui.mjs")), case],
        capture_output=True,
        text=True,
        timeout=15,
    )
    # Node includes the whole data-URL module in stack locations. Keep actual
    # error/stack text readable instead of printing encoded production source.
    diagnostic = re.sub(r"data:text/javascript;base64,[A-Za-z0-9+/=]+", "<module>", result.stderr)
    assert result.returncode == 0, result.stdout + diagnostic
