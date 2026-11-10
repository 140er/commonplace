"""Search-index sweep tests give the same answers on a host too slow for a fixed wait."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELECTION = [
    "tests/test_search_index_sweep.py::test_u3_a_coalescing_and_enrolment",
    "tests/test_search_index_sweep.py::test_u3_b_job_bounds_and_draining",
    "tests/test_search_index_sweep.py::test_u3_c_crash_retry_idempotence",
    "tests/test_search_index_sweep.py::test_u3_d_quota_defers_between_batches",
    "tests/test_search_index_sweep.py::test_u3_f_principals_and_import_groups",
]


def test_sweep_tests_pass_when_waits_run_out_and_sweeps_start_late():
    env = dict(
        os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    )
    run = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-p", "tests._slow_sweep_plugin",
            "-p", "no:cacheprovider", "-p", "no:xdist", "-q", "--no-header",
            "-o", "addopts=-m 'not benchmark and not slow and not stress and not timing'",
            *SELECTION,
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-1000:]
    assert " passed" in run.stdout and "skipped" not in run.stdout
