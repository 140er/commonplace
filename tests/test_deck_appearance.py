"""Browser-free contracts for the stage renderer and person-scoped appearance."""

import shutil
import subprocess
from pathlib import Path


def test_deck_appearance_node():
    node = shutil.which("node")
    assert node, "Node is required for the Appearance checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("deck_appearance.mjs"))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
