- **Owns:**

  - `status.collect_signals` reads started runs through the state-led index (`runner_jobs_state_updated`) back `STARTED_WINDOW_S` (90 days) and the current leases and starts under way in a second read: still one grouped row per machine, and a read that searches an index instead of scanning the run history.
- **Promises:**

  - A run recorded as started more than 90 days ago is no longer a "start success" signal (the machine's connection signals still say whether it is alive); nothing newer changes.
- **How it fails:**

  - A workspace without runner machines or jobs reads as no signals.
- **Tests:** `tests/test_topology_graph_status.py` and `tests/test_runner_drain.py` (the plan of the read).
