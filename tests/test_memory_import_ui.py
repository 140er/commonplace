"""File picker lifecycle and content-safety contracts in the browser module."""

import subprocess
from pathlib import Path


def test_memory_import_controller():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("memory_import.mjs"))],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
