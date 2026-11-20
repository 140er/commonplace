"""Priority actions exercised through the same frozen Node behavior selection."""

import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).with_name("card_priority_controls.mjs")
CASES = [
    "menu",
    "badges",
    "filters",
    "details",
    "normal",
    "write",
    "failure",
    "member",
    "undo",
    "stale",
    "race",
    "escaping",
]


@pytest.mark.parametrize("case", CASES)
def test_priority_controls(case):
    result = subprocess.run(["node", str(HARNESS), case], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
