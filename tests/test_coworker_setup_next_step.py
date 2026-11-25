"""Coworker setup dead ends have a named, reachable next action."""

import subprocess
from pathlib import Path


def test_coworker_setup_next_step():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("coworker_setup_next_step.mjs"))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
