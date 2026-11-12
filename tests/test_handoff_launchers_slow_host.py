"""Webhook launcher tests give the same answers when the server handles requests late."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELECTION = [
    "tests/test_handoff_launchers.py::test_webhook_times_out",
    "tests/test_handoff_launchers.py::test_webhook_minimal_success",
]


def test_webhook_tests_pass_when_every_request_worker_starts_late():
    env = dict(
        os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    )
    run = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-p", "tests._slow_webhook_plugin",
            "-p", "no:cacheprovider", "-p", "no:xdist", "-q", "--no-header",
            "-o", "addopts=-m 'not benchmark and not slow and not stress and not timing'",
            *SELECTION,
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-1000:]
    assert " passed" in run.stdout and "skipped" not in run.stdout
