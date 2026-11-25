"""The Work screen: lanes grouped like the Board, chips, Show more, empty lanes, escaping and the mount contract."""

import shutil
import subprocess
from pathlib import Path


def test_work_screen_node():
    node = shutil.which("node")
    assert node, "Node is required for the Work screen checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("deck_work.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-2000:]
