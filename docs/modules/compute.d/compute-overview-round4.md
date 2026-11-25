- **Owns:**

  - Round 4 of the #615 review, server side: what holds a slot of a machine is decided once, in `jobs.in_flight` (the lease's own definition, unchanged), and the overview reads it (finished runs whose card is still Doing are their own bucket, `finished_holding`, and say they hold a slot); started runs that hold no slot and never reported an end are `end_unreported` with no silent limit; every drain sentence comes from `DRAIN_SCOPE`; a run the ledger says ended is labelled Finished in the activity list; Slow to report and an Unknown workspace are not attention states; one unreadable resource report is skipped; the no-machine sentence is one.
- **Promises:**

  - The lease refuses exactly when the overview says the machine is full, for every case of the matrix.
- **How it fails:**

  - A card board that cannot be read counts nothing as in progress rather than guessing.
- **Tests:** `tests/test_runner_drain.py` (the matrix of lease against overview, plans, the backfill race, an unreadable resource row, labels) and `tests/test_compute_overview.py`.
