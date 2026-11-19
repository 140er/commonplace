"""Compute overview JS contract cases, using an in-memory synthetic server and fake timers."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case",
    [
        "pure",
        "rows",
        "detail",
        "states",
        "failure",
        "polling",
        "recent-data",
        "announcements",
        "filters",
        "detail-flow",
        "detail-errors",
        "detail-refresh",
        "staleness",
        "clock-ticks-in-place",
        "refresh-in-place",
        "pause",
        "workspaces-sync",
        "fold",
        "skew",
        "clock-step",
        "every-runner-status-ages",
        "scope-and-state",
        "rows-patched-in-place",
        "attention-filter-resets",
        "stale-states-keep-the-server-snapshot",
        "filters-repaint-when-load-resets-them",
        "member",
        "in-compute",
        "isolation",
    ],
)
def test_compute_overview_ui(case):
    node = shutil.which("node")
    assert node, "Node is required for Compute overview UI checks"
    result = subprocess.run(
        [node, str(Path(__file__).with_name("compute_overview_ui.mjs")), case],
        capture_output=True,
        text=True,
        timeout=15,
    )
    diagnostic = re.sub(r"data:text/javascript;base64,[A-Za-z0-9+/=]+", "<module>", result.stderr)
    assert result.returncode == 0, result.stdout + diagnostic
