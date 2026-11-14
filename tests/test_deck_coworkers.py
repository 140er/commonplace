"""Browser-free Capy list, optional panels, exact sends and lifecycle checks."""

import shutil
import subprocess
from pathlib import Path


def test_deck_coworkers():
    node = shutil.which("node")
    assert node, "Node is required for the Coworker checks"
    result = subprocess.run(
        [
            node,
            "--test",
            str(Path(__file__).with_name("deck_coworkers.mjs")),
            str(Path(__file__).with_name("deck_coworkers_route.mjs")),
            str(Path(__file__).with_name("deck_coworker_design_nits.mjs")),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
