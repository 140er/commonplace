"""Browser-free contracts against every installed action and server policy value."""

import json
import os
import subprocess
from pathlib import Path

from commonplace import extensions
from commonplace.tool_policy.routes import catalog
from commonplace.tool_policy.store import MODES


def test_settings_capy_node():
    extensions.load_modules()
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("deck_settings_capy.mjs"))],
        env={
            **os.environ,
            "POLICY_ACTIONS": json.dumps(sorted(catalog())),
            "POLICY_MODES": json.dumps(MODES),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
