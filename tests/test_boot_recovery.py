"""Identity failures retain a retry state until the server confirms a session."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case",
    [
        "timeout",
        "abort",
        "network",
        "500",
        "503",
        "null",
        "403",
        "401",
        "slow-success",
        "exhaustion",
        "manual",
        "route-error",
    ],
)
def test_boot_identity_recovery(case):
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("boot_recovery.mjs")), case],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("case", ["loading", "error", "stale", "denied", "empty"])
def test_boot_rail_does_not_invent_empty_lists(case):
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("boot_rail_recovery.mjs")), case],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_boot_route_discovery_failure_keeps_retry_state():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("boot_route_recovery.mjs"))],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
