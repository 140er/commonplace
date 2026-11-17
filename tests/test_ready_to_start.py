"""Ready-to-start states and actions through the shipped JavaScript renderer."""

import subprocess
from pathlib import Path


def test_ready_to_start_checklist():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("ready_to_start.mjs"))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
