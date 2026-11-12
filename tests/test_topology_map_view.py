"""Compute map canvas: model, icons, layout, scene, memory and the screen's wiring, run in node."""

import shutil
import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"


def test_topology_map_view():
    for name in ("topology-map.js", "topology-layout.js", "topology-icons.js", "topology.css"):
        assert (STATIC / name).exists(), name
    node = shutil.which("node")
    assert node, "Node is required for the Compute map checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("topology_map_view.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-2000:]
