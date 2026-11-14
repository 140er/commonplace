"""Browser-free Home and Today interaction regressions."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "harness", ["deck_home_today.mjs", "deck_home_today_routes.mjs", "deck_home_activity.mjs"]
)
def test_home_today(harness):
    node = shutil.which("node")
    assert node, "Node is required for the Home and Today checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name(harness))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-10000:] + result.stderr[-2000:]
