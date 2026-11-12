"""Compute map node detail panel: the four tabs, their empty states, escaping, and the screen's
wiring to the canvas, run in node."""

import shutil
import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"


def test_topology_detail():
    for name in ("topology-detail.js", "topology-detail.css"):
        assert (STATIC / name).exists(), name
    node = shutil.which("node")
    assert node, "Node is required for the Compute map checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("topology_detail.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-2000:]
