"""Reachable map mode and executable mounting in the Memory component."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("case", ["entry", "mount"])
def test_memory_map_mode(case):
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("memory_map_restore.mjs")), case],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
