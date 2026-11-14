"""Browser-free regression for the drawer's reservation removal path."""

import subprocess
from pathlib import Path


def test_remove_assignment_button_form_request_and_refusals():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("remove_assignment.mjs"))],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
