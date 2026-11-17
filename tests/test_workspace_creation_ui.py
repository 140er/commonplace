"""Browser-free workspace creation contract."""

import json
import os
import shutil
import subprocess
from pathlib import Path


def test_workspace_creation_flow():
    node = shutil.which("node")
    assert node, "Node is required for workspace creation checks"
    from mempalace.config import name_validation_rules

    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("workspace_creation.mjs"))],
        env={**os.environ, "WORKSPACE_NAME_RULES": json.dumps(name_validation_rules())},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
