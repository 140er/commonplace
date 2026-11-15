"""The Needs me filter and the board filter are one segment control."""

import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).with_name("seg_shared.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_needs_filter_is_the_board_segment_control():
    result = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
