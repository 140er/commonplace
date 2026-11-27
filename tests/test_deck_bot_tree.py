"""Browser-free contract of the sidebar bot tree: projects under bots, Unassigned, folding and keys."""

import subprocess
from pathlib import Path


def test_deck_bot_tree_contract():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("deck_bot_tree.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-2000:]
