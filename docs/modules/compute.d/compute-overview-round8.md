- **Owns:**

  - Round 7 of the #615 review, server side: the payload no longer serves `thresholds`, `drain_scope` or `next_steps` (they existed only for the screen's own status override, which is removed); "Allowed to start card runs" is "Yes, once a run finishes" for a Full runner; `NO_RESUME` reads "Resuming is not available in this version." and is said once (the draining blocker keeps only what drain stops); the Offline next step no longer repeats that new runs wait; the two sentences that pointed at restarting and starting say it is not available in this version; a Running, Starting or Stopping workspace has no "machine not verified" blocker.
- **Promises:**

  - The server's snapshot is the only source of a status, an attention state, a blocker, a next step and an age.
- **How it fails:**

  - Nothing new is refused; only the payload lost three fields and some words changed.
- **Tests:** `tests/test_compute_overview.py` (a running workspace's blockers, the draining and drained blocker word, the settings sentence, the Full runner), `tests/test_runner_drain.py` and `tests/test_compute_workspace_lifecycle.py`.
