"""Production JS cases for the history and handoff page."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case",
    ["list", "chain", "offer", "confirm", "results", "escaping", "failures", "mounting", "denied"],
)
def test_history_page_ui(case):
    node = shutil.which("node")
    assert node, "Node is required for history page checks"
    result = subprocess.run(
        [node, str(Path(__file__).with_name("history_page.mjs")), case],
        capture_output=True,
        text=True,
        timeout=15,
    )
    diagnostic = re.sub(r"data:text/javascript;base64,[A-Za-z0-9+/=]+", "<module>", result.stderr)
    assert result.returncode == 0, result.stdout + diagnostic
