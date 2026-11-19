- **Owns:**

  - `jobs.in_flight` and `jobs.working_cards`: the one definition of which runs hold a slot of a machine (leased, starting and uncertain runs, and started runs whose card is Doing for the machine's worker), used by `jobs.lease` (behaviour unchanged) and by the Compute overview. New indexes `runner_jobs_machine_updated` and `runner_drain_log_machine`; the ledger backfill marker is checked again inside its transaction.
- **Promises:**

  - Scheduling is exactly what it was; the screen can no longer disagree with the lease about what is in flight.
- **How it fails:**

  - A card board that is missing counts nothing as in flight for a started run.
- **Tests:** `tests/test_runner_jobs.py`, `tests/test_runner_capacity_contract_388.py`, `tests/test_runner_start.py` (the lease, unchanged) and `tests/test_runner_drain.py` (the matrix).
