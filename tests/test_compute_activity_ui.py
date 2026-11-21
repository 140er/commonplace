"""Navigation and aggregate-only rendering without a browser."""

import subprocess
from pathlib import Path


def test_activity_node_contracts():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("activity_ui.mjs"))],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_activity_regressions():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("activity_regressions.mjs"))],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
