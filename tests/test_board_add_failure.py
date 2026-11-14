"""#550: a failed Add card keeps the typed words, says why in plain words (a 409 included) and never adds twice.

Browser-free: node drives the real board renderer and its submit handler (tests/board_add_failure.mjs).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).with_name("board_add_failure.mjs")
CASES = ["message_409", "network", "survives_rerender", "double_submit", "retry_clears"]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


@pytest.mark.parametrize("case", CASES)
def test_add_card_failure(case):
    result = subprocess.run(
        ["node", str(SCRIPT), case],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "STATIC_DIR": str(ROOT / "commonplace/web/static")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{case}: PASS" in result.stdout
