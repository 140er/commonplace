"""Production JS cases for the organise memory page."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case",
    ["facts", "sources", "links", "confirm", "escaping", "failures", "mounting", "denied"],
)
def test_organise_page_ui(case):
    node = shutil.which("node")
    assert node, "Node is required for organise page checks"
    result = subprocess.run(
        [node, str(Path(__file__).with_name("organise_page.mjs")), case],
        capture_output=True,
        text=True,
        timeout=15,
    )
    diagnostic = re.sub(r"data:text/javascript;base64,[A-Za-z0-9+/=]+", "<module>", result.stderr)
    assert result.returncode == 0, result.stdout + diagnostic
