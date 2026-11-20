"""Card panel Previews section: the static module renders every state, mounts and acts."""

import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).with_name("cardpreviews.mjs")
CASES = [
    "states",
    "failure",
    "labels",
    "empty",
    "escape",
    "url",
    "rule",
    "rulekept",
    "ruleviewer",
    "rulecarry",
    "focusreal",
    "progress",
    "rulefail",
    "mount",
    "refused",
    "offline",
    "offlinepainted",
    "retry",
    "stop",
    "stopwords",
    "refusal",
    "rulesave",
    "redraw",
    "poll",
    "focuskeys",
    "deleteview",
    "deleteflow",
    "stale",
    "panel",
    "loads",
]


@pytest.mark.parametrize("case", CASES)
def test_card_previews(case):
    result = subprocess.run(
        ["node", str(HARNESS), case], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr
