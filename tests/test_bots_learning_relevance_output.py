"""Only the tiny informational report's format and repeatability are checked, never its score."""

import re
import subprocess
import sys
from pathlib import Path


def test_tiny_report_format_is_repeatable():
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(root / "tools/bots_learning_relevance.py"),
        "--seed",
        "7",
        "--conversations",
        "4",
    ]
    reports = [
        subprocess.run(command, cwd=root, capture_output=True, text=True, check=True, timeout=20)
        for _ in range(2)
    ]
    assert reports[0].stdout == reports[1].stdout
    assert reports[0].stderr == reports[1].stderr == ""
    lines = reports[0].stdout.splitlines()
    assert len(lines) == 5
    assert re.fullmatch(r"-?\d+\.\d{2}", lines[0])
    assert lines[1] == (
        "| Retrieval | Conversations | Expected item in prompt (%) | Mean added prompt chars |"
    )
    assert lines[2] == "| --- | ---: | ---: | ---: |"
    for line, label in zip(lines[3:], ("Without learned items", "With learned items")):
        assert re.fullmatch(rf"\| {label} \| 4 \| \d+\.\d{{2}} \| \d+\.\d{{2}} \|", line)
