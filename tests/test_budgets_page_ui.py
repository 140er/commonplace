"""Production JS cases for the spend and budgets page and its Needs me row."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

CASES = [
    "format",
    "owner",
    "badges",
    "member",
    "explained",
    "escaping",
    "form",
    "confirm",
    "mounting",
    "save",
    "refused",
    "clearing",
    "needs",
    "rows",
]


@pytest.mark.parametrize("case", CASES)
def test_budgets_page_ui(case):
    node = shutil.which("node")
    assert node, "Node is required for budgets page checks"
    result = subprocess.run(
        [node, str(Path(__file__).with_name("budgets_page.mjs")), case],
        capture_output=True,
        text=True,
        timeout=15,
    )
    diagnostic = re.sub(r"data:text/javascript;base64,[A-Za-z0-9+/=]+", "<module>", result.stderr)
    assert result.returncode == 0, result.stdout + diagnostic
