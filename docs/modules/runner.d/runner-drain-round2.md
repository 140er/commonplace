- **Owns:**

  - Round 2 of the #615 review for the runner side: the drain route re-checks that the runner is not removed inside the lock and the transaction; the hub audit line is written just before the commit and is its own write (a failed audit rolls the drain back with `503` / `audit`, a busy store is `503` / `busy`); the runner store has `runner_jobs_state_updated(state, updated_at, machine_id)` and `runner_jobs_starting(starting_at, machine_id)` so a poll searches an index instead of reading the history.
- **Promises:**

  - A runner removed while a drain is in flight is never drained; a poll does not grow with the number of finished runs beyond a 90-day window.
- **How it fails:**

  - `503 authority_unavailable` with reason `audit` or `busy`, nothing changed.
- **Tests:** `tests/test_runner_drain.py` (revoke inside the lock, busy store, SEARCH plans of every poll read).
