"""The Memory page waits out a warming or busy memory instead of failing at once."""

import subprocess
from pathlib import Path


def test_memory_warming_state():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("memory_warming.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
