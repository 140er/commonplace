- **Owns:**

  - Round 2 of the #615 review, server side: `tests/test_compute_workspace_lifecycle.py` (a legacy ready record decided as it is shown, a silent restart withdrawn, a refused replay after expiry, the expiry in the activity list, a running workspace reported failed without a claim), `tests/test_compute_overview.py` (an unreadable machine row under a workspace, a locked registry, the audit index) and `tests/test_runner_drain.py` (revoke inside the lock, a busy store, SEARCH plans); `tests/compute_overview_helpers.py` gains a fake executor `fail`.
- **Promises:**

  - Each check fails on the code before it and passes after; none sleeps (the fixture clock moves, calls are intercepted).
- **How it fails:**

  - A dead-end lifecycle state, a poll that scans the history, or a drain of a removed runner fails these tests.
- **Tests:** The three files above.
