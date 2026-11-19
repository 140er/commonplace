- **Owns:**

  - Round 3 of the #615 review, server words and counts: a started run is in progress only while its run ledger row has no end and its card is still being worked on by the machine's worker, a started run whose end was never reported is its own bucket (`end_unreported`) and a run the ledger says ended is not counted; a draining runner never also lists "may be full"; the drain confirmation uses `DRAIN_SCOPE` verbatim and the withdraw confirmation says the workspace is then Failed; an unverified machine is `idle` with no next step; "and N more" for a machine with more workspaces than names; plain fact labels; a locked compute database is `503` / `busy`.
- **Promises:**

  - A finished run is never Busy, never full and never "may still be open"; a drained runner never contradicts itself.
- **How it fails:**

  - A card that cannot be read (no board database yet) counts nothing as in progress rather than guessing.
- **Tests:** `tests/test_compute_overview.py` (finished, moved-on and restarted runs, a locked database, "and N more") and `tests/test_runner_drain.py` (busy drained runner, lock order with two threads, plans, the backfill marker).
