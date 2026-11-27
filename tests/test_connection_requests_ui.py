"""Connections and Waiting on you drive the request routes through their UI handlers."""

import subprocess
from pathlib import Path


def test_connection_request_screens():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("connection_requests.mjs"))],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
