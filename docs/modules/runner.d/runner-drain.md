- **Owns:**

  - `drain.py`: one row per machine in the workspace runner store (`runner_drain`: flag, `seq`, who and when), read by `capacity.available` in the same transaction as a card lease. The owner changes it through `/api/compute/overview/runner/<id>/drain` and `/resume`.
- **Promises:**

  - A drained machine gets no new card runs: `capacity.available` answers 0, queued runs stay queued, none is cancelled, and runs already started finish. Chat and orchestrator sessions do not read the flag, and the runner program is not stopped; the confirmation says so. A repeat (drain while draining) changes nothing.
- **How it fails:**

  - A stale `seq` is refused (`stale_state`) and a revoked machine cannot be drained (`revoked`); a runner store without the table behaves as if nothing were drained.
- **Tests:** `tests/test_runner_drain.py` (lease enforcement, started runs untouched, preview, repeat, stale token, revoked).
