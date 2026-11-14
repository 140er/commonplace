"""Browser-free Board and card drawer contracts."""

import subprocess
from pathlib import Path


def test_deck_board_contract():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("deck_board.mjs"))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
