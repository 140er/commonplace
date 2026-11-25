- **Owns:**

  - `drain.py`: one row per machine in the workspace runner store (`runner_drain`: flag, `seq`, who and when, the last request id and action) and an append-only `runner_drain_log` written in the same transaction as the flag and the hub-wide audit line (the audit goes in before the commit; if it cannot be written the drain is rolled back and the owner gets `503` / `audit`), read by `capacity.available` in the same transaction as a card lease. The owner changes it through `/api/compute/overview/runner/<id>/drain` and `/resume`.
- **Promises:**

  - A drained machine gets no new card runs: `capacity.available` answers 0, queued runs stay queued, none is cancelled, and runs already started finish. Chat relay and Coworker session leases do not read the flag (a decision of this slice, pinned by a test), so every sentence says "No new card runs start here. Chat and Coworker sessions can still start here.", and the runner program is not stopped; the confirmation says so. A repeat (drain while draining, or the same request id again) changes nothing.
- **How it fails:**

  - A stale `seq` is refused (`stale_state`), a request id reused for the other action is `request_reused` and a revoked machine cannot be drained (`revoked`); a request id is remembered only until that machine's next change; a runner store without the table behaves as if nothing were drained.
- **Tests:** `tests/test_runner_drain.py` (lease enforcement, started runs untouched, preview, repeat, stale token, revoked, the audit written with the change, the indexes the poll reads).
