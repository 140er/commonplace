"""Browser-free Settings regression, including every installed permission."""

import json
import os
import subprocess
from pathlib import Path

from commonplace import extensions
from commonplace.tool_policy.routes import catalog


def test_deck_settings_permissions():
    extensions.load_modules()
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("deck_settings.mjs"))],
        env={**os.environ, "POLICY_ACTIONS": json.dumps(sorted(catalog()))},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
