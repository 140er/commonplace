"""Isolated production-JS controller checks; no server or model is started."""

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
    assert result.returncode == 0, result.stdout + result.stderr
