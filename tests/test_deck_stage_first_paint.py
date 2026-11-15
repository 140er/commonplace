"""Deck stage first paint: a deep link draws the stage with its final data-dimmed value, before any route or read."""

import shutil
import subprocess
from pathlib import Path


def test_deck_stage_first_paint_node():
    node = shutil.which("node")
    assert node, "Node is required for the stage first-paint checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("deck_stage_first_paint.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-10000:] + result.stderr[-2000:]
