- **Owns:**

  - `ledger.ensure` backfills the run ledger for jobs that predate it once, marked by `run_ledger_backfill`, instead of an anti-join over every job at every open of the store; a ledger created (or created again) now is backfilled again. The drain route takes the runner write transaction before the control lock, the lease's own order.
- **Promises:**

  - Opening the store no longer scans every job; every job written since the ledger existed already has its row (`sync`).
- **How it fails:**

  - A job row written behind the ledger's back after the marker has no ledger row until it is synced; the overview reads a missing row as "end not reported".
- **Tests:** `tests/test_runner_drain.py` (backfill once, lock order), `tests/test_run_ledger.py`.
