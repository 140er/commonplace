"""Browser-free Today and Needs me card regressions: plain cards, visible actions, one shared count."""

import shutil
import subprocess
from pathlib import Path


def test_needs_me_cards():
    node = shutil.which("node")
    assert node, "Node is required for the Needs me card checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("needs_me_cards.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-10000:] + result.stderr[-2000:]
