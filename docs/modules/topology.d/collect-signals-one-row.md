- **Owns:**

  - `status.collect_signals` reads the runner jobs of a workspace as one grouped row per machine (the newest started run, the latest lease and the latest start under way) instead of one row per run, and takes the caller's already-open runner job store (`runs=`) so the Compute overview opens it once per read. What a signal means does not change.
- **Promises:**

  - A machine is busy while a lease or a start under way is current, and its last start is the newest started run, exactly as before; only the cost of reading them is bounded by the number of machines.
- **How it fails:**

  - A workspace without runner machines or jobs reads as no signals; nothing here raises for a missing table.
- **Tests:** `tests/test_topology_graph_status.py` (signals and statuses) and `tests/test_compute_overview.py` (the overview that passes its store in).
