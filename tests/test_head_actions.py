"""Browser-free contract for seating a page's main action in the desktop title bar."""

import subprocess
from pathlib import Path


def test_head_actions_contract():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("head_actions.mjs"))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
