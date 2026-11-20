"""Paused snapshots, including real controller pairing checks and delayed replies."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case",
    [
        "pause-pairing-check",
        "pause-inflight-overview",
        "pause-manual-refresh-pending",
        "pause-resume-pending",
        "pause-inflight-detail",
        "pause-inflight-denial",
        "pause-explicit-detail",
    ],
)
def test_pause_preserves_the_snapshot(case):
    node = shutil.which("node")
    assert node, "Node is required for paused snapshot checks"
    result = subprocess.run(
        [node, str(Path(__file__).with_name("compute_overview_ui.mjs")), case],
        capture_output=True,
        text=True,
    )
    diagnostic = re.sub(r"data:text/javascript;base64,[A-Za-z0-9+/=]+", "<module>", result.stderr)
    assert result.returncode == 0, result.stdout + diagnostic
