"""The map's add buttons: the answer lines, the copied prompt and the wiring, run in node."""

import shutil
import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"


def test_topology_add_ui():
    assert (STATIC / "map-add.js").exists()
    node = shutil.which("node")
    assert node, "Node is required for the Compute map checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("topology_add_ui.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-2000:]
