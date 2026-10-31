"""Rules: browser rendering, clarify prompts and owner request contracts."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_rules_render_and_requests():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed for the Rules render check")
    script = Path(__file__).with_name("commonplace_rules_render.mjs")
    subprocess.run([node, str(script)], check=True, timeout=10)
