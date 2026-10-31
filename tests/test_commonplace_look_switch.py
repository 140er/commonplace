"""Design trial selection, persistence and storage failures in the browser module."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_look_switch():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed for look switch checks")
    script = Path(__file__).with_name("commonplace_look_switch.mjs")
    subprocess.run([node, str(script)], check=True, timeout=15)
