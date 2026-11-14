"""Browser-free Capy Compute facts, copy, link state and motion preference."""

import shutil
import subprocess
from pathlib import Path


def test_compute_capy_node():
    node = shutil.which("node")
    assert node, "Node is required for Compute checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("deck_compute_capy.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-2000:]
