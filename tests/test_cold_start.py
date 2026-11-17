"""Cold shell data stays independent of slow secondary reads."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "slow,route",
    [
        (slow, route)
        for route in ["#/boards", "#/board/project", "#/settings", "#/memory", "#/bots/project"]
        for slow in ["/api/needs-me", "/api/rules/count", "/api/bots"]
        if not (slow == "/api/bots" and route.startswith("#/bots"))
    ],
)
def test_slow_shell_dependency(slow, route):
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("cold_start.mjs")), slow, route],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
