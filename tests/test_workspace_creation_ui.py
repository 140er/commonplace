"""Browser-free workspace creation contract."""

import shutil
import subprocess
from pathlib import Path


def test_workspace_creation_flow():
    node = shutil.which("node")
    assert node, "Node is required for workspace creation checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("workspace_creation.mjs"))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
