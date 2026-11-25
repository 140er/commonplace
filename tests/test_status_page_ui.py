"""Production JS cases for the read-only capabilities and status page."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case", ["ready", "explained", "unknown", "escaping", "failures", "mounting"]
)
def test_status_page_ui(case):
    node = shutil.which("node")
    assert node, "Node is required for status page checks"
    result = subprocess.run(
        [node, str(Path(__file__).with_name("status_page.mjs")), case],
        capture_output=True,
        text=True,
        timeout=15,
    )
    diagnostic = re.sub(r"data:text/javascript;base64,[A-Za-z0-9+/=]+", "<module>", result.stderr)
    assert result.returncode == 0, result.stdout + diagnostic
