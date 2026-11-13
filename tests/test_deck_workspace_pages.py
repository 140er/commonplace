"""Workspace route, navigation and controller integration contracts in Node."""

import shutil
import subprocess
from pathlib import Path


def test_deck_workspace_pages():
    node = shutil.which("node")
    assert node, "Node is required for workspace navigation checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("deck_workspace_pages.mjs"))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
