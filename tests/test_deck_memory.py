"""Browser-free Memory presentation and import-consent contracts."""

import subprocess
from pathlib import Path


def test_deck_memory():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("deck_memory.mjs"))],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
