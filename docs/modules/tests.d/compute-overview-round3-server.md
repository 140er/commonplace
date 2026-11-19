- **Owns:**

  - Round 3 of the #615 review, server side: `tests/compute_overview_helpers.py` gains `Fleet.work` (a card the worker is doing) and `Fleet.end` (the ledger learns a run ended); `tests/test_compute_overview.py` covers a finished run, a card that moved on and a locked compute database; `tests/test_runner_drain.py` covers a busy drained runner, the lock order with two threads and the one-time backfill; `tests/test_compute_overview_browser.py` uses the same fixtures (a run that ended, a drained and busy runner).
- **Promises:**

  - Each check fails on the code before it; none sleeps (events order the two threads).
- **How it fails:**

  - A finished run counted as in progress, a drained runner that also says it is full, or a drain that can deadlock with a lease fails these tests.
- **Tests:** The files above.
