"""Card panel Review comments section, project settings section and Pipeline chip: every state
renders in words, mounts, acts and keeps focus (node harness ``cardreviews.mjs``)."""

import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).with_name("cardreviews.mjs")
CASES = [
    "states",
    "waiting",
    "decision",
    "member",
    "off",
    "empty",
    "rounds",
    "long",
    "redacted",
    "escape",
    "slot",
    "focuskeys",
    "mount",
    "hidden",
    "offline",
    "answer",
    "refused",
    "repaint",
    "expand",
    "original",
    "more",
    "stale",
    "kept",
    "panel",
    "loads",
    "settings",
    "settingsmount",
    "chip",
]


@pytest.mark.parametrize("case", CASES)
def test_card_reviews(case):
    result = subprocess.run(
        ["node", str(HARNESS), case], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr
