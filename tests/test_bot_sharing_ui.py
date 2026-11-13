"""Deck sharing and thread contracts, including browser-free permission regressions."""

import shutil
import subprocess
from pathlib import Path


def test_bot_sharing_contracts():
    node = shutil.which("node")
    assert node, "Node is required for the sharing checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("commonplace_bot_sharing.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-2000:]
