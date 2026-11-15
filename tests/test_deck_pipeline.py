"""The Pipeline view over the example fixture: blocks, states, Amsterdam times, waits, slowest, lead time."""

import shutil
import subprocess
from pathlib import Path


def test_pipeline_view_node():
    node = shutil.which("node")
    assert node, "Node is required for the Pipeline view checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("deck_pipeline.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-2000:]
