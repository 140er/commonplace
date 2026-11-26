"""Card #661: browser-free checks of the workspace model picker, account lines and own-account rows."""

import shutil
import subprocess
from pathlib import Path


def test_workspace_models_view():
    node = shutil.which("node")
    assert node, "Node is required for the workspace models view checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("workspace_models_view.mjs"))],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
