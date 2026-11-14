"""Browser-free rail, count, tree and phone chrome contracts."""

import subprocess
from pathlib import Path


def test_deck_shell_navigation_and_trees():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("deck_shell.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-2000:]
