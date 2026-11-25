"""Run the Even Realities G2 bridge's own Node test suite (``integrations/even-g2/bridge``).

The bridge holds a Commonplace token for the glasses plugin, so its allowlist and
its never-leak-the-token checks must run on the same ``pytest`` path as the hub.
The bridge has no npm dependencies; ``node --test`` is all it needs.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

BRIDGE_DIR = Path(__file__).resolve().parent.parent / "integrations" / "even-g2" / "bridge"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_even_g2_bridge_node_suite():
    suite = sorted(str(path) for path in (BRIDGE_DIR / "test").glob("*.test.mjs"))
    assert suite, "the G2 bridge ships a Node test suite"
    result = subprocess.run(
        [NODE, "--test", "--test-reporter=tap", *suite],
        cwd=BRIDGE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# fail 0" in result.stdout, result.stdout
