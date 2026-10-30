"""Provider sign-in rendering and app boot behavior."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_signin_page_renders_and_handles_callback_errors():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed for the sign-in render check")
    script = Path(__file__).with_name("commonplace_signin_render.mjs")
    subprocess.run([node, str(script)], check=True, timeout=10)
