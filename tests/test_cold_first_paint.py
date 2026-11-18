"""Foreground regressions that also run unchanged against the development source."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("case", ["api-wave", "render-count", "bare-board", "selected-done"])
def test_first_paint_regression(case):
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("cold_first_paint.mjs")), case],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
